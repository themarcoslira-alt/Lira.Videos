import sys, json
sys.path.insert(0, "C:/ultracut3")
from services.broll_director import gerar_storyboard

r = gerar_storyboard("2026", usar_claude=True)
result = {}
result["camada"] = r.get("camada")
result["confiavel"] = r.get("camada_confiavel")
result["claude_ok"] = r.get("claude_ok")
result["local_fallback"] = r.get("local_fallback")
cena3 = [s for s in r["storyboard"] if s["id"] == 3]
result["cena3"] = cena3[0] if cena3 else None
with open("C:/ultracut3/_resultado_final.json", "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, ensure_ascii=False)
print("OK")