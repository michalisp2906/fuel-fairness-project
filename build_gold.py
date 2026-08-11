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

import numpy as np
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
from signal2_validation import (
    ISLAND_AREAS,
    ISLAND_DISTRICTS,
    outward_district,
)

FEATURES_IN = Path("data/features/features.parquet")
WHOLESALE_IN = Path("data/external/wholesale_prices.parquet")
GOLD_DIR = Path("data/gold")
GOLD_OUT = GOLD_DIR / "app_data.parquet"
SIGNAL2_IN = GOLD_DIR / "signal2_scores.parquet"

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
    prices = pd.Series(
        {grade: row[col] for grade, col in _GRADE_TO_WHOLESALE_COL.items()}
    )
    return prices, row["date"]


def attach_signal2(latest: pd.DataFrame, wholesale_week: pd.Timestamp) -> pd.DataFrame:
    """
    Merge the cross-fitted Signal 2 predictions (build_signal2.py) and turn
    them into the peer-relative numbers the app shows.

    THE DISPLAYED NUMBER IS DEMEANED (decided 2026-08-11). signal2_ppl is the
    leftover (actual overcharge minus predicted) minus the median leftover
    across all scored stations of that fuel, so it reads "pence dearer than the
    typical comparable station today", not raw pence.

    Why: the fold models only know the market regimes collected so far, and
    LightGBM clamps instead of extrapolating, so when wholesale moves outside
    that range the predicted LEVEL is wrong for every station at once. Measured
    2026-08-11, the raw leftover median was -2.0p (E10) and -3.1p (B7), which
    read literally would tell visitors that nearly every station in the country
    undercharges its peers. That error is common to all stations, so demeaning
    removes it and leaves the ranking untouched, and ranking is the only thing
    Signal 2 claims (Decision 5). It is also the same within-week demeaning
    already adopted as the accuracy gate on 2026-08-02, so the app and the
    validation harness finally describe the same quantity.

    Cost, which the Methodology page must state: Signal 2 answers "dearer than
    comparable stations", not "dearer by N pence in absolute terms". Signal 1
    remains the absolute number.

    Stations the model deliberately excludes (motorway, ferry-dependent
    islands, decided 2026-07-03 and 2026-07-08) get a null score and a status
    label, never a blank: they are the rows visitors click first.

    Only signal2_ppl and the excused delta are stored: the affluence-blind
    figure is their sum, so keeping it too would pay committed bytes for a
    column the app can add up itself.
    """
    s2_cols = [
        "signal2_ppl", "excused_by_affluence_ppl",
        "signal2_decile", "signal2_status",
    ]
    if not SIGNAL2_IN.exists():
        warnings.warn(
            f"{SIGNAL2_IN} is missing, so the app will show Signal 1 only. "
            "Run build_signal2.py to produce it.",
            stacklevel=2,
        )
        for col in s2_cols:
            latest[col] = np.nan
        latest["signal2_status"] = "not scored yet"
        return latest

    scores = pd.read_parquet(SIGNAL2_IN)
    scored_week = pd.Timestamp(scores["scored_for_week"].max())
    if scored_week != pd.Timestamp(wholesale_week):
        warnings.warn(
            f"Signal 2 scores were built for wholesale week "
            f"{scored_week.date()} but this rebuild is repricing against "
            f"{pd.Timestamp(wholesale_week).date()}. Peer comparisons are "
            "judged on a regime the model was not scored for; re-run "
            "build_signal2.py.",
            stacklevel=2,
        )

    scores["fuel_type"] = scores["fuel_type"].astype(str)
    latest["fuel_type"] = latest["fuel_type"].astype(str)
    merged = latest.merge(
        scores[["node_id", "fuel_type",
                "pred_signal2_ppl", "pred_signal2_nohp_ppl"]],
        on=["node_id", "fuel_type"], how="left",
    )

    leftover = merged["overcharge_ppl"] - merged["pred_signal2_ppl"]
    leftover_nohp = merged["overcharge_ppl"] - merged["pred_signal2_nohp_ppl"]
    fuel = merged["fuel_type"]
    merged["signal2_ppl"] = leftover - leftover.groupby(fuel).transform("median")
    signal2_nohp = leftover_nohp - leftover_nohp.groupby(fuel).transform("median")
    # Both sides are peer-relative, so this is how much house price moves a
    # station RELATIVE TO OTHERS, which is the comparison the finding is about.
    merged["excused_by_affluence_ppl"] = signal2_nohp - merged["signal2_ppl"]

    # 10 = dearest against comparable stations. Ranked first so that ties
    # (identical predictions in the same cell) cannot collapse a decile.
    merged["signal2_decile"] = (
        merged.groupby("fuel_type")["signal2_ppl"]
        .transform(
            lambda s: pd.qcut(s.rank(method="first"), 10, labels=False) + 1
        )
    )

    district = outward_district(merged["postcode"].fillna(""))
    is_island = district.isin(ISLAND_DISTRICTS) | district.str[:2].isin(ISLAND_AREAS)
    is_motorway = merged["is_motorway"].fillna(False).astype(bool)
    unscored = merged["signal2_ppl"].isna()
    status = pd.Series("scored", index=merged.index, dtype=object)
    status[unscored] = "not compared: too little local history"
    status[unscored & is_island] = "not compared: island, own group"
    status[unscored & is_motorway] = "not compared: motorway, own group"
    merged["signal2_status"] = status

    print(
        f"  Signal 2: {(~unscored).sum():,} of {len(merged):,} rows scored "
        f"({(~unscored).mean():.1%}), built for week {scored_week.date()}"
    )
    for grade, sub in merged.groupby("fuel_type"):
        print(
            f"    {grade:12s} vs peers: p10 {sub['signal2_ppl'].quantile(.1):+.1f}p  "
            f"median {sub['signal2_ppl'].median():+.1f}p  "
            f"p90 {sub['signal2_ppl'].quantile(.9):+.1f}p"
        )
    return merged.drop(columns=["pred_signal2_ppl", "pred_signal2_nohp_ppl"])


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
    wholesale_now, wholesale_week = current_wholesale()
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

    print("Attaching Signal 2 peer comparisons...")
    latest = attach_signal2(latest, wholesale_week)

    # The file is committed and re-committed by CI on every snapshot push, so
    # keep it small: category dtype for repetitive strings, float32 for
    # coordinates and prices, zstd compression.
    for col in ("fuel_type", "brand_name", "city", "county", "country",
                "ruc_2fold", "signal2_status"):
        latest[col] = latest[col].astype("category")
    for col in ("price_ppl", "fair_price_ppl", "overcharge_ppl", "latitude",
                "longitude", "signal2_ppl", "excused_by_affluence_ppl"):
        latest[col] = latest[col].astype("float32")
    # Displayed to one decimal, so storing more precision only costs bytes in
    # a file CI recommits several times a day.
    for col in ("signal2_ppl", "excused_by_affluence_ppl"):
        latest[col] = latest[col].round(2)
    latest["signal2_decile"] = latest["signal2_decile"].astype("Int8")

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
