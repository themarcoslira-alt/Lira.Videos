import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    try:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    except Exception:
        browser = p.chromium.connect_over_cdp("http://[::1]:9222")

    pg = None
    for page in browser.contexts[0].pages:
        if "flow.google.com" in page.url:
            pg = page
            break

    dock_btn = pg.locator('button.settings-trigger-button, button[aria-label="Gatilho de configurações"]').first
    sp = pg.locator("flow-prompt-box-settings")

    def ensure_settings_open():
        for attempt in range(3):
            if sp.count() > 0 and sp.first.is_visible():
                return True
            dock_btn.click()
            pg.wait_for_timeout(300)
        return sp.count() > 0 and sp.first.is_visible()

    def ensure_settings_closed():
        for attempt in range(3):
            if sp.count() == 0:
                return True
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(200)
        return sp.count() == 0

    print("Initial sp count:", sp.count())
    print("Closing...")
    ensure_settings_closed()
    print("Closed? sp count =", sp.count())
    print("Opening...")
    ok = ensure_settings_open()
    print("Opened? ok =", ok, "sp count =", sp.count())
    print("Closing...")
    ensure_settings_closed()
    print("Closed? sp count =", sp.count())
