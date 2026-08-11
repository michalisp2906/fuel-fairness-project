"""
Station lookup: searchable table of every station's current price, fair
price, and overcharge. Doubles as the accessible table view of the map.
"""
from __future__ import annotations

import streamlit as st

from app_utils import GRADE_LABELS, data_as_of, load_app_data, peer_note

st.set_page_config(page_title="Station lookup", page_icon="⛽", layout="wide")

st.title("Station lookup")
st.markdown(
    "Search by postcode, brand, station name, or town. Prices are pence per "
    "litre. **Vs fair** is the gap between the station's price and its "
    "cost-plus fair price (positive means above fair). **Vs peers** is the gap "
    "against comparable stations facing similar competition, area type, and "
    "local costs, so it centres on zero by construction."
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

view = view.copy()
view["peer_status"] = peer_note(view["signal2_status"]).where(
    view["signal2_ppl"].isna(), ""
)

table = (
    view[[
        "station_name", "brand_display", "postcode", "city", "country",
        "price_ppl", "fair_price_ppl", "overcharge_ppl", "signal1_flag",
        "signal2_ppl", "signal2_decile", "excused_by_affluence_ppl",
        "peer_status", "last_changed",
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
        "signal2_ppl": "Vs peers (p/l)",
        "signal2_decile": "Peer decile",
        "excused_by_affluence_ppl": "Excused by area (p/l)",
        "peer_status": "Not compared",
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
        "Vs peers (p/l)": st.column_config.NumberColumn(
            format="%+.1f",
            help="Gap against comparable stations. Blank for stations Signal 2 "
                 "does not compare, with the reason in the last column.",
        ),
        "Peer decile": st.column_config.NumberColumn(
            format="%d",
            help="10 = among the dearest tenth against comparable stations.",
        ),
        "Excused by area (p/l)": st.column_config.NumberColumn(
            format="%+.1f",
            help="How much the local house-price level moves this station's "
                 "peer comparison. Positive means the affluence of the area "
                 "is forgiving part of its price.",
        ),
    },
)

st.caption(
    "**Excused by area** is published because house prices cut both ways. "
    "They stand in for genuine site costs like rent and rates, but they also "
    "let a model forgive high prices simply for being in a wealthy area, and "
    "judge poorer areas more harshly for the same behaviour. Rather than pick "
    "one answer, the model is scored both with and without house prices and "
    "the difference is shown here. See the Methodology page."
)
