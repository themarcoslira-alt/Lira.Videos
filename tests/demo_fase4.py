"""Demo BLOCO 4 — Storyboard Builder no projeto real (beats visuais + mídia)."""
import sys
from pathlib import Path

BASE = Path(r"C:\ultracut3")
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from services.storyboard_builder import construir_storyboard, linhas_para_prompt  # noqa: E402

PROJETO = "Why His Lawn Is Greener - He Checks For This Every Week"


def main():
    r = construir_storyboard(PROJETO)
    if not r.get("success"):
        print("[ERRO]", r.get("error"))
        return 1
    print(f"storyboard: {r['arquivo']}")
    print(f"cenas: {r['cenas_count']} | video: {r['video_count']} | photo: {r['photo_count']}")
    print(f"usou word_timestamps: {r['usou_word_timestamps']} "
          f"({'SIM' if r['usou_word_timestamps'] else 'NAO (fallback proporcional documentado)'})")
    print("\nPrimeiras 6 cenas:")
    for s in r["scenes"][:6]:
        print(f"  #{s['scene_id']} [{s['start']}-{s['end']}] {s['media_type'].upper():5} {s['text'][:70]}")
    print("\nLinhas de prompt (BLOCO 4.6):")
    for linha in linhas_para_prompt(PROJETO)[:6]:
        print("  ", linha)
    return 0


if __name__ == "__main__":
    sys.exit(main())
