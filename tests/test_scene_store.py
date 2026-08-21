"""Testes do SceneStore (Fase 0) — persistência do scene_plan.json."""
import tempfile
import unittest
from pathlib import Path

from services.scene_plan_schema import nova_scene, nova_scene_plan
from services.scene_store import SceneStore
from services.visual_profile import VisualProfile


class TestSceneStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = SceneStore("projeto_teste", base_dir=self.base)
        self.plan = nova_scene_plan("projeto_teste", "Projeto Teste", VisualProfile.default().to_dict())
        self.plan["scenes"] = [nova_scene("scene_001", 0, 6.5, "narração um", "00:00"),
                               nova_scene("scene_002", 6.5, 12.0, "narração dois", "00:06")]

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_load_roundtrip(self):
        self.assertTrue(self.store.save(self.plan))
        carregado = self.store.load()
        self.assertIsNotNone(carregado)
        self.assertEqual(len(carregado["scenes"]), 2)
        self.assertEqual(carregado["project"]["id"], "projeto_teste")

    def test_load_ausente_retorna_none(self):
        self.assertIsNone(self.store.load())

    def test_upsert_preserva_demais_cenas(self):
        self.store.save(self.plan)
        nova = nova_scene("scene_003", 12, 18, "terceira")
        self.assertTrue(self.store.upsert_scene(nova))
        plan = self.store.load()
        self.assertEqual(len(plan["scenes"]), 3)

    def test_update_status_independente(self):
        self.store.save(self.plan)
        self.assertTrue(self.store.update_status("scene_001", "planning", "ready"))
        self.assertTrue(self.store.update_status("scene_001", "prompt", "ready"))
        sc = self.store.get_scene("scene_001")
        self.assertEqual(sc["status"]["planning"], "ready")
        self.assertEqual(sc["status"]["prompt"], "ready")
        self.assertEqual(sc["status"]["media"], "pending")
        self.assertEqual(sc["status"]["render"], "pending")

    def test_update_status_invalido_rejeitado(self):
        self.store.save(self.plan)
        self.assertFalse(self.store.update_status("scene_001", "planning", "pronto"))
        self.assertFalse(self.store.update_status("scene_001", "nao_existe", "ready"))

    def test_update_visual_plan_e_locks(self):
        self.store.save(self.plan)
        vp = {"visual_intent": "closeup", "subject": "dandelion", "action": "pulling", "environment": "garden",
              "shot": "closeup", "camera": "handheld", "lighting": "golden hour", "composition": "rule of thirds",
              "mood": "curious", "continuity": "same plant"}
        self.assertTrue(self.store.update_visual_plan("scene_001", vp))
        locks = {"style": "s", "character": "c", "world": "w", "composition": "co", "negative": "n"}
        self.assertTrue(self.store.update_locks("scene_001", locks))
        sc = self.store.get_scene("scene_001")
        self.assertEqual(sc["visual_plan"]["subject"], "dandelion")
        self.assertEqual(sc["locks"]["negative"], "n")

    def test_update_prompt(self):
        self.store.save(self.plan)
        self.assertTrue(self.store.update_prompt("scene_001", "prompt da cena"))
        sc = self.store.get_scene("scene_001")
        self.assertEqual(sc["prompt"]["text"], "prompt da cena")
        self.assertIsNotNone(sc["prompt"]["generated_at"])

    def test_update_media_plan(self):
        self.store.save(self.plan)
        mp = {"primary_queries": ["dandelion root"], "fallback_queries": ["weed"], "synonyms": ["taproot"]}
        self.assertTrue(self.store.update_media_plan("scene_001", mp))
        sc = self.store.get_scene("scene_001")
        self.assertEqual(sc["media_plan"]["primary_queries"], ["dandelion root"])

    def test_set_selected_media(self):
        self.store.save(self.plan)
        self.assertTrue(self.store.set_selected_media("scene_001", [
            {"arquivo": "/x/y.mp4", "media_type": "video", "quality": "green", "origem": "api"}]))
        sc = self.store.get_scene("scene_001")
        self.assertEqual(len(sc["selected_media"]), 1)

    def test_save_plan_invalido_retorna_false(self):
        invalido = nova_scene_plan("proj", "T", {})
        sc = nova_scene("scene_001", 0, 5, "texto")
        del sc["status"]
        invalido["scenes"].append(sc)
        self.assertFalse(self.store.save(invalido))

    def test_criar_scenes_de_cenas(self):
        cenas = [
            {"id": 1, "start_time": 0.0, "end_time": 6.5, "texto": "cena um", "timestamps": ["00:00"]},
            {"id": 2, "start_time": 6.5, "end_time": 12.0, "texto": "cena dois", "timestamps": ["00:06"]},
        ]
        scenes = SceneStore.criar_scenes_de_cenas(cenas)
        self.assertEqual(len(scenes), 2)
        self.assertEqual(scenes[0]["id"], "scene_001")
        self.assertEqual(scenes[0]["temporal"]["duration"], 6.5)

    def test_build_from_legacy(self):
        cenas = [{"id": 1, "start_time": 0.0, "end_time": 5.0, "texto": "texto", "timestamps": ["00:00"]}]
        storyboard = [{"id": 1, "visual_intent": "closeup", "subject": "planta", "action": "crescer",
                       "environment": "jardim", "shot_type": "closeup", "emotion": "curioso",
                       "search_queries": ["planta crescendo"], "fallback_queries": ["jardim"]}]
        midias = [{"scene_id": 1, "success": True, "arquivo": "/cache/scene_1/x.mp4",
                   "media_type": "video", "quality": "green", "origem_midia": "api"}]
        plan = self.store.build_from_legacy(cenas, storyboard, midias)
        sc = plan["scenes"][0]
        self.assertEqual(sc["visual_plan"]["subject"], "planta")
        self.assertEqual(sc["media_plan"]["primary_queries"], ["planta crescendo"])
        self.assertEqual(len(sc["selected_media"]), 1)
        self.assertEqual(sc["status"]["planning"], "ready")
        self.assertEqual(sc["status"]["media"], "ready")


if __name__ == "__main__":
    unittest.main()
