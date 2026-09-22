import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

DIAG_DIR = Path(r"C:\Lira Videos\_diagnostico")

def main():
    with sync_playwright() as p:
        print("Conectando ao Chrome CDP em [::1]:9222...")
        browser = p.chromium.connect_over_cdp("http://[::1]:9222")
        contexts = browser.contexts
        print(f"Contextos encontrados: {len(contexts)}")
        context = contexts[0] if contexts else browser.new_context()
        print(f"Páginas no contexto: {len(context.pages)}")
        for i, page in enumerate(context.pages):
            print(f"  Página {i}: {page.url} | {page.title()}")

        # Procura aba do Flow existente ou abre nova
        flow_page = None
        for page in context.pages:
            if "flow" in page.url.lower() or "labs.google" in page.url.lower():
                flow_page = page
                break
        
        if not flow_page:
            print("Nenhuma aba do Flow aberta. Abrindo nova aba para labs.google/fx/tools/flow...")
            flow_page = context.new_page()
            flow_page.goto("https://labs.google/fx/tools/flow", wait_until="domcontentloaded", timeout=30000)
        else:
            print(f"Aba do Flow já aberta: {flow_page.url}")

        flow_page.wait_for_timeout(3000)
        print(f"URL final da página: {flow_page.url}")
        print(f"Título: {flow_page.title()}")
        
        flow_page.screenshot(path=str(DIAG_DIR / "01_flow_initial_conta03.png"))
        print("Screenshot salvo: 01_flow_initial_conta03.png")

if __name__ == "__main__":
    main()
