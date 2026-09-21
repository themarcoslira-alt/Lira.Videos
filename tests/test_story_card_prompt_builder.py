# -*- coding: utf-8 -*-
"""
Testes unitários para o gerador de prompts de Story Cards
(services/story_card_prompt_builder.py).
"""

import unittest
from services.story_card_prompt_builder import (
    build_story_card_prompt,
    STEP_PALETTES,
)


class TestStoryCardPromptBuilder(unittest.TestCase):
    def test_step_palette_hex_codes(self):
        # Validação estrita das cores contratuais dos 5 passos
        self.assertEqual(STEP_PALETTES["step_1"]["bg_hex"], "#E6F1FB")
        self.assertEqual(STEP_PALETTES["step_1"]["title_color_hex"], "#185FA5")

        self.assertEqual(STEP_PALETTES["step_2"]["bg_hex"], "#EAF3DE")
        self.assertEqual(STEP_PALETTES["step_2"]["title_color_hex"], "#2B6E3F")

        self.assertEqual(STEP_PALETTES["step_3"]["bg_hex"], "#E1F5EE")
        self.assertEqual(STEP_PALETTES["step_3"]["title_color_hex"], "#1D6F60")

        self.assertEqual(STEP_PALETTES["step_4"]["bg_hex"], "#EEEDFE")
        self.assertEqual(STEP_PALETTES["step_4"]["title_color_hex"], "#533AB7")

        self.assertEqual(STEP_PALETTES["step_5"]["bg_hex"], "#FAECE7")
        self.assertEqual(STEP_PALETTES["step_5"]["title_color_hex"], "#9E3A20")

    def test_build_prompt_step_1_to_5(self):
        for i in range(1, 6):
            marker = {
                "story_card_type": f"step_{i}",
                "story_card_style": "step_intro",
                "story_card_config": {
                    "title": f"STEP {i}",
                    "subtitle": f"Execution details for step {i}",
                },
            }
            prompt = build_story_card_prompt(marker)
            palette = STEP_PALETTES[f"step_1" if i == 1 else f"step_{i}"]

            self.assertIn("16:9 widescreen composition", prompt)
            self.assertIn(palette["bg_hex"], prompt)
            self.assertIn(palette["title_color_hex"], prompt)
            self.assertIn(f"STEP {i}", prompt)
            self.assertIn(f"Execution details for step {i}", prompt)
            self.assertIn(palette["icon"], prompt)
            self.assertIn("no human faces", prompt)

    def test_build_prompt_vox_editorial_myth(self):
        marker = {
            "story_card_type": "myth_1",
            "story_card_style": "vox_editorial",
            "story_card_config": {
                "title": "MYTH 1",
                "subtitle": "Gypsum immediately alters acid soil pH",
            },
        }
        prompt = build_story_card_prompt(marker)
        self.assertIn("Vox editorial style", prompt)
        self.assertIn("#F8F9FA", prompt)
        self.assertIn("#C5221F", prompt)
        self.assertIn("MYTH 1", prompt)
        self.assertIn("Gypsum immediately alters acid soil pH", prompt)

    def test_build_prompt_data_viz(self):
        marker = {
            "story_card_type": "data_viz",
            "story_card_style": "data_viz",
            "story_card_config": {
                "title": "$500 SAVED",
                "subtitle": "Average lawn care budget reduction",
            },
        }
        prompt = build_story_card_prompt(marker)
        self.assertIn("Data visualization motion graphics", prompt)
        self.assertIn("#F1F3F4", prompt)
        self.assertIn("#137333", prompt)
        self.assertIn("$500 SAVED", prompt)
        self.assertIn("Average lawn care budget reduction", prompt)

    def test_build_prompt_timeline(self):
        marker = {
            "story_card_type": "timeline",
            "story_card_style": "timeline",
            "story_card_config": {
                "title": "WEEK 3 TO 4",
                "subtitle": "Peak flocculation period",
            },
        }
        prompt = build_story_card_prompt(marker)
        self.assertIn("Timeline progress graphic card", prompt)
        self.assertIn("#F6F8FA", prompt)
        self.assertIn("#1F2328", prompt)
        self.assertIn("WEEK 3 TO 4", prompt)

    def test_build_prompt_alert_warning(self):
        marker = {
            "story_card_type": "alert_1",
            "story_card_style": "alert_warning",
            "story_card_config": {
                "title": "CRITICAL MISTAKE 1",
                "subtitle": "Do not use with standing surface water",
            },
        }
        prompt = build_story_card_prompt(marker)
        self.assertIn("Urgent alert warning card", prompt)
        self.assertIn("#FEF7E0", prompt)
        self.assertIn("#B06000", prompt)
        self.assertIn("CRITICAL MISTAKE 1", prompt)


if __name__ == "__main__":
    unittest.main()
