"""
services/media_standard.py — PADRÃO LIRA STUDIO v0.3.0+ (SEM QUEBRAS)
====================================================================
Garante a nomenclatura e a integridade de relacionamentos de mídia:

  1. NOMENCLATURA DE INÍCIO
     Toda primeira imagem/vídeo de uma cena usa:
        {id:02d}_[{MM:SS}-{MM:SS}].png|.mp4
     (ex.: cena 1, [00:00-00:05] -> "01_[00:00-00:05].png")
     - NOTA WINDOWS: o caractere ':' é reservado em nomes de arquivo. O arquivo
       FÍSICO usa ':' -> '-' (01_[00-00]-[00-05].png); a notação com ':' é
       preservada nos campos de metadata (`timecode_padrao`) e no relatório.
     - O arquivo vive em <projeto>/imagens/ (nunca solto na raiz).

  2. INTEGRIDADE DE RELACIONAMENTOS (3 fontes sempre sincronizadas)
       a) lira_scene_plan.json   -> campo "arquivo_midia" = path em imagens/
       b) draft_content.json     -> campo "path" = "<draft>/<nome_arquivo>"
       c) Arquivo físico em disco -> <projeto>/imagens/<nome_arquivo>
     Se uma muda, as 3 mudam JUNTAS (função garantir_cena_padrao).

  3. METADATA ORGANIZADA
     Ao gerar a cena:
       - PNG/MP4 em <projeto>/imagens/
       - prompt.txt + status.json em <projeto>/metadata/cena_XXX/

  4. VALIDAÇÃO PRÉ-CAPCUT
     validar_pre_capcut() verifica: existência, nome padronizado, paths do
     draft == arquivo_midia e timestamps sem sobreposição.
"""

import json
import re
import shutil
from pathlib import Path
from typing import Dict, Any, Optional

from config import PROJETOS_DIR

# Regex do nome físico (Windows-safe, ':' -> '-'): 01_[00-00-00-05].png
# (display: 01_[00:00-00:05].png — o ':' é substituído por '-' no arquivo)
NOME_FISICO_RE = re.compile(
    r"^(\d{2})_\[(\d{2})-(\d{2})-(\d{2})-(\d{2})\]\.(png|jpeg|jpg|webp|mp4|mov)$"
)

# Extensões aceitas para mídia de cena
EXT_IMAGEM = (".png", ".jpg", ".jpeg", ".webp")
EXT_VIDEO = (".mp4", ".mov", ".webm", ".avi", ".mkv")


def mmss(sec: float) -> str:
    """Formata segundos como MM:SS (ex.: 123.45 -> '02:03')."""
    sec = max(0.0, float(sec or 0))
    m, s = int(sec // 60), int(sec % 60)
    return f"{m:02d}:{s:02d}"


def timecode_padrao(ts_ini: float, ts_fim: float) -> str:
    """Notação canônica [MM:SS]-[MM:SS] (ex.: '[00:00]-[00:05]')."""
    return f"[{mmss(ts_ini)}]-[{mmss(ts_fim)}]"


def nome_padrao_cena(cid: int, ts_ini: float, ts_fim: float, ext: str = ".png") -> str:
    """Nome canônico (display): {id:02d}_[{MM:SS}-{MM:SS}]{ext} -> '01_[00:00-00:05].png'."""
    if not ext.startswith("."):
        ext = f".{ext}"
    return f"{int(cid):02d}_[{mmss(ts_ini)}-{mmss(ts_fim)}]{ext.lower()}"


def nome_arquivo_seguro(nome: str) -> str:
    """Converte o nome canônico para um nome Windows-válido (':' -> '-').

    '01_[00:00-00:05].png' -> '01_[00-00]-[00-05].png'
    """
    return nome.replace(":", "-")


def caminho_imagens(projeto_id: str) -> Path:
    p = PROJETOS_DIR / projeto_id / "imagens"
    p.mkdir(parents=True, exist_ok=True)
    return p


def caminho_metadata_cena(projeto_id: str, cid: int) -> Path:
    p = PROJETOS_DIR / projeto_id / "metadata" / f"cena_{int(cid):03d}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def eh_nome_padrao(nome: str) -> bool:
    """True se o nome (físico) casa com o padrão v0.3.0: 01_[00-00]-[00-05].png."""
    return bool(NOME_FISICO_RE.match(str(nome).strip()))


def parse_nome_padrao(nome: str):
    """Extrai (cid, ts_ini, ts_fim, ext) de um nome físico padrão, ou None."""
    m = NOME_FISICO_RE.match(str(nome).strip())
    if not m:
        return None
    cid = int(m.group(1))
    ts_ini = int(m.group(2)) * 60 + int(m.group(3))
    ts_fim = int(m.group(4)) * 60 + int(m.group(5))
    return cid, ts_ini, ts_fim, m.group(6)


# ---------------------------------------------------------------------------
# Escrita / sincronização das 3 fontes
# ---------------------------------------------------------------------------

def _plan(projeto_id: str):
    from services.scene_plan_service import carregar_scene_plan
    return carregar_scene_plan(projeto_id)


def _achar_cena(projeto_id: str, cid: int):
    plan = _plan(projeto_id)
    if not plan or not plan.get("cenas"):
        return None, None
    for c in plan["cenas"]:
        if int(c.get("id", 0)) == int(cid) or int(c.get("scene_index", 0)) == int(cid):
            return plan, c
    return None, None


def garantir_cena_padrao(projeto_id: str, cid: int, mover: bool = True) -> Dict[str, Any]:
    """Garante o padrão v0.3.0+ para uma cena (idempotente).

    - Move/copia a mídia atual para <projeto>/imagens/{nome_padrao}
    - Atualiza lira_scene_plan.json (arquivo_midia, filename, timecode_padrao)
    - Escreve metadata/cena_XXX/prompt.txt + status.json
    - Sincroniza midias_encontradas.json (propaga para o draft CapCut)

    Retorna {"ok": bool, "cid": cid, "caminho": str, "nome": str,
             "msg": str, "mudou": bool}.
    """
    from services.scene_plan_service import (
        atualizar_cena, resolver_arquivo_cena, sincronizar_midias_encontradas,
    )
    plan, cena = _achar_cena(projeto_id, cid)
    if cena is None:
        return {"ok": False, "cid": cid, "msg": "cena não encontrada no scene_plan"}

    ts_ini = float(cena.get("tempo_inicio") or 0)
    ts_fim = float(cena.get("tempo_fim") or (ts_ini + 5.0))
    arq_atual = resolver_arquivo_cena(projeto_id, cid, ts_ini)
    if arq_atual is None:
        return {"ok": False, "cid": cid, "msg": "cena sem mídia (arquivo não encontrado)"}

    ext = Path(arq_atual).suffix.lower() or ".png"
    nome_display = nome_padrao_cena(cid, ts_ini, ts_fim, ext)
    nome_fisico = nome_arquivo_seguro(nome_display)  # ':' -> '-' (Windows)
    destino = caminho_imagens(projeto_id) / nome_fisico

    mudou = False
    if Path(arq_atual).resolve() != destino.resolve():
        destino.parent.mkdir(parents=True, exist_ok=True)
        if destino.exists():
            destino.unlink()
        if mover:
            shutil.move(str(arq_atual), str(destino))
        else:
            shutil.copy2(str(arq_atual), str(destino))
        mudou = True
    elif not destino.exists():
        return {"ok": False, "cid": cid, "msg": "arquivo_midia aponta para caminho inexistente"}

    # Metadata organizada: <projeto>/metadata/cena_XXX/
    md_dir = caminho_metadata_cena(projeto_id, cid)
    prompt = str(cena.get("prompt_imagem") or cena.get("visual_prompt")
                 or cena.get("prompt_animacao") or cena.get("texto") or "")
    (md_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

    from datetime import datetime
    status_data = {
        "id": cid,
        "scene_index": cid,
        "status": "BAIXADA",
        "pasta": md_dir.name,
        "arquivo_midia": str(destino),
        "arquivo_nome": nome_fisico,
        "filename": nome_fisico,
        "timecode_padrao": timecode_padrao(ts_ini, ts_fim),
        "tempo_inicio": ts_ini,
        "tempo_fim": ts_fim,
        "duracao": round(ts_fim - ts_ini, 2),
        "tipo": "video" if ext in EXT_VIDEO else "image",
        "atualizado_em": datetime.now().isoformat(sep=" ", timespec="seconds"),
    }
    (md_dir / "status.json").write_text(
        json.dumps(status_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Fonte canônica lira_scene_plan.json
    atualizar_cena(projeto_id, cid, {
        "arquivo_midia": str(destino),
        "filename": nome_fisico,
        "arquivo_nome": nome_fisico,
        "pasta": md_dir.name,
        "timecode_padrao": timecode_padrao(ts_ini, ts_fim),
    })
    sincronizar_midias_encontradas(projeto_id, force=True)

    return {"ok": True, "cid": cid, "caminho": str(destino), "nome": nome_fisico,
            "timecode": timecode_padrao(ts_ini, ts_fim), "msg": "padrão garantido", "mudou": mudou}


def sincronizar_todas_cenas(projeto_id: str) -> Dict[str, Any]:
    """Normaliza TODAS as cenas com mídia para o padrão v0.3.0+. Idempotente."""
    plan = _plan(projeto_id)
    if not plan or not plan.get("cenas"):
        return {"ok": False, "msg": "plano não encontrado"}
    ok = 0
    sem_midia = 0
    erros = []
    detalhes = []
    for c in plan["cenas"]:
        cid = int(c.get("id") or 0)
        r = garantir_cena_padrao(projeto_id, cid)
        if r.get("ok"):
            ok += 1
            detalhes.append({"cid": cid, "nome": r["nome"], "timecode": r.get("timecode", "")})
        elif "sem mídia" in r.get("msg", ""):
            sem_midia += 1
        else:
            erros.append({"cid": cid, "msg": r.get("msg", "")})
    return {"ok": True, "total": len(plan["cenas"]), "com_midia": ok,
            "sem_midia": sem_midia, "erros": erros, "detalhes": detalhes}



# ---------------------------------------------------------------------------
# Validação pré-CapCut (item 4)
# ---------------------------------------------------------------------------

def validar_pre_capcut(projeto_id: str, draft_content_path: Optional[str] = None) -> Dict[str, Any]:
    """Valida o projeto ANTES de exportar para o CapCut.

    1. Total de cenas e quantas têm mídia;
    2. Nomes padronizados ({id:02d}_[...]) e arquivos existindo em imagens/;
    3. draft_content.json (se existir): paths == basename de arquivo_midia;
    4. Timestamps sem sobreposição.

    Retorna {"ok": bool, "total", "com_media", "sem_media", "nomes_invalidos",
             "sobreposicoes", "divergencias_draft", "msg"}.
    """
    plan = _plan(projeto_id)
    if not plan or not plan.get("cenas"):
        return {"ok": False, "total": 0, "msg": "plano não encontrado"}

    cenas = plan["cenas"]
    total = len(cenas)
    nomes_invalidos = []
    sobreposicoes = []
    divergencias = []
    com_media = 0
    sem_media = 0

    for i, c in enumerate(cenas):
        cid = int(c.get("id") or 0)
        arq = str(c.get("arquivo_midia") or "")
        ts_ini = float(c.get("tempo_inicio") or 0)
        ts_fim = float(c.get("tempo_fim") or 0)

        if not arq or not Path(arq).exists():
            sem_media += 1
            continue
        com_media += 1

        # 2. Nome padronizado e em imagens/
        nome = Path(arq).name
        if not eh_nome_padrao(nome):
            nomes_invalidos.append({"cid": cid, "nome": nome})
        if "imagens" not in Path(arq).parts:
            nomes_invalidos.append({"cid": cid, "nome": nome, "fora_de_imagens": True})

        # 3. Sobreposição de timestamps (em relação à cena anterior)
        if i > 0:
            prev_fim = float(cenas[i - 1].get("tempo_fim") or 0)
            if ts_ini < prev_fim - 0.3:
                sobreposicoes.append({"cid": cid, "ini": round(ts_ini, 2),
                                      "prev_fim": round(prev_fim, 2)})

        # 4. Divergência com draft_content.json
        if draft_content_path and Path(draft_content_path).exists():
            try:
                dc = json.loads(Path(draft_content_path).read_text(encoding="utf-8"))
                paths = []
                for tr in dc.get("tracks", []):
                    for seg in tr.get("segments", []):
                        paths.append(str(seg.get("path", "")))
                if paths and nome not in " ".join(paths):
                    divergencias.append({"cid": cid, "nome": nome})
            except Exception:
                pass

    ok = (com_media > 0 and not nomes_invalidos and not sobreposicoes)
    return {
        "ok": ok,
        "total": total,
        "com_media": com_media,
        "sem_media": sem_media,
        "nomes_invalidos": nomes_invalidos[:20],
        "sobreposicoes": sobreposicoes[:20],
        "divergencias_draft": divergencias[:20],
        "msg": ("OK — pronto para CapCut" if ok else
                f"{len(nomes_invalidos)} nomes fora do padrão, "
                f"{len(sobreposicoes)} sobreposições, {sem_media} cenas sem mídia"),
    }

