import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

"""Testes da correção do BLOCO 6 no pai — `_agrupar_em_frases` (services/transcriber.py)."""
import unittest

from services.transcriber import _agrupar_em_frases, _quebra_leve


def _seg(texto, start, end, words=None):
    s = {"start": start, "end": end, "text": texto, "timestamp": ""}
    if words is not None:
        s["words"] = words
    return s


class TestAgruparFrases(unittest.TestCase):
    def test_segmento_curto_completo_nao_agrupa(self):
        # 2 segmentos curtos completos (com .) → 2 frases (canônico, sem agrupar)
        segs = [_seg("There's a lawn near you.", 0, 1.2),
                _seg("Maybe right next door.", 1.5, 2.8)]
        frases = _agrupar_em_frases(segs)
        self.assertEqual(len(frases), 2)
        self.assertIn("lawn near you", frases[0]["text"])
        self.assertIn("right next door", frases[1]["text"])

    def test_fragmento_junta_a_frase_anterior(self):
        # sentença completa + fragmento solto (1 palavra) → junta
        segs = [_seg("The root is deep.", 0, 1.5), _seg("Very", 1.7, 2.0)]
        frases = _agrupar_em_frases(segs)
        self.assertEqual(len(frases), 1)
        self.assertIn("Very", frases[0]["text"])

    def test_teto_8s_quebra(self):
        segs = [_seg("one part", 0, 3.0), _seg("two part", 3.0, 6.0),
                _seg("three part", 6.0, 9.0), _seg("four part", 9.0, 12.0)]
        frases = _agrupar_em_frases(segs)
        self.assertGreaterEqual(len(frases), 2)
        for f in frases:
            self.assertLessEqual(f["end"] - f["start"], 8.0 + 0.01)

    def test_quebra_leve_apos_5s(self):
        segs = [_seg("the root", 0, 2.0), _seg("the leaves, and", 2.0, 4.0),
                _seg("the stem grows", 4.2, 6.0)]
        frases = _agrupar_em_frases(segs)
        self.assertEqual(len(frases), 2)  # acumulado 6s ≥ 5s + ", and" → quebra

    def test_pausa_maior_que_015_quebra(self):
        segs = [_seg("primeira fala", 0, 1.0), _seg("segunda fala", 1.5, 2.5)]
        frases = _agrupar_em_frases(segs)
        self.assertEqual(len(frases), 2)

    def test_palavras_mescladas_na_frase(self):
        segs = [
            _seg("The root", 0, 1.0, words=[{"w": "The", "s": 0.0, "e": 0.2}, {"w": "root", "s": 0.2, "e": 0.9}]),
            _seg("and the leaf", 1.0, 2.0, words=[{"w": "and", "s": 1.0, "e": 1.2}, {"w": "leaf", "s": 1.3, "e": 1.9}]),
        ]
        frases = _agrupar_em_frases(segs)
        self.assertEqual(len(frases), 1)  # sem pontuação e sem pausa → mescla
        self.assertEqual(len(frases[0]["words"]), 4)

    def test_quebra_leve_helper(self):
        self.assertTrue(_quebra_leve("e o gramado, and"))
        self.assertTrue(_quebra_leve("ponto e vírgula;"))
        self.assertFalse(_quebra_leve("sem pontuação"))


if __name__ == "__main__":
    unittest.main()
