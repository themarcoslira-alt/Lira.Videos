import sys, json
sys.path.insert(0, "C:/ultracut3")
from services.broll_director import gerar_storyboard

# Usando Claude=False para testar fallback local rapidamente
r = gerar_storyboard("2026", usar_claude=False)
result = {
    "camada": r.get("camada"),
    "confiavel": r.get("camada_confiavel"),
    "claude_ok": r.get("claude_ok"),
    "local_fallback": r.get("local_fallback"),
    "storyboard": r.get("storyboard", [])
}
with open("C:/ultracut3/teste4_output.json", "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, ensure_ascii=False)

# Print stats
print(f"camada: {result['camada']}")
print(f"confiavel: {result['confiavel']}")
print(f"claude_ok: {result['claude_ok']}")
print(f"local_fallback: {result['local_fallback']}")
c3 = [s for s in result["storyboard"] if s["id"] == 3]
if c3:
    print(json.dumps(c3[0], indent=2, ensure_ascii=False))