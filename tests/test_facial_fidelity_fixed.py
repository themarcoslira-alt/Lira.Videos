# -*- coding: utf-8 -*-
"""
tests/test_facial_fidelity_fixed.py — Validação do novo motor de fidelidade facial e bypass de upload.
"""
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from services import facial_fidelity_engine as ffe
import services.playwright_flow as pf
import services.visual_judgment_service as vjs


class TestFacialFidelityFixed(unittest.TestCase):

    def test_ref_vs_ref_alta_fidelidade(self):
        ref = "characters/Marcos/reference.png"
        proj_ref = Path("projetos/WHY YOUR TOMATO PLANTS WONT PRODUCE - AND ITS NOT WHAT YOU THINK") / ref
        if not proj_ref.exists():
            self.skipTest("reference.png não disponível")
        r = ffe.calcular_fidelidade_facial(str(proj_ref), str(proj_ref))
        self.assertTrue(r["ok"])
        self.assertGreaterEqual(r["fidelidade"], 95)

    def test_ref_vs_cena1_aprovada(self):
        proj_dir = Path("projetos/WHY YOUR TOMATO PLANTS WONT PRODUCE - AND ITS NOT WHAT YOU THINK")
        ref = proj_dir / "characters/Marcos/reference.png"
        gen = proj_dir / "cenas/01_[00-00-05].png"
        if not ref.exists() or not gen.exists():
            self.skipTest("arquivos de teste não disponíveis")
        r = ffe.calcular_fidelidade_facial(str(ref), str(gen))
        self.assertTrue(r["ok"])
        self.assertGreaterEqual(r["fidelidade"], 75)
        self.assertEqual(r["metodo"], "opencv_haar")

    def test_ref_vs_cenario_flores_rejeitado(self):
        proj_dir = Path("projetos/WHY YOUR TOMATO PLANTS WONT PRODUCE - AND ITS NOT WHAT YOU THINK")
        ref = proj_dir / "characters/Marcos/reference.png"
        gen = proj_dir / "cenas/02_[00-06-00-12].png"
        if not ref.exists() or not gen.exists():
            self.skipTest("arquivos de teste não disponíveis")
        r = ffe.calcular_fidelidade_facial(str(ref), str(gen))
        self.assertLess(r["fidelidade"], 50)

    def test_bypass_upload_avatar_quando_personagem_nativo(self):
        worker = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
        worker._avatar_media_uploaded = False
        worker.page = None
        ok = worker._upload_avatar_projeto("WHY YOUR TOMATO PLANTS WONT PRODUCE - AND ITS NOT WHAT YOU THINK")
        self.assertTrue(ok)
        self.assertTrue(worker._avatar_media_uploaded)

    def test_ajustar_prompt_remove_no_people(self):
        prompt = "A rustic backyard garden with tomatoes, no people, soft morning sunlight."
        limpo = pf._ajustar_prompt_avatar_gestual(prompt)
        self.assertNotIn("no people", limpo.lower())
        self.assertIn("avatar", limpo.lower())


if __name__ == "__main__":
    unittest.main()
