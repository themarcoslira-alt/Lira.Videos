# -*- coding: utf-8 -*-
"""
tests/test_worker_id_conta_ativa.py — o singleton não força contas[0] no Chrome
===============================================================================
Bug: o worker SINGLETON da fila (porta 9222) tem `worker_id=0` e `profile_dir=None`.
`_abrir_chrome_cdp`, `_iniciar_sessao_thread` e `_rotacionar_conta` passavam
`worker_id=0` EXPLÍCITO ao `ensure_chrome_cdp` → ramo (c) → `contas[0]`
(conta_03), ignorando a conta ATIVA.

Correção: `_worker_id_para_chrome()` devolve `self.worker_id` APENAS quando o worker
é paralelo (`profile_dir` preenchido) e None no singleton — aí o índice é inferido da
porta e o ramo (d) escolhe a conta `ativa=True`.

Sem Chrome e sem rede: `ensure_chrome_cdp`/`subprocess.Popen` são mockados e as contas
vão para um diretório TEMPORÁRIO (`config/flow_accounts.json` real intocado).
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import services.playwright_flow as pf

RAIZ = Path(__file__).resolve().parents[1]
ARQUIVO_REAL_CONTAS = (RAIZ / "config" / "flow_accounts.json").resolve()


class _PararAposCdp(Exception):
    """Aborta `_rotacionar_conta` LOGO após a chamada do `ensure_chrome_cdp`."""


def _worker(profile_dir=None, worker_id=0):
    """Worker 'cru' (sem __init__) com o estado mínimo usado pelos 3 métodos."""
    w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
    w.port = 9222
    w.profile_dir = profile_dir
    w.worker_id = worker_id
    w.current_project_id = ""
    w.playwright = None
    w.browser = None
    w.context = None
    w.page = None
    return w


class TestWorkerIdParaChrome(unittest.TestCase):

    def test_01_singleton_devolve_none(self):
        w = _worker(profile_dir=None, worker_id=0)
        self.assertIsNone(w._worker_id_para_chrome())

    def test_02_paralelo_devolve_o_indice(self):
        w = _worker(profile_dir=r"C:\fake\lira_profiles\conta_05", worker_id=2)
        self.assertEqual(w._worker_id_para_chrome(), 2)


class TestSingletonUsaContaAtiva(unittest.TestCase):
    """(i)/(ii) `_abrir_chrome_cdp` e `_iniciar_sessao_thread`."""

    def test_01_singleton_passa_worker_id_none(self):
        w = _worker(profile_dir=None, worker_id=0)
        with mock.patch.object(pf, "ensure_chrome_cdp", return_value=(True, "ok")) as m:
            ok, _msg = w._abrir_chrome_cdp()
        self.assertTrue(ok)
        self.assertEqual(m.call_args[0][0], 9222)
        self.assertIsNone(m.call_args[1].get("worker_id"), "singleton não pode fixar contas[0]")
        self.assertIsNone(m.call_args[1].get("profile_dir"))

    def test_02_singleton_na_sessao_passa_worker_id_none(self):
        w = _worker(profile_dir=None, worker_id=0)
        with mock.patch.object(pf, "ensure_chrome_cdp",
                               return_value=(False, "sem CDP (teste)")) as m:
            ok, _msg = w._iniciar_sessao_thread()
        self.assertFalse(ok)
        self.assertIsNone(m.call_args[1].get("worker_id"))

    def test_03_paralelo_passa_o_indice_nos_dois(self):
        w = _worker(profile_dir=r"C:\fake\lira_profiles\conta_05", worker_id=2)
        with mock.patch.object(pf, "ensure_chrome_cdp", return_value=(True, "ok")) as m:
            w._abrir_chrome_cdp()
        self.assertEqual(m.call_args[1].get("worker_id"), 2)
        with mock.patch.object(pf, "ensure_chrome_cdp",
                               return_value=(False, "sem CDP (teste)")) as m2:
            w._iniciar_sessao_thread()
        self.assertEqual(m2.call_args[1].get("worker_id"), 2)


class _BaseContasTemp(unittest.TestCase):
    """Escreve um `flow_accounts.json` TEMPORÁRIO (nunca o real)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="t_wid_contas_")
        self.contas_path = Path(self._tmp.name) / "flow_accounts.json"
        self.assertNotEqual(
            str(self.contas_path).lower(), str(ARQUIVO_REAL_CONTAS).lower(),
            "o teste NUNCA pode escrever no config/flow_accounts.json real",
        )
        self.escrever_contas([
            # conta_03 é contas[0] (o perfil que o bug abria sempre)
            {"id": 3, "nome": "conta_03", "email": "c3@exemplo.invalid",
             "ativa": True, "profile_dir": "lira_profiles/conta_03"},
            # conta_05 é a PRÓXIMA da rotação e veio SEM profile_dir
            {"id": 5, "nome": "conta_05", "email": "c5@exemplo.invalid",
             "ativa": False, "profile_dir": ""},
        ])
        _p = mock.patch.object(pf, "FLOW_ACCOUNTS_PATH", self.contas_path)
        _p.start()
        self.addCleanup(_p.stop)
        self.addCleanup(self._tmp.cleanup)

    def escrever_contas(self, contas):
        self.contas_path.write_text(
            json.dumps({"contas": contas}, ensure_ascii=False), encoding="utf-8")


class TestRotacaoConta(_BaseContasTemp):
    """(iii)/(iv) `_rotacionar_conta` — `conta_id` da conta nova e `modo`."""

    def _rotacionar(self, w, **kw):
        """Executa a rotação e ABORTA no ensure_chrome_cdp; devolve o mock."""
        with mock.patch.object(pf, "ensure_chrome_cdp",
                               side_effect=_PararAposCdp()) as m:
            with self.assertRaises(_PararAposCdp):
                w._rotacionar_conta(**kw)
        return m

    def test_01_proxima_sem_profile_dir_manda_conta_id(self):
        """(iii) conta nova sem `profile_dir` → `conta_id=5` (não contas[0]=3)."""
        w = _worker(profile_dir=None, worker_id=0)
        m = self._rotacionar(w)
        _, kwargs = m.call_args
        self.assertEqual(kwargs.get("conta_id"), 5)
        self.assertIsNone(kwargs.get("worker_id"))
        self.assertIsNone(kwargs.get("profile_dir"))
        self.assertTrue(kwargs.get("force_restart"))
        self.assertEqual(m.call_args[0][0], 9222)

    def test_02_apenas_video_continua_enviando_modo_video(self):
        """(iv) `apenas_video=True` → modo="video"."""
        w = _worker(profile_dir=None, worker_id=0)
        m = self._rotacionar(w, apenas_video=True)
        kwargs = m.call_args[1]
        self.assertEqual(kwargs.get("modo"), "video")
        self.assertEqual(kwargs.get("conta_id"), 5)

    def test_03_imagem_envia_modo_image(self):
        w = _worker(profile_dir=None, worker_id=0)
        m = self._rotacionar(w)
        self.assertEqual(m.call_args[1].get("modo"), "image")

    def test_04_paralelo_com_profile_novo_nao_manda_conta_id(self):
        """Worker paralelo e conta nova COM profile_dir → perfil vence, sem conta_id."""
        self.escrever_contas([
            {"id": 3, "nome": "conta_03", "email": "c3@exemplo.invalid",
             "ativa": True, "profile_dir": "lira_profiles/conta_03"},
            {"id": 5, "nome": "conta_05", "email": "c5@exemplo.invalid",
             "ativa": False, "profile_dir": "lira_profiles/conta_05"},
        ])
        w = _worker(profile_dir="lira_profiles/conta_03", worker_id=2)
        m = self._rotacionar(w)
        kwargs = m.call_args[1]
        self.assertEqual(kwargs.get("worker_id"), 2)
        self.assertNotIn("conta_id", kwargs)
        self.assertEqual(kwargs.get("profile_dir"), "lira_profiles/conta_05")


class TestFimAFimSingletonAbreContaAtiva(_BaseContasTemp):
    """Prova FIM-A-FIM com o `ensure_chrome_cdp` REAL (Popen/porta/exe mockados).

    Com o bug (worker_id=0 EXPLÍCITO) o `--user-data-dir` seria o de
    `contas[0]` = conta_03; agora o singleton omite o worker_id e o ramo (d)
    escolhe a conta `ativa=True`.
    """

    def _abrir_e_ler_perfil(self, w):
        with mock.patch.object(pf.subprocess, "Popen") as m_popen, \
             mock.patch.object(pf, "_cdp_port_open", side_effect=[False, True]), \
             mock.patch.object(pf, "_find_chrome_exe",
                               return_value=r"C:\Fake\chrome.exe"), \
             mock.patch.object(pf.time, "sleep", return_value=None):
            ok, msg = w._abrir_chrome_cdp()
        self.assertTrue(ok, msg)
        args = m_popen.call_args[0][0]
        for a in args:
            if str(a).startswith("--user-data-dir="):
                return Path(str(a).split("=", 1)[1]).name
        return None

    def test_01_conta_ativa_vence_contas_zero(self):
        self.escrever_contas([
            {"id": 3, "nome": "conta_03", "email": "c3@exemplo.invalid",
             "ativa": False, "profile_dir": "lira_profiles/conta_03"},
            {"id": 5, "nome": "conta_05", "email": "c5@exemplo.invalid",
             "ativa": True, "profile_dir": "lira_profiles/conta_05"},
        ])
        w = _worker(profile_dir=None, worker_id=0)          # singleton da fila
        self.assertEqual(self._abrir_e_ler_perfil(w), "conta_05")

    def test_02_worker_paralelo_usa_o_proprio_profile(self):
        """Worker paralelo: o `profile_dir` que o orquestrador injeta vence."""
        w = _worker(profile_dir="lira_profiles/conta_05", worker_id=1)
        w.port = 9223
        self.assertEqual(self._abrir_e_ler_perfil(w), "conta_05")


if __name__ == "__main__":
    unittest.main(verbosity=2)
