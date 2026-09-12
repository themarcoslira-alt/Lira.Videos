# --- raiz ATUAL do repositorio (antes: C:\ultracut3 hardcoded) ---
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent
import subprocess, json, os, sys

script = str(ROOT_DIR / "_final_output.py")
outfile = str(ROOT_DIR / "_resultado_final.json")

if os.path.exists(outfile):
    os.remove(outfile)

try:
    r = subprocess.run(
        [str(ROOT_DIR / ".venv" / "Scripts" / "python.exe"), script],
        cwd=str(ROOT_DIR),
        capture_output=True, text=True, timeout=300
    )
    print("STDOUT:", r.stdout[:500])
    if r.stderr:
        print("STDERR:", r.stderr[:500])
except subprocess.TimeoutExpired:
    print("TIMEOUT apos 300s")
except Exception as e:
    print(f"ERRO: {e}")

if os.path.exists(outfile):
    data = json.loads(open(outfile, encoding="utf-8").read())
    print()
    print("camada:", data.get("camada"))
    print("confiavel:", data.get("confiavel"))
    print("claude_ok:", data.get("claude_ok"))
    print("local_fallback:", data.get("local_fallback"))
    print()
    print(json.dumps(data.get("cena3"), indent=2, ensure_ascii=False))
else:
    print("Arquivo de resultado nao criado")