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

        # Inspeciona os botões com settings
        elements = page.evaluate("""() => {
            const all = Array.from(document.querySelectorAll('button, a, [role="button"]'));
            return all.filter(el => {
                const txt = (el.innerText || '').toLowerCase();
                const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                const cls = (el.className || '').toLowerCase();
                return txt.includes('setting') || aria.includes('setting') || cls.includes('setting') || aria.includes('config');
            }).map(el => ({
                tag: el.tagName,
                text: (el.innerText || '').trim().replace(/\\n/g, ' '),
                ariaLabel: el.getAttribute('aria-label'),
                className: el.className,
                rect: el.getBoundingClientRect(),
                visible: el.offsetParent !== null,
                outerHTML: el.outerHTML
            }));
        }""")
        print(f"Elementos de configurações ({len(elements)}):")
        for el in elements:
            print(f"  * text='{el['text']}' aria='{el['ariaLabel']}' class='{el['className']}' visible={el['visible']} rect=({el['rect']['x']}, {el['rect']['y']})")

if __name__ == "__main__":
    main()
