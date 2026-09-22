import os
import sys
import json
import time
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import PROJETOS_DIR
from app_web import app
import services.playwright_flow as pw_flow

def run_experiment_b2():
    print("================================================================")
    print("EXPERIMENTO B2: Espera na Fila do Ator (Head-of-Line Blocking)")
    print("================================================================")

    pids = ["proj_exp_b2_p1", "proj_exp_b2_p2", "proj_exp_b2_p3"]
    for pid in pids:
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
                    "visual_prompt": f"Prompt for {pid}",
                    "image_status": "pending",
                    "status": "PENDENTE",
                    "arquivo_midia": ""
                }
            ]
        }
        with open(pdir / "lira_scene_plan.json", "w", encoding="utf-8") as f:
            json.dump(plan_data, f, indent=2)

    t0 = time.time()
    execucoes = []

    def stub_3s(projeto_id, scene_id, prompt=""):
        t_start = time.time() - t0
        cid = len(execucoes) + 1
        execucoes.append({"id": cid, "proj": projeto_id, "start": t_start, "end": None})
        print(f"[{t_start:.2f}s] [Stub #{cid}] INICIADO para {projeto_id}...")
        time.sleep(3.0)
        t_end = time.time() - t0
        execucoes[cid - 1]["end"] = t_end
        print(f"[{t_end:.2f}s] [Stub #{cid}] CONCLUÍDO para {projeto_id}.")
        return {"success": True, "caminho_imagem": f"img_{projeto_id}.png"}

    pw_flow._criar_imagem_cena_impl = stub_3s
    original_timeout_fn = pw_flow._timeout_do_ator
    # Timeout de 5s para cada tarefa
    pw_flow._timeout_do_ator = lambda: 5.0

    client = app.test_client()

    try:
        print("[0.00s] Disparando 3 projetos simultaneamente (stub=3s cada, timeout=5s)...")
        for pid in pids:
            client.post(f"/api/v2/producao/{pid}/fila/adicionar", json={"scene_ids": [1], "modo": "imagem"})

        # Monitorar por 12s
        for _ in range(12):
            time.sleep(1.0)
            elapsed = time.time() - t0
            status_line = []
            for pid in pids:
                st = client.get(f"/api/v2/producao/{pid}/fila_status").get_json()
                status_line.append(f"{pid}: [c={st.get('concluidas')}, g={st.get('gerando')}, e={st.get('erros')}, p={st.get('pendentes')}]")
            print(f"[{elapsed:.1f}s] " + " | ".join(status_line))

        print("\n--- RESULTADO DO EXPERIMENTO B2 ---")
        print(f"Execuções do stub iniciadas: {len(execucoes)}")
        for ex in execucoes:
            print(f"  Chamada #{ex['id']} ({ex['proj']}): start={ex['start']:.2f}s, end={ex['end']}")

        for pid in pids:
            pdir = PROJETOS_DIR / pid
            with open(pdir / "flow_queue.json", "r", encoding="utf-8") as fp:
                fq = json.load(fp)
            it0 = fq.get("itens", [{}])[0] if fq.get("itens") else {}
            print(f"Projeto {pid}: status={it0.get('status')}, tentativas={it0.get('tentativa_atual')}, ultimo_erro={it0.get('ultimo_erro')}")

        for pid in pids:
            w = pw_flow.FlowQueueWorker.peek_queue_worker(pid)
            if w:
                w.stop_processing()
    finally:
        pw_flow._timeout_do_ator = original_timeout_fn
        for pid in pids:
            shutil.rmtree(PROJETOS_DIR / pid, ignore_errors=True)

if __name__ == "__main__":
    run_experiment_b2()
