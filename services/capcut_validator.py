"""
services/capcut_validator.py — Validação Pré-CapCut (Lira Studio v0.3.5+)
=======================================================================
Camada fina SOBRE services/media_standard.validar_pre_capcut() (v0.3.0+),
que já verifica a sincronização das 3 fontes:

  a) lira_scene_plan.json   -> campo "arquivo_midia"
  b) draft_content.json     -> campo "path" = "<draft>/<nome_arquivo>"
  c) Arquivo físico em disco -> <projeto>/imagens/<nome_arquivo>

Este módulo ADICIONA a validação do ciclo narrativo:
  - tipo_cena/efeito/posicao_ciclo presentes e válidos;
  - posicao_ciclo respeita a sequência 1→2→3→1→2→3...;
  - nunca 2 avatar_intro consecutivos.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any

from config import PROJETOS_DIR
from services.media_standard import validar_pre_capcut as _validar_3_fontes
from services.scene_schema import validar_ciclo


def format_mmss(sec: float) -> str:
    sec = max(0.0, float(sec or 0))
    return f"{int(sec // 60):02d}:{int(sec % 60):02d}"


def _carregar_plan(projeto_id: str):
    try:
        plan = json.loads(
            (PROJETOS_DIR / projeto_id / "lira_scene_plan.json")
            .read_text(encoding="utf-8"))
        return plan
    except Exception:
        return None


def _carregar_draft(projeto_id: str):
    # Caminhos possíveis do draft (pasta do projeto ou CapCut User Data)
    candidatos = [
        PROJETOS_DIR / projeto_id / "draft_content.json",
        PROJETOS_DIR / projeto_id / "capcut" / "draft_content.json",
        Path(os.path.expandvars(
            r"%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft"
        )) / projeto_id / "draft_content.json",
    ]
    for p in candidatos:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
    return None


def validar_pre_capcut(projeto_id: str) -> Dict[str, Any]:
    """Valida o projeto antes de exportar para o CapCut.

    1. 3 fontes sincronizadas (delega a media_standard);
    2. ciclo narrativo íntegro (scene_schema.validar_ciclo);
    3. draft aponta para arquivos canônicos existentes.

    Retorna {"ok", "msg", "total", "com_media", "sem_media", "ciclo_erros"}.
    """
    projeto_id = str(projeto_id or "")
    if not projeto_id:
        return {"ok": False, "msg": "projeto_id obrigatório"}

    # 1. 3 fontes sincronizadas (base)
    base = _validar_3_fontes(projeto_id)
    plan = _carregar_plan(projeto_id)
    cenas = (plan or {}).get("cenas", []) or []

    # 2. ciclo narrativo
    ciclo_erros = validar_ciclo(cenas)

    # 3. draft aponta para arquivo existente
    draft = _carregar_draft(projeto_id)
    draft_ok = True
    if draft is not None:
        videos = (draft.get("materials") or {}).get("videos") or []
        paths = [str(v.get("path", "")) for v in videos]
        for c in cenas:
            arq = c.get("arquivo_midia") or ""
            nome = os.path.basename(str(arq))
            if nome and not any(nome in p for p in paths):
                draft_ok = False
                break

    ok = bool(base.get("ok")) and not ciclo_erros and draft_ok
    return {
        "ok": ok,
        "msg": ("✅ Validação pré-CapCut: PASSOU" if ok
                else "❌ Validação pré-CapCut: FALHOU"),
        "total": base.get("total", len(cenas)),
        "com_media": base.get("com_media", 0),
        "sem_media": base.get("sem_media", 0),
        "ciclo_erros": ciclo_erros,
        "draft_ok": draft_ok,
    }


__all__ = ["format_mmss", "validar_pre_capcut"]
