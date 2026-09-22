import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

p = sync_playwright().start()
b = p.chromium.connect_over_cdp('http://localhost:9222')
page = [pg for c in b.contexts for pg in c.pages if 'flow' in pg.url.lower()][0]

elements = page.locator('*:has-text("@Marcos"), *:has-text("Marcos")').all()
print(f'Elementos com Marcos: {len(elements)}')
for el in elements:
    try:
        tag = el.evaluate('e => e.tagName')
        cl = el.get_attribute('class') or ''
        txt = el.inner_text().strip().replace('\n', ' ')
        outer = el.evaluate('e => e.outerHTML[:200]')
        if len(txt) < 80:
            print(f'  [{tag}] [{cl}] text="{txt}" outer="{outer}"')
    except Exception:
        pass

# Também vamos inspecionar o container de personagens
print("\n--- CARDS NA TELA DE PERSONAGENS ---")
cards = page.locator('flow-custom-tile, [class*="character"], [class*="tile"], [class*="card"]').all()
print(f'Total cards/tiles: {len(cards)}')
for c in cards:
    try:
        txt = c.inner_text().strip().replace('\n', ' ')
        cl = c.get_attribute('class') or ''
        tag = c.evaluate('e => e.tagName')
        if txt and len(txt) < 100:
            print(f'  Card [{tag}] [{cl}]: "{txt}"')
    except Exception:
        pass
