import sys
sys.path.insert(0, "C:/ultracut3")
from services.broll_director import gerar_storyboard
import json

r = gerar_storyboard("2026", usar_claude=True)
with open("C:/ultracut3/teste3_output.json", "w", encoding="utf-8") as f:
    json.dump(r, f, indent=2, ensure_ascii=False)
print("OK")