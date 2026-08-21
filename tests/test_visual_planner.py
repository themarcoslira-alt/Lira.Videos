"""Testes do VisualPlanner (Fase 0) — contrato, retry e erro."""
import json
import unittest

from services.scene_plan_schema import VISUAL_PLAN_FIELDS, nova_scene
from services.visual_planner import VisualPlanner, _normalizar_visual_plan
from services.visual_profile import VisualProfile

PLANO_VALIDO = {
    "visual_intent": "closeup",
    "subject": "dandelion root",
    "action": "being pulled",
    "environment": "sunlit garden",
    "shot": "closeup",
    "camera": "handheld",
    "lighting": "golden hour",
    "composition": "rule of thirds",
    "mood": "curious",
    "continuity": "same plant as scene 1",
}


class FakeProvider:
    """Provider de teste: respostas configuráveis + contador de chamadas."""

    name = "fake"

    def __init__(self, respostas=None, erro=None):
        self.respostas = list(respostas or [])
        self.erro = erro
        self.chamadas = 0
        self.model = "fake-model"

    def generate(self, messages, model=None, temperature=0.2, max_tokens=2000):
        self.chamadas += 1
        if self.erro is not None:
            raise self.erro
        if self.respostas:
            resposta = self.respostas.pop(0)
            if callable(resposta):
                return resposta()
            return resposta
        return json.dumps(PLANO_VALIDO)


class TestVisualPlanner(unittest.TestCase):
    def setUp(self):
        self.scene = nova_scene("scene_001", 0, 6.5, "the dandelion root can be used for tea", "00:00")
        self.profile = VisualProfile.from_preset("photorealistic_cinematic")
        self.planner = VisualPlanner(retry_delay=0)

    def test_planejamento_local_valido_e_deterministico(self):
        r1 = self.planner.planejar_cena(self.scene, self.profile)
        r2 = self.planner.planejar_cena(self.scene, self.profile)
        self.assertTrue(r1["success"])
        self.assertEqual(r1["mode"], "local")
        self.assertEqual(r1["visual_plan"], r2["visual_plan"])
        for campo in VISUAL_PLAN_FIELDS:
            self.assertIn(campo, r1["visual_plan"])
        # locks resolvidos
        self.assertEqual(set(r1["locks"]), {"style", "character", "world", "composition", "negative"})
        self.assertEqual(r1["locks"]["style"], self.profile.style_lock)

    def test_planejamento_llm_valido(self):
        provider = FakeProvider()
        r = self.planner.planejar_cena(self.scene, self.profile, provider=provider)
        self.assertTrue(r["success"])
        self.assertEqual(r["mode"], "llm")
        self.assertEqual(r["visual_plan"]["subject"], "dandelion root")
        self.assertEqual(provider.chamadas, 1)

    def test_resposta_invalida_retry_e_erro(self):
        provider = FakeProvider(respostas=["isto não é json", "também não", "nada"])
        r = self.planner.planejar_cena(self.scene, self.profile, provider=provider)
        self.assertFalse(r["success"])
        self.assertEqual(provider.chamadas, 3)  # max_retries=2 → 3 tentativas

    def test_retry_apos_falha_transiente(self):
        def falha_na_primeira():
            raise RuntimeError("network timeout")

        provider = FakeProvider(respostas=[falha_na_primeira])
        r = self.planner.planejar_cena(self.scene, self.profile, provider=provider)
        self.assertTrue(r["success"])
        self.assertEqual(provider.chamadas, 2)

    def test_normalizacao_preenche_campos(self):
        plano = _normalizar_visual_plan({"subject": "planta"})
        for campo in VISUAL_PLAN_FIELDS:
            self.assertIsInstance(plano[campo], str)
        self.assertEqual(plano["subject"], "planta")
        self.assertEqual(plano["shot"], "medium")  # default

    def test_planejar_todas(self):
        cenas = [nova_scene("scene_001", 0, 5, "texto um"), nova_scene("scene_002", 5, 10, "texto dois")]
        resultados = self.planner.planejar_todas(cenas, self.profile)
        self.assertEqual(len(resultados), 2)
        self.assertTrue(all(r["resultado"]["success"] for r in resultados))


if __name__ == "__main__":
    unittest.main()
