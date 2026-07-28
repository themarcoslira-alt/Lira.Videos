import json

eventos = [json.loads(l) for l in open("C:/ultracut3/logs/events.jsonl", "r", encoding="utf-8") if l.strip()]
ultimos = [e for e in eventos if e.get("category") == "CLAUDE" and "OK" in e.get("message", "")]
print(f"Total Claude OK no log: {len(ultimos)}")
print(f"Ultimo: {ultimos[-1]['ts'][11:19]} {ultimos[-1]['message'][:100]}" if ultimos else "nenhum")

# Verifica se a execucao mais recente usou Claude ou local
final = [e for e in eventos if "Storyboard finalizado" in e.get("message", "")]
if final:
    print(f"Ultimo finalizado: {final[-1]['message'][:120]}")