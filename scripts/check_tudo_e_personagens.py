import sys
from playwright.sync_api import sync_playwright

p = sync_playwright().start()
b = p.chromium.connect_over_cdp('http://localhost:9222')
page = [pg for c in b.contexts for pg in c.pages if 'flow' in pg.url.lower()][0]

page.keyboard.press("Escape")
page.wait_for_timeout(300)

btn_mais = page.locator('button.add-menu-trigger').first
btn_mais.click()
page.wait_for_timeout(1000)

popover = page.locator('flow-add-menu-popover-content').first
tabs = popover.locator('mat-list-item').all()

# Clicar em Tudo
tabs[0].click()
page.wait_for_timeout(1000)
items_tudo = popover.locator('button.asset-item, .asset-item').all()
print(f"Itens em Tudo ({len(items_tudo)}):")
for it in items_tudo:
    print("  Tudo:", it.inner_text().strip().replace('\n', ' '))

# Clicar em Personagens
tabs[4].click()
page.wait_for_timeout(1000)
items_pers = popover.locator('button.asset-item, .asset-item').all()
print(f"\nItens em Personagens ({len(items_pers)}):")
for it in items_pers:
    print("  Personagens:", it.inner_text().strip().replace('\n', ' '))

page.keyboard.press("Escape")
