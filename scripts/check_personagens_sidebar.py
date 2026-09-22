import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

p = sync_playwright().start()
b = p.chromium.connect_over_cdp('http://localhost:9222')
page = [pg for c in b.contexts for pg in c.pages if 'flow' in pg.url.lower()][0]

from services.playwright_flow import _clicar_item_personagens_flow
_clicar_item_personagens_flow(page)
page.wait_for_timeout(1500)

tiles = page.locator('flow-custom-tile, div.character-tile-container, [class*="character-tile"]').all()
print(f"Total tiles: {len(tiles)}")
for t in tiles:
    print("Tile:", t.inner_text().strip().replace('\n', ' '))

page.screenshot(path='docs/screenshots/current_personagens_sidebar.png')
print("Screenshot salvo em docs/screenshots/current_personagens_sidebar.png")
