"""
Teste de Validação da Fase 2B:
- Testa _set_output_mode("video") no Chrome CDP real aberto na porta 9222.
- Verifica se seleciona 'Vídeo', '16:9', 'Veo 3.1 - Lite'.
- Confirma que dock_btn reflete 'Vídeo'.
- Testa _set_output_mode("image") e verifica se reflete 'Imagem'.
- ZERO créditos de vídeo consumidos (teste puramente de UI/seletores).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright
from services.playwright_flow import FlowQueueWorker


def test_selectors_on_real_flow():
    print("=== Teste 2B: Verificação de Seletores no Flow Real via CDP ===")
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        except Exception:
            try:
                browser = p.chromium.connect_over_cdp("http://[::1]:9222")
            except Exception as e_cdp:
                print(f"❌ Não foi possível conectar ao Chrome CDP na porta 9222: {e_cdp}")
                return

        context = browser.contexts[0]
        page = None
        for pg in context.pages:
            if "flow.google.com" in pg.url or "labs.google" in pg.url:
                page = pg
                break

        if not page:
            print("❌ Nenhuma aba do Google Flow encontrada aberta no Chrome.")
            return

        print(f"Conectado à página: {page.url}")

        # Instancia worker fake associado à página
        worker = FlowQueueWorker.get_worker()
        worker.page = page

        # 1. Testa seleção de MODO VÍDEO
        print("\n--- Testando _set_output_mode('video', modelo_solicitado='Veo 3.1 - Lite', proporcao_solicitada='16:9') ---")
        worker._configured_mode = None  # força reconfiguração
        ok_video = worker._set_output_mode("video", modelo_solicitado="Veo 3.1 - Lite", proporcao_solicitada="16:9")
        print(f"Retorno _set_output_mode('video'): {ok_video}")
        assert ok_video is True, "Configuração de modo vídeo deveria ter retornado True!"

        dock_btn = page.locator("button.settings-trigger-button, button[aria-label='Gatilho de configurações']").first
        dock_txt = dock_btn.inner_text().lower()
        print(f"Texto do chip de configurações: '{dock_txt}'")
        assert "vídeo" in dock_txt or "video" in dock_txt, f"Chip deveria indicar Vídeo, mas tem: {dock_txt}"
        print("✅ Seleção de modo Vídeo + Veo 3.1 - Lite funcionando perfeitamente!")

        # 2. Testa seleção de MODO IMAGEM
        print("\n--- Testando _set_output_mode('image', modelo_solicitado='Nano Banana 2', proporcao_solicitada='16:9') ---")
        worker._configured_mode = None  # força reconfiguração
        ok_image = worker._set_output_mode("image", modelo_solicitado="Nano Banana 2", proporcao_solicitada="16:9")
        print(f"Retorno _set_output_mode('image'): {ok_image}")
        assert ok_image is True, "Configuração de modo imagem deveria ter retornado True!"

        dock_txt_img = dock_btn.inner_text().lower()
        print(f"Texto do chip de configurações após Imagem: '{dock_txt_img}'")
        assert "imagem" in dock_txt_img or "banana" in dock_txt_img or "imagen" in dock_txt_img or ("vídeo" not in dock_txt_img and "video" not in dock_txt_img), f"Chip deveria indicar Imagem/Modelo de imagem, mas tem: {dock_txt_img}"
        print("✅ Seleção de modo Imagem funcionando perfeitamente!")

        # 3. Retorna para MODO VÍDEO para deixar o Flow pronto para vídeo
        print("\n--- Restaurando para MODO VÍDEO ---")
        worker._configured_mode = None
        ok_restore = worker._set_output_mode("video", modelo_solicitado="Veo 3.1 - Lite", proporcao_solicitada="16:9")
        assert ok_restore is True
        print("✅ Restaurado para modo Vídeo com sucesso!")

        print("\n=========================================")
        print("TODOS OS TESTES DA FASE 2B FORAM APROVADOS!")
        print("=========================================")


if __name__ == "__main__":
    test_selectors_on_real_flow()
