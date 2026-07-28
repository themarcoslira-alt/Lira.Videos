import sys
sys.path.insert(0, "C:/ultracut3")
from services.broll_director import gerar_storyboard
import json

r = gerar_storyboard("2026", usar_claude=True)
with open("C:/ultracut3/teste4_output.json", "w", encoding="utf-8") as f:
    json.dump(r, f, indent=2, ensure_ascii=False)
print(json.dumps({"camada": r.get("camada"), "confiavel": r.get("camada_confiavel"), "claude_ok": r.get("claude_ok"), "local_fallback": r.get("local_fallback")}, ensure_ascii=False))