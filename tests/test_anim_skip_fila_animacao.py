# -*- coding: utf-8 -*-
"""
tests/test_anim_skip_fila_animacao.py — ANTIGRAVITY (fila de animação NUNCA gera imagem)
=======================================================================================
Bug: cenas `static_macro` (animar=False, tipo image) foram enfileiradas em modo
"animacao" e o worker, com `video_mode=False`, trocou para Nano Banana 2 e gerou
IMAGEM — gastando créditos e (com o avatar no polling) sobrescrevendo os PNGs
002/004. Correção: em `_processar_cena_individual`, `is_anim=True` + `video_mode`
False ⇒ cena PULADA antes de qualquer interação com o Flow (sem prompt, sem troca
de modelo, sem tocar em mídia/status), retornando `(True, ANIM_SKIP_RESULTADO)`;
o chamador (`_handle_run_queue`) remove a cena da fila como "ignorada" — sem
STATUS_ERRO, sem retentativa e sem alimentar o circuit-breaker.

Exceção deliberada (PARTE 5, comportamento existente e testado): com
`_fallback_video_para_imagem` (crédito de VÍDEO esgotado) a geração de imagem
continua — só a INELEGIBILIDADE DA CENA pula. Modo "imagem" (is_anim=False) não
muda em nada (REGRA 6: sempre gera imagem).

Validação: worker stub + projeto temporário ("validado via simulação/worker
stub"). A validação real (regenerar 002/004 contra o Flow) é etapa posterior.
"""
import hashlib
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

PNG_FALSO = b"\x89PNG\r\n\x1a\n" + b"conteudo-original-da-cena" * 8


def _criar_projeto(tmp: Path, pid: str, cenas) -> Path:
    pdir = tmp / pid
    (pdir / "cenas").mkdir(parents=True, exist_ok=True)
    (pdir / "lira_scene_plan.json").write_text(
        json.dumps({"cenas": cenas}, ensure_ascii=False), encoding="utf-8")
    return pdir


def _ler_plano(pdir: Path) -> dict:
    dados = json.loads((pdir / "lira_scene_plan.json").read_text(encoding="utf-8"))
    return {int(c["id"]): c for c in dados.get("cenas", [])}


def _criar_worker(tmp_dir: Path, **extra):
    w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
    w.port = 9222
    w.page = MagicMock()
    w.page.wait_for_timeout = MagicMock()   # guardas de modal não devem esperar de verdade
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
    w._fallback_video_para_imagem = False
    w.get_exact_balance = MagicMock(return_value=None)   # sem leitura de créditos
    w._iniciar_sessao_thread = MagicMock(return_value=(True, ""))
    w._ensure_project_open = MagicMock(return_value=False)  # Flow "fechado": para no 1º passo
    w._upload_avatar_projeto = MagicMock()
    w._cenas_usam_personagem = MagicMock(return_value=False)
    w._verificar_personagem_na_biblioteca = MagicMock(return_value=None)
    w._set_output_mode = MagicMock(return_value=True)
    w._garantir_aba_flow_aberta = MagicMock(return_value=True)
    w._encerrar_sessao = MagicMock()
    w._extrair_metadados_sessao = MagicMock(return_value=("teste@lira.com", "Projeto Anim"))
    for k, v in extra.items():
        setattr(w, k, v)
    return w


def _processar(w, pid: str, cena: dict, is_anim: bool):
    """Chama o worker REAL (não stub) e devolve (resultado, stdout).

    `pf.time.sleep` é neutralizado: o worker real dorme em trechos de guarda de modal
    (irrelevantes aqui) e sem isso estes testes levariam ~10s cada.
    """
    buf = io.StringIO()
    with mock.patch.object(pf.time, "sleep", lambda *a, **k: None), \
         redirect_stdout(buf):
        res = w._processar_cena_individual(pid, cena, is_anim=is_anim, index=1, total_cenas=1)
    return res, buf.getvalue()


def _rodar_fila(tmp_dir: Path, pid: str, respostas: dict, modo: str = "animacao",
                target_ids=None):
    """Executa `_handle_run_queue` com respostas por cena e devolve (worker, chamadas, stdout).

    `target_ids` reproduz o caminho REAL do botão "Mídia de Movimento" (API manda
    `scene_ids`): o pré-filtro de `_handle_run_queue` (L6534-6543) tem escape
    `target_ids is not None and cid in target_ids` — por isso as 206 cenas
    static_macro entraram na fila de animação. O gate do worker (TAREFA 2) é o que
    impede a geração nesse caminho.
    """
    chamadas = []

    def _processar_stub(projeto_id, cena, is_anim=False, index=1, total_cenas=1):
        cid = int(cena["id"])
        chamadas.append(cid)
        return respostas.get(cid, (True, "OK"))

    w = _criar_worker(tmp_dir, _ensure_project_open=MagicMock(return_value=True))
    w._processar_cena_individual = _processar_stub
    buf = io.StringIO()
    with mock.patch.object(pf, "PROJETOS_DIR", tmp_dir), \
         mock.patch.object(sps, "PROJETOS_DIR", tmp_dir), \
         mock.patch.object(chars, "PROJETOS_DIR", tmp_dir), \
         mock.patch.object(pf.time, "sleep", lambda *a, **k: None), \
         mock.patch.object(pf, "DELAY_ENTRE_PROMPTS_SEG", 0), \
         redirect_stdout(buf):
        w._handle_run_queue(pid, target_ids, modo=modo, bypass_rate_limit=True)
    return w, chamadas, buf.getvalue()


class AnimSkipBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="anim_skip_"))
        cls._worker_antigo = getattr(pf, "_worker_instance", None)

    @classmethod
    def tearDownClass(cls):
        pf._worker_instance = cls._worker_antigo
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _preparar(self, pid: str, cena: dict, png: bool = True):
        pdir = _criar_projeto(self.tmp, pid, [cena])
        png_path = pdir / "cenas" / f"{int(cena['id']):03d}.png"
        if png:
            png_path.write_bytes(PNG_FALSO)
        sha_antes = hashlib.sha256(png_path.read_bytes()).hexdigest() if png else None
        return pdir, png_path, sha_antes


class TestWorkerAnimSkip(AnimSkipBase):
    def test_00_constante_do_marcador(self):
        self.assertEqual(pf.ANIM_SKIP_RESULTADO, "anim_skip:nao_elegivel")

    def test_01_static_macro_pulada_em_fila_animacao(self):
        """(a) cena static_macro (animar=False, tipo image) em fila de animação -> PULADA."""
        pid = "anim_skip_a"
        cena = {"id": 1, "tipo": "image", "animar": False, "animate_later": False,
                "animar_depois": False, "media_intent": "image", "scene_type": "broll_macro",
                "status": "PENDENTE", "prompt_imagem": "macro de solo", "texto": "solo"}
        pdir, png, sha_antes = self._preparar(pid, cena)
        w = _criar_worker(self.tmp)

        atualizar_cena = MagicMock()
        atualizar_status = MagicMock()
        with mock.patch.object(sps, "atualizar_cena", atualizar_cena), \
             mock.patch.object(sps, "atualizar_status_cena", atualizar_status):
            (ok, msg), out = _processar(w, pid, cena, is_anim=True)

        self.assertTrue(ok, "skip NÃO é erro (não pode acionar retentativa/circuit-breaker)")
        self.assertEqual(msg, pf.ANIM_SKIP_RESULTADO)
        self.assertIn("[LOG] ANIM_SKIP", out)
        self.assertFalse(w._ensure_project_open.called, "não pode nem abrir/validar o projeto")
        self.assertFalse(w._set_output_mode.called, "não pode trocar para Nano Banana 2")
        self.assertFalse(atualizar_cena.called, "não pode alterar a cena no plano")
        self.assertFalse(atualizar_status.called, "não pode marcar ENVIANDO")
        self.assertEqual(hashlib.sha256(png.read_bytes()).hexdigest(), sha_antes,
                         "o PNG da cena não pode ser tocado")
        self.assertEqual(_ler_plano(pdir)[1].get("status"), "PENDENTE")
        self.assertEqual(w.page.method_calls, [], "nenhuma chamada ao DOM do Flow")

    def test_02_cena_elegivel_segue_para_o_flow(self):
        """Cena de vídeo (elegível) NÃO é pulada — o fluxo atual é preservado."""
        pid = "anim_skip_b"
        cena = {"id": 1, "tipo": "video", "animar": True, "media_intent": "video",
                "scene_type": "broll_action", "status": "PENDENTE",
                "prompt_animacao": "slow push in", "texto": "rega"}
        self._preparar(pid, cena, png=False)
        w = _criar_worker(self.tmp)

        (ok, msg), out = _processar(w, pid, cena, is_anim=True)

        self.assertNotEqual(msg, pf.ANIM_SKIP_RESULTADO)
        self.assertNotIn("[ANIM_SKIP]", out)
        self.assertTrue(w._ensure_project_open.called, "cena elegível deve seguir para o Flow")
        self.assertFalse(ok)
        self.assertIn("Google Flow fechado", msg)  # stub: para no 1º passo do Flow

    def test_03_avatar_sem_animar_tambem_pula(self):
        """Avatar sem animar em fila de animação gera video_mode False -> pulado (não sobrescreve PNG)."""
        pid = "anim_skip_c"
        cena = {"id": 1, "tipo": "image", "animar": False, "uses_character": True,
                "scene_type": "avatar_talking", "media_intent": "image", "status": "PENDENTE",
                "prompt_imagem": "apresentador", "texto": "apresentador"}
        pdir, png, sha_antes = self._preparar(pid, cena)
        w = _criar_worker(self.tmp)

        (ok, msg), out = _processar(w, pid, cena, is_anim=True)

        self.assertTrue(ok)
        self.assertEqual(msg, pf.ANIM_SKIP_RESULTADO)
        self.assertFalse(w._set_output_mode.called)
        self.assertEqual(hashlib.sha256(png.read_bytes()).hexdigest(), sha_antes)
        self.assertEqual(_ler_plano(pdir)[1].get("status"), "PENDENTE")

    def test_04_fila_de_imagem_nao_pula(self):
        """REGRA 6 preservada: is_anim=False gera imagem normalmente (nada mudou)."""
        pid = "anim_skip_d"
        cena = {"id": 1, "tipo": "image", "animar": False, "media_intent": "image",
                "scene_type": "broll_macro", "status": "PENDENTE", "prompt_imagem": "solo"}
        self._preparar(pid, cena)
        w = _criar_worker(self.tmp)

        (ok, msg), out = _processar(w, pid, cena, is_anim=False)

        self.assertNotEqual(msg, pf.ANIM_SKIP_RESULTADO)
        self.assertNotIn("[ANIM_SKIP]", out)
        self.assertTrue(w._ensure_project_open.called)

    def test_05_fallback_ativo_cena_elegivel_gera_imagem_como_hoje(self):
        """(b) PARTE 5 mantida: fallback de crédito de VÍDEO ativo + cena ELEGÍVEL (tipo video)
        → NÃO pula; cai para geração de IMAGEM como hoje (video_mode=False → modo "image")."""
        pid = "anim_skip_e"
        cena = {"id": 1, "tipo": "video", "animar": True, "media_intent": "video",
                "scene_type": "broll_action", "status": "PENDENTE", "prompt_imagem": "solo"}
        self._preparar(pid, cena, png=False)
        w = _criar_worker(self.tmp, _fallback_video_para_imagem=True,
                          _ensure_project_open=MagicMock(return_value=True))

        logs = []
        with mock.patch.object(pf, "pw_log",
                               lambda msg, level=None, **k: logs.append(str(msg))):
            (ok, msg), out = _processar(w, pid, cena, is_anim=True)

        self.assertNotEqual(msg, pf.ANIM_SKIP_RESULTADO, "cena elegível não pode ser pulada")
        self.assertNotIn("[ANIM_SKIP]", " ".join(logs))
        self.assertTrue(any("[BROLL] Fallback video->imagem ATIVO" in m for m in logs),
                        "o fallback de crédito de vídeo deve reconverter a cena para IMAGEM")
        self.assertTrue(w._set_output_mode.called, "a cena elegível deve seguir para o Flow")
        self.assertEqual(w._set_output_mode.call_args[0][0], "image",
                         "com o fallback ativo a cena elegível é gerada como IMAGEM")

    def test_05e_elegivel_sem_fallback_pede_video(self):
        """(c) Controle do (b): MESMA cena elegível, fallback INATIVO → modo "video"
        (prova que a única diferença é o fallback; o gate não pula em nenhum dos dois)."""
        pid = "anim_skip_e4"
        cena = {"id": 1, "tipo": "video", "animar": True, "media_intent": "video",
                "scene_type": "broll_action", "status": "PENDENTE", "animacao_prompt": "x"}
        self._preparar(pid, cena, png=False)
        w = _criar_worker(self.tmp, _ensure_project_open=MagicMock(return_value=True))

        logs = []
        with mock.patch.object(pf, "pw_log",
                               lambda msg, level=None, **k: logs.append(str(msg))):
            (ok, msg), out = _processar(w, pid, cena, is_anim=True)

        self.assertNotEqual(msg, pf.ANIM_SKIP_RESULTADO)
        self.assertNotIn("[BROLL] Fallback video->imagem ATIVO", " ".join(logs))
        self.assertTrue(w._set_output_mode.called)
        self.assertEqual(w._set_output_mode.call_args[0][0], "video")

    def test_05b_fallback_ativo_cena_nao_elegivel_e_pulada(self):
        """(a) O VAZAMENTO CORRIGIDO: fallback de vídeo ativo + cena NÃO elegível (static_macro)
        → PULADA (elegivel_video é independente do fallback): sem prompt, sem troca de modelo,
        PNG intacto, plano PENDENTE."""
        pid = "anim_skip_e2"
        cena = {"id": 1, "tipo": "image", "animar": False, "animate_later": False,
                "animar_depois": False, "media_intent": "image", "scene_type": "broll_macro",
                "status": "PENDENTE", "prompt_imagem": "macro de solo", "texto": "solo"}
        pdir, png, sha_antes = self._preparar(pid, cena)
        w = _criar_worker(self.tmp, _fallback_video_para_imagem=True)

        atualizar_cena = MagicMock()
        with mock.patch.object(sps, "atualizar_cena", atualizar_cena):
            (ok, msg), out = _processar(w, pid, cena, is_anim=True)

        self.assertTrue(ok)
        self.assertEqual(msg, pf.ANIM_SKIP_RESULTADO)
        self.assertIn("[LOG] ANIM_SKIP", out)
        self.assertNotIn("[BROLL] Fallback video->imagem ATIVO", out,
                         "a cena nem chega a entrar no caminho de imagem")
        self.assertFalse(w._ensure_project_open.called)
        self.assertFalse(w._set_output_mode.called, "não pode trocar para Nano Banana 2")
        self.assertFalse(atualizar_cena.called)
        self.assertEqual(w.page.method_calls, [], "nenhuma chamada ao DOM do Flow")
        self.assertEqual(hashlib.sha256(png.read_bytes()).hexdigest(), sha_antes,
                         "o PNG da cena não pode ser tocado")
        self.assertEqual(_ler_plano(pdir)[1].get("status"), "PENDENTE")

    def test_05c_fallback_ativo_chamador_marca_ignorada(self):
        """(a) ponta a ponta: worker REAL + fallback ativo -> a cena inelegível sai da fila
        como 'ignorada' (sem ERRO, sem crédito, sem mídia) e o PNG permanece intacto."""
        pid = "anim_skip_q3"
        cena = {"id": 1, "scene_index": 1, "status": "PENDENTE", "visual_prompt": "c1",
                "tipo": "image", "animar": False, "media_intent": "image",
                "scene_type": "broll_macro", "prompt_imagem": "macro de solo"}
        pdir = _criar_projeto(self.tmp, pid, [cena])
        png = pdir / "cenas" / "001.png"
        png.write_bytes(PNG_FALSO)
        sha_antes = hashlib.sha256(png.read_bytes()).hexdigest()

        w = _criar_worker(self.tmp, _fallback_video_para_imagem=True,
                          _ensure_project_open=MagicMock(return_value=True))  # worker REAL
        atualizar_cena = MagicMock()
        buf = io.StringIO()
        with mock.patch.object(pf, "PROJETOS_DIR", self.tmp), \
             mock.patch.object(sps, "PROJETOS_DIR", self.tmp), \
             mock.patch.object(chars, "PROJETOS_DIR", self.tmp), \
             mock.patch.object(sps, "atualizar_cena", atualizar_cena), \
             mock.patch.object(pf.time, "sleep", lambda *a, **k: None), \
             mock.patch.object(pf, "DELAY_ENTRE_PROMPTS_SEG", 0), \
             redirect_stdout(buf):
            w._handle_run_queue(pid, [1], modo="animacao", bypass_rate_limit=True)

        out = buf.getvalue()
        self.assertIn("[LOG] ANIM_SKIP", out)
        fila = json.loads((pdir / "flow_queue.json").read_text(encoding="utf-8"))
        itens = {int(it["scene_id"]): it for it in fila["itens"]}
        self.assertEqual(itens[1]["status"], "ignorada")
        self.assertEqual(fila.get("erros", 0), 0)
        self.assertEqual(fila["concluidos"], 0)
        self.assertIsNone(w.pause_reason)
        self.assertEqual(_ler_plano(pdir)[1].get("status"), "PENDENTE")
        self.assertEqual(hashlib.sha256(png.read_bytes()).hexdigest(), sha_antes)

    def test_05d_fallback_inativo_sem_mudanca(self):
        """(c) fallback INATIVO: mesmo retorno e mesmas (não) interações do teste (a)."""
        pid = "anim_skip_e3"
        cena = {"id": 1, "tipo": "image", "animar": False, "media_intent": "image",
                "scene_type": "broll_macro", "status": "PENDENTE", "prompt_imagem": "solo"}
        self._preparar(pid, cena)
        w = _criar_worker(self.tmp)  # fallback False

        (ok, msg), out = _processar(w, pid, cena, is_anim=True)

        self.assertTrue(ok)
        self.assertEqual(msg, pf.ANIM_SKIP_RESULTADO)
        self.assertFalse(w._ensure_project_open.called)
        self.assertFalse(w._set_output_mode.called)
        self.assertEqual(w.page.method_calls, [])

    def test_06_avatar_com_animar_continua_animando(self):
        """Comportamento ATUAL preservado (divergência documentada na Tarefa 3): avatar com
        animar=True e is_anim=True mantém video_mode True e segue para o Flow."""
        pid = "anim_skip_f"
        cena = {"id": 1, "tipo": "image", "animar": True, "animate_later": True,
                "uses_character": True, "scene_type": "avatar_talking",
                "media_intent": "video", "status": "PENDENTE", "prompt_animacao": "push in"}
        self._preparar(pid, cena, png=False)
        w = _criar_worker(self.tmp)

        (ok, msg), out = _processar(w, pid, cena, is_anim=True)

        self.assertNotEqual(msg, pf.ANIM_SKIP_RESULTADO)
        self.assertTrue(w._ensure_project_open.called)


class TestChamadorAnimSkip(AnimSkipBase):
    def test_07_cena_pulada_fica_ignorada_na_fila(self):
        """Chamador: (True, ANIM_SKIP_RESULTADO) -> item 'ignorada' (sem ERRO, sem retentativa)."""
        pid = "anim_skip_q1"
        cenas = [
            {"id": 1, "scene_index": 1, "status": "PENDENTE", "visual_prompt": "c1",
             "tipo": "image", "animar": False},
            {"id": 2, "scene_index": 2, "status": "PENDENTE", "visual_prompt": "c2",
             "tipo": "video", "animar": True},
        ]
        pdir = _criar_projeto(self.tmp, pid, cenas)
        respostas = {1: (True, pf.ANIM_SKIP_RESULTADO), 2: (True, "OK")}

        w, chamadas, out = _rodar_fila(self.tmp, pid, respostas, modo="animacao",
                                       target_ids=[1, 2])

        self.assertEqual(list(dict.fromkeys(chamadas)), [1, 2], "cena 2 continua sendo processada")
        self.assertNotIn("[AVISO CENA", out, "cena pulada não pode virar falha registrada")
        fila = json.loads((pdir / "flow_queue.json").read_text(encoding="utf-8"))
        itens = {int(it["scene_id"]): it for it in fila["itens"]}
        self.assertEqual(itens[1]["status"], "ignorada")
        self.assertIsNone(itens[1]["ultimo_erro"])
        self.assertEqual(itens[2]["status"], "concluida")
        self.assertEqual(fila.get("erros", 0), 0)
        self.assertEqual(fila["concluidos"], 1, "a cena ignorada não conta como concluída")
        self.assertEqual(fila.get("ignoradas"), 1)
        self.assertIsNone(w.pause_reason, "skip não pode pausar a fila")
        self.assertFalse(w.stop_requested.is_set())
        plano = _ler_plano(pdir)
        self.assertEqual(plano[1].get("status"), "PENDENTE")
        self.assertNotEqual(plano[1].get("status"), sps.STATUS_ERRO)
        self.assertEqual(len(w.scene_durations), 1, "só a cena realmente concluída entra na média")

    def test_08_falha_real_continua_marcando_erro(self):
        """Controle: falha definitiva (2 tentativas) continua STATUS_ERRO + item 'erro'."""
        pid = "anim_skip_q2"
        cenas = [{"id": 1, "scene_index": 1, "status": "PENDENTE", "visual_prompt": "c1",
                  "tipo": "video", "animar": True}]
        pdir = _criar_projeto(self.tmp, pid, cenas)
        respostas = {1: (False, "Google Flow falhou ao gerar mídia: timeout de polling")}

        w, chamadas, out = _rodar_fila(self.tmp, pid, respostas, modo="animacao")

        self.assertEqual(len(chamadas), 2, "falha real continua tentando 2x")
        fila = json.loads((pdir / "flow_queue.json").read_text(encoding="utf-8"))
        itens = {int(it["scene_id"]): it for it in fila["itens"]}
        self.assertEqual(itens[1]["status"], "erro")
        self.assertIn("[AVISO CENA 001]", out)
        plano = _ler_plano(pdir)
        self.assertEqual(plano[1].get("status"), sps.STATUS_ERRO)
        self.assertIsNone(w.pause_reason, "falha isolada não pausa (só o circuit-breaker pausa)")


if __name__ == "__main__":
    unittest.main(verbosity=2)

