"""
Bronze-to-silver pipeline for the Fuel Finder project.

Reads all gzipped JSON snapshots from data/raw/ (the bronze layer), joins
station details to price events, deduplicates on
(node_id, fuel_type, price_change_effective_timestamp), and writes a tidy
Parquet file to data/silver/prices_silver.parquet.

Each row is one unique price-change event, enriched with station details from
the PFS snapshot closest in time to that price event (not just the latest one,
because station attributes like closure status can change over time).

Full rebuild: reads all bronze files on every run. Safe to run repeatedly.

Run:
    .venv\\Scripts\\python.exe build_silver.py
"""
from __future__ import annotations

import gzip
import json
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

RAW_DIR = Path("data/raw")
SILVER_DIR = Path("data/silver")
QC_DIR = SILVER_DIR / "qc"
SILVER_OUT = SILVER_DIR / "prices_silver.parquet"

KNOWN_FUEL_GRADES = {"E10", "E5", "B7_STANDARD", "B7_PREMIUM", "B10", "HVO"}


def load_snapshot(path: Path) -> tuple[pd.Timestamp, list[dict]]:
    """Read a gzipped JSON snapshot; return (pulled_at, records)."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        envelope = json.load(f)
    pulled_at = pd.Timestamp(envelope["pulled_at"], tz="UTC")
    return pulled_at, envelope["records"]


def load_all_pfs() -> pd.DataFrame:
    """
    Load every PFS snapshot into one dataframe, one row per (station, snapshot).
    Flattens the nested location dict into top-level columns.
    """
    paths = sorted(RAW_DIR.glob("pfs/**/*.json.gz"))
    if not paths:
        raise FileNotFoundError(f"No PFS snapshots found under {RAW_DIR / 'pfs'}")

    frames = []
    for path in paths:
        pulled_at, records = load_snapshot(path)
        rows = []
        for r in records:
            loc = r.get("location") or {}
            rows.append({
                "node_id":                       r["node_id"],
                "brand_name":                    r.get("brand_name"),
                "trading_name":                  r.get("trading_name"),
                "is_motorway_service_station":   r.get("is_motorway_service_station"),
                "is_supermarket_service_station": r.get("is_supermarket_service_station"),
                "temporary_closure":             r.get("temporary_closure"),
                "permanent_closure":             r.get("permanent_closure"),
                "postcode":                      loc.get("postcode"),
                "latitude":                      loc.get("latitude"),
                "longitude":                     loc.get("longitude"),
                "city":                          loc.get("city"),
                "county":                        loc.get("county"),
                "country":                       loc.get("country"),
            })
        df = pd.DataFrame(rows)
        df["pfs_pulled_at"] = pulled_at
        frames.append(df)

    pfs = pd.concat(frames, ignore_index=True)
    print(
        f"  Loaded {len(paths)} PFS snapshots, "
        f"{pfs['node_id'].nunique()} unique stations across all snapshots"
    )
    return pfs


def load_all_prices() -> pd.DataFrame:
    """
    Load every prices snapshot, exploding the fuel_prices list so each row
    is one (station, fuel_type, snapshot) observation.
    """
    paths = sorted(RAW_DIR.glob("prices/**/*.json.gz"))
    if not paths:
        raise FileNotFoundError(f"No prices snapshots found under {RAW_DIR / 'prices'}")

    frames = []
    for path in paths:
        pulled_at, records = load_snapshot(path)
        rows = []
        for r in records:
            for fp in r.get("fuel_prices") or []:
                rows.append({
                    "node_id":                          r["node_id"],
                    "fuel_type":                        fp.get("fuel_type"),
                    "price_ppl":                        fp.get("price"),
                    "price_last_updated":               fp.get("price_last_updated"),
                    "price_change_effective_timestamp": fp.get("price_change_effective_timestamp"),
                    "snapshot_pulled_at":               pulled_at,
                })
        frames.append(pd.DataFrame(rows))

    prices = pd.concat(frames, ignore_index=True)

    for col in ("price_last_updated", "price_change_effective_timestamp", "snapshot_pulled_at"):
        prices[col] = pd.to_datetime(prices[col], utc=True)

    # Guard: alert on any grade we have never seen before.
    seen = set(prices["fuel_type"].dropna().unique())
    unexpected = seen - KNOWN_FUEL_GRADES
    if unexpected:
        warnings.warn(
            f"UNEXPECTED FUEL GRADES detected: {unexpected}. "
            "Do not model these until the grade is understood.",
            stacklevel=2,
        )

    # Guard: null or zero prices mean something went wrong upstream.
    bad = prices["price_ppl"].isna() | (prices["price_ppl"] <= 0)
    if bad.any():
        warnings.warn(
            f"{bad.sum()} rows have a null or zero price_ppl. Investigate before modelling.",
            stacklevel=2,
        )

    print(
        f"  Loaded {len(paths)} prices snapshots, "
        f"{len(prices):,} raw rows across all snapshots"
    )
    return prices


def deduplicate_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse repeated observations of the same price event into one row.

    The deduplication key is (node_id, fuel_type, price_change_effective_timestamp).
    A price observed in 4 daily snapshots without changing is one event, not four.
    first_seen_at records the earliest snapshot that saw it, which is useful for
    measuring collection latency but is not part of the time series itself.
    """
    key = ["node_id", "fuel_type", "price_change_effective_timestamp"]

    # Sanity check: the same price event should have the same price in every snapshot
    # that reports it. If not, something is wrong with the upstream data.
    spread = prices.groupby(key)["price_ppl"].nunique()
    inconsistent = spread[spread > 1]
    if not inconsistent.empty:
        warnings.warn(
            f"{len(inconsistent)} price events have inconsistent price_ppl values across "
            "snapshots. Investigate before modelling.",
            stacklevel=2,
        )

    first_seen = (
        prices.groupby(key)["snapshot_pulled_at"]
        .min()
        .rename("first_seen_at")
        .reset_index()
    )
    # Take values from the earliest snapshot so price_ppl is consistent with first_seen_at.
    one_per_key = (
        prices.sort_values("snapshot_pulled_at")
        .drop_duplicates(subset=key, keep="first")[key + ["price_ppl", "price_last_updated"]]
    )
    deduped = first_seen.merge(one_per_key, on=key, how="left")

    n_raw, n_dedup = len(prices), len(deduped)
    print(
        f"  Deduplicated: {n_raw:,} raw rows -> {n_dedup:,} unique price events "
        f"({n_raw - n_dedup:,} duplicate observations removed)"
    )
    return deduped


def assign_nearest_pfs_snapshot(
    price_timestamps: pd.Series,
    pfs_times: list[pd.Timestamp],
) -> pd.Series:
    """
    For each price_change_effective_timestamp, return the pulled_at of the
    PFS snapshot closest in time.

    Uses binary search over the sorted list of PFS snapshot times, then
    checks both neighbours to find whichever is nearer.
    """
    pfs_unix = np.array([t.timestamp() for t in pfs_times])
    price_unix = price_timestamps.apply(lambda t: t.timestamp()).values

    # searchsorted gives the insertion point; the nearest neighbour is either
    # that index or the one before it.
    idx = np.searchsorted(pfs_unix, price_unix)
    idx = np.clip(idx, 0, len(pfs_unix) - 1)
    idx_prev = np.clip(idx - 1, 0, len(pfs_unix) - 1)

    dist_curr = np.abs(pfs_unix[idx] - price_unix)
    dist_prev = np.abs(pfs_unix[idx_prev] - price_unix)
    best_idx = np.where(dist_prev <= dist_curr, idx_prev, idx)

    return pd.Series(
        [pfs_times[i] for i in best_idx],
        index=price_timestamps.index,
        name="pfs_snapshot_used",
    )


def main() -> None:
    SILVER_DIR.mkdir(parents=True, exist_ok=True)
    QC_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading PFS snapshots...")
    pfs = load_all_pfs()

    print("Loading prices snapshots...")
    prices_raw = load_all_prices()

    print("Deduplicating price events...")
    prices = deduplicate_prices(prices_raw)

    print("Assigning nearest PFS snapshot to each price event...")
    pfs_times = sorted(pfs["pfs_pulled_at"].drop_duplicates().tolist())
    prices["pfs_snapshot_used"] = assign_nearest_pfs_snapshot(
        prices["price_change_effective_timestamp"], pfs_times
    )

    print("Joining station details...")
    pfs_renamed = pfs.rename(columns={
        "is_motorway_service_station":    "is_motorway",
        "is_supermarket_service_station": "is_supermarket",
    })
    enriched = prices.merge(
        pfs_renamed,
        left_on=["node_id", "pfs_snapshot_used"],
        right_on=["node_id", "pfs_pulled_at"],
        how="left",
    ).drop(columns=["pfs_pulled_at"])

    # QC: price events with no PFS match cannot be modelled and must be flagged.
    no_pfs_match = enriched["brand_name"].isna()
    if no_pfs_match.any():
        n = no_pfs_match.sum()
        print(f"  WARNING: {n} price events have no matching PFS station record.")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        qc_path = QC_DIR / f"unmatched_prices_{stamp}.csv"
        enriched.loc[no_pfs_match, [
            "node_id", "fuel_type",
            "price_change_effective_timestamp", "pfs_snapshot_used",
        ]].to_csv(qc_path, index=False)
        print(f"  Written to: {qc_path}")
    else:
        print("  All price events matched to a PFS record.")

    col_order = [
        "node_id", "fuel_type", "price_ppl",
        "price_change_effective_timestamp", "price_last_updated", "first_seen_at",
        "pfs_snapshot_used",
        "brand_name", "trading_name", "postcode",
        "latitude", "longitude", "city", "county", "country",
        "is_motorway", "is_supermarket",
        "temporary_closure", "permanent_closure",
    ]
    enriched = enriched[[c for c in col_order if c in enriched.columns]]

    print(f"Writing silver layer -> {SILVER_OUT}")
    enriched.to_parquet(SILVER_OUT, index=False)

    print(f"\nDone.")
    print(f"  Rows:            {len(enriched):,}")
    print(f"  Unique stations: {enriched['node_id'].nunique():,}")
    print(f"  Fuel types:      {sorted(enriched['fuel_type'].dropna().unique())}")
    ts = enriched["price_change_effective_timestamp"]
    print(f"  Date range:      {ts.min()} to {ts.max()}")


if __name__ == "__main__":
    main()
