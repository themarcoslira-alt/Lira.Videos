import re, sys

# COMANDO 1
lines = open("C:/ultracut3/services/pipeline_service.py", encoding="utf-8").readlines()
print("=" * 70)
print("COMANDO 1 — pipeline_service.py: ordem das chamadas")
print("=" * 70)
for i, l in enumerate(lines, 1):
    if re.search(r"def executar_tudo|def run_all|def pipeline_completo|self\.gerar_storyboard\(|self\.gerar_queries\(|self\.buscar_midias", l):
        # Mostra contexto
        start = max(0, i - 2)
        end = min(len(lines), i + 1)
        for j in range(start, end):
            print(f"  {j+1}:{lines[j]}", end="")
        print()

# COMANDO 2
print("\n" + "=" * 70)
print("COMANDO 2 — media_search.py: queries.json | storyboard.json | midias_encontradas")
print("=" * 70)
lines2 = open("C:/ultracut3/services/media_search.py", encoding="utf-8").readlines()
for i, l in enumerate(lines2, 1):
    if "queries.json" in l or "storyboard.json" in l or "midias_encontradas" in l:
        print(f"  {i}:{l}", end="")

# COMANDO 3
print("\n" + "=" * 70)
print("COMANDO 3 — gui.py: midias_encontradas | _cenas_data")
print("=" * 70)
lines3 = open("C:/ultracut3/gui.py", encoding="utf-8").readlines()
for i, l in enumerate(lines3, 1):
    if "midias_encontradas" in l or "_cenas_data" in l:
        print(f"  {i}:{l}", end="")