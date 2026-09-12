# --- raiz ATUAL do repositorio (antes: C:\ultracut3 hardcoded) ---
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
import sys, json
sys.path.insert(0, str(ROOT_DIR))
from services.broll_director import gerar_storyboard

r = gerar_storyboard("2026", usar_claude=True)
result = []
result.append("camada: " + str(r.get("camada")))
result.append("confiavel: " + str(r.get("camada_confiavel")))
result.append("claude_ok: " + str(r.get("claude_ok")))
result.append("local_fallback: " + str(r.get("local_fallback")))
cena3 = [s for s in r["storyboard"] if s["id"] == 3]
result.append(json.dumps(cena3[0], indent=2, ensure_ascii=False))

# Save to file at the very end
with open(str(ROOT_DIR / "_resultado_final.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(result))

print("\n".join(result))