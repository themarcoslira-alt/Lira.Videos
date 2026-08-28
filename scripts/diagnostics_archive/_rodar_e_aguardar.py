import subprocess, sys, time, os

# Dispara o teste em background
script = r"C:\ultracut3\_comando3.py"
out = r"C:\ultracut3\_output_com3.txt"
python = r"C:\ultracut3\.venv\Scripts\python.exe"

with open(out, "w") as f:
    proc = subprocess.Popen(
        [python, script],
        cwd=r"C:\ultracut3",
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