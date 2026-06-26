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
- Windows, PowerShell (not bash). Terminal commands must be PowerShell syntax.
- Python in the project virtual environment at `.venv`.
- Editor is VS Code.
- Dependencies declared in `pyproject.toml`, managed with `uv`. Run `uv sync --group notebook` to reproduce the environment. `uv.lock` is committed.

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
- Ingestion script (`fuel_snapshot.py`) works locally: authenticates and saves both
  station and price snapshots.
- `run_collection.ps1` loads `.env`, runs the collector, commits and pushes. Tested
  end to end: 7983 PFS records and 7968 price records collected and pushed on first
  clean run (2026-06-24).
- `setup_scheduler.ps1` registers the Task Scheduler task. Task `FuelFinderSnapshot`
  is live, Mon-Fri at 09:00, 11:30, 14:00, 16:30.
- `.env` confirmed gitignored and not tracked by git.
- Dead `.github/workflows/collect.yml` cloud workflow removed.
- All project files committed (.gitignore, CLAUDE.md, fuel-overcharging-project-plan.md,
  run_collection.ps1, setup_scheduler.ps1, fuel_snapshot.py). logs/ in .gitignore.
- DONE: collection pipeline fully operational. History accumulating from 2026-06-24.
- DONE: bronze-to-silver pipeline (`build_silver.py`). Produces
  `data/silver/prices_silver.parquet`: one row per unique price-change event,
  joined to station details from the nearest PFS snapshot in time.
  29,318 events across 7,969 stations as of 2026-06-25.
- NEXT: exploratory data analysis on the silver layer. Understand price
  distributions, brand patterns, and the shape of price-change events before
  modelling.
- No modelling started yet. Still in the data collection phase.

