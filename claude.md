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
- Two machines, two roles. Collection (`fuel_snapshot.py`, Task Scheduler) runs
  ONLY on the Windows PC, because the API blocks data-centre IPs and the scheduled
  task needs a residential IP. Do not move collection to the Mac or any cloud runner.
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

## Decisions already made (do not reopen without flagging)
- Model E10 (petrol) and B7_STANDARD (diesel) first. Other grades captured but parked.
- Anchor the time series on `price_change_effective_timestamp`.
- Canonical price unit: pence per litre.
- Storage: raw gzipped JSON snapshots are the immutable "bronze" layer in `data/raw/`,
  partitioned by date. Tidy tables (Parquet, DuckDB) get built on top, not in place of.
- Collection runs LOCALLY via Windows Task Scheduler. The API refuses data-centre
  IPs, so cloud collection (GitHub Actions, etc.) is abandoned.
- Known limitation: PC runs weekdays ~9 to 5, so collection misses nights, weekends,
  and holidays. This is a deliberate, documented sampling gap.

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
  in build_silver.py), 2021 MSOA code
  and RUC21 rural-urban indicator. 2.7M postcodes incl. terminated. The source
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
- AFTER deploy: Signal 2 modelling prep: temporal+spatial validation design,
  then LightGBM on Signal 1 residuals (needs lightgbm + scikit-learn added
  to pyproject). Then rocket-and-feathers, wire into app, write-up.
- DECIDED (2026-07-06): Signal 2 unit of observation is station-week (mean
  overcharge_ppl per station per week), separate models per fuel, E10 first
  (quarantines the diesel proxy basis error). Per-event weighting rejected
  (frequent repricers would dominate); pure per-station rejected (no time
  axis left for temporal validation). Known limitation, documented: only
  ~2 dense weeks collected so far and one market regime (falling wholesale,
  rocket-and-feathers), so temporal validation starts thin (train week 1,
  check week 2) and strengthens as history accumulates; cross-sectional
  ranking across ~8,000 stations is the part that is already well-powered.
- TO DISCUSS: user away for all of August 2026, Windows PC off, so
  collection stops (API needs a residential IP, cloud collection is not an
  option) and the manual weekly wholesale refresh stops too (app fair
  prices go stale; build_gold warns at 21 days). Options to weigh nearer
  the time: spare always-on machine on home network, Raspberry Pi,
  relative's machine, or accept and document the gap. Keep-alive and CI
  are cloud-side and unaffected.
- EDA review done 2026-07-03. Note: project started ~2026-06-24, so the
  plan's "week N" schedule does not map to calendar weeks; actual pace is
  much faster.
- NOTE: overcharge_ppl > 0 alone cannot be the Signal 1 YES/NO threshold
  (95-97% of events are positive because current market margins exceed the 7p
  fair margin, per CMA). Threshold choice is an open modelling decision.
- No modelling started yet.

