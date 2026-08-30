# -*- coding: utf-8 -*-
"""
tests/test_antigravity_v2.py — Rodada 2 ANTIGRAVITY (Imagens / Vídeo / CapCut / Blindagem)

Cobre:
  1. IMAGEM — endpoint /projeto/<id>/imagens/:
       - nome exato (imagens/ e cenas/)
       - resolução por ID de cena (001.png -> cena 1 com padrão canônico)
       - 404 para inexistente
       - proteção contra path traversal
       - header Cache-Control (frescor)
  2. VÍDEO — codec:
       - detectar_codec_video / detectar_container_video
       - garantir_video_h264_compat mantém H.264/MP4 inalterado
       - garantir_video_h264_compat converte MPEG4 -> H.264 (fallback CapCut old)
  3. CAPCUT — validação:
       - detectar_versao_capcut retorna dict completo
       - criar_draft_imagens com vídeo MPEG4 grava o clipe CONVERTIDO (h264) no draft
       - draft continua no schema nativo (version=360000)
"""
import json
import shutil
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from config import PROJETOS_DIR, FFMPEG_PATH, FFPROBE_PATH
import app_web


def _png_teste(pasta: Path, nome="img.png", w=320, h=180) -> Path:
    """Gera um PNG RGB preto válido sem dependências externas."""
    def chunk(tipo: bytes, dados: bytes) -> bytes:
        c = struct.pack(">I", len(dados)) + tipo + dados
        return c + struct.pack(">I", zlib.crc32(tipo + dados) & 0xFFFFFFFF)
    raw = b""
    for _ in range(h):
        raw += b"\x00" + b"\x00\x00\x00" * w
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    destino = pasta / nome
    destino.write_bytes(png)
    return destino


def _gerar_video(pasta: Path, nome: str, codec: str) -> Path:
    """Gera um vídeo de teste pequeno via ffmpeg (libx264 ou mpeg4)."""
    import subprocess
    destino = pasta / nome
    cmd = [
        FFMPEG_PATH, "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc=duration=1:size=160x90:rate=10",
        "-c:v", codec, "-pix_fmt", "yuv420p",
        str(destino),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not destino.exists() or destino.stat().st_size == 0:
        raise RuntimeError(f"falha ao gerar vídeo {codec}: {(r.stderr or '')[-300:]}")
    return destino


PROJ = "_t_antigravity2"


class TestEndpointImagens(unittest.TestCase):
    """1. IMAGEM — /projeto/<id>/imagens/ robusto."""

    @classmethod
    def setUpClass(cls):
        cls.client = app_web.app.test_client()
        pdir = PROJETOS_DIR / PROJ
        if pdir.exists():
            shutil.rmtree(pdir, ignore_errors=True)
        (pdir / "imagens").mkdir(parents=True, exist_ok=True)
        (pdir / "cenas").mkdir(parents=True, exist_ok=True)

        # cena 1: nome exato em imagens/
        cls.img_cena1 = _png_teste(pdir / "imagens", "001.png")

        # cena 2: padrão canônico timestamp em cenas/ (sem 002.png exato)
        cls.img_cena2 = _png_teste(pdir / "cenas", "2_1700000000.png")
        # cena 3: padrão canônico enriquecido em cenas/
        cls.img_cena3 = _png_teste(pdir / "cenas", "003_[00-00-05]_garden.png")
        (pdir / "midias_encontradas.json").write_text(json.dumps([
            {"scene_id": 2, "success": True, "arquivo": "cenas/2_1700000000.png"},
            {"scene_id": 3, "success": True, "arquivo": "cenas/003_[00-00-05]_garden.png"},
        ]), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        pdir = PROJETOS_DIR / PROJ
        if pdir.exists():
            shutil.rmtree(pdir, ignore_errors=True)

    def _get(self, filename):
        return self.client.get(f"/projeto/{PROJ}/imagens/{filename}")

    def test_nome_exato_em_imagens(self):
        r = self._get("001.png")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, self.img_cena1.read_bytes())

    def test_nome_exato_em_cenas(self):
        r = self._get("003_[00-00-05]_garden.png")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, self.img_cena3.read_bytes())

    def test_resolucao_por_id_canonico(self):
        # 002.png não existe exato -> resolve cena 2 via _arquivo_midia_cena
        r = self._get("002.png")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, self.img_cena2.read_bytes())

    def test_resolucao_por_id_com_underscore(self):
        r = self._get("003.png")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, self.img_cena3.read_bytes())

    def test_404_inexistente(self):
        r = self._get("999.png")
        self.assertEqual(r.status_code, 404)

    def test_path_traversal_bloqueado(self):
        # ../ não pode vazar para fora do projeto
        r = self._get("..%2F..%2Fconfig.py")
        self.assertEqual(r.status_code, 404)
        r2 = self._get("..\\..\\config.py")
        self.assertIn(r2.status_code, (400, 404))

    def test_cache_control_no_cache(self):
        r = self._get("001.png")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Cache-Control", r.headers)
        self.assertIn("no-cache", r.headers.get("Cache-Control", ""))


class TestCodecVideo(unittest.TestCase):
    """2. VÍDEO — detecção e garantia de H.264."""

    @unittest.skipUnless(FFMPEG_PATH and FFPROBE_PATH, "ffmpeg/ffprobe indisponível")
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="codec_test_"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_detectar_codec_h264(self):
        from services.video_encoder import detectar_codec_video, detectar_container_video
        v = _gerar_video(self._tmp, "v_h264.mp4", "libx264")
        self.assertEqual(detectar_codec_video(v), "h264")
        self.assertIn("mp4", detectar_container_video(v))

    def test_h264_permanece_inalterado(self):
        from services.video_encoder import garantir_video_h264_compat
        v = _gerar_video(self._tmp, "ok_h264.mp4", "libx264")
        saida = garantir_video_h264_compat(str(v))
        self.assertEqual(saida, str(v), "H.264 não precisa de conversão")

    def test_mpeg4_convertido_para_h264(self):
        from services.video_encoder import garantir_video_h264_compat, detectar_codec_video
        v = _gerar_video(self._tmp, "src_mpeg4.mp4", "mpeg4")
        self.assertNotEqual(detectar_codec_video(v), "h264")
        convertido = garantir_video_h264_compat(str(v), destino_dir=str(self._tmp / "conv"))
        self.assertTrue(Path(convertido).exists())
        self.assertEqual(detectar_codec_video(convertido), "h264")

    def test_arquivo_inexistente_nao_quebra(self):
        from services.video_encoder import garantir_video_h264_compat
        self.assertEqual(garantir_video_h264_compat(str(self._tmp / "nada.mp4")),
                         str(self._tmp / "nada.mp4"))



class TestCapCutBlindagem(unittest.TestCase):
    """3. CAPCUT — validação de versão + conversão no draft."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="capcut_blind_"))
        self.destino = self._tmp / "drafts"
        self.destino.mkdir(exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_detectar_versao_capcut(self):
        import capcut_draft_imagens as mod
        info = mod.detectar_versao_capcut()
        for k in ("instalado", "versao", "draft_version", "old"):
            self.assertIn(k, info)
        self.assertEqual(info["draft_version"], 360000)
        self.assertIsInstance(info["old"], bool)

    @unittest.skipUnless(FFMPEG_PATH, "ffmpeg indisponível")
    def test_draft_converte_video_mpeg4_para_h264(self):
        import capcut_draft_imagens as mod
        from services.video_encoder import detectar_codec_video
        v = _gerar_video(self._tmp, "clip_mpeg4.mp4", "mpeg4")
        cenas = [{"start": 0.0, "duracao": 1.0, "arquivo": str(v), "media_type": "video"}]
        res = mod.criar_draft_imagens("draftconv", cenas, "", str(self.destino),
                                      nome_projeto="draftconv")
        self.assertTrue(res["success"], res.get("error"))
        # schema nativo preservado
        dc = json.loads((Path(res["draft_dir"]) / "draft_content.json").read_text(encoding="utf-8"))
        self.assertEqual(dc.get("version"), 360000)
        # o clipe dentro do draft é H.264 (convertido) e é referenciado no material
        self.assertEqual(len(dc["materials"]["videos"]), 1)
        path_rel = dc["materials"]["videos"][0]["path"]
        arq_draft = Path(res["draft_dir"]) / Path(path_rel).name
        self.assertTrue(arq_draft.exists(), f"mídia ausente no draft: {path_rel}")
        self.assertEqual(detectar_codec_video(arq_draft), "h264")

    def test_draft_com_foto_mantem_schema(self):
        import capcut_draft_imagens as mod
        img = _png_teste(self._tmp, "cena1.png")
        cenas = [{"start": 0.0, "duracao": 3.0, "arquivo": str(img), "media_type": "photo"}]
        res = mod.criar_draft_imagens("draftfoto", cenas, "", str(self.destino),
                                      nome_projeto="draftfoto")
        self.assertTrue(res["success"], res.get("error"))
        self.assertIn("capcut_versao", res)
        self.assertIn("aviso_capcut_old", res)
        dc = json.loads((Path(res["draft_dir"]) / "draft_content.json").read_text(encoding="utf-8"))
        self.assertEqual(dc.get("version"), 360000)


if __name__ == "__main__":
    unittest.main()

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="codec_test_"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_detectar_codec_h264(self):
        from services.video_encoder import detectar_codec_video, detectar_container_video
        v = _gerar_video(self._tmp, "v_h264.mp4", "libx264")
        self.assertEqual(detectar_codec_video(v), "h264")
        self.assertIn("mp4", detectar_container_video(v))

    def test_h264_permanece_inalterado(self):
        from services.video_encoder import garantir_video_h264_compat
        v = _gerar_video(self._tmp, "ok_h264.mp4", "libx264")
        saida = garantir_video_h264_compat(str(v))
        self.assertEqual(saida, str(v), "H.264 não precisa de conversão")

    def test_mpeg4_convertido_para_h264(self):
        from services.video_encoder import garantir_video_h264_compat, detectar_codec_video
        v = _gerar_video(self._tmp, "src_mpeg4.mp4", "mpeg4")
        self.assertNotEqual(detectar_codec_video(v), "h264")
        convertido = garantir_video_h264_compat(str(v), destino_dir=str(self._tmp / "conv"))
        self.assertTrue(Path(convertido).exists())
        self.assertEqual(detectar_codec_video(convertido), "h264")

    def test_arquivo_inexistente_nao_quebra(self):
        from services.video_encoder import garantir_video_h264_compat
        self.assertEqual(garantir_video_h264_compat(str(self._tmp / "nada.mp4")),
                         str(self._tmp / "nada.mp4"))
