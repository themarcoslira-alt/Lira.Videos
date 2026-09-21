# -*- coding: utf-8 -*-
"""
Regressão — polling de /api/v2/cena_media sem backoff (tempestade de 404).

Dois problemas corrigidos no static/app.js:
  (1) `iniciarThumbCena` usava setInterval FIXO de 2s (sem backoff) e só parava no 404
      quando a cena declarava mídia;
  (2) as 3 miniaturas usavam `onerror="this.src='/api/v2/cena_media/...'"` — quando o
      fallback também respondia 404, o onerror reatribuía a MESMA URL em loop
      (centenas de requests por segundo).

Contrato verificado aqui:
  * backoff exponencial 2s -> 4s -> 8s -> 16s -> 30s (teto) com reset para 2s no 200;
  * parada definitiva após 5 404 consecutivos + marcação "sem mídia disponível";
  * nenhum `onerror` reatribui a mesma URL para a API (o loop era o que estourava).
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")


def _bloco(inicio: str, fim: str) -> str:
    i = JS.find(inicio)
    if i == -1:
        raise AssertionError(f"'{inicio}' não encontrado em static/app.js")
    j = JS.find(fim, i)
    if j == -1:
        raise AssertionError(f"'{fim}' não encontrado após '{inicio}'")
    return JS[i:j]


class TestConstantesDeBackoff(unittest.TestCase):
    def test_01_constantes(self):
        self.assertIn("const MEDIA_POLL_INTERVALO_INICIAL_MS = 2000;", JS)
        self.assertIn("const MEDIA_POLL_INTERVALO_MAX_MS = 30000;", JS)
        self.assertIn("const MEDIA_POLL_MAX_FALHAS_404 = 5;", JS)


class TestBackoffExponencial(unittest.TestCase):
    def test_02_404_dobra_intervalo_com_teto(self):
        b = _bloco("function _registrarFalha404Media", "function _resetarPollMedia")
        self.assertIn("st.falhas404 += 1;", b)
        # espera DESTA rodada (2s na 1ª falha) e dobra para a próxima, com teto
        self.assertIn("st.espera = st.intervalo;", b)
        self.assertIn("Math.min(st.intervalo * 2, MEDIA_POLL_INTERVALO_MAX_MS)", b)

    def test_03_para_apos_5_falhas(self):
        b = _bloco("function _registrarFalha404Media", "function _resetarPollMedia")
        self.assertIn("if (st.falhas404 >= MEDIA_POLL_MAX_FALHAS_404) st.parado = true;", b)

    def test_04_200_reseta_o_intervalo(self):
        b = _bloco("function _resetarPollMedia", "function _pararPollMedia")
        self.assertIn("st.falhas404 = 0;", b)
        self.assertIn("st.intervalo = MEDIA_POLL_INTERVALO_INICIAL_MS;", b)

    def test_05_loop_de_thumbnail_usa_timeout_com_backoff(self):
        b = _bloco("function iniciarThumbCena", "function _marcarMidiaDeletadaCena")
        self.assertNotIn("setInterval(", b, "o polling não pode voltar a ser intervalo fixo")
        self.assertIn("setTimeout(checar, atraso)", b)
        self.assertIn("agendar(MEDIA_POLL_INTERVALO_INICIAL_MS)", b)
        self.assertIn("agendar(st404.espera)", b)
        self.assertIn("_resetarPollMedia(chave)", b)

    def test_06_thumbnail_para_e_marca_apos_5_404(self):
        b = _bloco("function iniciarThumbCena", "function _marcarMidiaDeletadaCena")
        self.assertIn("if (st404.parado)", b)
        self.assertIn("_pararPollMedia(chave);", b)
        self.assertIn("_marcarSemMidiaDisponivelCena(imgEl, sceneId);", b)


class TestFimDoLoopDoOnerror(unittest.TestCase):
    def test_07_nenhum_onerror_reatribui_url_da_api(self):
        self.assertNotIn("onerror=\"this.src='/api/", JS)

    def test_08_renderers_usam_o_helper_de_thumb(self):
        self.assertEqual(JS.count("? _htmlThumbMidia(cid, imgUrl"), 3,
                         "as 3 miniaturas devem usar o helper com estado de polling")

    def test_09_helper_respeita_polling_encerrado(self):
        b = _bloco("function _htmlThumbMidia", "function _onerrorThumbCena")
        self.assertIn("_pollMediaParado(cid)", b)
        self.assertIn("_placeholderSemMidiaHtml()", b)

    def test_10_onerror_nao_reatribui_imediatamente_a_mesma_url(self):
        b = _bloco("function _onerrorThumbCena", "window._onerrorThumbCena")
        self.assertIn("imgEl.onerror = null;", b)
        self.assertIn("setTimeout(", b)
        self.assertIn("st.espera", b)
        # o fallback para cena_media acontece UMA única vez
        self.assertIn('imgEl.dataset.fallbackCenaMedia !== "1"', b)

    def test_11_estado_e_limpo_na_troca_de_projeto(self):
        b = _bloco("function pararTodosPollings", "async function pollTudo")
        self.assertIn("clearInterval(S.cenaThumbs[k]);", b)
        self.assertIn("clearTimeout(S.cenaThumbs[k]);", b)
        self.assertIn("_MEDIA_POLL_ESTADOS.clear();", b)


if __name__ == "__main__":
    unittest.main()
