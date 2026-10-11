# -*- coding: utf-8 -*-
"""Testes do download automático de vídeo via <flow-video-tile> (ANTIGRAVITY — Flow 2025).

O Flow não renderiza <video> nativo na grade: o vídeo vive em <flow-video-tile>
(play_circle) e o <img class="thumbnail"> interno é apenas PREVIEW. Estas provas
cobrem:
  * JS_FETCH_MEDIA_LIST reconhece o custom element e ignora thumbnails como imagem;
  * _bytes_sao_video_real aceita mp4/webm/mov e rejeita imagem (anti-mentira de formato);
  * _baixar_video_tile_automatico: menu → "Fazer o download" → qualidade → expect_download;
  * falhas (sem tile / download não-vídeo) retornam False SEM gravar mídia;
  * contrato de gating: `src` de preview nunca entra nos fallbacks de download.

VALIDADO VIA SIMULAÇÃO/STUB — nada aqui toca o Flow real (page = MagicMock).
A validação em execução real (fila no Flow) é etapa posterior separada.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import services.playwright_flow as pf


def _mp4_bytes(tamanho: int = 4096) -> bytes:
    cabecalho = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 8
    return cabecalho + b"\x00" * max(0, tamanho - len(cabecalho))


def _webm_bytes() -> bytes:
    return b"\x1a\x45\xdf\xa3" + b"\x00" * 4092


def _png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 4092


class TestDetectorJS(unittest.TestCase):
    """Contrato do detector injetado (JS_FETCH_MEDIA_LIST)."""

    def test_js_detecta_custom_element_e_play_circle(self):
        js = pf.JS_FETCH_MEDIA_LIST
        self.assertIn("querySelectorAll('flow-video-tile')", js)
        self.assertIn("play_circle", js)
        self.assertIn("srcEhPreview", js)
        self.assertIn("'video_tile_' + mediaKey", js)
        self.assertIn("tileIndex: tIdx", js)

    def test_thumbnail_do_tile_nao_conta_como_imagem(self):
        js = pf.JS_FETCH_MEDIA_LIST
        self.assertIn("img.closest('flow-video-tile')", js)

    def test_fallback_video_nativo_preservado(self):
        self.assertIn("querySelectorAll('video')", pf.JS_FETCH_MEDIA_LIST)

    def test_gating_anti_thumbnail_nos_fallbacks(self):
        src = (ROOT / "services" / "playwright_flow.py").read_text(encoding="utf-8")
        self.assertIn("_src_eh_preview_video", src)
        self.assertIn("VIDEO_DOWNLOAD_FALLBACK", src)
        self.assertIn("VIDEO_SAVED", src)


class TestAssinaturaVideo(unittest.TestCase):
    """_bytes_sao_video_real: aceita contêiner de vídeo, rejeita imagem."""

    def test_mp4_webm_mov_sao_video(self):
        self.assertTrue(pf.PlaywrightCDPWorker._bytes_sao_video_real(_mp4_bytes()))
        self.assertTrue(pf.PlaywrightCDPWorker._bytes_sao_video_real(_webm_bytes()))
        mov = b"\x00\x00\x00\x14stypqt  " + b"\x00" * 4092
        self.assertTrue(pf.PlaywrightCDPWorker._bytes_sao_video_real(mov))

    def test_imagens_e_curtos_nao_sao_video(self):
        casos = (
            _png_bytes(),
            b"\xff\xd8\xff\xe0" + b"\x00" * 4092,
            b"GIF89a" + b"\x00" * 4092,
            b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 4092,
            b"BM" + b"\x00" * 4092,
            b"\x00" * 4,
        )
        for dados in casos:
            self.assertFalse(
                pf.PlaywrightCDPWorker._bytes_sao_video_real(dados),
                f"assinatura aceita indevidamente: {dados[:8]!r}",
            )


class TestBaixarVideoTileAutomatico(unittest.TestCase):
    """Fluxo completo do download via menu do <flow-video-tile> com page stub."""

    def _worker(self, page):
        w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
        w.page = page
        return w

    def _page_sucesso(self, bytes_download: bytes):
        page = mock.MagicMock()
        page.locator.return_value.first.is_visible.return_value = True
        page.locator.return_value.last.is_visible.return_value = True
        download = mock.MagicMock()
        download.save_as.side_effect = lambda destino: Path(destino).write_bytes(bytes_download)
        ctx = mock.MagicMock()
        ctx.__enter__.return_value.value = download
        page.expect_download.return_value = ctx
        return page

    @staticmethod
    def _textos_log(m_log) -> str:
        return " ".join(str(chamada) for chamada in m_log.call_args_list)

    def test_01_download_720p_sucesso(self):
        tile = {
            "mediaKey": "11111111-2222-3333-4444-555555555555",
            "tileIndex": 0,
            "type": "video",
            "src": "https://flow/media?name=11111111-2222-3333-4444-555555555555",
            "srcEhPreview": True,
        }
        page = self._page_sucesso(_mp4_bytes())
        w = self._worker(page)
        with mock.patch.object(pf, "pw_log") as m_log:
            ok = w._baixar_video_tile_automatico(tile, 2, "720p")
        self.assertTrue(ok)
        self.assertIsNotNone(w._download_tile_bytes)
        self.assertEqual(w._download_tile_bytes[4:8], b"ftyp")
        textos = self._textos_log(m_log)
        self.assertIn("VIDEO_DOWNLOAD_START", textos)
        self.assertIn("VIDEO_DOWNLOAD_OK", textos)
        self.assertTrue(page.locator.called)
        self.assertTrue(page.expect_download.called)

    def test_02_download_de_thumbnail_e_rejeitado(self):
        tile = {"mediaKey": "uuid-tile", "tileIndex": 0, "type": "video",
                "srcEhPreview": True}
        page = self._page_sucesso(_png_bytes())
        w = self._worker(page)
        with mock.patch.object(pf, "pw_log") as m_log:
            ok = w._baixar_video_tile_automatico(tile, 2, "720p")
        self.assertFalse(ok)
        self.assertIsNone(w._download_tile_bytes)
        self.assertIn("não é vídeo real", self._textos_log(m_log))

    def test_03_sem_tile_retorna_false(self):
        page = mock.MagicMock()
        page.locator.return_value.first.is_visible.return_value = False
        page.locator.return_value.last.is_visible.return_value = False
        page.locator.return_value.nth.return_value.is_visible.return_value = False
        w = self._worker(page)
        with mock.patch.object(pf, "pw_log") as m_log:
            ok = w._baixar_video_tile_automatico({"mediaKey": "x", "tileIndex": 0}, 2, "720p")
        self.assertFalse(ok)
        self.assertIn("nenhum <flow-video-tile>", self._textos_log(m_log))

    def test_04_sem_page_retorna_false(self):
        w = self._worker(None)
        self.assertFalse(w._baixar_video_tile_automatico({"mediaKey": "x"}, 2, "720p"))


if __name__ == "__main__":
    unittest.main()
