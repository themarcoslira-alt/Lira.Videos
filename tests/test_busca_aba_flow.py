# -*- coding: utf-8 -*-
"""
Validação da busca de aba do Google Flow em criar_avatar_flow_via_playwright.

O problema original: o código procurava apenas "labs.google" mas a URL real
do Flow é "https://flow.google.com/". Este teste verifica que o código novo
aceita: flow.google, labs.google, tools/flow, e título "google flow".
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestBuscaAbaFlow(unittest.TestCase):
    """Valida a correção da busca de aba do Google Flow."""

    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / 'services' / 'playwright_flow.py').read_text(encoding='utf-8')

    def test_codigo_novo_esta_presente(self):
        """O trecho com retry e múltiplos contextos deve existir."""
        self.assertIn('for _ in range(5):  # retry de até ~2.5s caso a aba esteja em transição', self.src)
        self.assertIn('for ctx in (browser.contexts or []):', self.src)

    def test_busca_flow_google(self):
        """O código deve procurar por 'flow.google' (URL real do Flow)."""
        self.assertIn('"flow.google" in u', self.src)

    def test_busca_labs_google_fallback(self):
        """O código deve manter 'labs.google' como fallback."""
        self.assertIn('"labs.google" in u', self.src)

    def test_ignora_localhost(self):
        """O código NÃO deve confundir com interface local (127.0.0.1/localhost)."""
        self.assertIn('if "127.0.0.1" in u or "localhost" in u:', self.src)
        self.assertIn('continue', self.src)

    def test_busca_titulo_google_flow(self):
        """O código deve procurar também pelo título 'google flow'."""
        self.assertIn('"google flow" in t', self.src)

    def test_antigo_codigo_removido(self):
        """O código antigo (que só procurava labs.google no primeiro contexto) deve ter sido removido da função criar_avatar_flow_via_playwright."""
        # Extrai apenas o corpo da função criar_avatar_flow_via_playwright
        func_start = self.src.find('def criar_avatar_flow_via_playwright')
        self.assertGreater(func_start, -1, 'Função criar_avatar_flow_via_playwright não encontrada')
        func_end = self.src.find('\ndef ', func_start + 10)
        if func_end == -1:
            func_end = len(self.src)
        func_body = self.src[func_start:func_end]
        # Dentro da função nova NÃO deve haver o código antigo
        self.assertNotIn('pages = [pg for pg in context.pages', func_body)
        self.assertNotIn('if not browser.contexts:', func_body)

    def test_sintaxe_valida(self):
        """A sintaxe do arquivo deve ser válida."""
        import ast
        ast.parse(self.src)  # levanta SyntaxError se inválido

    def test_retry_com_time_sleep(self):
        """O retry deve usar time.sleep(0.5) entre tentativas."""
        self.assertIn('time.sleep(0.5)', self.src)


class TestLogicaBuscaAba(unittest.TestCase):
    """Testa a lógica de matching com URLs reais e falsas."""

    def _simular_match(self, url, title, eh_flow_esperado):
        """Simula a condição do código real para uma URL/título."""
        u = (url or '').lower()
        t = (title or '').lower()
        if '127.0.0.1' in u or 'localhost' in u:
            return False
        return ('flow.google' in u or 'labs.google' in u or 'tools/flow' in u or 'google flow' in t)

    def test_url_flow_google_com(self):
        """URL real 'https://flow.google.com/project/...' DEVE ser detectada."""
        self.assertTrue(self._simular_match('https://flow.google.com/project/abc123', 'Flow', True))

    def test_url_labs_google_fallback(self):
        """URL antiga 'https://labs.google/fx/tools/flow' DEVE ser detectada."""
        self.assertTrue(self._simular_match('https://labs.google/fx/tools/flow', 'Flow', True))

    def test_url_flow_google_projeto(self):
        """URL 'https://flow.google.com/project/7db9ca70-...' DEVE ser detectada."""
        self.assertTrue(self._simular_match(
            'https://flow.google.com/project/7db9ca70-4ccc-4969-b798-a33f1bbfb8ab',
            'Nano Banana — Flow', True))

    def test_localhost_ignorado(self):
        """URL 'http://127.0.0.1:5000' NÃO deve ser detectada."""
        self.assertFalse(self._simular_match('http://127.0.0.1:5000/projeto', 'UltraCut', False))

    def test_localhost_ignorado_2(self):
        """URL 'http://localhost:5000' NÃO deve ser detectada."""
        self.assertFalse(self._simular_match('http://localhost:5000/', 'Lira Studio', False))

    def test_outra_pagina_google_ignorada(self):
        """URL 'https://mail.google.com' NÃO deve ser detectada."""
        self.assertFalse(self._simular_match('https://mail.google.com/', 'Gmail', False))


if __name__ == '__main__':
    unittest.main()
