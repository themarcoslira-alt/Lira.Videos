import sys
from playwright.sync_api import sync_playwright

p = sync_playwright().start()
b = p.chromium.connect_over_cdp('http://localhost:9222')
page = [pg for c in b.contexts for pg in c.pages if 'flow' in pg.url.lower()][0]

# Fechar popover se estiver aberto
page.keyboard.press("Escape")
page.wait_for_timeout(300)

tile3 = page.locator('div.character-tile-container, [class*="character-tile"]').first
print('Tile 3 exists:', tile3.is_visible())
print('Tile 3 text:', tile3.inner_text().strip().replace('\n', ' '))
print('Tile 3 HTML:', tile3.inner_html()[:400])

# Clicar no Tile 3 para ver o que abre
tile3.click()
page.wait_for_timeout(1500)
page.screenshot(path='docs/screenshots/tile3_clicked.png')
print('URL apos clicar:', page.url)
