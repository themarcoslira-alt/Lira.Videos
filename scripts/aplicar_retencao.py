"""Aplica a Estratégia de Retenção + Avatar Inteligente nos projetos (com backup).

Uso:  python scripts/aplicar_retencao.py [PROJETO ...]
(omita para rodar em PLANTA, Boy, tsta — os projetos-piloto do estudo)
"""
import json
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
PROJETOS = BASE / "projetos"
BACKUPS = BASE / "_backups_antigos"
sys.path.insert(0, str(BASE))

from services.scene_plan_service import carregar_scene_plan, salvar_scene_plan  # noqa: E402
from services.retention_analyzer_service import (  # noqa: E402
    RetentionAnalyzer,
    aplicar_estrategia_retencao,
)

ALVOS = [a.strip() for a in sys.argv[1:]] or ["PLANTA", "Boy", "tsta"]


def _meta(proj: str) -> dict:
    try:
        return json.loads((PROJETOS / proj / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _character_ref(proj: str, cenas) -> str:
    meta = _meta(proj)
    nome = str(meta.get("nome_personagem") or "").strip()
    if nome:
        return nome if nome.startswith("@") else f"@{nome}"
    for c in cenas:
        ref = str(c.get("character_ref") or "").strip()
        if ref:
            return ref
    return ""


def _duracao(cenas) -> float:
    fim = 0.0
    for c in cenas:
        t0 = c.get("tempo_inicio")
        t1 = c.get("tempo_fim")
        try:
            if t0 is not None and t1 is not None:
                fim = max(fim, float(t1))
            elif t0 is not None:
                fim = max(fim, float(t0) + float(c.get("duracao") or 5.0))
        except (TypeError, ValueError):
            pass
    return fim


def main() -> int:
    BACKUPS.mkdir(parents=True, exist_ok=True)
    relatorio = {}
    for proj in ALVOS:
        plan = carregar_scene_plan(proj)
        if not plan or not plan.get("cenas"):
            relatorio[proj] = {"erro": "scene_plan ausente"}
            print(f"[{proj}] sem scene_plan — pulando")
            continue

        cenas = plan["cenas"]
        duracao = _duracao(cenas)
        ref = _character_ref(proj, cenas)

        antes = RetentionAnalyzer.analyze_cenas([dict(c) for c in cenas])

        # backup
        backup = BACKUPS / f"lira_scene_plan_{proj}_pre_retencao.json"
        shutil.copy2(str(PROJETOS / proj / "lira_scene_plan.json"), str(backup))

        aplicar_estrategia_retencao(cenas, video_duration=duracao, character_ref=ref)
        ok = salvar_scene_plan(proj, plan)

        depois_plan = carregar_scene_plan(proj)
        depois = RetentionAnalyzer.analyze_scene_plan(depois_plan) if depois_plan else {}

        relatorio[proj] = {
            "backup": str(backup),
            "salvo": ok,
            "duracao_total_s": round(duracao, 1),
            "character_ref": ref,
            "antes": {
                "avatar_scenes": antes.get("avatar_scenes"),
                "avatar_scenes_percent": antes.get("avatar_scenes_percent"),
                "maior_gap_broll_s": antes.get("maior_gap_broll_s"),
                "score_retencao": antes.get("score_retencao"),
                "problemas": antes.get("problemas", [])[:6],
            },
            "depois": {
                "avatar_scenes": depois.get("avatar_scenes"),
                "avatar_scenes_percent": depois.get("avatar_scenes_percent"),
                "roles": depois.get("roles"),
                "checkpoints": [
                    {k: cp.get(k) for k in ("scene_id", "timestamp", "role", "intervalo_desde_anterior_s")}
                    for cp in depois.get("avatar_checkpoints", [])
                ],
                "maior_gap_broll_s": depois.get("maior_gap_broll_s"),
                "score_retencao": depois.get("score_retencao"),
                "validacoes": depois.get("validacoes"),
                "problemas": depois.get("problemas", []),
            },
        }
        print(f"[{proj}] salvo={ok} | avatares {antes.get('avatar_scenes')} -> "
              f"{depois.get('avatar_scenes')} | score {antes.get('score_retencao')} -> "
              f"{depois.get('score_retencao')}")

    saida = BASE / "logs" / "retencao_relatorio.json"
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(json.dumps(relatorio, ensure_ascii=False, indent=1), encoding="utf-8")
    print("RELATÓRIO:", saida)
    return 0


if __name__ == "__main__":
    sys.exit(main())
