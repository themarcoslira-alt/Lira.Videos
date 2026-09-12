# COMANDO 1 e 2
# --- raiz ATUAL do repositorio (antes: C:\ultracut3 hardcoded) ---
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
import sys
sys.path.insert(0, str(ROOT_DIR))

print("=" * 70)
print("COMANDO 1 — gui.py linhas 1099-1118:")
print("=" * 70)
lines = open(str(ROOT_DIR / "gui.py"), encoding="utf-8").readlines()
for i in range(1098, 1118):
    if i < len(lines):
        print(f"{i+1}:{lines[i]}", end="")

print()
print("=" * 70)
print("COMANDO 2 — media_search.py linhas 85-119:")
print("=" * 70)
lines2 = open(str(ROOT_DIR / "services" / "media_search.py"), encoding="utf-8").readlines()
for i in range(84, 120):
    if i < len(lines2):
        print(f"{i+1}:{lines2[i]}", end="")