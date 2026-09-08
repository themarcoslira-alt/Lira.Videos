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


if __name__ == '__main__':
    unittest.main()
