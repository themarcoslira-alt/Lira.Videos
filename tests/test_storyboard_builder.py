"""Testes do BLOCO 4 — Storyboard Builder (beats visuais + mídia)."""
import json
import tempfile
import unittest
from pathlib import Path

from services.storyboard_builder import (
    _extrair_beats,
    _localizar_beat,
    carregar_storyboard,
    construir_storyboard,
    linhas_para_prompt,
)


def _escrever(base, nome, dados):
    d = Path(base) / nome
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")


FRASES = [
    {"start": 0.0, "end": 4.0, "text": "The root, the leaves, the flower, even the stem."},
    {"start": 4.0, "end": 7.0, "text": "Water your lawn every morning."},
    {"start": 7.0, "end": 8.5, "text": "A short one."},
]

PALAVRAS = {
    "segments": [
        {"index": 0, "start": 0.0, "end": 4.0, "text": "...", "words": [
            {"w": "The", "s": 0.0, "e": 0.2},
            {"w": "root,", "s": 0.3, "e": 0.8},
            {"w": "the", "s": 0.9, "e": 1.0},
            {"w": "leaves,", "s": 1.1, "e": 1.8},
            {"w": "the", "s": 1.9, "e": 2.0},
            {"w": "flower,", "s": 2.1, "e": 2.8},
            {"w": "even", "s": 2.9, "e": 3.1},
            {"w": "the", "s": 3.2, "e": 3.3},
            {"w": "stem.", "s": 3.4, "e": 3.9},
        ]},
    ]
}


class TestStoryboardBuilder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        _escrever(self.base, "proj_teste/roteiro_transcricao.json", {"segments": FRASES})

    def tearDown(self):
        self.tmp.cleanup()

    def test_extrair_beats_enumera(self):
        beats = _extrair_beats("The root, the leaves, the flower, even the stem.")
        self.assertEqual(beats, ["root", "leaves", "flower", "stem"])

    def test_extrair_beats_unidade_unica(self):
        beats = _extrair_beats("Water your lawn every morning.")
        self.assertEqual(len(beats), 1)

    def test_localizar_beat(self):
        palavras = [("root,", 0.3, 0.8), ("flower,", 2.1, 2.8)]
        self.assertEqual(_localizar_beat(palavras, "root", 0, 4), (0.3, 0.8))
        self.assertEqual(_localizar_beat(palavras, "flower", 0, 4), (2.1, 2.8))
        self.assertIsNone(_localizar_beat(palavras, "stem", 0, 4))

    def test_desdobra_com_word_timestamps(self):
        _escrever(self.base, "proj_teste/word_timestamps.json", PALAVRAS)
        r = construir_storyboard("proj_teste", base_dir=self.base)
        self.assertTrue(r["success"])
        self.assertTrue(r["usou_word_timestamps"])
        # 1ª frase -> 4 beats (sub-cenas); 2ª e 3ª -> 1 cena cada = 6
        self.assertEqual(r["cenas_count"], 6)
        raiz = [s for s in r["scenes"] if s["text"] == "root"]
        flor = [s for s in r["scenes"] if s["text"] == "flower"]
        self.assertAlmostEqual(raiz[0]["start_sec"], 0.3, delta=0.05)
        self.assertAlmostEqual(flor[0]["start_sec"], 2.1, delta=0.05)

    def test_fallback_proporcional_sem_word_timestamps(self):
        r = construir_storyboard("proj_teste", base_dir=self.base)
        self.assertTrue(r["success"])
        self.assertFalse(r["usou_word_timestamps"])
        self.assertEqual(r["cenas_count"], 6)
        raiz = [s for s in r["scenes"] if s["text"] == "root"]
        self.assertAlmostEqual(raiz[0]["start_sec"], 0.0, delta=0.05)

    def test_cena_curta_vira_imagem(self):
        r = construir_storyboard("proj_teste", base_dir=self.base)
        curta = [s for s in r["scenes"] if s["text"] == "A short one."]
        self.assertEqual(curta[0]["media_type"], "photo")

    def test_media_type_atribuido(self):
        r = construir_storyboard("proj_teste", base_dir=self.base)
        for s in r["scenes"]:
            self.assertIn(s["media_type"], ("video", "photo"))

    def test_legado_nao_destruido(self):
        legado = [{"id": 1, "keywords": ["x"], "search_queries": ["x"], "media_preference": "video"}]
        _escrever(self.base, "proj_teste/storyboard.json", legado)
        r = construir_storyboard("proj_teste", base_dir=self.base)
        self.assertTrue(r["success"])
        self.assertIn("storyboard_beats.json", r["arquivo"])
        # legado preservado
        existente = json.loads((self.base / "proj_teste" / "storyboard.json").read_text(encoding="utf-8"))
        self.assertIn("keywords", existente[0])

    def test_linhas_para_prompt(self):
        construir_storyboard("proj_teste", base_dir=self.base)
        linhas = linhas_para_prompt("proj_teste", base_dir=self.base)
        self.assertTrue(linhas)
        self.assertRegex(linhas[0], r"^\[\d{2}:\d{2}-\d{2}:\d{2}\] \[(VIDEO|IMAGE)\] .+")

    def test_carregar_storyboard_ausente(self):
        self.assertEqual(carregar_storyboard("proj_teste", base_dir=self.base), [])


if __name__ == "__main__":
    unittest.main()
