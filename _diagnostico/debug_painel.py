import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')
DIAG_DIR = Path(r"C:\Lira Videos\_diagnostico")

def main():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://[::1]:9222")
        context = browser.contexts[0]
        page = [pg for pg in context.pages if "/project/" in pg.url][0]
        print(f"Página: {page.url}")

        # Screenshot do estado atual
        page.screenshot(path=str(DIAG_DIR / "debug_current_state.png"))

        # Inspeciona botão de configurações
        btn_cfg = page.locator("button.settings-trigger-button, button[aria-label*='configurações' i]").first
        print(f"Botão cfg visível: {btn_cfg.is_visible()}")
        print(f"Texto no botão cfg: '{btn_cfg.inner_text().strip().replace(chr(10), ' ')}'")

        # Inspeciona se painel está aberto
        panel = page.locator("flow-prompt-box-settings, .settings-content-overlay").first
        print(f"Painel aberto antes do clique: {panel.is_visible()}")

        if not panel.is_visible():
            print("Clicando no botão de configurações...")
            btn_cfg.click()
            page.wait_for_timeout(1000)
            print(f"Painel aberto após clique: {panel.is_visible()}")
        
        # Se aberto, lista todos os botões do painel
        if panel.is_visible():
            btns = panel.locator("button").all()
            print(f"Total botões no painel: {len(btns)}")
            for b in btns:
                txt = b.inner_text().strip().replace("\n", " ")
                role = b.get_attribute("role")
                checked = b.get_attribute("aria-checked")
                label = b.get_attribute("aria-label")
                print(f"  btn: txt='{txt}' role='{role}' checked='{checked}' label='{label}'")

if __name__ == "__main__":
    main()
