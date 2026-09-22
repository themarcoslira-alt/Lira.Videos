import json
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')
DIAG_DIR = Path(r"C:\Lira Videos\_diagnostico")

def main():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://[::1]:9222")
        context = browser.contexts[0]
        page = [pg for pg in context.pages if "/project/" in pg.url][0]
        print(f"Página conectada: {page.url}")

        # Passo 1: Abrir configurações
        print("\n[PASSO 1] Abrindo painel de configurações...")
        btn_cfg = page.locator("button.settings-trigger-button").first
        btn_cfg.click()
        page.wait_for_timeout(600)

        # Passo 1.1: Garantir modo Vídeo
        tab_video = page.locator('flow-prompt-box-settings button[role="radio"]:has-text("Vídeo")').first
        if tab_video.get_attribute("aria-checked") != "true":
            print("Selecionando modo Vídeo...")
            tab_video.click()
            page.wait_for_timeout(400)
        else:
            print("Modo Vídeo já selecionado.")

        # Passo 1.2: Garantir Proporção 16:9
        prop_16_9 = page.locator('flow-prompt-box-settings flow-toggles[aria-label="Proporção"] button[role="radio"]:has-text("16:9")').first
        if prop_16_9.get_attribute("aria-checked") != "true":
            print("Selecionando 16:9...")
            prop_16_9.click()
            page.wait_for_timeout(300)

        # Passo 1.3: Selecionar Modelo Veo 3.1 - Lite
        model_trigger = page.locator('flow-prompt-box-settings button[aria-label="Selecionar família de modelos"]').first
        texto_modelo_atual = model_trigger.inner_text().strip().replace("\n", " ")
        print(f"Modelo atualmente ativo: '{texto_modelo_atual}'")
        if "veo 3.1 - lite" not in texto_modelo_atual.lower():
            print("Abrindo dropdown de modelos...")
            model_trigger.click()
            page.wait_for_timeout(600)
            
            veo_option = page.locator('.cdk-overlay-pane [role="menuitem"]:has-text("Veo 3.1 - Lite")').first
            print(f"Opção Veo 3.1 - Lite visível: {veo_option.is_visible()}")
            veo_option.click()
            page.wait_for_timeout(600)
            texto_modelo_novo = model_trigger.inner_text().strip().replace("\n", " ")
            print(f"Modelo após clique: '{texto_modelo_novo}'")
        else:
            print("Veo 3.1 - Lite já está selecionado.")

        # Passo 1.4: Quantidade x1
        qty_1 = page.locator('flow-prompt-box-settings flow-toggles[aria-label="Quantidade de resultados"] button[role="radio"]:has-text("x1")').first
        if qty_1.get_attribute("aria-checked") != "true":
            qty_1.click()
            page.wait_for_timeout(300)

        # Fecha configurações com Escape
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        page.screenshot(path=str(DIAG_DIR / "1_2_configuracao_video_pronta.png"))
        print(f"Resumo da barra de configurações: '{btn_cfg.inner_text().strip().replace(chr(10), ' ')}'")

        # Passo 2: Menu do tile -> 'Incluir no comando'
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
        page.screenshot(path=str(DIAG_DIR / "1_2_apos_incluir_no_comando.png"))

        # Passo 3: Inspecionar o Prompt Box para confirmar a foto fixada
        print("\n[PASSO 3] Verificando se a foto foi ancorada como chip de imagem...")
        chips = page.locator("flow-image-ingredient-chip, .ingredient-bar-container, flow-ingredient-bar, [class*='ingredient-chip'], img.chip-image, [data-type='image']").all()
        print(f"Total chips de ingredientes encontrados: {len(chips)}")
        for c in chips:
            print(f"  Chip: tag={c.evaluate('el => el.tagName')} class='{c.get_attribute('class')}'")

        # Inspeciona HTML do editor
        editor = page.locator('flow-rich-text-editor, div.ProseMirror[contenteditable="true"]').first
        print(f"Editor visível: {editor.is_visible()}")
        
        # Passo 4: Digitar prompt de movimento
        print("\n[PASSO 4] Digitando prompt de movimento...")
        editor.click()
        page.wait_for_timeout(200)
        prompt_movimento = "Smooth slow cinematic camera pan, natural gentle garden movement, 4s duration."
        page.keyboard.type(prompt_movimento, delay=30)
        page.wait_for_timeout(500)
        page.screenshot(path=str(DIAG_DIR / "1_2_prompt_digitado.png"))

        # Passo 5: Localizar botão de Criar / Enviar
        print("\n[PASSO 5] Localizando botão de envio...")
        submit_btn = page.locator('button.run-button, button[aria-label*="gerar" i], button[aria-label*="criar" i], button[aria-label*="generate" i], button:has(mat-icon:has-text("arrow_upward")), button:has(mat-icon:has-text("play_arrow"))').first
        print(f"Botão de envio encontrado: aria='{submit_btn.get_attribute('aria-label')}' class='{submit_btn.get_attribute('class')}'")

        # Verificação do custo de créditos antes de enviar
        custo_info = page.evaluate("""() => {
            const el = document.querySelector('.settings-credit-cost, .credit-cost-label, flow-credit-cost-label');
            return el ? el.innerText.trim() : 'não encontrado';
        }""")
        print(f"Custo informado na interface: '{custo_info}'")

        # Registra todos os passos até aqui
        print("\n[PRONTO PARA GERAR 1 VÍDEO]")
        print("Clique no botão de envio para gerar 1 MP4 de validação...")
        submit_btn.click()
        page.wait_for_timeout(3000)
        page.screenshot(path=str(DIAG_DIR / "1_2_apos_envio_gerando.png"))

        # Monitora a geração
        print("\n[PASSO 6] Monitorando progresso da geração no Flow...")
        inicio_gen = time.time()
        sucesso_video = False
        while time.time() - inicio_gen < 180:
            # Verifica cards / tiles
            status_text = page.evaluate("""() => {
                const prog = document.querySelector('.progress-bar, [role="progressbar"], .generating, .loading');
                const badge = document.querySelector('.status-badge, .badge');
                return {
                    hasProgress: !!prog,
                    progText: prog ? prog.innerText : '',
                    badgeText: badge ? badge.innerText : ''
                };
            }""")
            print(f"  [{int(time.time() - inicio_gen)}s] Status: {status_text}")
            time.sleep(5)
            
            # Checa se o primeiro tile terminou de renderizar e tem botão de download
            novo_tile = page.locator("flow-grid-tile-container, .virtual-item-container flow-grid-tile-container").first
            is_loading = novo_tile.locator(".loading, .spinner, [role='progressbar'], mat-progress-bar").count() > 0
            if not is_loading and (time.time() - inicio_gen > 15):
                print(f"Geração parece concluída em {int(time.time() - inicio_gen)}s!")
                sucesso_video = True
                break

        page.screenshot(path=str(DIAG_DIR / "1_2_resultado_geracao.png"))

        # Passo 7: Baixar o MP4 gerado
        print("\n[PASSO 7] Baixando a mídia gerada...")
        # Abre menu do tile recém gerado
        novo_tile = page.locator("flow-grid-tile-container, .virtual-item-container flow-grid-tile-container").first
        novo_tile.hover()
        page.wait_for_timeout(500)
        btn_opt = novo_tile.locator("button.mat-mdc-menu-trigger, button[aria-label*='Mais' i]").first
        if btn_opt.is_visible(timeout=1000):
            btn_opt.click()
        else:
            novo_tile.click(button="right")
        page.wait_for_timeout(600)

        btn_download = page.locator('.cdk-overlay-pane [role="menuitem"]:has-text("Fazer o download")').first
        print(f"Botão 'Fazer o download' visível: {btn_download.is_visible()}")
        
        caminho_mp4 = DIAG_DIR / "1_2_video_gerado.mp4"
        with page.expect_download(timeout=30000) as download_info:
            btn_download.click()
        
        download = download_info.value
        download.save_as(str(caminho_mp4))
        print(f"Arquivo salvo com sucesso em: {caminho_mp4}")
        print(f"Tamanho do arquivo: {caminho_mp4.stat().st_size} bytes")

        # Validação do arquivo
        with open(caminho_mp4, "rb") as f:
            header = f.read(16)
        print(f"Header primeiros 16 bytes: {header.hex()}")
        is_mp4 = b"ftyp" in header or b"moov" in header or caminho_mp4.stat().st_size > 50000
        print(f"Confirmação de MP4: {is_mp4}")

if __name__ == "__main__":
    main()
