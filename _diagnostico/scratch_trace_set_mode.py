from playwright.sync_api import sync_playwright
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    pg = browser.contexts[0].pages[1]

    def trace_set_mode(target_mode, modelo_alvo, prop_alvo="16:9", qtd_alvo="x1"):
        print(f"\n--- TRACE START: mode={target_mode}, model={modelo_alvo} ---")
        dock_btn = pg.locator(
            'button.settings-trigger-button, '
            'button[aria-label="Gatilho de configurações"], '
            'button[aria-label="Settings trigger"]'
        ).first
        settings_panel = pg.locator("flow-prompt-box-settings").first

        if not settings_panel.is_visible(timeout=300):
            print("Panel not visible, clicking dock_btn...")
            dock_btn.click(force=True)
            pg.wait_for_timeout(300)

        print("Panel visible now:", settings_panel.is_visible())

        target_tab = "Vídeo" if target_mode == "video" else "Imagem"
        alt_tab = "Video" if target_mode == "video" else "Image"
        tab_selectors = [
            f'flow-prompt-box-settings button[role="radio"]:has-text("{target_tab}")',
            f'flow-prompt-box-settings button[role="radio"]:has-text("{alt_tab}")',
            f'button.settings-trigger-button ~ * button[role="radio"]:has-text("{target_tab}")',
            f'button[role="radio"]:has-text("{target_tab}")',
            f'button[role="radio"]:has-text("{alt_tab}")',
            f'button[role="tab"]:has-text("{target_tab}")',
            f'button[role="tab"]:has-text("{alt_tab}")',
        ]
        tab_clicked = False
        for sel_t in tab_selectors:
            tab_loc = pg.locator(sel_t).first
            try:
                if tab_loc.is_visible(timeout=300):
                    is_sel = (
                        tab_loc.get_attribute("aria-selected") == "true" or
                        tab_loc.get_attribute("aria-checked") == "true" or
                        "selected" in (tab_loc.get_attribute("class") or "").lower() or
                        "checked" in (tab_loc.get_attribute("class") or "").lower()
                    )
                    print(f"Tab matched {sel_t}: is_sel={is_sel}")
                    if not is_sel:
                        print("Clicking tab...")
                        tab_loc.click(force=True)
                        pg.wait_for_timeout(350)
                    tab_clicked = True
                    break
            except Exception as e:
                print(f"Tab error: {e}")
        print(f"Tab processed. Found: {tab_clicked}")

        # Dropdown
        dd_trigger = pg.locator(
            'flow-prompt-box-settings button[aria-label="Selecionar família de modelos"], '
            'button[aria-label="Selecionar família de modelos"], '
            'button:has(.model-select-trigger-content)'
        ).first
        print(f"dd_trigger visible: {dd_trigger.is_visible()}")
        if dd_trigger.is_visible(timeout=500):
            curr_trigger_txt = (dd_trigger.inner_text() or "").lower()
            print(f"curr_trigger_txt: {repr(curr_trigger_txt)}")
            precisa_abrir = True
            if target_mode == "video" and "veo" in curr_trigger_txt:
                precisa_abrir = False
            elif target_mode == "image" and ("banana" in curr_trigger_txt or "imagen" in curr_trigger_txt):
                precisa_abrir = False
            print(f"precisa_abrir: {precisa_abrir}")
            if precisa_abrir:
                dd_trigger.click(force=True)
                pg.wait_for_timeout(350)
                # print items
                items = pg.locator('.cdk-overlay-pane [role="menuitem"]').all()
                print("Dropdown items opened:", [it.inner_text() for it in items])

        pg.keyboard.press("Escape")
        pg.wait_for_timeout(200)
        dock_txt = dock_btn.inner_text()
        print(f"FINAL DOCK TEXT: {repr(dock_txt)}")

    trace_set_mode("video", "Veo 3.1 - Lite")
    trace_set_mode("image", "Nano Banana 2")
