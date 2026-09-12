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


def _ruta_draft(proyecto_id: str):
    """Primer candidato existente del draft_content.json (raíz, capcut/ o CapCut User Data)."""
    candidatos = [
        PROJETOS_DIR / proyecto_id / "draft_content.json",
        PROJETOS_DIR / proyecto_id / "capcut" / "draft_content.json",
        Path(os.path.expandvars(
            r"%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft"
        )) / proyecto_id / "draft_content.json",
    ]
    for p in candidatos:
        if p.is_file():
            return p
    return None


def validar_draft_content(proyecto_id: str, draft_path=None) -> Dict[str, Any]:
    """Valida estructuralmente el draft_content.json (PHASE 2, ERRO 4).

    - Acepta `draft_path` explícito (el recién escrito por `criar_draft_imagens`)
      o busca en los candidatos canónicos del proyecto.
    - Chequea: existencia, tamaño mínimo (>1 KB), JSON parseable, materiales
      por tipo, y que al menos un segmento referencie un material válido.

    Retorna {"ok", "erro"/"tamanho"+"refs"+"materiales"+"ruta"}.
    """
    proyecto_id = str(proyecto_id or "")
    if not proyecto_id and not draft_path:
        return {"ok": False, "erro": "proyecto_id obrigatório"}

    ruta = None
    if draft_path:
        _c = Path(str(draft_path))
        ruta = _c if _c.is_file() else (_c / "draft_content.json")
    else:
        ruta = _ruta_draft(proyecto_id)

    if ruta is None or not ruta.is_file():
        return {"ok": False, "erro": "draft_content.json não encontrado"}

    size = ruta.stat().st_size
    if size < 1024:
        return {"ok": False, "erro": f"draft_content.json muito pequeno ({size} bytes)"}

    try:
        draft = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"ok": False, "erro": f"JSON inválido: {e}"}

    if not isinstance(draft, dict):
        return {"ok": False, "erro": "draft não é um objeto JSON"}

    materials = draft.get("materials") if isinstance(draft.get("materials"), dict) else {}
    material_ids = set()
    for mat_type in ("videos", "audios", "images", "stickers"):
        for mat in materials.get(mat_type) or []:
            if isinstance(mat, dict) and mat.get("id"):
                material_ids.add(str(mat["id"]))

    refs_found = 0
    for track in draft.get("tracks") or []:
        for seg in track.get("segments") or []:
            if seg.get("material_id") and str(seg["material_id"]) in material_ids:
                refs_found += 1

    if refs_found == 0:
        return {"ok": False, "erro": "Nenhum segmento referencia materiais válidos"}

    return {
        "ok": True,
        "tamanho": size,
        "refs": refs_found,
        "materiales": len(material_ids),
        "ruta": str(ruta),
    }


__all__ = ["format_mmss", "validar_pre_capcut", "validar_draft_content"]
