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

def run_experiment_b5():
    print("================================================================")
    print("EXPERIMENTO B5: Ator Travado e Exceções Críticas")
    print("================================================================")

    # -------------------------------------------------------------
    # Parte A: Tarefa que levanta SystemExit / KeyboardInterrupt
    # -------------------------------------------------------------
    print("\n--- PARTE A: Tarefa levanta SystemExit ---")
    actor = pw_flow._get_cdp_actor()
    print(f"Ator inicial vivo? {actor.is_alive()}, name={actor.name}, ident={actor.ident}")

    def raise_system_exit(p, s, prompt=""):
        print("[Ator] Levantando SystemExit propositalmente...")
        raise SystemExit("Simulando SystemExit na tarefa")

    original_impl = pw_flow._criar_imagem_cena_impl
    pw_flow._criar_imagem_cena_impl = raise_system_exit

    res_a = None
    try:
        res_a = pw_flow.criar_imagem_cena("proj_test", 1, "prompt")
    except BaseException as e:
        res_a = {"base_exception_caught": type(e).__name__, "msg": str(e)}

    print(f"Retorno / Exceção capturada após SystemExit: {res_a}")
    print(f"Ator ainda vivo após SystemExit? {actor.is_alive()}, ident={actor.ident}")

    # -------------------------------------------------------------
    # Parte B: Tarefa que nunca retorna (ator travado)
    # -------------------------------------------------------------
    print("\n--- PARTE B: Tarefa nunca retorna (travamento global) ---")
    pids = ["proj_b5_travado_1", "proj_b5_travado_2"]
    for pid in pids:
        pdir = PROJETOS_DIR / pid
        if pdir.exists():
            shutil.rmtree(pdir, ignore_errors=True)
        pdir.mkdir(parents=True, exist_ok=True)
        plan_data = {
            "projeto_id": pid,
            "cenas": [
                {"id": 1, "scene_id": 1, "visual_prompt": "Prompt 1", "image_status": "pending", "status": "PENDENTE", "arquivo_midia": ""}
            ]
        }
        with open(pdir / "lira_scene_plan.json", "w", encoding="utf-8") as f:
            json.dump(plan_data, f, indent=2)

    t0 = time.time()

    def hang_task(projeto_id, scene_id, prompt=""):
        if "travado_1" in projeto_id:
            print(f"[{time.time()-t0:.2f}s] [HUNG TASK] {projeto_id} travando indefinidamente (sleep 30s)...")
            time.sleep(30.0)
            return {"success": True}
        else:
            print(f"[{time.time()-t0:.2f}s] [NORMAL TASK] {projeto_id} executando...")
            return {"success": True, "caminho_imagem": "img.png"}

    pw_flow._criar_imagem_cena_impl = hang_task
    original_timeout_fn = pw_flow._timeout_do_ator
    # Timeout de 3s
    pw_flow._timeout_do_ator = lambda: 3.0

    client = app.test_client()

    try:
        print("[0.00s] Disparando proj_b5_travado_1 (que trava) e em seguida proj_b5_travado_2 (que espera)...")
        client.post(f"/api/v2/producao/{pids[0]}/fila/adicionar", json={"scene_ids": [1], "modo": "imagem"})
        time.sleep(0.5)
        client.post(f"/api/v2/producao/{pids[1]}/fila/adicionar", json={"scene_ids": [1], "modo": "imagem"})

        primeiro_erro_ts = None
        for _ in range(12):
            time.sleep(1.0)
            elapsed = time.time() - t0
            st1 = client.get(f"/api/v2/producao/{pids[0]}/fila_status").get_json()
            st2 = client.get(f"/api/v2/producao/{pids[1]}/fila_status").get_json()

            if primeiro_erro_ts is None and (st1.get("erros", 0) > 0 or st2.get("erros", 0) > 0):
                primeiro_erro_ts = elapsed

            print(f"[{elapsed:.1f}s] P1: c={st1.get('concluidas')}, g={st1.get('gerando')}, e={st1.get('erros')} | "
                  f"P2: c={st2.get('concluidas')}, g={st2.get('gerando')}, e={st2.get('erros')}, p={st2.get('pendentes')}")

        print("\n--- RESULTADO DO EXPERIMENTO B5 ---")
        print(f"Tempo até o primeiro erro visível na fila: {primeiro_erro_ts:.2f}s" if primeiro_erro_ts else "Nenhum erro reportado no período!")
        for pid in pids:
            pdir = PROJETOS_DIR / pid
            with open(pdir / "flow_queue.json", "r", encoding="utf-8") as fp:
                fq = json.load(fp)
            it0 = fq.get("itens", [{}])[0] if fq.get("itens") else {}
            print(f"{pid}: status={it0.get('status')}, tentativas={it0.get('tentativa_atual')}, ultimo_erro={it0.get('ultimo_erro')}")

        for pid in pids:
            w = pw_flow.FlowQueueWorker.peek_queue_worker(pid)
            if w:
                w.stop_processing()
    finally:
        pw_flow._criar_imagem_cena_impl = original_impl
        pw_flow._timeout_do_ator = original_timeout_fn
        for pid in pids:
            shutil.rmtree(PROJETOS_DIR / pid, ignore_errors=True)

if __name__ == "__main__":
    run_experiment_b5()
