# -*- coding: utf-8 -*-
"""
Validação da troca do botão principal "Personagem com Foto" →
"✨ Criar meu avatar" (dispara fluxo automático criar_personagem_flow).

Verifica:
1. HTML: botão da aba renomeado + botão criar_flow ATIVO + salvar como fallback
2. JS: listener do botão criar_flow presente e ativo
3. Backend: rota usa criar_avatar_flow_via_playwright (que chama criar_personagem_flow)
4. criar_personagem_flow NÃO foi alterada (assinatura preservada)
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _esta_dentro_de_comentario_html(html: str, elemento_id: str) -> bool:
    """Verifica se o elemento está dentro de um comentário HTML (<!-- ... -->)."""
    idx = html.find(elemento_id)
    if idx == -1:
        return True  # Não existe => com certeza não está ativo
    antes = html[:idx]
    ultimo_abre = antes.rfind('<!--')
    ultimo_fecha = antes.rfind('-->')
    return ultimo_abre > ultimo_fecha


class TestCriarMeuAvatar(unittest.TestCase):
    """Valida a troca do botão principal."""

    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
        cls.js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        cls.pw_src = (ROOT / 'services' / 'playwright_flow.py').read_text(encoding='utf-8')
        cls.api_src = (ROOT / 'services' / 'api_v2.py').read_text(encoding='utf-8')

    def test_botao_aba_renomeado(self):
        """O botão da aba deve dizer '✨ Criar meu avatar'."""
        self.assertIn('✨ Criar meu avatar', self.html)
        self.assertNotIn('📸 Personagem com Foto', self.html)

    def test_botao_criar_flow_ativo(self):
        """O botão btn-s2-criar-flow-personagem deve estar ATIVO (não comentado)."""
        self.assertFalse(
            _esta_dentro_de_comentario_html(self.html, 'btn-s2-criar-flow-personagem'),
            'Botão criar_flow ainda está comentado!',
        )
        self.assertIn('✨ Criar Meu Avatar no Google Flow', self.html)

    def test_botao_salvar_como_fallback(self):
        """O botão btn-s2-salvar-personagem deve continuar presente (fallback upload)."""
        self.assertIn('btn-s2-salvar-personagem', self.html)
        self.assertIn('📸 Salvar Foto (Upload Simples)', self.html)
        # Deve vir DEPOIS do botão principal (fallback = segundo nível)
        pos_criar = self.html.find('btn-s2-criar-flow-personagem')
        pos_salvar = self.html.find('btn-s2-salvar-personagem')
        self.assertGreater(pos_salvar, pos_criar, 'Botão salvar deve vir depois do criar')

    def test_js_listener_criar_flow_presente(self):
        """O JS deve ter o listener do btn-s2-criar-flow-personagem chamando a API."""
        self.assertIn('btn-s2-criar-flow-personagem").addEventListener("click"', self.js)
        self.assertIn('/personagem/${encodeURIComponent(S.projeto_id)}/criar_flow', self.js)

    def test_api_usa_novo_fluxo(self):
        """A rota /personagem/<id>/criar_flow deve usar criar_avatar_flow_via_playwright (assíncrono)."""
        self.assertIn('criar_avatar_flow_via_playwright', self.api_src)
        # A chamada real está dentro da função background _executar_criar_avatar_background
        self.assertIn('_executar_criar_avatar_background', self.api_src)
        self.assertIn('res_flow = criar_avatar_flow_via_playwright(', self.api_src)
        # O fluxo legado NÃO deve mais ser usado na rota principal de personagem
        self.assertNotIn('criar_personagem_no_flow_direto(projeto_id=projeto_id, nome=nome, imagem_abs=img_abs)', self.api_src)

    def test_criar_personagem_flow_intacta(self):
        """A função criar_personagem_flow NÃO foi alterada (assinatura e corpo preservados)."""
        self.assertIn(
            'def criar_personagem_flow(page, nome_personagem: str, caminho_foto: str) -> bool:',
            self.pw_src,
        )
        # A função de criação real NÃO pode ter sido modificada no corpo (passos 1-9 preservados)
        self.assertIn('9_validacao_popup', self.pw_src)
        self.assertIn('7_promover_portrait_oficial', self.pw_src)

    def test_nova_funcao_wrapper_existe(self):
        """A nova função wrapper criar_avatar_flow_via_playwright deve existir."""
        self.assertIn('def criar_avatar_flow_via_playwright(', self.pw_src)
        # Ela deve chamar criar_personagem_flow garantindo o nome com @ (nome_flow)
        self.assertIn('criado = criar_personagem_flow(page, nome_flow, str(imagem_abs))', self.pw_src)

    def test_disparo_flow_via_criar_avatar_flow_via_playwright(self):
        """Simula a chamada de criar_avatar_flow_via_playwright e confirma que ela dispara criar_personagem_flow."""
        from unittest.mock import patch, MagicMock
        from services.playwright_flow import criar_avatar_flow_via_playwright

        with patch('services.playwright_flow.ensure_chrome_cdp', return_value=(True, 'OK')), \
             patch('services.character_service.resolver_imagem_avatar_projeto', return_value='test.png'), \
             patch('pathlib.Path.exists', return_value=True), \
             patch('playwright.sync_api.sync_playwright') as mock_pw, \
             patch('services.playwright_flow.criar_personagem_flow', return_value=True) as mock_criar_flow, \
             patch('services.character_service.salvar_identidade_projeto'), \
             patch('services.character_service.atualizar_status_flow_personagem'), \
             patch('services.character_service.obter_identidade_projeto', return_value={'nome': 'Marcos'}):

            mock_browser = MagicMock()
            mock_context = MagicMock()
            mock_page = MagicMock()
            mock_page.url = 'https://flow.google.com/project/abc-123'
            mock_context.pages = [mock_page]
            mock_browser.contexts = [mock_context]
            mock_pw.return_value.__enter__.return_value.chromium.connect_over_cdp.return_value = mock_browser

            res = criar_avatar_flow_via_playwright(projeto_id='proj_test', nome='Marcos', imagem_abs='c:/test.png')
            self.assertTrue(res.get('success'))
            mock_criar_flow.assert_called_once_with(mock_page, '@Marcos', 'c:/test.png')

    def test_abertura_automatica_aba_flow_quando_nao_encontrada(self):
        """Quando nenhuma aba do Flow existe, abre automaticamente nova aba em https://flow.google.com/ com 30s timeout."""
        from unittest.mock import patch, MagicMock
        from services.playwright_flow import criar_avatar_flow_via_playwright

        with patch('services.playwright_flow.ensure_chrome_cdp', return_value=(True, 'OK')), \
             patch('services.character_service.resolver_imagem_avatar_projeto', return_value='test.png'), \
             patch('pathlib.Path.exists', return_value=True), \
             patch('playwright.sync_api.sync_playwright') as mock_pw, \
             patch('services.playwright_flow.criar_personagem_flow', return_value=True) as mock_criar_flow, \
             patch('services.character_service.salvar_identidade_projeto'), \
             patch('services.character_service.atualizar_status_flow_personagem'), \
             patch('services.character_service.obter_identidade_projeto', return_value={'nome': 'Marcos'}):

            mock_browser = MagicMock()
            mock_context = MagicMock()
            # Inicialmente contexts.pages NÃO tem aba do Flow (só tem uma aba qualquer do localhost)
            mock_existing_page = MagicMock()
            mock_existing_page.url = 'http://127.0.0.1:5000/'
            mock_context.pages = [mock_existing_page]
            mock_browser.contexts = [mock_context]

            # Quando chama new_page(), retorna a nova aba do Flow
            mock_new_page = MagicMock()
            mock_new_page.url = 'https://flow.google.com/project/uuid-456'
            mock_context.new_page.return_value = mock_new_page

            mock_pw.return_value.__enter__.return_value.chromium.connect_over_cdp.return_value = mock_browser

            res = criar_avatar_flow_via_playwright(projeto_id='proj_test', nome='Marcos', imagem_abs='c:/test.png')
            self.assertTrue(res.get('success'))
            # Confirma que context.new_page() foi chamado para abrir nova aba
            mock_context.new_page.assert_called_once()
            # Confirma navegação para https://flow.google.com/ com timeout de 30000ms
            mock_new_page.goto.assert_called_with('https://flow.google.com/', timeout=30000)
            mock_new_page.wait_for_load_state.assert_called_with('domcontentloaded', timeout=30000)
            mock_criar_flow.assert_called_once_with(mock_new_page, '@Marcos', 'c:/test.png')

    def test_api_endpoint_criar_flow_dispara_playwright(self):
        """Dispara POST /api/v2/personagem/<id>/criar_flow e confirma resposta ASSÍNCRONA (iniciado)."""
        from unittest.mock import patch
        import io
        from app_web import app

        # Mocka a thread para evitar automação real
        with patch('services.api_v2.threading.Thread') as mock_thread:
            client = app.test_client()
            data = {
                'nome': 'Marcos',
                'estilo_visual': 'photorealistic_cinematic',
                'imagem': (io.BytesIO(b'fake_image_data'), 'foto.png')
            }
            resp = client.post('/api/v2/personagem/projeto_teste/criar_flow', data=data, content_type='multipart/form-data')
            self.assertEqual(resp.status_code, 200)
            body = resp.get_json()
            # Resposta assíncrona: NÃO espera o resultado, retorna "iniciado"
            self.assertEqual(body.get('status'), 'iniciado')
            # Thread background foi iniciada
            mock_thread.assert_called_once()
            args, kwargs = mock_thread.call_args
            self.assertEqual(kwargs.get('args')[0], 'projeto_teste')  # projeto_id
            self.assertEqual(kwargs.get('args')[1], 'Marcos')  # nome


if __name__ == '__main__':
    unittest.main()
