import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

"""Testes do Avatar/B-roll Decision Service (Fase 1)."""
import unittest

from services.avatar_decision_service import (
    decide_avatar_or_broll,
    estimate_cost,
    get_personagem_reference,
    validate_personagem_exists,
)

PROJETO_HISTORIA = "Historia"


class TestAvatarDecision(unittest.TestCase):
    def test_hook_avatar(self):
        tipo, valor = decide_avatar_or_broll("HOOK")
        self.assertEqual(tipo, "avatar")
        self.assertIsInstance(valor, str)
        self.assertTrue(valor.strip())

    def test_avatar_role(self):
        tipo, _ = decide_avatar_or_broll("AVATAR")
        self.assertEqual(tipo, "avatar")

    def test_broll_broll(self):
        tipo, valor = decide_avatar_or_broll(
            "BROLL",
            {"texto": "A grama verde em close", "scene_type": "broll_macro"},
        )
        self.assertEqual(tipo, "broll")
        self.assertIsInstance(valor, str)
        self.assertTrue(valor.strip())

    def test_cta_avatar(self):
        tipo, _ = decide_avatar_or_broll("CTA")
        self.assertEqual(tipo, "avatar")

    def test_closing_avatar(self):
        tipo, _ = decide_avatar_or_broll("CLOSING")
        self.assertEqual(tipo, "avatar")

    def test_validar_personagem_inexistente(self):
        self.assertFalse(
            validate_personagem_exists("FulanoInexistenteXYZ", projeto_id=PROJETO_HISTORIA)
        )

    def test_validar_personagem_marcos(self):
        # @Marcos existe no projeto Historia (identidade.json + characters/Marcos/)
        self.assertTrue(validate_personagem_exists("Marcos", projeto_id=PROJETO_HISTORIA))
        self.assertTrue(validate_personagem_exists("@Marcos", projeto_id=PROJETO_HISTORIA))

    def test_get_personagem_reference(self):
        ref = get_personagem_reference("Marcos", projeto_id=PROJETO_HISTORIA)
        self.assertIsInstance(ref, str)
        if ref:
            self.assertTrue(Path(ref).is_file(), f"referência não existe: {ref}")

    def test_estimate_cost_avatar_mais_caro_que_broll(self):
        c_avatar = estimate_cost("AVATAR", "Marcos", projeto_id=PROJETO_HISTORIA)
        c_broll = estimate_cost("BROLL")
        self.assertEqual(c_avatar["type"], "avatar")
        self.assertEqual(c_broll["type"], "broll")
        self.assertGreater(c_avatar["cost_estimate"], c_broll["cost_estimate"])

    def test_estimate_cost_avatar_sem_personagem(self):
        c = estimate_cost("AVATAR", "InexistenteXYZ", projeto_id=PROJETO_HISTORIA)
        self.assertTrue(c["requires_character_creation"])
        self.assertGreater(c["cost_estimate"], 0.0)


if __name__ == "__main__":
    unittest.main()