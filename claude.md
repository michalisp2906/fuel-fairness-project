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
  patterns, diesel-petrol spread. Needs a re-run against the cleaned silver data.
- NEXT: re-run eda.ipynb against the cleaned silver layer, then begin the
  fair-price model (Phase 2 in the roadmap).
- No modelling started yet. Still in the data collection and cleaning phase.

