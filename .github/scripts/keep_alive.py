"""
Visit the deployed Streamlit app so Community Cloud registers traffic.

Community Cloud puts any app without traffic for 12 hours to sleep, and only
a real browser session counts as traffic (a plain HTTP GET does not, and
repo commits do not either, see streamlit/streamlit#10812). So this script
opens the app in headless Chromium, which establishes the websocket session
Streamlit counts as a visit, and waits for the app to actually render.

Community Cloud serves a host shell page and embeds the actual app in an
iframe (title "streamlitApp", src /~/+/), so every check searches ALL
frames, not just the top-level page.

If the app has gone to sleep, a "Yes, get this app back up!" button appears
instead; click it and wait for the app to boot. A cold boot reinstalls the
app's dependencies and can take several minutes, and the sleep page does not
always transition on its own, so the wait loop reloads the page
periodically within a long overall budget.

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

APP_URL = os.environ["APP_URL"]

# Stable marker for a rendered Streamlit app.
APP_RENDERED_SELECTOR = '[data-testid="stAppViewContainer"]'

# Loose match for the wake-up button on the hibernation page, in case
# Streamlit rewords it slightly.
WAKE_BUTTON_PATTERN = re.compile(r"get this app back up|wake", re.IGNORECASE)

# Overall budget for the app to render, covering a cold boot that has to
# reinstall dependencies (observed to exceed 5 minutes).
RENDER_BUDGET_S = 720
POLL_INTERVAL_S = 3
# Reload periodically while waiting: the sleep page does not always
# transition to the booted app on its own.
RELOAD_EVERY_S = 90

FAILURE_SCREENSHOT = "keep_alive_failure.png"
FAILURE_HTML = "keep_alive_failure.html"


def app_is_rendered(page) -> bool:
    """True if any frame contains a rendered Streamlit app container."""
    for frame in page.frames:
        try:
            if frame.locator(APP_RENDERED_SELECTOR).count() > 0:
                return True
        except Exception:
            continue  # frames can detach mid-check during reloads
    return False


def click_wake_button(page) -> bool:
    """Click the hibernation page's wake-up button in whichever frame has it."""
    for frame in page.frames:
        try:
            button = frame.get_by_role("button", name=WAKE_BUTTON_PATTERN)
            if button.count() > 0:
                button.first.click(timeout=5_000)
                return True
        except Exception:
            continue
    return False


def describe(page) -> str:
    """One line of page state for the log."""
    try:
        return f"title={page.title()!r} frames={len(page.frames)}"
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

            deadline = time.monotonic() + RENDER_BUDGET_S
            last_reload = time.monotonic()
            woke = False
            while not app_is_rendered(page):
                if not woke and click_wake_button(page):
                    woke = True
                    print("App was asleep: clicked the wake-up button.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"App did not render within {RENDER_BUDGET_S}s. "
                        f"{describe(page)}"
                    )
                if time.monotonic() - last_reload > RELOAD_EVERY_S:
                    print(
                        f"Not rendered yet ({remaining:.0f}s of budget left), "
                        f"reloading. {describe(page)}"
                    )
                    page.reload(wait_until="domcontentloaded", timeout=60_000)
                    last_reload = time.monotonic()
                page.wait_for_timeout(POLL_INTERVAL_S * 1_000)

            if not woke:
                print("App was already awake.")
            # Hold the websocket open briefly so the session registers as
            # traffic.
            page.wait_for_timeout(10_000)
            print(f"App rendered. Visit registered. {describe(page)}")
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
