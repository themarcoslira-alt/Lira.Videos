from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    pg = browser.contexts[0].pages[1]
    panel = pg.locator("flow-prompt-box-settings").first
    if not panel.is_visible():
        pg.locator("button.settings-trigger-button").first.click(force=True)
        pg.wait_for_timeout(400)

    tab_loc = pg.locator('flow-prompt-box-settings button[role="radio"]:has-text("Imagem")').first
    print("Clicking tab_loc...")
    tab_loc.click(force=True)
    pg.wait_for_timeout(350)
    print("aria-checked after click:", tab_loc.get_attribute("aria-checked"))

    dd_trigger = pg.locator(
        'flow-prompt-box-settings button[aria-label="Selecionar família de modelos"], '
        'button[aria-label="Selecionar família de modelos"], '
        'button:has(.model-select-trigger-content)'
    ).first
    print("dd_trigger count:", dd_trigger.count(), "vis:", dd_trigger.is_visible())
    if dd_trigger.is_visible():
        print("dd_trigger text:", repr(dd_trigger.inner_text()))
        print("Clicking dd_trigger...")
        dd_trigger.click(force=True)
        pg.wait_for_timeout(400)
        items = pg.locator('.cdk-overlay-pane [role="menuitem"]').all()
        print("Dropdown items:", [it.inner_text() for it in items])
        pg.keyboard.press("Escape")

    pg.keyboard.press("Escape")
    pg.wait_for_timeout(200)
    dock = pg.locator("button.settings-trigger-button").first
    print("Dock after escape:", repr(dock.inner_text()))
