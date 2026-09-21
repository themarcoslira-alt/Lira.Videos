"""
tests/test_cdp_conditional_tabs.py — Testes Unitários da Abertura Limpa CDP e open_flow_tab
===========================================================================================
Valida que:
  - Boot / Worker 0 (porta 9222 / worker_id=0): abre EXCLUSIVAMENTE a aba do Lira Studio (http://127.0.0.1:5000).
  - Boot / Workers 1+: abre o Chrome com porta CDP limpa, SEM abrir o Flow antecipadamente.
  - open_flow_tab(page): abre/navega para o Google Flow exclusivamente quando o worker de produção inicia.
  - Inferência automática de worker_id via porta CDP.
  - _preparar_flow.py executa no boot abrindo apenas Lira Studio.
  - WorkerInstancia propaga worker_id corretamente para o driver.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Adiciona raiz do projeto ao path
sys.path.insert(0, str(Path(__file__).parent.parent))

import services.playwright_flow as pf
from services.parallel_flow_orchestrator import WorkerInstancia
import _preparar_flow as prep_flow


class TestCDPAberturaLimpaBoot(unittest.TestCase):

    @patch("subprocess.Popen")
    @patch.object(pf, "_cdp_port_open", side_effect=[False, True])
    @patch.object(pf, "_find_chrome_exe", return_value="C:\\Fake\\chrome.exe")
    def test_boot_worker_0_abre_apenas_lira_studio(self, mock_exe, mock_open, mock_popen):
        """No boot, Worker 0 deve abrir APENAS o Lira Studio, SEM abrir o Flow."""
        ok, msg = pf.ensure_chrome_cdp(
            port=9222,
            force_restart=False,
            projeto_id="MeuProjeto",
            worker_id=0
        )
        self.assertTrue(ok)
        self.assertTrue(mock_popen.called)

        args = mock_popen.call_args[0][0]
        tem_projeto = any("127.0.0.1:5000" in a and "MeuProjeto" in a for a in args)
        tem_flow = any("labs.google" in a or "flow" in a for a in args)

        self.assertTrue(tem_projeto, "Worker 0 no boot DEVE abrir a URL do projeto Lira Studio")
        self.assertFalse(tem_flow, "Worker 0 no boot NÃO DEVE abrir o Google Flow")

    @patch("subprocess.Popen")
    @patch.object(pf, "_cdp_port_open", side_effect=[False, True])
    @patch.object(pf, "_find_chrome_exe", return_value="C:\\Fake\\chrome.exe")
    def test_worker_1_abre_porta_limpa_sem_flow(self, mock_exe, mock_open, mock_popen):
        """Worker 1 (ou qualquer worker >= 1) não deve abrir nem o Lira nem o Flow no ensure_chrome_cdp."""
        ok, msg = pf.ensure_chrome_cdp(
            port=9223,
            force_restart=False,
            projeto_id="MeuProjeto",
            worker_id=1
        )
        self.assertTrue(ok)
        self.assertTrue(mock_popen.called)

        args = mock_popen.call_args[0][0]
        tem_projeto = any("127.0.0.1:5000" in a or "localhost:5000" in a for a in args)
        tem_flow = any("labs.google" in a or "flow" in a for a in args)

        self.assertFalse(tem_projeto, "Worker 1+ NÃO DEVE abrir a URL do Lira Studio")
        self.assertFalse(tem_flow, "Worker 1+ NÃO DEVE abrir a URL do Flow no boot/ensure")

    @patch("subprocess.Popen")
    @patch.object(pf, "_cdp_port_open", side_effect=[False, True])
    @patch.object(pf, "_find_chrome_exe", return_value="C:\\Fake\\chrome.exe")
    def test_inferencia_automatica_worker_id(self, mock_exe, mock_open, mock_popen):
        """Sem worker_id explícito, porta 9222 infere worker 0 (abre Lira), porta 9225 infere worker 3."""
        # Porta 9222 -> Worker 0 (abre projeto Lira Studio)
        pf.ensure_chrome_cdp(port=9222, projeto_id="ProjZero")
        args_w0 = mock_popen.call_args[0][0]
        self.assertTrue(any("ProjZero" in a for a in args_w0))
        self.assertFalse(any("labs.google" in a for a in args_w0))

        mock_popen.reset_mock()
        mock_open.side_effect = [False, True]

        # Porta 9225 -> Worker 3 (porta limpa, sem projeto nem flow)
        pf.ensure_chrome_cdp(port=9225, projeto_id="ProjZero")
        args_w3 = mock_popen.call_args[0][0]
        self.assertFalse(any("ProjZero" in a for a in args_w3))
        self.assertFalse(any("labs.google" in a for a in args_w3))


class TestOpenFlowTab(unittest.TestCase):

    def test_open_flow_tab_navega_nova_pagina(self):
        """open_flow_tab navega para o Flow quando chamada em uma nova página."""
        page = MagicMock()
        page.url = "about:blank"

        res = pf.open_flow_tab(page, target_url="https://labs.google/fx/pt/tools/flow/project/123")
        self.assertEqual(res, page)
        page.goto.assert_called_once_with("https://labs.google/fx/pt/tools/flow/project/123", timeout=60000)

    def test_open_flow_tab_abre_nova_se_pagina_for_lira_studio(self):
        """Se a página for do Lira Studio, open_flow_tab cria uma NOVA página para o Flow para não fechar a UI."""
        page_lira = MagicMock()
        page_lira.url = "http://127.0.0.1:5000/?cdp=1"
        nova_page = MagicMock()
        nova_page.url = "about:blank"
        ctx = MagicMock()
        ctx.new_page.return_value = nova_page
        page_lira.context = ctx

        res = pf.open_flow_tab(page_lira)
        self.assertEqual(res, nova_page)
        ctx.new_page.assert_called_once()
        nova_page.goto.assert_called_once_with(pf.FLOW_URL, timeout=60000)
        page_lira.goto.assert_not_called()


class TestPrepararFlowCLI(unittest.TestCase):

    @patch("services.playwright_flow.ensure_chrome_cdp", return_value=(True, "OK CDP"))
    def test_preparar_flow_chamada_funcional(self, mock_ensure):
        """Função preparar_flow() encaminha worker_id e port calculada."""
        ok, msg = prep_flow.preparar_flow(worker_id=0)
        self.assertTrue(ok)
        mock_ensure.assert_called_once_with(
            port=9222,
            force_restart=False,
            profile_dir=None,
            projeto_id=None,
            worker_id=0
        )

    @patch("services.playwright_flow.ensure_chrome_cdp", return_value=(True, "OK CDP"))
    def test_preparar_flow_cli_default_worker_0(self, mock_ensure):
        """CLI sem argumentos deve rodar para worker_id=0 na porta 9222 (boot do Lira Studio)."""
        ret = prep_flow.main([])
        self.assertEqual(ret, 0)
        mock_ensure.assert_called_once_with(
            port=9222,
            force_restart=False,
            profile_dir=None,
            projeto_id=None,
            worker_id=0
        )


class TestWorkerInstanciaDriverPropagation(unittest.TestCase):

    def test_worker_instancia_passa_worker_id_e_projeto_ao_driver(self):
        w0 = WorkerInstancia(
            worker_id=0,
            conta_info={"id": 1},
            cdp_port=9222,
            bloco_cenas=[],
            projeto_id="ProjTest"
        )
        self.assertEqual(w0.driver.worker_id, 0)
        self.assertEqual(w0.driver.current_project_id, "ProjTest")

        w3 = WorkerInstancia(
            worker_id=3,
            conta_info={"id": 4},
            cdp_port=9225,
            bloco_cenas=[],
            projeto_id="ProjTest"
        )
        self.assertEqual(w3.driver.worker_id, 3)
        self.assertEqual(w3.driver.current_project_id, "ProjTest")


if __name__ == "__main__":
    unittest.main()
