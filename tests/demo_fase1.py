"""
demo_fase1.py — Demonstração Fase 1 (Prompt Engine) com o projeto real.

Fluxo: scene_plan.json → VisualProfile → PromptEngine → image_prompt /
animation_prompt / negative_prompt → SceneStore.set_prompt → scene_plan.json.

Seleciona cenas representativas: humano, planta, inseto, ambiental, close-up,
continuidade. Não renderiza vídeo.
"""
import sys
from pathlib import Path

BASE = Path(r"C:\ultracut3")
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from config import PROJETOS_DIR  # noqa: E402
from services.event_logger import log_event  # noqa: E402
from services.prompt_engine import PromptEngine  # noqa: E402
from services.scene_store import SceneStore  # noqa: E402
from services.visual_profile import carregar_visual_profile  # noqa: E402

PROJETO = "Why His Lawn Is Greener - He Checks For This Every Week"

PALAVRAS = {
    "humano": ("person", "people", "man", "woman", "gardener", "farmer",
               "hands", "his", "her", "he", "she", "kneeling"),
    "planta": ("plant", "grass", "lawn", "leaf", "leaves", "dandelion",
               "flower", "root", "weed", "garden", "tree", "seed", "stem"),
    "inseto": ("insect", "bug", "pest", "worm", "grub", "beetle",
               "caterpillar", "larvae", "moth", "ant"),
    "ambiental": ("lawn", "yard", "backyard", "soil", "ground",
                  "environment", "season", "weather", "morning"),
}


def _texto_cena(cena):
    vp = cena.get("visual_plan") or {}
    narr = cena.get("narration") or {}
    partes = [narr.get("text", "")]
    for k in ("subject", "action", "environment", "visual_intent", "continuity"):
        v = vp.get(k, "")
        if isinstance(v, str):
            partes.append(v)
    return " ".join(partes).lower()


def _selecionar(cenas):
    import re

    escolhidas = {}
    for cena in cenas:
        texto = _texto_cena(cena)
        vp = cena.get("visual_plan") or {}
        for nome, palavras in PALAVRAS.items():
            if nome in escolhidas:
                continue
            if any(re.search(rf"\b{re.escape(p)}\b", texto) for p in palavras):
                escolhidas[nome] = cena
        if "closeup" not in escolhidas:
            shot = str(vp.get("shot", "")).lower()
            intent = str(vp.get("visual_intent", "")).lower()
            if "close" in shot or "macro" in intent or ("close" in texto and "look" in texto):
                escolhidas["closeup"] = cena
    # continuidade: segunda cena (depende da anterior)
    if len(cenas) >= 2:
        escolhidas["continuidade"] = cenas[1]
    # segurança: preenche categorias ausentes com as primeiras cenas
    extras = iter(cenas)
    for nome in ("humano", "planta", "inseto", "ambiental", "closeup"):
        if nome not in escolhidas:
            for c in extras:
                if c not in escolhidas.values():
                    escolhidas[nome] = c
                    break
    return escolhidas


def main():
    store = SceneStore(PROJETO)
    plan = store.load()
    if not plan:
        print("[ERRO] scene_plan.json não encontrado. Execute tests/demo_fase0.py antes.")
        return 1
    scenes = plan["scenes"]
    profile = carregar_visual_profile(PROJETOS_DIR / PROJETO)
    if profile is None:
        print("[AVISO] visual_profile.json ausente — usando locks resolvidos das cenas.")

    engine = PromptEngine()
    ok = 0
    falhas = []
    for i, sc in enumerate(scenes):
        prev = scenes[i - 1] if i > 0 else None
        r = engine.generate(sc, visual_profile=profile, previous_scene=prev)
        if r["success"] and store.set_prompt(sc["id"], r):
            ok += 1
        else:
            falhas.append(sc["id"])
    print(f"prompts gerados e persistidos: {ok}/{len(scenes)}")
    if falhas:
        print("falhas:", falhas[:10])

    print("\n=== Exemplos representativos (diretor visual) ===")
    escolhidas = _selecionar(scenes)
    for nome, cena in escolhidas.items():
        prev = None
        if nome == "continuidade":
            idx = scenes.index(cena)
            prev = scenes[idx - 1] if idx > 0 else None
        r = engine.generate(cena, visual_profile=profile, previous_scene=prev)
        print(f"\n##### {nome.upper()} — {cena['id']}")
        print("NARRAÇÃO:", (cena.get("narration") or {}).get("text", "")[:160])
        print("--- IMAGE PROMPT ---")
        print(r["image_prompt"])
        print("--- ANIMATION PROMPT ---")
        print(r["animation_prompt"])
        print("--- NEGATIVE PROMPT ---")
        print(r["negative_prompt"])

    log_event("PROMPT", f"Demo Fase 1 concluída para {PROJETO}: {ok}/{len(scenes)} prompts persistidos",
              level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
