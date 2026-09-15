# -*- coding: utf-8 -*-
"""
tests/test_itens_7_8_10.py
==========================
Auditoria (itens 7, 8 e 10) — Lira Studio.

ITEM 7  GET /api/v2/cena_media: 404 sem motivo → agora devolve `reason`.
ITEM 8  Rota de retomada: alias /retomar_projeto sem a espera de 10s
        (`bypass_rate_limit`, default False = comportamento original intacto).
ITEM 10 app.js: 404 com reason="arquivo_deletado" para o polling e mostra
        "Arquivo deletado — aguardando reconstrução".
"""
import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import services.playwright_flow as pf
from config import PROJETOS_DIR

API_SRC = Path("services/api_v2.py").read_text(encoding="utf-8")
APP_JS = Path("static/app.js").read_text(encoding="utf-8")
PW_SRC = Path("services/playwright_flow.py").read_text(encoding="utf-8")


class TestItem7ReasonNo404(unittest.TestCase):
    """Runtime do endpoint real (projeto inexistente → sem mídia → 404)."""

    def test_01_404_traz_reason_arquivo_deletado(self):
        from app_web import app
        client = app.test_client()
        resp = client.get("/api/v2/cena_media/_t_item7_sem_midia/7")
        self.assertEqual(resp.status_code, 404)
        body = resp.get_json() or {}
        self.assertFalse(body.get("success"))
        self.assertEqual(body.get("reason"), "arquivo_deletado")
        self.assertEqual(body.get("cena_id"), 7)
        self.assertEqual(body.get("scene_id"), 7)

    def test_02_reason_presente_no_source(self):
        self.assertIn('"reason": "arquivo_deletado"', API_SRC)


class TestItem8RetomadaSemEspera(unittest.TestCase):

    def test_01_rota_alias_registrada(self):
        from app_web import app
        regras = {str(r.rule) for r in app.url_map.iter_rules()}
        self.assertIn("/api/v2/producao/<projeto_id>/retomar_projeto", regras)
        # a rota original continua existindo
        self.assertIn("/api/v2/producao/<projeto_id>/retomar", regras)

    def test_02_alias_usa_bypass_true(self):
        ini = API_SRC.index("def v2_producao_retomar_projeto(")
        trecho = API_SRC[ini:ini + 400]
        self.assertIn("v2_producao_iniciar_fila(projeto_id, bypass_rate_limit=True)", trecho)

    def test_03_retomar_original_nao_muda(self):
        ini = API_SRC.index("def v2_producao_retomar_fila(")
        trecho = API_SRC[ini:ini + 200]
        self.assertIn("return v2_producao_iniciar_fila(projeto_id)", trecho)
        self.assertNotIn("bypass_rate_limit=True", trecho)

    def test_04_start_worker_repassa_kwarg(self):
        w = MagicMock()
        w.is_running_queue = False
        with patch.object(pf.FlowQueueWorker, "get_worker", return_value=w), \
             patch.object(pf.threading, "Thread") as m_thread:
            pf.FlowQueueWorker.start_worker("t_item8", bypass_rate_limit=True)
        self.assertEqual(m_thread.call_args.kwargs.get("kwargs"), {"bypass_rate_limit": True})

    def test_05_start_worker_default_sem_kwargs(self):
        """Default False: chamada IDÊNTICA à de antes (3 posicionais, sem kwargs)."""
        w = MagicMock()
        w.is_running_queue = False
        with patch.object(pf.FlowQueueWorker, "get_worker", return_value=w), \
             patch.object(pf.threading, "Thread") as m_thread:
            pf.FlowQueueWorker.start_worker("t_item8")
        self.assertEqual(m_thread.call_args.kwargs.get("kwargs"), {})
        self.assertEqual(m_thread.call_args.kwargs.get("args"), ("t_item8", None, "imagem"))

    def test_06_handler_aceita_e_pula_a_espera(self):
        ini = PW_SRC.index("    def _handle_run_queue(")
        self.assertIn("bypass_rate_limit: bool = False", PW_SRC[ini:ini + 200])
        trecho = PW_SRC[ini:PW_SRC.index("\n    def ", ini + 10)]
        self.assertIn("if bypass_rate_limit:", trecho)
        self.assertIn("time.sleep(min(5, 10 - (time.time() - _t0_rate)))", trecho)

    def test_07_bypass_presente_no_worker(self):
        self.assertIn("bypass_rate_limit", PW_SRC)
        self.assertIn('_kwargs = {"bypass_rate_limit": True} if bypass_rate_limit else {}', PW_SRC)


class TestItem10FrontendArquivoDeletado(unittest.TestCase):

    def test_01_reason_presente_no_app_js(self):
        self.assertIn("arquivo_deletado", APP_JS)

    def test_02_helper_le_o_reason(self):
        self.assertIn("async function _motivo404CenaMedia(", APP_JS)
        trecho = APP_JS[APP_JS.index("async function _motivo404CenaMedia("):]
        self.assertIn('data.reason', trecho)

    def test_03_modal_trata_o_404_com_reason(self):
        trecho = APP_JS[APP_JS.index("async function definirBadgeDisponibilidade()"):]
        trecho = trecho[:2000]
        self.assertIn("resp.status === 404", trecho)
        self.assertIn('motivo === "arquivo_deletado"', trecho)
        self.assertIn("Arquivo deletado — aguardando reconstrução", trecho)
        self.assertIn("midiaDeletada = true", trecho)

    def test_04_polling_para_e_avisa(self):
        trecho = APP_JS[APP_JS.index("function iniciarThumbCena("):]
        trecho = trecho[:1600]
        self.assertIn("res.status === 404 && declarouMidia", trecho)
        self.assertIn("clearInterval(timer)", trecho)
        self.assertIn("delete S.cenaThumbs[sceneId]", trecho)
        self.assertIn("_marcarMidiaDeletadaCena(imgEl, sceneId)", trecho)

    def test_05_chamador_informa_midia_declarada(self):
        self.assertIn('iniciarThumbCena(cena.idx, card.querySelector(".scene-img"), Boolean(cena.arquivo_midia))',
                      APP_JS)

    def test_06_node_check_sintaxe(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node não disponível")
        import subprocess
        r = subprocess.run([node, "--check", "static/app.js"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
