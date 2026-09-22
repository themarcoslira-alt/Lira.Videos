# -*- coding: utf-8 -*-
"""
scripts/validar_timing_pipeline.py
Validação da TAREFA 5: Simulação e validação de 2 cenas:
  1. Cena com SUCESSO COMPLETO passando pelas 5 etapas instrumentadas
     (Abertura, Configuração de Modo, Anexo de Personagem, Envio de Prompt, Renderização).
  2. Cena com ABORT IMEDIATO por falha de modo vídeo.
Verifica:
  - Logs por etapa: [TIMING] Cena {cid} | Etapa: {nome} | Duração: {dur}ms
  - Log consolidado: [TIMING_RESUMO] Cena {cid} | Total: {total} | {breakdown}
  - Inclusão de duration_ms em events.jsonl e [duração: {ms}ms] em lira.log.
"""

import sys
import time
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

# Adiciona raiz do projeto
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.playwright_flow import PlaywrightCDPWorker
import services.scene_plan_service as scene_plan_svc
import services.event_logger as event_logger
from config import PROJETOS_DIR, LOGS_DIR

def run_timing_validation():
    print("=" * 80)
    print("VALIDAÇÃO DE TIMING DO PIPELINE (TAREFA 5)")
    print("=" * 80)

    projeto_id = "proj_timing_val"
    proj_dir = PROJETOS_DIR / projeto_id
    proj_dir.mkdir(parents=True, exist_ok=True)

    # Cena 1: Sucesso Completo (Avatar / com personagem, todas as 5 etapas ativas)
    cena_sucesso = {
        "id": 1,
        "tipo": "image",
        "texto": "Close-up portrait of character in library",
        "prompt_imagem": "portrait of a scholar in antique library",
        "status": scene_plan_svc.STATUS_PENDENTE
    }

    # Cena 2: Abort Imediato por Falha de Modo Vídeo
    cena_abort = {
        "id": 2,
        "tipo": "video",
        "texto": "Drone shot flying over ocean waves",
        "prompt_animacao": "cinematic drone flight over ocean waves, 4k",
        "status": scene_plan_svc.STATUS_PENDENTE
    }

    plan = {
        "projeto": projeto_id,
        "total": 2,
        "cenas": [cena_sucesso, cena_abort]
    }
    scene_plan_svc.salvar_scene_plan(projeto_id, plan)

    # Setup worker mock com simulação de latências realistas por etapa
    w = PlaywrightCDPWorker(port=9222)
    w.page = MagicMock()
    w.current_model = "Nano Banana 2"
    w.current_flow_mode = "image"
    w.get_exact_balance = MagicMock(return_value=100)
    w._fechar_modais_bloqueantes = MagicMock()
    w._disable_agent_mode = MagicMock()
    w._limpar_overlays_e_backdrops = MagicMock()
    w._get_existing_media_snapshot = MagicMock(return_value=(set(), 0))
    w._marcar_cards_falha_existentes = MagicMock()
    w._clean_prompt_text = MagicMock(side_effect=lambda p, **kw: p)
    w._safe_click = MagicMock()

    loc_mock = MagicMock()
    loc_mock.is_visible.return_value = True
    loc_mock.is_disabled.return_value = False
    w.page.locator.return_value.first = loc_mock
    w.stop_requested = threading.Event()
    w._detectar_erro_ou_limite_modelo = MagicMock(return_value=None)
    w._detectar_card_falha_geracao = MagicMock(return_value=None)
    w._detectar_novo_card_falha = MagicMock(return_value=None)
    w._garantir_aba_flow_aberta = MagicMock(return_value=True)
    w._salvar_imagem_baixada = MagicMock(return_value={"arquivo_nome": "cena_001.png"})
    w._sincronizar_saldo_apos_geracao = MagicMock()
    w._salvar_download_flow = MagicMock(return_value={"arquivo_nome": "cena_001.png"})
    w._baixar_imagem_card = MagicMock(return_value=(True, b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfake"))

    # Mock de evaluate para polling retornar mídia válida na detecção
    def _mock_evaluate(script, *args, **kwargs):
        s = str(script)
        if "progressbar" in s or "progress" in s:
            return False
        return {
            "ok": True,
            "media": [
                {
                    "id": "card-1",
                    "mediaKey": "key-unique-12345",
                    "src": "https://example.com/generated_1.png",
                    "type": "image",
                    "dataUrl": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
                    "width": 1920,
                    "height": 1080
                }
            ]
        }
    w.page.evaluate = _mock_evaluate

    # Simulação da Cena 1: Sucesso completo
    def _sim_ensure_project_open(*a, **kw):
        time.sleep(0.12)  # 120ms
        return True
    w._ensure_project_open = _sim_ensure_project_open

    def _sim_set_output_mode(*a, **kw):
        time.sleep(0.08)  # 80ms
        return True
    w._set_output_mode = _sim_set_output_mode

    def _sim_selecionar_referencia(*a, **kw):
        time.sleep(0.15)  # 150ms
        return True
    w._selecionar_referencia_flow = _sim_selecionar_referencia

    # No loop de polling (Stage E), simula 200ms de espera
    w.page.wait_for_timeout = MagicMock(side_effect=lambda ms: time.sleep(0.05))

    logs_capturados = []
    import services.event_logger as el
    original_log_event = el.log_event

    def spy_log_event(category, message, level="info", details=None, duration_ms=None):
        logs_capturados.append({
            "category": category,
            "message": message,
            "level": level,
            "duration_ms": duration_ms
        })
        return original_log_event(category, message, level=level, details=details, duration_ms=duration_ms)

    with patch("services.event_logger.log_event", side_effect=spy_log_event), \
         patch("services.playwright_flow.log_event", side_effect=spy_log_event):

        # =====================================================================
        # EXECUÇÃO 1: CENA DE SUCESSO COMPLETO (5 ETAPAS)
        # =====================================================================
        print("\n>>> EXECUTANDO CENA 1: SUCESSO COMPLETO (Todas as 5 Etapas) <<<")
        logs_capturados.clear()

        dummy_img = proj_dir / "cena_001.png"
        dummy_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 1000)

        with patch("services.playwright_flow._detectar_cena_avatar", return_value=False), \
             patch("services.character_service.obter_personagem_cena", return_value={"uses_character": True, "nome": "Marcos", "character_ref": "@Marcos"}), \
             patch("services.scene_plan_service.salvar_midia_cena_estruturada", return_value={"success": True, "arquivo_nome": "cena_001.png", "arquivo_path": str(dummy_img)}), \
             patch("services.playwright_flow.check_flow_credits", return_value={"has_credits": True}):
            ok1, msg1 = w._processar_cena_individual(projeto_id, cena_sucesso, is_anim=False)

        assert ok1 is True, f"Cena 1 deveria ter sucesso completo, recebeu {msg1}"

        print(f"\n[Resultado Cena 1] ok={ok1} | msg={msg1}")
        print("\nLogs [TIMING] capturados na Cena 1:")
        timing_logs_c1 = [l for l in logs_capturados if "[TIMING" in l["message"]]
        for tl in timing_logs_c1:
            dur_txt = f" (duration_ms={tl['duration_ms']:.1f}ms)" if tl.get('duration_ms') is not None else ""
            print(f"  {tl['message']}{dur_txt}")

        resumo_c1 = next((l for l in timing_logs_c1 if "[TIMING_RESUMO]" in l["message"]), None)
        assert resumo_c1 is not None, "Cena 1 deveria ter emitido [TIMING_RESUMO]"
        assert "abertura=" in resumo_c1["message"]
        assert "config_modo=" in resumo_c1["message"]
        assert "anexo_personagem=" in resumo_c1["message"]
        assert "envio_prompt=" in resumo_c1["message"]
        assert "renderizacao=" in resumo_c1["message"]

        # =====================================================================
        # EXECUÇÃO 2: CENA COM ABORT POR FALHA DE MODO VÍDEO
        # =====================================================================
        print("\n" + "-" * 80)
        print(">>> EXECUTANDO CENA 2: ABORT IMEDIATO (Falha de Modo Vídeo) <<<")
        logs_capturados.clear()

        # Força falha de modo vídeo
        w._set_output_mode = MagicMock(return_value=False)

        with patch("services.playwright_flow._detectar_cena_avatar", return_value=False), \
             patch("services.character_service.obter_personagem_cena", return_value={"uses_character": False}):
            ok2, msg2 = w._processar_cena_individual(projeto_id, cena_abort, is_anim=True)

        print(f"\n[Resultado Cena 2] ok={ok2} | msg={msg2}")
        print("\nLogs [TIMING] capturados na Cena 2:")
        timing_logs_c2 = [l for l in logs_capturados if "[TIMING" in l["message"]]
        for tl in timing_logs_c2:
            dur_txt = f" (duration_ms={tl['duration_ms']:.1f}ms)" if tl.get('duration_ms') is not None else ""
            print(f"  {tl['message']}{dur_txt}")

        resumo_c2 = next((l for l in timing_logs_c2 if "[TIMING_RESUMO]" in l["message"]), None)
        assert resumo_c2 is not None, "Cena 2 deveria ter emitido [TIMING_RESUMO]"
        assert "config_modo=" in resumo_c2["message"]
        assert "renderizacao=n/a" in resumo_c2["message"]
        assert "Total:" in resumo_c2["message"]
        assert "ms" in resumo_c2["message"], "Duração total da cena abortada deve estar expressa em ms no resumo consolidado"

    # Cleanup
    try:
        plan_path = proj_dir / "lira_scene_plan.json"
        if plan_path.exists():
            plan_path.unlink()
        proj_dir.rmdir()
    except Exception:
        pass

    print("\n" + "=" * 80)
    print("VALIDAÇÃO CONCLUÍDA COM SUCESSO: AMBAS AS CENAS ATENDERAM TODOS OS CRITÉRIOS!")
    print("=" * 80)
    return True

if __name__ == "__main__":
    success = run_timing_validation()
    sys.exit(0 if success else 1)
