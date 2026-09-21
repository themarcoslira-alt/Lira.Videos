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
        self.assertIn("#E6F1FB", cenas[0]["story_card_prompt_imagem"])
        self.assertIn("#185FA5", cenas[0]["story_card_prompt_imagem"])

        # Cena 2: Sem card
        self.assertFalse(cenas[1]["is_story_card"])
        self.assertEqual(cenas[1]["story_card_type"], "")
        self.assertEqual(cenas[1]["story_card_prompt_imagem"], "")

        # Cena 3: Myth 1
        self.assertTrue(cenas[2]["is_story_card"])
        self.assertEqual(cenas[2]["story_card_type"], "myth_1")
        self.assertEqual(cenas[2]["story_card_style"], "vox_editorial")
        self.assertIn("#C5221F", cenas[2]["story_card_prompt_imagem"])


if __name__ == "__main__":
    unittest.main()
