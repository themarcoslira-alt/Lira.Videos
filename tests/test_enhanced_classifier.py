import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

"""Testes do Enhanced Narrative Scene Classifier (Fase 1)."""
import unittest

from services.enhanced_scene_classifier import (
    classify_scene,
    estimate_duration,
    get_broll_query,
    get_narrative_role,
    NARRATIVE_ROLES,
)


class TestEnhancedClassifier(unittest.TestCase):
    def test_hook_primeira_cena(self):
        r = classify_scene(
            "Olá, sou Marcos. Hoje vamos falar sobre como consertar grama queimada.",
            "avatar_talking", "00:00", scene_index=1,
        )
        self.assertEqual(r["narrative_role"], "HOOK")
        self.assertTrue(r["requires_avatar"])
        self.assertTrue(0.0 <= r["intensity"] <= 1.0)

    def test_hook_tempo_inicial_menor_5s(self):
        r = classify_scene("Bem-vindos de volta", "avatar_talking", "00:02", scene_index=5)
        self.assertEqual(r["narrative_role"], "HOOK")

    def test_broll_deteccao(self):
        r = classify_scene(
            "Mostrando a grama verde e as flores", "broll_macro", "00:30", scene_index=8,
        )
        self.assertEqual(r["narrative_role"], "BROLL")
        self.assertFalse(r["requires_avatar"])
        self.assertIsNotNone(r["broll_query"])
        self.assertIsInstance(r["broll_query"], str)

    def test_cta_deteccao(self):
        r = classify_scene("Clique no link para comprar o produto", "avatar_talking", "00:40", scene_index=9)
        self.assertEqual(r["narrative_role"], "CTA")

    def test_closing_deteccao(self):
        r = classify_scene("E é isso, valeu e até a próxima pessoal", "cta", "00:50", scene_index=10)
        self.assertEqual(r["narrative_role"], "CLOSING")

    def test_default_avatar(self):
        r = classify_scene("Aqui explicamos o passo a passo da técnica", "avatar_talking", "00:20", scene_index=6)
        self.assertEqual(r["narrative_role"], "AVATAR")

    def test_acao_leva_a_avatar(self):
        self.assertEqual(get_narrative_role("Estou aqui plantando as mudas"), "AVATAR")

    def test_broll_query_none_para_avatar(self):
        self.assertIsNone(get_broll_query("Estou aqui cortando a grama"))

    def test_broll_query_para_broll(self):
        self.assertIsNotNone(get_broll_query("A grama está verde e linda"))

    def test_duracao_por_role(self):
        self.assertEqual(estimate_duration("HOOK"), 5.0)
        self.assertEqual(estimate_duration("AVATAR"), 6.0)
        self.assertEqual(estimate_duration("BROLL"), 5.0)
        self.assertEqual(estimate_duration("CTA"), 4.0)
        self.assertEqual(estimate_duration("CLOSING"), 3.0)

    def test_intensity_no_range(self):
        r = classify_scene("Que grama queimada horrível!", "broll_macro", "00:25", scene_index=7)
        self.assertTrue(0.0 <= r["intensity"] <= 1.0)

    def test_roles_validos(self):
        for texto in [
            "Olá, sou Marcos",
            "Mostrando a grama verde",
            "Clique no link abaixo",
            "Valeu e até a próxima",
            "Explico aqui o passo a passo",
        ]:
            self.assertIn(get_narrative_role(texto), NARRATIVE_ROLES)


if __name__ == "__main__":
    unittest.main()