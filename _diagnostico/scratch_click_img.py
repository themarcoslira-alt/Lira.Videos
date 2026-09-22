from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    pg = browser.contexts[0].pages[1]
    panel = pg.locator("flow-prompt-box-settings").first
    if not panel.is_visible():
        pg.locator("button.settings-trigger-button").first.click(force=True)
        pg.wait_for_timeout(400)

    img_btn = pg.locator("flow-prompt-box-settings button").filter(has_text="Imagem").first
    print("img_btn exists:", img_btn.count())
    print("img_btn aria-checked before:", img_btn.get_attribute("aria-checked"))
    print("img_btn HTML:", img_btn.evaluate("el => el.outerHTML"))
    img_btn.click(force=True)
    pg.wait_for_timeout(500)
    print("img_btn aria-checked after:", img_btn.get_attribute("aria-checked"))
    dock = pg.locator("button.settings-trigger-button").first
    print("dock text:", repr(dock.inner_text()))
