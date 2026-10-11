# -*- coding: utf-8 -*-
"""
tests/test_login_guiado_polling.py — Login guiado: polling por HTTP, SEM Playwright
===================================================================================
Bug: o polling do "Logar/Relogar" chamava `_iniciar_sessao_thread` a cada 5s. Com o
usuário parado em `accounts.google.com`, o worker caía no `new_page()` do
`_resolver_aba_flow` e abria uma aba EXTRA do Flow, atrapalhando o login manual.

Correção: o Chrome abre JÁ na tela de login (`url_extra=FLOW_LOGIN_URL`) e o polling
apenas OBSERVA as abas em `http://127.0.0.1:9222/json/list` (urllib). Só quando existe
uma aba do Flow LOGADO (`flow.google.com` fora de `/about`, ou `labs.google/fx`) a
extração existente (e-mail/créditos/projeto) roda — UMA única vez.

Sem Chrome e sem rede: `urllib.request.urlopen`, `time.sleep`, `ensure_chrome_cdp` e o
worker são mockados; as contas vão para um arquivo TEMPORÁRIO (nunca o
`config/flow_accounts.json` real) e nenhuma pasta é criada em `lira_profiles`.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app_web
import services.playwright_flow as pf

RAIZ = Path(__file__).resolve().parents[1]
ARQUIVO_REAL_CONTAS = (RAIZ / "config" / "flow_accounts.json").resolve()

LOGIN_URL = (
    "https://accounts.google.com/v3/signin/identifier"
    "?continue=https://flow.google.com/&flowName=GlifWebSignIn&flowEntry=ServiceLogin"
)
FLOW_ABAS = [{"type": "page", "url": LOGIN_URL},
             {"type": "page", "url": "https://flow.google.com/u/1/"}]


def _resposta_urlopen(payload):
    """Context manager falso no formato de `urllib.request.urlopen(req)`."""
    cm = mock.MagicMock()
    cm.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
    cm.__exit__.return_value = False
    return cm


class TestAbaFlowLogada(unittest.TestCase):
    """Classificador puro das abas — olha o HOST de verdade, não substring da URL."""

    def test_01_flow_logado_aceito(self):
        self.assertTrue(app_web._aba_flow_logada("https://flow.google.com/"))
        self.assertTrue(app_web._aba_flow_logada("https://flow.google.com/u/1/"))
        self.assertTrue(app_web._aba_flow_logada("https://labs.google/fx/tools/flow"))

    def test_02_about_login_e_outros_rejeitados(self):
        self.assertFalse(app_web._aba_flow_logada("https://flow.google.com/about"))
        # A tela de login carrega "flow.google.com" na QUERY — não pode dar falso positivo.
        self.assertFalse(app_web._aba_flow_logada(LOGIN_URL))
        self.assertFalse(app_web._aba_flow_logada("about:blank"))
        self.assertFalse(app_web._aba_flow_logada("http://127.0.0.1:5000/?cdp=1"))
        self.assertFalse(app_web._aba_flow_logada(""))

    def test_03_fluxo_de_login_usa_a_constante_de_login(self):
        self.assertIn("accounts.google.com/v3/signin/identifier", pf.FLOW_LOGIN_URL)
        self.assertIn("continue=https://flow.google.com/", pf.FLOW_LOGIN_URL)
        self.assertNotIn("dsh=", pf.FLOW_LOGIN_URL, "o parâmetro de sessão `dsh` não entra")


class _BaseLoginGuiado(unittest.TestCase):
    """Chama a ROTA real com urllib/time/worker/Chrome travados."""

    CONTA_ID = 1

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="t_login_guiado_")
        self.contas_path = Path(self._tmp.name) / "flow_accounts.json"
        self.assertNotEqual(
            str(self.contas_path).lower(), str(ARQUIVO_REAL_CONTAS).lower(),
            "o teste NUNCA pode escrever no config/flow_accounts.json real",
        )
        self.contas_path.write_text(json.dumps({
            "contas": [{"id": 1, "nome": "conta_login", "email": "",
                        "profile_dir": "lira_profiles/conta_01",
                        "creditos_esgotados": False, "ativa": True}]
        }, ensure_ascii=False), encoding="utf-8")
        _p = mock.patch.object(app_web, "_flow_accounts_path", lambda: self.contas_path)
        _p.start()
        self.addCleanup(_p.stop)
        self.addCleanup(self._tmp.cleanup)

        self.app = app_web.app
        self.client = self.app.test_client()
        self.worker = mock.MagicMock()
        self.worker.is_running_queue = False
        self.worker._iniciar_sessao_thread.return_value = (True, "sessão ok")
        self.worker._extrair_email_via_painel_conta.return_value = None
        self.worker._verificar_creditos_disponiveis.return_value = False
        self._flow_cls = mock.MagicMock()
        self._flow_cls.get_worker.return_value = self.worker
        self.sleep_calls = None
        self._ensure_calls = None

    def _post(self, abas_por_tentativa, email=None, projeto_id=None, erro_urlopen=False):
        """Executa a rota. `abas_por_tentativa` = abas retornadas em CADA poll."""
        self.worker._extrair_email_via_painel_conta.return_value = email
        respostas = list(abas_por_tentativa)

        def _urlopen_fake(*_a, **_k):
            if erro_urlopen:
                raise OSError("porta 9222 indisponível")
            return _resposta_urlopen(respostas.pop(0) if respostas else [])

        body = {"conta_id": self.CONTA_ID}
        if projeto_id:
            body["projeto_id"] = projeto_id
        with mock.patch("services.playwright_flow.ensure_chrome_cdp",
                        return_value=(True, "Chrome iniciado e CDP disponível.")) as m_ensure, \
             mock.patch("services.playwright_flow.FlowQueueWorker", self._flow_cls), \
             mock.patch("urllib.request.urlopen", side_effect=_urlopen_fake), \
             mock.patch.object(app_web.time, "sleep", return_value=None) as m_sleep:
            r = self.client.post("/api/flow/contas/login_guiado", json=body)
        self._ensure_calls = m_ensure
        self.sleep_calls = m_sleep.call_count
        return r

    # ---------------------------------------------------------------- casos (i)-(iv)
    def test_04_so_aba_de_login_nao_inicia_sessao(self):
        """(i) Só a aba de login → `_iniciar_sessao_thread` NÃO é chamado."""
        r = self._post([[{"type": "page", "url": LOGIN_URL}]] * 12)
        self.assertEqual(r.status_code, 200)
        self.worker._iniciar_sessao_thread.assert_not_called()
        self.worker._extrair_email_via_painel_conta.assert_not_called()
        self.assertIsNone((r.get_json() or {}).get("email"))

    def test_05_flow_logado_extrai_uma_unica_vez_e_grava_email(self):
        """(ii) Flow logado → chamado UMA vez e o e-mail é gravado na conta."""
        r = self._post([FLOW_ABAS], email="nova@exemplo.invalid")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.worker._iniciar_sessao_thread.call_count, 1)
        self.worker._extrair_email_via_painel_conta.assert_called_once()
        self.worker._verificar_creditos_disponiveis.assert_called_once()
        self.worker._encerrar_sessao.assert_called_once()
        self.assertEqual((r.get_json() or {}).get("email"), "nova@exemplo.invalid")
        gravado = json.loads(self.contas_path.read_text(encoding="utf-8"))
        self.assertEqual(gravado["contas"][0]["email"], "nova@exemplo.invalid")

    def test_06_so_about_nao_inicia_sessao(self):
        """(iii) Só `flow.google.com/about` (deslogado) → NÃO chama a sessão."""
        r = self._post([[{"type": "page", "url": "https://flow.google.com/about"}]] * 12)
        self.worker._iniciar_sessao_thread.assert_not_called()
        self.assertIsNone((r.get_json() or {}).get("email"))

    def test_07_tempo_esgotado_success_true_email_none(self):
        """(iv) Tempo esgotado → success True, email None e Chrome permanece aberto."""
        r = self._post([[]] * 12)
        body = r.get_json() or {}
        self.assertTrue(body.get("success"))
        self.assertIsNone(body.get("email"))
        self.assertIn("login não concluído", body.get("message", ""))
        self.worker._iniciar_sessao_thread.assert_not_called()
        self.assertEqual(self.sleep_calls, 11, "12 polls → 11 esperas de 5s")

    def test_08_erro_de_urllib_e_tolerado(self):
        """Porta 9222 fora do ar não derruba a rota: segue tentando e devolve sucesso."""
        r = self._post([], erro_urlopen=True)
        body = r.get_json() or {}
        self.assertEqual(r.status_code, 200)
        self.assertTrue(body.get("success"))
        self.assertIsNone(body.get("email"))
        self.worker._iniciar_sessao_thread.assert_not_called()
        self.assertEqual(self.sleep_calls, 11)

    def test_09_labs_google_fx_conta_como_flow_logado(self):
        abas = [{"type": "page", "url": "https://labs.google/fx/tools/flow/project/123"}]
        r = self._post([abas], email="labs@exemplo.invalid")
        self.assertEqual(self.worker._iniciar_sessao_thread.call_count, 1)
        self.assertEqual((r.get_json() or {}).get("email"), "labs@exemplo.invalid")

    def test_10_com_projeto_e_creditos_vincula_projeto(self):
        """Com créditos + projeto_id, `_ensure_project_open` roda com o conta_id."""
        self.worker._verificar_creditos_disponiveis.return_value = True
        r = self._post([FLOW_ABAS], email="com@exemplo.invalid", projeto_id="ProjX")
        self.assertEqual((r.get_json() or {}).get("creditos_ok"), True)
        self.worker._ensure_project_open.assert_called_once()
        args, kwargs = self.worker._ensure_project_open.call_args
        self.assertEqual(args[0], "ProjX")
        self.assertEqual(kwargs.get("conta_id"), self.CONTA_ID)

    def test_11_fila_ativa_responde_409_sem_tocar_no_chrome(self):
        self.worker.is_running_queue = True
        with mock.patch("services.playwright_flow.ensure_chrome_cdp") as m_ensure, \
             mock.patch("services.playwright_flow.FlowQueueWorker", self._flow_cls), \
             mock.patch.object(app_web, "_voltar_ao_studio") as m_voltar:
            r = self.client.post("/api/flow/contas/login_guiado",
                                 json={"conta_id": self.CONTA_ID})
        self.assertEqual(r.status_code, 409)
        m_ensure.assert_not_called()
        self.worker._iniciar_sessao_thread.assert_not_called()
        m_voltar.assert_not_called()

    def test_12_chrome_recebe_a_url_de_login(self):
        """`url_extra=FLOW_LOGIN_URL` é repassado ao ensure_chrome_cdp."""
        self._post([[{"type": "page", "url": LOGIN_URL}]] * 12)
        _, kwargs = self._ensure_calls.call_args
        self.assertEqual(kwargs.get("conta_id"), self.CONTA_ID)
        self.assertEqual(kwargs.get("url_extra"), pf.FLOW_LOGIN_URL)
        self.assertTrue(self._ensure_calls.call_args[0][0] == 9222)
        self.assertTrue(kwargs.get("force_restart"))

    def test_13_login_ok_chama_voltar_ao_studio_uma_vez(self):
        """E-mail extraído → `_voltar_ao_studio()` chamado UMA vez (sem argumentos)."""
        with mock.patch.object(app_web, "_voltar_ao_studio") as m_voltar:
            r = self._post([FLOW_ABAS], email="ok@exemplo.invalid")
        self.assertEqual((r.get_json() or {}).get("email"), "ok@exemplo.invalid")
        m_voltar.assert_called_once_with()

    def test_14_tempo_esgotado_nao_chama_voltar_ao_studio(self):
        """(iv) Tempo esgotado → `_voltar_ao_studio()` NÃO é chamado."""
        with mock.patch.object(app_web, "_voltar_ao_studio") as m_voltar:
            r = self._post([[]] * 12)
        self.assertTrue((r.get_json() or {}).get("success"))
        m_voltar.assert_not_called()


class TestVoltarAoStudio(unittest.TestCase):
    """TAREFA 3 — `_voltar_ao_studio()` fecha o Flow e ativa o Studio (HTTP puro)."""

    STUDIO = {"id": "ABA-STUDIO", "type": "page", "url": "http://127.0.0.1:5000/?cdp=1"}
    FLOW = {"id": "ABA-FLOW", "type": "page", "url": "https://flow.google.com/?pli=1"}
    FLOW_ABOUT = {"id": "ABA-ABOUT", "type": "page", "url": "https://flow.google.com/about"}
    LOGIN = {"id": "ABA-LOGIN", "type": "page", "url": LOGIN_URL}

    @staticmethod
    def _cm(payload):
        cm = mock.MagicMock()
        cm.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
        cm.__exit__.return_value = False
        return cm

    def _executar(self, abas, erro_list=False, erro_acao=False):
        urls = []

        def _urlopen(url, timeout=None, **_k):
            alvo = url if isinstance(url, str) else getattr(url, "full_url", str(url))
            urls.append(alvo)
            if erro_list:
                raise OSError("porta 9222 fora do ar")
            if alvo.endswith("/json/list"):
                return self._cm(abas)
            if erro_acao:
                raise OSError("close/activate recusado")
            return self._cm("OK")

        with mock.patch("urllib.request.urlopen", side_effect=_urlopen):
            app_web._voltar_ao_studio()          # NUNCA pode levantar exceção
        return urls

    def test_01_fecha_so_o_flow_e_ativa_o_studio(self):
        """(i) abas Studio+Flow → close SÓ com o id do Flow; activate com o do Studio."""
        urls = self._executar([self.LOGIN, self.FLOW, self.STUDIO])
        self.assertEqual([u for u in urls if "/json/close/" in u],
                         ["http://127.0.0.1:9222/json/close/ABA-FLOW"])
        self.assertEqual([u for u in urls if "/json/activate/" in u],
                         ["http://127.0.0.1:9222/json/activate/ABA-STUDIO"])

    def test_02_aba_about_tambem_e_fechada(self):
        urls = self._executar([self.FLOW_ABOUT, self.STUDIO])
        self.assertEqual([u for u in urls if "/json/close/" in u],
                         ["http://127.0.0.1:9222/json/close/ABA-ABOUT"])

    def test_03_sem_aba_studio_nao_fecha_nada(self):
        """(ii) sem aba do Studio → nenhum close (fechar a última aba mataria o Chrome)."""
        urls = self._executar([self.LOGIN, self.FLOW])
        self.assertEqual([u for u in urls if "/json/close/" in u], [])
        self.assertEqual([u for u in urls if "/json/activate/" in u], [])

    def test_04_urlopen_falhando_nao_levanta(self):
        """(iii) `urlopen` falhando → sem exceção (só a listagem é tentada)."""
        urls = self._executar([self.FLOW, self.STUDIO], erro_list=True)
        self.assertEqual(urls, ["http://127.0.0.1:9222/json/list"])

    def test_05_falha_do_close_e_do_activate_tolerada(self):
        urls = self._executar([self.FLOW, self.STUDIO], erro_acao=True)
        self.assertTrue(any("/json/close/" in u for u in urls))
        self.assertTrue(any("/json/activate/" in u for u in urls))

    def test_06_classificador_de_abas_do_flow(self):
        self.assertTrue(app_web._aba_flow_para_fechar("https://flow.google.com/about"))
        self.assertTrue(app_web._aba_flow_para_fechar("https://flow.google.com/?pli=1"))
        self.assertTrue(app_web._aba_flow_para_fechar("https://labs.google/fx/tools/flow"))
        self.assertFalse(app_web._aba_flow_para_fechar(LOGIN_URL))
        self.assertFalse(app_web._aba_flow_para_fechar("http://127.0.0.1:5000/?cdp=1"))
        self.assertFalse(app_web._aba_flow_para_fechar(""))


class TestAbaFlowReaproveitada(unittest.TestCase):
    """TAREFA 3: aba `flow.google.com` (fora de /about) é REAPROVEITADA — sem new_page."""

    @staticmethod
    def _worker_com_paginas(urls):
        w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
        w.context = mock.MagicMock()
        w.context.pages = []
        for u in urls:
            p = mock.MagicMock()
            p.url = u
            p.is_closed.return_value = False
            p.title.return_value = "Google Flow"
            w.context.pages.append(p)
        w.page = None
        w.current_project_id = ""
        return w

    def test_01_aba_flow_logada_e_reaproveitada(self):
        w = self._worker_com_paginas(["https://flow.google.com/u/1/"])
        pagina = w._resolver_aba_flow()
        self.assertIs(pagina, w.context.pages[0])
        w.context.new_page.assert_not_called()

    def test_02_eh_aba_flow_valida_aceita_flow_google_fora_de_about(self):
        self.assertTrue(pf.PlaywrightCDPWorker._eh_aba_flow_valida("https://flow.google.com/"))
        self.assertTrue(pf.PlaywrightCDPWorker._eh_aba_flow_valida("https://flow.google.com/u/1/"))
        self.assertFalse(pf.PlaywrightCDPWorker._eh_aba_flow_valida(LOGIN_URL))

    def test_03_apenas_aba_de_login_forcaria_new_page(self):
        """Documenta POR QUE o portão do polling existe: sem Flow logado, cria aba nova."""
        w = self._worker_com_paginas([LOGIN_URL])
        with mock.patch.object(pf, "open_flow_tab", side_effect=lambda p, target_url=None: p):
            pagina = w._resolver_aba_flow()
        self.assertTrue(w.context.new_page.called, "sem aba do Flow, o resolver abre uma nova")
        self.assertIsNotNone(pagina)


class TestEmailSemCache(unittest.TestCase):
    """TAREFA 3 — o e-mail vem do DOM da aba ATUAL; cache/fallback só servem à fila."""

    @staticmethod
    def _worker_com_dom(aria_label=None, account_email=None):
        """Worker 'cru' com `page` mockada devolvendo (ou não) o e-mail no DOM."""
        w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
        w.account_email = account_email
        w.playwright = None
        w.browser = None
        w.page = mock.MagicMock()
        w.page.is_closed.return_value = False
        if aria_label is None:
            w.page.locator.return_value.all.return_value = []
        else:
            el = mock.MagicMock()
            el.get_attribute.return_value = aria_label
            w.page.locator.return_value.all.return_value = [el]
        return w

    def test_01_encerrar_sessao_zera_o_cache(self):
        """(i) `_encerrar_sessao` limpa `account_email`."""
        w = self._worker_com_dom(account_email="conta3@exemplo.invalid")
        pf.PlaywrightCDPWorker._encerrar_sessao(w)
        self.assertIsNone(w.account_email)

    def test_02_iniciar_sessao_zera_o_cache(self):
        """(i) `_iniciar_sessao_thread` também começa com o cache zerado."""
        w = self._worker_com_dom(account_email="conta3@exemplo.invalid")
        w.port = 9222
        w.profile_dir = None
        w.worker_id = 0
        w.current_project_id = ""
        w._session_thread_ident = None
        with mock.patch.object(pf, "ensure_chrome_cdp", return_value=(False, "sem CDP (teste)")):
            ok, _msg = pf.PlaywrightCDPWorker._iniciar_sessao_thread(w)
        self.assertFalse(ok)
        self.assertIsNone(w.account_email)

    def test_03_usar_cache_false_ignora_o_cache_e_le_o_dom(self):
        """(ii) cache de OUTRA conta + `usar_cache=False` → devolve o e-mail do DOM."""
        w = self._worker_com_dom(
            aria_label="Conta do Google: Fulano (nova@exemplo.invalid)",
            account_email="antiga@exemplo.invalid",
        )
        email = w._extrair_email_via_painel_conta(usar_cache=False)
        self.assertEqual(email, "nova@exemplo.invalid")
        self.assertEqual(w.account_email, "nova@exemplo.invalid",
                         "ao ler do DOM o cache continua sendo gravado")

    def test_04_usar_cache_false_sem_dom_nao_consulta_o_config(self):
        """(iii) DOM sem e-mail + `usar_cache=False` → None e o config NÃO é consultado."""
        w = self._worker_com_dom(account_email="antiga@exemplo.invalid")
        with mock.patch("services.flow_account_manager.FlowAccountManager.listar_contas_ativas") as m_cfg:
            email = w._extrair_email_via_painel_conta(usar_cache=False)
        self.assertIsNone(email)
        m_cfg.assert_not_called()
        self.assertEqual(w.account_email, "antiga@exemplo.invalid", "cache intacto")

    def test_05_usar_cache_padrao_devolve_o_cache_sem_tocar_no_dom(self):
        """(iv) `usar_cache` padrão (fila) continua devolvendo o cache, sem reler o DOM."""
        w = self._worker_com_dom(
            aria_label="Conta do Google: X (dom@exemplo.invalid)",
            account_email="cache@exemplo.invalid",
        )
        email = w._extrair_email_via_painel_conta()          # chamada SEM argumento
        self.assertEqual(email, "cache@exemplo.invalid")
        w.page.locator.assert_not_called()


class TestEmailPorContaDoisLogins(unittest.TestCase):
    """(v) dois logins seguidos: cada conta grava o SEU e-mail (lido do DOM).

    NÃO herda de `_BaseLoginGuiado` de propósito: a herança traria junto os 11 casos
    daquela classe (worker MOCKADO), incompatíveis com o worker REAL usado aqui.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="t_email_por_conta_")
        self.contas_path = Path(self._tmp.name) / "flow_accounts.json"
        self.assertNotEqual(
            str(self.contas_path).lower(), str(ARQUIVO_REAL_CONTAS).lower(),
            "o teste NUNCA pode escrever no config/flow_accounts.json real",
        )
        self.contas_path.write_text(json.dumps({"contas": [
            {"id": 1, "nome": "conta_01", "email": "", "profile_dir": "lira_profiles/conta_01",
             "creditos_esgotados": False, "ativa": True},
            {"id": 2, "nome": "conta_02", "email": "", "profile_dir": "lira_profiles/conta_02",
             "creditos_esgotados": False, "ativa": False},
        ]}, ensure_ascii=False), encoding="utf-8")
        _p = mock.patch.object(app_web, "_flow_accounts_path", lambda: self.contas_path)
        _p.start()
        self.addCleanup(_p.stop)
        self.addCleanup(self._tmp.cleanup)

        self.client = app_web.app.test_client()
        self._flow_cls = mock.MagicMock()

        # Worker REAL: `_extrair_email_via_painel_conta` e `_encerrar_sessao` são os de
        # verdade (é isso que se quer validar); só a sessão Playwright é stubada.
        w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
        w.account_email = None
        w.playwright = None
        w.browser = None
        w.context = None
        w.page = None
        w.current_project_id = ""
        w.is_running_queue = False          # a rota consulta este atributo
        w._iniciar_sessao_thread = mock.MagicMock(return_value=(True, "sessão ok"))
        w._verificar_creditos_disponiveis = mock.MagicMock(return_value=False)
        self.worker = w
        self._flow_cls.get_worker.return_value = w
        self._estado_dom = {"aria": ""}

    def _preparar_pagina(self, aria_label):
        """Simula a aba NOVA de cada login (o DOM muda de conta para conta)."""
        self._estado_dom["aria"] = aria_label
        self.worker.page = mock.MagicMock()
        self.worker.page.is_closed.return_value = False
        el = mock.MagicMock()
        el.get_attribute.side_effect = lambda nome: (
            self._estado_dom["aria"] if nome == "aria-label" else None)
        self.worker.page.locator.return_value.all.return_value = [el]

    def _post_conta(self, conta_id):
        with mock.patch("services.playwright_flow.ensure_chrome_cdp",
                        return_value=(True, "ok")), \
             mock.patch("services.playwright_flow.FlowQueueWorker", self._flow_cls), \
             mock.patch("urllib.request.urlopen", return_value=_resposta_urlopen(FLOW_ABAS)), \
             mock.patch.object(app_web.time, "sleep", return_value=None):
            return self.client.post("/api/flow/contas/login_guiado", json={"conta_id": conta_id})

    def test_01_cada_conta_grava_o_seu_email(self):
        self._preparar_pagina("Conta do Google: A (email1@exemplo.invalid)")
        r1 = self._post_conta(1)
        self.assertEqual((r1.get_json() or {}).get("email"), "email1@exemplo.invalid")

        self._preparar_pagina("Conta do Google: B (email2@exemplo.invalid)")
        r2 = self._post_conta(2)
        self.assertEqual((r2.get_json() or {}).get("email"), "email2@exemplo.invalid")

        gravado = json.loads(self.contas_path.read_text(encoding="utf-8"))
        emails = {c["id"]: c.get("email") for c in gravado["contas"]}
        self.assertEqual(emails[1], "email1@exemplo.invalid")
        self.assertEqual(emails[2], "email2@exemplo.invalid",
                         "a 2ª conta NÃO pode receber o e-mail da 1ª (bug do cache)")
        self.assertIsNone(self.worker.account_email, "_encerrar_sessao zerou o cache")


if __name__ == "__main__":
    unittest.main(verbosity=2)
