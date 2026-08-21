"""Testes do BLOCO 3 — word-level timestamps (subprocesso de transcrição)."""
import unittest

from _transcrever_subprocesso import _coletar_palavras


class FakeWord:
    def __init__(self, w, s, e):
        self.word = w
        self.start = s
        self.end = e


class FakeSeg:
    words = [
        FakeWord("The", 0.0, 0.25),
        FakeWord("root,", 0.26, 0.5),
        FakeWord("the", 0.51, 0.6),
        FakeWord("leaves,", 0.61, 0.9),
    ]


class SemWords:
    words = None


class TestWordTimestamps(unittest.TestCase):
    def test_coleta_palavras(self):
        palavras = _coletar_palavras(FakeSeg())
        self.assertEqual([p["w"] for p in palavras], ["The", "root,", "the", "leaves,"])
        self.assertEqual(palavras[1]["s"], 0.26)
        self.assertEqual(palavras[1]["e"], 0.5)

    def test_sem_words_retorna_vazio(self):
        self.assertEqual(_coletar_palavras(SemWords()), [])

    def test_palavra_vazia_ignorada(self):
        class C:
            words = [FakeWord("", 0, 1), FakeWord("x", 1, 2)]
        self.assertEqual([p["w"] for p in _coletar_palavras(C())], ["x"])


if __name__ == "__main__":
    unittest.main()
