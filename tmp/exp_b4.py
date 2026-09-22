import os
import sys
import json
import time
import shutil
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import PROJETOS_DIR
from app_web import app
import services.playwright_flow as pw_flow

def run_experiment_b4():
    print("================================================================")
    print("EXPERIMENTO B4: Cancelar com Worker Vivo e Re-adicionar")
    print("================================================================")

    pid = "proj_exp_b4_cancel_live"
    pdir = PROJETOS_DIR / pid
    if pdir.exists():
        shutil.rmtree(pdir, ignore_errors=True)
    pdir.mkdir(parents=True, exist_ok=True)

    plan_data = {
        "projeto_id": pid,
        "cenas": [
            {
                "id": 1,
                "scene_id": 1,
                "visual_prompt": "Prompt for scene 1",
                "image_status": "pending",
                "status": "PENDENTE",
                "arquivo_midia": ""
            },
            {
                "id": 2,
                "scene_id": 2,
                "visual_prompt": "Prompt for scene 2",
                "image_status": "pending",
                "status": "PENDENTE",
                "arquivo_midia": ""
            }
        ]
    }
    with open(pdir / "lira_scene_plan.json", "w", encoding="utf-8") as f:
        json.dump(plan_data, f, indent=2)

    t0 = time.time()
    stub_executions = []

    def stub_5s(projeto_id, scene_id, prompt=""):
        cid = len(stub_executions) + 1
        t_start = time.time() - t0
        th_name = threading.current_thread().name
        stub_executions.append({"id": cid, "scene_id": scene_id, "start": t_start, "end": None, "thread": th_name})
        print(f"[{t_start:.2f}s] [Stub #{cid}] INICIADO para cena {scene_id} na thread '{th_name}' (dormindo 5s)...")
        time.sleep(5.0)
        t_end = time.time() - t0
        stub_executions[cid - 1]["end"] = t_end
        print(f"[{t_end:.2f}s] [Stub #{cid}] CONCLUÍDO para cena {scene_id}.")
        return {"success": True, "caminho_imagem": f"img_scene_{scene_id}_call_{cid}.png"}

    pw_flow._criar_imagem_cena_impl = stub_5s
    original_timeout_fn = pw_flow._timeout_do_ator
    pw_flow._timeout_do_ator = lambda: 15.0

    client = app.test_client()

    try:
        print("[0.00s] Enfileirando cena 1...")
        client.post(f"/api/v2/producao/{pid}/fila/adicionar", json={"scene_ids": [1], "modo": "imagem"})

        worker_antigo = None
        # Aguardar 1s para o worker iniciar e entrar no stub
        time.sleep(1.0)
        worker_antigo = pw_flow.FlowQueueWorker.peek_queue_worker(pid)
        print(f"[1.00s] Worker inicial capturado: {worker_antigo.name} (is_alive={worker_antigo.is_alive()})")

        # Cancelar fila aos 1s
        print("[1.00s] Chamando POST /api/v2/producao/{pid}/fila/cancelar...")
        client.post(f"/api/v2/producao/{pid}/fila/cancelar")

        # Re-adicionar cena 1 e cena 2 imediatamente
        print("[1.05s] Re-adicionando cena 1 e cena 2 imediatamente...")
        client.post(f"/api/v2/producao/{pid}/fila/adicionar", json={"scene_ids": [1, 2], "modo": "imagem"})

        worker_novo = pw_flow.FlowQueueWorker.peek_queue_worker(pid)
        print(f"[1.10s] Worker após re-adicionar: {worker_novo.name} (is_alive={worker_novo.is_alive()})")
        print(f"[1.10s] Coexistência de workers: worker_antigo is worker_novo? {worker_antigo is worker_novo}")
        print(f"[1.10s] Ambos vivos simultaneamente? worker_antigo.is_alive()={worker_antigo.is_alive()} e worker_novo.is_alive()={worker_novo.is_alive()}")

        # Acompanhar por 21 segundos
        tempo_morte_antigo = None
        for _ in range(21):
            time.sleep(1.0)
            elapsed = time.time() - t0
            if tempo_morte_antigo is None and not worker_antigo.is_alive():
                tempo_morte_antigo = elapsed
                print(f"[{elapsed:.2f}s] Worker antigo finalmente MORREU.")

            active_threads = [t.name for t in threading.enumerate() if pid in t.name]
            st = client.get(f"/api/v2/producao/{pid}/fila_status").get_json()
            print(f"[{elapsed:.1f}s] Threads vivas do projeto: {active_threads} | Status: c={st.get('concluidas')}, g={st.get('gerando')}, e={st.get('erros')}, p={st.get('pendentes')}")

        print("\n--- RESULTADO DO EXPERIMENTO B4 ---")
        print(f"Tempo até worker antigo morrer: {tempo_morte_antigo:.2f}s se medido" if tempo_morte_antigo else "Worker antigo não morreu no período!")
        print(f"Total de execuções do stub: {len(stub_executions)}")
        end_str = f"{ex['end']:.2f}s" if ex['end'] is not None else "em andamento"
        print(f"  Chamada #{ex['id']}: cena {ex['scene_id']}, start={ex['start']:.2f}s, end={end_str}")

        with open(pdir / "flow_queue.json", "r", encoding="utf-8") as fp:
            fq = json.load(fp)
        with open(pdir / "lira_scene_plan.json", "r", encoding="utf-8") as fp:
            lp = json.load(fp)

        print(f"flow_queue.json final: {json.dumps(fq.get('itens', []), indent=2)}")
        print(f"lira_scene_plan.json final: {json.dumps(lp.get('cenas', []), indent=2)}")

        w = pw_flow.FlowQueueWorker.peek_queue_worker(pid)
        if w:
            w.stop_processing()
    finally:
        pw_flow._timeout_do_ator = original_timeout_fn
        shutil.rmtree(pdir, ignore_errors=True)

if __name__ == "__main__":
    run_experiment_b4()
