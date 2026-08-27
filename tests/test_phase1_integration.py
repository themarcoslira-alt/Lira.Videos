import sys
import json
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

"""Testes integrados da Fase 1 (Lira Studio): classificação + avatar + migrate."""
from config import PROJETOS_DIR
from services.enhanced_scene_classifier import classify_scene, NARRATIVE_ROLES
from services.avatar_decision_service import decide_avatar_or_broll


class TestPhase1Integration(unittest.TestCase):
    def test_classify_hook_scene(self):
        r = classify_scene(
            "Olá, sou Marcos. Hoje vamos falar sobre como consertar grama queimada.",
            "avatar_talking", "00:00", scene_index=1,
        )
        self.assertEqual(r["narrative_role"], "HOOK")

    def test_classify_broll_scene(self):
        r = classify_scene(
            "Mostrando a grama verde e as folhas macias", "broll_macro", "00:20", scene_index=3,
        )
        self.assertEqual(r["narrative_role"], "BROLL")
        self.assertFalse(r["requires_avatar"])

    def test_classify_cta_scene(self):
        r = classify_scene(
            "Clique no link para se inscrever no canal", "avatar_talking", "00:30", scene_index=5,
        )
        self.assertEqual(r["narrative_role"], "CTA")

    def test_avatar_decision_logic(self):
        tipo, valor = decide_avatar_or_broll("HOOK")
        self.assertEqual(tipo, "avatar")
        self.assertIsInstance(valor, str)

    def test_avatar_decision_logic_broll(self):
        tipo, valor = decide_avatar_or_broll("BROLL", {"texto": "Close na flor", "scene_type": "broll_macro"})
        self.assertEqual(tipo, "broll")
        self.assertTrue(valor)

    def test_brand_profile_load(self):
        from services.brand_profile_service import load_brand_profile
        p = load_brand_profile()
        for k in (
            "channel_name", "presenter_name", "caption_style", "avatar_config",
            "video_mix_ratio", "quality_settings", "capcut_integration",
        ):
            self.assertIn(k, p)

    def test_migrate_scene_plan(self):
        import scripts.migrate_lira_scene_plan_v2 as mig
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            pp = base / "projX"
            pp.mkdir(parents=True)
            plano = {
                "projeto": "projX",
                "versao": 2,
                "cenas": [
                    {
                        "id": 1,
                        "scene_index": 1,
                        "texto": "Olá, sou Marcos, bem-vindos",
                        "scene_type": "avatar_talking",
                        "tempo_inicio": 0.0,
                        "status": "PENDENTE",
                        "emotion": "curiosity",
                        "prompt_imagem": "demo",
                    }
                ],
            }
            (pp / "lira_scene_plan.json").write_text(
                json.dumps(plano, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            r = mig.migrar_projeto("projX", projetos_dir=base)
            self.assertIsNotNone(r)
            novo = json.loads((pp / "lira_scene_plan.json").read_text(encoding="utf-8"))
            c = novo["cenas"][0]
            self.assertEqual(c["narrative_role"], "HOOK")
            self.assertTrue(c["avatar_required"])
            for f in mig.NOVOS_CAMPOS:
                self.assertIn(f, c)
            # Campos existentes preservados
            self.assertEqual(c["texto"], "Olá, sou Marcos, bem-vindos")
            self.assertEqual(c["status"], "PENDENTE")
            self.assertEqual(c["emotion"], "curiosity")

    def test_end_to_end_historia(self):
        import scripts.migrate_lira_scene_plan_v2 as mig
        plan_path = PROJETOS_DIR / "Historia" / "lira_scene_plan.json"
        if not plan_path.exists():
            self.skipTest("Projeto Historia sem lira_scene_plan.json de produção")
        # Idempotente: migra o projeto real Historia (aditivo)
        mig.migrar_todos(apenas="Historia")
        self.assertTrue(plan_path.exists())
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        cenas = plan["cenas"]
        self.assertGreaterEqual(len(cenas), 100)
        for c in cenas:
            self.assertIn(c.get("narrative_role"), NARRATIVE_ROLES)
            self.assertIsInstance(c.get("avatar_required"), bool)
            self.assertIn(c.get("broll_status", "NOT_STARTED"),
                          ("NOT_STARTED", "GENERATING", "READY", "ERROR"))
        # Valida de forma independente pelo classificador
        total = 0
        for c in cenas:
            texto = c.get("texto") or c.get("narration") or ""
            if not texto:
                continue
            total += 1
            r = classify_scene(
                texto, str(c.get("scene_type") or ""), str(c.get("tempo_inicio") or ""),
            )
            self.assertIn(r["narrative_role"], NARRATIVE_ROLES)
        self.assertGreaterEqual(total, 100)


if __name__ == "__main__":
    unittest.main()