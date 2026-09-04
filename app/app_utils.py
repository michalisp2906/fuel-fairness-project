"""
Shared helpers for the Streamlit app: data loading and display constants.

The app reads ONLY the committed gold table (data/gold/app_data.parquet),
never silver or features, so the deployed clone on Streamlit Community
Cloud works without rebuilding anything.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

GOLD_PATH = Path(__file__).resolve().parents[1] / "data" / "gold" / "app_data.parquet"

GRADE_LABELS = {
    "E10": "Petrol (E10)",
    "B7_STANDARD": "Diesel (B7)",
}

# Reference palette (dataviz skill): diverging blue/red poles, neutral gray
# midpoint, ink and surface tokens for chart chrome.
POLE_LOW = (42, 120, 214)      # #2a78d6 blue: below fair price
MIDPOINT = (195, 194, 183)     # #c3c2b7 neutral gray: at fair price
POLE_HIGH = (227, 73, 72)      # #e34948 red: above fair price
# Signal 1 clamps at +/- 20p, not 10p. At 10p more than a tenth of petrol
# stations pinned to identical maximum red, so the genuinely extreme stations
# were indistinguishable from merely typical ones. The zero point stays at zero
# deliberately: re-centring on the market median would excuse collective
# overcharging, which the project explicitly refuses to do.
SCALE_MAX_PPL = 20.0
# Peer comparisons are demeaned, so they sit in a much narrower band than
# Signal 1 (p10 to p90 is roughly -5p to +5p). Reusing the 10p scale would
# wash the whole map out to gray.
SCALE_MAX_PEER_PPL = 6.0
UNSCORED = (138, 138, 133)     # #8a8a85: deliberately not peer-compared

FLAG_BUFFER_PPL = 3.0          # Signal 1 flag threshold, must match build_features.py

FAIR_MARGIN_PPL = 7.0          # must match FAIR_MARGIN_PPL in build_features.py
CMA_MARGIN_PPL = 10.7          # must match CMA_MARGIN_PPL in build_features.py

# By construction, a station charging the CMA-observed market margin (10.7p)
# rather than the model's fair margin (7.0p) lands this far above the fair
# line, because fair price is a normative benchmark the current market does
# not meet. Must match FAIR_MARGIN_PPL, CMA_MARGIN_PPL and VAT_RATE in
# build_features.py. Used to explain the wall of red on the Signal 1 view.
STRUCTURAL_OFFSET_PPL = (CMA_MARGIN_PPL - FAIR_MARGIN_PPL) * 1.20


@st.cache_data
def load_app_data() -> pd.DataFrame:
    """Load the gold table and add display columns used across pages."""
    df = pd.read_parquet(GOLD_PATH)

    name = df["trading_name"].fillna("").str.strip()
    brand = df["brand_name"].astype("string").fillna("Unknown brand")
    df["station_name"] = name.where(name != "", brand)
    df["brand_display"] = brand

    changed = df["price_change_effective_timestamp"].dt.tz_convert("Europe/London")
    df["last_changed"] = changed.dt.strftime("%d %b %Y")

    df["price_str"] = df["price_ppl"].map(lambda v: f"{v:.1f}p")
    df["fair_price_str"] = df["fair_price_ppl"].map(lambda v: f"{v:.1f}p")
    df["overcharge_str"] = df["overcharge_ppl"].map(lambda v: f"{v:+.1f}p")

    # Signal 2, the peer comparison. Absent only if the gold table predates it
    # or build_signal2.py has never run, in which case the app degrades to
    # Signal 1 rather than failing.
    if "signal2_ppl" not in df.columns:
        df["signal2_ppl"] = pd.NA
        df["excused_by_affluence_ppl"] = pd.NA
        df["signal2_decile"] = pd.NA
        df["signal2_status"] = "not scored yet"

    # Stored as a delta to keep the committed table small: the affluence-blind
    # comparison is the peer score plus what house prices excused.
    df["signal2_nohp_ppl"] = df["signal2_ppl"] + df["excused_by_affluence_ppl"]
    df["signal2_str"] = df["signal2_ppl"].map(
        lambda v: "not compared" if pd.isna(v) else f"{v:+.1f}p"
    )
    df["excused_str"] = df["excused_by_affluence_ppl"].map(
        lambda v: "" if pd.isna(v) else f"{v:+.1f}p"
    )
    return df


def peer_note(status: pd.Series) -> pd.Series:
    """Short reason for the stations Signal 2 deliberately does not compare."""
    return status.astype("string").str.replace("not compared: ", "", regex=False)


def data_as_of(df: pd.DataFrame) -> str:
    """Timestamp of the most recent price change in the data, for captions."""
    ts = df["price_change_effective_timestamp"].max()
    return ts.tz_convert("Europe/London").strftime("%d %b %Y, %H:%M")
