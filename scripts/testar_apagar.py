import sys
from playwright.sync_api import sync_playwright

p = sync_playwright().start()
b = p.chromium.connect_over_cdp('http://localhost:9222')
page = [pg for c in b.contexts for pg in c.pages if 'flow' in pg.url.lower()][0]

btn_apagar = page.locator('button[aria-label*="Apagar comando" i], button.clear-button').first
print('Apagar visivel:', btn_apagar.is_visible())
if btn_apagar.is_visible():
    btn_apagar.click()
    page.wait_for_timeout(300)
    print('Clicou apagar!')
print('Apagar visivel agora:', btn_apagar.is_visible())
