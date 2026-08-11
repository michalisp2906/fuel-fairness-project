"""
Signal 2 scores for the CURRENT state of every station, ready for the gold
layer and the app.

The validation harness (signal2_validation.py) answers a historical question:
"what was a normal margin in week 27?" The app asks a different one: "what is
a normal margin for THIS station right now?" Nothing in the out-of-fold file
answers that, so this script produces it.

Scoring method (decided 2026-08-11, cross-fitted current-week scoring):
  Refit the SAME five GroupKFold-by-grid-cell models the validation harness
  scores, then predict each station with the fold model that HELD ITS CELL
  OUT, feeding it that station's static features plus today's national market
  regime. Every station gets a score, and no station is scored by a model that
  trained on it.

  Rejected alternatives:
  * Reuse the out-of-fold predictions as-is. They belong to weeks that have
    passed, and since the regime features went in (Decision 7) a prediction is
    genuinely week-specific, so an old one is simply wrong for today's price.
  * Train one model on everything and predict today. Each station appears in
    roughly one row per dense week, so the model partly memorises its own
    overcharging and reports a small gap for exactly the stations we want to
    catch. That is the objection that excluded brand on 2026-07-03, arriving
    through the back door.

READ THE EXTRAPOLATION WARNING this script prints. Today's market regime
routinely sits outside the range the models were trained on, and LightGBM
clamps rather than extrapolating. Within-week RANKING survives that (the level
is common to every station), the absolute pence figure does not. This is the
same failure that made diesel lose to a regional median in the next-week
transfer check, so it is a known property, not a surprise.

Output: data/gold/signal2_scores.parquet, one row per scored station per fuel.
It sits in data/gold/ because it is COMMITTED: build_gold.py runs on every
snapshot push in CI and needs to read it, and data/features/ is gitignored.

Deliberately holds PREDICTIONS ONLY, not the leftover score. The leftover
(actual minus predicted) is computed in build_gold.py against the standing
price, so scores stay valid as prices move between runs, the same way Signal 1
is recomputed there rather than carried from the event date. That split is what
lets the model refresh weekly while the app reprices several times a day.

Run:
    .venv/bin/python build_signal2.py           (Mac)
    .venv/Scripts/python.exe build_signal2.py   (Windows)
"""
from __future__ import annotations

import warnings
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

from build_features import (
    MAX_WHOLESALE_STALENESS_DAYS,
    WHOLESALE_LAG_DAYS,
    _GRADE_TO_WHOLESALE_COL,
)
from signal2_validation import (
    CATEGORICAL_FEATURES,
    FEATURES,
    FEATURES_NO_HP,
    ISLAND_AREAS,
    ISLAND_DISTRICTS,
    LGBM_PARAMS,
    N_FOLDS,
    STATIC_NUMERIC,
    WEEK_NUMERIC,
    assign_grid_cells,
    load_station_weeks,
    outward_district,
)

WHOLESALE_IN = Path("data/external/wholesale_prices.parquet")
GOLD_IN = Path("data/gold/app_data.parquet")
SCORES_OUT = Path("data/gold/signal2_scores.parquet")

FUELS = ["E10", "B7_STANDARD"]


def current_market_features(fuel: str) -> dict:
    """
    The two national regime features as they stand today, on exactly the
    convention build_gold.py uses for Signal 1: the latest wholesale week at
    or before (today minus the 10-day lag), and its change over the preceding
    4 calendar weeks.

    Mirrors weekly_market_features() in the harness, which takes a 4-row shift
    on a weekly series, so the training values and this one mean the same
    thing.
    """
    wholesale = pd.read_parquet(WHOLESALE_IN).sort_values("date")
    col = _GRADE_TO_WHOLESALE_COL[fuel]
    lag_date = pd.Timestamp.now().normalize() - pd.Timedelta(days=WHOLESALE_LAG_DAYS)

    usable = wholesale[wholesale["date"] <= lag_date]
    if usable.empty:
        raise RuntimeError(
            f"No wholesale week at or before the lag date {lag_date.date()}. "
            "Re-run build_external.py."
        )
    now = usable.iloc[-1]

    staleness = (lag_date - now["date"]).days
    if staleness > MAX_WHOLESALE_STALENESS_DAYS:
        warnings.warn(
            f"Latest usable wholesale week ({now['date'].date()}) is "
            f"{staleness} days before the lag date. Scores will describe a "
            "market regime that has moved on; re-run build_external.py.",
            stacklevel=2,
        )

    prior_target = now["date"] - pd.Timedelta(weeks=4)
    prior_rows = wholesale[wholesale["date"] <= prior_target]
    if prior_rows.empty:
        raise RuntimeError("Wholesale series too short for a 4-week change.")
    prior = prior_rows.iloc[-1]
    if prior["date"] != prior_target:
        warnings.warn(
            f"No wholesale week exactly 4 weeks before {now['date'].date()}; "
            f"using {prior['date'].date()}. The 4-week change is approximate.",
            stacklevel=2,
        )

    return {
        "wholesale_ppl": float(now[col]),
        "wholesale_chg_4w": float(now[col] - prior[col]),
        "wholesale_week": now["date"],
    }


def report_extrapolation(table: pd.DataFrame, market: dict) -> bool:
    """
    Say plainly whether today's regime sits inside the training range. Returns
    True if both features are inside it.

    Trees cannot extrapolate: a value past the edge of training gets the
    prediction of the most extreme training week, silently. That is worth a
    loud line in the log rather than a footnote in a docstring.
    """
    inside = True
    print("  Today's market regime vs the training range:")
    for feat in WEEK_NUMERIC:
        lo, hi = float(table[feat].min()), float(table[feat].max())
        value = market[feat]
        ok = lo <= value <= hi
        inside &= ok
        verdict = "inside" if ok else "OUTSIDE, prediction clamps"
        print(
            f"    {feat:18s} today {value:8.2f}   "
            f"training range [{lo:.2f}, {hi:.2f}]   {verdict}"
        )
    if not inside:
        print(
            "    Level is unreliable this week; the within-week RANKING is\n"
            "    unaffected because the regime term is common to all stations."
        )
    return bool(inside)


def fit_fold_models(table: pd.DataFrame) -> tuple[list, dict]:
    """
    Refit the five fold models and keep them, together with a map from grid
    cell to the fold that held that cell OUT.

    GroupKFold does not shuffle and LGBM_PARAMS pins random_state, so these
    are the same models spatial_cv() builds. That is the point: the number the
    app shows is then the same quantity the published CV metrics describe.
    """
    gkf = GroupKFold(n_splits=N_FOLDS)
    models: list[tuple] = []
    cell_to_fold: dict[str, int] = {}
    print(f"  Fitting {N_FOLDS} fold models (full and affluence-blind)")
    for fold, (tr_idx, te_idx) in enumerate(
        gkf.split(table, groups=table["cell_id"])
    ):
        train = table.iloc[tr_idx]
        model = lgb.LGBMRegressor(**LGBM_PARAMS)
        model.fit(
            train[FEATURES], train["overcharge_ppl"],
            categorical_feature=CATEGORICAL_FEATURES,
        )
        model_nohp = lgb.LGBMRegressor(**LGBM_PARAMS)
        model_nohp.fit(
            train[FEATURES_NO_HP], train["overcharge_ppl"],
            categorical_feature=CATEGORICAL_FEATURES,
        )
        models.append((model, model_nohp))
        for cell in table.iloc[te_idx]["cell_id"].unique():
            cell_to_fold[cell] = fold
    return models, cell_to_fold


def station_rows(table: pd.DataFrame, market: dict) -> pd.DataFrame:
    """
    One row per station: the static features exactly as the modelling table
    carries them (latest observation), with today's regime bolted on.
    """
    keep = ["node_id", "cell_id"] + STATIC_NUMERIC + CATEGORICAL_FEATURES
    stations = (
        table.sort_values("week")
        .drop_duplicates("node_id", keep="last")[keep]
        .reset_index(drop=True)
    )
    for feat in WEEK_NUMERIC:
        stations[feat] = market[feat]
    return stations


def score_fuel(fuel: str) -> pd.DataFrame:
    print(f"\n=== {fuel} ===")
    table = assign_grid_cells(load_station_weeks(fuel))
    market = current_market_features(fuel)
    print(
        f"  Scoring for wholesale week "
        f"{pd.Timestamp(market['wholesale_week']).date()}"
    )
    in_range = report_extrapolation(table, market)

    models, cell_to_fold = fit_fold_models(table)
    stations = station_rows(table, market)

    fold = stations["cell_id"].map(cell_to_fold)
    # A cell absent from the map never appeared in the modelling table, so no
    # fold trained on it and any model is equally valid. Cannot happen while
    # stations come from that same table, but the map is keyed on data, so
    # guard it rather than trust it.
    unseen = int(fold.isna().sum())
    if unseen:
        print(f"  {unseen} stations in cells no fold held out; scored by fold 0")
        fold = fold.fillna(0)
    stations["scored_by_fold"] = fold.astype(int)

    pred = np.full(len(stations), np.nan)
    pred_nohp = np.full(len(stations), np.nan)
    for idx, (model, model_nohp) in enumerate(models):
        mask = (stations["scored_by_fold"] == idx).to_numpy()
        if not mask.any():
            continue
        sub = stations.loc[mask]
        pred[mask] = model.predict(sub[FEATURES])
        pred_nohp[mask] = model_nohp.predict(sub[FEATURES_NO_HP])

    out = pd.DataFrame({
        "node_id": stations["node_id"],
        "fuel_type": fuel,
        "pred_signal2_ppl": pred,
        "pred_signal2_nohp_ppl": pred_nohp,
        "scored_by_fold": stations["scored_by_fold"],
        "scored_for_week": pd.Timestamp(market["wholesale_week"]),
        "regime_in_training_range": in_range,
    })
    print(
        f"  Scored {len(out):,} stations: predicted overcharge median "
        f"{np.nanmedian(pred):.2f}p, p10 {np.nanpercentile(pred, 10):.2f}p, "
        f"p90 {np.nanpercentile(pred, 90):.2f}p"
    )
    spread = float(np.nanmax(pred) - np.nanmin(pred))
    print(
        f"  Cross-sectional spread {spread:.2f}p "
        f"(this is what ranks stations; the level is shared)"
    )
    return out


def report_gold_coverage(scores: pd.DataFrame) -> None:
    """
    How much of the app's table Signal 2 can actually score, and why the rest
    is missing. The excluded groups are deliberate (motorway stations and
    ferry-dependent islands are their own comparison groups, decided
    2026-07-03 and 2026-07-08), so the app must label them, never blank them.
    """
    if not GOLD_IN.exists():
        print("\nGold table absent, skipping coverage report.")
        return
    gold = pd.read_parquet(
        GOLD_IN, columns=["node_id", "fuel_type", "is_motorway", "postcode"]
    )
    gold["fuel_type"] = gold["fuel_type"].astype(str)
    merged = gold.merge(
        scores[["node_id", "fuel_type", "pred_signal2_ppl"]],
        on=["node_id", "fuel_type"], how="left",
    )
    district = outward_district(merged["postcode"].fillna(""))
    is_island = district.isin(ISLAND_DISTRICTS) | district.str[:2].isin(ISLAND_AREAS)

    unscored = merged["pred_signal2_ppl"].isna()
    print("\nGold coverage:")
    print(
        f"  {(~unscored).sum():,} of {len(merged):,} gold rows scored "
        f"({(~unscored).mean():.1%})"
    )
    reasons = {
        "motorway (own comparison group)": unscored & merged["is_motorway"].fillna(False),
        "ferry island (own comparison group)": unscored & ~merged["is_motorway"].fillna(False) & is_island,
    }
    accounted = np.zeros(len(merged), dtype=bool)
    for label, mask in reasons.items():
        accounted |= mask.to_numpy()
        print(f"    {int(mask.sum()):5,d}  {label}")
    other = int((unscored.to_numpy() & ~accounted).sum())
    print(f"    {other:5,d}  no dense-week history, no coordinates, or closed")


def main() -> None:
    scores = pd.concat([score_fuel(fuel) for fuel in FUELS], ignore_index=True)

    both = scores[["pred_signal2_ppl", "pred_signal2_nohp_ppl"]].dropna()
    print(
        f"\nAffluence-blind twin agrees with the main model at "
        f"Pearson {both.corr().iloc[0, 1]:.3f} on the predicted level; the "
        "app shows where they disagree."
    )

    SCORES_OUT.parent.mkdir(parents=True, exist_ok=True)
    scores["fuel_type"] = scores["fuel_type"].astype("category")
    for col in ("pred_signal2_ppl", "pred_signal2_nohp_ppl"):
        scores[col] = scores[col].astype("float32")
    scores.to_parquet(SCORES_OUT, index=False, compression="zstd")
    size_kb = SCORES_OUT.stat().st_size / 1024
    print(f"\nWriting Signal 2 scores -> {SCORES_OUT} ({size_kb:.0f} KB)")
    print(f"  Rows: {len(scores):,} ({scores['node_id'].nunique():,} stations)")

    report_gold_coverage(scores)


if __name__ == "__main__":
    main()
