"""
Methodology: what the fair price is, how the flag works, and the honest
list of limitations.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Methodology", page_icon="⛽")

CRF_PATH = Path(__file__).resolve().parents[2] / "data" / "analysis" / (
    "rocket_feathers_crf.parquet"
)
CRF_LABELS = {"petrol": "Petrol (E10)", "diesel": "Diesel (B7)"}


@st.cache_data
def load_crf() -> pd.DataFrame | None:
    """
    Cumulative response functions from rocket_feathers.py.

    Returns None rather than raising if the file is absent, so the page still
    renders on a clone that has not run the analysis yet. Committed to the
    repo for the same reason data/gold is: the deployed app reads it from the
    clone and cannot rebuild it.
    """
    if not CRF_PATH.exists():
        return None
    return pd.read_parquet(CRF_PATH)


def render_crf_chart() -> None:
    """Response to a 1p cost rise vs a 1p cost fall, plotted against each other."""
    crf = load_crf()
    if crf is None:
        st.info("Pass-through analysis not available in this deployment.")
        return

    label = st.radio(
        "Fuel", list(CRF_LABELS.values()), horizontal=True, key="crf_fuel"
    )
    fuel = {v: k for k, v in CRF_LABELS.items()}[label]
    d = crf[crf["fuel"] == fuel].set_index("week")

    st.line_chart(
        d[["response_to_rise_ppl", "response_to_fall_ppl"]].rename(columns={
            "response_to_rise_ppl": "After a 1p cost RISE",
            "response_to_fall_ppl": "After a 1p cost FALL",
        }),
        height=320,
        color=["#e34948", "#2a78d6"],
    )
    peak = d.loc[d["gap_ppl"].abs().idxmax()]
    st.caption(
        f"Pence per litre passed through, week by week, after a permanent 1p "
        f"move in wholesale cost. Widest gap: **{peak.gap_ppl:+.2f}p** at week "
        f"{int(d['gap_ppl'].abs().idxmax())}. A gap above zero means rises are "
        "passed on more fully than falls at that horizon."
    )


st.title("Methodology")

st.markdown("""
## What this is

A fairness model for UK pump prices, not a cheapest-fuel finder. Cheapest-fuel
finders already exist; they tell you where prices are low, not whether a price
is justified. This project estimates what each station *should* charge given
its costs, then measures how far the actual price sits above that.

## The fair price (Signal 1: cost-plus)

For each price a station sets:

```
fair price = (wholesale + basis + duty + fair margin) * 1.20
overcharge = station price - fair price
```

All figures in pence per litre:

- **Wholesale**: weekly wholesale cost of the fuel itself, lagged 10 days,
  because pump prices reflect fuel bought one to two weeks earlier (the
  Competition and Markets Authority estimates 1 to 2 weeks pass-through).
- **Basis**: a constant per-fuel correction for the wholesale proxy, see
  limitations below.
- **Duty**: fuel duty, 52.95p per litre (unchanged since March 2022).
- **Fair margin**: 7p per litre, the CMA's pre-2022 average retail margin.
  The CMA has called the current average of roughly 10.7p excessive, so the
  fair price deliberately reflects the pre-weakening level of competition.
- **1.20**: VAT at 20%, charged on all of the above.

A station is **flagged** when its price is more than 3p per litre above its
fair price. The 3p buffer is roughly one standard deviation of the weekly
noise in the wholesale correction, so stations are only flagged when the gap
is too large to be measurement noise.

A market-wide note: because current retail margins exceed the 7p fair margin
nearly everywhere, the *average* station prices a few pence above this fair
price. The flag threshold is set so it highlights the worst offenders rather
than declaring the entire market unfair, but the market-wide gap is itself a
finding, consistent with the CMA's own conclusions.

## The peer comparison (Signal 2)

Signal 1 asks whether a price is justified by costs. Signal 2 asks a second
question: **is this station dear compared with stations facing similar
circumstances?** A gradient-boosted model (LightGBM) learns how much margin is
normal given competition density, distance to the nearest rival and nearest
supermarket, how rural the area is, and local house prices. The station's
**Vs peers** figure is the gap between what it charges and what the model
expects of a comparable station.

**It is a ranking, not an absolute figure.** Two design choices follow from
that, and both matter when reading the numbers:

- The figure is measured against the *typical* station in the current market,
  so it centres on zero: half of stations sit above, half below. A station at
  +4p is dearer than comparable stations, it is not overcharging by 4p in
  absolute terms. Signal 1 is the absolute number.
- The model is validated on its ability to rank, not to predict a price. Held
  out from training by geography, it ranks stations at Spearman 0.44 (petrol)
  and 0.39 (diesel) against 0.32 and 0.27 for a simple regional-median
  benchmark, and it puts about a quarter of the genuinely worst-priced tenth
  of stations into its own worst tenth, against 10% for chance. On raw
  accuracy it beats the regional benchmark by only about 3%, which is why
  accuracy is not the claim being made.

**Deliberately excluded from the model**: brand, motorway status, and
supermarket status. Controlling for them would teach the model that a
brand-wide or motorway-wide premium is normal, which would excuse exactly the
group-wide overcharging the project exists to detect. Distance to the nearest
supermarket stays, because competitive pressure from *others* is legitimate.

**Deliberately not compared**: motorway services and stations on
ferry-dependent islands. Both face real cost and competition structures that
peer comparison would misrepresent, so they are labelled rather than scored,
and analysed as their own groups.

### House prices, and why two numbers are published

Local house prices are the single largest driver in the model, and they are
genuinely ambiguous. They stand in for real site costs (rent, rates, land),
but they also let a model forgive a high price simply for being in a wealthy
area, and judge a poorer one more harshly for the same behaviour.

Rather than pick one answer and hide the choice, the model is scored twice,
with and without house prices, and the difference is published as **Excused by
area**. The pattern in that column is itself a finding: including house prices
does not only forgive rich areas, it penalises poor ones. Across England and
Wales, the wealthiest tenth of areas are excused about 1.5p per litre, while
the poorest tenth are judged roughly 1.2p more harshly, and around a fifth of
the stations in the worst-ranked tenth are there only because house prices are
in the model.

### Known weakness

The model has only observed a few months of market conditions. When wholesale
prices move outside the range it has seen, it cannot extrapolate and its
estimate of the overall level becomes unreliable, which is why the comparison
is reported relative to the current market rather than as an absolute figure.
The relative ranking is unaffected, because that error applies equally to
every station. This is a limitation of a young dataset, and it shrinks as
collection continues.

## Do prices rise faster than they fall?

This is the "rockets and feathers" question: when wholesale costs go up, pump
prices are widely believed to follow quickly, but when costs come down, prices
are believed to drift back slowly. It is a separate piece of analysis from the
two signals above, run on 444 weeks of national data from 2018 to 2026 rather
than on this project's own shorter history.

The finding is that the **immediate** response is even-handed. A cost rise and
a cost fall of the same size get passed on at the same speed in the first few
weeks. The difference shows up later, in how margins are corrected:

- When a retail margin is **thinner** than normal, it is rebuilt at a rate that
  is statistically clear (both fuels).
- When a margin is **fatter** than normal, the rate at which competition erodes
  it cannot be distinguished from zero.

The chart below traces what happens to pump prices after a permanent 1p change
in wholesale cost, following a rise and a fall separately. The gap between the
two lines is the asymmetry, and it peaks at about 0.19p per litre for petrol
and 0.24p for diesel, roughly ten to eleven weeks after the cost moves.
""")

render_crf_chart()

st.markdown("""
### How strongly to read this

Cautiously, and the reason is worth stating. The direction is consistent across
both fuels and every robustness check we ran, and for petrol in the post-2022
period the difference between the two correction speeds is statistically
significant. But across the full sample that difference is **not** statistically
established.

In particular, "the thin-margin effect is significant and the fat-margin one is
not" is a weaker argument than it sounds, because a difference in significance
is not itself a significant difference. We report the pattern because the
direction is consistent and economically meaningful, not because the full-sample
test settles it.

One further caveat that runs the other way: the wholesale price used here is a
US futures proxy, and measurement error in a proxy pulls estimates toward zero.
That biases this particular test **toward finding nothing**, so the effect that
does show up is more likely understated than overstated.

## Data sources

- **Station prices**: UK Government Fuel Finder open data scheme, collected
  four times per working day. Prices are as reported by stations.
- **Wholesale**: NYMEX RBOB gasoline (petrol) and NYMEX heating oil (diesel)
  futures, converted to pence per litre at the spot exchange rate.
- **National averages for calibration**: DESNZ weekly road fuel prices.
- **Local context** (for Signal 2): ONS house prices by area, rural-urban
  classification, station locations from the Fuel Finder station register.

## Limitations, stated plainly

- **Wholesale proxy**: the model uses US futures (NYMEX) because European
  benchmark prices (Platts and Argus Rotterdam) are paid services. The gap
  between UK wholesale and the US proxy is corrected with a constant per-fuel
  basis estimated over two years of national data (currently +7.0p petrol,
  +9.3p diesel). In any single month the corrected level can drift by a few
  pence, shared equally by all stations, so comparisons *between* stations
  are unaffected, but a station's exact overcharge figure carries that
  uncertainty. The diesel proxy is the weaker of the two.
- **Collection window**: prices are collected on weekday working hours only.
  Changes made at nights, weekends, and holidays are picked up at the next
  collection, so their timing (not their value) can be recorded late.
- **Stale prices**: a station's price stands until it reports a change.
  Stations that rarely report show older prices; the map tooltip shows when
  each price was last changed.
- **Northern Ireland**: local-context data (house prices, rural-urban class)
  covers Great Britain only. NI stations are still peer-compared, but with
  that context missing, so their comparison is weaker. Their prices and fair
  prices are computed the same as everywhere else.
- **Motorway services**: flagged at very high rates and shown on the map, but
  not peer-compared. Their distance-based competition measures are misleading
  (paired services sit on opposite carriageways) and their cost structure
  differs, so they are analysed as their own comparison group.
- **Young peer model**: Signal 2 is trained on the weeks collected so far, a
  short and unusually volatile stretch of the market. It ranks stations
  reliably; its sense of the overall price level is still thin, as described
  above.

## Fairness of the presentation

The map colours stations by their gap to fair price, or by their gap to
comparable stations, never by raw price, so a cheap rural market and an
expensive urban one are judged on the same footing. Blue means below, gray
means level, red means above. Stations that are deliberately not peer-compared
are drawn in flat gray when that view is selected, so they read as "not
assessed" rather than "assessed and average".
""")
