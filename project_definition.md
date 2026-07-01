# Project Definition: UK Fuel Pricing Fairness Model

## What this project is

This project asks one question: is a petrol station charging more than it should, given
its costs and the market it operates in?

It is not a cheapest-fuel finder. Apps that show you the cheapest station nearby already
exist and require no data science. The question worth asking is harder: is a station's
price fair, given everything we know about where it is, what it costs to run, and what
its competitors charge? A station might be the cheapest in its town and still be
overcharging, if the whole town is overpriced.

---

## What "fair price" means in this project

A fair price is the price a station would charge if it were recovering its legitimate
costs and earning a reasonable margin, nothing more. We define it using two independent
signals that measure different things.

### Signal 1: cost-plus fair price

This is the economic floor. A station has a known cost stack:

```
Fair price = wholesale product cost + fuel duty + VAT + reasonable retail margin
```

- Wholesale cost: the published refined product spot price (gasoil for diesel, gasoline
  for petrol), used as a proxy for what stations pay for supply. We cannot see individual
  supply contracts, so this is the best publicly available approximation.
- Fuel duty: fixed by HMRC. Currently frozen.
- VAT: 20 percent, applied on top of duty-inclusive price.
- Retail margin: the CMA (Competition and Markets Authority) publishes estimates of the
  expected retail margin from its ongoing fuel market monitoring. We use this as the
  benchmark for a reasonable margin. We do not derive our own; the CMA's estimate is the
  accepted public reference for this.

The Signal 1 overcharge score is: actual pump price minus this cost-plus fair price.
A positive number means the station is charging above what the cost stack justifies.

This signal applies the same national margin benchmark to every station. It does not
adjust for local operating costs (rent, rates). That adjustment is the job of Signal 2.

### Signal 2: peer-relative fair price

Even within the same cost-plus framework, some stations legitimately sit higher than
the national margin. A rural station with no competitors for ten miles can justify a
higher margin than an urban station with five rivals within walking distance. High-cost
areas (where commercial rent is expensive) also justify a higher margin.

Signal 2 is a gradient-boosted model trained on the Signal 1 residuals (how far above
the cost floor each station sits), using features that capture legitimate local variation:

- Competition within 1km, 3km, and 5km (count of nearby rivals)
- Distance to the nearest competitor
- Motorway or trunk road flag (captive customers)
- Rural-urban classification (ONS)
- Local house price index at MSOA level (ONS), used as a proxy for commercial property
  costs in the area
- Brand type (supermarket, oil major, independent)

The model learns what margin premium is typical for stations in each type of location
and competitive situation. The Signal 2 overcharge score is: how far above that learned
expectation the station sits.

This is deliberately data-driven. We do not hard-code a rent adjustment. The model
learns how much extra margin similar stations in similar locations charge, and uses
that as the local benchmark.

---

## How the two signals combine

Signal 1 answers: is this station above the economic cost floor?
Signal 2 answers: is this station above what similar stations in similar conditions charge?

| Signal 1 | Signal 2 | Interpretation |
|---|---|---|
| High | High | Strongest flag. Above cost floor and above local peers. Station-level problem. |
| High | Low | Above cost floor, but so are all nearby stations. Local market problem. Flag all of them. |
| Low | High | Below cost floor overall, but expensive relative to local peers. Weaker signal, worth monitoring. |
| Low | Low | Consistent with a fair price. Not flagged. |

The critical point in the second row: if Signal 1 is high and Signal 2 is low (because
all nearby stations are equally overpriced), we do not call it fair. We flag the area.
The peer model does not excuse collective overcharging. It explains who is the worst
offender within a market that may itself be broken.

---

## Rocket-and-feathers analysis (separate module)

This is an independent question about market dynamics, not about individual stations.

The "rockets and feathers" hypothesis (Bacon, 1991) states that fuel pump prices rise
quickly when wholesale costs go up (rockets) but fall slowly when wholesale costs come
down (feathers). This is a form of asymmetric pass-through and is associated with weak
retail competition.

We test this by comparing our accumulating time series of pump prices against the
published wholesale price series, using an asymmetric error-correction model. This
module stands on its own, with its own findings and caveats. It does not interact with
the fair-price model.

Important caveat: the strength of this analysis grows with the length of our price
history. We started logging in June 2026. Early results should be treated as provisional.

---

## What the model cannot see

These are honest, documented limitations:

- Individual supply contracts. Stations negotiate their own wholesale prices. We use the
  published spot price as a proxy, which is the best available public approximation but
  is not the true buy price for any individual station.
- Rent and rates. We proxy local operating costs via house prices. This is indirect.
  A station's actual lease terms are not public.
- Throughput volumes. A high-volume station can spread fixed costs over more litres and
  run on a thinner margin. We cannot see volumes.
- Staff costs, equipment costs, and other operating line items. Not public at station level.

These limitations mean the model flags stations that are likely overcharging, not
stations that are proven to be overcharging. We are explicit about this throughout.

---

## Data sources

| Data | Source | Use |
|---|---|---|
| Pump prices (station level) | UK Government Fuel Finder API | Primary price data |
| Wholesale product prices | DESNZ "Weekly road fuel prices" (gov.uk) | Cost floor for Signal 1 |
| Fuel duty and VAT rates | HMRC (fixed, public) | Cost floor for Signal 1 |
| Retail margin benchmark | CMA road fuel market monitoring reports | Reasonable margin anchor |
| Station locations and attributes | Fuel Finder API | Spatial and competition features |
| House prices (MSOA level) | ONS / Land Registry | Local cost proxy for Signal 2 |
| Rural-urban classification | ONS | Location feature for Signal 2 |
