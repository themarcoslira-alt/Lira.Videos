# --- raiz ATUAL do repositorio (antes: C:\ultracut3 hardcoded) ---
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
import re

lines = open(str(ROOT_DIR / "services" / "pipeline_service.py"), encoding="utf-8").readlines()
print("ANTES - linhas 415-425:")
for i in range(414, 425):
    if i < len(lines):
        print(f"{i+1}:{lines[i]}", end="")

# Aplica a correção: comenta linha 420
linha_420 = 419  # 0-indexed
if "gerar_queries" in lines[linha_420]:
    if not lines[linha_420].strip().startswith("#"):
        lines[linha_420] = "        # results[\"gerar_queries\"] = self.gerar_queries()  # DESATIVADO: media_search.py le storyboard.json direto\n"

with open(str(ROOT_DIR / "services" / "pipeline_service.py"), "w", encoding="utf-8") as f:
    f.writelines(lines)

print("\nDEPOIS - linhas 415-425:")
for i in range(414, 425):
    if i < len(lines):
        print(f"{i+1}:{lines[i]}", end="")