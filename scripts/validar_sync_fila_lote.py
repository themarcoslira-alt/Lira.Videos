"""
scripts/validar_sync_fila_lote.py — Validação do sincronismo de flow_queue.json com /fila_status em tempo real.
Simula um lote de 3 cenas com:
- Cena 1: Sucesso
- Cena 2: Abort rápido por falha de modo vídeo (Fast Abort)
- Cena 3: Sucesso
Captura o JSON de GET /api/v2/producao/<pid>/fila_status em múltiplos momentos durante e após a execução.
"""

import sys
import os
import json
import time
import shutil
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

# Adiciona raiz do projeto
ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

import config
import services.playwright_flow as pw_flow
import services.api_v2 as v2
import services.scene_plan_service as scene_plan_svc
from app_web import app


def executar_validacao():
    print("=" * 70)
    print("INICIANDO VALIDAÇÃO: SINCRONISMO flow_queue.json EM TEMPO REAL")
    print("=" * 70)

    tmp_dir = Path(tempfile.mkdtemp())
    proj_id = "proj_validacao_lote"
    pdir = tmp_dir / proj_id
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "cenas").mkdir(parents=True, exist_ok=True)

    # Plano com 5 cenas no projeto todo, mas o lote processará APENAS 3 cenas (ids: 1, 2, 3)
    # Isso valida o requisito de ESCOPO DO LOTE vs PROJETO INTEIRO.
    plano = {
        "versao": "2.0",
        "cenas": [
            {"id": 1, "scene_index": 1, "status": "PENDENTE", "tipo": "imagem", "arquivo_midia": "", "visual_prompt": "Cena 1 foto"},
            {"id": 2, "scene_index": 2, "status": "PENDENTE", "tipo": "video", "arquivo_midia": "", "prompt_animacao": "Cena 2 video abort"},
            {"id": 3, "scene_index": 3, "status": "PENDENTE", "tipo": "imagem", "arquivo_midia": "", "visual_prompt": "Cena 3 foto"},
            {"id": 4, "scene_index": 4, "status": "PENDENTE", "tipo": "imagem", "arquivo_midia": "", "visual_prompt": "Cena 4 fora do lote"},
            {"id": 5, "scene_index": 5, "status": "PENDENTE", "tipo": "imagem", "arquivo_midia": "", "visual_prompt": "Cena 5 fora do lote"}
        ]
    }
    (pdir / "lira_scene_plan.json").write_text(json.dumps(plano, indent=2), encoding="utf-8")

    client = app.test_client()

    # Prepara o worker
    w = pw_flow.PlaywrightCDPWorker.__new__(pw_flow.PlaywrightCDPWorker)
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
    w.scene_durations = []
    w.queue_start_time = None
    w.current_delay_info = None
    w.projetos_dir = tmp_dir

    w._iniciar_sessao_thread = MagicMock(return_value=(True, ""))
    w._ensure_project_open = MagicMock(return_value=True)
    w._upload_avatar_projeto = MagicMock()
    w._cenas_usam_personagem = MagicMock(return_value=False)
    w._set_output_mode = MagicMock(return_value=True)
    w._garantir_aba_flow_aberta = MagicMock(return_value=True)
    w._encerrar_sessao = MagicMock()
    w._extrair_metadados_sessao = MagicMock(return_value=("teste@lira.com", "Projeto Validacao"))

    capturas_fila_status = {}

    def mock_processar(projeto_id, cena, is_anim=False, index=1, total_cenas=3):
        cid = int(cena["id"])

        # Consulta /fila_status exatamente enquanto a cena está sendo executada pelo worker
        with client:
            res = client.get(f"/api/v2/producao/{proj_id}/fila_status")
            capturas_fila_status[f"durante_cena_{cid}"] = {
                "status_code": res.status_code,
                "data": res.get_json()
            }

        if cid == 2:
            return False, "Modo vídeo não confirmado no Flow — cena abortada antes do envio para evitar timeout de polling inutil"
        return True, "OK"

    w._processar_cena_individual = mock_processar

    print("\n[EXECUÇÃO] Disparando worker _handle_run_queue com lote de 3 cenas (ids: [1, 2, 3])...")

    with patch.object(pw_flow.FlowQueueWorker, "get_worker", return_value=w), \
         patch("services.playwright_flow.PROJETOS_DIR", tmp_dir), \
         patch("services.api_v2.PROJETOS_DIR", tmp_dir), \
         patch("services.scene_plan_service.PROJETOS_DIR", tmp_dir), \
         patch("services.playwright_flow.DELAY_ENTRE_PROMPTS_SEG", 0):

        w._handle_run_queue(proj_id, [1, 2, 3], modo="imagem", bypass_rate_limit=True)

        # Captura final após o término do processamento
        res_final = client.get(f"/api/v2/producao/{proj_id}/fila_status")
        capturas_fila_status["apos_termino"] = {
            "status_code": res_final.status_code,
            "data": res_final.get_json()
        }

    print("\n" + "=" * 70)
    print("CAPTURAS DO ENDPOINT /fila_status EM TEMPO REAL:")
    print("=" * 70)

    # 1. Durante a Cena 1
    d1 = capturas_fila_status["durante_cena_1"]["data"]
    print("\n--- [MOMENTO 1] Durante Execução da Cena 1 ---")
    print(f"Total lote: {d1['total']} (Projeto tem 5 cenas -> Escopo estrito do lote respeitado!)")
    print(f"Fila Ativa: {d1['fila_ativa']} | Cena em andamento: {d1['cena_em_andamento']}")
    print(f"Contadores: Concluídas={d1['concluidas']} | Gerando={d1['gerando']} | Pendentes={d1['pendentes']} | Erros={d1['erros']}")
    print("Status dos itens:")
    for it in d1["itens"]:
        print(f"  • Cena {it['scene_id']} (#{it['posicao']}) [{it['modo']}]: status='{it['status']}', iniciada_em={it['iniciada_em']}")

    assert d1["total"] == 3, f"Esperado total 3, obtido {d1['total']}"
    assert d1["gerando"] == 1
    assert d1["pendentes"] == 2
    assert d1["concluidas"] == 0
    assert d1["fila_ativa"] is True
    assert d1["cena_em_andamento"] == 1

    # 2. Durante a Cena 2 (vídeo com abort)
    d2 = capturas_fila_status["durante_cena_2"]["data"]
    print("\n--- [MOMENTO 2] Durante Execução da Cena 2 ---")
    print(f"Total lote: {d2['total']} | Fila Ativa: {d2['fila_ativa']} | Cena em andamento: {d2['cena_em_andamento']}")
    print(f"Contadores: Concluídas={d2['concluidas']} | Gerando={d2['gerando']} | Pendentes={d2['pendentes']} | Erros={d2['erros']}")
    print("Status dos itens:")
    for it in d2["itens"]:
        print(f"  • Cena {it['scene_id']} (#{it['posicao']}) [{it['modo']}]: status='{it['status']}', concluida_em={it['concluida_em']}")

    assert d2["total"] == 3
    assert d2["concluidas"] == 1
    assert d2["gerando"] == 1
    assert d2["pendentes"] == 1
    assert d2["fila_ativa"] is True
    assert d2["cena_em_andamento"] == 2

    # 3. Durante a Cena 3 (após falha na cena 2)
    d3 = capturas_fila_status["durante_cena_3"]["data"]
    print("\n--- [MOMENTO 3] Durante Execução da Cena 3 (Após Cena 2 Falhar) ---")
    print(f"Total lote: {d3['total']} | Fila Ativa: {d3['fila_ativa']} | Cena em andamento: {d3['cena_em_andamento']}")
    print(f"Contadores: Concluídas={d3['concluidas']} | Gerando={d3['gerando']} | Pendentes={d3['pendentes']} | Erros={d3['erros']}")
    print("Status dos itens:")
    for it in d3["itens"]:
        print(f"  • Cena {it['scene_id']} (#{it['posicao']}) [{it['modo']}]: status='{it['status']}', erro='{it['ultimo_erro']}'")

    assert d3["total"] == 3
    assert d3["concluidas"] == 1
    assert d3["erros"] == 1
    assert d3["gerando"] == 1
    assert d3["pendentes"] == 0
    assert d3["fila_ativa"] is True
    assert d3["cena_em_andamento"] == 3

    # 4. Após o Término
    df = capturas_fila_status["apos_termino"]["data"]
    print("\n--- [MOMENTO 4] Após Término Completo da Fila ---")
    print(f"Total lote: {df['total']} | Fila Ativa: {df['fila_ativa']} | Cena em andamento: {df['cena_em_andamento']}")
    print(f"Contadores: Concluídas={df['concluidas']} | Gerando={df['gerando']} | Pendentes={df['pendentes']} | Erros={df['erros']}")
    print("Status dos itens finais:")
    for it in df["itens"]:
        print(f"  • Cena {it['scene_id']} (#{it['posicao']}) [{it['modo']}]: status='{it['status']}', concluida_em={it['concluida_em']}, erro={it['ultimo_erro']}")

    assert df["total"] == 3
    assert df["concluidas"] == 2
    assert df["erros"] == 1
    assert df["gerando"] == 0
    assert df["pendentes"] == 0
    assert df["fila_ativa"] is False
    assert df["cena_em_andamento"] is None

    # Limpeza
    shutil.rmtree(tmp_dir, ignore_errors=True)

    print("\n" + "=" * 70)
    print("TODAS AS VALIDAÇÕES DE TEMPO REAL, ESCOPO E STATUS PASSARAM COM SUCESSO!")
    print("=" * 70)


if __name__ == "__main__":
    executar_validacao()
