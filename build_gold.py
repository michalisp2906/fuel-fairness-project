"""
Gold layer for the web app: the CURRENT state of every station, one row per
station per modelled grade.

Takes the feature layer (data/features/features.parquet, the full event
history) and keeps only the latest price event per (node_id, fuel_type),
with the columns the Streamlit app needs.

Fair price, overcharge, and the flag are RECOMPUTED here against the most
recent wholesale week (decided 2026-07-03), not carried over from the event
date. The app shows how each standing price compares with a fair price NOW:
a station that set a high price months ago and held it while costs fell is
still overcharging today. The event-time values stay untouched in the
feature layer, where modelling needs them. All stations of a grade are
judged against the same current wholesale, so the comparison is uniform.

Unlike silver and features, the output IS committed to git: the deployed
app on Streamlit Community Cloud reads it straight from the repo clone, so
it must be small (well under 1 MB) and always present. The planned GitHub
Action re-runs this script (after build_silver.py and build_features.py)
on every snapshot push so the deployed app self-updates.

Permanently closed stations are dropped. Temporarily closed stations are
kept and marked, so the app can badge them.

Run:
    .venv/Scripts/python.exe build_gold.py   (Windows)
    .venv/bin/python build_gold.py           (Mac)
"""
from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd

from build_features import (
    DUTY_PPL,
    FAIR_MARGIN_PPL,
    FLAG_BUFFER_PPL,
    MAX_WHOLESALE_STALENESS_DAYS,
    VAT_RATE,
    WHOLESALE_LAG_DAYS,
    _GRADE_TO_WHOLESALE_COL,
    estimate_wholesale_basis,
)

FEATURES_IN = Path("data/features/features.parquet")
WHOLESALE_IN = Path("data/external/wholesale_prices.parquet")
GOLD_DIR = Path("data/gold")
GOLD_OUT = GOLD_DIR / "app_data.parquet"

# Only what the app displays or filters on. Keeping this list explicit means
# a new feature column cannot silently bloat the committed file.
APP_COLUMNS = [
    "node_id", "fuel_type",
    "price_ppl", "price_change_effective_timestamp",
    "fair_price_ppl", "overcharge_ppl", "signal1_flag",
    "brand_name", "trading_name", "postcode", "city", "county", "country",
    "latitude", "longitude",
    "is_motorway", "is_supermarket", "temporary_closure",
    "ruc_2fold",
]


def current_wholesale() -> pd.Series:
    """
    The wholesale price per grade a station buying for TODAY'S pump price
    would have paid: the latest completed wholesale week as of today minus
    the same 10-day lag Signal 1 uses.
    """
    wholesale = pd.read_parquet(WHOLESALE_IN).sort_values("date")
    lag_date = pd.Timestamp.now().normalize() - pd.Timedelta(days=WHOLESALE_LAG_DAYS)
    row = wholesale[wholesale["date"] <= lag_date].iloc[-1]

    staleness = (lag_date - row["date"]).days
    if staleness > MAX_WHOLESALE_STALENESS_DAYS:
        warnings.warn(
            f"Latest usable wholesale week ({row['date'].date()}) is "
            f"{staleness} days before the lag date. The wholesale parquet "
            "looks stale; re-run build_external.py.",
            stacklevel=2,
        )
    print(f"  Current wholesale week: {row['date'].date()} (lag date {lag_date.date()})")
    return pd.Series(
        {grade: row[col] for grade, col in _GRADE_TO_WHOLESALE_COL.items()}
    )


def main() -> None:
    features = pd.read_parquet(FEATURES_IN)
    print(f"Loaded {len(features):,} feature-layer events")

    open_events = features[features["permanent_closure"] != True]
    dropped = len(features) - len(open_events)
    print(f"  Dropped {dropped:,} events from permanently closed stations")

    latest = (
        open_events.sort_values("price_change_effective_timestamp")
        .drop_duplicates(subset=["node_id", "fuel_type"], keep="last")
        [APP_COLUMNS]
        .reset_index(drop=True)
    )

    print("Recomputing Signal 1 against the current wholesale week...")
    wholesale_now = current_wholesale()
    basis = estimate_wholesale_basis()
    fair_now = {
        grade: (wholesale_now[grade] + basis[grade] + DUTY_PPL + FAIR_MARGIN_PPL)
        * VAT_RATE
        for grade in wholesale_now.index
    }
    latest["fair_price_ppl"] = latest["fuel_type"].map(fair_now)
    latest["overcharge_ppl"] = latest["price_ppl"] - latest["fair_price_ppl"]
    latest["signal1_flag"] = latest["overcharge_ppl"] > FLAG_BUFFER_PPL
    for grade, fair in fair_now.items():
        print(f"  {grade}: current fair price {fair:.1f}p")

    # The file is committed and re-committed by CI on every snapshot push, so
    # keep it small: category dtype for repetitive strings, float32 for
    # coordinates and prices, zstd compression.
    for col in ("fuel_type", "brand_name", "city", "county", "country", "ruc_2fold"):
        latest[col] = latest[col].astype("category")
    for col in ("price_ppl", "fair_price_ppl", "overcharge_ppl", "latitude", "longitude"):
        latest[col] = latest[col].astype("float32")

    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    latest.to_parquet(GOLD_OUT, index=False, compression="zstd")

    size_kb = GOLD_OUT.stat().st_size / 1024
    print(f"Writing gold app table -> {GOLD_OUT} ({size_kb:.0f} KB)")
    print(f"  Rows: {len(latest):,} ({latest['node_id'].nunique():,} stations)")
    for grade, sub in latest.groupby("fuel_type", observed=True):
        print(
            f"  {grade:12s} stations: {len(sub):,}  "
            f"median price {sub['price_ppl'].median():.1f}p  "
            f"flagged: {sub['signal1_flag'].mean():.1%}"
        )
    as_of = latest["price_change_effective_timestamp"].max()
    print(f"  Latest price change: {as_of}")


if __name__ == "__main__":
    main()
