# -*- coding: utf-8 -*-
"""
Testes do exportador CapCut nativo v9.x (capcut_draft_imagens.py).

Garante que o draft gerado usa o SCHEMA NATIVO do CapCut 9.x:
  - `version: 360000` (e NÃO o formato legado `draft_version: 2.0.0`)
  - top-level com id/version/tracks/materials/duration/canvas_config/platform
  - `materials` achatado por tipo
"""
import json
import shutil
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

import capcut_draft_imagens as mod


# Chaves de material que um draft real do CapCut 9.3 sempre tem
_MATERIAIS_OBRIGATORIOS = [
    "videos", "audios", "speeds", "placeholder_infos", "canvases",
    "material_animations", "sound_channel_mappings", "material_colors",
    "vocal_separations", "beats", "texts", "transitions", "effects",
]


def _png_teste(pasta: Path, nome="img.png", w=640, h=360) -> Path:
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


class TestCapCutDraftNativo(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="capcut_test_"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _exportar(self, nome="projeto_teste", cenas=None, audio=""):
        destino = self._tmp / "drafts"
        destino.mkdir(exist_ok=True)
        cenas = cenas or [{"start": 0.0, "duracao": 3.0,
                           "arquivo": str(_png_teste(self._tmp)),
                           "media_type": "photo"}]
        return mod.criar_draft_imagens(nome, cenas, audio, str(destino),
                                       nome_projeto=nome)

    def test_version_nativa_360000(self):
        res = self._exportar()
        self.assertTrue(res["success"])
        dc = json.loads((Path(res["draft_dir"]) / "draft_content.json").read_text(encoding="utf-8"))
        self.assertEqual(dc.get("version"), 360000)
        # nunca deve voltar ao formato legado (draft_version 2.0.0)
        self.assertNotIn("draft_version", dc)

    def test_top_keys_obrigatorias(self):
        res = self._exportar(nome="p2")
        self.assertTrue(res["success"])
        dc = json.loads((Path(res["draft_dir"]) / "draft_content.json").read_text(encoding="utf-8"))
        obrigatorias = ["id", "version", "new_version", "name", "duration", "fps",
                        "config", "canvas_config", "tracks", "materials",
                        "platform", "last_modified_platform"]
        for k in obrigatorias:
            self.assertIn(k, dc, f"campo de topo ausente: {k}")
        self.assertTrue(dc["tracks"], "deve haver pelo menos uma trilha")
        tipos = [t.get("type") for t in dc["tracks"]]
        self.assertIn("video", tipos)

    def test_materials_achatados(self):
        res = self._exportar(nome="p3")
        self.assertTrue(res["success"])
        dc = json.loads((Path(res["draft_dir"]) / "draft_content.json").read_text(encoding="utf-8"))
        for chave in _MATERIAIS_OBRIGATORIOS:
            self.assertIn(chave, dc["materials"], f"material {chave} ausente")
        self.assertEqual(len(dc["materials"]["videos"]), 1)

    def test_audio_vira_trilha_de_audio(self):
        destino = self._tmp / "drafts"
        destino.mkdir(exist_ok=True)
        img = _png_teste(self._tmp)
        fake_audio = self._tmp / "audio_demo.mp3"
        fake_audio.write_bytes(b"ID3\x04PROVA")
        cenas = [{"start": 0.0, "duracao": 4.0, "arquivo": str(img), "media_type": "photo"}]
        res = mod.criar_draft_imagens("p4", cenas, str(fake_audio), str(destino),
                                     nome_projeto="p4")
        self.assertTrue(res["success"])
        dc = json.loads((Path(res["draft_dir"]) / "draft_content.json").read_text(encoding="utf-8"))
        tipos = [t.get("type") for t in dc["tracks"]]
        self.assertIn("video", tipos)
        self.assertEqual(len(dc["materials"]["audios"]), 1)

    def test_metadados_no_draft_dir(self):
        destino = self._tmp / "drafts"
        destino.mkdir(exist_ok=True)
        img = _png_teste(self._tmp)
        cenas = [{"start": 0.0, "duracao": 3.0, "arquivo": str(img), "media_type": "photo"}]
        res = mod.criar_draft_imagens("nav", cenas, "", str(destino), nome_projeto="nav")
        self.assertTrue(res["success"])
        draft_dir = Path(res["draft_dir"])
        self.assertTrue((draft_dir / "draft_content.json").exists())
        self.assertTrue((draft_dir / "draft_meta_info.json").exists())
        meta = json.loads((draft_dir / "draft_meta_info.json").read_text(encoding="utf-8"))
        self.assertEqual(meta.get("draft_name"), "nav")


if __name__ == "__main__":
    unittest.main()