# Which UK petrol stations are overcharging you?

A fair-price model for UK road fuel. It estimates what each of about 8,100
petrol stations *should* charge given its costs and its local market, flags the
ones charging well above that, and separately tests whether pump prices rise
faster than they fall.

**Live app: https://fuel-fairness-project.streamlit.app**

This is deliberately **not** a cheapest-fuel finder. Those already exist, and
finding the lowest number in a list is not data science. A cheap station in a
cheap area can still be overcharging, and an expensive station in an expensive
area may be pricing fairly. The whole point is to separate the two.

---

## Why this matters

The Competition and Markets Authority has had UK road fuel under formal
scrutiny since 2022. Its road fuel review found that retail margins widened
materially: roughly 10.7p per litre against a pre-2022 norm nearer 7p. The CMA
now publishes a price monitoring function and has recommended a statutory open
data scheme, which is the scheme this project consumes.

So the question "is this station's price justified by its costs?" is a live
regulatory question, not a hypothetical one. That is what this project answers,
station by station, updated several times a day.

---

## What it produces

Three separate pieces of analysis, deliberately kept apart because they answer
different questions and have different levels of confidence behind them.

### Signal 1: cost-plus fair price (the absolute test)

```
fair price = (wholesale + fuel duty + fair margin) x VAT
           = (wholesale + 52.95p + 7.00p) x 1.20
overcharge = actual price - fair price
flag       = overcharge > 3p
```

The 7p fair margin is the CMA's pre-2022 baseline. Duty has been 52.95p since
28 March 2022. This is a transparent, arithmetic benchmark that anyone can
check, and it is the primary yes/no flag.

**A consequence worth stating up front, because it looks like a bug and is
not.** Since the market currently runs at about 10.7p margin and the model
allows 7p, a station charging the perfectly typical market margin still lands
about `(10.7 - 7.0) x 1.2 = 4.4p` above the fair line. Most of the country sits
above the benchmark most of the time. That is the finding, not an error. The
map is deliberately **not** re-centred on the market median, because doing so
would define the current market as fair by assumption, which is precisely the
question being asked.

### Signal 2: peer-relative fairness (the local test)

A LightGBM model predicting Signal 1's residual from competition and location
features: rival counts at 1, 3 and 5km, distance to the nearest rival, distance
to the nearest supermarket, rival brand variety, rural-urban classification,
area house price index, and the national wholesale regime.

What it deliberately **excludes** is as important as what it includes. Brand,
motorway status and supermarket status are all left out as features. Including
a station's own type would teach the model to expect that type's premium and
then forgive it, which would normalise exactly the group-wide overcharging the
project exists to detect. Rival pressure *from* others stays in, because that is
a genuine cost of doing business; a station's own identity does not.

Signal 2 is presented as a **ranking** model, not an accurate fair-price
predictor. It beats a regional-median baseline clearly on rank skill and only
marginally on absolute accuracy, so ranking is the only claim made for it.

### Rockets and feathers: pass-through asymmetry

An asymmetric error-correction model testing whether pump prices rise faster on
wholesale increases than they fall on decreases. Run at two levels: nationally
on eight years of DESNZ data, and as a station-level panel on this project's own
collected history.

---

## Headline findings

### 1. Feathers are real, and they are in the adjustment, not the impact

On 444 weeks of national data (2018 to 2026), the immediate pass-through of a
cost change is statistically symmetric for both fuels. The asymmetry is in how
margins are *corrected*:

| | margin too **fat** | margin too **thin** |
|---|---|---|
| Petrol | -0.0145 (p = 0.17, **not significant**) | -0.0283 (p = 0.011) |
| Diesel | -0.0151 (p = 0.17, **not significant**) | -0.0458 (p = 0.009) |

Thin margins get rebuilt significantly. Fat margins are not significantly
competed away. Tracing a permanent 1p cost move through the fitted system, the
gap between the response to a rise and the response to a fall peaks at
**0.19p for petrol (week 10)** and **0.24p for diesel (week 11)**.

The formal test that the two speeds *differ from each other* does not reject at
5% in the full sample (p = 0.44 petrol, p = 0.20 diesel), so this is reported as
suggestive rather than conclusive. It does reject for petrol in the post-2022
CMA-scrutiny era (p = 0.03).

**How much weight the table above can carry.** "Thin is significant, fat is
not" is a weaker argument than it looks: a difference in significance is not
itself a significant difference (Gelman and Stern, 2006). Two coefficients can
fall either side of the 5% line while being statistically indistinguishable
from each other, which is what the equality test reports here. The claim made
is therefore about the consistent *direction* and the economic size of the gap,
not about a test that rejected. The robustness runs below are also nested on the
same data, so 6 of 6 is far less independent confirmation than it sounds.

The pattern survives **6 of 6** robustness specifications for petrol and **5 of
6** for diesel, including imposing one-for-one long-run pass-through, halving
and doubling the Newey-West bandwidth, and restricting to the post-2022 period.
Excluding 2020 to 2022 makes the effect *stronger*, not weaker, so it is not an
artefact of COVID or the invasion of Ukraine.

### 2. Adjusting for affluence forgives rich areas and punishes poor ones

Signal 2 is trained twice per fuel, with and without the area house price index,
and both scores are published. House price is the largest static driver and
carries most of the model's ranking edge, but it cannot separate genuine site
costs (rent, rates, land) from simply charging more where people can pay more.

Quantified, for petrol:

- Spearman between area house price and pence excused by the feature: **0.680**
- Richest decile of areas: **+1.52p** excused
- Poorest decile: **-1.22p**, that is, judged *more* harshly
- **123 of 643** worst-decile stations appear there **only** because house price
  is in the model

Diesel is materially the same. Publishing one number would have hidden this, so
the app publishes both and names the trade-off.

### 3. The station panel does not yet support a competition story

The hypothesis was that stations with fewer local rivals would show stronger
feathers. On the current ~12 weeks of collected history, **it is not
supported** for either fuel. The panel is reported as provisional and
inconclusive, for two reasons found by checking rather than assumed away:

1. The error-correction term is each station's margin minus its own mean over
   the same short window, which is mechanically mean-reverting at T of 10 to 11
   weeks (Nickell bias, order 1/T). Panel coefficients are therefore about ten
   times the national ones and **are not comparable to them**.
2. The national cost series contains only nine weekly changes over the window,
   so the sign split that defines the whole exercise rests on a handful of
   national weeks.

This is scaffolding that gets stronger as history accumulates, not a result.

---

## Validation

Accuracy is treated as a **gate, not a target**. A perfectly accurate fair-price
model would explain away all overcharging, so the model is deliberately not
tuned for minimum error.

Splits are **spatial**, never random: stations cluster, so a random k-fold would
leak a station's neighbours into its own training set. Folds are 5-fold
GroupKFold over roughly 25km grid cells, sized against the 5km rival radius so
only border stations can leak competition features. Every row for a station
stays on one side of any split.

Out-of-fold results on 12 dense weeks:

**Petrol (E10)**, 57,443 station-weeks, 7,636 stations, 418 cells

| model | MAE | MAE within-week | RMSE | Spearman | top-decile capture |
|---|---|---|---|---|---|
| **LightGBM (Signal 2)** | **2.98** | **2.98** | **3.98** | **0.388** | **22.8%** |
| LightGBM, affluence-blind | 3.08 | 3.08 | 4.07 | 0.303 | 20.0% |
| regional median baseline | 4.31 | 3.03 | 5.52 | 0.323 | 15.2% |
| predict zero (Signal 1 alone) | 4.94 | 3.33 | 6.37 | n/a | n/a |

**Diesel (B7)**, 62,184 station-weeks, 7,715 stations, 421 cells

| model | MAE | MAE within-week | RMSE | Spearman | top-decile capture |
|---|---|---|---|---|---|
| **LightGBM (Signal 2)** | **3.58** | **3.59** | **4.67** | **0.378** | **22.3%** |
| LightGBM, affluence-blind | 3.66 | 3.66 | 4.75 | 0.307 | 20.7% |
| regional median baseline | 4.51 | 3.64 | 5.72 | 0.295 | 15.1% |
| predict zero (Signal 1 alone) | 5.54 | 3.97 | 6.99 | n/a | n/a |

The accuracy gate is the **within-week demeaned** MAE, so the model cannot
flatter itself by learning which week it is. On that gate Signal 2 beats a
regional median by under 2%, while beating it on rank skill by 20% (petrol) to
28% (diesel) and on top-decile capture by about 50% for both. Hence the
ranking-only claim.

Note that these numbers are *worse* than an earlier 8-week run (petrol Spearman
was 0.439, top-decile 24.8%). More data made the measured skill go down. The
earlier figure was optimistic, and it is the current one that is reported.

---

## Architecture

```
Fuel Finder API  ->  data/raw/        gzipped JSON snapshots, immutable bronze
                     build_silver.py  one row per price-change event, cleaned
                     build_features.py  + wholesale, competition, location
                     build_signal2.py   cross-fitted peer scores, weekly
                     build_gold.py      app table, repriced at current wholesale
                  ->  Streamlit app on Community Cloud
```

Collection runs locally on a schedule (four weekday slots) because the API is
geo-restricted to the UK. GitHub Actions rebuild the app table on every
snapshot push, refresh wholesale prices weekly, and refresh Signal 2 weekly.

One design decision worth calling out: the gold table **recomputes** fair price
against the current wholesale week rather than carrying the value from the price
event date. A stale standing price should be judged against today's costs, not
against the costs of the day it was set. Event-time values are kept in the
feature layer for modelling.

Signal 2 scoring in the app is **cross-fitted**: each station is scored by the
fold model that held its own grid cell out. A single model trained on everything
would partly memorise each station's own overcharging and then report a small
gap for exactly the stations the project exists to catch.

---

## Honest limitations

**The wholesale price is a proxy.** NYMEX RBOB and heating oil converted to
pence per litre, because the Platts and Argus Rotterdam benchmarks the CMA uses
are paid services. Heating oil may understate UK diesel wholesale by 5 to 10p.
This is corrected by a constant per-fuel basis term estimated from the national
accounting identity, but a constant correction cannot track a moving basis.

Note the proxy hurts the two analyses differently. Signal 1 compares **levels**,
so it is directly exposed. The rockets-and-feathers work compares **dynamics**,
where a constant basis is absorbed into the long-run intercept. What the proxy
still does there is add measurement noise, which attenuates coefficients toward
zero, biasing that analysis **toward finding no asymmetry**. A null result there
would be weak evidence; the positive result found is therefore conservative.

**The sampling record has three documented holes.** Collection runs weekdays
only, roughly 09:00 to 17:00, so nights, weekends and holidays are missing
throughout. On top of that there was a 32-hour outage on 2026-08-10 and a
week-long gap from 2026-08-27 to 2026-09-02 during a machine handover.
Collection is not continuous and is not described as such. Sparse weeks are
dropped from modelling rather than interpolated.

**The history is short.** Collection began 2026-06-24. Twelve dense weeks
covering roughly one and a half market regimes is enough for cross-sectional
ranking across 8,100 stations, which is well powered, and not enough for
confident temporal claims. This is why the national rockets-and-feathers work
uses eight years of DESNZ data rather than the project's own series.

**Signal 2 cannot extrapolate.** Its national regime features have taken few
distinct values, and LightGBM clamps outside its training range. A next-week
transfer test failed for diesel in one earlier run, losing to a regional median
when wholesale spiked outside the training range. The app works around this by
publishing a demeaned peer figure, so the shared level error cancels and only
the ranking is claimed. The underlying model limitation stands.

**What the model cannot see at all:** site rents, rates, fuel volumes, supply
contracts, and lease terms. A station may be expensive for reasons that are
entirely legitimate and entirely invisible here. Signal 1 says a price is above
a cost-plus benchmark. It does not say anyone is behaving badly.

**Coverage gaps.** House prices are England and Wales only. Northern Ireland has
no MSOA, rural-urban class, or house price data. Motorway services and
ferry-dependent islands are excluded from Signal 2 training and analysed as
their own groups, because paired motorway services on opposite carriageways
break haversine rival distances and island delivery costs cannot be calibrated
from available data. Those stations are labelled in the app, never left blank.

**One tension is deliberate and cross-referenced.** Signal 2 includes national
wholesale regime features, which teach it to *expect* margins to widen when
wholesale rises. That is the very rocket behaviour the pass-through module
exists to call out. It was accepted because a national level term cannot change
any within-week ranking, which is the deliverable, and because Signal 1, the
primary flag, is untouched.

---

## Running it

```bash
uv sync --group notebook

python build_external.py          # reference data: DESNZ, wholesale, ONS
python build_silver.py            # bronze -> cleaned price events
python build_features.py          # + wholesale, competition, location
python signal2_validation.py --fuel E10        # spatial CV, metrics
python rocket_feathers.py                      # national asymmetric ECM
python rocket_feathers_panel.py                # station panel (provisional)
python build_signal2.py && python build_gold.py

python -m streamlit run app/streamlit_app.py
```

`data/silver/` and `data/features/` are gitignored and rebuilt locally.
`data/gold/` is committed because the deployed app reads it from the repo.

### Repo guide

| file | what it does |
|---|---|
| `fuel_snapshot.py` | collector, one API snapshot to `data/raw/` |
| `build_silver.py` | dedup, clean, coordinate healing, one row per price event |
| `build_features.py` | Signal 1 fair price, competition and location features |
| `signal2_validation.py` | spatial CV harness and the locked metric suite |
| `build_signal2.py` | cross-fitted current-week peer scores |
| `build_gold.py` | the app table, repriced at current wholesale |
| `rocket_feathers.py` | national asymmetric ECM (stage 1) |
| `rocket_feathers_panel.py` | station-level panel (stage 2, provisional) |
| `app/` | Streamlit app: map, station lookup, methodology |
| `project_definition.md` | full model definitions |
| `claude.md` | decision log, every choice and why |

---

## Data sources

- **Fuel Finder open data scheme** (`fuel-finder.service.gov.uk`), station
  details and prices, several times daily. UK geo-restricted.
- **DESNZ** weekly national road fuel prices, duty and VAT, 2018 to present.
- **NYMEX** RBOB and heating oil futures via yfinance, as the wholesale proxy.
- **ONS**: NSPL postcode lookup (MSOA, rural-urban class, region, centroids),
  MSOA median house prices.
