"""
Bronze-to-silver pipeline for the Fuel Finder project.

Reads all gzipped JSON snapshots from data/raw/ (the bronze layer), joins
station details to price events, deduplicates on
(node_id, fuel_type, price_change_effective_timestamp), and writes a tidy
Parquet file to data/silver/prices_silver.parquet.

Each row is one unique price-change event, enriched with station details from
the PFS snapshot closest in time to that price event (not just the latest one,
because station attributes like closure status can change over time).

Exception: coordinates. Some PFS snapshots carry corrupted coordinates for a
small subset of stations, and a station does not move, so latitude/longitude
are healed to one canonical, validated value per station (see
heal_coordinates). Requires data/external/postcode_lookup.parquet (committed,
built by build_external.py) for the postcode-centroid reference.

Full rebuild: reads all bronze files on every run. Safe to run repeatedly.

Run:
    .venv\\Scripts\\python.exe build_silver.py
"""
from __future__ import annotations

import gzip
import json
import re
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

# --- Coordinate healing constants ---------------------------------------------

POSTCODES_IN = Path("data/external/postcode_lookup.parquet")

# Generous bounding box around the UK (Shetland included, Channel Islands not:
# the scheme has no stations there). Coordinates outside this box are corrupt.
UK_LAT_RANGE = (49.8, 61.0)
UK_LON_RANGE = (-8.7, 1.8)

# A station should sit close to its unit-postcode centroid (usually within a
# few hundred metres). 15 km is deliberately loose; it only needs to catch
# corruption like a flipped longitude sign, which moves a station 100+ km.
MAX_KM_FROM_POSTCODE = 15.0
EARTH_RADIUS_KM = 6371.0088

# --- Cleaning constants -------------------------------------------------------

PRICE_MIN_PPL = 50.0
PRICE_MAX_PPL = 300.0

# Brand names that mean "no brand" in any capitalisation.
_UNBRANDED_ALIASES = frozenset({
    "n/a", "none", "no brand", "no brand name", "no brand dispayed",
    "no brand displayed", "not branded", "unbranded", "independent",
    "independant",
})

# Values in the brand_name field that are clearly data errors, not brand names.
_BRAND_NULL_VALUES = frozenset({"8520231"})

# Title-cased brand names that need a manual fix after normalisation.
# Covers acronyms embedded in compound names and known data typos.
_BRAND_CORRECTIONS: dict[str, str] = {
    "Independant":       "Independent",
    "Bp Harvest Energy": "BP Harvest Energy",
    "Eg On The Move":    "EG On The Move",
}

# Brand names carrying a test/staging marker, seen leaked into the live PFS
# endpoint (e.g. "S49 Pre Prod Welcome Break", node_id cac88484...ea798a,
# persistently mislabeled across every PFS snapshot collected 2026-06-24 to
# 2026-07-02, even though the real-world station has traded as BP since
# 2026-04-25). Metadata this unreliable can't be trusted for modelling, so
# any price event resolving to one of these brands is dropped.
_TEST_BRAND_MARKERS = ("pre prod", "pre-prod")

# node_id values confirmed to carry implausible one-off data (not a pattern,
# just a known-bad record). Pilning Garage (BS35 4JB): E10 239.9p / B7_STANDARD
# 229.9p, ~90p above the mainland median for a non-remote England location.
# Flagged 2026-07-02; investigate with the API provider if it persists.
_EXCLUDED_NODE_IDS = frozenset({
    "de97c5230d9464e3ec72c7f48cd4a2bba1db8203753f65003b2cf14235ec4370",
})

# Postcode area codes that uniquely identify Scotland, Wales, Northern Ireland.
# Everything else is England. "S" (Sheffield) is England, not Scotland.
_SCOTTISH_AREAS = frozenset({
    "AB", "DD", "DG", "EH", "FK", "G", "HS", "IV", "KA", "KW", "KY",
    "ML", "PA", "PH", "TD", "ZE",
})
_WELSH_AREAS = frozenset({"CF", "LD", "LL", "NP", "SA"})
_NI_AREAS = frozenset({"BT"})

# Lowercase country strings from the API that map directly to a canonical name.
_COUNTRY_CANONICAL = {
    "england": "England",
    "e":       "England",
    "scotland": "Scotland",
    "s":        "Scotland",
    "wales":    "Wales",
    "w":        "Wales",
    "northern ireland": "Northern Ireland",
    "n":                "Northern Ireland",
}

# ------------------------------------------------------------------------------


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
                "node_id":                        r["node_id"],
                "brand_name":                     r.get("brand_name"),
                "trading_name":                   r.get("trading_name"),
                "is_motorway_service_station":    r.get("is_motorway_service_station"),
                "is_supermarket_service_station": r.get("is_supermarket_service_station"),
                "temporary_closure":              r.get("temporary_closure"),
                "permanent_closure":              r.get("permanent_closure"),
                "postcode":                       loc.get("postcode"),
                "latitude":                       loc.get("latitude"),
                "longitude":                      loc.get("longitude"),
                "city":                           loc.get("city"),
                "county":                         loc.get("county"),
                "country":                        loc.get("country"),
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
            "snapshots (same effective timestamp, different price). Keeping the value "
            "with the latest price_last_updated, treating it as a station correction.",
            stacklevel=2,
        )

    first_seen = (
        prices.groupby(key)["snapshot_pulled_at"]
        .min()
        .rename("first_seen_at")
        .reset_index()
    )
    # On a collision (same effective timestamp, different price) keep the row with
    # the latest price_last_updated: a later update to the same event is the station
    # correcting an entry error, so the correction wins. For the normal case (all
    # rows identical) this changes nothing.
    one_per_key = (
        prices.sort_values(["price_last_updated", "snapshot_pulled_at"], na_position="first")
        .drop_duplicates(subset=key, keep="last")[key + ["price_ppl", "price_last_updated"]]
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


def assign_nearest_pfs_snapshot_per_station(
    node_ids: pd.Series,
    price_timestamps: pd.Series,
    pfs: pd.DataFrame,
) -> pd.Series:
    """
    Fallback for price events whose globally-nearest PFS snapshot did not
    include that particular station (e.g. a truncated pull that stopped
    early). For each (node_id, event timestamp), finds the pulled_at of the
    closest snapshot that DID include that specific node_id, searching both
    earlier and later snapshots, not just the previous one.

    Returns NaT for a node_id that has no PFS record in ANY snapshot at all
    (a genuinely unmatched station, as opposed to one just missing from the
    globally-nearest snapshot).
    """
    station_times = (
        pfs.sort_values("pfs_pulled_at")
        .groupby("node_id")["pfs_pulled_at"]
        .apply(list)
        .to_dict()
    )

    results = []
    for node_id, ts in zip(node_ids, price_timestamps):
        times = station_times.get(node_id)
        if not times:
            results.append(pd.NaT)
            continue
        times_unix = np.array([t.timestamp() for t in times])
        t_unix = ts.timestamp()
        idx = min(np.searchsorted(times_unix, t_unix), len(times) - 1)
        idx_prev = max(idx - 1, 0)
        best = (
            times[idx_prev]
            if abs(times_unix[idx_prev] - t_unix) <= abs(times_unix[idx] - t_unix)
            else times[idx]
        )
        results.append(best)

    return pd.Series(results, index=node_ids.index, name="pfs_snapshot_used")


def _normalize_brand(raw) -> str | None:
    if pd.isna(raw):
        return None
    s = str(raw).strip()
    if not s or s in _BRAND_NULL_VALUES:
        return None
    # Catch all variants of "BP" before title-casing (strips spaces, compares uppercase).
    if re.sub(r"\s+", "", s).upper() == "BP":
        return "BP"
    # Consolidate all "no brand" variants regardless of capitalisation.
    if s.lower() in _UNBRANDED_ALIASES:
        return "Unbranded"
    s = s.title()
    # .title() capitalises after apostrophes: "Sainsbury'S" -> "Sainsbury's"
    s = re.sub(r"'([A-Z])", lambda m: "'" + m.group(1).lower(), s)
    return _BRAND_CORRECTIONS.get(s, s)


def _postcode_to_nation(postcode) -> str | None:
    """Infer the UK constituent nation from a postcode area prefix."""
    if pd.isna(postcode) or not postcode:
        return None
    m = re.match(r"^([A-Za-z]+)", str(postcode).strip().upper())
    if not m:
        return None
    area = m.group(1)
    if area in _NI_AREAS:
        return "Northern Ireland"
    if area in _SCOTTISH_AREAS:
        return "Scotland"
    if area in _WELSH_AREAS:
        return "Wales"
    return "England"


def _normalize_country(raw_country, postcode) -> str:
    """
    Return a canonical nation name.
    Known values (ENGLAND, E, Scotland, etc.) are mapped directly.
    Unknown or missing values (UNITED KINGDOM, UK, empty, NaN) fall back to
    postcode inference, then "UK Other" if the postcode is also missing.
    """
    if not pd.isna(raw_country):
        s = str(raw_country).strip().lower()
        if s in _COUNTRY_CANONICAL:
            return _COUNTRY_CANONICAL[s]
    nation = _postcode_to_nation(postcode)
    return nation if nation else "UK Other"


def _haversine_km(lat1, lon1, lat2, lon2):
    """Element-wise haversine distance in km between two coordinate arrays."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (
        np.sin((lat2 - lat1) / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def heal_coordinates(df: pd.DataFrame, pfs: pd.DataFrame) -> pd.DataFrame:
    """
    Replace per-event station coordinates with one canonical, validated
    coordinate per station.

    Some PFS snapshots carry corrupted coordinates for a small subset of
    stations (observed 2026-07-03: lat/long swapped, longitude sign flipped,
    signs dropped, or entirely wrong values; heaviest in the 2026-06-24
    snapshot, 92 stations affected in total). A station does not move, so
    per-event coordinates only add corruption risk: every event gets the
    station's best coordinate across ALL snapshots instead.

    Selection per station, validated against the NSPL unit-postcode centroid:
      1. latest observation inside the UK bounding box and within
         MAX_KM_FROM_POSTCODE of the postcode centroid ("observed");
      2. else latest observation inside the UK bounding box, only when no
         centroid is available to validate against ("observed");
      3. else the postcode centroid itself ("postcode_centroid", accurate to
         roughly 100 m, good enough for mapping and competition features).
         This includes stations whose in-box observations ALL disagree with
         a known centroid by >MAX_KM_FROM_POSTCODE: those observations match
         known corruption modes (a flipped longitude sign keeps a station
         inside the box but ~100 km off), while the postcode is modal across
         snapshots and corroborated by the station's town and country fields,
         so the centroid is the more trustworthy witness. Logged to QC;
      4. else coordinates are nulled, warned, never dropped.

    Adds a coord_source column ("observed", "postcode_centroid", or None).
    """
    postcodes = pd.read_parquet(
        POSTCODES_IN, columns=["pcd_key", "postcode_lat", "postcode_long"]
    )

    obs = pfs[["node_id", "latitude", "longitude", "postcode", "pfs_pulled_at"]].copy()

    # One postcode per station (the modal value across snapshots, so a
    # corrupted postcode in one snapshot cannot mislead the centroid check).
    modal_pc = (
        obs.dropna(subset=["postcode"])
        .groupby("node_id")["postcode"]
        .agg(lambda s: s.mode().iat[0])
        .rename("modal_postcode")
        .reset_index()
    )
    modal_pc["pcd_key"] = (
        modal_pc["modal_postcode"].str.replace(r"\s+", "", regex=True).str.upper()
    )
    modal_pc = modal_pc.merge(postcodes, on="pcd_key", how="left")

    obs = obs.dropna(subset=["latitude", "longitude"]).merge(
        modal_pc.drop(columns="pcd_key"), on="node_id", how="left"
    )
    in_box = (
        obs["latitude"].between(*UK_LAT_RANGE)
        & obs["longitude"].between(*UK_LON_RANGE)
    )
    dist_km = _haversine_km(
        obs["latitude"], obs["longitude"],
        obs["postcode_lat"], obs["postcode_long"],
    )
    near_postcode = dist_km <= MAX_KM_FROM_POSTCODE  # False where centroid unknown

    obs["tier"] = np.select([in_box & near_postcode, in_box], [1, 2], default=3)
    obs["dist_km"] = dist_km

    best = (
        obs[obs["tier"] < 3]
        .sort_values(["tier", "pfs_pulled_at"], ascending=[True, False])
        .drop_duplicates(subset="node_id", keep="first")
        .set_index("node_id")
    )

    # Stations whose every in-box observation disagrees with a known centroid
    # by >15 km take the centroid instead (see docstring). Logged for review.
    disputed = best[(best["tier"] == 2) & best["postcode_lat"].notna()]
    if len(disputed):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        qc_path = QC_DIR / f"coord_postcode_disagreement_{stamp}.csv"
        disputed.reset_index()[[
            "node_id", "modal_postcode", "latitude", "longitude", "dist_km",
        ]].to_csv(qc_path, index=False)
        print(
            f"  {len(disputed)} stations have in-UK coordinates >"
            f"{MAX_KM_FROM_POSTCODE:.0f} km from their postcode centroid, "
            f"replaced with the centroid: {qc_path}"
        )
        best = best.drop(disputed.index)

    canonical = modal_pc.set_index("node_id")[["postcode_lat", "postcode_long"]]
    canonical = canonical.join(best[["latitude", "longitude"]], how="outer")
    from_obs = canonical["latitude"].notna()
    canonical["coord_source"] = None
    canonical.loc[from_obs, "coord_source"] = "observed"
    centroid_fill = ~from_obs & canonical["postcode_lat"].notna()
    canonical.loc[centroid_fill, "latitude"] = canonical.loc[centroid_fill, "postcode_lat"]
    canonical.loc[centroid_fill, "longitude"] = canonical.loc[centroid_fill, "postcode_long"]
    canonical.loc[centroid_fill, "coord_source"] = "postcode_centroid"

    df = df.copy()
    had_coords = df["latitude"].notna()
    df["latitude"] = df["node_id"].map(canonical["latitude"])
    df["longitude"] = df["node_id"].map(canonical["longitude"])
    df["coord_source"] = df["node_id"].map(canonical["coord_source"])

    n_centroid = int((df["coord_source"] == "postcode_centroid").sum())
    nulled = had_coords & df["latitude"].isna()
    if nulled.any():
        warnings.warn(
            f"{int(nulled.sum())} price events "
            f"({df.loc[nulled, 'node_id'].nunique()} stations) had coordinates "
            "nulled: no valid observation in any snapshot and no postcode "
            "centroid. They stay in silver without a location.",
            stacklevel=2,
        )
    print(
        "  Coordinates healed: "
        f"{int((df['coord_source'] == 'observed').sum()):,} events from validated "
        f"observations, {n_centroid:,} from postcode centroids "
        f"({df.loc[df['coord_source'] == 'postcode_centroid', 'node_id'].nunique()} stations)"
    )
    return df


def clean_silver(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply four cleaning steps to the enriched silver dataframe:
      1. Drop price events outside the plausible range [50p, 300p].
      2. Normalise brand_name: title-case, consolidate unbranded variants,
         preserve acronyms (BP), fix known typos.
      3. Normalise country: canonical names for known values; postcode
         inference for UNITED KINGDOM / UK / empty / NaN; "UK Other" if
         the postcode is also missing.
      4. Drop known-bad records: test/staging brands leaked into the live
         PFS endpoint, and specific node_ids confirmed to carry implausible
         one-off data.
    """
    df = df.copy()

    # 1. Price outliers
    before = len(df)
    df = df[(df["price_ppl"] >= PRICE_MIN_PPL) & (df["price_ppl"] <= PRICE_MAX_PPL)]
    n_dropped = before - len(df)
    if n_dropped:
        print(f"  Dropped {n_dropped} price events outside "
              f"[{PRICE_MIN_PPL:.0f}p, {PRICE_MAX_PPL:.0f}p] as implausible")

    # 2. Brand names
    df["brand_name"] = df["brand_name"].map(_normalize_brand)

    # 3. Country
    df["country"] = df.apply(
        lambda r: _normalize_country(r["country"], r["postcode"]), axis=1
    )

    # 4. Known-bad records
    before = len(df)
    is_test_brand = df["brand_name"].str.contains(
        "|".join(_TEST_BRAND_MARKERS), case=False, na=False
    )
    is_excluded_node = df["node_id"].isin(_EXCLUDED_NODE_IDS)
    df = df[~(is_test_brand | is_excluded_node)]
    n_dropped = before - len(df)
    if n_dropped:
        print(f"  Dropped {n_dropped} price events from known-bad records "
              f"(test/staging brands or excluded node_ids)")

    return df


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
    event_cols = [
        "node_id", "fuel_type", "price_ppl",
        "price_change_effective_timestamp", "price_last_updated", "first_seen_at",
    ]

    def join_pfs(df: pd.DataFrame) -> pd.DataFrame:
        """Left-join station details onto (node_id, pfs_snapshot_used), preserving df's index."""
        joined = df.merge(
            pfs_renamed,
            left_on=["node_id", "pfs_snapshot_used"],
            right_on=["node_id", "pfs_pulled_at"],
            how="left",
        ).drop(columns=["pfs_pulled_at"])
        joined.index = df.index
        return joined

    enriched = join_pfs(prices)
    enriched["pfs_snapshot_is_fallback"] = False

    # QC: use latitude (not brand_name) to detect a missing PFS record, because
    # brand_name can legitimately be null for unbranded stations.
    no_pfs_match = enriched["latitude"].isna()
    if no_pfs_match.any():
        n_initial = int(no_pfs_match.sum())
        print(
            f"  {n_initial} price events did not match the globally-nearest PFS "
            "snapshot (e.g. a truncated pull that stopped early). Retrying with a "
            "per-station nearest match..."
        )

        # Fallback: for just the unmatched events, find the nearest snapshot that
        # DID include that specific station (searching both earlier and later
        # snapshots), instead of the snapshot nearest in time across all stations.
        fallback_input = enriched.loc[no_pfs_match, event_cols].copy()
        fallback_input["pfs_snapshot_used"] = assign_nearest_pfs_snapshot_per_station(
            fallback_input["node_id"],
            fallback_input["price_change_effective_timestamp"],
            pfs,
        )
        fallback_joined = join_pfs(fallback_input)
        fallback_joined["pfs_snapshot_is_fallback"] = True

        recovered = fallback_joined["latitude"].notna()
        n_recovered = int(recovered.sum())
        print(
            f"  Recovered {n_recovered} of {n_initial} via per-station fallback "
            "match (borrowed station details from the nearest snapshot that did "
            "include that station)."
        )

        recovered_idx = fallback_joined.index[recovered]
        enriched.loc[recovered_idx, fallback_joined.columns] = fallback_joined.loc[recovered_idx]

    no_pfs_match_final = enriched["latitude"].isna()
    if no_pfs_match_final.any():
        n = int(no_pfs_match_final.sum())
        print(f"  WARNING: {n} price events have no matching PFS station record in ANY snapshot.")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        qc_path = QC_DIR / f"unmatched_prices_{stamp}.csv"
        enriched.loc[no_pfs_match_final, [
            "node_id", "fuel_type",
            "price_change_effective_timestamp", "pfs_snapshot_used",
        ]].to_csv(qc_path, index=False)
        print(f"  Written to: {qc_path}")
    else:
        print("  All price events matched to a PFS record.")

    print("Healing station coordinates...")
    enriched = heal_coordinates(enriched, pfs)

    print("Cleaning silver layer...")
    enriched = clean_silver(enriched)

    col_order = [
        "node_id", "fuel_type", "price_ppl",
        "price_change_effective_timestamp", "price_last_updated", "first_seen_at",
        "pfs_snapshot_used", "pfs_snapshot_is_fallback",
        "brand_name", "trading_name", "postcode",
        "latitude", "longitude", "coord_source", "city", "county", "country",
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
    print(f"  Countries:       {sorted(enriched['country'].dropna().unique())}")
    top_brands = (
        enriched[enriched["fuel_type"] == "E10"]
        .groupby("brand_name", dropna=False)["node_id"]
        .nunique()
        .nlargest(5)
    )
    print(f"  Top 5 brands (E10 stations):\n{top_brands.to_string()}")


if __name__ == "__main__":
    main()
