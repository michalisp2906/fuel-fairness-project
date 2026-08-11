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
    SCALE_MAX_PEER_PPL,
    SCALE_MAX_PPL,
    UNSCORED,
    data_as_of,
    load_app_data,
    peer_note,
)

# The two things the map can colour by. Signal 1 is an absolute gap against a
# cost-plus fair price; Signal 2 is relative to comparable stations and is
# demeaned, so it always centres on zero by construction.
COLOUR_MODES = {
    "Vs fair price (cost-plus)": ("overcharge_ppl", SCALE_MAX_PPL, "above fair"),
    "Vs comparable stations": ("signal2_ppl", SCALE_MAX_PEER_PPL, "above peers"),
}

st.set_page_config(
    page_title="UK Fuel Fairness",
    page_icon="⛽",
    layout="wide",
)


def diverging_fill(values: pd.Series, scale_max: float) -> list[list[int]]:
    """
    Map pence per litre to an RGBA fill on the diverging scale: blue pole
    below, neutral gray at zero, red pole above, clamped at +/- scale_max.

    Stations with no value (Signal 2 does not compare motorways or islands)
    get a flat gray at lower opacity, so they read as "not assessed" rather
    than as "assessed and average".
    """
    raw = values.to_numpy(dtype=float)
    missing = np.isnan(raw)
    v = np.clip(np.nan_to_num(raw) / scale_max, -1.0, 1.0)
    t = np.abs(v)[:, None]
    mid = np.array(MIDPOINT, dtype=float)
    low = np.array(POLE_LOW, dtype=float)
    high = np.array(POLE_HIGH, dtype=float)
    pole = np.where(v[:, None] < 0, low, high)
    rgb = np.rint(mid + t * (pole - mid)).astype(int)
    alpha = np.full((len(v), 1), 190)
    rgb[missing] = np.array(UNSCORED, dtype=int)
    alpha[missing] = 110
    return np.hstack([rgb, alpha]).tolist()


st.title("UK Fuel Fairness")
st.markdown(
    "How each station's price compares with a **cost-plus fair price** "
    "(wholesale cost + duty + a fair retail margin + VAT), and with "
    "**comparable stations** facing similar competition and local costs. "
    "Not a cheapest-fuel finder: a cheap station in a cheap market can still "
    "overcharge, and an expensive one may be pricing fairly. "
    "See the Methodology page for definitions and limitations."
)

df = load_app_data()

# --- Filters, one row above the chart ----------------------------------------
fcol1, fcol2, fcol3, fcol4 = st.columns([2, 2, 2, 2])
with fcol1:
    grade_label = st.radio(
        "Fuel", list(GRADE_LABELS.values()), horizontal=True, index=0
    )
    grade = {v: k for k, v in GRADE_LABELS.items()}[grade_label]
with fcol2:
    colour_label = st.radio(
        "Colour by", list(COLOUR_MODES), index=0
    )
    colour_col, colour_max, colour_word = COLOUR_MODES[colour_label]
with fcol3:
    country = st.selectbox(
        "Country", ["All countries"] + sorted(df["country"].dropna().unique())
    )
with fcol4:
    flagged_only = st.toggle(
        f"Flagged stations only (more than {FLAG_BUFFER_PPL:.0f}p above fair)"
    )

view = df[df["fuel_type"] == grade]
if country != "All countries":
    view = view[view["country"] == country]
if flagged_only:
    view = view[view["signal1_flag"]]

# --- KPI tiles ----------------------------------------------------------------
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Stations", f"{len(view):,}")
k2.metric("Median price", f"{view['price_ppl'].median():.1f}p/litre")
k3.metric("Median vs fair price", f"{view['overcharge_ppl'].median():+.1f}p")
k4.metric("Flagged above fair", f"{view['signal1_flag'].mean():.1%}")
# Denominator is the stations Signal 2 actually compares, not every row, so
# motorways and islands cannot dilute the share.
compared = view["signal2_ppl"].notna().sum()
above_peers = (view["signal2_ppl"] > FLAG_BUFFER_PPL).sum()
k5.metric(
    f"More than {FLAG_BUFFER_PPL:.0f}p above peers",
    f"{above_peers / compared:.1%}" if compared else "n/a",
    help=(
        "Share of the stations Signal 2 compares that charge more than "
        f"{FLAG_BUFFER_PPL:.0f}p above comparable stations facing similar "
        "competition, area type, and local costs."
    ),
)

# --- Map ------------------------------------------------------------------------
map_df = view.dropna(subset=["latitude", "longitude"])[[
    "latitude", "longitude", "overcharge_ppl", "signal2_ppl",
    "station_name", "brand_display", "postcode",
    "price_str", "fair_price_str", "overcharge_str", "signal2_str",
    "signal2_status", "last_changed",
]].copy()
map_df["peer_note"] = peer_note(map_df["signal2_status"])
map_df["fill"] = diverging_fill(map_df[colour_col], colour_max)

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
        "Vs comparable stations: <b>{signal2_str}</b> {peer_note}<br/>"
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
unscored_note = (
    f"&nbsp;&nbsp;<span style='display:inline-block;width:10px;height:10px;"
    f"border-radius:5px;background:rgb{UNSCORED};'></span> not compared "
    "(motorway or island)"
    if colour_col == "signal2_ppl" else ""
)
st.markdown(
    f"""
    <div style="display:flex;align-items:center;gap:12px;
                font:13px system-ui,-apple-system,'Segoe UI',sans-serif;
                color:#52514e;margin-top:4px;">
      <span>{-colour_max:.0f}p below {colour_word.split()[-1]}</span>
      <div style="width:220px;height:10px;border-radius:5px;
                  border:1px solid rgba(11,11,11,0.10);
                  background:linear-gradient(to right,
                    rgb{POLE_LOW}, rgb{MIDPOINT}, rgb{POLE_HIGH});"></div>
      <span>+{colour_max:.0f}p {colour_word}</span>
      {unscored_note}
    </div>
    """,
    unsafe_allow_html=True,
)

if colour_col == "signal2_ppl":
    st.caption(
        "**Vs comparable stations** is relative by design: it is measured "
        "against the typical station facing similar competition, area type, "
        "and local costs, so half of stations sit above zero and half below. "
        "It ranks stations against their peers, it is not an absolute "
        "overcharge figure. Motorway services and ferry-dependent islands are "
        "deliberately not compared this way, because their costs and captive "
        "markets make peer comparison misleading."
    )

st.caption(
    f"Prices in pence per litre. Latest reported price change: {data_as_of(df)} "
    "(UK time). A station's price stands until it reports a change, so quiet "
    "stations can show older prices. Station-level data: Fuel Finder open data "
    "scheme. Hover a point for details; use the Station lookup page for a "
    "searchable table."
)
