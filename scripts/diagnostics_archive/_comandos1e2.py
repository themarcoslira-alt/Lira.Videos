# COMANDO 1: linhas com "content" + contexto 3 antes/3 depois
# COMANDO 2: linhas com "max_tokens"
import sys
sys.path.insert(0, "C:/ultracut3")

lines = open("C:/ultracut3/services/broll_director.py", encoding="utf-8").readlines()

print("=" * 70)
print("COMANDO 1 — linhas com 'content' + contexto 3 antes/3 depois")
print("=" * 70)
for i, l in enumerate(lines, 1):
    if "content" in l:
        start = max(0, i - 4)
        end = min(len(lines), i + 3)
        print(f"\n--- Linhas {start+1}-{end} (match na linha {i}) ---")
        for j in range(start, end):
            m = ">>>" if j == i - 1 else "   "
            print(f"{m}{j+1:4d}|{lines[j]}", end="")

print()
print("=" * 70)
print("COMANDO 2 — linhas com 'max_tokens'")
print("=" * 70)
for i, l in enumerate(lines, 1):
    if "max_tokens" in l:
        print(f"Linha {i}: {l}", end="")