# -*- coding: utf-8 -*-
"""
tests/test_prioridade4.py — PRIORIDADE 4: Avatar GESTUAL (sem fala, apenas gestos)

Valida:
  1. _ajustar_prompt_avatar_gestual remove narração/fala e adiciona sufixo.
  2. Cena avatar sem narração → apenas o sufixo é adicionado.
  3. _detectar_cena_avatar reconhece avatar (uses_character/scene_type/nome).
  4. video_mode=False garantido para avatar + config de IMAGEM aplicada (fonte).
  5. Logs [AVATAR] presentes.
"""
import unittest
from pathlib import Path

import services.playwright_flow as pf

ROOT = Path(__file__).resolve().parent.parent


class TestAjustarPromptAvatarGestual(unittest.TestCase):
    """1 e 2 — helper de prompt da P4."""

    def test_avatar_com_narracao_remove_fala_e_adiciona_sufixo(self):
        base = "A gardener speaks to camera while pointing at the roses. Close-up of rose."
        out = pf._ajustar_prompt_avatar_gestual(base, "cena 3")
        self.assertNotIn("speaks to camera", out)
        self.assertNotIn("narration", out)
        self.assertIn("sem fala, apenas linguagem corporal expressiva", out)
        self.assertIn("Avatar gesticulando e expressando emoção para câmera", out)

    def test_avatar_sem_narracao_apenas_sufixo(self):
        base = "A gardener in a garden, warm light"
        out = pf._ajustar_prompt_avatar_gestual(base, "cena 4")
        self.assertIn(base, out)
        self.assertTrue(
            out.endswith("Avatar gesticulando e expressando emoção para câmera, "
                         "sem fala, apenas linguagem corporal expressiva."),
            out,
        )

    def test_sufixo_objetivo_quando_prompt_tem_show(self):
        base = "Show how to prune a rose bush."
        out = pf._ajustar_prompt_avatar_gestual(base)
        self.assertIn("usando gestos e expressão corporal para transmitir intenção/objetivo", out)

    def test_prompt_vazio_retorna_vazio(self):
        self.assertEqual(pf._ajustar_prompt_avatar_gestual(""), "")
        self.assertEqual(pf._ajustar_prompt_avatar_gestual(None), "")


class TestDetectarCenaAvatar(unittest.TestCase):
    """3 — detecção de avatar."""

    def test_uses_character_true(self):
        self.assertTrue(pf._detectar_cena_avatar({"uses_character": True}))

    def test_scene_type_avatar(self):
        self.assertTrue(pf._detectar_cena_avatar({"scene_type": "avatar_talking", "uses_character": False}))

    def test_nome_com_avatar(self):
        self.assertTrue(pf._detectar_cena_avatar({"nome": "Avatar principal", "scene_type": "broll"}))

    def test_broll_nao_avatar(self):
        self.assertFalse(pf._detectar_cena_avatar({"uses_character": False, "scene_type": "broll_macro", "texto": "close up of the rose"}))


class TestAvatarGestualFonte(unittest.TestCase):
    """4 e 5 — verificações de fonte (estilo dos testes existentes do projeto)."""

    def setUp(self):
        self.src = (ROOT / "services" / "playwright_flow.py").read_text(encoding="utf-8")

    def test_logs_avatar_e_modo_gestual(self):
        for trecho in [
            "[AVATAR] Cena '{nome_cena[:80]}' é AVATAR. Forçando modo gestual.",
            "[AVATAR] Prompt ajustado para gestualidade. Sufixo:",
            "sem fala, apenas linguagem corporal expressiva",
            "video_mode = False  # Avatar sempre gera imagem, nunca vídeo (gestual)",
        ]:
            self.assertIn(trecho, self.src)

    def test_regex_remove_fala_no_fonte(self):
        self.assertIn(r"(@fala|@speaks|@says|speaks to camera|says to camera|narration)", self.src)

    def test_avatar_usa_config_imagem(self):
        for trecho in [
            "_cfg_av.get(\"modelo\") or _cfg_av.get(\"prod_modelo_imagem\")",
            "if eh_avatar:",
            "_ajustar_prompt_avatar_gestual(prompt, nome_cena)",
        ]:
            self.assertIn(trecho, self.src)


if __name__ == "__main__":
    unittest.main()