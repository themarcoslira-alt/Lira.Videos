"""Rodada B (2b) — harness do `JS_FETCH_MEDIA_LIST` com o DOM REAL medido no Flow
(2026-10-11) para tiles de vídeo.

Importa o JS REAL do módulo (`services/playwright_flow.JS_FETCH_MEDIA_LIST`) — o texto
NÃO é copiado — e o executa em **Chromium headless** (Playwright).

DOM real do tile PRONTO (medido): `<flow-video-tile>` + `<mat-icon>play_circle</mat-icon>`
+ `<img class="thumbnail" alt="Miniatura do vídeo gerada" src="https://flow.google.com/asb/<longo>">`,
SEM id, SEM data-id e SEM `?name=UUID` no src. O player mantém barra de seek PERMANENTE
(`.hover-overlay-has-progress-bar` > `.progress-bar`, opacity 0) mesmo em tile pronto —
por isso nenhum seletor de progresso pode descartar tile.

Casos:
  1. PRONTO com a barra permanente      -> DEVOLVIDO (mediaKey = URL completa)
  2. `<img>` sem src                    -> DESCARTADO
  3. src `data:`                        -> DESCARTADO
  4. pronto com `?name=<uuid 36>`       -> mediaKey = UUID (precedência)
  5. pronto com `data-id` e src válido  -> mediaKey = data-id (precedência)
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from services.playwright_flow import JS_FETCH_MEDIA_LIST  # noqa: E402

UUID = "3f6a1b2c-4d5e-6f70-8a9b-0c1d2e3f4a5b"
DATA_ID = "uuid-data-id-0001"
# URL longa no formato REAL do Flow (sem ?name=UUID — o tile pronto real é assim).
URL_ASB = ("https://flow.google.com/asb/ANqvLOK9d2Xk7QwErT1yU2i3o4p5a6s7d8f9g0h1j2k3l4m5n6b7v8c9"
           "0d1e2f3g4h5i6j7k8l9m0n1o2p3q4r5s6t7u8v9w0x1y2z3")
URL_UUID = f"https://flow.google.com/asb/xyz?name={UUID}"
URL_DATA_ID = "https://flow.google.com/asb/data-id-fallback-0001"


def _html_com_tiles():
    return f"""<!DOCTYPE html><html><body>
<flow-video-tile>
  <mat-icon>play_circle</mat-icon>
  <img class="thumbnail" alt="Miniatura do vídeo gerada" src="{URL_ASB}">
  <div class="hover-overlay-has-progress-bar"><div class="progress-bar"></div></div>
</flow-video-tile>
<flow-video-tile>
  <mat-icon>play_circle</mat-icon>
  <img class="thumbnail" alt="Miniatura do vídeo gerada">
  <div class="hover-overlay-has-progress-bar"><div class="progress-bar"></div></div>
</flow-video-tile>
<flow-video-tile>
  <mat-icon>play_circle</mat-icon>
  <img class="thumbnail" src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==">
</flow-video-tile>
<flow-video-tile>
  <mat-icon>play_circle</mat-icon>
  <img class="thumbnail" alt="Miniatura do vídeo gerada" src="{URL_UUID}">
</flow-video-tile>
<flow-video-tile data-id="{DATA_ID}">
  <mat-icon>play_circle</mat-icon>
  <img class="thumbnail" alt="Miniatura do vídeo gerada" src="{URL_DATA_ID}">
</flow-video-tile>
</body></html>"""


def coletar_media(html=None):
    """Executa o JS real em Chromium headless e devolve o dict {ok, media, count}."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html if html is not None else _html_com_tiles())
        res = page.evaluate(JS_FETCH_MEDIA_LIST)
        browser.close()
    return res


class TestJsFetchMediaListVideo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = coletar_media()
        cls.media = cls.res.get("media") or []
        cls.chaves = [str(m.get("mediaKey")) for m in cls.media]

    def test_00_js_executou_sem_erro(self):
        self.assertTrue(self.res.get("ok"), self.res.get("error"))

    def test_01_tile_pronto_com_barra_permanente_e_devolvido(self):
        """O tile PRONTO real tem .hover-overlay-has-progress-bar — NÃO pode ser descartado."""
        self.assertIn(URL_ASB, self.chaves, self.chaves)

    def test_02_sem_src_e_data_sao_descartados(self):
        """Só os 3 tiles prontos (1/4/5) viram vídeo; o sem src e o data: ficam fora."""
        vids = [m for m in self.media if m.get("type") == "video"]
        self.assertEqual(len(vids), 3, vids)

    def test_03_nenhum_item_com_src_data(self):
        for m in self.media:
            self.assertFalse(str(m.get("src")).startswith("data:"), m)

    def test_04_uuid_tem_precedencia(self):
        self.assertIn(UUID, self.chaves, self.chaves)

    def test_05_data_id_tem_precedencia_sobre_a_url(self):
        self.assertIn(DATA_ID, self.chaves, self.chaves)

    def test_06_media_key_nunca_e_sintetica(self):
        for c in self.chaves:
            self.assertNotIn("sem_src", c)


# ---------------------------------------------------------------- TAREFA A
# Caso "dois vídeos": URLs longas reais (com `-`, `_`, `=`, `&`, `?`) e SEM id/data-id.
URL_LONGA_A = ("https://flow.google.com/asb/AAA-video_um-0001?x=1&sig=aaa111bbb222ccc333"
               "ddd444eee555fff666ggg777hhh888")
URL_LONGA_B = ("https://flow.google.com/asb/BBB-video_dois-0002?x=2&sig=zzz999yyy888xxx777"
               "www666vvv555uuu444ttt333sss222")


def _html_dois_prontos():
    """Dois tiles PRONTOS, sem id/data-id, com src DIFERENTES e barra permanente."""
    return f"""<!DOCTYPE html><html><body>
<flow-video-tile>
  <mat-icon>play_circle</mat-icon>
  <img class="thumbnail" alt="Miniatura do vídeo gerada" src="{URL_LONGA_A}">
  <div class="hover-overlay-has-progress-bar"><div class="progress-bar"></div></div>
</flow-video-tile>
<flow-video-tile>
  <mat-icon>play_circle</mat-icon>
  <img class="thumbnail" alt="Miniatura do vídeo gerada" src="{URL_LONGA_B}">
  <div class="hover-overlay-has-progress-bar"><div class="progress-bar"></div></div>
</flow-video-tile>
</body></html>"""


class TestDoisVideosProntos(unittest.TestCase):
    """Caso 'dois vídeos': 2 tiles prontos devem sair com mediaKeys DIFERENTES = seus src."""

    @classmethod
    def setUpClass(cls):
        cls.res = coletar_media(_html_dois_prontos())
        cls.vids = [m for m in (cls.res.get("media") or []) if m.get("type") == "video"]
        cls.chaves = [str(m.get("mediaKey")) for m in cls.vids]

    def test_01_dois_itens_devolvidos(self):
        self.assertEqual(len(self.vids), 2, self.vids)

    def test_02_chaves_diferentes_entre_si(self):
        self.assertEqual(len(set(self.chaves)), 2, self.chaves)

    def test_03_cada_chave_e_o_src_do_respectivo_tile(self):
        self.assertEqual(sorted(self.chaves), sorted([URL_LONGA_A, URL_LONGA_B]))

    def test_04_src_preservado_em_cada_item(self):
        self.assertEqual(sorted(str(m.get("src")) for m in self.vids),
                         sorted([URL_LONGA_A, URL_LONGA_B]))


if __name__ == "__main__":
    for _nome, _r in (("PAGINA COMPLETA", coletar_media()),
                      ("DOIS VIDEOS PRONTOS", coletar_media(_html_dois_prontos()))):
        print(f"--- {_nome}: ok={_r.get('ok')} count={_r.get('count')}")
        for _m in (_r.get("media") or []):
            if _m.get("type") == "video":
                print(f"  video mediaKey={str(_m.get('mediaKey'))[:70]} src={str(_m.get('src'))[:45]}")
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
