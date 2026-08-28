import sys, json
sys.path.insert(0, "C:/ultracut3")
from services.broll_director import gerar_storyboard

r = gerar_storyboard("2026", usar_claude=True)
print("camada:", r.get("camada"))
print("confiavel:", r.get("camada_confiavel"))
print("claude_ok:", r.get("claude_ok"))
print("local_fallback:", r.get("local_fallback"))
print(json.dumps([s for s in r["storyboard"] if s["id"] == 3][0], indent=2, ensure_ascii=False))