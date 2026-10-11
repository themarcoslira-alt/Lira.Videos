# -*- coding: utf-8 -*-
"""
tests/test_personagem_popover_biblioteca.py — Bug 1 (ANTIGRAVITY)
================================================================
Correção da verificação de personagem na biblioteca do Flow:

1. O botão amplo "Adicionar" casava com o MENU DE UPLOAD ("Menu para adicionar
   arquivos"); o overlay era aceito como dialog e os 4 itens de menu contavam
   como "cards estáveis" => False indevido (e o pré-voo recriava @Marcos).
2. Agora o menu de upload é DESCARTADO (Escape) e o fallback que digita '@' no
   prompt editor (contraprova no Flow real) abre o popover correto.
3. `_aguardar_lista_personagens_estavel` só conta cards dentro do popover REAL
   (flow-add-menu-popover-content / .asset-title / abas Personagens): itens
   button[role="menuitem"] de menu de upload não contam; sem popover válido o
   resultado é "inconclusivo" (=> None), nunca False.

Validação: stubs de página/dialog ("validado via simulação"). O Bug 1 só fecha
em execução real quando o projeto atual (@Marcos já na conta) logar
PERSONAGEM_JA_NA_BIBLIOTECA sem PERSONAGEM_CRIADO_OK.
"""
import unittest
from unittest import mock
from unittest.mock import MagicMock

import services.playwright_flow as pf


class _StubLoc:
    """Locator mínimo data-driven para o dialog stub (count/is_visible)."""

    def __init__(self, dono, *, count=0, visible=False):
        self.dono = dono
        self._count = count
        self._visible = visible

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def is_visible(self, timeout=None):
        return self._visible

    def locator(self, sel):
        return self.dono.locator(str(sel))


class _DialogBibliotecaStub:
    """Dialog data-driven para `_aguardar_lista_personagens_estavel`."""

    def __init__(self, *, popover_raiz=0, tabs=0, cards_popover=0, cards_local=0,
                 nome="", nome_visivel=False, menu_upload=0, loader=False):
        self.popover_raiz = popover_raiz
        self.tabs = tabs
        self.cards_popover = cards_popover
        self.cards_local = cards_local
        self.nome = nome
        self.nome_visivel = nome_visivel
        self.menu_upload = menu_upload
        self.loader = loader

    def locator(self, sel):
        s = str(sel)
        if s == pf.SEL_POPOVER_PERSONAGENS_RAIZ:
            return _StubLoc(self, count=self.popover_raiz)
        if s == pf.SEL_CARDS_POPOVER_PERSONAGENS:
            return _StubLoc(self, count=self.cards_popover)
        if s == pf.SEL_CARDS_LOCAL_POPOVER_PERSONAGENS:
            return _StubLoc(self, count=self.cards_local)
        if s == pf.SEL_TABS_POPOVER_PERSONAGENS:
            return _StubLoc(self, count=self.tabs)
        if s == '.asset-title, .asset-item':
            return _StubLoc(self, count=self.cards_local)
        if 'menuitem' in s:
            return _StubLoc(self, count=self.menu_upload)
        if self.loader and any(t in s for t in
                               ('spinner', 'progress', 'Carregando', 'Loading', 'aria-busy')):
            return _StubLoc(self, visible=True)
        if self.nome and f'"{self.nome}"' in s:
            return _StubLoc(self, count=1 if self.nome_visivel else 0,
                            visible=self.nome_visivel)
        return _StubLoc(self)


class TestAguardarListaEndurecida(unittest.TestCase):
    """TAREFA 3 — só a lista VÁLIDA (popover real) conta; senão inconclusivo."""

    def _worker(self):
        w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
        w.page = MagicMock()  # wait_for_timeout instantâneo
        return w

    def test_menu_upload_nunca_conta_como_cards_retorna_inconclusivo(self):
        # Antes: contagem via sel_cards incluía button[role="menuitem"] => "estavel".
        d = _DialogBibliotecaStub(nome="Marcos", menu_upload=4)
        r = self._worker()._aguardar_lista_personagens_estavel(d, "Marcos", timeout_ms=120)
        self.assertEqual(r, "inconclusivo")

    def test_overlay_sem_sinais_retorna_inconclusivo(self):
        d = _DialogBibliotecaStub(nome="Marcos")
        r = self._worker()._aguardar_lista_personagens_estavel(d, "Marcos", timeout_ms=120)
        self.assertEqual(r, "inconclusivo")

    def test_popover_valido_com_nome_retorna_encontrado(self):
        d = _DialogBibliotecaStub(popover_raiz=1, nome="Marcos", nome_visivel=True)
        r = self._worker()._aguardar_lista_personagens_estavel(d, "Marcos", timeout_ms=120)
        self.assertEqual(r, "encontrado")

    def test_popover_valido_sem_nome_retorna_false_estavel(self):
        d = _DialogBibliotecaStub(popover_raiz=1, cards_popover=2)
        r = self._worker()._aguardar_lista_personagens_estavel(d, "Marcos", timeout_ms=300)
        self.assertEqual(r, "estavel")

    def test_popover_valido_vazio_retorna_vazio(self):
        d = _DialogBibliotecaStub(popover_raiz=1, cards_popover=0)
        r = self._worker()._aguardar_lista_personagens_estavel(d, "Marcos", timeout_ms=120)
        self.assertEqual(r, "vazio")

    def test_popover_carregando_retorna_carregando(self):
        d = _DialogBibliotecaStub(popover_raiz=1, cards_popover=2, loader=True)
        r = self._worker()._aguardar_lista_personagens_estavel(d, "Marcos", timeout_ms=120)
        self.assertEqual(r, "carregando")

    def test_dialog_e_o_proprio_popover_por_abas(self):
        # Dialog = próprio conteúdo do popover (sem filho flow-add-menu-...):
        # valida pelas abas Personagens/Characters e conta cards locais.
        d = _DialogBibliotecaStub(tabs=1, cards_local=2)
        r = self._worker()._aguardar_lista_personagens_estavel(d, "Marcos", timeout_ms=300)
        self.assertEqual(r, "estavel")


class _FakeKeyboard:
    def __init__(self, page):
        self.page = page

    def press(self, key, **kw):
        self.page.pressed.append(key)

    def type(self, text, **kw):
        self.page.typed.append(text)


class _LocPagina:
    """Locator da página stub (count/is_visible/locator/evaluate/click)."""

    def __init__(self, page, *, visible=False, count=0, origem=""):
        self.page = page
        self._visible = visible
        self._count = count
        self.origem = origem

    @property
    def first(self):
        return self

    def is_visible(self, timeout=None):
        return self._visible

    def count(self):
        return self._count

    def click(self, **kw):
        self.page.cliques.append(self.origem)

    def focus(self):
        return None

    def evaluate(self, js=None):
        return None

    def locator(self, sel):
        return self.page.probe(self.origem, str(sel))


class _FakePage:
    """Página do Flow stub, dirigida pelo cenário do teste.

    * botao_ingredientes: botão "+" específico visível (clique abre o overlay);
    * menu_upload_visivel: ao clicar, abre o MENU DE UPLOAD (role=menu com 4 itens);
    * popover_visivel / popover_apos_arroba: popover REAL visível (direto ou só
      depois de digitar '@' — contraprova do Flow real).
    """

    def __init__(self, *, botao_ingredientes=False, menu_upload_visivel=False,
                 popover_visivel=False, popover_apos_arroba=False,
                 popover_tem_tabs=True):
        self.botao_ingredientes = botao_ingredientes
        self.menu_upload_visivel = menu_upload_visivel
        self.popover_visivel = popover_visivel
        self.popover_apos_arroba = popover_apos_arroba
        self.popover_tem_tabs = popover_tem_tabs
        self.pressed = []
        self.typed = []
        self.cliques = []
        self.keyboard = _FakeKeyboard(self)

    def wait_for_timeout(self, ms):
        return None

    def _popover_aberto(self):
        return self.popover_visivel or (self.popover_apos_arroba and '@' in self.typed)

    def locator(self, sel):
        s = str(sel)
        if 'contenteditable' in s or 'ProseMirror' in s:
            return _LocPagina(self, visible=True, origem='editor')
        if 'Apagar comando' in s or 'clear-button' in s:
            return _LocPagina(self, visible=False, origem='limpar')
        if ('add-menu-trigger' in s or 'Adicionar elementos' in s
                or 'Add ingredients' in s or 'ingredient' in s or 'elemento' in s):
            return _LocPagina(self, visible=self.botao_ingredientes,
                              origem='btn_ingredientes')
        if s == 'flow-add-menu-popover-content':
            return _LocPagina(self, visible=self._popover_aberto(), origem='popover')
        if s == '.add-menu-popover-container':
            return _LocPagina(self, visible=False, origem='container')
        if 'cdk-overlay-pane' in s:
            if self._popover_aberto():
                return _LocPagina(self, visible=False, origem='overlay_generico')
            vis = self.menu_upload_visivel and 'btn_ingredientes' in self.cliques
            return _LocPagina(self, visible=vis, origem='overlay_generico')
        if s == 'div[role="dialog"]':
            return _LocPagina(self, visible=False, origem='dialog')
        return _LocPagina(self, visible=False, origem='outro')

    def probe(self, origem, sel):
        """Probes feitos DENTRO de um locator (validação de overlay/abas)."""
        if origem == 'popover':
            if sel in (pf.SEL_POPOVER_PERSONAGENS, pf.SEL_TABS_POPOVER_PERSONAGENS):
                return _LocPagina(self, count=1 if self.popover_tem_tabs else 0)
            if sel == pf.SEL_POPOVER_PERSONAGENS_RAIZ:
                return _LocPagina(self, count=0)
        if origem == 'overlay_generico':
            if sel == pf.SEL_POPOVER_PERSONAGENS:
                return _LocPagina(self, count=0)
            if sel == pf.SEL_ITENS_MENU_UPLOAD:
                return _LocPagina(self, count=4 if self.menu_upload_visivel else 0)
            if sel in (pf.SEL_POPOVER_PERSONAGENS_RAIZ, pf.SEL_TABS_POPOVER_PERSONAGENS):
                return _LocPagina(self, count=0)
        return _LocPagina(self, count=0)


class TestVerificarPopoverAbertura(unittest.TestCase):
    """TAREFA 2 — o menu de upload nunca vira dialog; o fallback '@' é o caminho real."""

    def _worker(self, page):
        w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
        w.page = page
        w.current_project_id = None
        w._limpar_overlays_e_backdrops = MagicMock()
        w._safe_click = MagicMock()
        return w

    def test_a_menu_upload_rejeitado_e_fallback_arroba_assume(self):
        """(a) menu de upload aberto -> não aceito; Escape + fallback '@' -> popover."""
        page = _FakePage(botao_ingredientes=True, menu_upload_visivel=True,
                         popover_apos_arroba=True)
        w = self._worker(page)
        w._aguardar_lista_personagens_estavel = MagicMock(return_value="encontrado")
        with mock.patch.object(pf, "pw_log"):
            res = w._verificar_personagem_na_biblioteca("Marcos")
        self.assertTrue(res)
        self.assertIn("@", page.typed, "fallback '@' deve ter sido digitado")
        self.assertIn("Escape", page.pressed, "menu de upload deve ser fechado com Escape")
        self.assertGreaterEqual(page.cliques.count("btn_ingredientes"), 1)
        loc_dialog = w._aguardar_lista_personagens_estavel.call_args[0][0]
        self.assertEqual(loc_dialog.origem, "popover",
                         "o dialog entregue à verificação deve ser o POPOVER, não o menu")

    def test_b_popover_correto_com_personagem_retorna_true(self):
        """(b) popover correto (via '@') com @Marcos -> True."""
        page = _FakePage(botao_ingredientes=False, popover_apos_arroba=True)
        w = self._worker(page)
        w._aguardar_lista_personagens_estavel = MagicMock(return_value="encontrado")
        with mock.patch.object(pf, "pw_log"):
            self.assertTrue(w._verificar_personagem_na_biblioteca("Marcos"))
        self.assertIn("@", page.typed)

    def test_c_popover_correto_sem_personagem_retorna_none(self):
        """(c) popover correto sem @Marcos (lista lida, 'estavel') -> None.

        AJUSTE (política ANTIGRAVITY): lista lida sem o nome NÃO prova ausência — só
        'vazio_confirmado' (.empty-state 2x estável + registro sem entrada) gera False.
        """
        page = _FakePage(botao_ingredientes=False, popover_apos_arroba=True)
        w = self._worker(page)
        w._aguardar_lista_personagens_estavel = MagicMock(return_value="estavel")
        with mock.patch.object(pf, "pw_log"):
            self.assertIsNone(w._verificar_personagem_na_biblioteca("Marcos"))

    def test_c2_popover_vazio_confirmado_sem_registro_retorna_false(self):
        """(c2) 'vazio_confirmado' + registro local sem entrada -> False."""
        page = _FakePage(botao_ingredientes=False, popover_apos_arroba=True)
        w = self._worker(page)
        w._aguardar_lista_personagens_estavel = MagicMock(return_value="vazio_confirmado")
        with mock.patch.object(pf, "pw_log"):
            self.assertIs(w._verificar_personagem_na_biblioteca("Marcos"), False)

    def test_d_popover_nao_abriu_retorna_none_sem_usar_o_menu(self):
        """(d) nada abre (só o menu de upload) -> None (inconclusivo), helper não chamado."""
        page = _FakePage(botao_ingredientes=True, menu_upload_visivel=True,
                         popover_apos_arroba=False)
        w = self._worker(page)
        m_aguardar = MagicMock(return_value="encontrado")
        w._aguardar_lista_personagens_estavel = m_aguardar
        with mock.patch.object(pf, "pw_log"):
            self.assertIsNone(w._verificar_personagem_na_biblioteca("Marcos"))
        m_aguardar.assert_not_called()

    def test_e_deteccao_direta_menu_upload_vs_popover(self):
        page_menu = _FakePage(botao_ingredientes=True, menu_upload_visivel=True)
        page_menu.cliques.append("btn_ingredientes")
        w1 = self._worker(page_menu)
        self.assertTrue(w1._e_menu_upload_overlay(page_menu.locator('div.cdk-overlay-pane')))
        page_pop = _FakePage(popover_visivel=True)
        w2 = self._worker(page_pop)
        self.assertFalse(w2._e_menu_upload_overlay(page_pop.locator('flow-add-menu-popover-content')))


if __name__ == "__main__":
    unittest.main()


