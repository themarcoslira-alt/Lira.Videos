# -*- coding: utf-8 -*-
"""
tests/test_rotacao_conta_reset.py — RESET TOTAL na troca de conta

Valida as 3 modificações cirúrgicas de services/playwright_flow.py:

1) salvar/carregar_projeto_flow_url indexam URLs por conta (urls_por_conta{},
   param `conta_id`) mantendo compat retroativa (flow_project_url).
2) _rotacionar_conta faz RESET TOTAL: reinicia/reconecta Chrome CDP, limpa o
   estado cacheado da conta ANTERIOR, NUNCA navega à URL da conta antiga e CRIA
   projeto novo quando a conta nova não tem projeto próprio (1 projeto/conta).
3) _reprovisionar_personagem_apos_rotacao recria o personagem na conta nova
   (criar_personagem_flow) quando as cenas usam personagem, marcando
   _avatar_uploaded = True.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import services.playwright_flow as pf


def _worker(**kw):
    w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
    w.port = 9222
    w.page = mock.Mock()
    w.browser = None
    w.context = None
    w.playwright = None
    w.current_flow_reference = None
    w.current_project_id = "ProjX"
    w.current_flow_mode = "imagem"
    w.current_project_name = None
    w.account_email = None
    w._avatar_uploaded = False
    w._project_url_saved = False
    w.current_delay_info = None
    w.current_model = "Nano Banana 2"
    w.is_fallback_active = False
    for k, v in kw.items():
        setattr(w, k, v)
    return w


class TestUrlsPorConta(unittest.TestCase):
    """Modificação 1 — indexação de URL por conta em urls_por_conta{}."""

    def test_salvar_carregar_indexa_por_conta(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(pf, "PROJETOS_DIR", Path(tmp)):
                pf.salvar_projeto_flow_url("P", "URL_CONTA_1", conta_id=1)
                pf.salvar_projeto_flow_url("P", "URL_CONTA_2", conta_id=2)
                # sem conta_id → usa a conta ativa resolvida internamente
                with mock.patch.object(pf, "_conta_ativa_id", return_value=3):
                    pf.salvar_projeto_flow_url("P", "URL_CONTA_3")

                meta = json.loads((Path(tmp) / "P" / "flow_meta.json").read_text(encoding="utf-8"))
                self.assertEqual(meta["urls_por_conta"],
                                 {"1": "URL_CONTA_1", "2": "URL_CONTA_2", "3": "URL_CONTA_3"})
                # flow_project_url continua sendo gravado (retrocompat)
                self.assertEqual(meta["flow_project_url"], "URL_CONTA_3")

                # Leitura estrita por conta
                self.assertEqual(pf.carregar_projeto_flow_url("P", conta_id=1), "URL_CONTA_1")
                self.assertEqual(pf.carregar_projeto_flow_url("P", conta_id=2), "URL_CONTA_2")
                # conta sem projeto próprio → None (nunca cai p/ URL de outra conta)
                self.assertIsNone(pf.carregar_projeto_flow_url("P", conta_id=99))
                # sem conta_id → legado flow_project_url
                self.assertEqual(pf.carregar_projeto_flow_url("P"), "URL_CONTA_3")

    def test_carregar_sem_meta_retorna_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(pf, "PROJETOS_DIR", Path(tmp)):
                self.assertIsNone(pf.carregar_projeto_flow_url("P"))
                self.assertIsNone(pf.carregar_projeto_flow_url("P", conta_id=1))


class TestRotacionarContaReset(unittest.TestCase):
    """Modificação 2 — RESET TOTAL em _rotacionar_conta()."""

    def _patch_accounts_file(self, contas):
        """Redireciona `config/flow_accounts.json` para um arquivo temporário."""
        acc = Path(tempfile.gettempdir()) / "flow_accounts_test_rotacao.json"
        acc.write_text(json.dumps({"contas": contas}), encoding="utf-8")
        orig_exists = pf.Path.exists
        orig_read = pf.Path.read_text
        orig_write = pf.Path.write_text

        def _fake_exists(self_):
            if str(self_).replace("\\", "/") == "config/flow_accounts.json":
                return True
            return orig_exists(self_)

        def _fake_read(self_, *a, **k):
            if str(self_).replace("\\", "/") == "config/flow_accounts.json":
                return acc.read_text(encoding="utf-8")
            return orig_read(self_, *a, **k)

        def _fake_write(self_, data, *a, **k):
            if str(self_).replace("\\", "/") == "config/flow_accounts.json":
                acc.write_text(data, encoding="utf-8")
                return None
            return orig_write(self_, data, *a, **k)

        return acc, (
            mock.patch.object(pf.Path, "exists", _fake_exists),
            mock.patch.object(pf.Path, "read_text", _fake_read),
            mock.patch.object(pf.Path, "write_text", _fake_write),
        )

    def test_nunca_navega_url_antiga_e_cria_projeto_conta2(self):
        acc, patchers = self._patch_accounts_file([
            {"id": 1, "nome": "Conta 1", "ativa": True, "creditos_esgotados": False},
            {"id": 2, "nome": "Conta 2", "ativa": False, "creditos_esgotados": False},
        ])
        page = mock.Mock()
        page.url = "https://labs.google/fx/pt/tools/flow"
        w = _worker(page=page, browser=None, context=None,
                    account_email="conta1@gmail.com", current_project_name="Antigo",
                    _avatar_uploaded=True, _project_url_saved=True)

        chamadas = {"ensure": 0, "carregadas": []}

        def _fake_iniciar_sessao():
            w.browser = mock.Mock()
            w.context = mock.Mock()
            w.page = page
            return True, "ok"

        def _fake_carregar(proj, conta_id=None):
            chamadas["carregadas"].append(conta_id)
            if conta_id == 2:
                return None  # conta 2 ainda sem projeto próprio (estrito)
            return "https://labs.google/fx/project/LEGADO_CONTA1"

        def _fake_ensure(proj, timeout_s=5, conta_id=None):
            chamadas["ensure"] += 1
            return True

        with mock.patch.object(pf, "ensure_chrome_cdp", return_value=(True, "ok")) as m_cdp, \
             mock.patch.object(pf.PlaywrightCDPWorker, "_encerrar_sessao"), \
             mock.patch.object(pf.PlaywrightCDPWorker, "_iniciar_sessao_thread", side_effect=_fake_iniciar_sessao), \
             mock.patch.object(pf, "carregar_projeto_flow_url", side_effect=_fake_carregar), \
             mock.patch.object(pf.PlaywrightCDPWorker, "_ensure_project_open", side_effect=_fake_ensure), \
             mock.patch.object(pf.PlaywrightCDPWorker, "_reprovisionar_personagem_apos_rotacao", return_value=False), \
             patchers[0], patchers[1], patchers[2]:
            w._rotacionar_conta()

        # 1. Chrome reiniciado com force_restart
        m_cdp.assert_called_once()
        self.assertTrue(m_cdp.call_args.kwargs.get("force_restart"))
        # 2. NUNCA navegou à URL da conta antiga (goto não foi chamado)
        self.assertFalse(page.goto.called)
        # 3. Consulta estrita para a conta nova (id=2)
        self.assertEqual(chamadas["carregadas"][-1], 2)
        # 4. Conta 2 sem projeto -> cria projeto novo (1 projeto por conta)
        self.assertEqual(chamadas["ensure"], 1)
        # 5. RESET TOTAL do estado cacheado da conta anterior
        self.assertIsNone(w.account_email)
        self.assertIsNone(w.current_project_name)
        self.assertFalse(w._avatar_uploaded)
        self.assertFalse(w._project_url_saved)

    def test_conta_nova_ja_tem_projeto_reutiliza_url(self):
        acc, patchers = self._patch_accounts_file([
            {"id": 1, "nome": "Conta 1", "ativa": True, "creditos_esgotados": False},
            {"id": 2, "nome": "Conta 2", "ativa": False, "creditos_esgotados": False},
        ])
        page = mock.Mock()
        page.url = "https://labs.google/fx/pt/tools/flow"
        w = _worker(page=page, browser=None, context=None,
                    _project_url_saved=False, _avatar_uploaded=True)

        chamadas = {"ensure": 0}

        def _fake_iniciar_sessao():
            w.browser = mock.Mock()
            w.context = mock.Mock()
            w.page = page
            return True, "ok"

        def _fake_carregar(proj, conta_id=None):
            return "https://labs.google/fx/project/CONTA2" if conta_id == 2 else None

        def _fake_ensure(*a, **k):
            chamadas["ensure"] += 1
            return True

        with mock.patch.object(pf, "ensure_chrome_cdp", return_value=(True, "ok")) as m_cdp, \
             mock.patch.object(pf.PlaywrightCDPWorker, "_encerrar_sessao"), \
             mock.patch.object(pf.PlaywrightCDPWorker, "_iniciar_sessao_thread", side_effect=_fake_iniciar_sessao), \
             mock.patch.object(pf, "carregar_projeto_flow_url", side_effect=_fake_carregar), \
             mock.patch.object(pf.PlaywrightCDPWorker, "_ensure_project_open", side_effect=_fake_ensure), \
             mock.patch.object(pf.PlaywrightCDPWorker, "_reprovisionar_personagem_apos_rotacao", return_value=False), \
             patchers[0], patchers[1], patchers[2]:
            w._rotacionar_conta()

        m_cdp.assert_called_once()
        # A URL da própria conta 2 é reutilizada (não cria projeto novo)
        page.goto.assert_called_once_with("https://labs.google/fx/project/CONTA2", timeout=45000)
        self.assertEqual(chamadas["ensure"], 0)
        self.assertTrue(w._project_url_saved)


class TestReprovisionarPersonagem(unittest.TestCase):
    """Modificação 3 — reprovisionar personagem na conta nova."""

    def test_cenas_usam_personagem_recria_e_marca_flag(self):
        from services import scene_plan_service as sps
        from services import character_service as chars

        w = _worker()
        w._avatar_uploaded = False
        foto = str(Path("C:/fake/reference.png"))

        with mock.patch.object(sps, "carregar_scene_plan",
                               return_value={"cenas": [
                                   {"uses_character": True, "scene_type": "avatar_talking",
                                    "character_ref": "@Neto"}]}), \
             mock.patch.object(chars, "obter_identidade_projeto", return_value={"nome": "Neto"}), \
             mock.patch.object(chars, "resolver_imagem_avatar_projeto", return_value=foto), \
             mock.patch.object(pf, "criar_personagem_flow", return_value=True) as m_criar, \
             mock.patch.object(pf.Path, "exists", return_value=True):
            ok = w._reprovisionar_personagem_apos_rotacao()

        self.assertTrue(ok)
        m_criar.assert_called_once()
        args_mock, _ = m_criar.call_args
        self.assertEqual(args_mock[0], w.page)
        self.assertEqual(args_mock[1], "Neto")
        self.assertEqual(args_mock[2], foto)
        self.assertTrue(w._avatar_uploaded)

    def test_sem_personagem_nas_cenas_nao_cria(self):
        from services import scene_plan_service as sps

        w = _worker()
        w._avatar_uploaded = False

        with mock.patch.object(sps, "carregar_scene_plan",
                               return_value={"cenas": [
                                   {"uses_character": False, "scene_type": "broll_macro"}]}), \
             mock.patch.object(pf, "criar_personagem_flow") as m_criar:
            ok = w._reprovisionar_personagem_apos_rotacao()

        self.assertFalse(ok)
        m_criar.assert_not_called()
        self.assertFalse(w._avatar_uploaded)


if __name__ == "__main__":
    unittest.main()