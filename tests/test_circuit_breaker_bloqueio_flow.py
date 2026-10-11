# -*- coding: utf-8 -*-
"""
tests/test_circuit_breaker_bloqueio_flow.py — Bug 3 (ANTIGRAVITY)
================================================================
Circuit breaker de "atividade incomum" no `_handle_run_queue`:

* 3 falhas CONSECUTIVAS por bloqueio do Flow ("notamos uma atividade incomum")
  pausam a fila automaticamente com `pause_reason = PAUSADA_BLOQUEIO_FLOW`
  (set_pause_reason -> stop_requested.set() -> finally grava fila["pausada"]=True);
* só um SUCESSO zera o contador;
* falhas de outro tipo (imagem no lugar de vídeo etc.) são NEUTRAS
  (não incrementam e não zeram);
* as cenas restantes permanecem PENDENTE (nenhuma é tocada após a pausa).

Validação: worker stub + projeto temporário (mesmo padrão do
TestBatchQueueFlowSync). "Validado via simulação/worker stub" — a execução real
contra o Flow só ocorre quando a fila real bater 3 bloqueios seguidos.
"""
import io
import json
import shutil
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

import services.character_service as chars
import services.playwright_flow as pf
import services.scene_plan_service as sps

MSG_ABNORMAL = ("Google Flow falhou ao gerar mídia: Notamos uma atividade incomum "
                "na sua conta. Tente novamente mais tarde.")
MSG_IMAGEM = "esperado vídeo do Flow, mas retornou imagem"


def _criar_projeto(tmp: Path, pid: str, n_cenas: int) -> Path:
    pdir = tmp / pid
    (pdir / "cenas").mkdir(parents=True, exist_ok=True)
    cenas = [
        {"id": i, "scene_index": i, "status": "PENDENTE", "arquivo_midia": "",
         "visual_prompt": f"Cena {i}"}
        for i in range(1, n_cenas + 1)
    ]
    (pdir / "lira_scene_plan.json").write_text(
        json.dumps({"cenas": cenas}), encoding="utf-8")
    return pdir


def _plano(pdir: Path) -> dict:
    dados = json.loads((pdir / "lira_scene_plan.json").read_text(encoding="utf-8"))
    return {int(c["id"]): c for c in dados.get("cenas", [])}


def _criar_worker(tmp_dir: Path):
    w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
    w.port = 9222
    w.page = MagicMock()
    w.is_running_queue = False
    w.stop_requested = threading.Event()
    w.cena_ativa = None
    w.current_flow_mode = "imagem"
    w.current_project_id = ""
    w.current_model = "Nano Banana 2"
    w.current_download_quality = "1K"
    w.current_download_quality_video = "1080p"
    w.last_queue_pause_reason = ""
    w.pause_reason = None
    w.pause_reason_ts = None
    w.scene_durations = []
    w.queue_start_time = None
    w.current_delay_info = None
    w.projetos_dir = tmp_dir
    w._iniciar_sessao_thread = MagicMock(return_value=(True, ""))
    w._ensure_project_open = MagicMock(return_value=True)
    w._upload_avatar_projeto = MagicMock()
    w._cenas_usam_personagem = MagicMock(return_value=False)
    w._verificar_personagem_na_biblioteca = MagicMock(return_value=None)
    w._set_output_mode = MagicMock(return_value=True)
    w._garantir_aba_flow_aberta = MagicMock(return_value=True)
    w._encerrar_sessao = MagicMock()
    w._extrair_metadados_sessao = MagicMock(return_value=("teste@lira.com", "Projeto CB"))
    return w


def _rodar_fila(tmp_dir: Path, pid: str, respostas: dict):
    """Executa `_handle_run_queue` com respostas por cena e devolve (worker, chamadas, stdout)."""
    chamadas = []

    def _processar(projeto_id, cena, is_anim=False, index=1, total_cenas=1):
        cid = int(cena["id"])
        chamadas.append(cid)
        return respostas.get(cid, (True, "OK"))

    w = _criar_worker(tmp_dir)
    w._processar_cena_individual = _processar
    buf = io.StringIO()
    with mock.patch.object(pf, "PROJETOS_DIR", tmp_dir), \
         mock.patch.object(sps, "PROJETOS_DIR", tmp_dir), \
         mock.patch.object(chars, "PROJETOS_DIR", tmp_dir), \
         mock.patch.object(pf.time, "sleep", lambda *a, **k: None), \
         mock.patch.object(pf, "DELAY_ENTRE_PROMPTS_SEG", 0), \
         redirect_stdout(buf):
        w._handle_run_queue(pid, None, modo="imagem", bypass_rate_limit=True)
    return w, chamadas, buf.getvalue()


class TestCircuitBreaker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cb_bloq_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_00_constantes_canonicas(self):
        self.assertEqual(pf.LIMITE_FALHAS_CONSECUTIVAS_BLOQUEIO_FLOW, 3)
        self.assertEqual(pf.MOTIVO_PAUSA_BLOQUEIO_FLOW, "PAUSADA_BLOQUEIO_FLOW")

    def test_a_tres_falhas_consecutivas_pausam_a_fila(self):
        """(a) 3 falhas 'atividade incomum' seguidas -> pausa canônica + fila pausada + 4ª cena PENDENTE."""
        pid = "cb_pausa"
        pdir = _criar_projeto(self.tmp, pid, 4)
        respostas = {
            1: (False, MSG_ABNORMAL),
            2: (False, MSG_ABNORMAL),
            3: (False, MSG_ABNORMAL),
            4: (True, "OK"),
        }
        w, chamadas, out = _rodar_fila(self.tmp, pid, respostas)

        self.assertEqual(w.pause_reason, "PAUSADA_BLOQUEIO_FLOW")
        self.assertTrue(w.stop_requested.is_set(), "stop_requested deve interromper o loop")
        # `_processar_cena_individual` roda 2x por cena falha (tentativas); a ORDEM
        # única prova que a 4ª cena nunca foi tocada após a pausa.
        self.assertEqual(list(dict.fromkeys(chamadas)), [1, 2, 3],
                         "nenhuma cena após a 3ª pode ser processada")
        self.assertNotIn(4, chamadas)
        self.assertIn("[CIRCUIT_BREAKER]", out)
        self.assertIn("3 falhas consecutivas", out)
        self.assertIn("[PAUSE] Motivo: PAUSADA_BLOQUEIO_FLOW", out)

        fila = pf._ler_fila(pid, self.tmp)
        self.assertTrue(fila.get("pausada"), "finally deve gravar fila['pausada']=True")
        self.assertFalse(fila.get("ativa"))

        plano = _plano(pdir)
        for cid in (1, 2, 3):
            self.assertEqual(plano[cid]["status"], sps.STATUS_ERRO)
            self.assertEqual(plano[cid]["erro_msg"], MSG_ABNORMAL)
        # (d) cena restante intocada
        self.assertEqual(plano[4]["status"], "PENDENTE")
        self.assertEqual(plano[4].get("erro_msg", ""), "")

    def test_b_duas_falhas_sucesso_duas_falhas_nao_pausam(self):
        """(b) 2 falhas + 1 sucesso + 2 falhas -> NÃO pausa (sucesso zera o contador)."""
        pid = "cb_intercalado"
        pdir = _criar_projeto(self.tmp, pid, 5)
        respostas = {
            1: (False, MSG_ABNORMAL),
            2: (False, MSG_ABNORMAL),
            3: (True, "OK"),
            4: (False, MSG_ABNORMAL),
            5: (False, MSG_ABNORMAL),
        }
        w, chamadas, _ = _rodar_fila(self.tmp, pid, respostas)

        self.assertFalse(w.stop_requested.is_set(), "NÃO deve pausar")
        self.assertIn(w.pause_reason, (None, ""))
        self.assertEqual(list(dict.fromkeys(chamadas)), [1, 2, 3, 4, 5])
        plano = _plano(pdir)
        for cid in (1, 2, 4, 5):
            self.assertEqual(plano[cid]["status"], sps.STATUS_ERRO)

    def test_c_falhas_de_imagem_intercaladas_sao_neutras(self):
        """(c) 'retornou imagem' não incrementa nem zera: A,V,A,V,A -> pausa na 5ª.

        Se 'retornou imagem' incrementasse, a pausa viria na 3ª cena;
        se zerasse, NÃO haveria pausa. O resultado prova o comportamento neutro.
        """
        pid = "cb_neutras"
        _criar_projeto(self.tmp, pid, 5)
        respostas = {
            1: (False, MSG_ABNORMAL),
            2: (False, MSG_IMAGEM),
            3: (False, MSG_ABNORMAL),
            4: (False, MSG_IMAGEM),
            5: (False, MSG_ABNORMAL),
        }
        w, chamadas, out = _rodar_fila(self.tmp, pid, respostas)

        self.assertEqual(w.pause_reason, "PAUSADA_BLOQUEIO_FLOW")
        self.assertTrue(w.stop_requested.is_set())
        self.assertEqual(list(dict.fromkeys(chamadas)), [1, 2, 3, 4, 5])
        self.assertIn("3 falhas consecutivas", out)

    def test_c2_imagem_neutra_e_sucesso_zera(self):
        """(c2) A,V,A,OK,A,A -> NÃO pausa (V neutra + sucesso zera)."""
        pid = "cb_neutras_sucesso"
        _criar_projeto(self.tmp, pid, 5)
        respostas = {
            1: (False, MSG_ABNORMAL),
            2: (False, MSG_IMAGEM),
            3: (True, "OK"),
            4: (False, MSG_ABNORMAL),
            5: (False, MSG_ABNORMAL),
        }
        w, chamadas, _ = _rodar_fila(self.tmp, pid, respostas)

        self.assertFalse(w.stop_requested.is_set(), "V não incrementa (senão pausaria na 3ª)")
        self.assertIn(w.pause_reason, (None, ""))
        self.assertEqual(list(dict.fromkeys(chamadas)), [1, 2, 3, 4, 5])


class TestFrontendBannerPausaBloqueio(unittest.TestCase):
    """TAREFA 3 — banner amarelo no HUD para o motivo PAUSADA_BLOQUEIO_FLOW."""

    @classmethod
    def setUpClass(cls):
        cls.js = Path("static/app.js").read_text(encoding="utf-8")

    def test_01_motivo_no_mapa_e_na_legenda(self):
        bloco = self.js.split("const PAUSA_MENSAGENS = {", 1)[1].split("};", 1)[0]
        self.assertIn("PAUSADA_BLOQUEIO_FLOW", bloco)
        self.assertIn("atividade incomum", bloco)
        self.assertIn("Aguarde alguns minutos antes de clicar em Retomar", bloco)
        self.assertIn('cor: "warning"', bloco)
        trecho = self.js.split("const motivoPausa", 1)[1][:1400]
        self.assertIn("PAUSADA_BLOQUEIO_FLOW", trecho)

    def test_02_node_extrai_mapa_e_valida_a_entrada(self):
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node não disponível")
        app_js_abs = str(Path("static/app.js").resolve()).replace("\\", "/")
        js = (
            'const fs = require("fs");\n'
            f'const src = fs.readFileSync("{app_js_abs}", "utf-8");\n'
            'const i = src.indexOf("const PAUSA_MENSAGENS = {");\n'
            'const j = src.indexOf("};", i);\n'
            'const bloco = src.slice(i + "const PAUSA_MENSAGENS = ".length, j + 1);\n'
            'const map = eval("(" + bloco + ")");\n'
            'const cfg = map.PAUSADA_BLOQUEIO_FLOW;\n'
            'if (!cfg) throw new Error("entrada ausente");\n'
            'if (cfg.cor !== "warning") throw new Error("cor: " + cfg.cor);\n'
            'if (!cfg.titulo.includes("Produção pausada automaticamente")) throw new Error("titulo: " + cfg.titulo);\n'
            'if (!cfg.corpo.includes("atividade incomum")) throw new Error("corpo: " + cfg.corpo);\n'
            'if (!cfg.corpo.includes("Aguarde alguns minutos antes de clicar em Retomar")) throw new Error("corpo2");\n'
            'console.log("CB_FRONT_OK");\n'
        )
        tmp = Path(tempfile.mkdtemp(prefix="cb_front_"))
        try:
            arq = tmp / "cb_front.js"
            arq.write_text(js, encoding="utf-8")
            r = subprocess.run([node, str(arq)], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(r.returncode, 0, f"node falhou:\n{r.stdout}\n{r.stderr}")
        self.assertIn("CB_FRONT_OK", r.stdout or "")


if __name__ == "__main__":
    unittest.main()

