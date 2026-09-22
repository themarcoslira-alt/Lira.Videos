import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

p = sync_playwright().start()
b = p.chromium.connect_over_cdp('http://localhost:9222')
page = [pg for c in b.contexts for pg in c.pages if 'flow' in pg.url.lower()][0]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.playwright_flow import PlaywrightCDPWorker
worker = PlaywrightCDPWorker.__new__(PlaywrightCDPWorker)
worker.page = page
worker.current_project_id = 'WHY YOUR TOMATO PLANTS WONT PRODUCE - AND ITS NOT WHAT YOU THINK'

nome_limpo = "Marcos"

# 1. Limpa overlays
worker._limpar_overlays_e_backdrops()

# 2. Localiza editor
editor = page.locator('div.ProseMirror[contenteditable="true"]').first
print("Editor visivel:", editor.is_visible())

# 3. Localiza btn_mais
btn_mais = page.locator('button.add-menu-trigger, button[aria-label*="Adicionar elementos" i]').first
print("Btn mais visivel:", btn_mais.is_visible())
btn_mais.click()
page.wait_for_timeout(600)

# 4. Localiza dialog
dialog = page.locator('flow-add-menu-popover-content').first
print("Dialog visivel:", dialog.is_visible())

# 5. Clica na aba Personagens
tab_pers = dialog.locator('mat-list-item:has-text("Personagens")').first
print("Tab Personagens visivel:", tab_pers.is_visible())
tab_pers.click()
page.wait_for_timeout(800)

page.screenshot(path='docs/screenshots/debug_verificar_tab_personagens.png')

# 6. Executa _aguardar_lista_personagens_estavel
t0 = time.time()
status_lista = worker._aguardar_lista_personagens_estavel(dialog, nome_limpo)
dt = int((time.time() - t0) * 1000)
print(f"Status retornado por _aguardar_lista_personagens_estavel: '{status_lista}' em {dt}ms")

# Inspeciona o que está no DOM do dialog
print("\n--- TEXTOS NO DIALOG ---")
texts = dialog.locator('button.asset-item, .asset-item, [role="listitem"]').all()
print(f"Total itens no dialog: {len(texts)}")
for it in texts:
    print("  Item text:", it.inner_text().strip().replace('\n', ' '))

page.keyboard.press("Escape")
