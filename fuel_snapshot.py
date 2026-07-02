"""
Fuel Finder snapshot logger.

Pulls station details and fuel prices from the UK Government Fuel Finder API
and writes a timestamped, gzipped RAW JSON snapshot for each. Run it on a
schedule to build the price history your project depends on.

Credentials are read from environment variables. Never hard-code them and
never commit them:
    FUEL_FINDER_CLIENT_ID
    FUEL_FINDER_CLIENT_SECRET

Why raw JSON snapshots: this is your "bronze" layer. Storing exactly what the
API returned, untouched, means you can re-parse it any way you like later
without ever needing to re-pull. You will build the tidy tables (Parquet,
DuckDB) on top of this, in a separate step.

Run:
    pip install requests
    export FUEL_FINDER_CLIENT_ID=...        # do not paste these into code
    export FUEL_FINDER_CLIENT_SECRET=...
    python fuel_snapshot.py
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "https://www.fuel-finder.service.gov.uk"
TOKEN_ENDPOINT = "/api/v1/oauth/generate_access_token"
ENDPOINTS = {
    "pfs": "/api/v1/pfs",                  # station details (changes rarely)
    "prices": "/api/v1/pfs/fuel-prices",   # fuel prices (changes often)
}

# --- Decisions you own (sensible starting values) ----------------------------
DATA_DIR = Path("data/raw")          # where snapshots land; your call on layout
REQUEST_GAP_SECONDS = 2.1            # politeness gap between calls
REQUEST_TIMEOUT = 30
MAX_BATCHES = 300                    # safety stop so a bug cannot loop forever

# Transient errors (rate limit + temporary server faults) get retried rather
# than crashing the whole snapshot. One server hiccup should not leave a hole
# in your history.
TRANSIENT_STATUS = {429, 500, 502, 503, 504}
MAX_TRANSIENT_RETRIES = 4            # attempts after the first try, then give up
RETRY_WAIT_SECONDS = 5              # base wait; grows with each retry
# Set a real contact so the service can reach you rather than just blocking you.
USER_AGENT = "fuel-fairness-research/0.1 (m.papamichael29@gmail.com)"
# -----------------------------------------------------------------------------


def get_token(client_id: str, client_secret: str) -> str:
    """Exchange client credentials for a short-lived access token.

    Uses the format documented by Fuel Finder: form-encoded body with
    grant_type and scope, not JSON.
    """
    resp = requests.post(
        f"{BASE_URL}{TOKEN_ENDPOINT}",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "fuelfinder.read",
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    token = (data or payload or {}).get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in token response: {payload!r}")
    return token


def fetch_all_batches(endpoint: str, token: str) -> tuple[list, bool]:
    """Page through a resource using the batch-number param until exhausted.

    Returns (records, truncated), where truncated is True if paging stopped
    because the duplicate-page guard fired rather than a clean 404/empty page.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    records: list = []
    seen_batches: set = set()

    for batch in range(1, MAX_BATCHES + 1):
        rows = _fetch_one_batch(endpoint, batch, headers)
        if not rows:
            return records, False  # 404 or empty page: past the last page of data

        # Guard against an API that keeps returning the same page. Compare the
        # full set of node_ids in the batch, not a truncated dump of just the
        # first record: after sort_keys=True, node_id sorts past the first
        # 300 characters, so two different stations that share brand,
        # amenities, and flags could produce an identical truncated signature
        # and falsely trigger this guard, cutting the pull short.
        batch_signature = frozenset(r.get("node_id") for r in rows)
        if batch_signature in seen_batches:
            print(
                f"WARNING: {endpoint} batch {batch} repeats an earlier batch, "
                f"stopping early with {len(records)} records collected so far.",
                file=sys.stderr,
            )
            return records, True
        seen_batches.add(batch_signature)

        records.extend(rows)
        time.sleep(REQUEST_GAP_SECONDS)

    return records, False


def _fetch_one_batch(endpoint: str, batch: int, headers: dict) -> list | None:
    """Fetch a single batch, retrying transient errors.

    Returns the list of records, or None to signal "stop paging" (a 404 or an
    empty page). A transient error (rate limit or temporary server fault) is
    retried with a growing wait; only if it persists past the retry budget does
    it raise and stop the run.
    """
    for attempt in range(1, MAX_TRANSIENT_RETRIES + 2):  # first try + retries
        resp = requests.get(
            f"{BASE_URL}{endpoint}",
            params={"batch-number": batch},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

        if resp.status_code == 404 and batch > 1:
            return None  # past the last page

        if resp.status_code in TRANSIENT_STATUS:
            if attempt > MAX_TRANSIENT_RETRIES:
                resp.raise_for_status()  # out of retries, let it fail loudly
            wait = float(resp.headers.get("Retry-After", RETRY_WAIT_SECONDS * attempt))
            print(
                f"  transient {resp.status_code} on batch {batch}, "
                f"retry {attempt}/{MAX_TRANSIENT_RETRIES} after {wait:.0f}s"
            )
            time.sleep(wait)
            continue  # retry the SAME batch

        resp.raise_for_status()
        rows = _records_from_payload(resp.json())
        return rows or None  # empty list also means stop paging

    return None


def _records_from_payload(payload) -> list:
    """Find the list of record dicts inside the API's response wrapper.

    The exact wrapper key is not officially documented, so this checks the
    common ones and then falls back to the first list-of-dicts it finds.
    After your first run, look at a raw snapshot and tighten this if needed.
    """
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("data", "results", "items", "records", "pfs", "fuel_prices"):
            value = payload.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
    return []


def save_snapshot(name: str, records: list, ts: datetime) -> Path:
    """Write a gzipped JSON snapshot, partitioned by UTC date."""
    day = ts.strftime("%Y-%m-%d")
    stamp = ts.strftime("%Y%m%dT%H%M%SZ")
    out_dir = DATA_DIR / name / day
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}_{stamp}.json.gz"
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        json.dump(
            {"pulled_at": ts.isoformat(), "count": len(records), "records": records},
            f,
        )
    return out_path


def main() -> int:
    client_id = os.environ.get("FUEL_FINDER_CLIENT_ID")
    client_secret = os.environ.get("FUEL_FINDER_CLIENT_SECRET")
    if not client_id or not client_secret:
        print(
            "Set FUEL_FINDER_CLIENT_ID and FUEL_FINDER_CLIENT_SECRET first.",
            file=sys.stderr,
        )
        return 1

    ts = datetime.now(timezone.utc)
    token = get_token(client_id, client_secret)
    print("Got access token.")

    exit_code = 0
    for name, endpoint in ENDPOINTS.items():
        records, truncated = fetch_all_batches(endpoint, token)
        if not records:
            print(f"WARNING: {name} returned no records", file=sys.stderr)
            exit_code = 2
            continue
        path = save_snapshot(name, records, ts)
        print(f"{name}: saved {len(records)} records -> {path}")
        if truncated:
            exit_code = max(exit_code, 3)


if __name__ == "__main__":
    raise SystemExit(main())