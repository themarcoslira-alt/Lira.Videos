import time
from pathlib import Path
from playwright.sync_api import sync_playwright

DIAG_DIR = Path(r"C:\Lira Videos\_diagnostico")

def main():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://[::1]:9222")
        context = browser.contexts[0]
        page = context.pages[0]
        print(f"URL: {page.url}")
        print(f"Title: {page.title()}")

        # Verifica botões na página
        buttons = page.locator("button, a").all()
        print(f"Total botões/links: {len(buttons)}")
        for b in buttons[:20]:
            try:
                txt = (b.inner_text() or "").strip().replace("\n", " ")
                href = b.get_attribute("href") or ""
                if txt or href:
                    print(f"  Element: txt='{txt[:40]}' href='{href}' role='{b.get_attribute('role')}'")
            except Exception:
                pass

        # Se houver botão de entrar ou lançar Flow:
        launch_btn = page.locator("button:has-text('Iniciar'), a:has-text('Iniciar'), button:has-text('Launch'), a:has-text('Launch'), button:has-text('Criar'), a:has-text('Criar'), button:has-text('Acessar'), a:has-text('Acessar')").first
        if launch_btn.is_visible(timeout=2000):
            print(f"Clicando em botão de acesso: {launch_btn.inner_text()}")
            launch_btn.click()
            page.wait_for_timeout(4000)
            print(f"Nova URL: {page.url}")
            page.screenshot(path=str(DIAG_DIR / "02_after_launch_click.png"))
        else:
            # Tenta ir direto para um projeto existente ou galeria
            print("Tentando ir para https://labs.google/fx/tools/flow/project/981ee33a-ea18-49cc-8848-849103c671ac...")
            page.goto("https://labs.google/fx/tools/flow/project/981ee33a-ea18-49cc-8848-849103c671ac", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            print(f"URL após abrir projeto: {page.url}")
            print(f"Título: {page.title()}")
            page.screenshot(path=str(DIAG_DIR / "03_project_page_conta03.png"))

if __name__ == "__main__":
    main()
