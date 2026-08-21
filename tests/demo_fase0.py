"""
demo_fase0.py — Demonstração obrigatória da Fase 0 (projeto real).

Fluxo: roteiro → cena → Visual Profile → Visual Plan → locks resolvidos →
queries → scene_plan.json (sem executar renderização).

Uso:
    .venv\\Scripts\\python.exe tests\\demo_fase0.py [--provider local|deepseek]
"""
import json
import sys
import time
from pathlib import Path

BASE = Path(r"C:\ultracut3")
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from config import PROJETOS_DIR  # noqa: E402
from services.event_logger import log_event  # noqa: E402
from services.llm_providers import criar_provider  # noqa: E402
from services.query_engine import QueryGenerator  # noqa: E402
from services.scene_builder import gerar_cenas  # noqa: E402
from services.scene_plan_schema import nova_scene_plan, validar_scene_plan  # noqa: E402
from services.scene_store import SceneStore  # noqa: E402
from services.visual_planner import VisualPlanner  # noqa: E402
from services.visual_profile import VisualProfile, salvar_visual_profile  # noqa: E402

PROJETO = "Why His Lawn Is Greener - He Checks For This Every Week"


def main():
    inicio = time.time()
    project_dir = PROJETOS_DIR / PROJETO
    if not project_dir.exists():
        print(f"[ERRO] Projeto não encontrado: {project_dir}")
        return 1

    print(f"=== Fase 0 — Direção Visual: {PROJETO} ===")

    # 1) Cenas (determinístico) — cria cenas.json se ainda não existir
    cenas_file = project_dir / "cenas.json"
    if cenas_file.exists():
        cenas = json.loads(cenas_file.read_text(encoding="utf-8"))
        print(f"[1] cenas.json existente: {len(cenas)} cenas")
    else:
        r = gerar_cenas(PROJETO)
        if not r.get("success"):
            print(f"[ERRO] gerar_cenas falhou: {r.get('error')}")
            return 1
        cenas = r["cenas"]
        print(f"[1] cenas geradas pelo scene_builder: {len(cenas)}")

    # 2) Visual Profile (criar + persistir)
    profile = VisualProfile.from_preset("photorealistic_cinematic")
    destino_perfil = salvar_visual_profile(project_dir, profile)
    print(f"[2] VisualProfile salvo: {profile.name}")
    print(f"    arquivo: {destino_perfil}")
    print(f"    style_lock: {profile.style_lock[:80]}...")

    # 3) Monta o scene_plan (skeleton) a partir das cenas
    store = SceneStore(PROJETO)
    scenes = store.criar_scenes_de_cenas(cenas)
    plan = nova_scene_plan(PROJETO, PROJETO, profile.to_dict())
    plan["scenes"] = scenes
    print(f"[3] skeleton do scene_plan: {len(scenes)} cenas")

    # 4) Visual Planner + locks resolvidos + QueryGenerator
    provider = None
    if "--provider" in sys.argv:
        idx = sys.argv.index("--provider")
        provider = criar_provider(sys.argv[idx + 1])
        print(f"[4] provider LLM: {provider.name} (modelo: {provider.model or 'default'})")
    else:
        print("[4] provider: local (determinístico, sem rede)")

    planner = VisualPlanner()
    qg = QueryGenerator()
    planejadas = 0
    for i, sc in enumerate(scenes):
        prev = scenes[i - 1] if i > 0 else None
        r = planner.planejar_cena(sc, visual_profile=profile, previous_scene=prev, provider=provider)
        if r["success"]:
            sc["visual_plan"] = r["visual_plan"]
            sc["locks"] = r["locks"]
            sc["media_plan"] = qg.generate_for_provider(
                sc["visual_plan"], provider="pexels", narration_text=sc["narration"]["text"])
            sc["status"]["planning"] = "ready"
            planejadas += 1
        else:
            sc["locks"] = r["locks"]
            print(f"    aviso cena {sc['id']}: {r['error']}")

    print(f"[4] cenas planejadas: {planejadas}/{len(scenes)}")

    # 5) Valida e salva scene_plan.json
    erros = validar_scene_plan(plan)
    if erros:
        print(f"[ERRO] scene_plan inválido: {erros[:5]}")
        return 1
    if not store.save(plan):
        print("[ERRO] falha ao salvar scene_plan.json")
        return 1

    # 6) Resumo
    primeira = scenes[0]
    print("[5] scene_plan.json salvo em:")
    print(f"    {store.path()}")
    print(f"    cenas: {len(scenes)} | planning ready: {planejadas}")
    print(f"    status da cena 1: {primeira['status']}")
    print(f"    locks resolvidos cena 1: {sorted(primeira['locks'].keys())}")
    print(f"    media_plan cena 1: {primeira['media_plan']}")
    print(f"    tempo total: {round(time.time() - inicio, 2)}s")
    print("[OK] Demonstração Fase 0 concluída (sem renderização).")
    log_event("SCENE_PLAN",
              f"Demo Fase 0 concluída para {PROJETO}: {planejadas}/{len(scenes)} cenas planejadas",
              level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
