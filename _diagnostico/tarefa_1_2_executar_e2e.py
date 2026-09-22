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
        # PASSO 1: Configurar Modo Vídeo + Modelo Veo 3.1 - Lite
        # -------------------------------------------------------------
        print("\n[PASSO 1] Verificando / Configurando Modo Vídeo...")
        btn_cfg = page.locator('button[aria-label="Gatilho de configurações"], button.settings-trigger-button').first
        texto_cfg = btn_cfg.inner_text().strip().replace("\n", " ")
        print(f"Estado inicial do gatilho: '{texto_cfg}'")

        # Abre painel de configurações se necessário
        limpar_overlays(page)
        btn_cfg.click(force=True)
        page.wait_for_timeout(600)

        panel = page.locator("flow-prompt-box-settings, .settings-content-overlay").first
        if not panel.is_visible(timeout=2000):
            print("Painel não abriu, tentando novamente com force=True...")
            btn_cfg.click(force=True)
            page.wait_for_timeout(600)

        print(f"Painel aberto: {panel.is_visible()}")

        # Seleciona Vídeo
        tab_video = panel.locator('button[role="radio"]:has-text("Vídeo")').first
        if tab_video.is_visible(timeout=1000):
            if tab_video.get_attribute("aria-checked") != "true":
                print("Clicando no botão 'Vídeo'...")
                tab_video.click()
                page.wait_for_timeout(400)
            else:
                print("Aba Vídeo já selecionada.")

        # Seleciona 16:9
        tab_16_9 = panel.locator('flow-toggles[aria-label="Proporção"] button[role="radio"]:has-text("16:9")').first
        if tab_16_9.is_visible(timeout=1000) and tab_16_9.get_attribute("aria-checked") != "true":
            print("Selecionando proporção 16:9...")
            tab_16_9.click()
            page.wait_for_timeout(300)

        # Seleciona Modelo Veo 3.1 - Lite
        model_btn = panel.locator('button[aria-label="Selecionar família de modelos"]').first
        texto_modelo = model_btn.inner_text().strip().replace("\n", " ")
        print(f"Modelo atualmente selecionado: '{texto_modelo}'")

        if "veo 3.1 - lite" not in texto_modelo.lower():
            print("Abrindo dropdown de modelos...")
            model_btn.click()
            page.wait_for_timeout(600)

            veo_opt = page.locator('.cdk-overlay-pane [role="menuitem"]:has-text("Veo 3.1 - Lite")').first
            print(f"Opção Veo 3.1 - Lite visível: {veo_opt.is_visible()}")
            if veo_opt.is_visible():
                veo_opt.click()
                page.wait_for_timeout(600)
                print(f"Modelo confirmado após clique: '{model_btn.inner_text().strip().replace(chr(10), ' ')}'")
        else:
            print("Veo 3.1 - Lite já está ativo!")

        # Fecha painel
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        limpar_overlays(page)

        texto_cfg_final = btn_cfg.inner_text().strip().replace("\n", " ")
        print(f"[SUCESSO PASSO 1] Configuração aplicada: '{texto_cfg_final}'")
        page.screenshot(path=str(DIAG_DIR / "1_2_passo1_config_ok.png"))

        # -------------------------------------------------------------
        # PASSO 2: Anexar Imagem via 'Incluir no comando' do Tile
        # -------------------------------------------------------------
        print("\n[PASSO 2] Anexando imagem do tile via 'Incluir no comando'...")
        tiles = page.locator("flow-grid-tile-container, .virtual-item-container flow-grid-tile-container").all()
        tile = tiles[0]
        tile.hover()
        page.wait_for_timeout(500)

        btn_opt = tile.locator("button.mat-mdc-menu-trigger, button[aria-label*='Mais' i]").first
        if btn_opt.is_visible(timeout=1000):
            btn_opt.click()
        else:
            tile.click(button="right")
        page.wait_for_timeout(600)

        incluir_cmd = page.locator('.cdk-overlay-pane [role="menuitem"]:has-text("Incluir no comando")').first
        print(f"Opção 'Incluir no comando' visível: {incluir_cmd.is_visible()}")
        incluir_cmd.click()
        page.wait_for_timeout(1000)
        limpar_overlays(page)
        page.screenshot(path=str(DIAG_DIR / "1_2_passo2_imagem_anexada.png"))

        # -------------------------------------------------------------
        # PASSO 3: Digitar Prompt de Movimento
        # -------------------------------------------------------------
        print("\n[PASSO 3] Digitando prompt de movimento no editor...")
        editor = page.locator('flow-rich-text-editor div.ProseMirror, div.ProseMirror[contenteditable="true"]').first
        editor.click()
        page.wait_for_timeout(200)
        
        # Limpa texto prévio se houver
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.wait_for_timeout(200)

        prompt_movimento = "Smooth slow cinematic camera pan, natural gentle garden movement, 4s duration."
        page.keyboard.type(prompt_movimento, delay=25)
        page.wait_for_timeout(500)
        page.screenshot(path=str(DIAG_DIR / "1_2_passo3_prompt_pronto.png"))
        print(f"Prompt digitado: '{prompt_movimento}'")

        # -------------------------------------------------------------
        # PASSO 4: Clicar no botão Enviar / Criar
        # -------------------------------------------------------------
        print("\n[PASSO 4] Enviando comando para geração de vídeo...")
        # Localiza o botão Create / Arrow / Run
        submit_btn = page.locator('button.run-button, button[aria-label*="gerar" i], button[aria-label*="criar" i], button:has(mat-icon:has-text("arrow_upward")), button:has(mat-icon:has-text("play_arrow"))').first
        print(f"Botão de envio: aria='{submit_btn.get_attribute('aria-label')}' text='{submit_btn.inner_text().strip()}'")
        
        submit_btn.click()
        print("Botão de envio clicado!")
        page.wait_for_timeout(3000)
        page.screenshot(path=str(DIAG_DIR / "1_2_passo4_apos_envio.png"))

        # -------------------------------------------------------------
        # PASSO 5: Monitorar a Renderização até Concluir
        # -------------------------------------------------------------
        print("\n[PASSO 5] Monitorando renderização no Flow...")
        inicio_t = time.time()
        concluido = False
        while time.time() - inicio_t < 180:
            # Verifica se há indicador de progresso ativo
            status_render = page.evaluate("""() => {
                const prog = document.querySelector('mat-progress-bar, .progress-bar, .loading, [role="progressbar"]');
                const overlay = document.querySelector('.generating-overlay, .generation-status');
                const toast = document.querySelector('.cdk-overlay-pane snack-bar-container, [role="alert"]');
                return {
                    isGenerating: !!prog || !!overlay,
                    progText: prog ? prog.innerText : '',
                    toastText: toast ? toast.innerText : ''
                };
            }""")
            print(f"  [{int(time.time() - inicio_t)}s] Renderizando: {status_render}")
            
            if status_render.get("toastText"):
                print(f"  [AVISO/TOAST] Detectado: '{status_render['toastText']}'")

            # Aguarda pelo menos 20s para o início da geração
            if time.time() - inicio_t > 20 and not status_render.get("isGenerating"):
                print("Renderização concluída (barra de progresso encerrou)!")
                concluido = True
                break
            time.sleep(5)

        page.screenshot(path=str(DIAG_DIR / "1_2_passo5_render_concluida.png"))

        # -------------------------------------------------------------
        # PASSO 6: Baixar e Validar o MP4
        # -------------------------------------------------------------
        print("\n[PASSO 6] Baixando a mídia gerada...")
        tiles_finais = page.locator("flow-grid-tile-container, .virtual-item-container flow-grid-tile-container").all()
        print(f"Total tiles pós-geração: {len(tiles_finais)}")
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
        print(f"Botão de download visível: {btn_download.is_visible()}")
        
        caminho_video = DIAG_DIR / "1_2_video_gerado.mp4"
        with page.expect_download(timeout=30000) as dl_info:
            btn_download.click()
        
        dl = dl_info.value
        dl.save_as(str(caminho_video))
        print(f"\n[SUCESSO] Arquivo baixado em: {caminho_video}")
        print(f"Tamanho: {caminho_video.stat().st_size} bytes")

        with open(caminho_video, "rb") as f:
            header = f.read(32)
        print(f"Cabeçalho primeiros 32 bytes (hex): {header.hex()}")
        print(f"Cabeçalho ASCII: {header}")
        is_mp4 = b"ftyp" in header or b"moov" in header or b"isom" in header
        print(f"Validação de formato MP4: {is_mp4}")

if __name__ == "__main__":
    main()
