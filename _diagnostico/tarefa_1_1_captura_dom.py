import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

DIAG_DIR = Path(r"C:\Lira Videos\_diagnostico")

def main():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://[::1]:9222")
        context = browser.contexts[0]
        # Localiza a página do projeto
        page = None
        for pg in context.pages:
            if "/project/" in pg.url:
                page = pg
                break
        if not page:
            page = context.pages[0]

        print(f"Página ativa: {page.url} | {page.title()}")
        page.wait_for_timeout(2000)

        # =====================================================================
        # PARTE 1: Painel de Configurações (button.settings-trigger-button)
        # =====================================================================
        print("\n--- 1. Painel de Configurações ---")
        settings_btn = None
        for sel in [
            'button.settings-trigger-button',
            'button[aria-label*="configurações" i]',
            'button[aria-label*="settings" i]',
            '.settings-trigger-button',
            'button:has(.settings-trigger-content)'
        ]:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1000):
                settings_btn = loc
                print(f"Botão de configurações encontrado via seletor: {sel}")
                break
        
        if not settings_btn:
            print("Buscando botão de configurações via evaluate...")
            idx = page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button'));
                for (let i = 0; i < btns.length; i++) {
                    const b = btns[i];
                    const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                    const txt = (b.innerText || '').toLowerCase();
                    const cls = (b.className || '').toLowerCase();
                    if (cls.includes('settings') || aria.includes('configur') || aria.includes('setting') || txt.includes('configur')) {
                        return i;
                    }
                }
                return -1;
            }""")
            if idx >= 0:
                settings_btn = page.locator('button').nth(idx)
                print(f"Botão de configurações encontrado via nth({idx}): {settings_btn.inner_text()}")

        if settings_btn:
            print(f"HTML do botão de configurações: {settings_btn.evaluate('el => el.outerHTML')}")
            settings_btn.click()
            page.wait_for_timeout(1000)
            page.screenshot(path=str(DIAG_DIR / "1_1_painel_config_aberto.png"))
            print("Screenshot salvo: 1_1_painel_config_aberto.png")

            # Captura o container do painel aberto
            panel_html = page.evaluate("""() => {
                const panel = document.querySelector('.flow-settings-panel, .cdk-overlay-pane, [role="dialog"], [role="menu"], .settings-panel');
                return panel ? panel.outerHTML : document.body.innerHTML;
            }""")
            (DIAG_DIR / "1_1_painel_config.html").write_text(panel_html, encoding="utf-8")
            print("HTML do painel salvo em 1_1_painel_config.html")

            # =====================================================================
            # PARTE 2: Dropdown de Modelo e Opções de Vídeo/Imagem
            # =====================================================================
            print("\n--- 2. Abas de Modo (Imagem / Vídeo) e Dropdown de Modelos ---")
            tabs_info = page.evaluate("""() => {
                const tabs = Array.from(document.querySelectorAll('[role="tab"], [role="radio"], mat-button-toggle, button'));
                return tabs.filter(t => {
                    const txt = (t.innerText || '').trim();
                    return ['imagem', 'vídeo', 'image', 'video'].includes(txt.toLowerCase());
                }).map(t => ({
                    text: t.innerText.trim(),
                    tag: t.tagName,
                    role: t.getAttribute('role'),
                    ariaSelected: t.getAttribute('aria-selected'),
                    ariaChecked: t.getAttribute('aria-checked'),
                    classes: t.className,
                    outerHTML: t.outerHTML
                }));
            }""")
            print(f"Abas de modo encontradas: {json.dumps(tabs_info, indent=2, ensure_ascii=False)}")
            (DIAG_DIR / "1_1_abas_modo.json").write_text(json.dumps(tabs_info, indent=2, ensure_ascii=False), encoding="utf-8")

            # Se houver aba de Vídeo, tenta clicar nela para inspecionar modelos de vídeo
            video_tab = page.locator('button[role="tab"]:has-text("Vídeo"), button[role="tab"]:has-text("Video"), button:has-text("Vídeo"), button:has-text("Video"), [role="radio"]:has-text("Vídeo")').first
            if video_tab.is_visible(timeout=1000):
                print(f"Clicando na aba de Vídeo: {video_tab.inner_text()}")
                video_tab.click()
                page.wait_for_timeout(800)
                page.screenshot(path=str(DIAG_DIR / "1_1_aba_video_ativa.png"))

            # Abre dropdown de modelo
            model_btn = None
            for m_sel in [
                'button[aria-label*="modelo" i]',
                'button[aria-label*="model" i]',
                'button:has(.model-select-trigger-content)',
                '.model-select-trigger',
                'button[aria-haspopup="menu"]'
            ]:
                loc = page.locator(m_sel).first
                if loc.is_visible(timeout=800):
                    model_btn = loc
                    print(f"Gatilho de modelo encontrado: {m_sel} -> text='{loc.inner_text().strip()}'")
                    break

            if model_btn:
                model_btn.click()
                page.wait_for_timeout(1000)
                page.screenshot(path=str(DIAG_DIR / "1_1_dropdown_modelos_aberto.png"))
                
                # Lista todas as opções de modelo
                model_options = page.evaluate("""() => {
                    const items = Array.from(document.querySelectorAll('.cdk-overlay-pane button, [role="menuitem"], [role="option"], .mat-mdc-menu-item'));
                    return items.map(el => ({
                        text: (el.innerText || '').trim(),
                        role: el.getAttribute('role'),
                        ariaLabel: el.getAttribute('aria-label'),
                        ariaDisabled: el.getAttribute('aria-disabled'),
                        disabled: el.disabled || false,
                        ariaCurrent: el.getAttribute('aria-current'),
                        classes: el.className,
                        outerHTML: el.outerHTML
                    }));
                }""")
                print(f"Opções de modelo encontradas: {len(model_options)}")
                for opt in model_options:
                    print(f"  Modelo: '{opt['text']}' | disabled={opt['disabled'] or opt['ariaDisabled']} | role={opt['role']}")
                (DIAG_DIR / "1_1_opcoes_modelo.json").write_text(json.dumps(model_options, indent=2, ensure_ascii=False), encoding="utf-8")
                
                # Fecha o dropdown
                page.keyboard.press("Escape")
                page.wait_for_timeout(400)

            # Fecha o painel de configurações
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
        else:
            print("AVISO: Botão de configurações não encontrado!")

        # =====================================================================
        # PARTE 3: Menu do Tile de Imagem (clique direito e hover + ⋮)
        # =====================================================================
        print("\n--- 3. Menu do Tile de Imagem na Galeria ---")
        tiles = page.locator("flow-grid-tile-container, .virtual-item-container flow-grid-tile-container, [role='gridcell']").all()
        print(f"Total tiles na galeria: {len(tiles)}")
        if tiles:
            tile = tiles[0]
            print("Tentando abrir menu via hover no primeiro tile...")
            tile.hover()
            page.wait_for_timeout(600)
            page.screenshot(path=str(DIAG_DIR / "1_1_tile_hover.png"))

            # Procura botão de 3 pontinhos dentro do tile
            menu_btn = tile.locator("button[aria-label*='Mais' i], button[aria-label*='More' i], button.mat-mdc-menu-trigger, button:has-text('⋮'), button:has-text('⋯')").first
            menu_aberto = False
            if menu_btn.is_visible(timeout=1000):
                print(f"Botão ⋮ visível no tile! aria-label='{menu_btn.get_attribute('aria-label')}'")
                menu_btn.click()
                page.wait_for_timeout(800)
                menu_aberto = True
                page.screenshot(path=str(DIAG_DIR / "1_1_tile_menu_3pontinhos.png"))
            else:
                print("Botão ⋮ não visível diretamente. Tentando clique com botão direito no tile...")
                tile.click(button="right")
                page.wait_for_timeout(800)
                menu_aberto = True
                page.screenshot(path=str(DIAG_DIR / "1_1_tile_menu_right_click.png"))

            if menu_aberto:
                tile_menu_items = page.evaluate("""() => {
                    const items = Array.from(document.querySelectorAll('.cdk-overlay-pane button, [role="menu"] button, [role="menuitem"], .mat-mdc-menu-item'));
                    return items.map(el => ({
                        text: (el.innerText || '').trim().replace(/\\n/g, ' '),
                        ariaLabel: el.getAttribute('aria-label'),
                        role: el.getAttribute('role'),
                        icon: el.querySelector('mat-icon, [class*="icon"]')?.innerText || '',
                        disabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
                        classes: el.className,
                        outerHTML: el.outerHTML
                    }));
                }""")
                print(f"Itens do menu do tile encontrados: {len(tile_menu_items)}")
                for it in tile_menu_items:
                    print(f"  Item: '{it['text']}' | icon='{it['icon']}' | aria='{it['ariaLabel']}'")
                (DIAG_DIR / "1_1_tile_menu_itens.json").write_text(json.dumps(tile_menu_items, indent=2, ensure_ascii=False), encoding="utf-8")
                page.keyboard.press("Escape")
                page.wait_for_timeout(400)

        # =====================================================================
        # PARTE 4: Menu "+" (Recursos / Ingredientes)
        # =====================================================================
        print("\n--- 4. Menu '+' de Ingredientes / Recursos ---")
        plus_btn = None
        for p_sel in [
            'button[aria-label*="ingrediente" i]',
            'button[aria-label*="ingredient" i]',
            'button[aria-label*="adicionar" i]',
            'button[aria-label*="add" i]',
            'button.add-menu-trigger'
        ]:
            loc = page.locator(p_sel).first
            if loc.is_visible(timeout=800):
                plus_btn = loc
                print(f"Botão '+' encontrado via {p_sel}: aria='{loc.get_attribute('aria-label')}'")
                break

        if not plus_btn:
            plus_btn = page.locator("button:has-text('add'), button:has-text('add_circle')").first

        if plus_btn and plus_btn.is_visible(timeout=1000):
            plus_btn.click()
            page.wait_for_timeout(800)
            page.screenshot(path=str(DIAG_DIR / "1_1_menu_mais_aberto.png"))

            plus_menu_items = page.evaluate("""() => {
                const items = Array.from(document.querySelectorAll('.cdk-overlay-pane button, [role="menu"] button, [role="menuitem"], .flow-add-menu-popover-content button, [role="tab"]'));
                return items.map(el => ({
                    text: (el.innerText || '').trim().replace(/\\n/g, ' '),
                    ariaLabel: el.getAttribute('aria-label'),
                    role: el.getAttribute('role'),
                    icon: el.querySelector('mat-icon, [class*="icon"]')?.innerText || '',
                    classes: el.className,
                    outerHTML: el.outerHTML
                }));
            }""")
            print(f"Itens do menu '+' encontrados: {len(plus_menu_items)}")
            for it in plus_menu_items:
                print(f"  Item '+': '{it['text']}' | icon='{it['icon']}' | aria='{it['ariaLabel']}'")
            (DIAG_DIR / "1_1_menu_mais_itens.json").write_text(json.dumps(plus_menu_items, indent=2, ensure_ascii=False), encoding="utf-8")
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
        else:
            print("AVISO: Botão '+' não encontrado!")

        print("\nTarefa 1.1 concluída com sucesso!")

if __name__ == "__main__":
    main()
