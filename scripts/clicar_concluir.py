import sys
from playwright.sync_api import sync_playwright

p = sync_playwright().start()
b = p.chromium.connect_over_cdp('http://localhost:9222')
page = [pg for c in b.contexts for pg in c.pages if 'flow' in pg.url.lower()][0]

print('URL antes:', page.url)
btn_concluir = page.locator('button:has-text("Concluir"), button:has-text("Done")').first
print('Botao concluir visivel:', btn_concluir.is_visible())
if btn_concluir.is_visible():
    btn_concluir.click()
    page.wait_for_timeout(3000)

print('URL depois:', page.url)
page.screenshot(path='docs/screenshots/after_concluir_marcos.png')
print('Screenshot salvo em docs/screenshots/after_concluir_marcos.png!')
