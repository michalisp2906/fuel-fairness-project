"""
Feature layer for the fair-price model: Signal 1 plus the Signal 2 inputs.

Signal 1 (cost-plus fair price) for the two modelled grades, E10 and
B7_STANDARD:

    fair_price_ppl = (wholesale + duty + fair_margin) * 1.2
    overcharge_ppl = price_ppl - fair_price_ppl

Wholesale is the weekly NYMEX proxy (RBOB for petrol, heating oil for diesel)
from data/external/wholesale_prices.parquet, lagged by WHOLESALE_LAG_DAYS to
reflect that pump prices track the cost of fuel bought days to weeks earlier
(CMA estimates 1 to 2 weeks wholesale-to-pump pass-through). The weekly rows
are labelled by week END (right-labelled W-MON resample in build_external.py),
and the as-of join looks backward from the lagged date, so each event only
ever sees wholesale weeks that had fully completed 10 days before the event:
no lookahead, effective information lag 10 to 16 days.

Competition features (static per station, computed over the full station
universe in silver, all grades, excluding permanently closed stations from
the rival set):

    rival_count_1km / _3km / _5km   stations within each haversine radius
    dist_nearest_rival_km           distance to the closest other station
    dist_nearest_supermarket_km     distance to the closest supermarket station
    n_rival_brands_5km              distinct rival brands within 5 km

Location features, joined via the station postcode through the NSPL lookup
(data/external/postcode_lookup.parquet):

    msoa21cd, ruc21desc, ruc_2fold  2021 MSOA and rural-urban classification
    median_house_price, house_price_index   ONS MSOA house prices (E+W only)

Brand is deliberately NOT a model feature (see claude.md): it would
normalise brand-wide premiums. It stays in the table for reporting only.

Run:
    .venv/bin/python build_features.py
"""
from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

SILVER_IN = Path("data/silver/prices_silver.parquet")
WHOLESALE_IN = Path("data/external/wholesale_prices.parquet")
DESNZ_IN = Path("data/external/desnz_pump_prices.parquet")
POSTCODES_IN = Path("data/external/postcode_lookup.parquet")
HOUSE_PRICES_IN = Path("data/external/msoa_house_prices.parquet")
FEATURES_DIR = Path("data/features")
FEATURES_OUT = FEATURES_DIR / "features.parquet"

EARTH_RADIUS_KM = 6371.0088
RIVAL_RADII_KM = (1.0, 3.0, 5.0)
BRAND_RADIUS_KM = 5.0

# Grades modelled first; other grades are captured in silver but parked.
MODELLED_GRADES = {"E10", "B7_STANDARD"}

# --- Signal 1 constants (locked, see project_definition.md) -------------------

DUTY_PPL = 52.95          # cut from 57.95p on 28 March 2022, frozen since
FAIR_MARGIN_PPL = 7.0     # CMA pre-2022 baseline retail margin
VAT_RATE = 1.20           # applied on top of the duty-inclusive price
WHOLESALE_LAG_DAYS = 10   # decided 2026-07-02; sensitivity at 7/14 planned

# --- Proxy basis calibration and flag threshold (decided 2026-07-03) ----------
#
# The NYMEX proxy systematically understates UK wholesale (freight, spec, and
# market differences; worst for diesel). We estimate the gap ("basis") per fuel
# from the national accounting identity: DESNZ national pump price, minus VAT,
# duty, and the CMA's measured retail margin, gives an implied UK wholesale;
# basis = implied UK wholesale - proxy. A CONSTANT basis over a long trailing
# window is used deliberately: the weekly basis series also contains genuine
# national margin swings (rockets and feathers), and a rolling calibration
# would absorb those into the correction, hiding market-wide overcharging
# dynamics that Signal 1 must keep visible. Over ~2 years those swings average
# out. Known cost: in any single month the corrected level can be off by a few
# pence (weekly basis std ~3.5p), shared by all stations equally, so
# cross-sectional comparisons are unaffected. FLAG_BUFFER_PPL (~1 std) absorbs
# this residual noise: a station is flagged only when its price exceeds the
# fair price by more than the buffer.
CMA_MARGIN_PPL = 10.7       # CMA road fuel monitoring, current avg retail margin
BASIS_WINDOW_WEEKS = 104
FLAG_BUFFER_PPL = 3.0

# If the backward join has to reach further back than this for a wholesale
# value, the wholesale parquet is stale (build_external.py needs a re-run).
MAX_WHOLESALE_STALENESS_DAYS = 21

_GRADE_TO_WHOLESALE_COL = {
    "E10": "petrol_wholesale_ppl",
    "B7_STANDARD": "diesel_wholesale_ppl",
}


def load_modelled_events() -> pd.DataFrame:
    """Load silver price events for the modelled grades only."""
    silver = pd.read_parquet(SILVER_IN)
    events = silver[silver["fuel_type"].isin(MODELLED_GRADES)].copy()
    print(
        f"  {len(events):,} events for modelled grades "
        f"({len(silver):,} total in silver), "
        f"{events['node_id'].nunique():,} stations"
    )
    return events


def join_lagged_wholesale(events: pd.DataFrame) -> pd.DataFrame:
    """
    Attach the lagged weekly wholesale price to each price event.

    For each event, uses the latest wholesale week that had fully completed
    WHOLESALE_LAG_DAYS before the event's effective timestamp.
    """
    wholesale = pd.read_parquet(WHOLESALE_IN)
    # Parquet stores this column at millisecond precision; align it with the
    # microsecond precision pandas uses for the event timestamps so merge_asof
    # accepts the pair.
    wholesale["date"] = wholesale["date"].astype("datetime64[us]")
    wholesale_long = wholesale.melt(
        id_vars="date",
        value_vars=list(_GRADE_TO_WHOLESALE_COL.values()),
        var_name="wholesale_col",
        value_name="wholesale_ppl",
    )
    col_to_grade = {v: k for k, v in _GRADE_TO_WHOLESALE_COL.items()}
    wholesale_long["fuel_type"] = wholesale_long["wholesale_col"].map(col_to_grade)
    wholesale_long = wholesale_long.drop(columns="wholesale_col")

    events = events.copy()
    events["wholesale_lag_date"] = (
        events["price_change_effective_timestamp"]
        .dt.tz_localize(None)
        .dt.normalize()
        - pd.Timedelta(days=WHOLESALE_LAG_DAYS)
    )

    joined = pd.merge_asof(
        events.sort_values("wholesale_lag_date"),
        wholesale_long.sort_values("date"),
        left_on="wholesale_lag_date",
        right_on="date",
        by="fuel_type",
        direction="backward",
    ).rename(columns={"date": "wholesale_week_end"})

    missing = joined["wholesale_ppl"].isna()
    if missing.any():
        warnings.warn(
            f"{missing.sum()} events have no wholesale price (event predates the "
            "wholesale series). Investigate before modelling.",
            stacklevel=2,
        )

    staleness = (joined["wholesale_lag_date"] - joined["wholesale_week_end"]).dt.days
    stale = staleness > MAX_WHOLESALE_STALENESS_DAYS
    if stale.any():
        warnings.warn(
            f"{stale.sum()} events joined to a wholesale week more than "
            f"{MAX_WHOLESALE_STALENESS_DAYS} days before their lag date. "
            "The wholesale parquet looks stale; re-run build_external.py.",
            stacklevel=2,
        )

    print(
        f"  Joined wholesale (lag {WHOLESALE_LAG_DAYS}d): weeks used "
        f"{joined['wholesale_week_end'].min().date()} to "
        f"{joined['wholesale_week_end'].max().date()}"
    )
    return joined


def build_station_table() -> pd.DataFrame:
    """
    One row per station with its most recent attributes, over ALL grades in
    silver (a station selling only unmodelled grades still competes on price).
    """
    silver = pd.read_parquet(SILVER_IN)
    stations = (
        silver.sort_values("price_change_effective_timestamp")
        .drop_duplicates(subset="node_id", keep="last")[[
            "node_id", "latitude", "longitude", "postcode", "brand_name",
            "is_supermarket", "is_motorway", "permanent_closure",
        ]]
        .reset_index(drop=True)
    )
    no_coords = stations["latitude"].isna() | stations["longitude"].isna()
    if no_coords.any():
        warnings.warn(
            f"{no_coords.sum()} stations have no coordinates; their "
            "competition features will be null.",
            stacklevel=2,
        )
    print(f"  {len(stations):,} stations in the universe")
    return stations


def compute_competition_features(stations: pd.DataFrame) -> pd.DataFrame:
    """
    Static competition features per station, using haversine distances over
    the full station table. Permanently closed stations are excluded from the
    rival set (they are not competitors) but still receive features.

    Pairwise distances for ~8,000 stations are computed in row chunks to keep
    memory bounded.
    """
    has_coords = stations["latitude"].notna() & stations["longitude"].notna()
    lat = np.radians(stations["latitude"].to_numpy(dtype=float))
    lon = np.radians(stations["longitude"].to_numpy(dtype=float))

    rival_mask = (has_coords & (stations["permanent_closure"] != True)).to_numpy()
    rival_idx = np.flatnonzero(rival_mask)
    r_lat, r_lon = lat[rival_idx], lon[rival_idx]
    r_super = stations["is_supermarket"].to_numpy()[rival_idx] == True
    r_brand = stations["brand_name"].to_numpy(dtype=object)[rival_idx]

    n = len(stations)
    counts = {r: np.full(n, np.nan) for r in RIVAL_RADII_KM}
    nearest = np.full(n, np.nan)
    nearest_super = np.full(n, np.nan)
    n_brands = np.full(n, np.nan)

    chunk_size = 500
    for start in range(0, n, chunk_size):
        rows = np.arange(start, min(start + chunk_size, n))
        rows = rows[has_coords.to_numpy()[rows]]
        if len(rows) == 0:
            continue
        # Haversine distance matrix: chunk rows x rival columns.
        dlat = lat[rows, None] - r_lat[None, :]
        dlon = lon[rows, None] - r_lon[None, :]
        a = (
            np.sin(dlat / 2) ** 2
            + np.cos(lat[rows, None]) * np.cos(r_lat[None, :]) * np.sin(dlon / 2) ** 2
        )
        d = 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))

        # A station is not its own rival.
        self_cols = np.searchsorted(rival_idx, rows)
        is_self = (self_cols < len(rival_idx)) & (
            rival_idx[np.minimum(self_cols, len(rival_idx) - 1)] == rows
        )
        d[np.flatnonzero(is_self), self_cols[is_self]] = np.inf

        for r in RIVAL_RADII_KM:
            counts[r][rows] = (d <= r).sum(axis=1)
        nearest[rows] = d.min(axis=1)
        if r_super.any():
            nearest_super[rows] = d[:, r_super].min(axis=1)
        within = d <= BRAND_RADIUS_KM
        for i, row in enumerate(rows):
            brands = r_brand[within[i]]
            n_brands[row] = len({b for b in brands if isinstance(b, str)})

    out = stations[["node_id"]].copy()
    for r in RIVAL_RADII_KM:
        out[f"rival_count_{r:.0f}km"] = counts[r]
    out["dist_nearest_rival_km"] = nearest
    out["dist_nearest_supermarket_km"] = nearest_super
    out["n_rival_brands_5km"] = n_brands

    with_rival_5km = (out["rival_count_5km"] > 0).mean()
    print(
        f"  Competition features done: median nearest rival "
        f"{out['dist_nearest_rival_km'].median():.2f} km, "
        f"{with_rival_5km:.0%} of stations have a rival within 5 km"
    )
    return out


def join_location_features(stations: pd.DataFrame) -> pd.DataFrame:
    """
    Join MSOA-level features onto stations via postcode: NSPL lookup for the
    2021 MSOA code and rural-urban classification, then ONS house prices on
    the MSOA code. House prices and RUC cover England and Wales (RUC also
    Scotland); Northern Ireland stations get nulls, a documented limitation.
    """
    postcodes = pd.read_parquet(POSTCODES_IN)
    house = pd.read_parquet(HOUSE_PRICES_IN)

    out = stations[["node_id", "postcode"]].copy()
    out["pcd_key"] = (
        out["postcode"].str.replace(r"\s+", "", regex=True).str.upper()
    )
    out = out.merge(
        postcodes[["pcd_key", "msoa21cd", "region", "ruc21desc", "ruc_2fold"]],
        on="pcd_key", how="left",
    )
    pc_match = out["msoa21cd"].notna().mean()

    out = out.merge(
        house[["msoa_code", "median_house_price", "house_price_index"]],
        left_on="msoa21cd", right_on="msoa_code", how="left",
    ).drop(columns=["msoa_code", "pcd_key", "postcode"])
    hp_match = out["median_house_price"].notna().mean()

    print(
        f"  Location features: {pc_match:.1%} of stations matched to an MSOA, "
        f"{hp_match:.1%} have a house price (England and Wales only)"
    )
    return out


def estimate_wholesale_basis() -> dict[str, float]:
    """
    Estimate the constant per-fuel basis (UK wholesale minus NYMEX proxy) from
    the national accounting identity over the trailing BASIS_WINDOW_WEEKS.

    implied UK wholesale = DESNZ pump / 1.2 - duty - CMA margin
    basis               = median(implied UK wholesale - lagged proxy)

    The proxy is lagged with the same convention as Signal 1 (pump prices for
    the week reflect wholesale ~10 days before mid-week).
    """
    desnz = pd.read_parquet(DESNZ_IN)
    wholesale = pd.read_parquet(WHOLESALE_IN)
    wholesale["date"] = wholesale["date"].astype("datetime64[us]")

    d = desnz.copy()
    d["week_commencing"] = d["week_commencing"].astype("datetime64[us]")
    d["lag_date"] = (
        d["week_commencing"]
        + pd.Timedelta(days=3)
        - pd.Timedelta(days=WHOLESALE_LAG_DAYS)
    )
    d = pd.merge_asof(
        d.sort_values("lag_date"),
        wholesale.sort_values("date"),
        left_on="lag_date", right_on="date",
        direction="backward",
    )

    basis = {}
    for grade, pump_col, duty_col, whl_col in (
        ("E10", "ulsp_pump_ppl", "ulsp_duty_ppl", "petrol_wholesale_ppl"),
        ("B7_STANDARD", "ulsd_pump_ppl", "ulsd_duty_ppl", "diesel_wholesale_ppl"),
    ):
        weekly = (
            d[pump_col] / VAT_RATE - d[duty_col] - CMA_MARGIN_PPL - d[whl_col]
        ).dropna().tail(BASIS_WINDOW_WEEKS)
        basis[grade] = float(weekly.median())
        print(
            f"  {grade}: basis +{basis[grade]:.1f}p over the proxy "
            f"(weekly std {weekly.std():.1f}p, {len(weekly)} weeks)"
        )
    return basis


def compute_signal1(events: pd.DataFrame) -> pd.DataFrame:
    """
    Compute the cost-plus fair price, the Signal 1 overcharge score, and the
    Signal 1 flag (overcharge above the measurement-noise buffer).
    """
    events = events.copy()
    basis = estimate_wholesale_basis()
    events["wholesale_basis_ppl"] = events["fuel_type"].map(basis)
    events["fair_price_ppl"] = (
        events["wholesale_ppl"] + events["wholesale_basis_ppl"]
        + DUTY_PPL + FAIR_MARGIN_PPL
    ) * VAT_RATE
    events["overcharge_ppl"] = events["price_ppl"] - events["fair_price_ppl"]
    events["signal1_flag"] = events["overcharge_ppl"] > FLAG_BUFFER_PPL
    return events


def main() -> None:
    print("Loading silver price events...")
    events = load_modelled_events()

    print("Joining lagged wholesale prices...")
    events = join_lagged_wholesale(events)

    print("Computing Signal 1 (cost-plus fair price)...")
    events = compute_signal1(events)

    print("Building station table...")
    stations = build_station_table()

    print("Computing competition features...")
    competition = compute_competition_features(stations)

    print("Joining location features (MSOA, RUC, house prices)...")
    location = join_location_features(stations)

    events = (
        events.merge(competition, on="node_id", how="left")
        .merge(location, on="node_id", how="left")
    )

    FEATURES_DIR.mkdir(parents=True, exist_ok=True)
    events.to_parquet(FEATURES_OUT, index=False)
    print(f"Writing feature layer -> {FEATURES_OUT}")

    print("\nDone.")
    print(f"  Rows: {len(events):,}")
    for grade in sorted(MODELLED_GRADES):
        sub = events[events["fuel_type"] == grade]
        oc = sub["overcharge_ppl"]
        print(
            f"  {grade:12s} overcharge_ppl: "
            f"median {oc.median():6.1f}  "
            f"p10 {oc.quantile(0.10):6.1f}  "
            f"p90 {oc.quantile(0.90):6.1f}  "
            f"flagged: {sub['signal1_flag'].mean():5.1%}"
        )


if __name__ == "__main__":
    main()
