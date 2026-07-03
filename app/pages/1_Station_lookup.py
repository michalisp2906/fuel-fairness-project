"""
Station lookup: searchable table of every station's current price, fair
price, and overcharge. Doubles as the accessible table view of the map.
"""
from __future__ import annotations

import streamlit as st

from app_utils import GRADE_LABELS, data_as_of, load_app_data

st.set_page_config(page_title="Station lookup", page_icon="⛽", layout="wide")

st.title("Station lookup")
st.markdown(
    "Search by postcode, brand, station name, or town. Prices are pence per "
    "litre; **Vs fair** is the gap between the station's price and its "
    "cost-plus fair price (positive means above fair)."
)

df = load_app_data()

col1, col2 = st.columns([3, 2])
with col1:
    query = st.text_input(
        "Search", placeholder="e.g. YO24, Tesco, Glasgow", max_chars=60
    )
with col2:
    grade_label = st.radio(
        "Fuel", list(GRADE_LABELS.values()), horizontal=True, index=0
    )
grade = {v: k for k, v in GRADE_LABELS.items()}[grade_label]

view = df[df["fuel_type"] == grade]
if query.strip():
    q = query.strip().lower()
    haystack = (
        view["postcode"].fillna("") + " " + view["brand_display"].fillna("")
        + " " + view["station_name"].fillna("") + " " + view["city"].astype("string").fillna("")
    ).str.lower()
    view = view[haystack.str.contains(q, regex=False)]

st.caption(
    f"{len(view):,} stations. Latest reported price change: {data_as_of(df)} (UK time)."
)

table = (
    view[[
        "station_name", "brand_display", "postcode", "city", "country",
        "price_ppl", "fair_price_ppl", "overcharge_ppl", "signal1_flag",
        "last_changed",
    ]]
    .sort_values("overcharge_ppl", ascending=False)
    .rename(columns={
        "station_name": "Station",
        "brand_display": "Brand",
        "postcode": "Postcode",
        "city": "Town",
        "country": "Country",
        "price_ppl": "Price (p/l)",
        "fair_price_ppl": "Fair price (p/l)",
        "overcharge_ppl": "Vs fair (p/l)",
        "signal1_flag": "Flagged",
        "last_changed": "Price last changed",
    })
)

st.dataframe(
    table,
    hide_index=True,
    height=560,
    column_config={
        "Price (p/l)": st.column_config.NumberColumn(format="%.1f"),
        "Fair price (p/l)": st.column_config.NumberColumn(format="%.1f"),
        "Vs fair (p/l)": st.column_config.NumberColumn(format="%+.1f"),
    },
)
