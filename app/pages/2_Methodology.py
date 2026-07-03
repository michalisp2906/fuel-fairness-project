"""
Methodology: what the fair price is, how the flag works, and the honest
list of limitations.
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Methodology", page_icon="⛽")

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

## What is coming next (Signal 2)

A second, peer-relative signal: a model of how much of each station's
overcharge is explained by local market structure (competition density,
rurality, local costs). It will rank flagged stations and separate
"expensive because remote" from "expensive because it can be". Deliberately
excluded from that model: brand, motorway status, and supermarket status,
because controlling for them would excuse group-wide overcharging.

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
  covers Great Britain only, so NI stations will be excluded from Signal 2
  local-market adjustments. Their prices and fair prices are computed the
  same as everywhere else.
- **Motorway services**: flagged at very high rates and shown on the map,
  but they will be analysed as their own comparison group in Signal 2. Their
  distance-based competition measures are misleading (paired services sit on
  opposite carriageways), and their cost structure differs.

## Fairness of the presentation

The map colours stations by their gap to fair price, not by raw price, so a
cheap rural market and an expensive urban one are judged on the same footing.
Blue means below fair, gray means at fair, red means above.
""")
