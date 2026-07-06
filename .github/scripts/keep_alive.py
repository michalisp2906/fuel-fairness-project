"""
Visit the deployed Streamlit app so Community Cloud registers traffic.

Community Cloud puts any app without traffic for 12 hours to sleep, and only
a real browser session counts as traffic (a plain HTTP GET does not, and
repo commits do not either, see streamlit/streamlit#10812). So this script
opens the app in headless Chromium, which establishes the websocket session
Streamlit counts as a visit, and waits for the app to actually render.

If the app has gone to sleep, the page shows a "Yes, get this app back up!"
button instead; click it and wait for the app to boot. A cold boot
reinstalls the app's dependencies and can take several minutes, and the
sleep page does not always transition on its own, so the wait is a
reload-and-retry loop with a long overall budget.

Any failure exits nonzero (after saving a screenshot and page dump for the
workflow to upload as an artifact) so the run fails and GitHub sends a
notification email.

Run by .github/workflows/keep-app-awake.yml. Needs APP_URL in the
environment and playwright with Chromium installed.
"""
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

APP_URL = os.environ["APP_URL"]

# Stable marker for a rendered Streamlit app.
APP_RENDERED_SELECTOR = '[data-testid="stAppViewContainer"]'

# Loose match for the wake-up button on the hibernation page, in case
# Streamlit rewords it slightly.
WAKE_BUTTON_PATTERN = re.compile(r"get this app back up|wake", re.IGNORECASE)

# Overall budget for the app to render, covering a cold boot that has to
# reinstall dependencies. Between attempts the page is reloaded, because the
# sleep page does not always transition to the booted app by itself.
RENDER_BUDGET_S = 720
ATTEMPT_TIMEOUT_MS = 60_000

FAILURE_SCREENSHOT = "keep_alive_failure.png"
FAILURE_HTML = "keep_alive_failure.html"


def describe(page) -> str:
    """One line of page state for the log: title plus start of body text."""
    try:
        body = page.locator("body").inner_text(timeout=5_000)
        snippet = " ".join(body.split())[:200]
        return f"title={page.title()!r} body={snippet!r}"
    except Exception as exc:
        return f"(could not read page state: {exc})"


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            print(f"Visiting {APP_URL}")
            page.goto(APP_URL, wait_until="domcontentloaded", timeout=60_000)
            print(f"Landed: {describe(page)}")

            try:
                page.get_by_role("button", name=WAKE_BUTTON_PATTERN).click(
                    timeout=10_000
                )
                print("App was asleep: clicked the wake-up button.")
            except PlaywrightTimeout:
                print("No wake-up button found: app appears to be awake.")

            deadline = time.monotonic() + RENDER_BUDGET_S
            attempt = 0
            while True:
                attempt += 1
                try:
                    page.wait_for_selector(
                        APP_RENDERED_SELECTOR, timeout=ATTEMPT_TIMEOUT_MS
                    )
                    break
                except PlaywrightTimeout:
                    remaining = deadline - time.monotonic()
                    print(
                        f"Attempt {attempt}: not rendered yet "
                        f"({remaining:.0f}s of budget left). {describe(page)}"
                    )
                    if remaining <= 0:
                        raise
                    page.reload(wait_until="domcontentloaded", timeout=60_000)

            # Hold the websocket open briefly so the session registers as
            # traffic.
            page.wait_for_timeout(10_000)
            print(f"App rendered on attempt {attempt}. Visit registered.")
        except Exception:
            try:
                page.screenshot(path=FAILURE_SCREENSHOT, full_page=True)
                with open(FAILURE_HTML, "w", encoding="utf-8") as f:
                    f.write(page.content())
                print(f"Saved {FAILURE_SCREENSHOT} and {FAILURE_HTML}")
            except Exception as dump_exc:
                print(f"Could not save failure dump: {dump_exc}")
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Keep-alive visit FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
