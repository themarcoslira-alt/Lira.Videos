import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

print("[START] Conectando ao CDP...", flush=True)
p = sync_playwright().start()
b = p.chromium.connect_over_cdp('http://localhost:9222')
page = [pg for c in b.contexts for pg in c.pages if 'flow' in pg.url.lower()][0]

print(f"[CDP] URL: {page.url}", flush=True)

# Screenshot inicial
Path("docs/screenshots").mkdir(parents=True, exist_ok=True)
page.screenshot(path='docs/screenshots/flow_initial_state.png')
print("[STEP] Screenshot inicial salvo em docs/screenshots/flow_initial_state.png", flush=True)

# Verificar se popover está visível
popover = page.locator('flow-add-menu-popover-content, div.cdk-overlay-pane:has(flow-add-menu-popover-content)').first
is_open = False
try:
    is_open = popover.is_visible(timeout=1000)
except Exception:
    pass

print(f"[STEP] Popover já visível: {is_open}", flush=True)

if not is_open:
    btn = page.locator('button.add-menu-trigger, button[aria-label*="Adicionar elementos" i]').first
    btn_vis = False
    try:
        btn_vis = btn.is_visible(timeout=1500)
    except Exception:
        pass
    print(f"[STEP] Botão '+' visível: {btn_vis}", flush=True)
    if btn_vis:
        try:
            btn.click(timeout=3000)
            page.wait_for_timeout(1000)
        except Exception as e:
            print(f"[WARN] Erro ao clicar no botão: {e}", flush=True)

try:
    is_open = popover.is_visible(timeout=2000)
except Exception:
    pass
print(f"[STEP] Popover visível após tentativa de clique: {is_open}", flush=True)

# Inspecionar tabs
tabs = page.locator('flow-add-menu-popover-content mat-list-item, flow-add-menu-popover-content [role="tab"], flow-add-menu-popover-content .tab-item').all()
print(f'[STEP] Encontradas {len(tabs)} abas/itens:', flush=True)
for i, tab in enumerate(tabs):
    txt = tab.inner_text().strip().replace('\n', ' ')
    aria = tab.get_attribute('aria-label') or ''
    print(f'  Aba {i}: txt="{txt}" aria="{aria}"', flush=True)
    if 'personagen' in txt.lower() or 'character' in txt.lower() or 'personagen' in aria.lower():
        print(f'  -> Clicando na aba de Personagens: {txt}', flush=True)
        try:
            tab.click(timeout=3000)
            page.wait_for_timeout(1500)
        except Exception as e:
            print(f'  [WARN] Erro ao clicar na aba: {e}', flush=True)

# Inspecionar itens no popover
items = page.locator('flow-add-menu-popover-content button, flow-add-menu-popover-content .asset-item, flow-add-menu-popover-content [role="listitem"]').all()
print(f'\n[STEP] Total de botões/itens no popover: {len(items)}', flush=True)
for i, it in enumerate(items):
    try:
        txt = it.inner_text().strip().replace('\n', ' ')
        aria = it.get_attribute('aria-label') or ''
        cl = it.get_attribute('class') or ''
        tag = it.evaluate("el => el.tagName")
        print(f'  Item {i} [{tag}] [{cl}] aria="{aria}" text="{txt}"', flush=True)
    except Exception:
        pass

# Input de busca
search_inputs = page.locator('flow-add-menu-popover-content input').all()
print(f'\n[STEP] Inputs de busca no popover: {len(search_inputs)}', flush=True)
for i, inp in enumerate(search_inputs):
    try:
        ph = inp.get_attribute('placeholder') or ''
        cl = inp.get_attribute('class') or ''
        print(f'  Input {i}: placeholder="{ph}" class="{cl}"', flush=True)
    except Exception:
        pass

# Screenshot final
page.screenshot(path='docs/screenshots/flow_characters_tab_inspection.png')
print('\n[DONE] Screenshot final salvo em docs/screenshots/flow_characters_tab_inspection.png', flush=True)
b.close()
p.stop()
