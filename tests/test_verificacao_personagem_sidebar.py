# -*- coding: utf-8 -*-
"""
tests/test_verificacao_personagem_sidebar.py — ANTIGRAVITY (pré-voo que recriou o @Marcos)
=========================================================================================
Causa raiz: o popover `flow-add-menu-popover-content` é ISOLADO POR PROJETO e abriu com
"Nenhum recurso encontrado"; `_aguardar_lista_personagens_estavel` devolveu "vazio" e a
verificação retornou False → o pré-voo do avatar recriou '@Marcos' (4ª cópia), mesmo com
os 4 tiles visíveis na sidebar.

Correção (3 camadas):
  1. Sidebar (`flow-character-tile .character-tile-name`) como leitura PRIMÁRIA — True
     sem abrir popover nem digitar '@'; zero tiles NÃO prova ausência.
  2. Registro local por CONTA + PROJETO do Flow (identidades/contas_flow.json, escrita
     atômica) preenchido quando a criação é confirmada ou quando a verificação acha.
  3. Política: False SOMENTE com prova acumulada (sidebar sem o tile + .empty-state em
     2 leituras estáveis sem spinner + registro sem entrada); registro com entrada ou
     qualquer instabilidade → None. No pré-voo do avatar, True E None pulam o upload.

Validação por SIMULAÇÃO (page/worker stub + registro em arquivo temporário): nada foi
validado em execução real contra o Flow.
"""
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

import services.character_service as chars
import services.playwright_flow as pf

UUID_PROJ = "1111aaaa-2222-4bbb-8ccc-333333333333"
UUID_OUTRO = "9999zzzz-8888-4yyy-8xxx-777777777777"
CONTA = 7
NOME = "Marcos"


class _PaginaStubSidebar:
    """Página mínima: URL de projeto + evaluate() só para o JS da sidebar.

    Qualquer `locator()` LEVANTA: prova que a decisão veio da sidebar (sem abrir
    popover/menu nem digitar '@').
    """

    def __init__(self, url="", nomes=None, tiles=None, avaliar_erro=False):
        self.url = url
        self._nomes = list(nomes or [])
        self._tiles = tiles if tiles is not None else len(self._nomes)
        self._avaliar_erro = avaliar_erro
        self.type_chamadas = []
        self.press_chamadas = []
        self.locator_chamadas = []
        self.keyboard = self

    def evaluate(self, js, *args):
        if js == pf.JS_LER_TILES_PERSONAGEM_SIDEBAR:
            if self._avaliar_erro:
                raise RuntimeError("evaluate falhou (sidebar)")
            return {"ok": True, "tiles": self._tiles, "nomes": list(self._nomes)}
        return {}

    def wait_for_timeout(self, ms):
        return None

    def locator(self, sel):
        self.locator_chamadas.append(str(sel))
        raise AssertionError(f"locator() não deveria ser chamado (decisão pela sidebar): {sel}")

    def press(self, key, **kw):
        self.press_chamadas.append(key)

    def type(self, text, **kw):
        self.type_chamadas.append(text)

    def focus(self):
        return None

    def insert_text(self, texto):
        return None


class _BaseVerificacao(unittest.TestCase):
    """Base: registro temporário + patches comuns."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="verif_sidebar_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.reg_path = self.tmp / "contas_flow.json"
        # Suíte rápida: o timeout REAL da sidebar (3s) é preservado em produção; aqui o
        # laço é encurtado para os stubs não gastarem segundos por chamada.
        _p = mock.patch.object(pf, "TIMEOUT_SIDEBAR_PERSONAGENS_S", 0.3)
        _p.start()
        self.addCleanup(_p.stop)

    def _worker(self, page):
        w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
        w.port = 9222
        w.worker_id = 0
        w.page = page
        w.current_project_id = "ProjTest"
        w.stop_requested = threading.Event()
        w._limpar_overlays_e_backdrops = MagicMock()
        w._safe_click = MagicMock()
        return w

    def _pagina_popover(self, nomes_sidebar=None):
        """Página MagicMock (para os cenários da camada do popover)."""
        page = MagicMock()
        page.url = f"https://labs.google/fx/tools/flow/project/{UUID_PROJ}"
        _nomes = list(nomes_sidebar or [])

        def _evaluate(js, *args):
            if js == pf.JS_LER_TILES_PERSONAGEM_SIDEBAR:
                return {"ok": True, "tiles": len(_nomes), "nomes": _nomes}
            return {}

        page.evaluate.side_effect = _evaluate
        return page

    def _patch_registro(self):
        return mock.patch.object(pf, "REGISTRO_CONTAS_FLOW_PATH", self.reg_path)

    def _patch_conta(self):
        return mock.patch.object(pf, "_conta_ativa_id", return_value=CONTA)

    def _escrever_registro(self, chaves, versao=1):
        self.reg_path.write_text(json.dumps({
            "versao": versao,
            "personagens": {k: {"nome": "Marcos", "conta": str(CONTA),
                                "projeto_flow": UUID_PROJ, "fonte": "teste",
                                "criado_em": "2026-01-01T00:00:00"} for k in chaves},
        }), encoding="utf-8")

    def _chave(self, nome=NOME, conta=CONTA, projeto=UUID_PROJ):
        return f"{conta}|{projeto}|{nome.strip().lower()}"


class TestCamadaSidebar(_BaseVerificacao):
    """TAREFA 2 — leitura PRIMÁRIA pela sidebar (True sem abrir popover/digitar '@')."""

    def test_a_sidebar_com_tile_retorna_true_sem_popover_nem_arroba(self):
        page = _PaginaStubSidebar(url=f"https://labs.google/fx/tools/flow/project/{UUID_PROJ}",
                                  nomes=[f"@{NOME}"])
        w = self._worker(page)
        with self._patch_registro(), self._patch_conta(), mock.patch.object(pf, "pw_log"):
            self.assertIs(w._verificar_personagem_na_biblioteca(NOME), True)
        self.assertEqual(page.type_chamadas, [], "não pode digitar '@'")
        self.assertEqual(page.press_chamadas, [], "não pode abrir/fechar popover")
        self.assertEqual(page.locator_chamadas, [], "não pode nem procurar o popover")
        _dados = json.loads(self.reg_path.read_text(encoding="utf-8"))
        self.assertIn(self._chave(), _dados["personagens"])
        self.assertEqual(_dados["personagens"][self._chave()]["fonte"], "sidebar")

    def test_f_nome_exato_nao_casa_com_sufixo(self):
        self.assertTrue(pf.PlaywrightCDPWorker._nome_casa_exato("@Marcos", "Marcos"))
        self.assertTrue(pf.PlaywrightCDPWorker._nome_casa_exato("@marcos", "MARCOS"))
        self.assertTrue(pf.PlaywrightCDPWorker._nome_casa_exato(" Marcos ", "@Marcos"))
        self.assertFalse(pf.PlaywrightCDPWorker._nome_casa_exato("@Marcos2", "Marcos"))
        self.assertFalse(pf.PlaywrightCDPWorker._nome_casa_exato("@Marcos", "Marcos2"))
        self.assertFalse(pf.PlaywrightCDPWorker._nome_casa_exato("@", "Marcos"))
        # sidebar SÓ com "@Marcos2": não casa → segue para o popover (vazio confirmado → False)
        page = self._pagina_popover(nomes_sidebar=["@Marcos2"])
        w = self._worker(page)
        w._aguardar_lista_personagens_estavel = MagicMock(return_value="vazio_confirmado")
        with self._patch_registro(), self._patch_conta(), mock.patch.object(pf, "pw_log"):
            self.assertIs(w._verificar_personagem_na_biblioteca(NOME), False)
        # controle: com o tile EXATO presente (mesmo com outro parecido) → True
        page2 = _PaginaStubSidebar(url=f"https://labs.google/fx/tools/flow/project/{UUID_PROJ}",
                                   nomes=["@Marcos2", "@Marcos"])
        w2 = self._worker(page2)
        with self._patch_registro(), self._patch_conta(), mock.patch.object(pf, "pw_log"):
            self.assertIs(w2._verificar_personagem_na_biblioteca(NOME), True)

    def test_sidebar_fora_da_rota_nao_decide(self):
        """Fora de /project/<uuid> a sidebar não é consultada; instabilidade → None."""
        page = self._pagina_popover()
        page.url = "https://labs.google/fx/tools/flow"
        w = self._worker(page)
        w._aguardar_lista_personagens_estavel = MagicMock(return_value="carregando")
        with self._patch_registro(), self._patch_conta(), mock.patch.object(pf, "pw_log"):
            self.assertIsNone(w._verificar_personagem_na_biblioteca(NOME))

    def test_sidebar_com_erro_de_leitura_nao_decide(self):
        """Erro no evaluate() da sidebar é TOLERANTE (log + segue) e não decide."""
        page = self._pagina_popover()

        def _evaluate(js, *args):
            if js == pf.JS_LER_TILES_PERSONAGEM_SIDEBAR:
                raise RuntimeError("evaluate falhou (sidebar)")
            return {}

        page.evaluate.side_effect = _evaluate
        w = self._worker(page)
        w._aguardar_lista_personagens_estavel = MagicMock(return_value="carregando")
        with self._patch_registro(), self._patch_conta(), mock.patch.object(pf, "pw_log") as m_log:
            self.assertIsNone(w._verificar_personagem_na_biblioteca(NOME))
            _msgs = " ".join(str(c.args[0]) for c in m_log.call_args_list if c.args)
        self.assertIn("sidebar", _msgs.lower())


class TestPoliticaDeDecisao(_BaseVerificacao):
    """TAREFA 4 — False SÓ com prova acumulada; instabilidade ou registro local → None."""

    def _rodar(self, *, nomes_sidebar=(), status_popover="vazio_confirmado"):
        page = self._pagina_popover(nomes_sidebar=list(nomes_sidebar))
        w = self._worker(page)
        w._aguardar_lista_personagens_estavel = MagicMock(return_value=status_popover)
        with self._patch_registro(), self._patch_conta(), mock.patch.object(pf, "pw_log") as m_log:
            res = w._verificar_personagem_na_biblioteca(NOME)
            msgs = " ".join(str(c.args[0]) for c in m_log.call_args_list if c.args)
        return res, msgs, w

    def test_b_sidebar_sem_tiles_popover_vazio_estavel_registro_vazio_retorna_false(self):
        res, msgs, _ = self._rodar(nomes_sidebar=[], status_popover="vazio_confirmado")
        self.assertIs(res, False)
        self.assertIn("sidebar+popover+registro", msgs, "o log deve indicar as fontes da prova")

    def test_c_registro_com_entrada_para_conta_e_projeto_retorna_none(self):
        self._escrever_registro([self._chave()])
        res, msgs, _ = self._rodar(nomes_sidebar=[], status_popover="vazio_confirmado")
        self.assertIsNone(res, "registro local com entrada NUNCA vira False")
        self.assertIn("registro local diz", msgs)

    def test_d_popover_instavel_retorna_none(self):
        # (d1) spinner visível → "carregando"
        res1, _, _ = self._rodar(status_popover="carregando")
        self.assertIsNone(res1)
        # (d2) uma única leitura sem .empty-state estável → "vazio" legado
        res2, msgs2, _ = self._rodar(status_popover="vazio")
        self.assertIsNone(res2)
        self.assertIn("vazio sozinho não prova ausência", msgs2)
        # (d3) lista lida sem o nome → "estavel"
        res3, _, _ = self._rodar(status_popover="estavel")
        self.assertIsNone(res3)

    def test_e_popover_acha_o_item_retorna_true_e_grava_registro(self):
        res, msgs, _ = self._rodar(status_popover="encontrado")
        self.assertIs(res, True)
        self.assertIn("fonte: popover", msgs)
        _dados = json.loads(self.reg_path.read_text(encoding="utf-8"))
        self.assertIn(self._chave(), _dados["personagens"])
        self.assertEqual(_dados["personagens"][self._chave()]["fonte"], "popover")

    def test_h_registro_de_outra_conta_ou_projeto_nao_bloqueia(self):
        self._escrever_registro([
            self._chave(conta=8),                                  # outra conta
            self._chave(projeto=UUID_OUTRO),                       # outro projeto Flow
            self._chave(nome="Outro"),                             # outro personagem
        ])
        res, _, _ = self._rodar(nomes_sidebar=[], status_popover="vazio_confirmado")
        self.assertIs(res, False, "entrada de outra conta/projeto não pode bloquear a criação")

    def test_i_registro_ausente_ou_corrompido_e_tratado_como_vazio(self):
        w = self._worker(MagicMock())
        with self._patch_registro(), self._patch_conta():
            # ausente
            self.assertEqual(w._ler_registro_contas_flow(), {})
            self.assertFalse(w._registro_diz_criado(NOME))
            # corrompido (JSON inválido) → {} sem exceção
            self.reg_path.write_text("{ isso não é json", encoding="utf-8")
            with mock.patch.object(pf, "pw_log") as m_log:
                self.assertEqual(w._ler_registro_contas_flow(), {})
                _msgs = " ".join(str(c.args[0]) for c in m_log.call_args_list if c.args)
            self.assertIn("tratado como vazio", _msgs)
            self.assertFalse(w._registro_diz_criado(NOME))
            # dict sem 'personagens' → estrutura normalizada
            self.reg_path.write_text(json.dumps({"versao": 2}), encoding="utf-8")
            self.assertEqual(w._ler_registro_contas_flow(), {"versao": 2, "personagens": {}})
        # e a verificação segue a política (vazio confirmado + registro inválido → False)
        self.reg_path.write_text("lixo", encoding="utf-8")
        res, _, _ = self._rodar(nomes_sidebar=[], status_popover="vazio_confirmado")
        self.assertIs(res, False)

    def test_j_escrita_do_registro_e_atomica_e_deixa_json_valido(self):
        w = self._worker(MagicMock())
        w.page = self._pagina_popover()
        with self._patch_registro(), self._patch_conta(), mock.patch.object(pf, "pw_log"):
            self.assertTrue(w._registrar_personagem_flow("Marcos", fonte="criacao"))
            self.assertTrue(w._registrar_personagem_flow("Ana", fonte="popover"))
        self.assertTrue(self.reg_path.exists())
        self.assertFalse(self.reg_path.with_name(self.reg_path.name + ".tmp").exists(),
                         "o arquivo temporário da escrita atômica não pode sobrar")
        _dados = json.loads(self.reg_path.read_text(encoding="utf-8"))
        self.assertIn(self._chave(nome="Marcos"), _dados["personagens"])
        self.assertIn(self._chave(nome="Ana"), _dados["personagens"])
        self.assertEqual(_dados["personagens"][self._chave(nome="Ana")]["fonte"], "popover")

    def test_sem_uuid_do_projeto_nao_grava_registro(self):
        """Sem projeto Flow identificado a chave não distingue sessões → não grava."""
        page = self._pagina_popover()
        page.url = "https://labs.google/fx/tools/flow"
        w = self._worker(page)
        with self._patch_registro(), self._patch_conta(), mock.patch.object(pf, "pw_log") as m_log:
            self.assertFalse(w._registrar_personagem_flow("Marcos", fonte="teste"))
            _msgs = " ".join(str(c.args[0]) for c in m_log.call_args_list if c.args)
        self.assertIn("não registrado", _msgs)
        self.assertFalse(self.reg_path.exists())


class TestVazioConfirmadoNoHelper(_BaseVerificacao):
    """TAREFA 4 — .empty-state em DUAS leituras consecutivas sem spinner → 'vazio_confirmado'."""

    class _DialogVazio:
        def __init__(self, loader=False):
            self.loader = loader

        def locator(self, sel):
            s = str(sel)
            loc = MagicMock()
            loc.first = loc
            if s == pf.SEL_POPOVER_PERSONAGENS_RAIZ:
                loc.count.return_value = 1
                loc.is_visible.return_value = True
            elif "empty-state" in s:
                loc.count.return_value = 1
                loc.is_visible.return_value = True
            elif self.loader and any(t in s for t in ("spinner", "progress", "aria-busy")):
                loc.count.return_value = 1
                loc.is_visible.return_value = True
            else:
                loc.count.return_value = 0
                loc.is_visible.return_value = False
            return loc

    def _worker(self):
        w = self._worker_base()
        w.page = MagicMock()          # wait_for_timeout instantâneo
        return w

    def _worker_base(self):
        w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
        w.port = 9222
        w.page = None
        return w

    def test_empty_state_duas_leituras_retorna_vazio_confirmado(self):
        w = self._worker()
        r = w._aguardar_lista_personagens_estavel(self._DialogVazio(), NOME, timeout_ms=2000)
        self.assertEqual(r, "vazio_confirmado")

    def test_spinner_impede_vazio_confirmado(self):
        w = self._worker()
        r = w._aguardar_lista_personagens_estavel(self._DialogVazio(loader=True), NOME, timeout_ms=400)
        self.assertNotEqual(r, "vazio_confirmado")
        self.assertIn(r, ("carregando", "vazio"))


class TestPreVooAvatarInconclusivoNaoCria(_BaseVerificacao):
    """TAREFA 5 — no pré-voo do avatar: True E None pulam o upload/criação; só False cria."""

    def _rodar_fila(self, status_verificacao):
        import services.scene_plan_service as sps
        pid = f"proj_av_{status_verificacao}"
        (self.tmp / pid).mkdir(parents=True, exist_ok=True)
        # sem lira_scene_plan.json → _handle_run_queue retorna logo após o pré-voo do avatar
        w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
        w.port = 9222
        w.worker_id = 0
        w.page = MagicMock()
        w.is_running_queue = False
        w.stop_requested = threading.Event()
        w.cena_ativa = None
        w.current_flow_mode = "imagem"
        w.current_project_id = ""
        w.projetos_dir = self.tmp
        w._iniciar_sessao_thread = MagicMock(return_value=(True, ""))
        w._ensure_project_open = MagicMock(return_value=True)
        w._upload_avatar_projeto = MagicMock()
        w._cenas_usam_personagem = MagicMock(return_value=False)
        w._verificar_personagem_na_biblioteca = MagicMock(return_value=status_verificacao)
        w._encerrar_sessao = MagicMock()
        w._extrair_metadados_sessao = MagicMock(return_value=("t@l.com", "Proj"))
        with mock.patch.object(pf, "PROJETOS_DIR", self.tmp), \
             mock.patch.object(sps, "PROJETOS_DIR", self.tmp), \
             mock.patch.object(pf, "DELAY_ENTRE_PROMPTS_SEG", 0), \
             mock.patch.object(pf.time, "sleep", lambda *a, **k: None), \
             mock.patch("services.character_service.validar_personagem_do_projeto",
                        return_value=({"nome": "Marcos", "referencia_flow": "@Marcos"}, None)), \
             mock.patch.object(pf, "pw_log") as m_log:
            w._handle_run_queue(pid, None, modo="imagem", bypass_rate_limit=True)
            msgs = " ".join(str(c.args[0]) for c in m_log.call_args_list if c.args)
        return w, msgs

    def test_g_true_pula_o_upload(self):
        w, _ = self._rodar_fila(True)
        w._upload_avatar_projeto.assert_not_called()

    def test_g_none_pula_o_upload_com_aviso(self):
        """(g) INCONCLUSIVO não pode criar (era o caminho da 4ª cópia do @Marcos)."""
        w, msgs = self._rodar_fila(None)
        w._upload_avatar_projeto.assert_not_called()
        self.assertIn("Verificação inconclusiva", msgs)
        self.assertIn("prevenidos para não duplicar", msgs)

    def test_g_false_permite_a_criacao_como_antes(self):
        w, _ = self._rodar_fila(False)
        w._upload_avatar_projeto.assert_called_once()


if __name__ == "__main__":
    unittest.main()


