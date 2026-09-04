"""
Rocket and feathers: do pump prices rise faster than they fall?

Fits an asymmetric error-correction model (ECM) of pump price on wholesale
cost, in the Bacon (1991) / Borenstein-Cameron-Gilbert (1997) tradition, and
tests whether cost INCREASES pass through faster than cost DECREASES.

Two levels of analysis, run in this order deliberately:

  NATIONAL (this module, stage 1). DESNZ weekly national average pump prices
  against the NYMEX wholesale proxy, 2018 to present, ~440 weekly observations.
  Properly powered, directly comparable to the published CMA work, and immune
  to the HO=F basis problem that limits Signal 1 (see "Why the proxy basis does
  not matter here" below).

  STATION PANEL (stage 2, rocket_feathers_panel.py). The project's own
  collected history. Novel, but short, so it is presented as provisional.

Run:
    .venv/Scripts/python.exe rocket_feathers.py

Outputs a printed report plus data/analysis/rocket_feathers_national.json and
data/analysis/rocket_feathers_crf.parquet (the cumulative response functions
the app and write-up plot).

--- Method ----------------------------------------------------------------

Work in EX-TAX retail terms, because duty and VAT are mechanical and would
otherwise dominate the dynamics:

    retail_t = pump_t / (1 + vat_t) - duty_t          [pence/litre]
    cost_t   = wholesale proxy                         [pence/litre]

Long run (cointegrating regression):

    retail_t = a + b * cost_t + u_t

Short run, asymmetric ECM:

    d_retail_t = mu
                 + sum_{i=0..K} ( thP_i * d_cost_plus_{t-i}
                                + thN_i * d_cost_minus_{t-i} )
                 + lamP * ECT_plus_{t-1}
                 + lamN * ECT_minus_{t-1}
                 + sum_{j=1..J} phi_j * d_retail_{t-j}
                 + e_t

where d_cost_plus = max(d_cost, 0), d_cost_minus = min(d_cost, 0), and the
lagged equilibrium error u_{t-1} is split by sign into ECT_plus (margin ABOVE
its long-run level) and ECT_minus (margin BELOW it).

Two distinct asymmetries, which the literature is careful to separate and
which mean different things commercially:

  1. SHORT-RUN / IMPACT asymmetry, H0: sum(thP) == sum(thN).
     "Rockets" is sum(thP) > sum(thN): a 1p cost rise is passed on faster
     than a 1p cost fall.

  2. ADJUSTMENT-SPEED asymmetry, H0: lamP == lamN.
     Both should be negative (margins revert). "Feathers" is |lamN| > |lamP|:
     a margin that is too THIN is rebuilt faster than a margin that is too
     FAT is competed away.

CAVEAT ON HOW THESE RESULTS ARE READ, and it applies to the headline finding.
In this data the informative-looking pattern is that lamN is significant while
lamP is not. That is WEAKER evidence than it appears: a difference in
significance is not itself a significant difference (Gelman and Stern, 2006).
Two coefficients can sit either side of the 5% line while being statistically
indistinguishable from each other, which is exactly what the equality test in
(2) reports here for the full sample. The `feathers_pattern` flag below encodes
that weaker reading and is deliberately named "pattern", not "result".
`feathers_strict` is the one backed by an actual test of difference. Quote the
pattern for its direction and its economic size, never as if the equality test
had rejected.

Standard errors are Newey-West (HAC), because weekly fuel prices are serially
correlated and OLS standard errors would overstate significance.

--- Why the proxy basis does not matter here ------------------------------

Signal 1 is sensitive to the NYMEX proxy's basis error (documented: HO=F may
understate UK diesel wholesale by 5-10p), because Signal 1 compares LEVELS.
This module compares DYNAMICS, so a CONSTANT basis is harmless: it is absorbed
into the intercept `a` of the long-run regression and differenced out of every
short-run term.

Be careful not to overclaim from that, though. Two things the proxy CAN still
do to these estimates, both stated as limitations rather than waved away:

  1. MEASUREMENT ERROR ATTENUATION. The proxy is a noisy measure of true UK
     wholesale cost. Classical errors-in-variables biases regression
     coefficients TOWARD ZERO, which shrinks theta+ and theta- and shrinks the
     estimated gap between them. So this design is biased toward FINDING NO
     ASYMMETRY. A null result here is weak evidence of symmetry; a positive
     result is strong evidence of asymmetry.
  2. CYCLE-DEPENDENT BASIS. If the US-UK basis itself widens on rising markets
     and narrows on falling ones, that would masquerade as pass-through
     asymmetry. We cannot rule this out without paid Platts/Argus data.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.tsa.stattools import adfuller, coint

DATA_DIR = Path(__file__).resolve().parent / "data"
DESNZ_PATH = DATA_DIR / "external" / "desnz_pump_prices.parquet"
WHOLESALE_PATH = DATA_DIR / "external" / "wholesale_prices.parquet"
OUT_DIR = DATA_DIR / "analysis"

# Fuels, mapping DESNZ columns to the matching wholesale proxy column.
FUELS = {
    "petrol": {
        "pump": "ulsp_pump_ppl",
        "duty": "ulsp_duty_ppl",
        "vat": "ulsp_vat_pct",
        "wholesale": "petrol_wholesale_ppl",
        "label": "Petrol (ULSP, proxy RBOB)",
    },
    "diesel": {
        "pump": "ulsd_pump_ppl",
        "duty": "ulsd_duty_ppl",
        "vat": "ulsd_vat_pct",
        "wholesale": "diesel_wholesale_ppl",
        "label": "Diesel (ULSD, proxy NYMEX heating oil)",
    },
}

MAX_COST_LAGS = 8       # K: weeks of cost changes to allow through
MAX_RETAIL_LAGS = 4     # J: own-lag momentum terms
HAC_LAGS = 8            # Newey-West bandwidth, ~2 months of weekly data
CRF_HORIZON = 26        # weeks to trace the cumulative response over


# --- Data -------------------------------------------------------------------

def load_national_panel() -> pd.DataFrame:
    """
    Weekly national series with the two week conventions reconciled.

    CRITICAL ALIGNMENT NOTE. Both tables are Monday-dated but mean opposite
    things, and getting this wrong shifts every estimated lag by a week:
      - DESNZ `week_commencing` D covers days D .. D+6.
      - wholesale `date` W comes from pandas resample("W-MON"), which labels a
        bin by its RIGHT edge, so W covers days W-6 .. W.
    So wholesale row W overlaps DESNZ week W-7 on six of seven days. We shift
    the wholesale date back a week to put both on a week-commencing footing.
    (The "to align with DESNZ data" comment in build_external.py is misleading
    on this point. build_features.py is unaffected: it correctly treats the
    wholesale table as week-END labelled for its 10-day lagged as-of join.)
    """
    pump = pd.read_parquet(DESNZ_PATH)
    whl = pd.read_parquet(WHOLESALE_PATH)

    pump["week_commencing"] = pd.to_datetime(pump["week_commencing"])
    whl["week_commencing"] = pd.to_datetime(whl["date"]) - pd.Timedelta(days=7)

    df = pump.merge(
        whl.drop(columns=["date"]), on="week_commencing", how="inner"
    ).sort_values("week_commencing").reset_index(drop=True)

    # Weekly data should be exactly 7 days apart. A gap means a missing week,
    # which would make lag i mean something different either side of it.
    gaps = df["week_commencing"].diff().dt.days.dropna()
    if not (gaps == 7).all():
        bad = df.loc[gaps[gaps != 7].index, "week_commencing"]
        raise ValueError(f"non-contiguous weeks in the national panel: {list(bad)}")
    return df


def build_fuel_series(df: pd.DataFrame, fuel: str) -> pd.DataFrame:
    """Ex-tax retail price and wholesale cost for one fuel, in pence/litre."""
    spec = FUELS[fuel]
    out = pd.DataFrame({"week_commencing": df["week_commencing"]})
    vat = 1.0 + df[spec["vat"]] / 100.0
    out["retail"] = df[spec["pump"]] / vat - df[spec["duty"]]
    out["cost"] = df[spec["wholesale"]]
    out["margin"] = out["retail"] - out["cost"]
    return out


# --- Pre-tests --------------------------------------------------------------

def stationarity_report(s: pd.Series, name: str) -> dict:
    """
    ADF tests on the level and the first difference.

    An ECM is only the right model if both series are I(1) (non-stationary in
    levels, stationary in differences) and they cointegrate. If they were
    already stationary in levels we should be running a plain regression; if
    they do not cointegrate, the error-correction term is spurious.
    """
    lvl = adfuller(s.dropna(), autolag="AIC")
    dif = adfuller(s.diff().dropna(), autolag="AIC")
    return {
        "series": name,
        "adf_level_stat": float(lvl[0]),
        "adf_level_p": float(lvl[1]),
        "adf_diff_stat": float(dif[0]),
        "adf_diff_p": float(dif[1]),
        "is_i1": bool(lvl[1] > 0.05 and dif[1] < 0.05),
    }


# --- Model ------------------------------------------------------------------

def _design(
    s: pd.DataFrame, k: int, j: int, restrict_slope: float | None = None
) -> tuple[pd.DataFrame, pd.Series, dict]:
    """
    Build the asymmetric ECM design matrix.

    Returns (X with constant, y, meta) where meta records which columns belong
    to which hypothesis, so the Wald tests do not depend on column ordering.

    `restrict_slope` imposes a known long-run pass-through coefficient instead
    of estimating it, so the equilibrium error becomes retail - b*cost - mean.
    Used as a robustness check with b=1: economic theory says a permanent 1p
    cost rise should eventually raise the ex-tax price by 1p, and imposing that
    matters most for diesel, where Engle-Granger sits right on the 5% boundary
    and the freely-estimated slope may be poorly identified.
    """
    d_retail = s["retail"].diff()
    d_cost = s["cost"].diff()

    # Long-run (cointegrating) relation, and the equilibrium error split by
    # sign. ECT > 0 means the margin sits ABOVE its long-run level.
    if restrict_slope is None:
        lr = sm.OLS(s["retail"], sm.add_constant(s["cost"])).fit()
        ect = lr.resid
        lr_const, lr_slope, lr_r2 = (
            float(lr.params.iloc[0]), float(lr.params.iloc[1]), float(lr.rsquared)
        )
    else:
        gap = s["retail"] - restrict_slope * s["cost"]
        lr_const, lr_slope = float(gap.mean()), float(restrict_slope)
        ect = gap - gap.mean()
        lr_r2 = float(np.nan)

    X = pd.DataFrame(index=s.index)
    pos_cols, neg_cols = [], []
    for i in range(k + 1):
        X[f"dcost_pos_l{i}"] = d_cost.clip(lower=0).shift(i)
        X[f"dcost_neg_l{i}"] = d_cost.clip(upper=0).shift(i)
        pos_cols.append(f"dcost_pos_l{i}")
        neg_cols.append(f"dcost_neg_l{i}")

    X["ect_pos"] = ect.clip(lower=0).shift(1)
    X["ect_neg"] = ect.clip(upper=0).shift(1)
    for m in range(1, j + 1):
        X[f"dretail_l{m}"] = d_retail.shift(m)

    X = sm.add_constant(X)
    meta = {
        "pos_cols": pos_cols,
        "neg_cols": neg_cols,
        "lr_const": lr_const,
        "lr_slope": lr_slope,
        "lr_r2": lr_r2,
        "ect": ect,
        "k": k,
        "j": j,
    }
    return X, d_retail, meta


def fit_asymmetric_ecm(
    s: pd.DataFrame,
    k: int,
    j: int,
    restrict_slope: float | None = None,
    hac_lags: int = HAC_LAGS,
):
    """Fit the ECM at fixed lag orders with Newey-West standard errors."""
    X, y, meta = _design(s, k, j, restrict_slope=restrict_slope)
    ok = X.notna().all(axis=1) & y.notna()
    model = sm.OLS(y[ok], X[ok]).fit(
        cov_type="HAC", cov_kwds={"maxlags": hac_lags}
    )
    meta["n_obs"] = int(ok.sum())
    return model, meta


def robustness(s: pd.DataFrame, k: int, j: int) -> pd.DataFrame:
    """
    Re-run the asymmetry tests under specifications that could plausibly
    overturn them. A finding that only survives one specification is not a
    finding, and this is the table to have ready for "what does it get wrong?".

    Variants:
      baseline          as reported.
      long run b=1      imposes one-for-one long-run pass-through instead of
                        estimating it. Matters most for diesel, where
                        Engle-Granger sits on the 5% boundary.
      excl 2020-2022    drops COVID demand collapse and the Ukraine invasion
                        spike. Those three years contain the largest cost moves
                        in the sample and could be driving the whole result.
      2022 onward       the CMA-scrutiny era only, which is the period the
                        write-up actually comments on.
      HAC 4 / HAC 16    halve and double the Newey-West bandwidth, to show the
                        p-values are not an artefact of one bandwidth choice.
    """
    yr = s["week_commencing"].dt.year
    variants = {
        "baseline": dict(sample=slice(None), slope=None, hac=HAC_LAGS),
        "long run b=1": dict(sample=slice(None), slope=1.0, hac=HAC_LAGS),
        "excl 2020-2022": dict(sample=~yr.between(2020, 2022), slope=None, hac=HAC_LAGS),
        "2022 onward": dict(sample=yr >= 2022, slope=None, hac=HAC_LAGS),
        "HAC 4": dict(sample=slice(None), slope=None, hac=4),
        "HAC 16": dict(sample=slice(None), slope=None, hac=16),
    }

    rows = []
    for name, cfg in variants.items():
        sub = s if isinstance(cfg["sample"], slice) else s[cfg["sample"]]
        sub = sub.reset_index(drop=True)
        model, meta = fit_asymmetric_ecm(
            sub, k, j, restrict_slope=cfg["slope"], hac_lags=cfg["hac"]
        )
        t = asymmetry_tests(model, meta)
        rows.append({
            "variant": name,
            "n_obs": meta["n_obs"],
            "sum_pos": t["shortrun_sum_pos"],
            "sum_neg": t["shortrun_sum_neg"],
            "shortrun_p": t["shortrun_p"],
            "lambda_fat": t["lambda_pos"],
            "p_fat": t["lambda_pos_p"],
            "lambda_thin": t["lambda_neg"],
            "p_thin": t["lambda_neg_p"],
            "equality_p": t["adjust_p"],
            "feathers_pattern": t["feathers_pattern"],
        })
    return pd.DataFrame(rows)


def select_lags(s: pd.DataFrame) -> tuple[int, int]:
    """
    Pick K and J by BIC on a common sample.

    BIC rather than AIC: it penalises extra lags harder, and here the risk is
    over-fitting a flexible sign-split model to a single national series and
    reading noise as asymmetry.
    """
    best, best_bic = (2, 1), np.inf
    max_k, max_j = MAX_COST_LAGS, MAX_RETAIL_LAGS
    # Hold the sample fixed at the largest model's, so BIC values are
    # comparable across lag orders rather than rewarding short lags for
    # quietly using more rows.
    Xf, yf, _ = _design(s, max_k, max_j)
    ok = Xf.notna().all(axis=1) & yf.notna()
    for k in range(1, max_k + 1):
        for j in range(0, max_j + 1):
            X, y, _ = _design(s, k, j)
            fit = sm.OLS(y[ok], X[ok]).fit()
            if fit.bic < best_bic:
                best, best_bic = (k, j), fit.bic
    return best


def asymmetry_tests(model, meta: dict) -> dict:
    """
    The two hypotheses that define rockets and feathers.

    Reported as one-sided readings of two-sided Wald tests: the F-test says
    whether the coefficients differ, the sign of the estimated gap says which
    way, and we only call it "rockets" or "feathers" when both agree.
    """
    names = list(model.params.index)

    def contrast(pos: list[str], neg: list[str]) -> np.ndarray:
        r = np.zeros(len(names))
        for c in pos:
            r[names.index(c)] = 1.0
        for c in neg:
            r[names.index(c)] = -1.0
        return r

    # 1. Cumulative short-run pass-through: sum(theta+) vs sum(theta-).
    r_short = contrast(meta["pos_cols"], meta["neg_cols"])
    t_short = model.f_test(r_short)
    sum_pos = float(sum(model.params[c] for c in meta["pos_cols"]))
    sum_neg = float(sum(model.params[c] for c in meta["neg_cols"]))

    # 2. Adjustment speed: lambda+ vs lambda-.
    r_adj = contrast(["ect_pos"], ["ect_neg"])
    t_adj = model.f_test(r_adj)
    lam_pos = float(model.params["ect_pos"])
    lam_neg = float(model.params["ect_neg"])

    # Individual significance of each adjustment speed. This matters as much as
    # the equality test: the informative pattern in this data is that the
    # BELOW-equilibrium term is significant while the ABOVE-equilibrium term is
    # not, i.e. thin margins are rebuilt but fat ones are not measurably
    # competed away, even where the two cannot be shown to differ from
    # each other at 5%.
    p_lam_pos = float(model.pvalues["ect_pos"])
    p_lam_neg = float(model.pvalues["ect_neg"])

    return {
        "shortrun_sum_pos": sum_pos,
        "shortrun_sum_neg": sum_neg,
        "shortrun_gap": sum_pos - sum_neg,
        "shortrun_F": float(np.squeeze(t_short.fvalue)),
        "shortrun_p": float(np.squeeze(t_short.pvalue)),
        "rockets": bool(sum_pos > sum_neg and np.squeeze(t_short.pvalue) < 0.05),
        "lambda_pos": lam_pos,
        "lambda_pos_p": p_lam_pos,
        "lambda_neg": lam_neg,
        "lambda_neg_p": p_lam_neg,
        "adjust_F": float(np.squeeze(t_adj.fvalue)),
        "adjust_p": float(np.squeeze(t_adj.pvalue)),
        "feathers_strict": bool(
            abs(lam_neg) > abs(lam_pos) and np.squeeze(t_adj.pvalue) < 0.05
        ),
        # The weaker, and here the more informative, reading.
        "feathers_pattern": bool(
            abs(lam_neg) > abs(lam_pos) and p_lam_neg < 0.05 and p_lam_pos >= 0.05
        ),
    }


def _half_life_from_path(path: np.ndarray, target: float) -> float | None:
    """
    Weeks for the cumulative response to reach half of its long-run value.

    NOT log(0.5)/log(1+lambda). That textbook formula assumes the ECT is the
    only dynamic term, and it is not: the fitted momentum coefficient on
    d_retail_{t-1} is about 0.62 in both fuels, which compounds each week's
    move into the next. Reading lambda alone gave half-lives of 45 to 47 weeks
    when the simulated system actually completes most of its adjustment inside
    a quarter. Deriving the half-life from the full simulated path is the only
    honest way to state it.

    Returns None if the path never reaches half of the long-run response,
    which is itself a finding rather than a number to fudge.
    """
    half = 0.5 * target
    for wk, v in enumerate(path):
        if (target > 0 and v >= half) or (target < 0 and v <= half):
            return float(wk)
    return None


# --- Cumulative response functions ------------------------------------------

def cumulative_response(model, meta: dict, shock: float, horizon: int) -> np.ndarray:
    """
    Trace the cumulative pump-price response to a PERMANENT `shock` pence
    change in wholesale cost, starting from long-run equilibrium.

    This is the headline visual: plot the +1p and -1p (sign-flipped) paths on
    the same axes and the gap between them IS the rockets-and-feathers effect,
    in pence, week by week.

    The intercept is set to zero deliberately. We are tracing the response to a
    shock, not the model's average drift, and mu would otherwise add a linear
    trend to both paths.
    """
    p = model.params
    k, j = meta["k"], meta["j"]

    # Work in DEVIATIONS from the pre-shock levels, both starting at zero and
    # in long-run equilibrium. The long-run intercept cancels in deviation
    # space; only the slope matters, and it enters through the ECT below.
    cost = 0.0
    retail = 0.0

    d_cost_hist: list[float] = []      # most recent first
    d_retail_hist: list[float] = []    # most recent first
    ect = 0.0
    path = []

    for t in range(horizon + 1):
        # The cost steps once at t=0 and then stays there permanently.
        d_cost = shock if t == 0 else 0.0
        cost += d_cost
        d_cost_hist.insert(0, d_cost)

        d_retail = 0.0
        for i in range(k + 1):
            if i < len(d_cost_hist):
                dc = d_cost_hist[i]
                d_retail += p[f"dcost_pos_l{i}"] * max(dc, 0.0)
                d_retail += p[f"dcost_neg_l{i}"] * min(dc, 0.0)
        d_retail += p["ect_pos"] * max(ect, 0.0)
        d_retail += p["ect_neg"] * min(ect, 0.0)
        for m in range(1, j + 1):
            if m - 1 < len(d_retail_hist):
                d_retail += p[f"dretail_l{m}"] * d_retail_hist[m - 1]

        retail += d_retail
        d_retail_hist.insert(0, d_retail)
        # Equilibrium error in deviation space: how far retail sits from where
        # the long-run relation says it should be given the new cost level.
        ect = retail - meta["lr_slope"] * cost
        path.append(retail)

    return np.asarray(path)


def build_crf(model, meta: dict, horizon: int = CRF_HORIZON) -> pd.DataFrame:
    """
    Response to a +1p rise and a -1p fall, with the fall sign-flipped so the
    two are directly comparable. `gap` > 0 means rises pass through faster.
    """
    up = cumulative_response(model, meta, +1.0, horizon)
    down = -cumulative_response(model, meta, -1.0, horizon)
    crf = pd.DataFrame({
        "week": np.arange(horizon + 1),
        "response_to_rise_ppl": up,
        "response_to_fall_ppl": down,
        "gap_ppl": up - down,
    })
    # Long-run target is the cointegrating slope: a permanent 1p cost move
    # should eventually move retail by `b` pence.
    target = meta["lr_slope"]
    crf.attrs["halflife_rise_weeks"] = _half_life_from_path(up, target)
    crf.attrs["halflife_fall_weeks"] = _half_life_from_path(down, target)
    return crf


# --- Report -----------------------------------------------------------------

def run_fuel(df: pd.DataFrame, fuel: str) -> dict:
    spec = FUELS[fuel]
    s = build_fuel_series(df, fuel)

    print(f"\n{'=' * 74}\n{spec['label']}\n{'=' * 74}")
    print(f"  {len(s)} weeks, {s.week_commencing.min().date()} to "
          f"{s.week_commencing.max().date()}")
    print(f"  ex-tax retail  mean {s.retail.mean():6.2f}p  "
          f"[{s.retail.min():.2f}, {s.retail.max():.2f}]")
    print(f"  wholesale cost mean {s.cost.mean():6.2f}p  "
          f"[{s.cost.min():.2f}, {s.cost.max():.2f}]")
    print(f"  implied margin mean {s.margin.mean():6.2f}p")

    print("\n  Pre-tests (an ECM needs I(1) series that cointegrate)")
    pre = {n: stationarity_report(s[n], n) for n in ("retail", "cost")}
    for r in pre.values():
        print(f"    ADF {r['series']:<7} level p={r['adf_level_p']:.3f}  "
              f"diff p={r['adf_diff_p']:.3f}  I(1): {r['is_i1']}")
    ct_stat, ct_p, _ = coint(s["retail"], s["cost"])
    print(f"    Engle-Granger cointegration p={ct_p:.4f}  "
          f"{'cointegrated' if ct_p < 0.05 else 'NOT cointegrated'}")

    k, j = select_lags(s)
    model, meta = fit_asymmetric_ecm(s, k, j)
    print(f"\n  Long run: retail = {meta['lr_const']:.2f} + "
          f"{meta['lr_slope']:.3f} * cost   (R2 {meta['lr_r2']:.3f})")
    print(f"  ECM lags chosen by BIC: K={k} cost, J={j} own   "
          f"(n={meta['n_obs']}, HAC lags={HAC_LAGS})")

    t = asymmetry_tests(model, meta)
    print("\n  1. SHORT-RUN PASS-THROUGH (rockets?)")
    print(f"     cost RISES  passed through {t['shortrun_sum_pos']:+.3f} "
          "p per 1p over K weeks")
    print(f"     cost FALLS  passed through {t['shortrun_sum_neg']:+.3f} "
          "p per 1p over K weeks")
    print(f"     gap {t['shortrun_gap']:+.3f}p   F={t['shortrun_F']:.2f}  "
          f"p={t['shortrun_p']:.4f}")
    print(f"     -> {'ROCKETS: rises pass faster' if t['rockets'] else 'no significant impact asymmetry'}")

    print("\n  2. MARGIN ADJUSTMENT SPEED (feathers?)")
    print(f"     margin ABOVE long run (too FAT):  lambda={t['lambda_pos']:+.4f}"
          f"  p={t['lambda_pos_p']:.3f}"
          f"  {'reverts' if t['lambda_pos_p'] < 0.05 else 'NOT significantly reverting'}")
    print(f"     margin BELOW long run (too THIN): lambda={t['lambda_neg']:+.4f}"
          f"  p={t['lambda_neg_p']:.3f}"
          f"  {'reverts' if t['lambda_neg_p'] < 0.05 else 'NOT significantly reverting'}")
    print(f"     equality test  F={t['adjust_F']:.2f}  p={t['adjust_p']:.4f}")
    if t["feathers_strict"]:
        print("     -> FEATHERS, and the two speeds differ significantly")
    elif t["feathers_pattern"]:
        print("     -> FEATHERS PATTERN: thin margins are rebuilt significantly,")
        print("        fat margins are not significantly eroded. But the two speeds")
        print("        cannot be shown to DIFFER from each other at 5%, and a")
        print("        difference in significance is not a significant difference")
        print("        (Gelman and Stern). Suggestive, not conclusive.")
    else:
        print("     -> no adjustment asymmetry")

    crf = build_crf(model, meta)
    hl_r = crf.attrs["halflife_rise_weeks"]
    hl_f = crf.attrs["halflife_fall_weeks"]
    print("\n  3. CUMULATIVE RESPONSE to a permanent 1p cost move")
    print("     week |  to rise |  to fall |   gap")
    for wk in (0, 1, 2, 4, 8, 13, 26):
        if wk < len(crf):
            r = crf.iloc[wk]
            print(f"     {wk:>4} | {r.response_to_rise_ppl:>7.3f}p | "
                  f"{r.response_to_fall_ppl:>7.3f}p | {r.gap_ppl:>+6.3f}p")
    print(f"     weeks to pass half the long-run move:  "
          f"rise {hl_r if hl_r is not None else 'never'},  "
          f"fall {hl_f if hl_f is not None else 'never'}")
    peak = crf.loc[crf["gap_ppl"].abs().idxmax()]
    print(f"     peak gap {peak.gap_ppl:+.3f}p at week {int(peak.week)}, "
          "per 1p of cost move, vs symmetric pass-through")

    rob = robustness(s, k, j)
    print("\n  4. ROBUSTNESS (does the pattern survive other specifications?)")
    print("     variant            n    lam_fat  p_fat  lam_thin p_thin  eq_p  pattern")
    for _, r in rob.iterrows():
        print(f"     {r.variant:<16} {r.n_obs:>4}  {r.lambda_fat:>+8.4f} {r.p_fat:>6.3f}"
              f"  {r.lambda_thin:>+8.4f} {r.p_thin:>6.3f} {r.equality_p:>5.2f}"
              f"   {'yes' if r.feathers_pattern else 'no'}")
    n_yes = int(rob["feathers_pattern"].sum())
    print(f"     -> feathers pattern holds in {n_yes} of {len(rob)} specifications")

    crf["fuel"] = fuel
    return {
        "fuel": fuel,
        "label": spec["label"],
        "n_weeks": int(len(s)),
        "start": str(s.week_commencing.min().date()),
        "end": str(s.week_commencing.max().date()),
        "pretests": pre,
        "coint_p": float(ct_p),
        "lr_const": meta["lr_const"],
        "lr_slope": meta["lr_slope"],
        "lr_r2": meta["lr_r2"],
        "k": k, "j": j, "n_obs": meta["n_obs"],
        "tests": t,
        "halflife_rise_weeks": crf.attrs["halflife_rise_weeks"],
        "halflife_fall_weeks": crf.attrs["halflife_fall_weeks"],
        "peak_gap_ppl": float(crf.loc[crf["gap_ppl"].abs().idxmax(), "gap_ppl"]),
        "peak_gap_week": int(crf.loc[crf["gap_ppl"].abs().idxmax(), "week"]),
        "robustness": rob.to_dict(orient="records"),
        "_crf": crf,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = load_national_panel()
    print(f"National panel: {len(df)} weeks, "
          f"{df.week_commencing.min().date()} to {df.week_commencing.max().date()}")

    results, crfs = {}, []
    for fuel in FUELS:
        r = run_fuel(df, fuel)
        crfs.append(r.pop("_crf"))
        results[fuel] = r

    pd.concat(crfs, ignore_index=True).to_parquet(
        OUT_DIR / "rocket_feathers_crf.parquet", index=False
    )
    with open(OUT_DIR / "rocket_feathers_national.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, default=str)
    print(f"\nWrote {OUT_DIR / 'rocket_feathers_national.json'} and "
          f"{OUT_DIR / 'rocket_feathers_crf.parquet'}")


if __name__ == "__main__":
    main()
