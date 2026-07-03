"""
UK Fuel Fairness: map page.

Every UK petrol station, coloured by how far its current price sits from the
cost-plus fair price (Signal 1). Blue = below fair, gray = at fair,
red = above fair. This is deliberately NOT a cheapest-fuel finder: the map
shows overcharging relative to costs, not raw price.

Run locally:
    .venv/Scripts/python.exe -m streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

from app_utils import (
    FLAG_BUFFER_PPL,
    GRADE_LABELS,
    MIDPOINT,
    POLE_HIGH,
    POLE_LOW,
    SCALE_MAX_PPL,
    data_as_of,
    load_app_data,
)

st.set_page_config(
    page_title="UK Fuel Fairness",
    page_icon="⛽",
    layout="wide",
)


def diverging_fill(overcharge: pd.Series) -> list[list[int]]:
    """
    Map overcharge (pence per litre) to an RGBA fill on the diverging scale:
    blue pole below fair price, neutral gray at fair, red pole above.
    Clamped at +/- SCALE_MAX_PPL.
    """
    v = np.clip(overcharge.to_numpy(dtype=float) / SCALE_MAX_PPL, -1.0, 1.0)
    t = np.abs(v)[:, None]
    mid = np.array(MIDPOINT, dtype=float)
    low = np.array(POLE_LOW, dtype=float)
    high = np.array(POLE_HIGH, dtype=float)
    pole = np.where(v[:, None] < 0, low, high)
    rgb = np.rint(mid + t * (pole - mid)).astype(int)
    alpha = np.full((len(v), 1), 190)
    return np.hstack([rgb, alpha]).tolist()


st.title("UK Fuel Fairness")
st.markdown(
    "How each station's price compares with a **cost-plus fair price** "
    "(wholesale cost + duty + a fair retail margin + VAT). "
    "Not a cheapest-fuel finder: a cheap station in a cheap market can still "
    "overcharge, and an expensive one may be pricing fairly. "
    "See the Methodology page for definitions and limitations."
)

df = load_app_data()

# --- Filters, one row above the chart ----------------------------------------
fcol1, fcol2, fcol3 = st.columns([2, 2, 2])
with fcol1:
    grade_label = st.radio(
        "Fuel", list(GRADE_LABELS.values()), horizontal=True, index=0
    )
    grade = {v: k for k, v in GRADE_LABELS.items()}[grade_label]
with fcol2:
    country = st.selectbox(
        "Country", ["All countries"] + sorted(df["country"].dropna().unique())
    )
with fcol3:
    flagged_only = st.toggle(
        f"Flagged stations only (more than {FLAG_BUFFER_PPL:.0f}p above fair)"
    )

view = df[df["fuel_type"] == grade]
if country != "All countries":
    view = view[view["country"] == country]
if flagged_only:
    view = view[view["signal1_flag"]]

# --- KPI tiles ----------------------------------------------------------------
k1, k2, k3, k4 = st.columns(4)
k1.metric("Stations", f"{len(view):,}")
k2.metric("Median price", f"{view['price_ppl'].median():.1f}p/litre")
k3.metric("Median vs fair price", f"{view['overcharge_ppl'].median():+.1f}p")
k4.metric("Flagged above fair", f"{view['signal1_flag'].mean():.1%}")

# --- Map ------------------------------------------------------------------------
map_df = view.dropna(subset=["latitude", "longitude"])[[
    "latitude", "longitude", "overcharge_ppl",
    "station_name", "brand_display", "postcode",
    "price_str", "fair_price_str", "overcharge_str", "last_changed",
]].copy()
map_df["fill"] = diverging_fill(map_df["overcharge_ppl"])

layer = pdk.Layer(
    "ScatterplotLayer",
    data=map_df,
    get_position=["longitude", "latitude"],
    get_fill_color="fill",
    get_line_color=[252, 252, 251, 160],
    line_width_min_pixels=1,
    stroked=True,
    pickable=True,
    radius_min_pixels=2.5,
    radius_max_pixels=10,
    get_radius=900,
)

tooltip = {
    "html": (
        "<b>{station_name}</b> ({brand_display})<br/>"
        "{postcode}<br/>"
        "Price: <b>{price_str}</b> &nbsp; Fair: {fair_price_str}<br/>"
        "Vs fair price: <b>{overcharge_str}</b><br/>"
        "Price last changed: {last_changed}"
    ),
    "style": {"backgroundColor": "#0b0b0b", "color": "#fcfcfb"},
}

deck = pdk.Deck(
    layers=[layer],
    initial_view_state=pdk.ViewState(latitude=54.6, longitude=-3.4, zoom=5),
    map_style="light",
    tooltip=tooltip,
)
st.pydeck_chart(deck, height=620)

# --- Legend ---------------------------------------------------------------------
st.markdown(
    f"""
    <div style="display:flex;align-items:center;gap:12px;
                font:13px system-ui,-apple-system,'Segoe UI',sans-serif;
                color:#52514e;margin-top:4px;">
      <span>{-SCALE_MAX_PPL:.0f}p below fair</span>
      <div style="width:220px;height:10px;border-radius:5px;
                  border:1px solid rgba(11,11,11,0.10);
                  background:linear-gradient(to right,
                    rgb{POLE_LOW}, rgb{MIDPOINT}, rgb{POLE_HIGH});"></div>
      <span>+{SCALE_MAX_PPL:.0f}p above fair</span>
    </div>
    """,
    unsafe_allow_html=True,
)

st.caption(
    f"Prices in pence per litre. Latest reported price change: {data_as_of(df)} "
    "(UK time). A station's price stands until it reports a change, so quiet "
    "stations can show older prices. Station-level data: Fuel Finder open data "
    "scheme. Hover a point for details; use the Station lookup page for a "
    "searchable table."
)
