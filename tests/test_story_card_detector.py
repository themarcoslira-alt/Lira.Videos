# -*- coding: utf-8 -*-
"""
Testes unitários para o detector de Story Cards pedagógicos
(services/story_card_detector_service.py).
"""

import unittest
from services.story_card_detector_service import (
    detect_story_cards,
    apply_story_cards_to_plan,
    COLOR_PALETTES,
)


class TestStoryCardDetector(unittest.TestCase):
    def test_detect_steps(self):
        cenas = [
            {"id": 1, "narration": "In step one, we test the soil pH and structure."},
            {"id": 2, "narration": "Moving on to step 2, apply the product evenly across your lawn."},
            {"id": 3, "narration": "Now for passo 3, water deeply immediately after spreading."},
            {"id": 4, "narration": "During step four, wait for microbial activation."},
            {"id": 5, "narration": "Finally in step 5, admire your healthy root zone."},
        ]
        markers = detect_story_cards(cenas)
        self.assertEqual(len(markers), 5)
        
        # Validação passo a passo
        tipos = [m["story_card_type"] for m in markers]
        self.assertEqual(tipos, ["step_1", "step_2", "step_3", "step_4", "step_5"])

        # Validação de estilos e paletas
        self.assertEqual(markers[0]["story_card_style"], "step_intro")
        self.assertEqual(markers[0]["story_card_config"]["background_color"], COLOR_PALETTES["step_1"]["bg"])
        self.assertEqual(markers[0]["story_card_config"]["title_color"], COLOR_PALETTES["step_1"]["title_color"])
        self.assertEqual(markers[1]["story_card_config"]["background_color"], COLOR_PALETTES["step_2"]["bg"])
        self.assertEqual(markers[4]["story_card_config"]["background_color"], COLOR_PALETTES["step_5"]["bg"])

    def test_detect_myths(self):
        cenas = [
            {"id": 10, "narration": "The first myth people believe is that gypsum lowers soil pH."},
            {"id": 11, "narration": "Here is myth 2: you must apply it every single month."},
            {"id": 12, "narration": "The third myth is about clay soils needing sand."},
            {"id": 13, "narration": "And the fourth myth concerns salt buildup."},
        ]
        markers = detect_story_cards(cenas)
        self.assertEqual(len(markers), 4)

        for i, m in enumerate(markers, 1):
            self.assertEqual(m["story_card_type"], f"myth_{i}")
            self.assertEqual(m["story_card_style"], "vox_editorial")
            self.assertEqual(m["story_card_config"]["title"], f"MYTH {i}")
            self.assertEqual(m["story_card_config"]["title_color"], COLOR_PALETTES["myth"]["title_color"])

    def test_detect_mistakes_and_alerts(self):
        cenas = [
            {"id": 20, "narration": "Mistake one is applying gypsum on sandy soil without compost."},
            {"id": 21, "narration": "Erro 2: forgetting to water the grass right after application."},
            {"id": 22, "narration": "Warning: never inhale the powder directly."},
            {"id": 23, "narration": "Alerta importante: check local regulations before runoff."},
        ]
        markers = detect_story_cards(cenas)
        self.assertEqual(len(markers), 4)

        self.assertEqual(markers[0]["story_card_type"], "alert_1")
        self.assertEqual(markers[0]["story_card_style"], "alert_warning")
        self.assertEqual(markers[0]["story_card_config"]["title"], "MISTAKE 1")

        self.assertEqual(markers[1]["story_card_type"], "alert_2")
        self.assertEqual(markers[1]["story_card_style"], "alert_warning")

    def test_detect_data_viz(self):
        cenas = [
            {"id": 30, "narration": "Homeowners spend over $500 on unnecessary lawn supplements."},
            {"id": 31, "narration": "Yields can increase by up to 35% in compacted clay."},
            {"id": 32, "narration": "A typical farm applies 50 pounds per thousand square feet."},
        ]
        markers = detect_story_cards(cenas)
        self.assertEqual(len(markers), 3)

        for m in markers:
            self.assertEqual(m["story_card_type"], "data_viz")
            self.assertEqual(m["story_card_style"], "data_viz")
            self.assertEqual(m["story_card_config"]["title_color"], COLOR_PALETTES["data_viz"]["title_color"])

    def test_detect_timeline(self):
        cenas = [
            {"id": 40, "narration": "By week 2, visible root elongation occurs beneath the sod."},
            {"id": 41, "narration": "Allow 3 to 4 weeks for full soil flocculation."},
        ]
        markers = detect_story_cards(cenas)
        self.assertEqual(len(markers), 2)

        for m in markers:
            self.assertEqual(m["story_card_type"], "timeline")
            self.assertEqual(m["story_card_style"], "timeline")

    def test_apply_story_cards_to_plan(self):
        plan = {
            "cenas": [
                {"id": 1, "narration": "Step 1: aerate your compacted lawn."},
                {"id": 2, "narration": "Regular grass maintenance throughout summer."},
                {"id": 3, "narration": "Myth 1: gypsum fixes all lawn problems."},
            ]
        }
        res = apply_story_cards_to_plan(plan)
        cenas = res["cenas"]

        # Cena 1: Step 1
        self.assertTrue(cenas[0]["is_story_card"])
        self.assertEqual(cenas[0]["story_card_type"], "step_1")
        self.assertEqual(cenas[0]["story_card_style"], "step_intro")
        self.assertIn("#F0EDB8", cenas[0]["story_card_prompt_imagem"])
        self.assertIn("#000000", cenas[0]["story_card_prompt_imagem"])

        # Cena 2: Sem card
        self.assertFalse(cenas[1]["is_story_card"])
        self.assertEqual(cenas[1]["story_card_type"], "")
        self.assertEqual(cenas[1]["story_card_prompt_imagem"], "")

        # Cena 3: Myth 1
        self.assertTrue(cenas[2]["is_story_card"])
        self.assertEqual(cenas[2]["story_card_type"], "myth_1")
        self.assertEqual(cenas[2]["story_card_style"], "vox_editorial")
        self.assertIn("#C5221F", cenas[2]["story_card_prompt_imagem"])

    def test_detect_comparison_cards(self):
        """Valida a detecção de comparison_card para padrões comparativos."""
        cenas = [
            {"id": 1, "narration": "Identificando o percevejo juvenil vs adulto nas folhas.", "start": 0.0, "end": 4.0},
            {"id": 2, "narration": "Veja a diferença entre adubo químico e orgânico.", "start": 4.0, "end": 8.0},
            {"id": 3, "narration": "Colocando lado a lado para um comparativo claro.", "start": 8.0, "end": 12.0},
        ]
        markers = detect_story_cards(cenas)
        self.assertEqual(len(markers), 3)

        for m in markers:
            self.assertEqual(m["story_card_type"], "comparison_card")
            self.assertEqual(m["story_card_style"], "side_by_side")
            self.assertEqual(m["badge_preset"], "borda_preta_pop")
            self.assertEqual(m["layout"], "side_by_side")
            self.assertIn("texto", m)
            self.assertIn("timing_start_ms", m)

        # Validação do título do badge e extração de texto
        self.assertEqual(markers[0]["story_card_config"]["badge_preset"], "borda_preta_pop")
        self.assertEqual(markers[0]["story_card_config"]["layout"], "side_by_side")
        self.assertIn("juvenil", markers[0]["texto"].lower())

    def test_apply_story_cards_com_comparison_card(self):
        """Valida que apply_story_cards_to_plan seta labeled_comparison_diagram e static_freeze."""
        plan = {
            "cenas": [
                {"id": 1, "narration": "Percevejo juvenil vs adulto no jardim.", "start": 0.0, "end": 4.0}
            ]
        }
        res = apply_story_cards_to_plan(plan)
        c = res["cenas"][0]
        self.assertTrue(c["is_story_card"])
        self.assertEqual(c["story_card_type"], "comparison_card")
        self.assertEqual(c["tipo"], "labeled_comparison_diagram")
        self.assertEqual(c["scene_type"], "labeled_comparison_diagram")
        self.assertFalse(c["should_animate"])
        self.assertEqual(c["animation_type"], "static_freeze")
        self.assertIn("comparison diagram", c["story_card_prompt_imagem"].lower())

    def test_brain_director_comparison_card_end_to_end_and_capcut_badge(self):
        """Valida a integração completa: roteiro com juvenil vs adulto -> comparison_card -> static_freeze -> timeline CapCut."""
        from services.brain_system_director import BrainSystemDirector
        from capcut_draft_imagens import _gerar_trilha_texto

        director = BrainSystemDirector("test_comparison_card_proj")
        entrada = {
            "projeto_id": "test_comparison_card_proj",
            "roteiro_transcricao": {"text": "Veja o percevejo juvenil vs adulto em detalhe.", "duration": 4.0},
            "word_timestamps": [
                {"w": "percevejo", "s": 0.5, "e": 1.0},
                {"w": "juvenil", "s": 1.0, "e": 1.4},
                {"w": "vs", "s": 1.4, "e": 1.7},
                {"w": "adulto", "s": 1.7, "e": 2.2},
            ],
            "cenas": [
                {"id": 1, "texto": "Veja o percevejo juvenil vs adulto em detalhe.", "start": 0.0, "end": 4.0}
            ]
        }

        saida = director.orquestar_producao_completa(entrada)
        cena_out = saida["cenas"]["cena_1"]
        scene_plan = saida["scene_plan"]
        cena_plan = scene_plan["cenas"][0]

        # 1. Roteiro com "percevejo juvenil vs adulto" gera story_card_type = "comparison_card"
        self.assertEqual(cena_out["story_card"]["tipo"], "comparison_card")
        self.assertEqual(cena_plan["story_card_type"], "comparison_card")

        # 2. Cena deve aparecer como static_freeze no scene_plan
        self.assertEqual(cena_plan["animation_type"], "static_freeze")
        self.assertFalse(cena_plan["animar"])
        self.assertFalse(cena_plan["should_animate"])
        self.assertEqual(cena_out["animacao"]["animation_type"], "static_freeze")
        self.assertFalse(cena_out["animacao"]["should_animate"])

        # 3. Tipo da cena no JSON final deve ser "labeled_comparison_diagram"
        self.assertEqual(cena_out["tipo"], "labeled_comparison_diagram")
        self.assertEqual(cena_out["narrativa"]["scene_type"], "labeled_comparison_diagram")
        self.assertEqual(cena_plan["tipo"], "labeled_comparison_diagram")
        self.assertEqual(cena_plan["scene_type"], "labeled_comparison_diagram")

        # 4. Badge "borda_preta_pop" deve aparecer na timeline do CapCut no timestamp correto
        materials_text, tracks = _gerar_trilha_texto([cena_plan])
        # Deve ter trilha de legenda e trilha de badge
        self.assertTrue(len(tracks) >= 2)
        # Material do badge deve usar borda_preta_pop
        mat_badge = next((m for m in materials_text if "borda_preta_pop" in str(m).lower() or m.get("id") == tracks[1]["segments"][0]["material_id"]), None)
        self.assertIsNotNone(mat_badge)
        # Timestamp de início do badge no CapCut deve corresponder à ancoragem Whisper (~1.0s)
        badge_segment = tracks[1]["segments"][0]
        # 1.0s = 1_000_000 us (tolerância de clamp)
        self.assertGreaterEqual(badge_segment["target_timerange"]["start"], 500_000)
        self.assertLessEqual(badge_segment["target_timerange"]["start"], 1_500_000)


if __name__ == "__main__":
    unittest.main()
