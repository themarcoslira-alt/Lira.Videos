import sys
from playwright.sync_api import sync_playwright

p = sync_playwright().start()
b = p.chromium.connect_over_cdp('http://localhost:9222')
page = [pg for c in b.contexts for pg in c.pages if 'flow' in pg.url.lower()][0]

popover = page.locator('flow-add-menu-popover-content').first
if not popover.is_visible():
    btn = page.locator('button.add-menu-trigger, button[aria-label*="Adicionar elementos" i]').first
    btn.click()
    page.wait_for_timeout(1000)

print("Popover visivel:", popover.is_visible())

# Inspecionar todas as abas
tabs = popover.locator('mat-list-item').all()
print(f"Abas encontradas: {len(tabs)}")
for i, t in enumerate(tabs):
    t_text = t.inner_text().strip().replace('\n', ' ')
    t.click()
    page.wait_for_timeout(1000)
    
    # Itens visíveis
    items = popover.locator('button.asset-item, .asset-item, [role="listitem"]').all()
    print(f"\nAba [{i}] '{t_text}' -> {len(items)} itens:")
    for it in items:
        txt = it.inner_text().strip().replace('\n', ' ')
        aria = it.get_attribute('aria-label') or ''
        tag = it.evaluate('e => e.tagName')
        cl = it.get_attribute('class') or ''
        print(f"   [{tag}] [{cl}] aria='{aria}' txt='{txt}'")
    
    # Se tiver empty state
    empty = popover.locator('text="Nenhum recurso encontrado."').first
    if empty.is_visible():
        print(f"   -> [EMPTY STATE] Nenhum recurso encontrado.")

page.screenshot(path='docs/screenshots/popover_tabs_walkthrough.png')
