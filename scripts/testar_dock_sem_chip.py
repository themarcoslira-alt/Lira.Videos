import sys
from playwright.sync_api import sync_playwright

p = sync_playwright().start()
b = p.chromium.connect_over_cdp('http://localhost:9222')
page = [pg for c in b.contexts for pg in c.pages if 'flow' in pg.url.lower()][0]

print("--- REMOVENDO O CHIP DO PROMPT ---")
clear_btn = page.locator('button[aria-label*="Apagar comando" i], button.clear-button').first
if clear_btn.is_visible():
    clear_btn.click()
    page.wait_for_timeout(500)
    print("Clear button clicado!")

# Se o chip ainda existir, clica nele diretamente
chip = page.locator('button.chip-container, button[aria-label="Elemento"]').first
if chip.is_visible():
    chip.click()
    page.wait_for_timeout(500)
    print("Chip clicado para remover!")

print("Chip visível agora:", chip.is_visible())

# Agora reabrir o popover
add_btn = page.locator('button.add-menu-trigger').first
print("Add button visivel:", add_btn.is_visible())
add_btn.click()
page.wait_for_timeout(1000)

popover = page.locator('flow-add-menu-popover-content').first
print("Popover visivel:", popover.is_visible())

# Inspecionar aba Personagens
tab_p = popover.locator('mat-list-item:has-text("Personagens")').first
print("Aba Personagens visivel:", tab_p.is_visible())
tab_p.click()
page.wait_for_timeout(1000)

items = popover.locator('button.asset-item, .asset-item, [role="listitem"]').all()
print(f"Itens na aba Personagens SEM o chip no prompt: {len(items)}")
for it in items:
    print("  Item:", it.inner_text().strip().replace('\n', ' '))

page.keyboard.press("Escape")
