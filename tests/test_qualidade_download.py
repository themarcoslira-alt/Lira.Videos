# -*- coding: utf-8 -*-
"""
Teste da configuração "Qualidade download" (1K/2K).

Valida:
1. Rota config aceita e persiste prod_qualidade_download
2. playwright_flow lê o campo do meta.json
3. Frontend (HTML) expõe dropdown Qualidade download com opções 1K/2K
4. app.js envia o campo ao salvar
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestQualidadeDownloadConfig(unittest.TestCase):
    """Valida o campo prod_qualidade_download no backend."""

    def test_rota_config_aceita_campo(self):
        """A rota config deve aceitar prod_qualidade_download no POST."""
        src = (ROOT / 'services' / 'api_v2.py').read_text(encoding='utf-8')
        self.assertIn('"prod_qualidade_download"', src)

    def test_playwright_le_campo(self):
        """O playwright_flow deve ler prod_qualidade_download do meta.json."""
        src = (ROOT / 'services' / 'playwright_flow.py').read_text(encoding='utf-8')
        self.assertIn('"prod_qualidade_download"', src)
        self.assertIn('current_download_quality', src)

    def test_html_tem_dropdown(self):
        """O HTML deve expor o dropdown Qualidade download."""
        html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('s2-prod-qualidade-download', html)
        self.assertIn('1K (Original)', html)
        self.assertIn('2K (Upscaled)', html)

    def test_js_envia_campo(self):
        """O app.js deve enviar prod_qualidade_download ao salvar."""
        js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('prod_qualidade_download', js)
        self.assertIn('s2-prod-qualidade-download', js)

    def test_js_le_campo_ao_carregar(self):
        """O app.js deve carregar o valor salvo ao abrir o projeto."""
        js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('m.prod_qualidade_download', js)

    def test_playwright_has_upscale_2k(self):
        """O playwright_flow deve ter a função de upscale 2K."""
        src = (ROOT / 'services' / 'playwright_flow.py').read_text(encoding='utf-8')
        self.assertIn('def _tentar_upscale_2k', src)
        self.assertIn('UPSCALE_2K_OK', src)
        self.assertIn('current_download_quality', src)

    def test_playwright_aplica_upscale_no_polling(self):
        """O polling deve chamar o upscale quando qualidade==2K."""
        src = (ROOT / 'services' / 'playwright_flow.py').read_text(encoding='utf-8')
        self.assertIn('== "2K"', src)
        self.assertIn('_upscale_tentado_cena', src)

    def test_default_e_1k(self):
        """O default deve ser '1K' no __init__ do worker."""
        src = (ROOT / 'services' / 'playwright_flow.py').read_text(encoding='utf-8')
        self.assertIn('current_download_quality: str = "1K"', src)

    def test_rota_config_aceita_campo_video(self):
        """A rota config deve aceitar prod_qualidade_download_video no POST."""
        src = (ROOT / 'services' / 'api_v2.py').read_text(encoding='utf-8')
        self.assertIn('"prod_qualidade_download_video"', src)

    def test_playwright_le_campo_video(self):
        """O playwright_flow deve ler prod_qualidade_download_video do meta.json."""
        src = (ROOT / 'services' / 'playwright_flow.py').read_text(encoding='utf-8')
        self.assertIn('"prod_qualidade_download_video"', src)
        self.assertIn('current_download_quality_video', src)

    def test_html_tem_dropdown_video(self):
        """O HTML deve expor o dropdown Qualidade download (vídeo)."""
        html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('s2-prod-qualidade-download-video', html)
        self.assertIn('1080p (Aprimorada)', html)
        self.assertIn('720p (Original)', html)

    def test_js_envia_campo_video(self):
        """O app.js deve enviar prod_qualidade_download_video ao salvar."""
        js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('prod_qualidade_download_video', js)
        self.assertIn('s2-prod-qualidade-download-video', js)

    def test_js_le_campo_ao_carregar_video(self):
        """O app.js deve carregar o valor salvo de prod_qualidade_download_video."""
        js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('m.prod_qualidade_download_video', js)

    def test_playwright_has_download_1080p_menu(self):
        """O playwright_flow deve ter o método _baixar_video_1080p_menu com expect_download."""
        src = (ROOT / 'services' / 'playwright_flow.py').read_text(encoding='utf-8')
        self.assertIn('def _baixar_video_1080p_menu', src)
        self.assertIn('expect_download', src)
        self.assertIn('DOWNLOAD_1080P_OK', src)

    def test_default_video_e_1080p(self):
        """O default de vídeo deve ser '1080p' no __init__ do worker."""
        src = (ROOT / 'services' / 'playwright_flow.py').read_text(encoding='utf-8')
        self.assertIn('current_download_quality_video: str = "1080p"', src)

    def test_playwright_has_failure_card_detection(self):
        """O playwright_flow deve ter detecção imediata de cards de falha no canvas."""
        src = (ROOT / 'services' / 'playwright_flow.py').read_text(encoding='utf-8')
        self.assertIn('def _marcar_cards_falha_existentes', src)
        self.assertIn('def _detectar_novo_card_falha', src)
        self.assertIn('def _acionar_retry_card_falha', src)
        self.assertIn('FLOW_FALHA', src)
        self.assertIn('não foi possível gerar', src)


if __name__ == '__main__':
    unittest.main()

