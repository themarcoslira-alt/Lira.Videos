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
        print(f"[CONEXÃO] Página conectada: {page.url}")

        # Localiza o elemento video no Tile 0
        video_el = page.locator("flow-grid-tile-container video, video").first
        v_src = video_el.get_attribute("src")
        print(f"Vídeo SRC capturado do DOM: {v_src}")

        if not v_src:
            # Tenta pegar via evaluate
            v_src = page.evaluate("() => document.querySelector('video')?.src || ''")
            print(f"Vídeo SRC via evaluate: {v_src}")

        if not v_src:
            raise RuntimeError("Não foi possível encontrar a tag video com atributo src no DOM!")

        print("Baixando bytes do vídeo via page.request...")
        resp = page.request.get(v_src)
        print(f"Status da resposta HTTP: {resp.status} {resp.status_text}")
        print(f"Headers da resposta: {resp.headers.get('content-type')}")
        
        video_bytes = resp.body()
        print(f"Total bytes recebidos: {len(video_bytes)} bytes")

        caminho_salvo = DIAG_DIR / "1_2_video_i2v_real.mp4"
        caminho_salvo.write_bytes(video_bytes)
        print(f"Arquivo gravado com sucesso em: {caminho_salvo}")

        # Validação do cabeçalho
        with open(caminho_salvo, "rb") as f:
            header = f.read(32)
        print(f"Cabeçalho primeiros 32 bytes (hex): {header.hex()}")
        print(f"Cabeçalho ASCII: {header}")
        is_mp4 = b"ftyp" in header or b"moov" in header or b"isom" in header or b"mp4" in header
        print(f"Confirmação de formato MP4: {is_mp4}")

if __name__ == "__main__":
    main()
