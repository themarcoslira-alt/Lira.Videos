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

        chip = page.locator('button.chip-container, .chip-container').first
        print(f"Chip HTML: {chip.evaluate('el => el.outerHTML')}")
        print(f"Chip classes: {chip.get_attribute('class')}")
        print(f"Chip innerText: {chip.inner_text()}")

        # Verifica se há mensagem de erro ou tooltip
        chip.hover()
        page.wait_for_timeout(500)
        page.screenshot(path=str(DIAG_DIR / "1_2_chip_error_hover.png"))
        
        # Verifica toasts ou mensagens
        msgs = page.evaluate("""() => {
            const all = Array.from(document.querySelectorAll('.mat-mdc-tooltip-surface, [role="tooltip"], .cdk-overlay-pane, .error-message'));
            return all.map(el => el.innerText.trim()).filter(Boolean);
        }""")
        print(f"Tooltips/Mensagens: {msgs}")

if __name__ == "__main__":
    main()
