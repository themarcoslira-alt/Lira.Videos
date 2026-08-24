import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

"""Testes do BLOCO 6 — segmentação consistente (VAD + pós-processamento)."""
import unittest

from _transcrever_subprocesso import (
    MAX_SENTENCAS_SEGMENTO,
    TETO_DURACAO_SEGMENTO,
    VAD_FILTER,
    _fmt_mmss,
    _quebrar_segmentos_longos,
    _tempos_finais_sentencas,
)


def _seg(texto, start, end, words=None):
    s = {"start": start, "end": end, "text": texto, "timestamp": _fmt_mmss(start)}
    if words is not None:
        s["words"] = words
    return s


class TestSegmentacao(unittest.TestCase):
    def test_constantes_vad(self):
        self.assertTrue(VAD_FILTER)
        self.assertEqual(TETO_DURACAO_SEGMENTO, 8.0)
        self.assertEqual(MAX_SENTENCAS_SEGMENTO, 2)

    def test_fmt_mmss(self):
        self.assertEqual(_fmt_mmss(65), "01:05")

    def test_nao_quebra_segmento_curto(self):
        segs = [_seg("It's called the dandelion.", 0, 3.0)]
        saida = _quebrar_segmentos_longos(segs)
        self.assertEqual(len(saida), 1)

    def test_quebra_por_muitas_sentencas(self):
        # 3 sentenças, mesmo dentro do teto -> quebra em 3 (max_sentencas=2)
        texto = "One sentence. Two sentences. Three sentences."
        palavras = [
            {"w": "One", "s": 0.0, "e": 0.3}, {"w": "sentence.", "s": 0.3, "e": 0.8},
            {"w": "Two", "s": 1.0, "e": 1.3}, {"w": "sentences.", "s": 1.3, "e": 1.9},
            {"w": "Three", "s": 2.1, "e": 2.4}, {"w": "sentences.", "s": 2.4, "e": 3.0},
        ]
        segs = [_seg(texto, 0, 3.0, palavras)]
        saida = _quebrar_segmentos_longos(segs)
        self.assertEqual(len(saida), 3)
        self.assertIn("One sentence.", saida[0]["text"])
        self.assertIn("Three sentences.", saida[2]["text"])

    def test_quebra_por_teto_sem_palavras(self):
        # 1 sentença, 14s -> blocos de ~8s (2 blocos, fallback proporcional)
        segs = [_seg("A very long single sentence that goes on and on.", 0, 14.0)]
        saida = _quebrar_segmentos_longos(segs)
        self.assertEqual(len(saida), 2)
        self.assertLessEqual(saida[0]["end"] - saida[0]["start"], TETO_DURACAO_SEGMENTO + 0.01)

    def test_tempos_finais_proporcionais(self):
        tempos = _tempos_finais_sentencas("A. B. C.", [2, 6, 10], [], 0, 12)
        self.assertEqual(len(tempos), 3)
        self.assertAlmostEqual(tempos[0], 4.0)
        self.assertAlmostEqual(tempos[2], 12.0)


if __name__ == "__main__":
    unittest.main()
