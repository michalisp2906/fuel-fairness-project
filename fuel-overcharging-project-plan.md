# Project Plan: "Which Petrol Stations Are Overcharging You"

A UK fuel pricing fairness model, built to be deployed, defensible, and yours.

---

## The one-line north star

You are building a model that predicts what each petrol station *should* charge given its costs and local market, then flags the stations charging well above that. You are **not** building a cheapest-fuel finder. Those already exist and they are not data science. Keep this distinction central in the README, the app, and every interview answer.

## The principle behind the build order

This is a "walking skeleton". You deploy a thin live slice early (a map of raw prices), prove the whole pipeline end to end, then layer the real intelligence on top. This guarantees you finish with a deployed project instead of a notebook, and it is itself a defensible engineering decision worth stating out loud.

## Critical first action (do this in week 1, before any modelling)

The live feed is a snapshot, not a history. The time-series half of this project (the rocket-and-feathers analysis) only exists from the day you start logging. Register for the feed and start capturing scheduled snapshots immediately. Every day you wait is history you never recover.

---

## Recommended stack (with the why, so you can defend each choice)

| Layer | Choice | Why this, defensibly |
|---|---|---|
| Language | Python | Your strength, and it owns this ecosystem |
| Dependency management | uv (or plain venv + pip) | Fast, reproducible environments |
| Scheduled ingestion | GitHub Actions (cron) | Free, version-controlled, a clean lightweight MLOps pattern |
| Store | DuckDB over date-partitioned Parquet | Query the full history in SQL (plays to your SQL strength), no server to run |
| Spatial features | GeoPandas, shapely, geopy | Standard, well-documented spatial toolchain |
| Geocoding | postcodes.io | Free UK postcode to lat/long, no key |
| Modelling | scikit-learn, LightGBM | Interpretable baseline plus a strong nonlinear model you already know |
| Time-series econometrics | statsmodels | For the asymmetric error-correction model |
| Explainability | SHAP | Every overcharging flag must be explainable |
| App (MVP) | Streamlit on Streamlit Community Cloud | Fastest honest route to a live URL with a map |
| App (optional upgrade) | FastAPI backend + Leaflet/Mapbox frontend | If you want to show full-stack range later |
| Secrets | GitHub Actions secrets / env vars | Keep the API client credentials out of the repo |

Do not over-engineer the store early. Parquet plus DuckDB is enough until the app genuinely needs a live queryable database, at which point Supabase or Neon (free Postgres tiers) are the upgrade.

---

## Phase 0: Framing and foundations (about 1 week)

**Goal.** Pin down the problem precisely and set up the project so everything after is clean.

**What you build.**
- A written problem statement and a precise definition of "fair price" and "overcharging". This is the intellectual core of the whole project. Decide now: overcharging means the actual pump price sits materially above the price your model predicts from legitimate cost and market drivers.
- Scope decisions: start with two fuels (E10 petrol and B7 diesel), and decide whether to start with one region (cleaner, faster) or go national from day one.
- A git repository with a sensible structure (separate the ingestion, the feature build, the model, and the app), a virtual environment, and a README stub.

**Where to look.**
- CMA road fuel market study and its monitoring reports, for the official framing of the competition problem, retail spread, and fuel margins. Search "CMA road fuel market study" and "CMA road fuel monitoring report" on gov.uk.
- The original "rockets and feathers" idea (Bacon, 1991) and any recent UK fuel pass-through analysis, so you can speak the economics.

**Learning checkpoint.** Be able to explain, in two sentences, why fuel prices might rise faster than they fall, and what a "fair" price even means when you cannot see a station's rent, volumes, or supply contracts.

**Be ready to answer.** "Why is this a modelling problem and not just a price comparison?" Your answer is that the comparison question is trivial and solved; the interesting question is conditional fairness, which requires controlling for cost and local market structure.

---

## Phase 1: Data acquisition and the ingestion pipeline (about 1 to 2 weeks)

**Goal.** A reliable, scheduled pipeline that accumulates a growing history of station prices, plus the cost and context data you will join to it.

**What you build.**
- Registration for the Fuel Finder feed and working access via the OAuth 2.0 API (you create a client id and client secret on the developer portal). The CSV flat file is the simpler fallback for a daily history; the API gives you intra-day changes, which matter for the asymmetry work.
- A scheduled job (GitHub Actions cron) that pulls the feed several times a day, timestamps each pull, and appends it as a partitioned Parquet snapshot. This is the single most important piece of infrastructure. Get it running early and leave it running.
- Ingestion of the cost inputs (the wholesale cost basis) and the context data (for competition and location features).
- Data validation checks on every pull (schema, plausible price ranges, station counts, duplicate handling). This is your QA and FCA-grade data-integrity background showing through, and it is a genuine portfolio strength, so make it visible.

**Where to look.**
- The feed: gov.uk guidance "Access the latest fuel prices and forecourt data via API or email", and the developer portal at developer.fuel-finder.service.gov.uk. The fields include trading name, address, latitude and longitude, and the selling price of each grade (E10, E5, B7 diesel, super diesel, B10, HVO).
- An existing open-source consumer of this API (for example the "Fuel-Prices-UK" Home Assistant integration on GitHub) is a useful reference for how the OAuth flow works in practice. Read it to understand the auth, then write your own ingestion.
- Wholesale and crude cost data: the US EIA open data API (free) for Brent crude and refined product (gasoil and gasoline) spot prices, and the DESNZ "Weekly road fuel prices" series on gov.uk for a UK pump and duty reference. Remember fuel duty (currently frozen) and VAT at 20 percent are part of the cost stack.
- Context data: ONS population density and local income by small area, for rurality and affluence features, and OS Open Roads or OpenStreetMap for the motorway and road context.

**Learning checkpoint.** Scheduling and idempotent ingestion (a re-run should not corrupt your history), and secrets handling in GitHub Actions.

**Be ready to answer.** "How did you build a time series from a live snapshot feed, and what are the gaps?" You captured snapshots on a schedule; the gaps are the periods before you started and any times the feed or your job was down, which you log and account for.

---

## Phase 2: Feature engineering (about 1 week)

**Goal.** Turn raw prices and locations into the features that explain a fair price.

**What you build.**
- A cost basis per litre (wholesale product price plus duty plus VAT), which acts as the floor and the main driver.
- Competition features per station: count of rival stations within a chosen radius, distance to nearest rival, the price of nearby rivals, and brand mix (supermarket versus oil-major versus independent).
- Location features: rurality (population density), a motorway or trunk-road flag, region, and local income.
- Temporal features for the asymmetry work later: lagged wholesale changes, split into positive and negative moves.

**Where to look.**
- GeoPandas documentation for spatial joins and distance calculations. The competition features are spatial joins between stations and their neighbours.
- Think hard about the radius choice for "local competition". It is a real decision with a real effect, so test a few and justify your pick rather than hard-coding one.

**Learning checkpoint.** Spatial joins, coordinate reference systems (use a projected CRS for distances, not raw lat/long), and avoiding the trap of leaking a station's own price into its competition features.

**Be ready to answer.** "What makes a price legitimately high versus an overcharge?" Your features are the answer: rurality, lack of local competition, and motorway captivity are legitimate; a high price with none of those present is the signal.

---

## Phase 3: Modelling (about 2 weeks)

**Goal.** A defensible fair-price model and an overcharging score, with honest validation.

**What you build.**
- A simple interpretable baseline first (ordinary least squares or a generalised linear model) predicting price from cost and features. This establishes the fair price transparently and gives you something to beat.
- Then a gradient-boosted model (LightGBM) for the nonlinear fair-price prediction. Consider quantile regression to produce a fair-price band rather than a single point, so "overcharging" becomes "above the upper band" rather than an arbitrary threshold.
- The overcharging score: the residual of actual price minus predicted fair price, or the station's position relative to the predicted quantile band.
- SHAP explanations so each flag comes with its reasons (this station is 9p above fair, driven mostly by X and Y).
- The rocket-and-feathers module: an asymmetric error-correction model on your accumulating time series, testing whether pump prices rise faster on wholesale increases than they fall on decreases. Be explicit that this strengthens as your logged history grows, and that early results are provisional.
- An honest limitations document covering what the model cannot see (rents, volumes, supply contracts), the short time series, and the imperfection of the cost proxy.

**Where to look.**
- scikit-learn for the baseline and cross-validation machinery, LightGBM docs for the main model, statsmodels for the error-correction model, and the SHAP docs for explanations.
- Read up on spatial cross-validation. Random k-fold leaks here because neighbouring stations are correlated, so group your folds by local market or region.

**Learning checkpoint.** Spatial cross-validation, quantile regression, SHAP, and the structure of an asymmetric error-correction model.

**Be ready to answer.** "Why should I trust your fair-price number?" Because it is validated with spatial cross-validation to avoid leakage, benchmarked against a transparent baseline, and every prediction is explainable rather than a black box. "Why not deep learning?" Tabular data of this size and a need for interpretability favour gradient boosting; you can say that cleanly.

---

## Phase 4: Deployment (about 2 weeks, your main growth area)

**Goal.** A live, public URL where anyone can see the overcharging map and look up a station.

**What you build.**
- The walking-skeleton deploy first: a Streamlit app showing a map of stations from your stored data, deployed to a live URL, before the model is even wired in. This proves the pipeline.
- Then wire in the scored data: a map coloured by overcharging score, a station lookup, and the SHAP-based reasons for each flag.
- A scoring job (run after each ingestion, on the same GitHub Actions schedule) that recomputes features and overcharging scores and writes them where the app reads them.
- Model artifact handling (save with joblib, load in the app).

**Where to look.**
- Streamlit docs, and pydeck or folium for the map layer inside Streamlit.
- Streamlit Community Cloud for free hosting. If you outgrow it or move to FastAPI, look at Hugging Face Spaces, Render, Railway, or Fly.io.
- If you take the optional upgrade path: FastAPI for the backend API, and Leaflet with OpenStreetMap tiles (free) or Mapbox (free tier) for a custom frontend.

**Learning checkpoint.** Deploying a scheduled-data app, separating the offline scoring job from the serving app, and managing the model artifact. Optionally, containerising with Docker.

**Be ready to answer.** "Walk me through your architecture." Ingestion on a schedule, into a partitioned store, a scoring job that produces station scores, and a thin app that serves them on a map. Simple, cheap, and it runs itself.

---

## Phase 5: Polish and write-up (about 1 week)

**Goal.** Make the rigour visible, because the write-up is what converts the project into a job.

**What you build.**
- A README that leads with the problem and the commercial relevance (tie it to the live CMA scrutiny of fuel pricing), then the methodology, the "why this approach" decisions, and an honest limitations section.
- Validation visuals: how the model performs, and a few worked examples of stations it flags and why.
- A short blog-style write-up or a LinkedIn post, framed around the fairness finding rather than the tech.

**Be ready to answer.** "What does it get wrong, and why?" Have a crisp, honest answer ready. This is the question that separates senior candidates, and you flagged it yourself as the bar.

---

## The decisions you personally must own

These are the choices an interviewer will press on. Do not let any tool make them for you.

1. The definition of "fair price" and what counts as overcharging.
2. The competition radius and how you justified it.
3. The validation strategy (why spatial cross-validation, not random).
4. The cost proxy and its limitations (you cannot see true wholesale buy prices per station).
5. The model choice (interpretable baseline plus gradient boosting, and why not deep learning).
6. The honest account of what the model cannot see and therefore gets wrong.

---

## Resource list

- Fuel Finder access: gov.uk guidance "Access the latest fuel prices and forecourt data via API or email"; developer.fuel-finder.service.gov.uk
- Competition and pricing context: CMA road fuel market study and monitoring reports (gov.uk)
- Wholesale and crude: US EIA open data API; DESNZ "Weekly road fuel prices" (gov.uk)
- Geographic and demographic: ONS (population density, income); OS Open Roads or OpenStreetMap
- Geocoding: postcodes.io
- Reference implementation of the feed auth: search GitHub for UK Fuel Finder API integrations

---

## Suggested timeline

| Weeks | Phase |
|---|---|
| 1 | Phase 0 framing, and start logging snapshots (Phase 1 ingestion live) |
| 2 to 3 | Phase 1 finish the pipeline and cost/context data |
| 4 | Phase 2 features |
| 5 to 6 | Phase 3 modelling |
| 7 to 8 | Phase 4 deployment |
| 9 | Phase 5 polish and write-up |

The history keeps accumulating the whole time, so by the write-up your rocket-and-feathers analysis has real data behind it.
