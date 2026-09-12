# --- raiz ATUAL do repositorio (antes: C:\ultracut3 hardcoded) ---
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent
import json, os

d = json.load(open(str(ROOT_DIR / "projetos" / "2026" / "storyboard.json"), encoding="utf-8"))
print("Total cenas:", len(d))
print("Campos cena[0]:", list(d[0].keys()))
c3 = d[2] if len(d) > 2 else d[0]
print("Cena 3:")
print("  id:", c3.get("id"))
print("  scene_type:", c3.get("scene_type", "?"))
print("  keywords:", c3.get("keywords", []))
print("  search_queries:", c3.get("search_queries", [])[:3])
print("  subject:", c3.get("subject", "(sem subject)"))
print("  energy:", c3.get("energy", ""))
print("  visual_intent:", c3.get("visual_intent", ""))

# PASSO 2 - verificar codigo GUI
lines = open(str(ROOT_DIR / "gui.py"), encoding="utf-8").readlines()
print("\nGUI _cenas_concluidas:")
for i in range(1103, 1116):
    print(f"  {i+1}:{lines[i]}", end="")

# PASSO 3 - grep gerar_queries
print("\npipeline_service.py gerar_queries:")
lines2 = open(str(ROOT_DIR / "services" / "pipeline_service.py"), encoding="utf-8").readlines()
for i, l in enumerate(lines2, 1):
    if "gerar_queries" in l:
        print(f"  {i}:{l}", end="")