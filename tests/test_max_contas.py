# -*- coding: utf-8 -*-
"""
tests/test_max_contas.py — TAREFA 1: limite de 3 contas Flow simultâneas
=======================================================================
Sem Chrome e sem rede: valida a constante, o padrão do orquestrador e que TODOS os
pontos que aceitavam `num_contas` foram trocados pelo clamp (registro de aviso).
"""
import pathlib
import unittest

import config
import services.parallel_flow_orchestrator as orch

RAIZ = pathlib.Path(__file__).resolve().parents[1]


class TestLimiteContasSimultaneas(unittest.TestCase):

    def test_01_constante_e_tres(self):
        self.assertEqual(config.MAX_CONTAS_SIMULTANEAS, 3)

    def test_02_padrao_do_orquestrador_e_o_limite(self):
        o = orch.OrchestradorParalelo("ProjX")
        self.assertEqual(o.num_contas, config.MAX_CONTAS_SIMULTANEAS)

    def test_03_api_v2_usa_o_limite_nos_dois_pontos(self):
        src = (RAIZ / "services" / "api_v2.py").read_text(encoding="utf-8")
        # default do campo + clamp explícito nos DOIS pontos (pré-login e paralelo)
        self.assertIn('data.get("num_contas", MAX_CONTAS_SIMULTANEAS)', src)
        self.assertEqual(src.count("if num_contas > MAX_CONTAS_SIMULTANEAS:"), 2)
        self.assertEqual(src.count("num_contas = MAX_CONTAS_SIMULTANEAS"), 2)
        self.assertIn("MAX_CONTAS_SIMULTANEAS", src.splitlines()[24])   # import do config

    def test_04_orquestrador_usa_o_limite(self):
        src = (RAIZ / "services" / "parallel_flow_orchestrator.py").read_text(encoding="utf-8")
        self.assertIn("min(num_contas, MAX_CONTAS_SIMULTANEAS)", src)

    def test_05_nao_sobrou_limite_antigo_de_12_nem_default_5(self):
        for rel in ("services/api_v2.py", "services/parallel_flow_orchestrator.py"):
            src = (RAIZ / rel).read_text(encoding="utf-8")
            with self.subTest(arquivo=rel):
                self.assertNotIn("min(num_contas, 12)", src)
                self.assertNotIn('num_contas", 5)', src)
                self.assertNotIn("num_contas: int = 5", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
