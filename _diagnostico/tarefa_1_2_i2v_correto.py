import json
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')
DIAG_DIR = Path(r"C:\Lira Videos\_diagnostico")

def limpar_overlays(page):
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        page.evaluate("""() => {
            document.querySelectorAll('.cdk-overlay-backdrop').forEach(el => {
                try { el.remove(); } catch(e){}
            });
        }""")
    except Exception:
        pass

def main():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://[::1]:9222")
        context = browser.contexts[0]
        page = [pg for pg in context.pages if "/project/" in pg.url][0]
        print(f"[CONEXÃO] Página conectada: {page.url}")

        limpar_overlays(page)

        # -------------------------------------------------------------
        # Limpar chips ou textos prévios no prompt box
        # -------------------------------------------------------------
        print("\n[LIMPEZA] Removendo chips antigos e limpando editor...")
        btn_clear = page.locator('button[aria-label="Apagar comando"], button.clear-button').first
        if btn_clear.is_visible(timeout=500):
            btn_clear.click()
            page.wait_for_timeout(400)

        # Remove qualquer chip disabled restante
        page.evaluate("""() => {
            document.querySelectorAll('.disabled-cancel-icon, .chip-container-disabled .cancel').forEach(b => {
                try { b.click(); } catch(e){}
            });
        }""")
        page.wait_for_timeout(300)

        # -------------------------------------------------------------
        # PASSO 1: Configurar Modo Vídeo + Modelo Veo 3.1 - Lite
        # -------------------------------------------------------------
        print("\n[PASSO 1] Verificando Modo Vídeo e Veo 3.1 - Lite...")
        btn_cfg = page.locator('button[aria-label="Gatilho de configurações"]').first
        texto_cfg = btn_cfg.inner_text().strip().replace("\n", " ")
        print(f"Gatilho cfg texto: '{texto_cfg}'")

        btn_cfg.click(force=True)
        page.wait_for_timeout(600)

        panel = page.locator("flow-prompt-box-settings, .settings-content-overlay").first
        if panel.is_visible(timeout=2000):
            # Modo Vídeo
            tab_video = panel.locator('button[role="radio"]:has-text("Vídeo")').first
            if tab_video.get_attribute("aria-checked") != "true":
                tab_video.click()
                page.wait_for_timeout(300)

            # Proporção 16:9
            tab_16_9 = panel.locator('flow-toggles[aria-label="Proporção"] button[role="radio"]:has-text("16:9")').first
            if tab_16_9.is_visible() and tab_16_9.get_attribute("aria-checked") != "true":
                tab_16_9.click()
                page.wait_for_timeout(200)

            # Modelo Veo 3.1 - Lite
            model_btn = panel.locator('button[aria-label="Selecionar família de modelos"]').first
            texto_mod = model_btn.inner_text().strip().replace("\n", " ")
            if "veo 3.1 - lite" not in texto_mod.lower():
                print(f"Alterando modelo de '{texto_mod}' para 'Veo 3.1 - Lite'...")
                model_btn.click()
                page.wait_for_timeout(500)
                veo_opt = page.locator('.cdk-overlay-pane [role="menuitem"]:has-text("Veo 3.1 - Lite")').first
                veo_opt.click()
                page.wait_for_timeout(500)
            else:
                print("Veo 3.1 - Lite já confirmado!")

            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
            limpar_overlays(page)

        # -------------------------------------------------------------
        # PASSO 2: Localizar um Tile de IMAGEM (não de vídeo)
        # -------------------------------------------------------------
        print("\n[PASSO 2] Localizando tile de imagem...")
        image_tile_idx = page.evaluate("""() => {
            const tiles = Array.from(document.querySelectorAll('flow-grid-tile-container'));
            for (let i = 0; i < tiles.length; i++) {
                if (tiles[i].querySelector('img') && !tiles[i].querySelector('video')) {
                    return i;
                }
            }
            return -1;
        }""")
        print(f"Índice do tile de imagem encontrado: {image_tile_idx}")
        if image_tile_idx < 0:
            raise RuntimeError("Nenhum tile de imagem estática encontrado no canvas do Flow!")

        tiles = page.locator("flow-grid-tile-container").all()
        target_tile = tiles[image_tile_idx]
        target_tile.hover()
        page.wait_for_timeout(400)

        # Clica no menu do tile
        btn_opt = target_tile.locator("button.mat-mdc-menu-trigger, button[aria-label*='Mais' i]").first
        if btn_opt.is_visible(timeout=1000):
            btn_opt.click()
        else:
            target_tile.click(button="right")
        page.wait_for_timeout(600)

        # Clica 'Incluir no comando'
        incluir_cmd = page.locator('.cdk-overlay-pane [role="menuitem"]:has-text("Incluir no comando")').first
        print(f"Clicando em 'Incluir no comando' (visível={incluir_cmd.is_visible()})...")
        incluir_cmd.click()
        page.wait_for_timeout(1000)
        limpar_overlays(page)
        page.screenshot(path=str(DIAG_DIR / "1_2_chip_imagem_anexado.png"))

        # -------------------------------------------------------------
        # PASSO 3: Validar que o chip é válido (sem erro)
        # -------------------------------------------------------------
        print("\n[PASSO 3] Inspecionando chip no prompt box...")
        chip_info = page.evaluate("""() => {
            const chip = document.querySelector('.chip-container');
            if (!chip) return null;
            return {
                classes: chip.className,
                isDisabled: chip.className.includes('disabled'),
                ariaLabel: chip.getAttribute('aria-label'),
                text: chip.innerText.replace(/\\n/g, ' ')
            };
        }""")
        print(f"Status do chip anexado: {chip_info}")

        # -------------------------------------------------------------
        # PASSO 4: Digitar Prompt de Movimento
        # -------------------------------------------------------------
        print("\n[PASSO 4] Digitando prompt de movimento...")
        editor = page.locator('flow-rich-text-editor div.ProseMirror').first
        editor.click()
        page.wait_for_timeout(200)

        prompt_movimento = "Smooth slow cinematic camera pan, natural gentle garden movement, 4s duration."
        page.keyboard.type(prompt_movimento, delay=20)
        page.wait_for_timeout(400)
        page.screenshot(path=str(DIAG_DIR / "1_2_prompt_com_imagem_pronto.png"))

        # -------------------------------------------------------------
        # PASSO 5: Enviar Comando via button.generate-icon-button
        # -------------------------------------------------------------
        print("\n[PASSO 5] Enviando geração de vídeo...")
        submit_btn = page.locator('button.generate-icon-button, button[aria-label="Iniciar geração"]').first
        print(f"Botão de envio: visível={submit_btn.is_visible()} disabled={submit_btn.get_attribute('disabled')}")
        page.screenshot(path=str(DIAG_DIR / "1_2_pre_envio_submit.png"))
        
        submit_btn.click()
        print("Botão de geração clicado com sucesso!")
        page.wait_for_timeout(4000)
        page.screenshot(path=str(DIAG_DIR / "1_2_pos_clique_gerar.png"))

        # -------------------------------------------------------------
        # PASSO 6: Monitorar Renderização
        # -------------------------------------------------------------
        print("\n[PASSO 6] Monitorando renderização de vídeo no Flow...")
        inicio_t = time.time()
        sucesso_render = False

        while time.time() - inicio_t < 180:
            status = page.evaluate("""() => {
                const prog = document.querySelector('mat-progress-bar, .progress-bar, .loading, [role="progressbar"]');
                const overlay = document.querySelector('.generating-overlay, .generation-status');
                const toast = document.querySelector('.cdk-overlay-pane snack-bar-container, [role="alert"]');
                const firstTile = document.querySelector('flow-grid-tile-container');
                const firstTileLoading = firstTile ? !!firstTile.querySelector('.loading, .spinner, [role="progressbar"]') : false;
                return {
                    isGenerating: !!prog || !!overlay || firstTileLoading,
                    toastText: toast ? toast.innerText : ''
                };
            }""")
            elapsed = int(time.time() - inicio_t)
            print(f"  [{elapsed}s] Renderizando: {status}")

            if status.get("toastText"):
                print(f"  [TOAST DETECTADO] {status['toastText']}")

            # Se estava gerando e terminou após pelo menos 25 segundos:
            if elapsed > 25 and not status.get("isGenerating"):
                print(f"Renderização concluída em {elapsed}s!")
                sucesso_render = True
                break
            time.sleep(6)

        page.screenshot(path=str(DIAG_DIR / "1_2_render_finalizada.png"))

        # -------------------------------------------------------------
        # PASSO 7: Baixar o Vídeo Gerado
        # -------------------------------------------------------------
        print("\n[PASSO 7] Baixando a mídia gerada...")
        tiles_finais = page.locator("flow-grid-tile-container").all()
        print(f"Total tiles no canvas: {len(tiles_finais)}")
        novo_tile = tiles_finais[0]
        novo_tile.hover()
        page.wait_for_timeout(500)

        btn_opt_novo = novo_tile.locator("button.mat-mdc-menu-trigger, button[aria-label*='Mais' i]").first
        if btn_opt_novo.is_visible(timeout=1000):
            btn_opt_novo.click()
        else:
            novo_tile.click(button="right")
        page.wait_for_timeout(600)

        btn_download = page.locator('.cdk-overlay-pane [role="menuitem"]:has-text("Fazer o download")').first
        print(f"Botão 'Fazer o download' visível: {btn_download.is_visible()}")

        caminho_video = DIAG_DIR / "1_2_video_i2v_sucesso.mp4"
        with page.expect_download(timeout=30000) as dl_info:
            btn_download.click()

        dl = dl_info.value
        dl.save_as(str(caminho_video))
        print(f"\n=======================================================")
        print(f"VÍDEO BAIXADO COM SUCESSO: {caminho_video}")
        print(f"Tamanho do arquivo: {caminho_video.stat().st_size} bytes")

        with open(caminho_video, "rb") as f:
            header = f.read(32)
        print(f"Cabeçalho primeiros 32 bytes (hex): {header.hex()}")
        print(f"Cabeçalho ASCII: {header}")
        is_mp4 = b"ftyp" in header or b"moov" in header or b"isom" in header
        print(f"Validação cabeçalho MP4: {is_mp4}")
        print(f"=======================================================")

if __name__ == "__main__":
    main()
