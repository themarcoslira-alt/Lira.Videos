import os
import sys
import time
import socket
import subprocess
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import services.playwright_flow as pw_flow

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def run_concurrent():
    port = get_free_port()
    user_data_dir = tempfile.mkdtemp(prefix="chrome_cdp_b2_conc_")
    cmd = [
        CHROME_PATH,
        f"--remote-debugging-port={port}",
        "--headless=new",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank"
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2.0)

    pw_flow.ensure_chrome_cdp = lambda *args, **kwargs: (True, "OK CDP teste")
    worker = pw_flow.FlowQueueWorker.get_worker()
    worker.port = port
    worker._eh_aba_flow_valida = lambda url: True

    ev_t1_pronto = threading.Event()
    ev_t2_terminou = threading.Event()
    res_t2 = {}

    def t1_viva():
        print("[Thread 1] Criando sessão e mantendo-se VIVA...")
        worker._iniciar_sessao_thread()
        ev_t1_pronto.set()
        ev_t2_terminou.wait(timeout=10.0)
        print("[Thread 1] Encerrando...")

    def t2_rouba_sessao():
        ev_t1_pronto.wait(timeout=5.0)
        print("[Thread 2] Tentando chamar criar_imagem_cena enquanto Thread 1 ESTÁ VIVA...")
        try:
            # worker.page pertence à Thread 1 viva
            # Thread 2 chama _check_is_active -> _iniciar_sessao_thread -> _encerrar_sessao()
            r = worker._iniciar_sessao_thread()
            res_t2["status"] = "SUCESSO"
            res_t2["output"] = str(r)
        except Exception as e:
            res_t2["status"] = "ERRO"
            res_t2["output"] = f"{type(e).__name__}: {e}"
        finally:
            ev_t2_terminou.set()

    t1 = threading.Thread(target=t1_viva, name="Thread-1-Viva")
    t2 = threading.Thread(target=t2_rouba_sessao, name="Thread-2-Invade")
    t1.start()
    t2.start()
    t2.join()
    t1.join()

    print("\n--- RESULTADO CONCORRÊNCIA ENTRE THREADS VIVAS ---")
    print(f"Thread 2 resultado: {res_t2}")

    proc.terminate()
    try:
        proc.wait(timeout=2.0)
    except Exception:
        proc.kill()

if __name__ == "__main__":
    run_concurrent()
