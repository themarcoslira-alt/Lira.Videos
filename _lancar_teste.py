import sys, json, subprocess, time, os

# Lança o script em background e aguarda
script = r"C:\ultracut3\_rodar_teste.py"
out = r"C:\ultracut3\_output_final.txt"
python = r"C:\ultracut3\.venv\Scripts\python.exe"

proc = subprocess.Popen(
    [python, script],
    cwd=r"C:\ultracut3",
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