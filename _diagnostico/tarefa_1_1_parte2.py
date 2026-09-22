import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

# Força UTF-8 no stdout
sys.stdout.reconfigure(encoding='utf-8')

DIAG_DIR = Path(r"C:\Lira Videos\_diagnostico")

def main():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://[::1]:9222")
        context = browser.contexts[0]
        page = [pg for pg in context.pages if "/project/" in pg.url][0]

        print(f"Página conectada: {page.url}")

        # =====================================================================
        # 1. Abre painel de configurações se não estiver aberto
        # =====================================================================
        panel = page.locator("flow-prompt-box-settings, .settings-content-overlay").first
        if not panel.is_visible(timeout=500):
            print("Abrindo painel de configurações...")
            page.locator("button.settings-trigger-button").first.click()
            page.wait_for_timeout(800)

        # =====================================================================
        # 2. Clica no dropdown 'Selecionar família de modelos' DENTRO do painel
        # =====================================================================
        model_trigger = page.locator('flow-prompt-box-settings button[aria-label="Selecionar família de modelos"]').first
        print(f"Gatilho de modelo visível: {model_trigger.is_visible()}")
        print(f"Texto atual do gatilho: '{model_trigger.inner_text().strip().replace(chr(10), ' ')}'")
        model_trigger.click()
        page.wait_for_timeout(1000)
        page.screenshot(path=str(DIAG_DIR / "1_1_dropdown_modelos_video_aberto.png"))

        # Captura todos os itens de modelo no menu aberto
        overlay_menu = page.locator(".cdk-overlay-pane:has([role='menu'])").last
        models_html = overlay_menu.evaluate("el => el.outerHTML") if overlay_menu.count() > 0 else ""
        (DIAG_DIR / "1_1_dropdown_modelos.html").write_text(models_html, encoding="utf-8")

        model_items = page.evaluate("""() => {
            const items = Array.from(document.querySelectorAll('.cdk-overlay-pane [role="menuitem"], .cdk-overlay-pane button'));
            return items.map(el => ({
                text: (el.innerText || '').trim().replace(/\\n/g, ' - '),
                ariaLabel: el.getAttribute('aria-label'),
                role: el.getAttribute('role'),
                disabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
                classes: el.className,
                outerHTML: el.outerHTML
            }));
        }""")
        print(f"\nOpções de modelos de vídeo disponíveis ({len(model_items)}):")
        for m in model_items:
            print(f"  * '{m['text']}' | disabled={m['disabled']} | role={m['role']}")
        (DIAG_DIR / "1_1_modelos_video_lista.json").write_text(json.dumps(model_items, indent=2, ensure_ascii=False), encoding="utf-8")

        # Fecha menu de modelos
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        # Fecha painel de configurações
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        # =====================================================================
        # 3. Menu do Tile da Imagem (Hover + ⋮ e Clique Direito)
        # =====================================================================
        print("\n--- Inspecionando Menu do Tile ---")
        tiles = page.locator("flow-grid-tile-container, .virtual-item-container flow-grid-tile-container, [role='gridcell']").all()
        print(f"Total tiles: {len(tiles)}")
        if tiles:
            tile = tiles[0]
            tile.hover()
            page.wait_for_timeout(500)
            page.screenshot(path=str(DIAG_DIR / "1_1_tile_hover.png"))

            # Procura botão de mais opções
            btn_opt = tile.locator("button.mat-mdc-menu-trigger, button[aria-label*='Mais' i], button[aria-label*='More' i]").first
            if btn_opt.is_visible(timeout=1000):
                print(f"Botão de 3 pontinhos visível! aria-label='{btn_opt.get_attribute('aria-label')}'")
                btn_opt.click()
            else:
                print("Botão ⋮ não visível no hover, usando clique direito...")
                tile.click(button="right")

            page.wait_for_timeout(800)
            page.screenshot(path=str(DIAG_DIR / "1_1_tile_menu_aberto.png"))

            tile_items = page.evaluate("""() => {
                const items = Array.from(document.querySelectorAll('.cdk-overlay-pane [role="menuitem"], .cdk-overlay-pane button'));
                return items.map(el => ({
                    text: (el.innerText || '').trim().replace(/\\n/g, ' '),
                    ariaLabel: el.getAttribute('aria-label'),
                    role: el.getAttribute('role'),
                    icon: el.querySelector('mat-icon')?.innerText || '',
                    disabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
                    classes: el.className,
                    outerHTML: el.outerHTML
                }));
            }""")
            print(f"Itens do menu do tile ({len(tile_items)}):")
            for it in tile_items:
                print(f"  * '{it['text']}' | icon='{it['icon']}' | aria='{it['ariaLabel']}'")
            (DIAG_DIR / "1_1_tile_menu_itens.json").write_text(json.dumps(tile_items, indent=2, ensure_ascii=False), encoding="utf-8")

            # Fecha menu
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)

        # =====================================================================
        # 4. Menu "+" (Ingredientes / Recursos)
        # =====================================================================
        print("\n--- Inspecionando Menu '+' de Ingredientes ---")
        plus_btn = page.locator('button[aria-label*="ingrediente" i], button[aria-label*="ingredient" i], button[aria-label*="adicionar" i], button[aria-label*="add" i], button.add-menu-trigger').first
        if not plus_btn.is_visible(timeout=1000):
            plus_btn = page.locator("button:has(mat-icon:has-text('add'))").first
        
        if plus_btn.is_visible(timeout=1000):
            print(f"Botão '+' visível: aria='{plus_btn.get_attribute('aria-label')}'")
            plus_btn.click()
            page.wait_for_timeout(800)
            page.screenshot(path=str(DIAG_DIR / "1_1_menu_mais_aberto.png"))

            plus_html = page.evaluate("""() => {
                const p = document.querySelector('.flow-add-menu-popover-content, .cdk-overlay-pane:has([role="menu"]), .cdk-overlay-pane');
                return p ? p.outerHTML : '';
            }""")
            (DIAG_DIR / "1_1_menu_mais.html").write_text(plus_html, encoding="utf-8")

            plus_items = page.evaluate("""() => {
                const items = Array.from(document.querySelectorAll('.cdk-overlay-pane [role="menuitem"], .cdk-overlay-pane button, [role="tab"]'));
                return items.map(el => ({
                    text: (el.innerText || '').trim().replace(/\\n/g, ' '),
                    ariaLabel: el.getAttribute('aria-label'),
                    role: el.getAttribute('role'),
                    icon: el.querySelector('mat-icon')?.innerText || '',
                    classes: el.className,
                    outerHTML: el.outerHTML
                }));
            }""")
            print(f"Itens do menu '+' ({len(plus_items)}):")
            for p_it in plus_items:
                print(f"  * '{p_it['text']}' | icon='{p_it['icon']}' | aria='{p_it['ariaLabel']}'")
            (DIAG_DIR / "1_1_menu_mais_itens.json").write_text(json.dumps(plus_items, indent=2, ensure_ascii=False), encoding="utf-8")

            page.keyboard.press("Escape")
            page.wait_for_timeout(300)

        print("\nDiagnóstico 1.1 concluído 100% com sucesso!")

if __name__ == "__main__":
    main()
