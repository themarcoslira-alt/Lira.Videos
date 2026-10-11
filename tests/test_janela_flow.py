# -*- coding: utf-8 -*-
"""
tests/test_janela_flow.py — Chrome do Flow fora da tela (config + args + marcador)
=================================================================================
Sem abrir Chrome e sem rede. Valida:
  * `_montar_args_chrome` com posição vazia devolve a lista IDÊNTICA à do modo atual;
  * modo oculto troca `--start-maximized` por `--window-position/--window-size` e NÃO
    abre a `url_lira` (mantendo `url_extra`);
  * `visivel=True` mantém o modo atual mesmo com posição configurada;
  * `_posicao_janela_flow` recusa valores inválidos ("abc", "1,2,3", "99999,0") e
    aceita "" (que é válido);
  * marcador de modo com PID INEXISTENTE é ignorado.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import services.playwright_flow as pf

EXE = r"C:\Chrome\chrome.exe"
PORT = 9222
PROFILE = r"C:\perfis\conta_03"
URL_LIRA = "http://127.0.0.1:5000/?cdp=1&projeto_id=P1"
URL_EXTRA = pf.FLOW_LOGIN_URL

# Lista do comportamento ATUAL após a TAREFA 1: o Studio (127.0.0.1:5000) saiu do
# Chrome do Flow, então a lista é a MESMA para worker 0, 1 e 2 (sem a URL do Studio).
LISTA_ATUAL = [
    EXE,
    f"--remote-debugging-port={PORT}",
    "--remote-allow-origins=*",
    f"--user-data-dir={PROFILE}",
    "--no-first-run",
    "--no-default-browser-check",
    "--start-maximized",
]
LISTA_ATUAL_EXTRA = LISTA_ATUAL + [URL_EXTRA]


class TestMontarArgsChrome(unittest.TestCase):

    def test_01_worker_0_1_2_sem_url_do_studio(self):
        """TAREFA 1 — nenhum worker recebe a URL do Studio (validação (a))."""
        for worker_id in (0, 1, 2):
            for visivel in (False, True):
                args = pf._montar_args_chrome(EXE, PORT, PROFILE, worker_id, URL_LIRA, None,
                                              "", visivel)
                with self.subTest(worker_id=worker_id, visivel=visivel):
                    self.assertEqual(args, LISTA_ATUAL)
                    self.assertFalse(any("127.0.0.1:5000" in a for a in args))
                    self.assertFalse(any("localhost:5000" in a for a in args))
                    self.assertIn("--start-maximized", args)

    def test_02_url_extra_mantida_sem_url_do_studio(self):
        args = pf._montar_args_chrome(EXE, PORT, PROFILE, 0, URL_LIRA, URL_EXTRA, "", False)
        self.assertEqual(args, LISTA_ATUAL_EXTRA)
        self.assertFalse(any("127.0.0.1:5000" in a for a in args))
        self.assertIn(URL_EXTRA, args)

    def test_03_modo_oculto_troca_maximized_e_remove_url_lira(self):
        args = pf._montar_args_chrome(EXE, PORT, PROFILE, 0, URL_LIRA, URL_EXTRA,
                                      "-30000,-30000", False)
        self.assertIn("--window-position=-30000,-30000", args)
        self.assertIn("--window-size=1920,1080", args)
        self.assertNotIn("--start-maximized", args)
        self.assertNotIn(URL_LIRA, args, "no modo oculto a aba do Studio NÃO entra")
        self.assertIn(URL_EXTRA, args, "url_extra continua entrando")

    def test_04_visivel_true_ignora_a_posicao(self):
        args = pf._montar_args_chrome(EXE, PORT, PROFILE, 0, URL_LIRA, None,
                                      "-30000,-30000", True)
        self.assertEqual(args, LISTA_ATUAL)


class TestPosicaoJanelaFlow(unittest.TestCase):
    """`browser_window_position` lido do config/flow_config.json (temporário)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="t_janela_flow_")
        self.cfg = Path(self._tmp.name) / "flow_config.json"
        self.cfg.write_text(json.dumps({"browser_window_position": ""}), encoding="utf-8")
        _p = mock.patch.object(pf, "FLOW_CONFIG_PATH", self.cfg)
        _p.start()
        self.addCleanup(_p.stop)
        self.addCleanup(self._tmp.cleanup)

    def _valor(self, valor):
        self.cfg.write_text(json.dumps({"browser_window_position": valor}), encoding="utf-8")
        return pf._posicao_janela_flow()

    def test_01_vazio_e_valido_e_devolve_vazio(self):
        self.assertEqual(self._valor(""), "")

    def test_02_posicoes_validas(self):
        self.assertEqual(self._valor("-30000,-30000"), "-30000,-30000")
        self.assertEqual(self._valor("10,20"), "10,20")

    def test_03_posicoes_invalidas_caem_em_vazio(self):
        for ruim in ("abc", "1,2,3", "99999,0", "-40000,0", "10,", "10,x", "10;20"):
            with self.subTest(valor=ruim):
                self.assertEqual(self._valor(ruim), "")

    def test_04_arquivo_ausente_devolve_vazio(self):
        self.cfg.unlink()
        self.assertEqual(pf._posicao_janela_flow(), "")

    def test_05_chave_ausente_devolve_vazio(self):
        self.cfg.write_text(json.dumps({"outra": 1}), encoding="utf-8")
        self.assertEqual(pf._posicao_janela_flow(), "")


class TestMarcadorModoChrome(unittest.TestCase):
    """Marcador porta → {pid, oculto} (tem de funcionar entre processos)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="t_marcador_")
        self.marc = Path(self._tmp.name) / "chrome_modo.json"
        _p = mock.patch.object(pf, "CHROME_MODO_PATH", self.marc)
        _p.start()
        self.addCleanup(_p.stop)
        self.addCleanup(self._tmp.cleanup)
        pf._CACHE_MODO_CHROME.update({"porta": None, "valor": False, "ts": 0.0})

    def _escrever(self, pid, oculto):
        self.marc.write_text(json.dumps({str(PORT): {"pid": pid, "oculto": oculto}}),
                             encoding="utf-8")

    def test_01_pid_inexistente_e_ignorado(self):
        self._escrever(999999999, True)
        self.assertFalse(pf._chrome_da_porta_esta_oculto(PORT))

    def test_02_pid_vivo_e_lido(self):
        self._escrever(os.getpid(), True)
        self.assertTrue(pf._chrome_da_porta_esta_oculto(PORT))

    def test_03_sem_marcador_devolve_false(self):
        self.assertFalse(pf._chrome_da_porta_esta_oculto(PORT))

    def test_04_registrar_e_ler(self):
        pf._registrar_modo_chrome(PORT, os.getpid(), True)
        pf._CACHE_MODO_CHROME.update({"porta": None, "valor": False, "ts": 0.0})
        self.assertTrue(pf._chrome_da_porta_esta_oculto(PORT))
        pf._registrar_modo_chrome(PORT, os.getpid(), False)
        pf._CACHE_MODO_CHROME.update({"porta": None, "valor": False, "ts": 0.0})
        self.assertFalse(pf._chrome_da_porta_esta_oculto(PORT))

    def test_05_pid_zero_ou_invalido_e_ignorado(self):
        for pid in (0, -1, "x", None):
            with self.subTest(pid=pid):
                self._escrever(pid, True)
                self.assertFalse(pf._chrome_da_porta_esta_oculto(PORT))


if __name__ == "__main__":
    unittest.main(verbosity=2)
