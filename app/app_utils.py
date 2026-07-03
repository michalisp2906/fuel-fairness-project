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
SCALE_MAX_PPL = 10.0           # color scale clamps at +/- 10p overcharge

FLAG_BUFFER_PPL = 3.0          # Signal 1 flag threshold, must match build_features.py


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
    return df


def data_as_of(df: pd.DataFrame) -> str:
    """Timestamp of the most recent price change in the data, for captions."""
    ts = df["price_change_effective_timestamp"].max()
    return ts.tz_convert("Europe/London").strftime("%d %b %Y, %H:%M")
