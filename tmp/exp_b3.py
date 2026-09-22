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

def run_experiment_b3():
    print("================================================================")
    print("EXPERIMENTO B3: Lock Legado Bloqueando o Ator CDP")
    print("================================================================")

    pid = "proj_exp_b3_leglock"
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
                "visual_prompt": "Cyberpunk cityscape at night",
                "image_status": "pending",
                "status": "PENDENTE",
                "arquivo_midia": ""
            }
        ]
    }
    with open(pdir / "lira_scene_plan.json", "w", encoding="utf-8") as f:
        json.dump(plan_data, f, indent=2)

    t0 = time.time()
    stub_calls = []

    def stub_com_lock(projeto_id, scene_id, prompt=""):
        print(f"[{time.time()-t0:.2f}s] [Ator] Tentando adquirir _flow_generation_lock...")
        with pw_flow._flow_generation_lock:
            print(f"[{time.time()-t0:.2f}s] [Ator] Lock adquirido pelo Ator!")
            t_call = time.time() - t0
            stub_calls.append(t_call)
            print(f"[{t_call:.2f}s] [Stub] EXECUTADO com sucesso para cena {scene_id}!")
            return {"success": True, "caminho_imagem": f"img_{scene_id}.png"}

    pw_flow._criar_imagem_cena_impl = stub_com_lock
    original_timeout_fn = pw_flow._timeout_do_ator
    # Timeout do ator = 2s
    pw_flow._timeout_do_ator = lambda: 2.0

    client = app.test_client()

    # Thread que simula o caminho legado segurando _flow_generation_lock por 10s
    def simular_legado_segurando_lock():
        print("[0.00s] [Legado] Adquirindo _flow_generation_lock por 10 segundos...")
        with pw_flow._flow_generation_lock:
            print("[0.00s] [Legado] Lock adquirido! Dormindo 10 segundos...")
            time.sleep(10.0)
            print("[10.00s] [Legado] Liberando _flow_generation_lock.")

    t_legado = threading.Thread(target=simular_legado_segurando_lock, daemon=True)
    t_legado.start()
    time.sleep(0.1) # Garante que a thread do legado pegou o lock primeiro

    try:
        print("[0.10s] Chamando POST /api/v2/producao/{pid}/fila/adicionar...")
        r = client.post(f"/api/v2/producao/{pid}/fila/adicionar", json={"scene_ids": [1], "modo": "imagem"})
        print(f"Resposta adicionar: {r.status_code}, {r.get_json()}")

        # Monitorar por 14 segundos
        for _ in range(14):
            time.sleep(1.0)
            elapsed = time.time() - t0
            st = client.get(f"/api/v2/producao/{pid}/fila_status").get_json()
            q_file = pdir / "flow_queue.json"
            q_data = {}
            if q_file.exists():
                try:
                    with open(q_file, "r", encoding="utf-8") as fp:
                        q_data = json.load(fp)
                except Exception:
                    pass
            it0 = q_data.get("itens", [{}])[0] if q_data.get("itens") else {}
            print(f"[{elapsed:.1f}s] Polling: p={st.get('pendentes')}, g={st.get('gerando')}, "
                  f"c={st.get('concluidas')}, e={st.get('erros')} | Item: status={it0.get('status')}, "
                  f"tentativas={it0.get('tentativa_atual')}/{it0.get('max_tentativas')}, "
                  f"erro={str(it0.get('ultimo_erro'))[:50]}")

        print("\n--- RESULTADO DO EXPERIMENTO B3 ---")
        print(f"Total de chamadas do stub durante/após o lock: {len(stub_calls)}")
        with open(pdir / "flow_queue.json", "r", encoding="utf-8") as fp:
            fq = json.load(fp)
        with open(pdir / "lira_scene_plan.json", "r", encoding="utf-8") as fp:
            lp = json.load(fp)

        it0 = fq.get("itens", [{}])[0] if fq.get("itens") else {}
        print(f"Item final em flow_queue.json: status={it0.get('status')}, tentativa={it0.get('tentativa_atual')}, erro={it0.get('ultimo_erro')}")
        print(f"Cena 1 em lira_scene_plan.json: status={lp['cenas'][0].get('status')}, image_status={lp['cenas'][0].get('image_status')}")

        w = pw_flow.FlowQueueWorker.peek_queue_worker(pid)
        if w:
            w.stop_processing()
    finally:
        pw_flow._timeout_do_ator = original_timeout_fn
        shutil.rmtree(pdir, ignore_errors=True)

if __name__ == "__main__":
    run_experiment_b3()
