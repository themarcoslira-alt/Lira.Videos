"""
services/media_manager.py — Gerenciador de Mídia (Lira Studio v0.3.5+)
=====================================================================
Camada fina SOBRE services/media_standard.py (v0.3.0+), que já garante:

  - Nomenclatura canônica: {id:02d}_[{MM:SS}-{MM:SS}].ext (físico usa ':' -> '-')
  - Integridade das 3 fontes (lira_scene_plan.json, draft_content.json, disco)
  - Metadata organizada: <projeto>/metadata/cena_XXX/ (prompt.txt + status.json)

Este módulo adiciona:
  - salvar_midia_com_padrao() — grava bytes + metadata + sync 3 fontes
  - integração com os campos do ciclo narrativo (tipo_cena, efeito, ciclo_numero)
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from config import PROJETOS_DIR, FILENAME_PATTERN
from services.media_standard import (
    nome_padrao_cena,
    nome_arquivo_seguro,
    caminho_imagens,
    caminho_metadata_cena,
    garantir_cena_padrao,
    validar_pre_capcut,
)


def _format_mmss(sec: float) -> str:
    """Segundos -> MM:SS."""
    sec = max(0.0, float(sec or 0))
    return f"{int(sec // 60):02d}:{int(sec % 60):02d}"


def format_mmss(sec: float) -> str:
    """API pública compatível com o pseudocódigo do Bloco Executivo."""
    return _format_mmss(sec)


def gerar_filename_padrao(cena_id: int, tempo_inicio: float, tempo_fim: float,
                          tipo: str = "png") -> str:
    """Gera o nome canônico {id:02d}_[{MM:SS}-{MM:SS}].ext.

    Usa o padrão do media_standard (display com ':'); o nome FÍSICO é o
    Windows-safe (':' -> '-'), aplicado por nome_arquivo_seguro().
    """
    ext = tipo if str(tipo).startswith(".") else f".{tipo}"
    nome_display = nome_padrao_cena(int(cena_id), float(tempo_inicio),
                                    float(tempo_fim), ext)
    return nome_arquivo_seguro(nome_display)


def salvar_midia_com_padrao(
    projeto_id: str,
    cena_id: int,
    tempo_inicio: float,
    tempo_fim: float,
    arquivo_bytes: bytes,
    tipo: str = "png",
    prompt: str = "",
    cena_dict: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Salva mídia com o padrão {id:02d}_[MM:SS-MM:SS].ext.

    - Grava os bytes em <projeto>/cenas/<filename> (imagem e vídeo unificados);
    - Escreve metadata/cena_XXX/prompt.txt + status.json;
    - Sincroniza as 3 fontes (lira_scene_plan.json, midias_encontradas.json, disco).

    Retorna {"ok", "filename", "caminho", "msg", ...}.
    """
    projeto_id = str(projeto_id or "")
    if not projeto_id:
        return {"ok": False, "msg": "projeto_id obrigatório"}

    cena_id = int(cena_id or 0)
    tempo_inicio = float(tempo_inicio or 0)
    tempo_fim = float(tempo_fim or (tempo_inicio + 5.0))
    ext = str(tipo or "png").lower().lstrip(".")
    if ext not in ("png", "jpg", "jpeg", "webp", "mp4", "mov"):
        return {"ok": False, "msg": f"tipo inválido: {tipo!r}"}

    filename = gerar_filename_padrao(cena_id, tempo_inicio, tempo_fim, ext)
    pasta = "cenas"  # unificada: toda mídia de cena (png/mp4/mov/...) vai para cenas/
    destino = PROJETOS_DIR / projeto_id / pasta / filename
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(arquivo_bytes)

    # Metadata organizada
    cena_dict = cena_dict or {}
    md_dir = caminho_metadata_cena(projeto_id, cena_id)
    prompt = str(prompt or cena_dict.get("prompt")
                 or cena_dict.get("prompt_imagem") or "")
    (md_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

    status_data = {
        "id": cena_id,
        "scene_index": cena_id,
        "status": "BAIXADA",
        "pasta": md_dir.name,
        "arquivo_midia": str(destino),
        "arquivo_nome": filename,
        "filename": filename,
        "timecode_padrao": f"[{_format_mmss(tempo_inicio)}]-[{_format_mmss(tempo_fim)}]",
        "tempo_inicio": tempo_inicio,
        "tempo_fim": tempo_fim,
        "duracao": round(tempo_fim - tempo_inicio, 2),
        "tipo": "video" if ext in ("mp4", "mov") else "image",
        "tipo_cena": cena_dict.get("tipo_cena"),
        "efeito": cena_dict.get("efeito"),
        "posicao_ciclo": cena_dict.get("posicao_ciclo"),
        "ciclo_numero": cena_dict.get("ciclo_numero"),
        "atualizado_em": datetime.now().isoformat(sep=" ", timespec="seconds"),
    }
    (md_dir / "status.json").write_text(
        json.dumps(status_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Sincroniza as 3 fontes via media_standard: preenche arquivo_midia no plano
    # diretamente (não depende do glob por tamanho >500 bytes) e propaga para
    # midias_encontradas.json (draft CapCut).
    from services.scene_plan_service import (
        atualizar_cena,
        sincronizar_midias_encontradas,
    )
    atualizar_cena(projeto_id, cena_id, {
        "arquivo_midia": str(destino),
        "filename": filename,
        "arquivo_nome": filename,
        "pasta": md_dir.name,
        "timecode_padrao": f"[{_format_mmss(tempo_inicio)}]-[{_format_mmss(tempo_fim)}]",
        "tipo": "video" if ext in ("mp4", "mov") else "image",
    })
    try:
        sincronizar_midias_encontradas(projeto_id, force=True)
    except Exception:
        pass

    return {"ok": True, "filename": filename, "caminho": str(destino),
            "pasta": pasta, "msg": "mídia salva com padrão"}


__all__ = [
    "format_mmss",
    "gerar_filename_padrao",
    "salvar_midia_com_padrao",
    "garantir_cena_padrao",
    "validar_pre_capcut",
]
