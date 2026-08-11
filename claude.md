# CLAUDE.md

## Project
A UK fuel pricing fairness model. It predicts what each petrol station *should*
charge given its costs and local market, then flags stations charging well above
that (an "overcharging" score), plus a rocket-and-feathers analysis of whether
pump prices rise faster than they fall. Ends as a deployed web app.

You should always be precise, double check what you do, deep dive, and question me 
if you think I am wrong, so that we always reach the best answer.

**Framing rule:** this is NOT a cheapest-fuel finder. Those exist and are not data
science. The differentiator is the fair-price model and overcharging detection.

Full roadmap is in `fuel-overcharging-project-plan.md`. Read it for the bigger
picture and phase order.

## How to work with me
- Explain the approach and trade-offs BEFORE making changes. Do not just implement.
- For modelling, feature, and validation decisions: propose options with reasoning
  and let me choose. I must be able to defend every decision in interviews.
- Plumbing and boilerplate (ingestion, error handling, scaffolding) you can handle
  directly.
- Prefer simple, readable solutions over clever ones.
- I am learning, so favour clarity and tell me why, not just what.

## Working philosophy:
- Plan before you act. State your approach explicitly before writing code.
- Log every assumption. If you're not certain, say so and ask.
- Treat your first solution as a draft. Actively look for flaws before presenting it.
- If you believe the requested approach is wrong or suboptimal, say so first.
  Propose an alternative. Do not silently implement something you'd improve.
- Before calling a task complete, verify your output against the original
  requirement step by step. List what you checked.
- Precision over speed. A slower, correct answer beats a fast, plausible one.

## Writing style
- Plain punctuation only. Never use em dashes or en dashes. Use commas, periods,
  colons, parentheses.
- No corporate filler.

## Environment
- THROUGH AUGUST 2026: all development is on the **MacBook only** (user is in
  Cyprus, Windows PC is off). Assume bash/zsh, never PowerShell, until the user
  says otherwise. Collection runs on the Android phone, not the PC.
- Collection (`fuel_snapshot.py`) normally runs on the Windows PC via Task
  Scheduler. It requires a **UK-geolocating** connection (see "API access is
  geo-blocked" below). An Android phone in Termux is a proven second collector.
- Three machines now: Windows PC (primary collector), MacBook Pro (analysis),
  Android phone (backup collector, see `docs/termux_collection.md`).
- Windows PC: Windows, PowerShell (not bash). Terminal commands must be PowerShell
  syntax. Runs collection plus any development work done there.
- MacBook Pro: zsh/bash. Used for analysis and development (`build_silver.py`,
  `eda.ipynb`, modelling). Has no `.env`, so it cannot run live collection; it
  works from the raw snapshots synced via git. Terminal commands here are bash/zsh,
  not PowerShell.
- Python in the project virtual environment at `.venv` (machine-local, not synced).
- Editor is VS Code.
- Dependencies declared in `pyproject.toml`, managed with `uv`. Run `uv sync --group notebook` to reproduce the environment on either machine. `uv.lock` is committed.

## Data source: Fuel Finder API (reverse-engineered, official docs are bot-walled)
- Base URL: `https://www.fuel-finder.service.gov.uk`
- Token: POST `/api/v1/oauth/generate_access_token`, FORM-ENCODED body with
  `grant_type=client_credentials`, `client_id`, `client_secret`, `scope=fuelfinder.read`.
  Token is in `data.access_token`.
- Station details: GET `/api/v1/pfs` (location lat/long, postcode, brand, motorway
  and supermarket flags, closure flags).
- Prices: GET `/api/v1/pfs/fuel-prices`.
- Paging: `?batch-number=1`, increment until empty or 404.
- Records joined on `node_id`.
- Prices are in PENCE PER LITRE. No conversion.
- Fuel grades: API uses E10, E5, B7_STANDARD, B7_PREMIUM, B10, HVO. The twice-daily
  CSV abbreviates diesel as B7S and B7P. Handle both naming conventions.

## API access is GEO-blocked, not data-centre-blocked (corrected 2026-08-02)
Supersedes the earlier "the API refuses data-centre IPs" claim, which was wrong.
That was a misdiagnosis: the original VPN test almost certainly used a non-UK
exit, so a geo-block got recorded as a hosting-type block.

Evidence gathered 2026-08-02 (user in Cyprus):
- From a Cypriot consumer ISP (EPIC, mobile/residential), EVERY request 403s,
  including the plain homepage with a normal browser User-Agent. Response is
  `server: CloudFront`, `content-length: 0`. The 403 comes from the CDN edge;
  the request never reaches the application.
- From a commercial VPN with a UK exit on a pure data-centre range
  (`84.17.50.144`, Datacamp Ltd / cdn77.com), a deliberately-invalid-credentials
  probe returned `401` from `server: nginx/1.25.5` with a JSON body
  `"Invalid client credentials"`. That is the ORIGIN application answering, so
  the request completed the full journey.
- Confirmed end to end: a real collection from the Android phone on that UK VPN
  succeeded and pushed (commit c3b4897, 7,998 stations, payload verified
  complete and grade-clean).

What this means:
- Hosting type is irrelevant. UK geolocation is the ONLY requirement.
- Diagnostic tell: an empty-body 403 with `server: CloudFront` is a geo-block.
  A JSON 401 from nginx means you got through and the credentials are the issue.
- OPEN QUESTION, do not assume either way: "cloud collection is impossible"
  rested on the false premise, so it should be RE-TESTED. Caveat: CloudFront may
  treat known cloud ranges differently from CDN77, and GitHub's Azure runner IPs
  are not guaranteed to geolocate to the UK. Needs an actual test, not an
  assumption. If it passes, the residential-IP constraint that shapes this
  project's whole architecture disappears.
- Honesty flag, unresolved: check whether the API terms restrict access by
  location. The project must be defensible in interviews, so we want to know
  how the collection method reads before someone else asks.

## Decisions already made (do not reopen without flagging)
- Model E10 (petrol) and B7_STANDARD (diesel) first. Other grades captured but parked.
- Anchor the time series on `price_change_effective_timestamp`.
- Canonical price unit: pence per litre.
- Storage: raw gzipped JSON snapshots are the immutable "bronze" layer in `data/raw/`,
  partitioned by date. Tidy tables (Parquet, DuckDB) get built on top, not in place of.
- Collection runs LOCALLY (Windows Task Scheduler, or Termux cron on the Android
  phone). Cloud collection was abandoned on the data-centre-IP premise, which is
  now known to be false: see "API access is GEO-blocked" above. Treat cloud
  collection as UNTESTED rather than impossible.
- Known limitation: PC runs weekdays ~9 to 5, so collection misses nights, weekends,
  and holidays. This is a deliberate, documented sampling gap. The phone is always
  on and could close it (2-hourly, 7 days); if the cadence changes, record it in
  the write-up so the sampling record stays honest.

## Guardrails
- Credentials live ONLY in `.env` (local) and never in code. `.env` MUST stay
  gitignored. Never commit `.env`, `.venv`, or any secret.
- Validation must be temporal (train on past, test on future) and spatial (group folds
  by local market or region). Never naive random splits, because stations cluster.
- If an unexpected fuel grade appears, flag it. Never silently drop data.

## Repo
- GitHub: https://github.com/michalisp2906/fuel-fairness-project

## Fuel duty rate (critical: do not use the wrong figure)
- Duty was cut from 57.95p to 52.95p/litre on 28 March 2022 and has remained
  at 52.95p since. The DESNZ CSV confirms this. Do NOT use 57.95p.
- VAT remains 20%, applied on top of duty-inclusive price.

## Fair-price model definitions (locked, see project_definition.md for full detail)
- Signal 1 (cost-plus): fair price = (wholesale + duty + fair_margin) * 1.2
  Fair margin = 7p/litre (CMA pre-2022 baseline; CMA says current ~10.7p is excessive).
- Signal 2 (peer-relative): LightGBM trained on Signal 1 residuals with competition
  and location features (house price index, rural-urban classification, rival counts).
- Combined flag: Signal 1 is the primary YES/NO. Signal 2 ranks within flagged group.
  Signal 1 high + Signal 2 low = local market problem, flag all. Never excuse collective
  overcharging.
- Rocket-and-feathers: separate module, pass-through asymmetry only.
- Wholesale lag: Signal 1 uses wholesale lagged 10 days (decided 2026-07-02),
  matching the CMA's 1-2 week pass-through estimate. Backward as-of join against
  week-END-labelled weekly data, so no lookahead; effective lag 10-16 days.
  Sensitivity check at 7/14 days planned; validate later against the
  rocket-and-feathers pass-through estimate.
- Brand is EXCLUDED from Signal 2 features (decided 2026-07-02): including it
  would normalise brand-wide premiums, which violates the collective-overcharging
  rule. Brand stays in EDA and reporting. is_motorway and is_supermarket are in
  the same grey zone, to be discussed before Signal 2 training.
- MSOA join method: ONS postcode directory (ONSPD/NSPL) lookup on station
  postcode, not point-in-polygon (decided 2026-07-02).
- Dedup tiebreak: on price_ppl collisions at the same effective timestamp, keep
  the row with the latest price_last_updated, treating it as a station
  correction (decided 2026-07-02, fixed in build_silver.py).
- Signal 1 flag (decided 2026-07-03): proxy basis correction + noise buffer.
  Per-fuel constant basis (UK wholesale minus NYMEX proxy) estimated from the
  national accounting identity (DESNZ pump / 1.2 - duty - CMA margin 10.7p)
  over a trailing 104-week window: currently E10 +7.0p, B7_STANDARD +9.3p.
  Constant, NOT rolling: a rolling calibration would absorb genuine national
  margin dynamics (rockets and feathers) into the correction. Flag =
  overcharge_ppl > 3p buffer (~1 weekly std of the basis series). Known cost:
  month-level fair-price levels carry a few pence of drift uncertainty, shared
  by all stations, so cross-sectional comparisons are unaffected.
- Gold layer for the app (decided 2026-07-03): data/gold/app_data.parquet
  (build_gold.py) holds the latest price per station per modelled grade, with
  fair price, overcharge, and flag RECOMPUTED against the current wholesale
  week (same 10-day lag convention), not carried from the event date. A stale
  standing price is judged on today's costs. Event-time values stay in the
  feature layer for modelling. Gold IS committed to git (about 800 KB, zstd,
  category dtypes) because the deployed app reads it from the repo clone.
- Signal 2 feature exclusions (decided 2026-07-03): brand, is_motorway, and
  is_supermarket are all excluded as features (own-type attributes would
  normalise group-wide premiums). dist_nearest_supermarket_km STAYS (rival
  pressure from others is legitimate). Motorway stations are excluded from
  Signal 2 training entirely and analysed as their own comparison group
  (also sidesteps the paired-services distance problem).
- Signal 2 ACCEPTED (decided 2026-08-02, Decision 5): it passes the gate as an
  explicitly CROSS-SECTIONAL RANKING model, not as an accurate fair-price
  predictor. The write-up must claim ranking skill and nothing more. Evidence
  (E10, 36,392 station-weeks, 7 dense weeks): Spearman 0.439 vs 0.324 for a
  regional median, top-decile capture 24.8% vs 18.3% (random 10%). On the
  accuracy gate it beats the regional median by only about 3%, so accuracy is
  NOT the claim.
- median_house_price DROPPED as a feature (decided 2026-08-02): it is
  house_price_index * 290000 exactly (build_external.py), Spearman 1.000000,
  so the two were one variable entered twice and LightGBM split the gain
  arbitrarily between the copies. Dropping it left MAE and RMSE unchanged and
  moved both rank metrics marginally the right way. house_price_index stays.
  Lesson worth keeping: a gain table cannot be read feature-by-feature when
  inputs are collinear.
- House price KEPT but scored BOTH WAYS (decided 2026-08-02, Decision 6).
  Signal 2 is trained twice per fuel, with and without house price
  (FEATURES_NO_HP), and both out-of-fold predictions are saved so the app can
  show which stations are excused only by sitting in an expensive area.
  Rationale: house price is the largest static driver and carries most of
  Signal 2's ranking edge (dropping it takes E10 Spearman 0.439 -> 0.354,
  against 0.324 for a regional median), but it cannot separate genuine site
  costs (rent, rates, land) from willingness-to-pay discrimination. Publishing
  one number would hide that; publishing both makes it a finding.
  EQUITY FINDING, quantified by affluence_sensitivity(): including house price
  does not only forgive rich areas, it judges poor ones MORE harshly. E10:
  Spearman(house_price_index, pence excused) 0.662; richest decile of MSOAs
  +1.52p excused, poorest decile -1.28p (i.e. penalised); 130 of the 638
  worst-decile stations are there ONLY because house price is in the model.
  Diesel is materially the same (0.625, +1.70p / -1.22p, 127 of 640).
- Signal 2 time-varying features ADDED (decided 2026-08-02, Decision 7):
  national market regime only, wholesale_ppl and wholesale_chg_4w
  (WEEK_NUMERIC in signal2_validation.py), built from the already-10-day-lagged
  wholesale_ppl so there is no lookahead. REJECTED at the same time:
  * lagged own overcharge: it is the target autocorrelated, would dominate
    every other feature, and would normalise a station's own persistent
    overcharging. Same objection as brand, in its purest form.
  * lagged rival price pressure: defensible, but deferred.
  * price staleness (weeks since last reprice): deferred until the
    rocket-and-feathers module can say whether slow pass-through is a cost
    story or an unfairness story. Adding it now risks excusing feathers.
  What this bought: it fixed a structural defect. Before, EVERY feature was
  static per station, so the model emitted one constant per station and 0 of
  7,554 stations had more than one distinct prediction across 7 weeks. Now
  7,386 of 7,554 vary by week, via interactions with station features.
  ACCEPTED COST, documented: wholesale_chg_4w is the single largest E10 driver
  (30.4% gain) and it teaches the model to EXPECT margins to widen when
  wholesale rises, which is exactly the rocket behaviour the separate
  rocket-and-feathers module exists to call out. Judged acceptable because a
  national level term cannot change any within-week ranking (the deliverable)
  and Signal 1, the primary flag, is untouched. Cross-reference this from the
  rocket-and-feathers write-up.
- Accuracy gate is now the WITHIN-WEEK DEMEANED MAE (decided 2026-08-02,
  amends Decision 4): actual and predicted are demeaned within each week
  before scoring, so the national level term cannot flatter the number. Raw
  MAE fell 3.67 -> 2.82 when the regime features went in, but that was almost
  entirely the model learning which week it was: Spearman moved only
  0.423 -> 0.439. Demeaned, E10 is 2.82 vs 2.91 regional median vs 3.19
  predict-zero. Raw MAE and RMSE are still reported alongside.
- "temporal_check" RENAMED to "next_week_transfer_check" (2026-08-02). The old
  name and docstring claimed it was the regime-shift test. It was not: with
  all-static features the model could not respond to a regime shift at all.
  It measures whether the learned mapping still ranks the following week.
- Signal 2 app scoring is CROSS-FITTED, not a production refit (decided
  2026-08-11, Decision 8). build_signal2.py refits the same five
  GroupKFold-by-cell models the validation harness scores, then predicts each
  station with the fold model that HELD ITS CELL OUT, using today's national
  regime values. Every station gets a score, no station is scored by a model
  that trained on it, and the app number is the same quantity the published CV
  metrics describe. REJECTED: (a) reusing the stored out-of-fold predictions,
  which belong to past weeks and are week-specific now that regime features are
  in; (b) one model trained on everything, which partly memorises each
  station's own overcharging across its ~8 station-weeks and would report a
  small gap for exactly the stations the project exists to catch (the brand
  objection arriving through the back door).
- The app shows a DEMEANED peer figure (decided 2026-08-11, Decision 9).
  signal2_ppl = leftover (actual minus predicted) minus the median leftover
  across scored stations of that fuel, i.e. "pence dearer than the typical
  comparable station today". Forced by measurement, not taste: on 2026-08-11
  the raw leftover median was -2.0p (E10) and -3.1p (B7), because the fold
  models were trained on a wide-margin regime and clamp when wholesale moves
  outside it, so the app would have told visitors that nearly every station in
  the country undercharges. The error is a single level shared by all stations,
  so demeaning removes it and leaves the ranking untouched. Consistent with the
  demeaned accuracy gate from Decision 4 as amended, so the app and the
  validation harness now describe the same quantity. ACCEPTED COST, stated on
  the Methodology page: Signal 2 answers "dearer than comparable stations", not
  "dearer by N pence". Signal 1 remains the absolute number.
- Excluded stations are LABELLED, never blank (decided 2026-08-11). Motorway
  and ferry-island rows carry signal2_status ("not compared: motorway, own
  group" etc.) and render in flat gray on the peer map view. They are 465 of
  the 623 unscored gold rows and the ones visitors click first, so a silent
  blank would read as "assessed and average".
- Signal 2 refreshes WEEKLY, gold reprices continuously (decided 2026-08-11,
  Decision 10). build_signal2.py writes PREDICTIONS ONLY to
  data/gold/signal2_scores.parquet (committed, because CI's build_gold.py reads
  it and data/features/ is gitignored); build_gold.py computes the leftover
  against the standing price at every rebuild. Retraining on all ~10 snapshot
  pushes a day would cost ten LightGBM fits each time to move a station-week
  model barely at all. .github/workflows/refresh-signal2.yml runs Mondays 08:00
  UTC, an hour after the wholesale refresh, so scores and prices share a
  wholesale week; build_gold.py warns if they ever drift apart.

## Wholesale price proxy (limitation, documented)
- Source: NYMEX RBOB Gasoline (RB=F) for petrol, NYMEX Heating Oil (HO=F) for diesel,
  via yfinance. Converted to pence/litre using GBP/USD spot rate.
- These are US contracts. CMA uses Platts/Argus Rotterdam prices (paid service).
  NYMEX is the closest free proxy but carries basis risk, especially for diesel.
  HO=F (US heating oil) may understate UK diesel wholesale by ~5-10p/litre.
  This is a documented limitation.

## External reference data (data/external/)
- desnz_pump_prices.parquet: weekly national avg pump prices + duty/VAT, 2018-present.
- wholesale_prices.parquet: weekly NYMEX wholesale proxy in pence/litre, 2018-present.
- msoa_house_prices.parquet: median house price per MSOA, year ending Sep 2025.
- rural_urban_classification.parquet: 2011 RUC per MSOA (Urban/Rural + 10-fold).
  Superseded for modelling by the RUC21 indicator in postcode_lookup.parquet.
- postcode_lookup.parquet: NSPL (May 2026) per-postcode lookup: unit-postcode
  centroid (postcode_lat/postcode_long, float32, used by coordinate healing
  in build_silver.py), 2021 MSOA code, RUC21 rural-urban indicator, and
  region (added 2026-07-08 from NSPL rgn25cd: 9 English regions plus Wales,
  Scotland, Northern Ireland mapped from pseudo-codes, 12 groups total;
  used by the Signal 2 regional-median baseline). 2.7M postcodes incl.
  terminated. The source
  zip (~180MB) is gitignored and re-downloaded by build_external.py if missing;
  the release-specific ArcGIS item id is a constant in that script, update it
  quarterly if refreshing.
- Build script: build_external.py. Re-run to refresh wholesale prices.
- Coverage: house prices England and Wales only. MSOA codes cover England,
  Wales, and Scotland (NSPL fills msoa21cd with Scottish Intermediate Zones).
  RUC21 covers England, Wales, Scotland. Northern Ireland gets nulls for all
  of these (documented limitation).
- House price table confirmed to be on MSOA 2021 boundaries (99.9% join match
  against NSPL msoa21cd for England stations).

## Current status and next steps
- DONE: collection pipeline fully operational. Task `FuelFinderSnapshot` runs
  Mon-Fri at 09:00, 11:30, 14:00, 16:30 via Windows Task Scheduler.
  History accumulating from 2026-06-24.
- DONE: bronze-to-silver pipeline (`build_silver.py`). Produces
  `data/silver/prices_silver.parquet`: one row per unique price-change event,
  joined to station details from the nearest PFS snapshot in time.
  36,469 events across 7,967 stations as of 2026-06-26.
- DONE: silver cleaning step (in `build_silver.py`, `clean_silver()`):
  - Price outliers outside [50p, 300p] dropped (24 records, likely data entry errors).
  - Brand names normalised: title-case, BP acronym preserved, compound brands
    (BP Harvest Energy, EG On The Move) corrected, apostrophe capitalisation fixed,
    unbranded variants consolidated to "Unbranded", data errors nulled out.
  - Country normalised to 5 canonical values: England, Scotland, Wales,
    Northern Ireland, UK Other. Postcode prefix used to resolve ambiguous values
    (UNITED KINGDOM, UK, empty, NaN). Only 26 rows remain as UK Other.
  - QC check uses latitude.isna() (not brand_name) to detect unmatched PFS records.
- DONE: initial EDA notebook (`eda.ipynb`). Covers grade coverage, price
  distributions, brand patterns, station type, price staleness, regional
  patterns, diesel-petrol spread. Re-run against cleaned silver 2026-07-01;
  awaiting user review.
- DONE: external reference data acquired and processed (build_external.py).
  DESNZ pump prices, NYMEX wholesale proxy, ONS MSOA house prices, rural-urban
  classification all saved as Parquet in data/external/.
- DONE: project_definition.md written. Defines the dual-signal fair-price model,
  the combined flag logic, the rocket-and-feathers module, and data sources.
- DONE: silver data-quality fixes complete. PFS fallback join and collector
  truncation fix (2026-07-02, Windows PC), dedup tiebreak on latest
  price_last_updated (2026-07-02, Mac). Silver as of 2026-07-02: 56,800 events,
  7,975 stations, zero unmatched PFS records.
- DONE: feature layer (`build_features.py`), output data/features/features.parquet
  (37,694 E10 + B7_STANDARD events, gitignored, rebuild locally):
  - Signal 1: 10-day-lagged wholesale join, fair_price_ppl, overcharge_ppl.
    Sanity-checked against DESNZ (weekly mean within ~1-3p of national average)
    and CMA margins (implied E10 margin median 12.6p vs CMA ~10.7p; diesel
    inflated ~5p by the HO=F proxy limitation, as documented).
  - Competition features (static per station, permanently closed stations
    excluded from the rival set): rival_count_1/3/5km, dist_nearest_rival_km,
    dist_nearest_supermarket_km, n_rival_brands_5km. Behave as expected
    (urban median 11 rivals in 5km vs rural 2).
  - Location features via NSPL postcode join: msoa21cd, ruc21desc, ruc_2fold,
    median_house_price, house_price_index. Match rates: England 99.9%,
    Wales 100%, Scotland 99.7% (MSOA/RUC), NI 0% (no MSOAs, documented).
    8 stations have invalid postcodes (API data errors, e.g. "BY8 4XP").
- CAVEAT for Signal 2: motorway stations have the closest median nearest
  rival (0.50km) because paired services sit on opposite carriageways.
  Haversine distance overstates motorway competition. Handled by analysing
  motorway stations as their own group outside Signal 2 training.
- DONE: Signal 1 flag implemented (basis calibration + 3p buffer, see
  decisions). June 2026 flag shares: E10 10.2% of events (motorway 85%,
  supermarkets 3%, rural 23% vs urban 7%), B7_STANDARD 22.4%. Top
  overchargers: motorway services and remote islands (Scilly, Gigha), which
  is face-valid. Remote-island delivery costs are a Signal 2 discussion item.
- DONE (2026-07-03, Windows PC): walking-skeleton Streamlit app built and
  verified locally. app/streamlit_app.py (map page: pydeck scatter coloured
  by overcharge on a diverging blue/gray/red scale, KPI tiles, filters),
  app/pages/1_Station_lookup.py (searchable table), app/pages/2_Methodology.py.
  Reads ONLY data/gold/app_data.parquet via app/app_utils.py. streamlit added
  to pyproject dependencies. Verified via AppTest smoke tests plus headless
  Edge screenshots driven over CDP (plain headless screenshots capture
  Streamlit before websocket hydration; see cdp technique in session memory).
- FINDING (2026-07-03): judged at current wholesale, 76% of diesel stations
  are flagged (E10 21%). Cross-checked against DESNZ: national avg diesel
  really is 7-9p above the fair line, wholesale fell sharply mid-June and
  pump prices are following slowly (rocket-and-feathers, visibly). Some
  postcodes arrive unspaced (e.g. TF118TG).
- DONE (2026-07-03): coordinate healing in build_silver.py. Some PFS
  snapshots carry corrupted station coordinates (lat/long swapped, longitude
  sign flipped, signs dropped, garbage; 92 stations affected, heaviest in the
  2026-06-24 snapshot, user-reported as stations in the sea and off Somalia).
  Stations do not move, so silver now assigns ONE canonical coordinate per
  station: latest observation inside the UK bounding box AND within 15 km of
  its NSPL unit-postcode centroid; else the postcode centroid (~100 m
  accuracy); else null, warned. coord_source column records which. In-box
  observations that disagree with a known centroid by >15 km take the
  centroid too (flipped signs can stay in-box; postcode is modal across
  snapshots and corroborated by town/country fields) and are logged to
  data/silver/qc/. postcode_lookup.parquet gained centroid columns for this
  (zstd-compressed, 27.5 MB, still committed).
- DONE (2026-07-03): deployed on Streamlit Community Cloud from main,
  entry file app/streamlit_app.py, deps resolved from uv.lock. App URL:
  https://fuel-fairness-project.streamlit.app
- DONE (2026-07-06): keep-alive workflow .github/workflows/keep-app-awake.yml.
  Community Cloud sleeps apps after 12h without traffic; commits and bare
  HTTP GETs do not count, only a real browser session does. Every 6 hours
  the Action renders the app in headless Chromium (Playwright, CI-only
  dependency) via .github/scripts/keep_alive.py and clicks the wake-up
  button if the app fell asleep anyway. A failed run emails the repo owner;
  investigate those, the app may be showing recruiters the sleep page.
- DONE (2026-07-06): fixed rebuild-app-data crash. One PFS snapshot
  (2026-07-03T14:13Z) listed a station twice across API batch pages;
  load_all_pfs now dedups on (node_id, pfs_pulled_at), warning loudly if
  duplicates ever conflict.
- DONE (2026-07-03): GitHub Action .github/workflows/rebuild-app-data.yml.
  Fires on pushes touching data/raw/ (the Task Scheduler pushes), rebuilds
  silver+features+gold on the runner, commits data/gold/app_data.parquet if
  changed; Streamlit Cloud redeploys on that commit. No trigger loop (bot
  commit touches only data/gold/, outside the path filter, and GITHUB_TOKEN
  pushes do not fire workflows). data/gold/ is CI-owned now: avoid committing
  it manually. Wholesale refresh (build_external.py) is NOT in the Action
  yet, so wholesale_prices.parquet still needs a manual weekly-ish re-run
  and push; build_gold.py warns when it goes stale (>21 days).
  Collection stays on Windows.
- DONE (2026-07-08): Signal 2 validation harness (signal2_validation.py,
  lightgbm + scikit-learn added to pyproject). Station-week table per fuel,
  exclusions (motorway, ferry-dependent islands by postcode district,
  closed, no coords), ~25km grid cells, 5-fold GroupKFold, predict-zero and
  regional-median baselines, locked metric suite, out-of-fold predictions
  saved to data/features/signal2_cv_{fuel}.parquet (gitignored). Islands
  are identified by a documented postcode-district list (no fixed road
  link; Skye/Anglesey have bridges so stay in). Model deliberately untuned.
  First E10 results (14,732 station-weeks, 7,386 stations, 4 dense weeks,
  415 cells): spatial OOF MAE 2.75 vs 3.50 predict-zero vs 2.82 regional
  median; per-week Spearman 0.461 vs 0.348; top-decile capture 23.9% vs
  12.9% (random 10%). Temporal check (train 3 weeks, test w/c 2026-07-06):
  MAE 2.95 vs 3.27/3.42, Spearman 0.664 (looks better than spatial CV
  because train and test share stations; it is the regime-shift test, not
  the unseen-station test, caveat in docstring). Leftover score stability
  rho 0.85-0.93 week over week. Next: user review of results, then B7
  run, feature importance inspection, wire Signal 2 into gold/app.
  SUPERSEDED 2026-08-02 by the review below. Note the "regime-shift test"
  claim in this entry was wrong; see the rename decision above.
- DONE (2026-08-02): Signal 2 REVIEWED and accepted. This unblocks the path
  that had been stalled since 2026-07-08. Rebuilt silver/features first, which
  took the modelling table from 4 dense weeks to 7 (E10: 14,732 -> 36,392
  station-weeks, 7,554 stations, 417 cells). See Decisions 5 to 7 above for
  what was decided and why. Both fuels now run; all numbers below are spatial
  5-fold GroupKFold out-of-fold, MAE/wk = the within-week demeaned accuracy
  gate.
    E10           MAE 2.82  MAE/wk 2.82  RMSE 3.75  Spearman 0.439  top-dec 24.8%
      affl-blind  MAE 2.94  MAE/wk 2.94  RMSE 3.86  Spearman 0.354  top-dec 23.7%
      zero        MAE 3.98  MAE/wk 3.19  RMSE 5.09
      regional    MAE 3.72  MAE/wk 2.91  RMSE 4.77  Spearman 0.324  top-dec 18.3%
    B7_STANDARD   MAE 3.64  MAE/wk 3.64  RMSE 4.74  Spearman 0.386  top-dec 24.3%
      affl-blind  MAE 3.71  MAE/wk 3.72  RMSE 4.83  Spearman 0.330  top-dec 23.1%
      zero        MAE 6.11  MAE/wk 4.03  RMSE 7.58
      regional    MAE 4.65  MAE/wk 3.75  RMSE 5.92  Spearman 0.267  top-dec 12.6%
  Diesel ranks slightly worse than petrol but beats its baselines by MORE,
  which is consistent with the HO=F proxy basis error inflating the level
  without destroying the cross-section. Leftover stability rho 0.79-0.92 (E10)
  and 0.69-0.86 (B7); diesel is the noisier of the two.
  Feature importance (gain, full-data fit, DIAGNOSTIC ONLY): E10
  wholesale_chg_4w 30.4%, house_price_index 18.6%, dist_nearest_rival 10.8%,
  dist_nearest_supermarket 9.9%, ruc21desc 6.5%, rival_count_5km 5.7%.
  B7 is flatter: wholesale_chg_4w 18.7%, house_price_index 18.7%,
  wholesale_ppl 17.8%, dist_nearest_supermarket 11.2%.
- OPEN and IMPORTANT (2026-08-02): the next-week transfer check is NOT
  trustworthy yet, and diesel proves it. With 7 dense weeks the two national
  regime features have taken only 7 values, and in BOTH fuels the held-out
  week fell OUTSIDE the training range of both features. LightGBM cannot
  extrapolate, so it clamps to the most extreme training week.
  * E10 looked good (MAE 2.98 vs 5.34 zero vs 5.12 regional, Spearman 0.583)
    but only because the market kept moving the same direction, so clamping
    happened to be right. That is luck, not skill.
  * B7_STANDARD FAILED: MAE 6.10 model vs 4.60 regional median. The model
    lost to a baseline. Diesel wholesale spiked 67.57 -> 74.35p in one week
    (chg_4w -2.92 -> +8.42, far outside the training range of -12.56 to
    -2.92), pump prices lagged, so margins COMPRESSED to 6.37p while the
    model confidently predicted the ~8.4p high-margin regime it had been
    trained on.
  Do not quote next-week transfer numbers in the write-up without this
  caveat. Candidate fix, NOT yet implemented, user to decide: predict the
  WITHIN-WEEK DEMEANED target instead of the raw target, which removes the
  level from the model's job entirely and so removes the extrapolation
  failure mode, at the cost of no longer producing a fair-price level from
  Signal 2. Revisit once more market regimes have been observed.
- DONE (2026-07-13): fixed silent snapshot-push failure. run_collection.ps1
  pushed without pulling and ignored the push exit code, so after the CI bot
  pushed a gold rebuild to main on 2026-07-08 every scheduled push from
  2026-07-09 onward was rejected (non-fast-forward) while the log claimed
  success. Eight snapshot commits sat local-only and app data went stale for
  5 days. Fixed: rebased and pushed the backlog, and the script now does
  git pull --rebase before push and exits loudly if pull or push fails.
- AFTER Signal 2: rocket-and-feathers, wire into app, write-up.
- DECIDED (2026-07-06): Signal 2 unit of observation is station-week (mean
  overcharge_ppl per station per week), separate models per fuel, E10 first
  (quarantines the diesel proxy basis error). Per-event weighting rejected
  (frequent repricers would dominate); pure per-station rejected (no time
  axis left for temporal validation). Known limitation, documented: only
  ~2 dense weeks collected so far and one market regime (falling wholesale,
  rocket-and-feathers), so temporal validation starts thin (train week 1,
  check week 2) and strengthens as history accumulates; cross-sectional
  ranking across ~8,000 stations is the part that is already well-powered.
- DECIDED (2026-07-08, Signal 2 Decision 2): validation design. All rows
  for a station stay on one side of any split (non-negotiable). Spatial
  grouping: ~25km grid cells, 5-fold GroupKFold. Block size reasoned from
  the 5km rival radius, so only border stations can leak competition
  features across folds (minor, documented). MSOA rejected (smaller than
  the competition radius, heavy leakage); region rejected (12 groups,
  tests transfer to unseen regions, harder than the use case needs).
  Temporal side: train week 1, check week 2 for now, grows into proper
  multi-week folds as history accumulates.
- DECIDED (2026-07-08, Signal 2 Decision 3): remote islands (Scilly,
  Gigha, etc.) follow the motorway precedent: excluded from Signal 2
  training, reported as their own comparison group. No invented
  delivery-cost feature (no data to calibrate it, and it would risk
  normalising island monopoly pricing).
- DECIDED (2026-07-08, Signal 2 Decision 4): evaluation metrics.
  Accuracy is a gate, not a target (a perfectly accurate model would
  explain away all overcharging, so we do not tune for minimum error).
  Suite: (1) headline held-out Spearman between predicted and actual
  station-week overcharge (rank skill is the job, and ranks are immune
  to the shared basis drift); (2) MAE (RMSE reported alongside) vs two
  within-fold baselines: predict-zero (Signal 1 alone) and
  regional-median overcharge (grid-cell median is uncomputable under
  grid-cell GroupKFold because held-out cells have no training
  stations, which is evidence the CV is doing its job); (3) top-decile
  capture (share of truly worst-decile stations the model also puts in
  its worst decile); (4) week-over-week Spearman stability of the
  leftover score (actual minus predicted), preliminary while history
  is thin, grows into a stability curve.
- RESOLVED (2026-08-02): the August collection gap. User is in Cyprus for
  August 2026 with the Windows PC off. Collection now runs from the Android
  phone in Termux, over a commercial VPN with a UK exit. Verified working end
  to end: snapshot -> GitHub -> CI rebuild -> live app, with the CI rebuild
  landing 70 seconds after the push. See `docs/termux_collection.md`.
  - The wholesale refresh half of this problem was solved separately by
    `.github/workflows/refresh-wholesale.yml` (Mondays 07:00 UTC), so it no
    longer depends on any machine being on.
  - STILL UNPROVEN as of 2026-08-02: only a MANUAL phone run has been
    observed. Termux cron firing unattended, with the VPN up, has not been.
    Failure mode to watch: cron fires, tunnel is down, request 403s, and the
    log records a failure nobody reads. Android "Always-on VPN" plus "Block
    connections without VPN" is what closes this. First thing to check on
    pickup: did snapshots appear without anyone touching the phone.
- OPEN (2026-08-02): gold rebuild is coupled to collection. `rebuild-app-data.yml`
  triggers only on pushes touching `data/raw/`, so the weekly wholesale refresh
  does NOT reprice the app on its own. If collection pauses (phone quiet, VPN
  down), fair prices freeze on stale wholesale and the flag share drifts toward
  100%, which looks like a finding but is an artefact. This actually happened:
  between 2026-07-13 and 2026-08-02 the gold table sat on 2026-07-13 wholesale
  while the market rose, showing 97.8% of diesel and 93.2% of petrol stations
  flagged. The 2026-08-02 rebuild (fresh wholesale) took that to 37.7% and
  41.8%, median overcharge 15.6p -> 1.6p diesel and 9.9p -> 1.3p petrol.
  Proposed fix, NOT yet implemented, user to decide: also trigger
  `rebuild-app-data.yml` on `data/external/wholesale_prices.parquet`, and add a
  "prices as of DATE" staleness banner to the app (judging old standing prices
  against current wholesale is the correct method, but it must be visible).
- DONE (2026-08-11): Signal 2 wired into gold and the app. See Decisions 8 to
  10 above for the method and why. New `build_signal2.py` (cross-fitted
  current-week scoring) writes data/gold/signal2_scores.parquet; build_gold.py
  merges it into four app columns (signal2_ppl, excused_by_affluence_ppl,
  signal2_decile, signal2_status) and computes the demeaned peer figure;
  .github/workflows/refresh-signal2.yml refreshes it weekly. The
  affluence-blind figure is NOT stored, it is signal2_ppl +
  excused_by_affluence_ppl, derived in app_utils to keep the committed table
  small (834 KB, was 792 KB).
  App changes: map page gained a "Colour by" radio (vs fair price, or vs
  comparable stations, narrower +/-6p scale) and a fifth KPI tile; station
  lookup gained Vs peers, Peer decile, and Excused by area columns; the
  Methodology page's "coming next" section became a full Signal 2 write-up
  including the equity finding and the extrapolation weakness. All three pages
  verified headless with streamlit AppTest, including the peer colour mode.
  NOTE for testing: AppTest does not put app/ on sys.path the way
  `streamlit run` does, so a test harness must insert it before importing.
  Coverage: 15,214 of 15,837 gold rows scored (96.1%). Unscored: 327 motorway,
  138 ferry island, 158 with no dense-week history, no coordinates, or closed.
  Rebuilt silver and features first (8 dense weeks now, was 7; 171,793 feature
  events, 8,070 stations, through 2026-08-10). 16.7% of compared E10 stations
  sit more than 3p above their peers.
  Signal 2 genuinely reorders: about 70% of the worst decile matches a plain
  Signal 1 ranking, so roughly 230 stations per fuel move in or out once local
  circumstances are accounted for. That is the evidence it earns its place.
  UNVERIFIED, flag on next pickup: data/gold/app_data.parquet was committed by
  hand this once (schema change, and CI cannot rebuild while collection is
  down), against the usual "gold is CI-owned" rule. refresh-signal2.yml has
  never actually run; its first scheduled fire is Monday 2026-08-17 08:00 UTC.
- KNOWN WRINKLE (2026-08-11), not fixed, level-only impact: in training the
  wholesale regime features are a within-week MEAN across events, while
  build_signal2.py scores today with a single wholesale week's value. Slightly
  different quantities, and it makes the "outside training range" verdict look
  worse than it is. Only affects the level, which Decision 9 demeans away, so
  it was left alone deliberately rather than missed.
- EDA review done 2026-07-03. Note: project started ~2026-06-24, so the
  plan's "week N" schedule does not map to calendar weeks; actual pace is
  much faster.
- NOTE: overcharge_ppl > 0 alone cannot be the Signal 1 YES/NO threshold
  (95-97% of events are positive because current market margins exceed the 7p
  fair margin, per CMA). Threshold choice is an open modelling decision.
  Related, still open: with correct (fresh) wholesale, the 3p buffer still
  flags 37.7% of diesel and 41.8% of petrol stations (2026-08-02). That is no
  longer an artefact, but it is high for a signal meant to identify UNFAIR
  pricing. Revisit the buffer and the constant basis alongside the Signal 2
  review, not separately.

## PICK UP HERE (as of 2026-08-11)
Signal 2 is IN THE APP. Collection is the only thing needing attention.
1. COLLECTION IS DOWN, and this is the live problem. Termux cron is PROVEN: it
   fired unattended 4x/day Mon-Fri from 2026-08-03 through 08-07 and again
   08-10, exactly on schedule, so that question from the last pickup is
   answered yes. Then it stopped. Last snapshot 2026-08-10T11:03Z; the 08-10
   16:30 EEST run and all four runs on Tuesday 08-11 are missing, about 30
   hours silent as of 20:00 EEST. Check in this order: VPN tunnel up (the
   silent-403 failure mode), phone on and online, Termux not killed by Android
   battery management, pushes not being rejected (the 2026-07-13 failure).
2. Decide the gold-rebuild trigger fix and the app staleness banner (OPEN item
   above). Note the coupling got worse: with collection down, gold does not
   reprice, so the app's peer comparisons and fair prices both freeze on
   2026-07-27 wholesale.
3. Decide the next-week-transfer extrapolation fix (OPEN item above, where
   diesel lost to a regional median). Probably wait for more market regimes.
   Related: the app-side workaround for the same defect is already in
   (Decision 9 demeaning), so this is now about the model, not the app.
4. Optional, cheap, high value if it pays off: re-test cloud collection now that
   the data-centre-IP premise is known to be false. Would remove the project's
   biggest architectural constraint.
Then: rocket-and-feathers module, then the write-up. The rocket-and-feathers
module has a specific job it did not have before: Decision 7 knowingly let
Signal 2 treat widening margins on rising wholesale as expected, and that needs
cross-referencing when the asymmetry is measured.

