# -*- coding: utf-8 -*-
"""
scripts/validar_abort_modo_video.py
Validação da TAREFA 5: Execução simulada onde o modo vídeo falha, confirmando:
1. Abort imediato em milissegundos (<0.5s) sem os 150s-300s de timeout/retry.
2. Cena marcada com STATUS_ERRO e VIDEO_STATUS_ERROR no scene_plan.
3. Mensagens de erro e logs descritivos.
4. Desbloqueio imediato da fila para a próxima cena.
5. Modo imagem não é afetado pelo abort estrito.
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# Adiciona raiz do projeto
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.playwright_flow import PlaywrightCDPWorker
import services.scene_plan_service as scene_plan_svc
from config import PROJETOS_DIR

def run_validation():
    print("=" * 70)
    print("INICIANDO VALIDAÇÃO DO ABORT IMEDIATO EM FALHA DE MODO VÍDEO")
    print("=" * 70)

    # Setup worker mock
    w = PlaywrightCDPWorker.__new__(PlaywrightCDPWorker)
    w.port = 9222
    w.page = MagicMock()
    w.current_model = "Veo 3.1 - Lite"
    w.current_flow_mode = "video"
    w.get_exact_balance = MagicMock(return_value=50)
    w._garantir_aba_flow_aberta = MagicMock(return_value=True)
    w._ensure_project_open = MagicMock(return_value=True)
    w._disable_agent_mode = MagicMock()
    w._fechar_modais_bloqueantes = MagicMock()
    w._limpar_overlays_e_backdrops = MagicMock()
    w._get_existing_media_snapshot = MagicMock(return_value=(set(), 0))
    w._marcar_cards_falha_existentes = MagicMock()
    w._clean_prompt_text = MagicMock(side_effect=lambda p, **kw: p)
    w._animar_imagem_galeria_flow = MagicMock(return_value=False)
    w._selecionar_referencia_flow = MagicMock(return_value=True)
    w._safe_click = MagicMock()

    loc_mock = MagicMock()
    loc_mock.is_visible.return_value = True
    w.page.locator.return_value.first = loc_mock

    # Setup projeto temporário
    projeto_id = "validacao_abort_video"
    proj_dir = PROJETOS_DIR / projeto_id
    proj_dir.mkdir(parents=True, exist_ok=True)

    cenas = [
        {
            "id": 1,
            "tipo": "video",
            "texto": "Cena 1 - Falha Ponto 1 (Text-to-Video)",
            "prompt_animacao": "cinematic drone flight over ocean",
            "status": scene_plan_svc.STATUS_PENDENTE
        },
        {
            "id": 2,
            "tipo": "video",
            "texto": "Cena 2 - Falha Ponto 2 (Fallback Animar -> Text-to-Video)",
            "arquivo_midia": str(proj_dir / "ref_cena2.png"),
            "prompt_animacao": "character walking slow motion",
            "status": scene_plan_svc.STATUS_PENDENTE
        },
        {
            "id": 3,
            "tipo": "image",
            "texto": "Cena 3 - Modo Imagem com modo_ok=False (não deve abortar imediatamente)",
            "prompt_imagem": "sunset over mountains",
            "status": scene_plan_svc.STATUS_PENDENTE
        },
        {
            "id": 4,
            "tipo": "video",
            "texto": "Cena 4 - Modo Vídeo OK (sucesso)",
            "prompt_animacao": "smooth zoom in on flowers",
            "status": scene_plan_svc.STATUS_PENDENTE
        }
    ]

    # Cria dummy file para cena 2
    (proj_dir / "ref_cena2.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfake")

    plan = {
        "projeto": projeto_id,
        "total": len(cenas),
        "cenas": cenas
    }
    scene_plan_svc.salvar_scene_plan(projeto_id, plan)

    resultados = []

    try:
        # TESTE 1: Ponto 1 - Falha na configuração de modo vídeo
        print("\n--- TESTE 1: Ponto 1 - Falha em _set_output_mode ('video') ---")
        w._set_output_mode = MagicMock(return_value=False)
        
        t0 = time.perf_counter()
        with patch("services.playwright_flow._detectar_cena_avatar", return_value=False), \
             patch("services.character_service.obter_personagem_cena", return_value={"uses_character": False}):
            ok, msg = w._processar_cena_individual(projeto_id, cenas[0], is_anim=True)
        t_elapsed = time.perf_counter() - t0

        p = scene_plan_svc.carregar_scene_plan(projeto_id)
        c1 = next(c for c in p["cenas"] if c["id"] == 1)

        print(f"Resultado retornado: ok={ok}, msg='{msg}'")
        print(f"Tempo de execução: {t_elapsed:.4f}s (esperado < 0.5s)")
        print(f"Status no scene_plan: {c1.get('status')}")
        print(f"Video status: {c1.get('video_status')}")
        print(f"Erro gravado: {c1.get('erro_msg')}")

        assert ok is False, "Deveria retornar False"
        assert t_elapsed < 0.5, f"Abort demorou {t_elapsed}s, esperado < 0.5s"
        assert c1.get("status") == scene_plan_svc.STATUS_ERRO, "Status deveria ser ERRO"
        assert c1.get("video_status") == scene_plan_svc.VIDEO_STATUS_ERROR, "video_status deveria ser ERROR"
        assert "Modo vídeo não confirmado no Flow" in msg, "Mensagem incorreta"
        resultados.append(("Ponto 1 (Abort antes do envio)", True, f"{t_elapsed:.4f}s"))

        # TESTE 2: Ponto 2 - Falha após Animar falhar
        print("\n--- TESTE 2: Ponto 2 - Falha no fallback após Animar ---")
        # Passo 3 (inicial) retorna True, mas o fallback passo 5 retorna False
        w._set_output_mode = MagicMock(side_effect=[True, False])
        w._animar_imagem_galeria_flow = MagicMock(return_value=False)

        t0 = time.perf_counter()
        with patch("services.playwright_flow._detectar_cena_avatar", return_value=False), \
             patch("services.character_service.obter_personagem_cena", return_value={"uses_character": False}):
            ok, msg = w._processar_cena_individual(projeto_id, cenas[1], is_anim=True)
        t_elapsed = time.perf_counter() - t0

        p = scene_plan_svc.carregar_scene_plan(projeto_id)
        c2 = next(c for c in p["cenas"] if c["id"] == 2)

        print(f"Resultado retornado: ok={ok}, msg='{msg}'")
        print(f"Tempo de execução: {t_elapsed:.4f}s (esperado < 0.5s)")
        print(f"Status no scene_plan: {c2.get('status')}")
        print(f"Video status: {c2.get('video_status')}")
        print(f"Erro gravado: {c2.get('erro_msg')}")

        assert ok is False, "Deveria retornar False"
        assert t_elapsed < 0.5, f"Abort demorou {t_elapsed}s, esperado < 0.5s"
        assert c2.get("status") == scene_plan_svc.STATUS_ERRO, "Status deveria ser ERRO"
        assert c2.get("video_status") == scene_plan_svc.VIDEO_STATUS_ERROR, "video_status deveria ser ERROR"
        assert "após falha do Animar" in msg, "Mensagem incorreta"
        resultados.append(("Ponto 2 (Abort pós-Animar)", True, f"{t_elapsed:.4f}s"))

        # TESTE 3: Modo Imagem não sofre abort precoce
        print("\n--- TESTE 3: Modo Imagem não deve sofrer abort imediato ---")
        w._set_output_mode = MagicMock(return_value=False)
        with patch("services.playwright_flow._detectar_cena_avatar", return_value=False), \
             patch("services.character_service.obter_personagem_cena", return_value={"uses_character": False}), \
             patch("services.playwright_flow.check_flow_credits", return_value={"has_credits": False}), \
             patch.object(w, "_tratar_indicador_limite", return_value="stop"):
            ok, msg = w._processar_cena_individual(projeto_id, cenas[2], is_anim=False)

        print(f"Resultado retornado para imagem: msg='{msg}'")
        assert "Modo vídeo não confirmado no Flow" not in msg, "Modo imagem não deveria sofrer abort de vídeo"
        resultados.append(("Modo Imagem flexível mantido", True, "OK"))

        # TESTE 4: Modo Vídeo quando _set_output_mode tem sucesso
        print("\n--- TESTE 4: Modo Vídeo quando configuração tem sucesso ---")
        w._set_output_mode = MagicMock(return_value=True)
        w._animar_imagem_galeria_flow = MagicMock(return_value=True)
        with patch("services.playwright_flow._detectar_cena_avatar", return_value=False), \
             patch("services.character_service.obter_personagem_cena", return_value={"uses_character": False}), \
             patch("services.playwright_flow.check_flow_credits", return_value={"has_credits": False}), \
             patch.object(w, "_tratar_indicador_limite", return_value="stop"):
            ok, msg = w._processar_cena_individual(projeto_id, cenas[3], is_anim=True)

        print(f"Resultado retornado para vídeo válido: msg='{msg}'")
        assert "Modo vídeo não confirmado no Flow" not in msg, "Modo vídeo bem-sucedido não deve abortar"
        resultados.append(("Modo Vídeo válido sem regressão", True, "OK"))

    finally:
        # Cleanup
        try:
            plan_path = proj_dir / "lira_scene_plan.json"
            if plan_path.exists():
                plan_path.unlink()
            for f in proj_dir.glob("*"):
                f.unlink()
            proj_dir.rmdir()
        except Exception:
            pass

    print("\n" + "=" * 70)
    print("RESUMO DA VALIDAÇÃO:")
    for nome, passou, detalhe in resultados:
        status_txt = "[PASSOU]" if passou else "[FALHOU]"
        print(f"{status_txt:10} | {nome:35} | {detalhe}")
    print("=" * 70)
    return all(r[1] for r in resultados)

if __name__ == "__main__":
    sucesso = run_validation()
    sys.exit(0 if sucesso else 1)
