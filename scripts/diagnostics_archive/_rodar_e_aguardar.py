# --- raiz ATUAL do repositorio (antes: C:\ultracut3 hardcoded) ---
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
import subprocess, sys, time, os

# Dispara o teste em background
script = str(ROOT_DIR / "_comando3.py")
out = str(ROOT_DIR / "_output_com3.txt")
python = str(ROOT_DIR / ".venv" / "Scripts" / "python.exe")

with open(out, "w") as f:
    proc = subprocess.Popen(
        [python, script],
        cwd=str(ROOT_DIR),
        stdout=f,
        stderr=subprocess.STDOUT
    )

# Aguarda ate 5 minutos
for i in range(60):
    time.sleep(5)
    if proc.poll() is not None:
        break

# Le o resultado
with open(out, "r", encoding="utf-8", errors="replace") as f:
    conteudo = f.read()

print(conteudo, end="")