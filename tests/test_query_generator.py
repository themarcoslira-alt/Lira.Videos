"""Testes do QueryGenerator (Fase 0) — determinístico, por provider."""
import unittest

from services.query_engine import PROVIDER_PROFILES, QueryGenerator


class TestQueryGenerator(unittest.TestCase):
    def setUp(self):
        self.qg = QueryGenerator()
        self.vp = {
            "visual_intent": "closeup",
            "subject": "dandelion root",
            "action": "being pulled from soil",
            "environment": "sunlit garden",
            "shot": "closeup",
            "camera": "handheld",
            "lighting": "golden hour",
            "composition": "rule of thirds",
            "mood": "curious",
            "continuity": "same plant",
        }

    def test_generate_deterministico(self):
        r1 = self.qg.generate(self.vp)
        r2 = self.qg.generate(self.vp)
        self.assertEqual(r1, r2)

    def test_generate_estrutura(self):
        r = self.qg.generate(self.vp)
        self.assertIn("primary_queries", r)
        self.assertIn("fallback_queries", r)
        self.assertIn("synonyms", r)
        self.assertTrue(r["primary_queries"])
        self.assertLessEqual(len(r["primary_queries"]), 4)
        self.assertLessEqual(len(r["fallback_queries"]), 3)

    def test_primary_inclui_subject_action(self):
        r = self.qg.generate(self.vp)
        self.assertIn("dandelion root being pulled from soil", r["primary_queries"])

    def test_generate_vazio(self):
        r = self.qg.generate(None)
        self.assertEqual(r["primary_queries"], [])
        self.assertEqual(r["fallback_queries"], [])

    def test_generate_for_provider_pexels(self):
        r = self.qg.generate_for_provider(self.vp, "pexels")
        self.assertTrue(r["primary_queries"])
        self.assertLessEqual(len(r["primary_queries"]), PROVIDER_PROFILES["pexels"]["max_primary"])

    def test_generate_for_provider_image_generation_reforca_mood(self):
        r = self.qg.generate_for_provider(self.vp, "image_generation")
        self.assertTrue(any("curious" in q for q in r["primary_queries"]))

    def test_generate_for_provider_desconhecido_usa_default(self):
        r = self.qg.generate_for_provider(self.vp, "nao_existe")
        self.assertTrue(r["primary_queries"])


if __name__ == "__main__":
    unittest.main()
