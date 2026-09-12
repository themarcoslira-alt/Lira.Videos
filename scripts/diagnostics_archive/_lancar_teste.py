# --- raiz ATUAL do repositorio (antes: C:\ultracut3 hardcoded) ---
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
import sys, json, subprocess, time, os

# Lança o script em background e aguarda
script = str(ROOT_DIR / "_rodar_teste.py")
out = str(ROOT_DIR / "_output_final.txt")
python = str(ROOT_DIR / ".venv" / "Scripts" / "python.exe")

proc = subprocess.Popen(
    [python, script],
    cwd=str(ROOT_DIR),
    stdout=open(out, "w"),
    stderr=subprocess.STDOUT
)

# Aguarda até 5 minutos
for i in range(60):
    time.sleep(5)
    if proc.poll() is not None:
        break
    # Mostra progresso
    try:
        with open(out) as f:
            size = len(f.read())
        print(f"[{i*5}s] process running, output {size} bytes")
    except:
        pass

print(f"\nProcesso terminou com codigo {proc.returncode}")
print("=" * 60)
with open(out, "r", encoding="utf-8") as f:
    print(f.read())