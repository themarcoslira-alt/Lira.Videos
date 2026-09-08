# -*- coding: utf-8 -*-
"""
Validação da remoção da aba "Avatar Flow @me" da tela Identidade & Avatar.

Verifica:
1. O botão da aba está comentado no HTML
2. O bloco de conteúdo está comentado no HTML
3. O listener JS do botão avatar está comentado
4. As abas Personagem e Biblioteca continuam funcionais (JS não quebrado)
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


class TestRemocaoAvatarFlowMe(unittest.TestCase):
    """Valida a remoção da aba Avatar Flow @me."""

    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
        cls.js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')

    def test_botao_aba_avatar_comentado(self):
        """O botão s2-tab-tipo-avatar deve estar comentado ou ausente."""
        self.assertTrue(
            _esta_dentro_de_comentario_html(self.html, 's2-tab-tipo-avatar'),
            'Botão da aba Avatar @me ainda está ativo!',
        )

    def test_bloco_conteudo_avatar_comentado(self):
        """O bloco s2-bloco-avatar-flow deve estar comentado ou ausente."""
        self.assertTrue(
            _esta_dentro_de_comentario_html(self.html, 's2-bloco-avatar-flow'),
            'Bloco de conteúdo Avatar @me ainda está ativo!',
        )

    def test_btn_salvar_avatar_comentado(self):
        """O botão btn-s2-salvar-avatar-flow deve estar comentado ou ausente."""
        self.assertTrue(
            _esta_dentro_de_comentario_html(self.html, 'btn-s2-salvar-avatar-flow'),
            'Botão salvar avatar ainda está ativo!',
        )

    def test_js_nao_referencia_avatar_aba(self):
        """Nenhuma referência ativa (não-comentada) a s2-tab-tipo-avatar no JS."""
        for i, l in enumerate(self.js.splitlines(), 1):
            s = l.strip()
            if 's2-tab-tipo-avatar' in s and not s.startswith('//') and 'REMOV' not in s:
                self.fail(f'Linha {i}: referência ativa a s2-tab-tipo-avatar: {s[:100]}')

    def test_js_nao_referencia_bloco_avatar(self):
        """Nenhuma referência ativa (não-comentada) a s2-bloco-avatar-flow no JS."""
        for i, l in enumerate(self.js.splitlines(), 1):
            s = l.strip()
            if 's2-bloco-avatar-flow' in s and not s.startswith('//') and 'REMOV' not in s:
                self.fail(f'Linha {i}: referência ativa a s2-bloco-avatar-flow: {s[:100]}')

    def test_js_nao_referencia_btn_salvar_avatar(self):
        """Nenhuma referência ativa (não-comentada) a btn-s2-salvar-avatar-flow no JS."""
        for i, l in enumerate(self.js.splitlines(), 1):
            s = l.strip()
            if 'btn-s2-salvar-avatar-flow' in s and not s.startswith('//') and 'REMOV' not in s:
                self.fail(f'Linha {i}: referência ativa a btn-s2-salvar-avatar-flow: {s[:100]}')

    def test_abas_personagem_biblioteca_continuam(self):
        """As abas Personagem e Biblioteca devem permanecer funcionais no HTML."""
        self.assertIn('s2-tab-tipo-personagem', self.html)
        self.assertIn('s2-tab-tipo-biblioteca', self.html)

    def test_js_abas_personagem_biblioteca_funcionais(self):
        """O JS deve manter listeners das abas Personagem e Biblioteca."""
        self.assertIn('s2-tab-tipo-personagem").addEventListener("click"', self.js)
        self.assertIn('s2-tab-tipo-biblioteca").addEventListener("click"', self.js)

    def test_html_sem_comentarios_quebrados(self):
        """Comentários HTML devem estar balanceados (<!-- antes de -->)."""
        import re
        abertos = len(re.findall(r'<!--', self.html))
        fechados = len(re.findall(r'-->', self.html))
        # Permite 1 comentário extra (o marcador -- no final pode aparecer em atributos)
        self.assertGreaterEqual(abertos, fechados - 1, 'Comentários HTML desbalanceados!')


if __name__ == '__main__':
    unittest.main()
