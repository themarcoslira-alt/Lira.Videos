"""Testes do VisualProfile (Fase 0)."""
import tempfile
import unittest
from pathlib import Path

from services.visual_profile import (
    LOCK_KEYS,
    PRESETS,
    VisualProfile,
    carregar_visual_profile,
    salvar_visual_profile,
)


class TestVisualProfile(unittest.TestCase):
    def test_default_tem_5_locks(self):
        p = VisualProfile.default()
        self.assertTrue(p.is_valid())
        self.assertEqual(len(p.to_dict()), 6)  # name + 5 locks
        for k in LOCK_KEYS:
            self.assertIn(k, p.to_dict())
            self.assertIsInstance(getattr(p, k), str)

    def test_presets_sao_validos(self):
        for nome in PRESETS:
            p = VisualProfile.from_preset(nome)
            self.assertTrue(p.is_valid(), f"preset inválido: {nome}")
            self.assertIn("style_lock", p.to_dict())

    def test_preset_desconhecido_erro(self):
        with self.assertRaises(ValueError):
            VisualProfile.from_preset("nao_existe")

    def test_from_dict(self):
        data = {"name": "X", "style_lock": "s", "character_lock": "c", "world_lock": "w",
                "composition_lock": "co", "negative_lock": "n"}
        p = VisualProfile.from_dict(data)
        self.assertEqual(p.name, "X")
        self.assertEqual(p.negative_lock, "n")

    def test_resolved_locks_chaves_curtas(self):
        p = VisualProfile.default()
        locks = p.resolved_locks()
        self.assertEqual(set(locks), {"style", "character", "world", "composition", "negative"})
        self.assertEqual(locks["style"], p.style_lock)

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = VisualProfile.from_preset("cartoon")
            destino = salvar_visual_profile(Path(tmp), p)
            self.assertTrue(destino.exists())
            carregado = carregar_visual_profile(Path(tmp))
            self.assertIsNotNone(carregado)
            self.assertEqual(carregado.name, "CARTOON")
            self.assertEqual(carregado.negative_lock, p.negative_lock)

    def test_load_ausente_retorna_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(carregar_visual_profile(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
