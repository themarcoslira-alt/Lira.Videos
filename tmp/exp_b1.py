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

def run_experiment_b1():
    print("================================================================")
    print("EXPERIMENTO B1: Timeout Menor que a Geração")
    print("================================================================")

    pid = "proj_exp_b1_timeout"
    pdir = PROJETOS_DIR / pid
    if pdir.exists():
        shutil.rmtree(pdir, ignore_errors=True)
    pdir.mkdir(parents=True, exist_ok=True)

    # Cria plano com 1 cena
    plan_data = {
        "projeto_id": pid,
        "cenas": [
            {
                "id": 1,
                "scene_id": 1,
                "visual_prompt": "Cinematic shot of a cybernetic robot",
                "image_status": "pending",
                "status": "PENDENTE",
                "arquivo_midia": ""
            }
        ]
    }
    with open(pdir / "lira_scene_plan.json", "w", encoding="utf-8") as f:
        json.dump(plan_data, f, indent=2)

    # Stub de 5 segundos de geração
    t0 = time.time()
    execucoes = []
    imagens_geradas = []

    def stub_lento(projeto_id, scene_id, prompt=""):
        chamada_id = len(execucoes) + 1
        t_start = time.time() - t0
        execucoes.append({"id": chamada_id, "start": t_start, "end": None})
        print(f"[{t_start:.2f}s] [Stub #{chamada_id}] INICIADO (dormindo 5s)...")
        time.sleep(5.0)
        t_end = time.time() - t0
        execucoes[chamada_id - 1]["end"] = t_end
        img_name = f"img_{projeto_id}_scene_{scene_id}_call_{chamada_id}.png"
        imagens_geradas.append(img_name)
        print(f"[{t_end:.2f}s] [Stub #{chamada_id}] CONCLUÍDO. Imagem gerada: {img_name}")
        return {"success": True, "caminho_imagem": img_name}

    # Monkeypatch do timeout para 2 segundos
    pw_flow._criar_imagem_cena_impl = stub_lento
    original_timeout_fn = pw_flow._timeout_do_ator
    pw_flow._timeout_do_ator = lambda: 2.0

    client = app.test_client()

    try:
        print("[0.00s] Enfileirando cena 1 com timeout do ator = 2s (stub demora 5s)...")
        r = client.post(f"/api/v2/producao/{pid}/fila/adicionar", json={"scene_ids": [1], "modo": "imagem"})
        print(f"Resposta adicionar: {r.status_code}, {r.get_json()}")

        # Acompanhar por 16 segundos
        for _ in range(16):
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
            print(f"[{elapsed:.1f}s] Polling: pendentes={st.get('pendentes')}, gerando={st.get('gerando')}, "
                  f"concluidas={st.get('concluidas')}, erros={st.get('erros')} | Item status={it0.get('status')}, "
                  f"tentativas={it0.get('tentativa_atual')}, exec_id={str(it0.get('exec_id'))[:8]}")

        print("\n--- RESULTADO DO EXPERIMENTO B1 ---")
        print(f"Total de chamadas do stub iniciadas: {len(execucoes)}")
        for ex in execucoes:
            print(f"  Chamada #{ex['id']}: start={ex['start']:.2f}s, end={ex['end']}")
        print(f"Imagens geradas/produzidas: {imagens_geradas}")

        with open(pdir / "flow_queue.json", "r", encoding="utf-8") as fp:
            fq_final = json.load(fp)
        with open(pdir / "lira_scene_plan.json", "r", encoding="utf-8") as fp:
            lp_final = json.load(fp)

        print(f"Estado final flow_queue.json: {json.dumps(fq_final, indent=2)}")
        print(f"Estado final lira_scene_plan.json: {json.dumps(lp_final['cenas'], indent=2)}")

        w = pw_flow.FlowQueueWorker.peek_queue_worker(pid)
        if w:
            w.stop_processing()
    finally:
        pw_flow._timeout_do_ator = original_timeout_fn
        shutil.rmtree(pdir, ignore_errors=True)

if __name__ == "__main__":
    run_experiment_b1()
