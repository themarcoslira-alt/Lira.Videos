import time, os, json, sys

target = "C:/ultracut3/teste_output.json"
fim = "C:/ultracut3/teste_timestamps.txt"

print(f"Inicio: {time.strftime('%H:%M:%S')}", flush=True)

# Aguarda ate 8 minutos
for i in range(96):
    if os.path.exists(target) and os.path.getsize(target) > 0:
        break
    time.sleep(5)
    if i % 12 == 0:
        print(f"  [{i*5}s] aguardando...", flush=True)

print(f"Fim: {time.strftime('%H:%M:%S')}", flush=True)
print(f"Arquivo existe: {os.path.exists(target)}", flush=True)
print(f"Tamanho: {os.path.getsize(target)} bytes" if os.path.exists(target) else "0", flush=True)

if os.path.exists(target):
    with open(target, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"\ncamada: {data.get('camada')}", flush=True)
    print(f"confiavel: {data.get('camada_confiavel')}", flush=True)
    print(f"claude_ok: {data.get('claude_ok')}", flush=True)
    print(f"local_fallback: {data.get('local_fallback')}", flush=True)
    print(f"\nCENA 3:", flush=True)
    cena3 = [s for s in data.get("storyboard", []) if s.get("id") == 3]
    if cena3:
        print(json.dumps(cena3[0], indent=2, ensure_ascii=False), flush=True)
    else:
        print("(nao encontrada)", flush=True)

if os.path.exists(fim):
    print("\nTimestamps:", flush=True)
    for l in open(fim):
        print(l.strip(), flush=True)