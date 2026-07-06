"""
Visit the deployed Streamlit app so Community Cloud registers traffic.

Community Cloud puts any app without traffic for 12 hours to sleep, and only
a real browser session counts as traffic (a plain HTTP GET does not, and
repo commits do not either, see streamlit/streamlit#10812). So this script
opens the app in headless Chromium, which establishes the websocket session
Streamlit counts as a visit, and waits for the app to actually render.

If the app has already gone to sleep (e.g. this workflow was down), the page
shows a "Yes, get this app back up!" button instead; click it and wait for
the app to boot.

Any failure exits nonzero so the workflow fails and GitHub sends a
notification email.

Run by .github/workflows/keep-app-awake.yml. Needs APP_URL in the
environment and playwright with Chromium installed.
"""
import os
import re
import sys

from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

APP_URL = os.environ["APP_URL"]

# Stable marker for a rendered Streamlit app.
APP_RENDERED_SELECTOR = '[data-testid="stAppViewContainer"]'

# Loose match for the wake-up button on the hibernation page, in case
# Streamlit rewords it slightly.
WAKE_BUTTON_PATTERN = re.compile(r"get this app back up|wake", re.IGNORECASE)

# A woken app has to pull its container and boot, which can take minutes.
RENDER_TIMEOUT_MS = 300_000


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        print(f"Visiting {APP_URL}")
        page.goto(APP_URL, wait_until="domcontentloaded", timeout=60_000)

        try:
            page.get_by_role("button", name=WAKE_BUTTON_PATTERN).click(
                timeout=10_000
            )
            print("App was asleep: clicked the wake-up button.")
        except PlaywrightTimeout:
            print("No wake-up button found: app appears to be awake.")

        page.wait_for_selector(APP_RENDERED_SELECTOR, timeout=RENDER_TIMEOUT_MS)
        # Hold the websocket open briefly so the session registers as traffic.
        page.wait_for_timeout(10_000)
        print("App rendered. Visit registered.")
        browser.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Keep-alive visit FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
