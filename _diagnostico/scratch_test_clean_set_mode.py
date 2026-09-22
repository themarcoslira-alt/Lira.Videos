import sys, re
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    try:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    except Exception:
        browser = p.chromium.connect_over_cdp("http://[::1]:9222")

    pg = None
    for page in browser.contexts[0].pages:
        if "flow.google.com" in page.url:
            pg = page
            break

    dock_btn = pg.locator('button.settings-trigger-button, button[aria-label="Gatilho de configurações"]').first
    sp = pg.locator("flow-prompt-box-settings")

    def ensure_settings_open():
        for _ in range(3):
            if sp.count() > 0 and sp.first.is_visible():
                return True
            dock_btn.click()
            pg.wait_for_timeout(300)
        return sp.count() > 0 and sp.first.is_visible()

    def ensure_settings_closed():
        for _ in range(3):
            if sp.count() == 0:
                return True
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(200)
        return sp.count() == 0

    def set_mode_test(target_mode, modelo_alvo="Veo 3.1 - Lite", prop_alvo="16:9", qtd_alvo="x1"):
        print(f"\nSetting mode to {target_mode} ({modelo_alvo})...")
        assert ensure_settings_open(), "Falha ao abrir painel de configurações"

        # Tab Imagem vs Vídeo usando regex resiliente a encoding/idioma
        tab_pattern = re.compile(r"v[ií]deo", re.I) if target_mode == "video" else re.compile(r"imag[eé]m|image", re.I)
        tab_loc = pg.locator('flow-prompt-box-settings button[role="radio"]').filter(has_text=tab_pattern).first
        print(f"Tab loc found: {tab_loc.count()} text: {repr(tab_loc.inner_text() if tab_loc.count() else 'NONE')}")
        if tab_loc.is_visible(timeout=500):
            if tab_loc.get_attribute("aria-checked") != "true":
                print("Clicking tab...")
                tab_loc.click(force=True)
                pg.wait_for_timeout(300)

        # Proporção
        prop_btn = pg.locator(f'flow-prompt-box-settings button[role="radio"]').filter(has_text=prop_alvo).first
        if prop_btn.is_visible(timeout=400):
            if prop_btn.get_attribute("aria-checked") != "true":
                prop_btn.click(force=True)
                pg.wait_for_timeout(200)

        # Dropdown de modelo se necessário
        dd_trigger = pg.locator('flow-prompt-box-settings button:has(.model-select-trigger-content), flow-prompt-box-settings button[aria-label="Selecionar família de modelos"]').first
        if dd_trigger.is_visible(timeout=400):
            curr_txt = (dd_trigger.inner_text() or "").lower()
            needs_dd = False
            if target_mode == "video" and "veo" not in curr_txt:
                needs_dd = True
            elif target_mode == "image" and ("banana" not in curr_txt and "imagen" not in curr_txt):
                needs_dd = True
            elif modelo_alvo.lower() not in curr_txt:
                needs_dd = True

            if needs_dd:
                dd_trigger.click(force=True)
                pg.wait_for_timeout(300)
                opt = pg.locator(f'.cdk-overlay-pane [role="menuitem"]').filter(has_text=modelo_alvo).first
                if opt.is_visible(timeout=400):
                    opt.click(force=True)
                    pg.wait_for_timeout(200)

        ensure_settings_closed()

        dock_txt = (dock_btn.inner_text() or "").lower()
        print(f"Result dock text: {repr(dock_txt)}")
        if target_mode == "video":
            assert "vídeo" in dock_txt or "video" in dock_txt, f"Expected video in dock, got {dock_txt}"
        else:
            assert "banana" in dock_txt or "imagem" in dock_txt or "imagen" in dock_txt or ("vídeo" not in dock_txt and "video" not in dock_txt), f"Expected image in dock, got {dock_txt}"
        print("OK!")

    set_mode_test("video", "Veo 3.1 - Lite")
    set_mode_test("image", "Nano Banana 2")
    set_mode_test("video", "Veo 3.1 - Lite")
    print("\nALL PASSED SUCCESSFULLY!")
