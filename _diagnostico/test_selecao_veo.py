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

        btn = page.locator('button[aria-label="Gatilho de configurações"]').first
        print(f"Botão encontrado: text='{btn.inner_text().strip().replace(chr(10), ' ')}'")
        btn.click()
        page.wait_for_timeout(800)

        panel = page.locator('flow-prompt-box-settings, .settings-content-overlay').first
        print(f"Painel visível após clique: {panel.is_visible()}")
        page.screenshot(path=str(DIAG_DIR / "debug_painel_aberto_correto.png"))

        # Inspeciona dropdown de modelo
        model_btn = page.locator('flow-prompt-box-settings button[aria-label="Selecionar família de modelos"]').first
        print(f"Dropdown de modelo visível: {model_btn.is_visible()} | texto: '{model_btn.inner_text().strip().replace(chr(10), ' ')}'")

        # Clica no dropdown de modelo
        model_btn.click()
        page.wait_for_timeout(600)
        page.screenshot(path=str(DIAG_DIR / "debug_dropdown_modelo_aberto.png"))

        # Seleciona Veo 3.1 - Lite
        veo_opt = page.locator('.cdk-overlay-pane [role="menuitem"]:has-text("Veo 3.1 - Lite")').first
        print(f"Veo 3.1 - Lite visível: {veo_opt.is_visible()}")
        if veo_opt.is_visible():
            veo_opt.click()
            page.wait_for_timeout(500)
            print(f"Texto do modelo após seleção: '{model_btn.inner_text().strip().replace(chr(10), ' ')}'")

        # Fecha painel
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        print(f"Resumo final do botão: '{btn.inner_text().strip().replace(chr(10), ' ')}'")

if __name__ == "__main__":
    main()
