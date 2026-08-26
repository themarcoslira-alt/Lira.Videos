"""
scripts/migrate_lira_scene_plan_v2.py — Migração Fase 1 (Lira Studio)
======================================================================
Adiciona os campos NARRATIVOS da Fase 1 em todos os lira_scene_plan.json:

  narrative_role      HOOK | AVATAR | BROLL | CTA | CLOSING
  avatar_required     bool (usa @personagem do Flow?)
  broll_query         string | None (query Pexels quando BROLL)
  recommended_duration segundos sugeridos
  action_verb         verbo concreto da cena
  intensity           0-1 (intensidade emocional)
  video_url / broll_url      None (preenchidos na Fase 2)
  broll_status               NOT_STARTED

Puramente ADITIVO: preserva todos os campos existentes (estado, arquivos,
prompts, aprovações). Idempotente: pode rodar quantas vezes quiser.

Uso:
    python scripts/migrate_lira_scene_plan_v2.py                  # todos os projetos
    python scripts/migrate_lira_scene_plan_v2.py --projeto Historia
"""

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import PROJETOS_DIR  # noqa: E402
from services.scene_plan_service import aplicar_classificacao_narrativa_cena  # noqa: E402

NOVOS_CAMPOS = [
    "narrative_role",
    "avatar_required",
    "broll_query",
    "recommended_duration",
    "action_verb",
    "intensity",
    "video_url",
    "broll_url",
    "broll_status",
]


def _salvar_atomicamente(plan_path: Path, plan: dict) -> None:
    tmp = plan_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp), str(plan_path))


def migrar_projeto(projeto_id: str, projetos_dir=None, salvar: bool = True) -> dict | None:
    """Migra um único projeto. Retorna relatório ou None se não houver plano."""
    base = Path(projetos_dir) if projetos_dir else PROJETOS_DIR
    plan_path = base / projeto_id / "lira_scene_plan.json"
    if not plan_path.exists():
        return None
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"projeto": projeto_id, "erro": f"falha ao ler JSON: {e}"}

    cenas = plan.get("cenas") or []
    atualizadas = 0
    for idx, c in enumerate(cenas):
        if not isinstance(c, dict):
            continue
        antes = set(c.keys())
        aplicar_classificacao_narrativa_cena(c, index=idx)
        novos = sorted(set(c.keys()) - antes)
        if novos:
            atualizadas += 1

    if salvar:
        _salvar_atomicamente(plan_path, plan)

    return {
        "projeto": projeto_id,
        "cenas_no_plano": len(cenas),
        "cenas_atualizadas": atualizadas,
        "novos_campos": NOVOS_CAMPOS,
    }


def migrar_todos(projetos_dir=None, apenas: str | None = None) -> list:
    """Migra todos os projetos (ou apenas um) e retorna a lista de relatórios."""
    base = Path(projetos_dir) if projetos_dir else PROJETOS_DIR
    if not base.exists():
        print(f"[ERRO] Diretório de projetos não encontrado: {base}")
        return []

    if apenas:
        alvos = [apenas]
    else:
        alvos = [
            p.name for p in sorted(base.iterdir())
            if p.is_dir() and (p / "lira_scene_plan.json").exists()
        ]

    relatorio = []
    for pid in alvos:
        r = migrar_projeto(pid, projetos_dir=base)
        if r:
            relatorio.append(r)
    return relatorio


def _main() -> list:
    parser = argparse.ArgumentParser(description="Migração Fase 1 do lira_scene_plan.json")
    parser.add_argument("--projeto", default=None, help="Migra apenas um projeto (default: todos)")
    args = parser.parse_args()

    relatorio = migrar_todos(apenas=args.projeto)
    print(f"Projetos migrados: {len(relatorio)}")
    for r in relatorio:
        if "erro" in r:
            print(f"  - {r.get('projeto')}: {r['erro']}")
        else:
            print(f"  - {r.get('projeto')}: {r.get('cenas_no_plano')} cenas, "
                  f"{r.get('cenas_atualizadas')} atualizadas")
    return relatorio


if __name__ == "__main__":
    _main()