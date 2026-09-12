# --- raiz ATUAL do repositorio (antes: C:\ultracut3 hardcoded) ---
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
import sys
sys.path.insert(0, str(ROOT_DIR))
from services.broll_director import gerar_storyboard
import json

r = gerar_storyboard("2026", usar_claude=True)
with open(str(ROOT_DIR / "teste4_output.json"), "w", encoding="utf-8") as f:
    json.dump(r, f, indent=2, ensure_ascii=False)
print(json.dumps({"camada": r.get("camada"), "confiavel": r.get("camada_confiavel"), "claude_ok": r.get("claude_ok"), "local_fallback": r.get("local_fallback")}, ensure_ascii=False))