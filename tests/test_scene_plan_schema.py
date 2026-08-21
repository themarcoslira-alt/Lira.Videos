"""Testes do schema/validação do scene_plan.json (Fase 0)."""
import unittest

from services.scene_plan_schema import (
    STATUS_STAGES,
    STATUS_VALUES,
    VISUAL_PLAN_FIELDS,
    eh_valida_scene,
    eh_valido_scene_plan,
    nova_scene,
    nova_scene_plan,
    novo_status,
    validar_scene_plan,
    validar_status,
)


class TestScenePlanSchema(unittest.TestCase):
    def test_novo_status(self):
        st = novo_status()
        self.assertEqual(set(st), set(STATUS_STAGES))
        self.assertTrue(all(v == "pending" for v in st.values()))

    def test_status_valores(self):
        for valor in ("pending", "processing", "ready", "error"):
            self.assertEqual(validar_status(valor), [])
        self.assertTrue(validar_status("qualquer_coisa"))

    def test_nova_scene_completa(self):
        sc = nova_scene("scene_001", 0, 6.5, "narração", "00:00")
        self.assertTrue(eh_valida_scene(sc))
        self.assertEqual(sc["temporal"]["duration"], 6.5)
        self.assertEqual(sc["status"]["planning"], "pending")
        self.assertEqual(len(sc["visual_plan"]), len(VISUAL_PLAN_FIELDS))

    def test_nova_scene_plan_valida(self):
        plan = nova_scene_plan("proj", "Título", {"name": "p"})
        plan["scenes"].append(nova_scene("scene_001", 0, 5, "texto"))
        self.assertTrue(eh_valido_scene_plan(plan))

    def test_plan_sem_scenes_valido(self):
        plan = nova_scene_plan("proj", "Título", {})
        self.assertFalse(validar_scene_plan(plan))  # scenes vazia é válido (sem erros)

    def test_plan_invalido_cena_sem_status(self):
        sc = nova_scene("scene_001", 0, 5, "texto")
        del sc["status"]
        plan = nova_scene_plan("proj", "T", {})
        plan["scenes"].append(sc)
        self.assertFalse(eh_valido_scene_plan(plan))

    def test_status_independentes(self):
        sc = nova_scene("scene_001", 0, 5, "texto")
        sc["status"]["planning"] = "ready"
        sc["status"]["prompt"] = "ready"
        sc["status"]["media"] = "pending"
        sc["status"]["render"] = "pending"
        self.assertTrue(eh_valida_scene(sc))
        self.assertEqual(sc["status"]["media"], "pending")

    def test_status_invalido_rejeitado(self):
        sc = nova_scene("scene_001", 0, 5, "texto")
        sc["status"]["planning"] = "pronto"
        self.assertFalse(eh_valida_scene(sc))


if __name__ == "__main__":
    unittest.main()
