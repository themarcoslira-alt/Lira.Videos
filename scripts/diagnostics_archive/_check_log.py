# --- raiz ATUAL do repositorio (antes: C:\ultracut3 hardcoded) ---
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
import json
from pathlib import Path

log = Path(str(ROOT_DIR / "logs" / "events.jsonl"))
eventos = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]

ok = [e for e in eventos if e.get("category") == "CLAUDE" and "OK" in e.get("message", "")]
print(f"Total OK: {len(ok)}")
print(f"Mais recente: {ok[-1]['ts'][11:19]} {ok[-1]['message'][:60]}" if ok else "nenhum")

# Verifica output file
out = Path(str(ROOT_DIR / "_output_final.txt"))
if out.exists():
    print(f"\nOutput file: {out.stat().st_size} bytes")
    content = out.read_text(encoding="utf-8", errors="replace")
    if content.strip():
        print(content[:2000])
    else:
        print("(vazio - ainda rodando)")
else:
    print("(arquivo nao existe)")