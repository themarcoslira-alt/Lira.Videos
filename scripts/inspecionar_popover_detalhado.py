import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

p = sync_playwright().start()
b = p.chromium.connect_over_cdp('http://localhost:9222')
page = [pg for c in b.contexts for pg in c.pages if 'flow' in pg.url.lower()][0]

# Garantir que o popover de ingredientes está aberto
page.keyboard.press("Escape")
page.wait_for_timeout(300)

btn = page.locator('button.add-menu-trigger, button[aria-label*="Adicionar elementos" i]').first
if btn.is_visible():
    btn.click()
    page.wait_for_timeout(1000)

popover = page.locator('flow-add-menu-popover-content').first
print("Popover visivel:", popover.is_visible())

# 1. Inspecionar dropdown de projeto no topo do popover
print("\n--- DROPDOWN NO TOPO DO POPOVER ---")
project_btn = popover.locator('button:has-text("set. 21"), [class*="project-select"], [class*="dropdown"]').first
if project_btn.is_visible():
    print("Botão dropdown encontrado:", project_btn.inner_text())
    project_btn.click()
    page.wait_for_timeout(500)
    page.screenshot(path='docs/screenshots/popover_dropdown_opened.png')
    # Listar opções do dropdown
    opts = page.locator('div.cdk-overlay-pane [role="menuitem"], div.cdk-overlay-pane [role="option"], div.cdk-overlay-pane button').all()
    print(f"Opções no dropdown ({len(opts)}):")
    for o in opts:
        print("  -", o.inner_text().strip().replace("\n", " "))
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

# 2. Testar cada aba do popover
print("\n--- TESTANDO ABAS DO POPOVER ---")
tabs = popover.locator('mat-list-item, [role="tab"], .tab-item').all()
for i, tab in enumerate(tabs):
    t_text = tab.inner_text().strip().replace("\n", " ")
    print(f"\nClicando na aba [{i}]: {t_text}")
    tab.click()
    page.wait_for_timeout(1000)
    
    # Inspecionar itens exibidos
    items = popover.locator('button.asset-item, .asset-item, [role="listitem"], .asset-title, [class*="item"]').all()
    item_texts = []
    for it in items:
        txt = it.inner_text().strip().replace("\n", " ")
        if txt and len(txt) < 80:
            item_texts.append(txt)
    print(f"  Itens encontrados ({len(item_texts)}): {item_texts[:5]}")
    
    # Screenshot de Personagens e Avatares
    if "personagen" in t_text.lower():
        page.screenshot(path='docs/screenshots/popover_tab_personagens.png')
    elif "avatar" in t_text.lower():
        page.screenshot(path='docs/screenshots/popover_tab_avatares.png')
    elif "tudo" in t_text.lower():
        page.screenshot(path='docs/screenshots/popover_tab_tudo.png')

# 3. Testar busca por 'Marcos' no input
print("\n--- TESTANDO BUSCA POR 'Marcos' ---")
search_in = popover.locator('input.search-input').first
if search_in.is_visible():
    search_in.fill("Marcos")
    page.wait_for_timeout(1500)
    page.screenshot(path='docs/screenshots/popover_search_marcos.png')
    items_search = popover.locator('button.asset-item, .asset-item, [role="listitem"], .asset-title').all()
    print(f"Itens encontrados com busca 'Marcos': {len(items_search)}")
    for it in items_search:
        print("  -", it.inner_text().strip().replace("\n", " "))
