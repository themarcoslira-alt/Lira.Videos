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

        # Inspeciona todos os botões dentro do dock do prompt
        box_buttons = page.evaluate("""() => {
            const promptBox = document.querySelector('flow-prompt-box, .prompt-box, flow-rich-text-editor, .prompt-dock') 
                || document.querySelector('.dock') || document.body;
            const btns = Array.from(promptBox.querySelectorAll('button'));
            return btns.map(b => ({
                text: (b.innerText || '').trim().replace(/\\n/g, ' '),
                ariaLabel: b.getAttribute('aria-label'),
                className: b.className,
                rect: b.getBoundingClientRect(),
                outerHTML: b.outerHTML
            }));
        }""")
        print(f"Botões encontrados na área do prompt box ({len(box_buttons)}):")
        for b in box_buttons:
            print(f"  * text='{b['text']}' aria='{b['ariaLabel']}' class='{b['className']}' rect=({b['rect']['x']}, {b['rect']['y']})")
        
        # Também verifica se pressionar Enter no editor dispara a geração
        editor = page.locator('flow-rich-text-editor div.ProseMirror').first
        print(f"Editor texto atual: '{editor.inner_text().strip()}'")

if __name__ == "__main__":
    main()
