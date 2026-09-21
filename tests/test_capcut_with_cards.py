# -*- coding: utf-8 -*-
"""
Testes de integração do exportador CapCut nativo v9.x com Story Cards.
Verifica a geração de Track 3 (badges de Story Card) e posicionamento complementar.
"""

import json
import shutil
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

import capcut_draft_imagens as mod


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


class TestCapCutWithStoryCards(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="capcut_card_test_"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_exportar_cenas_com_story_card_gera_track_overlay(self):
        destino = self._tmp / "drafts"
        destino.mkdir(exist_ok=True)
        img1 = _png_teste(self._tmp, "img1.png")
        img2 = _png_teste(self._tmp, "img2.png")

        cenas = [
            {
                "id": 1,
                "start": 0.0,
                "duracao": 4.0,
                "arquivo": str(img1),
                "media_type": "photo",
                "texto": "In step one, prepare your lawn carefully.",
                "is_story_card": True,
                "story_card_type": "step_1",
                "story_card_style": "step_intro",
                "story_card_config": {
                    "title": "PASSO 1",
                    "subtitle": "Preparar o solo",
                    "duration_ms": 3000,
                },
            },
            {
                "id": 2,
                "start": 4.0,
                "duracao": 4.0,
                "arquivo": str(img2),
                "media_type": "photo",
                "texto": "Regular maintenance without special steps.",
                "is_story_card": False,
            },
        ]

        res = mod.criar_draft_imagens("proj_story_cards", cenas, "", str(destino), nome_projeto="proj_story_cards")
        self.assertTrue(res["success"])

        draft_content_path = Path(res["draft_dir"]) / "draft_content.json"
        self.assertTrue(draft_content_path.exists())

        dc = json.loads(draft_content_path.read_text(encoding="utf-8"))
        self.assertEqual(dc.get("version"), 360000)

        # Trilha de vídeo + pelo menos 2 trilhas de texto (legendas + badge de story card)
        text_tracks = [t for t in dc.get("tracks", []) if t.get("type") == "text"]
        self.assertGreaterEqual(len(text_tracks), 2, "Deve haver pelo menos 2 trilhas de texto: legendas e badges")

        # Verifica se o texto do badge PASSO 1 está nos materiais de texto
        materiais_texto = dc["materials"].get("texts", [])
        textos_encontrados = []
        for m in materiais_texto:
            # Em CapCut, o conteúdo do texto pode estar serializado em content (JSON string)
            content_str = m.get("content", "")
            try:
                content_json = json.loads(content_str)
                textos_encontrados.append(content_json.get("text", ""))
            except Exception:
                textos_encontrados.append(content_str)

        badge_presente = any("PASSO 1" in t.upper() for t in textos_encontrados)
        self.assertTrue(badge_presente, f"Badge 'PASSO 1' não encontrado nos materiais de texto: {textos_encontrados}")


if __name__ == "__main__":
    unittest.main()
