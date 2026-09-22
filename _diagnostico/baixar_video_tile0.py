import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')
DIAG_DIR = Path(r"C:\Lira Videos\_diagnostico")

def limpar_overlays(page):
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    except Exception:
        pass

def main():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://[::1]:9222")
        context = browser.contexts[0]
        page = [pg for pg in context.pages if "/project/" in pg.url][0]
        print(f"[CONEXÃO] Página conectada: {page.url}")

        limpar_overlays(page)

        # Inspeciona Tile 0
        tiles = page.locator("flow-grid-tile-container").all()
        print(f"Total tiles no canvas: {len(tiles)}")
        tile0 = tiles[0]
        
        has_video = tile0.locator("video").count() > 0
        has_img = tile0.locator("img").count() > 0
        print(f"Tile 0: has_video={has_video}, has_img={has_img}")

        if not has_video:
            print("AVISO: Tile 0 não tem tag video!")
        else:
            v_src = tile0.locator("video").first.get_attribute("src")
            print(f"Vídeo src: {v_src}")

        # Hover e clica nos 3 pontinhos
        tile0.hover()
        page.wait_for_timeout(500)
        btn_opt = tile0.locator("button.mat-mdc-menu-trigger, button[aria-label*='Mais' i]").first
        if btn_opt.is_visible(timeout=1000):
            btn_opt.click()
        else:
            tile0.click(button="right")
        page.wait_for_timeout(600)

        # Clica 'Fazer o download'
        btn_dl = page.locator('.cdk-overlay-pane [role="menuitem"]:has-text("Fazer o download")').first
        print(f"Botão 'Fazer o download' visível: {btn_dl.is_visible()}")
        btn_dl.hover()
        page.wait_for_timeout(300)
        btn_dl.click()
        page.wait_for_timeout(600)
        page.screenshot(path=str(DIAG_DIR / "1_2_submenu_download_aberto.png"))

        # Inspeciona itens do submenu aberto
        sub_items = page.evaluate("""() => {
            const items = Array.from(document.querySelectorAll('.cdk-overlay-pane [role="menuitem"], .cdk-overlay-pane button'));
            return items.map(el => ({
                text: el.innerText.trim().replace(/\\n/g, ' '),
                aria: el.getAttribute('aria-label'),
                role: el.getAttribute('role')
            }));
        }""")
        print(f"Itens disponíveis no submenu de download ({len(sub_items)}):")
        for s in sub_items:
            print(f"  * text='{s['text']}' aria='{s['aria']}' role='{s['role']}'")

        # Clica na opção de download (ex: 720p ou 1080p ou primeira opção de download)
        opt_dl = page.locator('.cdk-overlay-pane [role="menuitem"]:has-text("720p"), .cdk-overlay-pane [role="menuitem"]:has-text("1080p"), .cdk-overlay-pane button:has-text("720p"), .cdk-overlay-pane button:has-text("1080p")').first
        if not opt_dl.is_visible():
            opt_dl = page.locator('.cdk-overlay-pane [role="menuitem"]').last

        print(f"Opção de download alvo selecionada: text='{opt_dl.inner_text().strip().replace(chr(10), ' ')}'")
        caminho_mp4 = DIAG_DIR / "1_2_video_i2v_real.mp4"

        with page.expect_download(timeout=35000) as dl_info:
            opt_dl.click()

        download = dl_info.value
        download.save_as(str(caminho_mp4))
        print(f"\n=======================================================")
        print(f"VÍDEO BAIXADO COM SUCESSO: {caminho_mp4}")
        print(f"Tamanho do arquivo: {caminho_mp4.stat().st_size} bytes")

        with open(caminho_mp4, "rb") as f:
            header = f.read(32)
        print(f"Cabeçalho primeiros 32 bytes (hex): {header.hex()}")
        print(f"Cabeçalho ASCII: {header}")
        is_mp4 = b"ftyp" in header or b"moov" in header or b"isom" in header
        print(f"Validação cabeçalho MP4: {is_mp4}")
        print(f"=======================================================")

if __name__ == "__main__":
    main()
