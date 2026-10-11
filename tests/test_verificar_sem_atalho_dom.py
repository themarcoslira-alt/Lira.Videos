import unittest
from unittest import mock
import services.playwright_flow as pf
from services.playwright_flow import PlaywrightCDPWorker


def make_page(aria_label_count=1, popup_visivel=True):
    page = mock.MagicMock()
    page.wait_for_timeout = mock.MagicMock()

    def _fake_locator(sel):
        loc = mock.MagicMock()
        if "aria-label" in sel:
            loc.count.return_value = aria_label_count
            loc.first.is_visible.return_value = False
        elif "contenteditable" in sel or "ProseMirror" in sel:
            loc.count.return_value = 1
            loc.first.is_visible.return_value = True
        elif "dialog" in sel or "cdk-overlay" in sel:
            loc.count.return_value = 1 if popup_visivel else 0
            loc.first.is_visible.return_value = popup_visivel
        else:
            loc.count.return_value = 0
            loc.first.is_visible.return_value = False
        return loc

    page.locator.side_effect = _fake_locator
    return page


class TestVerificarSemAtalhoDom(unittest.TestCase):
    def setUp(self):
        self.w = PlaywrightCDPWorker(port=9222)
        self.w.current_project_id = "ProjTest"

    def test_a_dom_com_aria_label_popup_nao_abre_retorna_none(self):
        """(a) DOM com aria-label casando o nome e popup que NÃO abre: resultado deve ser None (hoje retorna True)."""
        self.w.page = make_page(aria_label_count=1, popup_visivel=False)
        res = self.w._verificar_personagem_na_biblioteca("Marcos")
        self.assertIs(res, None)

    def test_b_dom_com_aria_label_helper_estavel_retorna_none(self):
        """(b) DOM com aria-label casando e helper 'estavel' → None (política ANTIGRAVITY:
        lista lida sem o nome NÃO é prova de ausência; só 'vazio_confirmado' gera False)."""
        self.w.page = make_page(aria_label_count=1, popup_visivel=True)
        with mock.patch.object(self.w, "_aguardar_lista_personagens_estavel", return_value="estavel"):
            res = self.w._verificar_personagem_na_biblioteca("Marcos")
            self.assertIsNone(res)

    def test_b2_vazio_confirmado_sem_registro_retorna_false(self):
        """(b2) .empty-state estável + registro local sem entrada → False (prova acumulada)."""
        self.w.page = make_page(aria_label_count=1, popup_visivel=True)
        with mock.patch.object(self.w, "_aguardar_lista_personagens_estavel",
                               return_value="vazio_confirmado"):
            res = self.w._verificar_personagem_na_biblioteca("Marcos")
            self.assertIs(res, False)

    def test_c_dom_sem_aria_label_helper_encontrado_retorna_true(self):
        """(c) DOM sem aria-label casando e helper mockado para 'encontrado': resultado deve ser True (passa antes e depois)."""
        self.w.page = make_page(aria_label_count=0, popup_visivel=True)
        with mock.patch.object(self.w, "_aguardar_lista_personagens_estavel", return_value="encontrado"):
            res = self.w._verificar_personagem_na_biblioteca("Marcos")
            self.assertIs(res, True)

    def test_d_dom_com_aria_label_helper_carregando_retorna_none(self):
        """(d) DOM com aria-label casando e helper mockado para 'carregando': resultado deve ser None."""
        self.w.page = make_page(aria_label_count=1, popup_visivel=True)
        with mock.patch.object(self.w, "_aguardar_lista_personagens_estavel", return_value="carregando"):
            res = self.w._verificar_personagem_na_biblioteca("Marcos")
            self.assertIs(res, None)


if __name__ == "__main__":
    unittest.main()
