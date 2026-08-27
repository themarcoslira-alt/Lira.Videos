"""
test_media_naming_and_resolver.py
==================================
Testes unitários para Nomenclatura Canônica e Resolvedor Resiliente de Mídia.
"""

import shutil
import unittest
from pathlib import Path

from services.scene_plan_service import (
    formatar_nome_midia_canonico,
    resolver_arquivo_cena,
    PROJETOS_DIR,
    salvar_scene_plan,
)


class TestMediaNamingAndResolver(unittest.TestCase):

    def setUp(self):
        self.temp_project = "test_resilient_proj"
        self.pdir = PROJETOS_DIR / self.temp_project
        if self.pdir.exists():
            shutil.rmtree(self.pdir, ignore_errors=True)
        self.pdir.mkdir(parents=True, exist_ok=True)
        (self.pdir / "cenas").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self.pdir.exists():
            shutil.rmtree(self.pdir, ignore_errors=True)

    def test_formatar_nome_midia_canonico_exemplos_obrigatorios(self):
        """
        Valida os exemplos da especificação:
          - 1_[00-00]_Photorealistic_ci.png (cena 1, 0.0s)
          - 14_[01-07]_Blender_3D.png (cena 14, 67.0s)
          - 120_[07-31]_Anime.png (cena 120, 451.0s)
        """
        n1 = formatar_nome_midia_canonico(1, 0.0, "Photorealistic_ci", ".png")
        self.assertEqual(n1, "1_[00-00]_Photorealistic_ci.png")

        n14 = formatar_nome_midia_canonico(14, 67.0, "Blender_3D", ".png")
        self.assertEqual(n14, "14_[01-07]_Blender_3D.png")

        n120 = formatar_nome_midia_canonico(120, 451.0, "Anime", ".png")
        self.assertEqual(n120, "120_[07-31]_Anime.png")

        n_vid = formatar_nome_midia_canonico(5, 15.0, "Dark_cinematic", ".mp4")
        self.assertEqual(n_vid, "5_[00-15]_Dark_cinematic.mp4")

    def test_resolver_prioridade_1_arquivo_midia(self):
        """Valida resolução via campo arquivo_midia existente."""
        midia_file = self.pdir / "cenas" / "custom_media.png"
        midia_file.write_bytes(b"PNG_FAKE_DATA_VALID" * 50)

        salvar_scene_plan(self.temp_project, {
            "cenas": [
                {"id": 1, "tempo_inicio": 0.0, "arquivo_midia": str(midia_file)}
            ]
        })

        res = resolver_arquivo_cena(self.temp_project, cid=1, tempo_inicio=0.0)
        self.assertIsNotNone(res)
        self.assertEqual(res.resolve(), midia_file.resolve())

    def test_resolver_prioridade_2_canonico_novo(self):
        """Valida resolução via nome canônico novo: 1_[00-00]_Photorealistic_ci.png."""
        midia_canon = self.pdir / "cenas" / "1_[00-00]_Photorealistic_ci.png"
        midia_canon.write_bytes(b"PNG_FAKE_DATA_CANONICAL" * 50)

        res = resolver_arquivo_cena(self.temp_project, cid=1, tempo_inicio=0.0, estilo_slug="Photorealistic_ci")
        self.assertIsNotNone(res)
        self.assertEqual(res.name, "1_[00-00]_Photorealistic_ci.png")

    def test_resolver_prioridade_3_glob_id(self):
        """Valida resolução via glob por ID: 1_anything.png."""
        midia_glob = self.pdir / "cenas" / "1_custom_generated_render.png"
        midia_glob.write_bytes(b"PNG_FAKE_DATA_GLOB" * 50)

        res = resolver_arquivo_cena(self.temp_project, cid=1, tempo_inicio=0.0)
        self.assertIsNotNone(res)
        self.assertEqual(res.name, "1_custom_generated_render.png")

    def test_resolver_prioridade_4_legado_simples(self):
        """Valida resolução via legado 002.png."""
        midia_leg = self.pdir / "cenas" / "002.png"
        midia_leg.write_bytes(b"PNG_FAKE_DATA_LEGACY" * 50)

        res = resolver_arquivo_cena(self.temp_project, cid=2, tempo_inicio=10.0)
        self.assertIsNotNone(res)
        self.assertEqual(res.name, "002.png")

    def test_resolver_prioridade_5_subpasta_auditoria(self):
        """Valida resolução em subpastas de auditoria cenas/cena_003_00-15-20/imagem.png."""
        sub = self.pdir / "cenas" / "cena_003_00-15-20"
        sub.mkdir(parents=True, exist_ok=True)
        midia_sub = sub / "imagem.png"
        midia_sub.write_bytes(b"PNG_FAKE_DATA_SUBFOLDER" * 50)

        res = resolver_arquivo_cena(self.temp_project, cid=3, tempo_inicio=15.0)
        self.assertIsNotNone(res)
        self.assertEqual(res.resolve(), midia_sub.resolve())

    def test_resolver_retorna_none_quando_inexistente(self):
        """Valida que retorna None de forma segura quando a mídia não foi gerada."""
        res = resolver_arquivo_cena(self.temp_project, cid=999, tempo_inicio=0.0)
        self.assertIsNone(res)


if __name__ == "__main__":
    unittest.main()
