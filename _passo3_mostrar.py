import re

lines = open("C:/ultracut3/services/media_search.py", encoding="utf-8").readlines()

print("=" * 70)
print("TECHO 1 — _gerar_queries_frescas (linhas 92-134):")
print("=" * 70)
for i in range(91, 135):
    if i < len(lines):
        print(f"{i+1}:{lines[i]}", end="")

print("\n" + "=" * 70)
print("TECHO 2 — contexto QueryPool (linhas 280-325):")
print("=" * 70)
for i in range(279, 326):
    if i < len(lines):
        print(f"{i+1}:{lines[i]}", end="")