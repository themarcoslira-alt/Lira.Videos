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

UUID = "3f6a1b2c-4d5e-6f70-8a9b-0c1d2e3f4a5b"       # /video/ PRONTO (caso principal)
UUID_MESMO = "3f6a1b2c-4d5e-6f70-8a9b-0c1d2e3f4a5b"   # mesmo UUID, query diferente
UUID_OUTRO = "7a2b3c4d-5e6f-7081-92a3-b4c5d6e7f809"   # segundo vídeo pronto (UUID distinto)
UUID_IMG = "8b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"     # /image/ = AINDA EM RENDER
# Srcs no formato REAL medido (a query Expires/Signature muda a cada carga).
URL_RENDER_IMG = f"https://flow.google.com/image/{UUID_IMG}?Expires=1770000000&Signature=aaa111bbb"
URL_VID_A = f"https://flow.google.com/video/{UUID}?Expires=1770000001&Signature=bbb222ccc"
URL_VID_A_MESMO = f"https://flow.google.com/video/{UUID_MESMO}?Expires=1799999999&Signature=zzz999yyy"
URL_VID_B = f"https://flow.google.com/video/{UUID_OUTRO}?Expires=1770000002&Signature=ccc333ddd"
URL_LEGADO = f"https://flow.google.com/asb/xyz?name={UUID}"


def _html_com_tiles():
    """Página com 7 tiles: /image/ com 45%, sem src, data:, /video/ pronto (+barra de seek),
    o MESMO uuid com query diferente, um 2º uuid pronto e um src legado `?name=<uuid>`."""
    return f"""<!DOCTYPE html><html><body>
<flow-video-tile>
  <mat-icon>play_circle</mat-icon>
  <img class="thumbnail" alt="Miniatura do vídeo gerada" src="{URL_RENDER_IMG}">
  <div class="hover-overlay-has-progress-bar"><div class="progress-bar"></div></div>
  <span>45%</span>
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
  <img class="thumbnail" alt="Miniatura do vídeo gerada" src="{URL_VID_A}">
  <div class="hover-overlay-has-progress-bar"><div class="progress-bar"></div></div>
</flow-video-tile>
<flow-video-tile>
  <mat-icon>play_circle</mat-icon>
  <img class="thumbnail" alt="Miniatura do vídeo gerada" src="{URL_VID_A_MESMO}">
</flow-video-tile>
<flow-video-tile>
  <mat-icon>play_circle</mat-icon>
  <img class="thumbnail" alt="Miniatura do vídeo gerada" src="{URL_VID_B}">
</flow-video-tile>
<flow-video-tile>
  <mat-icon>play_circle</mat-icon>
  <img class="thumbnail" alt="Miniatura do vídeo gerada" src="{URL_LEGADO}">
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
        cls.vids = [m for m in cls.media if m.get("type") == "video"]
        cls.chaves = [str(m.get("mediaKey")) for m in cls.vids]

    def test_00_js_executou_sem_erro(self):
        self.assertTrue(self.res.get("ok"), self.res.get("error"))

    def test_01_tile_em_render_image_com_45pct_nao_e_devolvido(self):
        """src /image/<uuid> + texto '45%' = renderizando: NÃO é candidato."""
        self.assertNotIn(UUID_IMG, self.chaves, self.chaves)

    def test_02_sem_src_e_data_nao_sao_devolvidos(self):
        for m in self.media:
            self.assertTrue(str(m.get("src")), m)
            self.assertFalse(str(m.get("src")).startswith("data:"), m)

    def test_03_tile_pronto_video_com_barra_permanente_e_devolvido(self):
        """O tile PRONTO (/video/) tem .hover-overlay-has-progress-bar — não pode ser descartado."""
        self.assertIn(UUID, self.chaves, self.chaves)

    def test_04_mesmo_uuid_com_query_diferente_da_a_mesma_chave(self):
        """Dois srcs /video/ do MESMO uuid caem na MESMA mediaKey (a query não entra)."""
        self.assertEqual(self.chaves.count(UUID), 1, self.chaves)
        for c in self.chaves:
            self.assertNotIn("Expires", c)
            self.assertNotIn("Signature", c)

    def test_05_dois_uuids_distintos_dao_chaves_distintas(self):
        self.assertEqual(sorted(set(self.chaves)), sorted([UUID, UUID_OUTRO]))

    def test_06_apenas_dois_videos_prontos(self):
        self.assertEqual(len(self.vids), 2, self.vids)


# --------------------------------------------------- Caso "dois vídeos prontos"
# UUIDs distintos em srcs /video/ (com query volátil) e SEM id/data-id.
URL_LONGA_A = f"https://flow.google.com/video/{UUID}?Expires=1770000003&Signature=aaa111bbb"
URL_LONGA_B = f"https://flow.google.com/video/{UUID_OUTRO}?Expires=1770000004&Signature=zzz999yyy"


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

    def test_03_cada_chave_e_o_uuid_do_respectivo_tile(self):
        self.assertEqual(sorted(self.chaves), sorted([UUID, UUID_OUTRO]))

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
