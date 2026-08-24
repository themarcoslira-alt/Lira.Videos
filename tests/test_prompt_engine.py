import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

"""Testes do PromptEngine (Fase 1) — 15 obrigatórios + validações."""
import tempfile
import unittest
from pathlib import Path

from services.prompt_engine import (
    PROMPT_ENGINE_NAME,
    PROMPT_ENGINE_VERSION,
    PromptEngine,
    validar_animation_prompt,
    validar_image_prompt,
    validar_negative_prompt,
    validar_prompt_result,
)
from services.scene_plan_schema import nova_scene, nova_scene_plan
from services.scene_store import SceneStore
from services.visual_profile import VisualProfile


def _cena(texto="a lush green lawn under morning sun", subject="healthy residential lawn", **vp_overrides):
    sc = nova_scene("scene_001", 0, 6.0, texto, "00:00")
    vp = sc["visual_plan"]
    vp.update({
        "subject": subject,
        "action": "gently swaying in the breeze",
        "environment": "quiet backyard with trees",
        "shot": "medium",
        "camera": "static",
        "lighting": "soft morning light",
        "composition": "rule of thirds",
        "mood": "calm",
        "visual_intent": "establishing",
        "continuity": "",
    })
    vp.update(vp_overrides)
    return sc


def _perfil_2d():
    return VisualProfile(
        name="Educational 2D",
        style_lock="2D educational hand-drawn watercolor illustration, clean instructional visual language",
        character_lock="Consistent friendly educator character in every scene: same colors, proportions and outline.",
        world_lock="A warm, tidy educational backyard; consistent season and lighting across scenes.",
        composition_lock="Clear hierarchy, subject centered, generous margins for labels.",
        negative_lock="no photorealism, no 3D render, no text, no watermark",
    )


class TestPromptEngine(unittest.TestCase):
    def setUp(self):
        self.engine = PromptEngine()
        self.perfil = VisualProfile.default()

    # Teste 1 — Visual Plan simples → prompt válido
    def test_visual_plan_simples_prompt_valido(self):
        r = self.engine.generate(_cena(), self.perfil)
        self.assertTrue(r["success"])
        self.assertEqual(r["errors"], [])
        self.assertEqual(validar_image_prompt(r["image_prompt"]), [])
        self.assertEqual(validar_animation_prompt(r["animation_prompt"]), [])
        self.assertEqual(validar_negative_prompt(r["negative_prompt"]), [])

    # Teste 2 — Visual Profile → locks presentes
    def test_visual_profile_locks_presentes(self):
        r = self.engine.generate(_cena(), self.perfil)
        self.assertIn(self.perfil.style_lock, r["image_prompt"])
        self.assertIn(self.perfil.world_lock, r["image_prompt"])
        self.assertIn(self.perfil.composition_lock, r["image_prompt"])
        self.assertIn("Avoid: " + self.perfil.negative_lock, r["image_prompt"])

    # Teste 3 — Character Lock → preservado quando a cena tem pessoa
    def test_character_lock_preservado(self):
        cena = _cena(subject="a gardener pulling weeds", action="kneeling beside the lawn")
        r = self.engine.generate(cena, self.perfil)
        self.assertIn("Character:", r["image_prompt"])
        self.assertIn(self.perfil.character_lock, r["image_prompt"])

    # Teste 4 — Style Lock → preservado
    def test_style_lock_preservado(self):
        r = self.engine.generate(_cena(), self.perfil)
        self.assertIn(self.perfil.style_lock, r["image_prompt"])
        self.assertEqual(validar_image_prompt(r["image_prompt"], style=self.perfil.style_lock), [])

    # Teste 5 — World Lock → preservado
    def test_world_lock_preservado(self):
        r = self.engine.generate(_cena(), self.perfil)
        self.assertIn(self.perfil.world_lock, r["image_prompt"])

    # Teste 6 — Composition Lock → preservado
    def test_composition_lock_preservado(self):
        r = self.engine.generate(_cena(), self.perfil)
        self.assertIn(self.perfil.composition_lock, r["image_prompt"])

    # Teste 7 — Negative Lock → presente
    def test_negative_lock_presente(self):
        r = self.engine.generate(_cena(), self.perfil)
        self.assertIn("Avoid:", r["negative_prompt"])
        self.assertIn(self.perfil.negative_lock, r["negative_prompt"])
        self.assertIn("Avoid: " + self.perfil.negative_lock, r["image_prompt"])

    # Teste 8 — Conflito Visual Plan vs Lock → Lock vence
    def test_conflito_visual_plan_versus_lock_lock_vence(self):
        cena = _cena(subject="photorealistic close-up of a plant")
        r = self.engine.generate(cena, _perfil_2d())
        self.assertTrue(r["success"])
        self.assertIn(_perfil_2d().style_lock, r["image_prompt"])
        self.assertNotIn("photorealistic", r["image_prompt"].lower())

    # Teste 9 — Cena sem personagem → não inventar personagem
    def test_sem_personagem_nao_inventa(self):
        r = self.engine.generate(_cena(subject="healthy residential lawn"), self.perfil)
        self.assertNotIn("Character:", r["image_prompt"])

    # Teste 10 — Sem contexto anterior → funciona
    def test_sem_contexto_anterior_funciona(self):
        r = self.engine.generate(_cena(), self.perfil, previous_scene=None)
        self.assertTrue(r["success"])

    # Teste 11 — Com contexto anterior → continuidade aplicada
    def test_com_contexto_anterior_continuidade(self):
        prev = _cena(subject="lush backyard garden", texto="previous scene")
        r = self.engine.generate(_cena(), self.perfil, previous_scene=prev)
        self.assertIn("Continuity", r["image_prompt"])
        self.assertIn("same subject", r["image_prompt"].lower())

    # Teste 12 — Mesma entrada → mesmo prompt
    def test_mesma_entrada_mesmo_prompt(self):
        cena = _cena()
        r1 = self.engine.generate(cena, self.perfil)
        r2 = self.engine.generate(cena, self.perfil)
        self.assertEqual(r1, r2)

    # Teste 13 — Animation Prompt diferente de Image Prompt
    def test_animation_diferente_de_image(self):
        r = self.engine.generate(_cena(), self.perfil)
        self.assertNotEqual(r["animation_prompt"], r["image_prompt"])
        self.assertNotIn(r["image_prompt"], r["animation_prompt"])

    # Teste 14 — Sem duplicações excessivas
    def test_sem_duplicacoes_excessivas(self):
        r = self.engine.generate(_cena(), self.perfil)
        self.assertEqual(r["image_prompt"].count(self.perfil.style_lock), 1)
        linhas = [l.strip().lower() for l in r["image_prompt"].split("\n") if l.strip()]
        self.assertEqual(len(linhas), len(set(linhas)))

    # Teste 15 — SceneStore salva o resultado corretamente
    def test_scenestore_salva_resultado(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SceneStore("projeto", base_dir=Path(tmp))
            plan = nova_scene_plan("projeto", "P", self.perfil.to_dict())
            plan["scenes"] = [_cena()]
            self.assertTrue(store.save(plan))
            r = self.engine.generate(_cena(), self.perfil)
            self.assertTrue(r["success"])
            self.assertTrue(store.set_prompt("scene_001", r))
            carregado = store.load()
            prompt = carregado["scenes"][0]["prompt"]
            self.assertEqual(prompt["engine"], PROMPT_ENGINE_NAME)
            self.assertEqual(prompt["version"], PROMPT_ENGINE_VERSION)
            self.assertEqual(prompt["image_prompt"], r["image_prompt"])
            self.assertEqual(prompt["animation_prompt"], r["animation_prompt"])
            self.assertEqual(prompt["negative_prompt"], r["negative_prompt"])

    # --------------------------------------------------------------- extras

    def test_animacao_controlada_por_padrao(self):
        r = self.engine.generate(_cena(), self.perfil)
        self.assertIn("Subtle", r["animation_prompt"])
        self.assertNotIn("chaotic", r["animation_prompt"].lower())

    def test_animacao_reforca_movimento_quando_plan_pede(self):
        cena = _cena(mood="dynamic", visual_intent="action")
        r = self.engine.generate(cena, self.perfil)
        self.assertIn("Moderate", r["animation_prompt"])

    def test_generate_sem_scene_retorna_erro(self):
        r = self.engine.generate(None, self.perfil)
        self.assertFalse(r["success"])
        self.assertTrue(r["errors"])

    def test_generate_sem_style_lock_erro(self):
        cena = _cena()
        cena["locks"]["style"] = ""
        r = self.engine.generate(cena, None)
        self.assertFalse(r["success"])

    def test_generate_many(self):
        cenas = [_cena(subject="a"), _cena(subject="b"), _cena(subject="c")]
        resultados = self.engine.generate_many(cenas, self.perfil)
        self.assertEqual(len(resultados), 3)
        self.assertTrue(all(x["resultado"]["success"] for x in resultados))

    def test_validar_prompt_result(self):
        r = self.engine.generate(_cena(), self.perfil)
        self.assertEqual(validar_prompt_result(r), [])


if __name__ == "__main__":
    unittest.main()
