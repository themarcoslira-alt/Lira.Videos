# -*- coding: utf-8 -*-
"""
tests/test_chrome_profile_selection.py — O perfil do Chrome segue a CONTA pedida
==============================================================================
Bug: qualquer abertura do Chrome na porta 9222 abria SEMPRE
`lira_profiles/conta_03`, ignorando a conta escolhida no login guiado.

Causa raiz (services/playwright_flow.py, `ensure_chrome_cdp`):
  - `worker_id` era inferido da porta (9222 → 0) e o ramo de ÍNDICE
    (`contas[worker_id]`) vinha ANTES do ramo da conta `ativa=True` — que ficava
    inalcançável nas portas 9222-9240.

Prioridade validada aqui:
  (a) `conta_id` explícito → perfil dessa conta (id inexistente = ERRO, nunca
      conta_03/ativa);
  (b) modo animacao/video → `proxima_conta_para_video()`;
  (c) `worker_id` EXPLÍCITO → `contas[worker_id]` (produção paralela preservada);
  (d) `worker_id` apenas inferido da porta → conta `ativa=True`; sem ativa, `contas[0]`.

Sem Chrome e sem rede: `subprocess.Popen`, `_encerrar_chrome_cdp`, `_find_chrome_exe`
e `_cdp_port_open` são mockados; as contas são escritas num diretório TEMPORÁRIO
(mesma técnica do `tests/conftest.py`) — `config/flow_accounts.json` real nunca é tocado.
"""
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import services.playwright_flow as pf

RAIZ = Path(__file__).resolve().parents[1]
ARQUIVO_REAL_CONTAS = (RAIZ / "config" / "flow_accounts.json").resolve()


def contas_exemplo():
    """contas[0] = conta_03 (o perfil que o bug abria sempre)."""
    return {
        "contas": [
            {"id": 3, "nome": "conta_03", "profile_dir": "lira_profiles/conta_03", "ativa": True},
            {"id": 5, "nome": "conta_05", "profile_dir": "lira_profiles/conta_05", "ativa": False},
            {"id": 8, "nome": "conta_08", "profile_dir": "lira_profiles/conta_08", "ativa": False},
        ]
    }


class _BaseSemChrome(unittest.TestCase):
    """Redireciona as contas para um diretório temporário e trava o Chrome."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="t_perfil_chrome_")
        self.contas_path = Path(self._tmp.name) / "flow_accounts.json"
        self.assertNotEqual(
            str(self.contas_path).lower(), str(ARQUIVO_REAL_CONTAS).lower(),
            "o teste NUNCA pode escrever no config/flow_accounts.json real",
        )
        _p = mock.patch.object(pf, "FLOW_ACCOUNTS_PATH", self.contas_path)
        _p.start()
        self.addCleanup(_p.stop)
        self.addCleanup(self._tmp.cleanup)

    def _escrever_contas(self, dados):
        self.contas_path.write_text(json.dumps(dados, ensure_ascii=False, indent=2),
                                    encoding="utf-8")

    def _rodar(self, dados, **kwargs):
        """Chama `ensure_chrome_cdp` com o Chrome travado. Devolve (ok, msg, popen, kill)."""
        self._escrever_contas(dados)
        with mock.patch.object(pf.subprocess, "Popen") as m_popen, \
             mock.patch.object(pf, "_cdp_port_open", side_effect=[False, True]), \
             mock.patch.object(pf, "_find_chrome_exe",
                               return_value=r"C:\Fake\chrome.exe"), \
             mock.patch.object(pf, "_encerrar_chrome_cdp") as m_kill, \
             mock.patch.object(pf.time, "sleep", return_value=None):
            ok, msg = pf.ensure_chrome_cdp(**kwargs)
        return ok, msg, m_popen, m_kill

    @staticmethod
    def _perfil_usado(m_popen):
        """Basename do `--user-data-dir=` passado ao Chrome (None se não houver)."""
        if not m_popen.called:
            return None
        args = m_popen.call_args[0][0]
        for a in args:
            if str(a).startswith("--user-data-dir="):
                return Path(str(a).split("=", 1)[1]).name
        return None


class TestPrioridadeDoPerfil(_BaseSemChrome):

    def test_01_conta_id_8_vence_contas_zero(self):
        """(i) conta_id=8 → conta_08, mesmo com contas[0]=conta_03."""
        ok, msg, popen, _ = self._rodar(contas_exemplo(), port=9222, conta_id=8)
        self.assertTrue(ok, msg)
        self.assertEqual(self._perfil_usado(popen), "conta_08")

    def test_02_login_guiado_force_restart_e_conta_pedida(self):
        """Login/Relogar (force_restart=True) abre o perfil da conta pedida."""
        ok, msg, popen, kill = self._rodar(contas_exemplo(), port=9222,
                                           force_restart=True, conta_id=8)
        self.assertTrue(ok, msg)
        self.assertTrue(kill.called, "force_restart deve encerrar o CDP anterior")
        self.assertEqual(self._perfil_usado(popen), "conta_08")

    def test_03_sem_conta_id_porta_9222_usa_conta_ativa(self):
        """(ii) sem conta_id, porta 9222 → perfil da conta ATIVA (não contas[0])."""
        dados = contas_exemplo()
        dados["contas"][0]["ativa"] = False   # conta_03 deixa de ser a ativa
        dados["contas"][1]["ativa"] = True    # conta_05 passa a ser a ativa
        ok, msg, popen, _ = self._rodar(dados, port=9222)
        self.assertTrue(ok, msg)
        self.assertEqual(self._perfil_usado(popen), "conta_05")

    def test_04_worker_id_explicito_usa_indice(self):
        """(iii) worker_id=1 EXPLÍCITO → contas[1] (produção paralela preservada)."""
        dados = contas_exemplo()
        dados["contas"][0]["ativa"] = True
        dados["contas"][1]["ativa"] = False
        ok, msg, popen, _ = self._rodar(dados, port=9223, worker_id=1)
        self.assertTrue(ok, msg)
        self.assertEqual(self._perfil_usado(popen), "conta_05")

    def test_05_worker_id_inferido_nao_usa_indice(self):
        """Regressão do bug: porta 9223 SEM worker_id explícito NÃO usa contas[1]."""
        dados = contas_exemplo()
        dados["contas"][0]["ativa"] = True    # ativa = conta_03
        dados["contas"][1]["ativa"] = False
        dados["contas"][2]["ativa"] = False
        ok, msg, popen, _ = self._rodar(dados, port=9223)
        self.assertTrue(ok, msg)
        self.assertEqual(self._perfil_usado(popen), "conta_03",
                         "sem worker_id explícito a conta vem de ativa=True, não do índice")

    def test_06_conta_id_inexistente_erro_sem_chrome(self):
        """(iv) conta_id inexistente → erro claro e Popen NUNCA chamado."""
        ok, msg, popen, _ = self._rodar(contas_exemplo(), port=9222, conta_id=999)
        self.assertFalse(ok)
        self.assertFalse(popen.called, "nenhum Chrome pode ser aberto")
        self.assertIn("999", msg)
        self.assertIn("não encontrada", msg)

    def test_07_sem_conta_ativa_usa_contas_zero(self):
        """(v) nenhuma conta ativa → contas[0]."""
        dados = contas_exemplo()
        dados["contas"][0]["ativa"] = False   # nenhuma conta ativa
        ok, msg, popen, _ = self._rodar(dados, port=9222)
        self.assertTrue(ok, msg)
        self.assertEqual(self._perfil_usado(popen), "conta_03")

    def test_08_modo_video_preservado(self):
        """(b) modo video → proxima_conta_para_video() (comportamento anterior intacto)."""
        alvo = {"id": 8, "nome": "conta_08", "profile_dir": "lira_profiles/conta_08"}
        with mock.patch(
            "services.flow_account_manager.FlowAccountManager.proxima_conta_para_video",
            return_value=alvo,
        ):
            ok, msg, popen, _ = self._rodar(contas_exemplo(), port=9222, modo="video")
        self.assertTrue(ok, msg)
        self.assertEqual(self._perfil_usado(popen), "conta_08")

    def test_09_profile_dir_explicito_tem_precedencia(self):
        """profile_dir passado pelo chamador (orquestrador/api_v2) continua vencendo."""
        ok, msg, popen, _ = self._rodar(contas_exemplo(), port=9222,
                                        profile_dir="lira_profiles/outro_perfil",
                                        conta_id=8, worker_id=2)
        self.assertTrue(ok, msg)
        self.assertEqual(self._perfil_usado(popen), "outro_perfil")

    def test_10_conta_sem_profile_dir_erro_sem_chrome(self):
        """Conta alvo sem `profile_dir` → erro claro, sem abrir nenhum Chrome."""
        dados = {"contas": [{"id": 8, "nome": "conta_08", "profile_dir": ""}]}
        ok, msg, popen, _ = self._rodar(dados, port=9222, conta_id=8)
        self.assertFalse(ok)
        self.assertFalse(popen.called)
        self.assertIn("profile_dir", msg)

    def test_11_conta_id_como_string(self):
        """`conta_id` vindo do body como string ("8") também resolve."""
        ok, msg, popen, _ = self._rodar(contas_exemplo(), port=9222, conta_id="8")
        self.assertTrue(ok, msg)
        self.assertEqual(self._perfil_usado(popen), "conta_08")


class TestContratoAssinatura(unittest.TestCase):
    """A assinatura, a rota e a ordem da flag preservam o contrato pedido."""

    def test_01_conta_id_e_o_ultimo_parametro_da_conta(self):
        """`url_extra` (v9.20) foi acrescentado no FINAL, depois de `conta_id`."""
        params = list(inspect.signature(pf.ensure_chrome_cdp).parameters)
        self.assertEqual(
            params,
            ["port", "force_restart", "profile_dir", "projeto_id",
             "worker_id", "modo", "conta_id", "url_extra", "visivel"],
        )
        _assin = inspect.signature(pf.ensure_chrome_cdp).parameters
        self.assertIsNone(_assin["conta_id"].default)
        self.assertIsNone(_assin["url_extra"].default)
        self.assertIs(_assin["visivel"].default, False)

    def test_02_rota_login_guiado_passa_conta_id_e_url_extra(self):
        src = (RAIZ / "app_web.py").read_text(encoding="utf-8")
        ini = src.index("def flow_contas_login_guiado(")
        trecho = src[ini:ini + 6000]
        self.assertIn("ensure_chrome_cdp(9222, force_restart=True, conta_id=conta_id,", trecho)
        self.assertIn("url_extra=FLOW_LOGIN_URL", trecho)

    def test_03_flag_de_worker_explicito_antes_da_inferencia(self):
        src = (RAIZ / "services" / "playwright_flow.py").read_text(encoding="utf-8")
        ini = src.index("def ensure_chrome_cdp(")
        trecho = src[ini:ini + 4000]
        pos_flag = trecho.index("worker_id_informado = worker_id is not None")
        pos_infer = trecho.index("if worker_id is None:")
        self.assertLess(pos_flag, pos_infer,
                        "a flag precisa ser capturada ANTES da inferência por porta")

    def test_04_arquivo_real_de_contas_intacto_e_valido(self):
        """Sanidade: o `config/flow_accounts.json` real segue existindo e é JSON válido."""
        self.assertTrue(ARQUIVO_REAL_CONTAS.exists())
        json.loads(ARQUIVO_REAL_CONTAS.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
