"""
Signal 2 validation harness: spatial + temporal CV with the locked metric suite.

Implements the Signal 2 decisions recorded in claude.md on 2026-07-06/08:

  Unit of observation   station-week mean overcharge_ppl, separate models per
                        fuel, E10 first (quarantines the diesel proxy basis).
  Spatial validation    5-fold GroupKFold over ~25km grid cells. All rows for
                        a station stay on one side of any split. Cell size is
                        reasoned from the 5km rival radius: only stations near
                        a cell border can leak competition information across
                        folds (minor, documented).
  Temporal validation   train on all-but-last dense week, test on the last.
                        Thin while history is short; grows with collection.
  Exclusions            motorway stations (own comparison group; haversine
                        distances overstate their competition), ferry-dependent
                        islands (own comparison group; genuine delivery costs
                        we cannot calibrate), permanently closed stations.
  Metrics               accuracy is a gate, not a target. Headline: held-out
                        per-week Spearman. MAE (+RMSE) vs predict-zero and
                        within-fold regional-median baselines. Top-decile
                        capture. Week-over-week stability of the leftover
                        score (actual minus out-of-fold prediction).

Feature exclusions (decided 2026-07-03): brand, is_motorway, is_supermarket
are NOT features (own-type attributes would normalise group-wide premiums).
dist_nearest_supermarket_km stays (rival pressure from others is legitimate).

The model is deliberately untuned (fixed modest LightGBM defaults): tuning for
minimum error would push the model towards explaining away the overcharging
signal that the leftover score exists to surface.

Run (Windows: .venv\\Scripts\\python.exe, Mac: .venv/bin/python):
    python signal2_validation.py --fuel E10
"""
from __future__ import annotations

import argparse
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.model_selection import GroupKFold

FEATURES_IN = Path("data/features/features.parquet")
OOF_OUT_TEMPLATE = "data/features/signal2_cv_{fuel}.parquet"

CELL_KM = 25.0
N_FOLDS = 5
# Weeks before collection started only contain stations with old standing
# prices (a biased subset), so cross-sectional weeks need a minimum breadth.
MIN_STATIONS_PER_WEEK = 500
KM_PER_DEG_LAT = 110.574
KM_PER_DEG_LON = 111.320 * np.cos(np.radians(54.0))  # UK mid-latitude

# Ferry-dependent islands (no fixed road link to the mainland), by postcode
# district. Skye, Anglesey, Sheppey etc. have bridges and are NOT listed.
# Sources: ONS postcode geography; checked against stations present in silver.
ISLAND_DISTRICTS = {
    # Isles of Scilly
    "TR21", "TR22", "TR23", "TR24", "TR25",
    # Isle of Wight
    "PO30", "PO31", "PO32", "PO33", "PO34", "PO35", "PO36", "PO37", "PO38",
    "PO39", "PO40", "PO41",
    # Orkney (KW15-17) and Shetland (all ZE)
    "KW15", "KW16", "KW17", "ZE1", "ZE2", "ZE3",
    # Arran, Cumbrae, Bute
    "KA27", "KA28", "PA20",
    # Gigha (PA41), Islay (PA42-49), Jura (PA60), Colonsay (PA61),
    # Mull and neighbours (PA62-75), Iona (PA76), Tiree (PA77), Coll (PA78)
    "PA41", "PA42", "PA43", "PA44", "PA45", "PA46", "PA47", "PA48", "PA49",
    "PA60", "PA61", "PA62", "PA63", "PA64", "PA65", "PA66", "PA67", "PA68",
    "PA69", "PA70", "PA71", "PA72", "PA73", "PA74", "PA75", "PA76", "PA77",
    "PA78",
    # Small Isles (Eigg, Rum, Canna)
    "PH42", "PH43", "PH44",
}
# Outer Hebrides: the whole HS postcode area is islands.
ISLAND_AREAS = {"HS"}

NUMERIC_FEATURES = [
    "rival_count_1km", "rival_count_3km", "rival_count_5km",
    "dist_nearest_rival_km", "dist_nearest_supermarket_km",
    "n_rival_brands_5km", "median_house_price", "house_price_index",
]
CATEGORICAL_FEATURES = ["ruc21desc", "ruc_2fold"]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES

LGBM_PARAMS = {
    "objective": "regression_l1",   # MAE objective, robust to heavy tails
    "n_estimators": 500,
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_child_samples": 50,
    "colsample_bytree": 0.9,
    "subsample": 0.9,
    "subsample_freq": 1,
    "random_state": 42,
    "verbosity": -1,
}


def outward_district(postcode: pd.Series) -> pd.Series:
    """
    Postcode district (outward code), robust to missing spaces: strip all
    whitespace, drop the last three characters (the inward code is always
    three), what remains is the district. E.g. 'TF118TG' -> 'TF11'.
    """
    key = postcode.str.replace(r"\s+", "", regex=True).str.upper()
    return key.str[:-3]


def load_station_weeks(fuel: str) -> pd.DataFrame:
    """
    Build the station-week modelling table for one fuel: mean overcharge_ppl
    per station per ISO week (Mon-Sun), static features carried per station,
    exclusions applied and reported.
    """
    cols = [
        "node_id", "fuel_type", "price_change_effective_timestamp",
        "overcharge_ppl", "latitude", "longitude", "postcode",
        "is_motorway", "permanent_closure", "region",
    ] + FEATURES
    df = pd.read_parquet(FEATURES_IN, columns=cols)
    df = df[df["fuel_type"] == fuel].copy()
    print(f"  {len(df):,} {fuel} events, {df['node_id'].nunique():,} stations")

    district = outward_district(df["postcode"].astype("string"))
    area = district.str.extract(r"^([A-Z]{1,2})", expand=False)
    df["is_island"] = district.isin(ISLAND_DISTRICTS) | area.isin(ISLAND_AREAS)

    excluded = pd.Series(False, index=df.index)
    for mask, why in [
        (df["is_motorway"].fillna(False).astype(bool), "motorway (own group)"),
        (df["is_island"], "ferry-dependent island (own group)"),
        (df["permanent_closure"].fillna(False).astype(bool), "permanently closed"),
        (df["latitude"].isna() | df["longitude"].isna(), "no usable coordinates"),
        (df["overcharge_ppl"].isna(), "no overcharge (wholesale gap)"),
    ]:
        new = mask & ~excluded
        print(f"  Excluded {df.loc[new, 'node_id'].nunique():4d} stations: {why}")
        excluded |= mask
    df = df[~excluded]

    week = (
        df["price_change_effective_timestamp"]
        .dt.tz_localize(None)
        .dt.to_period("W-SUN")
    )
    df["week"] = week.dt.start_time

    grouped = df.groupby(["node_id", "week"], as_index=False).agg(
        overcharge_ppl=("overcharge_ppl", "mean"),
        n_events=("overcharge_ppl", "size"),
    )
    # Static per-station attributes (latest observation).
    station_cols = ["node_id", "latitude", "longitude", "region"] + FEATURES
    stations = (
        df.sort_values("price_change_effective_timestamp")
        .drop_duplicates("node_id", keep="last")[station_cols]
    )
    out = grouped.merge(stations, on="node_id", how="left")

    counts = out.groupby("week")["node_id"].size()
    print("  Stations per week:")
    for wk, n in counts.items():
        keep = "" if n >= MIN_STATIONS_PER_WEEK else "  (dropped, sparse)"
        print(f"    w/c {wk.date()}: {n:5d}{keep}")
    dense_weeks = counts[counts >= MIN_STATIONS_PER_WEEK].index
    out = out[out["week"].isin(dense_weeks)].copy()
    print(
        f"  Modelling table: {len(out):,} station-weeks, "
        f"{out['node_id'].nunique():,} stations, {len(dense_weeks)} weeks"
    )

    for col in CATEGORICAL_FEATURES:
        out[col] = out[col].astype("category")
    return out


def assign_grid_cells(df: pd.DataFrame) -> pd.DataFrame:
    """
    ~25km grid cells from an equirectangular projection at UK mid-latitude.
    Crude as a map projection, entirely adequate for spatial blocking: cell
    edges only need to be roughly 25km, not survey-grade.
    """
    df = df.copy()
    cell_x = np.floor(df["longitude"] * KM_PER_DEG_LON / CELL_KM).astype(int)
    cell_y = np.floor(df["latitude"] * KM_PER_DEG_LAT / CELL_KM).astype(int)
    df["cell_id"] = cell_x.astype(str) + "_" + cell_y.astype(str)
    n_cells = df["cell_id"].nunique()
    print(f"  {n_cells} occupied {CELL_KM:.0f}km grid cells")
    return df


def regional_median_baseline(
    train: pd.DataFrame, test: pd.DataFrame
) -> np.ndarray:
    """
    Predict each test station-week's overcharge as the median overcharge of
    TRAINING rows in its region (12 groups: 9 English regions, Wales,
    Scotland, Northern Ireland). Rows without a region fall back to the
    global training median. Computed within-fold, so the baseline never sees
    held-out stations.
    """
    medians = train.groupby("region", observed=True)["overcharge_ppl"].median()
    global_median = train["overcharge_ppl"].median()
    return test["region"].map(medians).fillna(global_median).to_numpy()


def point_metrics(y: np.ndarray, pred: np.ndarray) -> tuple[float, float]:
    err = y - pred
    return float(np.abs(err).mean()), float(np.sqrt((err**2).mean()))


def rank_metrics(df: pd.DataFrame, pred_col: str) -> tuple[float, float]:
    """
    Per-week Spearman and top-decile capture, averaged over weeks. Computed
    per week so week composition (which stations repriced) cannot masquerade
    as ranking skill.
    """
    rhos, captures = [], []
    for _, wk in df.groupby("week"):
        if len(wk) < 50:
            continue
        rho = spearmanr(wk["overcharge_ppl"], wk[pred_col]).statistic
        n_top = max(1, len(wk) // 10)
        actual_top = set(wk.nlargest(n_top, "overcharge_ppl")["node_id"])
        pred_top = set(wk.nlargest(n_top, pred_col)["node_id"])
        rhos.append(rho)
        captures.append(len(actual_top & pred_top) / n_top)
    return float(np.mean(rhos)), float(np.mean(captures))


def spatial_cv(df: pd.DataFrame) -> pd.DataFrame:
    """
    5-fold GroupKFold by grid cell. Returns df with out-of-fold model and
    baseline predictions: every station is predicted by a model that never
    saw its cell.
    """
    df = df.copy()
    df["pred_model"] = np.nan
    df["pred_regional"] = np.nan

    gkf = GroupKFold(n_splits=N_FOLDS)
    print(f"\nSpatial CV: {N_FOLDS}-fold GroupKFold by grid cell")
    header = (
        f"  {'fold':4s} {'test rows':>9s} {'cells':>6s} "
        f"{'MAE model':>10s} {'MAE zero':>9s} {'MAE region':>11s}"
    )
    print(header)
    for fold, (tr_idx, te_idx) in enumerate(
        gkf.split(df, groups=df["cell_id"])
    ):
        train, test = df.iloc[tr_idx], df.iloc[te_idx]
        model = lgb.LGBMRegressor(**LGBM_PARAMS)
        model.fit(
            train[FEATURES], train["overcharge_ppl"],
            categorical_feature=CATEGORICAL_FEATURES,
        )
        pred = model.predict(test[FEATURES])
        pred_reg = regional_median_baseline(train, test)
        df.iloc[te_idx, df.columns.get_loc("pred_model")] = pred
        df.iloc[te_idx, df.columns.get_loc("pred_regional")] = pred_reg

        y = test["overcharge_ppl"].to_numpy()
        mae_m, _ = point_metrics(y, pred)
        mae_z, _ = point_metrics(y, np.zeros(len(y)))
        mae_r, _ = point_metrics(y, pred_reg)
        print(
            f"  {fold + 1:4d} {len(test):9,d} {test['cell_id'].nunique():6d} "
            f"{mae_m:10.2f} {mae_z:9.2f} {mae_r:11.2f}"
        )
    return df


def report_pooled(df: pd.DataFrame) -> None:
    y = df["overcharge_ppl"].to_numpy()
    rows = [
        ("LightGBM (Signal 2)", df["pred_model"].to_numpy(), "pred_model"),
        ("predict-zero (Signal 1 alone)", np.zeros(len(df)), None),
        ("regional median", df["pred_regional"].to_numpy(), "pred_regional"),
    ]
    print("\nPooled out-of-fold results (all held-out station-weeks):")
    print(
        f"  {'':32s} {'MAE':>7s} {'RMSE':>7s} "
        f"{'Spearman/wk':>12s} {'top-decile':>11s}"
    )
    for name, pred, col in rows:
        mae, rmse = point_metrics(y, pred)
        if col is not None:
            rho, cap = rank_metrics(df, col)
            rank_txt = f"{rho:12.3f} {cap:11.1%}"
        else:
            rank_txt = f"{'n/a':>12s} {'n/a':>11s}"  # zero has no ranking
        print(f"  {name:32s} {mae:7.2f} {rmse:7.2f} {rank_txt}")


def temporal_check(df: pd.DataFrame) -> None:
    """
    Train on all dense weeks except the last, predict the last. Documented as
    thin while only a few dense weeks exist; strengthens with history.

    Honest caveat: train and test weeks share stations (unavoidable in a
    same-panel forward check), so this measures whether last week's learned
    mapping still ranks THIS week, not generalisation to unseen stations.
    The spatial CV is the unseen-station test; this is the regime-shift test.
    Expect these numbers to look better than the spatial CV for that reason.
    """
    weeks = sorted(df["week"].unique())
    if len(weeks) < 2:
        print("\nTemporal check: skipped, fewer than 2 dense weeks.")
        return
    last = weeks[-1]
    train = df[df["week"] < last]
    test = df[df["week"] == last].copy()
    model = lgb.LGBMRegressor(**LGBM_PARAMS)
    model.fit(
        train[FEATURES], train["overcharge_ppl"],
        categorical_feature=CATEGORICAL_FEATURES,
    )
    test["pred_temporal"] = model.predict(test[FEATURES])
    y = test["overcharge_ppl"].to_numpy()
    mae_m, _ = point_metrics(y, test["pred_temporal"].to_numpy())
    mae_z, _ = point_metrics(y, np.zeros(len(y)))
    mae_r, _ = point_metrics(y, regional_median_baseline(train, test))
    rho, cap = rank_metrics(test, "pred_temporal")
    print(
        f"\nTemporal check (train {len(weeks) - 1} week(s) -> "
        f"test w/c {pd.Timestamp(last).date()}, {len(test):,} stations):"
    )
    print(
        f"  MAE model {mae_m:.2f} | zero {mae_z:.2f} | regional {mae_r:.2f}"
        f" | Spearman {rho:.3f} | top-decile capture {cap:.1%}"
    )


def stability_check(df: pd.DataFrame) -> None:
    """
    Week-over-week Spearman of the leftover score (actual minus out-of-fold
    prediction) for stations present in consecutive dense weeks. This is the
    deliverable score; if it reshuffles randomly between weeks it is noise.
    Preliminary while history is thin.
    """
    df = df.copy()
    df["leftover"] = df["overcharge_ppl"] - df["pred_model"]
    weeks = sorted(df["week"].unique())
    print("\nLeftover score stability (week-over-week Spearman):")
    if len(weeks) < 2:
        print("  Skipped, fewer than 2 dense weeks.")
        return
    for w1, w2 in zip(weeks, weeks[1:]):
        a = df[df["week"] == w1].set_index("node_id")["leftover"]
        b = df[df["week"] == w2].set_index("node_id")["leftover"]
        common = a.index.intersection(b.index)
        if len(common) < 50:
            print(f"  w/c {w1.date()} -> {w2.date()}: skipped, "
                  f"only {len(common)} stations in both")
            continue
        rho = spearmanr(a.loc[common], b.loc[common]).statistic
        print(
            f"  w/c {w1.date()} -> {w2.date()}: rho {rho:.3f} "
            f"({len(common):,} stations in both weeks)"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fuel", default="E10", choices=["E10", "B7_STANDARD"])
    args = parser.parse_args()

    print(f"Building station-week table ({args.fuel})...")
    df = load_station_weeks(args.fuel)
    df = assign_grid_cells(df)

    df = spatial_cv(df)
    report_pooled(df)
    temporal_check(df)
    stability_check(df)

    out_path = Path(OOF_OUT_TEMPLATE.format(fuel=args.fuel.lower()))
    df.to_parquet(out_path, index=False)
    print(f"\nOut-of-fold predictions -> {out_path} (gitignored)")


if __name__ == "__main__":
    main()
