# -*- coding: utf-8 -*-
"""
tests/test_pause_reason.py — REQ 2 / REQ 3
==========================================
Motivo CANÔNICO da parada da fila + exposição em /status e no SSE.

Defeito corrigido: a fila parava (por créditos, por fim de fila ou por ação do
operador) sem registrar POR QUÊ — o frontend não distinguia "créditos zerados"
de "bug" ou "usuário pausou".

Cobertura:
1. `PlaywrightCDPWorker.set_pause_reason` — carimba o motivo, loga
   "[HH:MM:SS.mmm] [PAUSE] Motivo: ..." e interrompe a fila via
   `stop_requested` (threading.Event, NUNCA bool).
2. `last_queue_pause_reason` — propriedade de compatibilidade (get/set) que
   registra o motivo SEM parar a fila (comportamento dos usos antigos).
3. `FlowQueueWorker.get_status()` — expõe `pause_reason`, `stop_requested`
   (bool, serializável em JSON) e `pause_reason_ts`.
4. `FlowQueueWorker.stop_worker()` — parada manual marca "manual".
5. Pontos de parada por crédito: `_tratar_indicador_limite` (vídeo),
   `_rotacionar_conta` (ninguém com crédito) e fim de fila por créditos zerados.
6. `_flow_sse_status` + payload/assinatura do SSE (api_v2).
7. `GET /api/v2/producao/<id>/status` devolve `pause_reason`/`stop_requested`.
8. Frontend (static/app.js) consome `prod.pause_reason` na transição do motivo.
"""
import io
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from config import PROJETOS_DIR

import services.playwright_flow as pf


def _worker_limpo():
    """Worker real sem passar pelo __init__ (mesmo padrão dos testes existentes)."""
    w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
    w.page = None
    w.is_running_queue = False
    w.cena_ativa = None
    w.current_flow_mode = "animacao"
    w.current_project_id = None
    w._fallback_video_para_imagem = False
    w.pause_reason = None
    w.pause_reason_ts = None
    w.stop_requested = threading.Event()
    w._check_is_active = lambda: False
    return w


class TestSetPauseReason(unittest.TestCase):
    """TAREFA 1/2/5 — estado do worker + log com timestamp."""

    def test_01_carimba_motivo_loga_e_para_a_fila(self):
        w = _worker_limpo()
        buf = io.StringIO()
        with redirect_stdout(buf):
            w.set_pause_reason("credito_esgotado_video")

        self.assertEqual(w.pause_reason, "credito_esgotado_video")
        self.assertTrue(w.pause_reason_ts, "o timestamp do motivo deve ser gravado")
        self.assertTrue(w.stop_requested.is_set(),
                        "stop_requested (threading.Event) deve ser acionado")
        self.assertIn("[PAUSE] Motivo: credito_esgotado_video", buf.getvalue(),
                      "o log [PAUSE] com timestamp deve ir para o CMD")

    def test_02_motivo_vazio_limpa_sem_parar_a_fila(self):
        w = _worker_limpo()
        w.set_pause_reason("credito_esgotado_imagem")
        self.assertTrue(w.stop_requested.is_set())
        # reset do início de fila: limpa o motivo sem mexer no Event
        w.stop_requested.clear()
        w.set_pause_reason("")
        self.assertIsNone(w.pause_reason)
        self.assertIsNone(w.pause_reason_ts)
        self.assertFalse(w.stop_requested.is_set())

    def test_03_parar_fila_false_apenas_registra(self):
        """Aviso informativo (pré-voo) NÃO pode interromper a fila."""
        w = _worker_limpo()
        w.set_pause_reason("aviso_pre_voo", parar_fila=False)
        self.assertEqual(w.pause_reason, "aviso_pre_voo")
        self.assertFalse(w.stop_requested.is_set())

    def test_04_propriedade_de_compatibilidade(self):
        w = _worker_limpo()
        w.last_queue_pause_reason = "PRE-VOO PAUSADO: personagem ausente"
        self.assertEqual(w.last_queue_pause_reason, "PRE-VOO PAUSADO: personagem ausente")
        self.assertEqual(w.pause_reason, "PRE-VOO PAUSADO: personagem ausente")
        self.assertFalse(w.stop_requested.is_set(),
                         "o setter antigo registra sem parar a fila")
        w.last_queue_pause_reason = ""
        self.assertEqual(w.last_queue_pause_reason, "")
        self.assertIsNone(w.pause_reason)

    def test_05_stop_worker_marca_manual(self):
        w = _worker_limpo()
        w.is_running_queue = True
        with patch.object(pf.FlowQueueWorker, "get_worker", return_value=w):
            ok = pf.FlowQueueWorker.stop_worker()
        self.assertTrue(ok)
        self.assertEqual(w.pause_reason, "manual")
        self.assertTrue(w.stop_requested.is_set())


class TestGetStatusExpoeMotivo(unittest.TestCase):
    """TAREFA 3 (base) — get_status() é a fonte do /status e do SSE."""

    def test_01_pause_reason_e_stop_requested_serializaveis(self):
        w = _worker_limpo()
        w.set_pause_reason("credito_esgotado_video")
        with patch.object(pf.FlowQueueWorker, "get_worker", return_value=w):
            st = pf.FlowQueueWorker.get_status()

        self.assertEqual(st["pause_reason"], "credito_esgotado_video")
        self.assertIsInstance(st["stop_requested"], bool)
        self.assertTrue(st["stop_requested"])
        self.assertTrue(st["pause_reason_ts"])
        # CRÍTICO: threading.Event não é serializável — a exposição usa bool().
        json.dumps(st)

    def test_02_sem_pausa_reason_vazio(self):
        w = _worker_limpo()
        with patch.object(pf.FlowQueueWorker, "get_worker", return_value=w):
            st = pf.FlowQueueWorker.get_status()
        self.assertEqual(st["pause_reason"], "")
        self.assertFalse(st["stop_requested"])


class TestPontosDeParadaPorCredito(unittest.TestCase):
    """TAREFA 2 — todos os pontos de crédito carimbam o motivo canônico."""

    @classmethod
    def setUpClass(cls):
        cls.src = Path("services/playwright_flow.py").read_text(encoding="utf-8")

    def test_01_video_esgotado(self):
        self.assertIn('self.set_pause_reason("credito_esgotado_video")', self.src)
        self.assertIn('return "credito_esgotado_video"', self.src)

    def test_02_todas_as_contas_esgotadas(self):
        self.assertIn('self.set_pause_reason("credito_esgotado_imagem")', self.src)

    def test_03_fim_de_fila_com_credito_zerado(self):
        # TAREFA 4 do turno seguinte deixou a chamada EXPLÍCITA (parar_fila=True).
        marcador = 'self.set_pause_reason("fim_fila_credito_zerado", parar_fila=True)'
        self.assertIn(marcador, self.src)
        idx = self.src.rindex(marcador)
        trecho = self.src[idx - 2000:idx]
        # A cena NÃO pode mais ser marcada como ERRO nesse bloco (REQ 2).
        self.assertNotIn('"status": scene_plan_svc.STATUS_ERRO,', trecho)
        self.assertIn("scene_plan_svc.STATUS_PENDENTE", trecho)

    def test_04_rotacionar_conta_runtime_para_a_fila(self):
        """Runtime real: sem nenhuma conta com crédito -> motivo + fila parada."""
        import os
        import tempfile

        tmp = tempfile.mkdtemp(prefix="pause_reason_")
        cwd_original = os.getcwd()
        real_cfg = Path("config/flow_accounts.json")
        real_antes = real_cfg.read_bytes() if real_cfg.exists() else None
        try:
            cfg_dir = Path(tmp) / "config"
            cfg_dir.mkdir(parents=True, exist_ok=True)
            (cfg_dir / "flow_accounts.json").write_text(json.dumps({
                "contas": [
                    {"id": 1, "nome": "unica", "email": "unica@x.com", "ativa": True,
                     "creditos_esgotados": False},
                    {"id": 2, "nome": "esgotada", "email": "e@x.com", "ativa": False,
                     "creditos_esgotados": True},
                ]
            }), encoding="utf-8")
            os.chdir(tmp)
            w = _worker_limpo()
            buf = io.StringIO()
            with redirect_stdout(buf):
                w._rotacionar_conta()
            self.assertEqual(w.pause_reason, "credito_esgotado_imagem")
            self.assertTrue(w.stop_requested.is_set())
        finally:
            os.chdir(cwd_original)
            shutil.rmtree(tmp, ignore_errors=True)
        # O config REAL nunca é tocado por este caminho de teste.
        if real_antes is not None:
            self.assertEqual(real_cfg.read_bytes(), real_antes)

    def test_05_guard_do_loop_registra_o_motivo(self):
        self.assertIn('getattr(self, "pause_reason", None) or "manual"', self.src)


class TestApiV2ExpoeMotivo(unittest.TestCase):
    """TAREFA 3/4 — /status e SSE devolvem o motivo."""

    @classmethod
    def setUpClass(cls):
        cls.src = Path("services/api_v2.py").read_text(encoding="utf-8")

    def setUp(self):
        self.pid = "_t_pause_reason_proj"
        self.pdir = PROJETOS_DIR / self.pid
        shutil.rmtree(self.pdir, ignore_errors=True)

    def tearDown(self):
        shutil.rmtree(self.pdir, ignore_errors=True)

    def test_01_flow_sse_status_inclui_motivo(self):
        from services import api_v2
        w = _worker_limpo()
        w.set_pause_reason("credito_esgotado_video")
        with patch.object(pf.FlowQueueWorker, "get_worker", return_value=w):
            flow = api_v2._flow_sse_status(self.pid)
        self.assertEqual(flow["pause_reason"], "credito_esgotado_video")
        self.assertTrue(flow["stop_requested"])
        json.dumps(flow)

    def test_02_assinatura_do_sse_inclui_motivo(self):
        """Sem os campos na assinatura, o evento não sairia quando só o motivo muda."""
        self.assertIn('"pause_reason": payload["pause_reason"],', self.src)
        self.assertIn('"stop_requested": payload["stop_requested"],', self.src)
        self.assertIn('payload["pause_reason"] = str(payload["flow"].get("pause_reason")', self.src)

    def test_03_endpoint_status_devolve_motivo(self):
        from app_web import app
        w = _worker_limpo()
        w.set_pause_reason("fim_fila_credito_zerado")
        client = app.test_client()
        with patch.object(pf.FlowQueueWorker, "get_worker", return_value=w):
            resp = client.get(f"/api/v2/producao/{self.pid}/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body.get("success"))
        self.assertEqual(body.get("pause_reason"), "fim_fila_credito_zerado")
        self.assertTrue(body.get("stop_requested"))
        self.assertEqual(body["flow"]["pause_reason"], "fim_fila_credito_zerado")
        self.assertTrue(body["flow"]["stop_requested"])

    def test_04_endpoint_status_sem_pausa_usa_nao_pausado(self):
        from app_web import app
        w = _worker_limpo()
        client = app.test_client()
        with patch.object(pf.FlowQueueWorker, "get_worker", return_value=w):
            resp = client.get(f"/api/v2/producao/{self.pid}/status")
        body = resp.get_json()
        self.assertEqual(body.get("pause_reason"), "nao_pausado")
        self.assertFalse(body.get("stop_requested"))


class TestNavegacaoAutomatica(unittest.TestCase):
    """REQ 4 — evento SSE 'navegarAba' quando a fila pausa por crédito."""

    PID = "_t_req4_nav_proj"

    def setUp(self):
        from services import api_v2
        self.api_v2 = api_v2
        with api_v2._NAV_LOCK:
            api_v2._NAV_PENDENTES.pop(self.PID, None)
            api_v2._NAV_ULTIMO_ENTREGUE.pop(self.PID, None)

    def tearDown(self):
        with self.api_v2._NAV_LOCK:
            self.api_v2._NAV_PENDENTES.pop(self.PID, None)
            self.api_v2._NAV_ULTIMO_ENTREGUE.pop(self.PID, None)

    def test_01_should_navigate_por_motivo(self):
        for motivo in ("credito_esgotado_video", "credito_esgotado_imagem",
                       "fim_fila_credito_zerado"):
            self.assertTrue(self.api_v2.should_navigate_on_pause(motivo), motivo)
            self.assertEqual(self.api_v2.aba_destino_para_pausa(motivo), "montagem")
        for motivo in ("manual", "", "nao_pausado", "credito_esgotado_desconhecido"):
            self.assertFalse(self.api_v2.should_navigate_on_pause(motivo), motivo)
            self.assertEqual(self.api_v2.aba_destino_para_pausa(motivo), "")

    def test_02_emit_enfileira_evento(self):
        ev = self.api_v2.emit_navigate_event(self.PID, "montagem", "credito_esgotado_video")
        self.assertIsNotNone(ev)
        self.assertEqual(ev["tipo"], "navegarAba")
        self.assertEqual(ev["aba"], "montagem")
        self.assertEqual(ev["motivo"], "credito_esgotado_video")
        self.assertTrue(ev["timestamp"])
        json.dumps(ev)
        with self.api_v2._NAV_LOCK:
            self.assertEqual(len(self.api_v2._NAV_PENDENTES.get(self.PID, [])), 1)

    def test_03_emit_ignora_motivo_nao_navegavel(self):
        self.assertIsNone(self.api_v2.emit_navigate_event(self.PID, "montagem", "manual"))
        with self.api_v2._NAV_LOCK:
            self.assertFalse(self.api_v2._NAV_PENDENTES.get(self.PID))

    def test_04_consumo_entrega_uma_vez(self):
        self.api_v2.emit_navigate_event(self.PID, "montagem", "credito_esgotado_video")
        primeiros = self.api_v2._consumir_eventos_navegacao(self.PID, "credito_esgotado_video")
        self.assertEqual(len(primeiros), 1)
        self.assertEqual(primeiros[0]["tipo"], "navegarAba")
        # 2ª chamada (mesmo tick seguinte): nada é reenviado.
        self.assertEqual(self.api_v2._consumir_eventos_navegacao(self.PID, "credito_esgotado_video"), [])

    def test_05_transicao_cobre_aba_aberta_depois_da_pausa(self):
        """Sem evento enfileirado, a própria transição dispara (SSE não tem replay)."""
        eventos = self.api_v2._consumir_eventos_navegacao(self.PID, "credito_esgotado_imagem")
        self.assertEqual(len(eventos), 1)
        self.assertEqual(eventos[0]["aba"], "montagem")
        self.assertEqual(eventos[0]["motivo"], "credito_esgotado_imagem")
        # Não repete no próximo tick
        self.assertEqual(self.api_v2._consumir_eventos_navegacao(self.PID, "credito_esgotado_imagem"), [])
        # Fila voltou a rodar -> libera para a próxima pausa
        self.assertEqual(self.api_v2._consumir_eventos_navegacao(self.PID, ""), [])
        self.assertEqual(len(self.api_v2._consumir_eventos_navegacao(self.PID, "credito_esgotado_imagem")), 1)

    def test_06_worker_notifica_a_camada_web(self):
        """Runtime: o worker (após set_pause_reason) enfileira a navegação no SSE."""
        w = _worker_limpo()
        w.current_project_id = self.PID
        w.set_pause_reason("credito_esgotado_video")
        buf = io.StringIO()
        with redirect_stdout(buf):
            w._notificar_navegacao_credito("credito_esgotado_video")
        eventos = self.api_v2._consumir_eventos_navegacao(self.PID, "credito_esgotado_video")
        self.assertEqual(len(eventos), 1)
        self.assertEqual(eventos[0]["aba"], "montagem")
        self.assertIn("should_navigate_on_pause('credito_esgotado_video') = True", buf.getvalue())
        self.assertIn("emit_navigate_event(montagem)", buf.getvalue())

    def test_07_worker_nao_notifica_motivo_manual(self):
        w = _worker_limpo()
        w.current_project_id = self.PID
        w.set_pause_reason("manual")
        with redirect_stdout(io.StringIO()):
            w._notificar_navegacao_credito("manual")
        with self.api_v2._NAV_LOCK:
            self.assertFalse(self.api_v2._NAV_PENDENTES.get(self.PID))

    def test_08_sse_emite_evento_navegaraba(self):
        """Runtime real do stream: o gerador SSE entrega o evento 'navegarAba'."""
        from app_web import app
        w = _worker_limpo()
        w.current_project_id = self.PID
        w.set_pause_reason("credito_esgotado_video")
        evento_nav = None
        with patch.object(pf.FlowQueueWorker, "get_worker", return_value=w):
            with app.test_request_context(f"/api/v2/producao/{self.PID}/stream"):
                gen = self.api_v2.producao_sse(self.PID).response
                for _ in range(3):
                    dados = json.loads(next(gen).split("data: ", 1)[1].strip())
                    if dados.get("tipo") == "navegarAba":
                        evento_nav = dados
                        break
        self.assertIsNotNone(evento_nav, "o SSE deve emitir o evento 'navegarAba'")
        self.assertEqual(evento_nav["aba"], "montagem")
        self.assertEqual(evento_nav["motivo"], "credito_esgotado_video")
        self.assertTrue(evento_nav["timestamp"])

    def test_09_worker_chama_navegacao_nos_3_motivos(self):
        src = Path("services/playwright_flow.py").read_text(encoding="utf-8")
        for motivo in ("credito_esgotado_video", "credito_esgotado_imagem",
                       "fim_fila_credito_zerado"):
            self.assertIn(f'self._notificar_navegacao_credito("{motivo}")', src)

    def test_10_frontend_trata_navegaraba(self):
        js = Path("static/app.js").read_text(encoding="utf-8")
        self.assertIn('data.tipo === "navegarAba"', js)
        self.assertIn("irParaAbaS2(abaNav)", js)
        self.assertIn("S2_ULTIMA_NAVEGACAO", js)

    def test_11_api_expoe_os_tres_simbolos(self):
        src = Path("services/api_v2.py").read_text(encoding="utf-8")
        self.assertIn("def should_navigate_on_pause(", src)
        self.assertIn("def emit_navigate_event(", src)
        self.assertIn('_consumir_eventos_navegacao(proyecto_id, payload["pause_reason"])', src)


class TestFrontendConsomeMotivo(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.js = Path("static/app.js").read_text(encoding="utf-8")

    def test_01_le_pause_reason_do_status(self):
        self.assertIn("prod.pause_reason", self.js)
        self.assertIn("S2_ULTIMO_PAUSE_REASON", self.js)

    def test_02_motivos_canonicos_tem_legenda(self):
        for motivo in ("credito_esgotado_video", "credito_esgotado_imagem",
                       "fim_fila_credito_zerado", "manual"):
            self.assertIn(motivo, self.js, f"falta legenda para {motivo}")

    def test_03_aviso_usa_showtoast(self):
        # REQ 5: o aviso principal virou o cartão showPauseNotification; showToast
        # permanece como FALLBACK quando a função não está disponível.
        trecho = self.js.split("const motivoPausa")[1][:1800]
        self.assertIn("showPauseNotification", trecho)
        self.assertIn("showToast", trecho)


class TestNotificacaoVisualPausa(unittest.TestCase):
    """REQ 5 — toast/banner de pausa com título, corpo e ação."""

    @classmethod
    def setUpClass(cls):
        cls.js = Path("static/app.js").read_text(encoding="utf-8")
        cls.css = Path("static/style.css").read_text(encoding="utf-8")

    def test_01_estrutura_da_funcao(self):
        self.assertIn("function showPauseNotification(", self.js)
        self.assertIn("window.showPauseNotification = showPauseNotification;", self.js)
        self.assertIn("const PAUSA_MENSAGENS = {", self.js)

    def test_02_mensagens_para_os_4_motivos(self):
        bloco = self.js.split("const PAUSA_MENSAGENS = {", 1)[1].split("};", 1)[0]
        for motivo in ("credito_esgotado_video", "credito_esgotado_imagem",
                       "fim_fila_credito_zerado", "manual"):
            self.assertIn(motivo, bloco, f"falta mensagem para {motivo}")
        for chave in ("titulo:", "corpo:", "acao:", "destino:", "cor:", "auto:"):
            self.assertIn(chave, bloco)

    def test_03_recursos_do_toast(self):
        bloco = self.js.split("function showPauseNotification(", 1)[1]
        bloco = bloco.split("window.showPauseNotification = showPauseNotification;")[0]
        self.assertIn("clearTimeout(timer)", bloco)          # auto-fechamento
        self.assertIn("toast-pausa-fechar", bloco)            # botão X
        self.assertIn("toast-pausa-btn", bloco)               # botão de ação
        self.assertIn("irParaAbaS2(cfg.destino)", bloco)      # ação navega
        self.assertIn("S2_ULTIMO_TOAST_PAUSA", bloco)         # dedup
        # Segurança: nada de innerHTML (conteúdo entra por textContent).
        self.assertNotIn("innerHTML", bloco)

    def test_04_ligada_na_transicao_do_pause_reason(self):
        trecho = self.js.split("const motivoPausa")[1][:1400]
        self.assertIn("showPauseNotification(motivoPausa", trecho)
        self.assertIn("_contarBrollPendenteS2(prod)", trecho)
        self.assertIn('S2_ULTIMO_TOAST_PAUSA = ""', self.js)

    def test_05_css_do_toast_pausa(self):
        for classe in (".toast-pausa {", ".toast-pausa-titulo", ".toast-pausa-corpo",
                       ".toast-pausa-acoes", ".toast-pausa-btn", ".toast-pausa-fechar",
                       ".toast-pausa-warning", ".toast-pausa-success", ".toast-pausa-info"):
            self.assertIn(classe, self.css, f"falta estilo {classe}")
        # #toast-container é pointer-events:none -> o cartão precisa reativar.
        self.assertIn("pointer-events: auto", self.css.split(".toast-pausa {")[1][:200])

    def _bloco_funcao(self) -> str:
        ini = self.js.index("const PAUSA_MENSAGENS = {")
        fim = self.js.index("window.showPauseNotification = showPauseNotification;")
        return self.js[ini:fim + len("window.showPauseNotification = showPauseNotification;")]

    def test_06_runtime_node_dom_minimo(self):
        """Executa a função REAL (extraída do app.js) em Node com um DOM mínimo."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node não disponível")
        js = """
const window = globalThis;
""" + self._bloco_funcao() + """
// ---- DOM mínimo ----
const _byId = {};
function _mkEl(tag) {
  return {
    tagName: tag, id: "", className: "", textContent: "", children: [], listeners: {},
    parentNode: null,
    classList: { _c: new Set(),
      add(c) { this._c.add(c); }, remove(c) { this._c.delete(c); },
      contains(c) { return this._c.has(c); } },
    setAttribute(k, v) { this[k] = v; },
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; },
    addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); },
    removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); }
  };
}
const document = {
  _byId,
  getElementById(id) { return _byId[id] || null; },
  createElement(tag) { return _mkEl(tag); },
  body: { appendChild(c) { if (c.id) _byId[c.id] = c; } }
};
globalThis.document = document;
globalThis.requestAnimationFrame = (fn) => fn();
const _find = (el, cls) => el.children.find((c) => c.className === cls);

// ---- Asserções ----
let destino = "";
globalThis.irParaAbaS2 = (aba) => { destino = aba; };

const r1 = showPauseNotification("credito_esgotado_video", { pendentesBroll: 3 });
if (!r1) throw new Error("nao criou o toast");
if (!r1.className.includes("toast-pausa-warning")) throw new Error("cor: " + r1.className);
if (!r1.classList.contains("show")) throw new Error("classe .show nao aplicada");
const t1 = _find(r1, "toast-pausa-titulo"), c1 = _find(r1, "toast-pausa-corpo");
if (!t1 || !t1.textContent.includes("Cr\u00e9ditos de V\u00eddeo")) throw new Error("titulo: " + (t1 && t1.textContent));
if (!c1 || !c1.textContent.includes("3 cena(s) B-roll")) throw new Error("contagem broll: " + (c1 && c1.textContent));
const acoes1 = _find(r1, "toast-pausa-acoes");
const btn1 = acoes1.children[0], x1 = acoes1.children[1];
if (!btn1.textContent.includes("Montagem")) throw new Error("acao: " + btn1.textContent);
if (!x1.textContent.includes("\u2715")) throw new Error("botao fechar ausente");
btn1.listeners.click[0]();
if (destino !== "montagem") throw new Error("acao nao navegou: " + destino);

if (showPauseNotification("credito_esgotado_video", {}) !== null) throw new Error("dedup falhou");
if (showPauseNotification("nao_pausado") !== null) throw new Error("motivo invalido deveria ser ignorado");
if (showPauseNotification("") !== null) throw new Error("motivo vazio deveria ser ignorado");

const r2 = showPauseNotification("manual", {});
if (!r2 || !r2.className.includes("toast-pausa-info")) throw new Error("cor manual");
_find(r2, "toast-pausa-acoes").children[0].listeners.click[0]();
if (destino !== "producao") throw new Error("manual nao foi para producao: " + destino);

const r3 = showPauseNotification("fim_fila_credito_zerado", { pendentesBroll: 2 });
if (!r3 || !r3.className.includes("toast-pausa-success")) throw new Error("cor fim de fila");
if (!_find(r3, "toast-pausa-corpo").textContent.includes("2 cena(s) B-roll")) throw new Error("broll fim de fila");
console.log("REQ5_OK");
"""
        tmp = Path(tempfile.mkdtemp(prefix="req5_toast_"))
        try:
            arq = tmp / "req5_toast.js"
            arq.write_text(js, encoding="utf-8")
            r = subprocess.run([node, str(arq)], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(r.returncode, 0, f"node falhou:\n{r.stdout}\n{r.stderr}")
        self.assertIn("REQ5_OK", r.stdout or "")


if __name__ == "__main__":
    unittest.main()
