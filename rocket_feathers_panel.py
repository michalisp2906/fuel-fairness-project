"""
Rocket and feathers, stage 2: the station-level panel.

Stage 1 (rocket_feathers.py) establishes the effect nationally on eight years
of DESNZ data. This module asks the question the national series CANNOT:

    Does the asymmetry differ by how much competition a station faces?

That is the project-specific contribution, and it is the point where the
rocket-and-feathers work joins up with Signal 2. If stations with few local
rivals rebuild thin margins just as fast but let fat margins sit for longer
than competitive stations do, that is a competition story, not a cost story.

Run (needs data/features/features.parquet, so rebuild silver + features first):
    .venv/Scripts/python.exe rocket_feathers_panel.py

--- What can and cannot be identified here ---------------------------------

READ THIS BEFORE QUOTING ANY NUMBER FROM THIS MODULE.

Every station in the panel faces the SAME national wholesale cost series. So
however many stations there are, the cost path has only as many independent
observations as there are weeks, currently about a dozen. That has a sharp
consequence:

  * The IMPACT coefficients (theta+, theta-, the immediate pass-through of a
    cost change) are identified off roughly a dozen time-series points. Their
    standard errors are not trustworthy no matter how large N looks, because
    the regressor is common to every station and does not vary in the cross
    section at all. They are reported as descriptive only.

  * The ERROR-CORRECTION coefficients (lambda+, lambda-) ARE identified
    cross-sectionally, because each station's own margin deviates from its own
    long-run level by a different amount in the same week. This is where the
    panel earns its place, and it is also exactly the term that carries the
    feathers result in stage 1.

So this module reports the LAMBDA asymmetry by competition group and treats
the impact terms as background. Standard errors are clustered by station.
Clustering by week as well would be preferable, since the common cost shock
correlates stations within a week, but with about a dozen weeks there are far
too few clusters for that to be reliable. Stated as a limitation rather than
papered over.

--- Why the LEVEL of lambda here is not comparable to stage 1 ---------------

Two biases, both a consequence of a short T, and both found by checking rather
than assumed away. They are why this module reports RATIOS between groups and
not lambda levels:

  1. MECHANICAL MEAN REVERSION (Nickell bias). The ECT is each station's margin
     minus that station's OWN mean margin, taken over the same ~10 weeks. A
     demeaned short series must sum to zero, so deviations revert by
     construction. In a dynamic panel with fixed effects the within estimator
     is biased by order 1/T, which at T=10 is large. This is why panel lambdas
     come out around -0.4 to -0.7 against -0.015 to -0.046 nationally. Those
     two numbers are NOT measuring the same thing and must never be quoted
     side by side as if they were.

  2. TOO FEW COST TURNING POINTS. Over the current window the national cost
     series has only nine weekly changes (petrol six up / three down, diesel
     five up / four down). The sign split that defines the whole exercise is
     therefore driven by a handful of national weeks. Diesel's apparent
     asymmetry in particular may be reading a single episode: wholesale spiked
     from 76.7p to 86.1p in August 2026, margins compressed, and retailers
     rebuilt them. That is one event, not an estimated regularity.

The bias in (1) is common across groups because T is common, so comparisons
BETWEEN groups are more defensible than levels. That is the only inference
this module attempts, and even that is provisional.

--- Other limitations, all documented in claude.md --------------------------

  * About 13 weeks of history, one and a bit market regimes.
  * Collection is weekdays only, with a 32-hour outage on 2026-08-10 and a
    week-long handover gap 2026-08-27 to 2026-09-02. Sparse weeks are dropped
    rather than interpolated.
  * A station that does not reprice still HAS a price, so standing prices are
    carried forward. That is not a data gap, it is the behaviour under study:
    sitting still while costs fall IS the feather.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

from rocket_feathers import FUELS, OUT_DIR, WHOLESALE_PATH

DATA_DIR = Path(__file__).resolve().parent / "data"
FEATURES_PATH = DATA_DIR / "features" / "features.parquet"

DUTY_PPL = 52.95        # since 2022-03-28, see claude.md
VAT_RATE = 1.20

# Map the modelled grades onto the national module's fuel keys.
GRADE_TO_FUEL = {"E10": "petrol", "B7_STANDARD": "diesel"}

PANEL_COST_LAGS = 2     # K. Kept small: the cost series has ~13 points.
PANEL_OWN_LAGS = 1      # J.
MIN_STATIONS_PER_WEEK = 3000   # drop sparse weeks (matches signal2_validation)
MIN_WEEKS_PER_STATION = 6      # a station needs enough weeks to have dynamics
LONG_RUN_SLOPE = 1.0    # imposed, see build_panel()


# --- Panel construction -----------------------------------------------------

def weekly_cost() -> pd.DataFrame:
    """
    National weekly wholesale cost on a week-commencing footing.

    Same alignment fix as the national module: the wholesale table is
    week-END labelled by resample("W-MON"), so shift back 7 days.
    """
    whl = pd.read_parquet(WHOLESALE_PATH)
    whl["week"] = pd.to_datetime(whl["date"]) - pd.Timedelta(days=7)
    return whl[["week", "petrol_wholesale_ppl", "diesel_wholesale_ppl"]]


def build_panel(grade: str) -> pd.DataFrame:
    """
    Balanced-ish station-week panel of ex-tax retail price and national cost.

    Standing prices are carried forward within a station: between price-change
    events the old price still stands, and a station holding its price while
    costs fall is precisely the behaviour being measured. Forward fill is
    therefore the correct treatment, not a convenience.

    The long-run relation imposes a slope of 1.0 rather than estimating one per
    station. Two reasons: with ~13 weeks there is nowhere near enough
    within-station variation to estimate a station-specific cointegrating
    slope, and economic theory says a permanent 1p cost rise should eventually
    move the ex-tax price by 1p. The station's own average margin plays the
    role of its intercept, so the ECT is that station's deviation from its OWN
    normal margin, not from the national one. That matters: it means a
    permanently expensive station is not flagged here simply for being
    expensive. This module measures adjustment SPEED, not level. Level is
    Signal 1 and Signal 2's job.
    """
    fuel = GRADE_TO_FUEL[grade]
    cost_col = FUELS[fuel]["wholesale"]

    f = pd.read_parquet(
        FEATURES_PATH,
        columns=[
            "node_id", "fuel_type", "price_ppl",
            "price_change_effective_timestamp",
            "is_motorway", "is_supermarket", "permanent_closure",
            "rival_count_5km", "dist_nearest_rival_km", "ruc_2fold",
        ],
    )
    f = f[(f["fuel_type"] == grade) & (~f["permanent_closure"].fillna(False))].copy()

    ts = f["price_change_effective_timestamp"]
    f["week"] = (ts - pd.to_timedelta(ts.dt.dayofweek, unit="D")).dt.normalize()
    f["week"] = f["week"].dt.tz_localize(None)

    # Guard against the handful of absurd effective timestamps in the feed
    # (one station reports 1964-01-01), which would otherwise create thousands
    # of empty forward-filled weeks.
    f = f[f["week"] >= f["week"].max() - pd.Timedelta(weeks=104)]

    # Last reported price in each station-week wins: it is the price standing
    # at the end of that week, which is what the next week reacts from.
    f = f.sort_values("price_change_effective_timestamp")
    sw = (
        f.groupby(["node_id", "week"], observed=True)
        .agg(price_ppl=("price_ppl", "last"))
        .reset_index()
    )

    # Reindex onto the full week grid per station and carry prices forward.
    weeks = pd.date_range(sw["week"].min(), sw["week"].max(), freq="W-MON")
    idx = pd.MultiIndex.from_product(
        [sw["node_id"].unique(), weeks], names=["node_id", "week"]
    )
    sw = sw.set_index(["node_id", "week"]).reindex(idx)
    sw["price_ppl"] = sw.groupby(level="node_id")["price_ppl"].ffill()
    sw = sw.dropna(subset=["price_ppl"]).reset_index()

    # Drop sparse weeks (collection gaps) before anything is differenced.
    per_week = sw.groupby("week")["node_id"].nunique()
    dense = per_week[per_week >= MIN_STATIONS_PER_WEEK].index
    sw = sw[sw["week"].isin(dense)]

    # Station attributes, one row per station.
    attrs = (
        f.sort_values("price_change_effective_timestamp")
        .groupby("node_id", observed=True)
        .agg(
            is_motorway=("is_motorway", "last"),
            is_supermarket=("is_supermarket", "last"),
            rival_count_5km=("rival_count_5km", "last"),
            dist_nearest_rival_km=("dist_nearest_rival_km", "last"),
            ruc_2fold=("ruc_2fold", "last"),
        )
    )
    sw = sw.merge(attrs, on="node_id", how="left")

    cost = weekly_cost().rename(columns={cost_col: "cost"})
    sw = sw.merge(cost[["week", "cost"]], on="week", how="inner")

    sw["retail"] = sw["price_ppl"] / VAT_RATE - DUTY_PPL

    # Keep stations with enough weeks to support lags and a difference.
    n = sw.groupby("node_id")["week"].transform("size")
    sw = sw[n >= MIN_WEEKS_PER_STATION]

    return sw.sort_values(["node_id", "week"]).reset_index(drop=True)


def add_ecm_terms(panel: pd.DataFrame) -> pd.DataFrame:
    """Differences, sign-split cost changes, and the sign-split station ECT."""
    p = panel.copy()
    g = p.groupby("node_id", observed=True)

    p["d_retail"] = g["retail"].diff()
    p["d_cost"] = g["cost"].diff()

    # ECT: how far this station's margin sits from its OWN average margin.
    p["margin"] = p["retail"] - LONG_RUN_SLOPE * p["cost"]
    p["ect"] = p["margin"] - p.groupby("node_id", observed=True)["margin"].transform("mean")

    g = p.groupby("node_id", observed=True)
    for i in range(PANEL_COST_LAGS + 1):
        p[f"dcost_pos_l{i}"] = g["d_cost"].shift(i).clip(lower=0)
        p[f"dcost_neg_l{i}"] = g["d_cost"].shift(i).clip(upper=0)
    p["ect_pos"] = g["ect"].shift(1).clip(lower=0)
    p["ect_neg"] = g["ect"].shift(1).clip(upper=0)
    for m in range(1, PANEL_OWN_LAGS + 1):
        p[f"d_retail_l{m}"] = g["d_retail"].shift(m)
    return p


# --- Estimation -------------------------------------------------------------

def fit_panel(p: pd.DataFrame) -> tuple:
    """Pooled asymmetric ECM, standard errors clustered by station."""
    cols = (
        [f"dcost_pos_l{i}" for i in range(PANEL_COST_LAGS + 1)]
        + [f"dcost_neg_l{i}" for i in range(PANEL_COST_LAGS + 1)]
        + ["ect_pos", "ect_neg"]
        + [f"d_retail_l{m}" for m in range(1, PANEL_OWN_LAGS + 1)]
    )
    d = p.dropna(subset=cols + ["d_retail"])
    if d["node_id"].nunique() < 50 or len(d) < 500:
        return None, None, d

    X = sm.add_constant(d[cols])
    model = sm.OLS(d["d_retail"], X).fit(
        cov_type="cluster", cov_kwds={"groups": d["node_id"].astype("category").cat.codes}
    )
    return model, cols, d


def summarise(model, label: str, n_stations: int, n_obs: int) -> dict:
    lam_fat = float(model.params["ect_pos"])
    lam_thin = float(model.params["ect_neg"])
    p_fat = float(model.pvalues["ect_pos"])
    p_thin = float(model.pvalues["ect_neg"])
    names = list(model.params.index)
    r = np.zeros(len(names))
    r[names.index("ect_pos")] = 1.0
    r[names.index("ect_neg")] = -1.0
    eq = model.f_test(r)
    return {
        "group": label,
        "n_stations": n_stations,
        "n_obs": n_obs,
        "lambda_fat": lam_fat,
        "p_fat": p_fat,
        "lambda_thin": lam_thin,
        "p_thin": p_thin,
        "ratio_thin_over_fat": abs(lam_thin) / abs(lam_fat) if lam_fat else np.nan,
        "equality_p": float(np.squeeze(eq.pvalue)),
        "feathers_pattern": bool(abs(lam_thin) > abs(lam_fat) and p_thin < 0.05),
    }


def competition_groups(p: pd.DataFrame) -> dict[str, pd.Series]:
    """
    The comparison the panel exists to make.

    Motorway stations are excluded from the competition split for the same
    reason Signal 2 excludes them: paired services on opposite carriageways
    make haversine rival counts meaningless there. They get their own row.
    """
    mw = p["is_motorway"].fillna(False)
    rivals = p["rival_count_5km"]
    lo, hi = rivals[~mw].quantile([1 / 3, 2 / 3])
    return {
        "All (non-motorway)": ~mw,
        f"Fewest rivals (<={lo:.0f} in 5km)": ~mw & (rivals <= lo),
        f"Middle ({lo:.0f}-{hi:.0f} in 5km)": ~mw & (rivals > lo) & (rivals <= hi),
        f"Most rivals (>{hi:.0f} in 5km)": ~mw & (rivals > hi),
        "Rural": ~mw & (p["ruc_2fold"] == "Rural"),
        "Urban": ~mw & (p["ruc_2fold"] == "Urban"),
        "Supermarket": ~mw & p["is_supermarket"].fillna(False),
        "Motorway": mw,
    }


def run_grade(grade: str) -> dict:
    print(f"\n{'=' * 78}\n{grade} station panel\n{'=' * 78}")
    panel = build_panel(grade)
    p = add_ecm_terms(panel)
    weeks = sorted(p["week"].unique())
    print(f"  {p['node_id'].nunique():,} stations, {len(weeks)} dense weeks "
          f"({pd.Timestamp(weeks[0]).date()} to {pd.Timestamp(weeks[-1]).date()})")
    print(f"  {len(p):,} station-weeks before lag losses")

    model, cols, d = fit_panel(p)
    if model is None:
        print("  NOT ENOUGH DATA to fit the panel. Skipping.")
        return {"grade": grade, "fitted": False}

    print(f"  estimation sample {len(d):,} station-weeks, "
          f"{d['node_id'].nunique():,} stations")
    sum_pos = sum(model.params[f"dcost_pos_l{i}"] for i in range(PANEL_COST_LAGS + 1))
    sum_neg = sum(model.params[f"dcost_neg_l{i}"] for i in range(PANEL_COST_LAGS + 1))
    print(f"\n  Impact pass-through (DESCRIPTIVE ONLY, see module docstring):")
    print(f"    cost rises {sum_pos:+.3f}p   cost falls {sum_neg:+.3f}p")
    print("    Not tested. The cost series is national, so these are identified")
    print(f"    off {len(weeks)} time points regardless of how many stations there are.")

    rows = [summarise(model, "All stations", d["node_id"].nunique(), len(d))]

    print("\n  ADJUSTMENT SPEED BY GROUP (this is the identified part)")
    print("  group                          stns   lam_fat  p_fat  lam_thin p_thin  ratio  eq_p")
    for label, mask in competition_groups(p).items():
        sub = p[mask]
        m, _, dd = fit_panel(sub)
        if m is None:
            print(f"  {label:<28} too few observations")
            continue
        r = summarise(m, label, dd["node_id"].nunique(), len(dd))
        rows.append(r)
        print(f"  {label:<28} {r['n_stations']:>5}  {r['lambda_fat']:>+8.4f} "
              f"{r['p_fat']:>6.3f}  {r['lambda_thin']:>+8.4f} {r['p_thin']:>6.3f}"
              f"  {r['ratio_thin_over_fat']:>5.2f} {r['equality_p']:>5.2f}")

    print("\n  Reading: ratio > 1 means thin margins are rebuilt faster than fat")
    print("  ones are competed away, i.e. feathers. Higher ratio = stronger.")
    print("  Compare ratios ACROSS rows only. The lambda levels carry a")
    print("  small-T mean-reversion bias and are not comparable to stage 1.")

    # State plainly whether the competition hypothesis actually got support,
    # so a reader skimming the output cannot take silence for confirmation.
    comp = [r for r in rows if "in 5km" in r["group"]]
    if len(comp) == 3:
        fewest, most = comp[0]["ratio_thin_over_fat"], comp[-1]["ratio_thin_over_fat"]
        supported = fewest > most and comp[0]["equality_p"] < 0.05
        print(f"\n  Competition hypothesis (fewest rivals should feather MOST):")
        print(f"    fewest rivals ratio {fewest:.2f} vs most rivals {most:.2f}")
        print(f"    -> {'SUPPORTED' if supported else 'NOT SUPPORTED by this sample'}")

    return {
        "grade": grade,
        "fitted": True,
        "n_stations": int(p["node_id"].nunique()),
        "n_weeks": len(weeks),
        "week_start": str(pd.Timestamp(weeks[0]).date()),
        "week_end": str(pd.Timestamp(weeks[-1]).date()),
        "impact_sum_pos": float(sum_pos),
        "impact_sum_neg": float(sum_neg),
        "groups": rows,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = {g: run_grade(g) for g in GRADE_TO_FUEL}

    frames = [
        pd.DataFrame(r["groups"]).assign(grade=g)
        for g, r in results.items() if r.get("fitted")
    ]
    if frames:
        pd.concat(frames, ignore_index=True).to_parquet(
            OUT_DIR / "rocket_feathers_panel.parquet", index=False
        )
    with open(OUT_DIR / "rocket_feathers_panel.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, default=str)

    print("\nPROVISIONAL. Short history, weekday-only collection with two known")
    print("gaps, and roughly one and a half market regimes. The national result")
    print("in rocket_feathers.py is the one with statistical weight behind it.")
    print(f"\nWrote {OUT_DIR / 'rocket_feathers_panel.parquet'}")


if __name__ == "__main__":
    main()
