"""
test_visual_presets.py
======================
Testes unitários dos Presets Visuais Canônicos do Lira Studio.
"""

import unittest
from services.visual_presets_service import (
    listar_presets_estilos,
    obter_preset_por_id,
    obter_slug_estilo,
    sanitizar_slug_estilo,
    PRESETS_DIRECAO_VISUAL,
    ESTILO_PADRAO_ID,
)


class TestVisualPresets(unittest.TestCase):

    def test_presets_quantidade_e_chaves_obrigatorias(self):
        """Valida se todos os 11 presets solicitados existem e contêm os campos canônicos."""
        presets = listar_presets_estilos()
        self.assertEqual(len(presets), 11)

        estilos_esperados = [
            "photorealistic_cinematic",
            "documentary_natural",
            "commercial_advertising",
            "dark_cinematic",
            "vintage_film",
            "clean_editorial",
            "hyperrealistic",
            "blender_3d",
            "illustration",
            "anime",
            "custom",
        ]

        for eid in estilos_esperados:
            self.assertIn(eid, PRESETS_DIRECAO_VISUAL)
            p = PRESETS_DIRECAO_VISUAL[eid]
            self.assertEqual(p["id"], eid)
            self.assertTrue(len(p["nome"]) > 0)
            self.assertTrue(len(p["slug"]) > 0)
            self.assertTrue(len(p["style_lock"]) > 0)
            self.assertTrue(len(p["instructions"]) > 0)
            self.assertTrue(len(p["negative_defaults"]) > 0)

    def test_slugs_canonicos_exatos(self):
        """Valida os slugs exatos esperados para a nomenclatura oficial de arquivos."""
        self.assertEqual(obter_slug_estilo("photorealistic_cinematic"), "Photorealistic_ci")
        self.assertEqual(obter_slug_estilo("documentary_natural"), "Documentary_nat")
        self.assertEqual(obter_slug_estilo("commercial_advertising"), "Commercial_adv")
        self.assertEqual(obter_slug_estilo("dark_cinematic"), "Dark_cinematic")
        self.assertEqual(obter_slug_estilo("vintage_film"), "Vintage_film")
        self.assertEqual(obter_slug_estilo("clean_editorial"), "Clean_editorial")
        self.assertEqual(obter_slug_estilo("hyperrealistic"), "Hyperrealistic")
        self.assertEqual(obter_slug_estilo("blender_3d"), "Blender_3D")
        self.assertEqual(obter_slug_estilo("illustration"), "Illustration")
        self.assertEqual(obter_slug_estilo("anime"), "Anime")
        self.assertEqual(obter_slug_estilo("custom"), "Custom")

    def test_fallback_estilo_invalido(self):
        """Valida que passar None ou ID desconhecido retorna photorealistic_cinematic de forma segura."""
        p_none = obter_preset_por_id(None)
        self.assertEqual(p_none["id"], ESTILO_PADRAO_ID)

        p_invalido = obter_preset_por_id("estilo_fantasma_inexistente")
        self.assertEqual(p_invalido["id"], ESTILO_PADRAO_ID)

    def test_sanitizacao_slug(self):
        """Valida sanitização para sistema de arquivos Windows."""
        self.assertEqual(sanitizar_slug_estilo("Meu Estilo / Especial!"), "Meu_Estilo_Especial_")
        self.assertEqual(sanitizar_slug_estilo(""), "Visual")


if __name__ == "__main__":
    unittest.main()
