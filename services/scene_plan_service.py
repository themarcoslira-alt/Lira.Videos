"""
scene_plan_service.py — Lira Studio
====================================
Geração e persistência do scene_plan.json por projeto.

Estrutura simplificada orientada ao fluxo Lira:
  - id, tempo_inicio, tempo_fim
  - tipo: "image" | "video"
  - personagem_ref: path local da imagem de referência (ou "")
  - animar: bool — se True, cena imagem deve ser animada pelo Flow após geração
  - prompt_imagem: texto do prompt para geração de imagem no Flow
  - prompt_animacao: texto do prompt de animação para o Flow (modo vídeo)
  - arquivo_midia: path local do arquivo baixado (imagem ou vídeo)
  - status: PENDENTE | PROMPT_PRONTO | MIDIA_IMPORTADA |
            PRONTA_PARA_ANIMAR | ANIMADA | PRONTA_PARA_MONTAGEM | MONTADA

NÃO substitui scene_plan_schema.py (Fase 0 / direção visual LLM) —
coexistem sem conflito.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any, Set, Tuple

from config import PROJETOS_DIR
from services.event_logger import log_event

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SCENE_PLAN_FILE = "lira_scene_plan.json"

# IMAGE STATUS (Máquina de Estados de Imagem)
IMAGE_STATUS_PENDING    = "PENDING"
IMAGE_STATUS_GENERATING = "GENERATING"
IMAGE_STATUS_RECEIVED   = "RECEIVED"
IMAGE_STATUS_DOWNLOADED = "DOWNLOADED"
IMAGE_STATUS_READY      = "READY"

# VIDEO STATUS (Máquina de Estados de Vídeo)
VIDEO_STATUS_NOT_STARTED = "NOT_STARTED"
VIDEO_STATUS_QUEUED      = "QUEUED"
VIDEO_STATUS_GENERATING  = "GENERATING"
VIDEO_STATUS_READY       = "READY"

STATUS_PENDENTE             = "PENDENTE"
STATUS_ENVIANDO             = "ENVIANDO"
STATUS_GERANDO              = "GERANDO"
STATUS_GERADA               = "GERADA"
STATUS_BAIXADA              = "BAIXADA"
STATUS_ERRO                 = "ERRO"

# Compatibilidade com referências legadas
STATUS_ENVIADA              = "ENVIANDO"
STATUS_PROMPT_PRONTO        = "PENDENTE"
STATUS_MIDIA_IMPORTADA      = "BAIXADA"
STATUS_PRONTA_PARA_ANIMAR   = "BAIXADA"
STATUS_ANIMADA              = "BAIXADA"
STATUS_PRONTA_PARA_MONTAGEM = "BAIXADA"
STATUS_MONTADA              = "BAIXADA"

STATUS_VALIDOS = (
    STATUS_PENDENTE, STATUS_ENVIANDO, STATUS_GERANDO, STATUS_GERADA,
    STATUS_BAIXADA, STATUS_ERRO,
)

TIPO_IMAGE = "image"
TIPO_VIDEO = "video"
TIPO_TEXT = "text"

IMAGEM_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# Tamanho mínimo (bytes) de mídia aceito na validação de integridade (FASE 3.2)
TAMANHO_MIN_IMAGEM = 1024
TAMANHO_MIN_VIDEO = 8192


def tipo_efetivo_cena(cena: dict) -> str:
    """Fonte ÚNICA de tipo de mídia de uma cena.

    Prioridade:
      1. campo 'tipo' (authority): 'image' | 'video'
      2. fallback legado por 'animar' (True → video)
      3. default: image
    NUNCA permite que 'animar_depois'/'animate_later' alterem o tipo.
    Retorna sempre 'image' ou 'video'.
    """
    cena = cena or {}
    t = str(cena.get("tipo") or "").lower().strip().strip('"').strip("'")
    if t == TIPO_VIDEO or t == "video":
        return TIPO_VIDEO
    if cena.get("animar") is True or str(cena.get("animar") or "").lower() == "true":
        return TIPO_VIDEO
    return TIPO_IMAGE


def validar_midia_bytes(midia_bytes: bytes, is_video: bool) -> dict:
    """Valida a integridade real da mídia ANTES de persistir.

    Critérios (falha → mídia nunca entra em storyboard/galeria):
      - bytes presentes (não vazios)
      - tamanho mínimo (imagem >= 1KB, vídeo >= 8KB)
      - assinatura/decodificação real:
          * imagem: abertura + carga real via Pillow (magic + decodificação)
          * vídeo:  assinatura container MP4 (ftyp) ou WebM
    Retorna {'valid': bool, 'error': str}.
    """
    if not midia_bytes:
        return {"valid": False, "error": "mídia vazia (0 bytes)"}

    if is_video:
        if len(midia_bytes) < TAMANHO_MIN_VIDEO:
            return {"valid": False,
                    "error": f"vídeo demasiado pequeno ({len(midia_bytes)} bytes < {TAMANHO_MIN_VIDEO})"}
        if (len(midia_bytes) >= 8 and midia_bytes[4:8] == b"ftyp") or midia_bytes[:4] == b"\x1a\x45\xdf\xa3":
            return {"valid": True, "error": ""}
        return {"valid": False,
                "error": "assinatura de vídeo não reconhecida (esperado MP4/WebM)"}

    # --- imagem ---
    if len(midia_bytes) < TAMANHO_MIN_IMAGEM:
        return {"valid": False,
                "error": f"imagem demasiado pequena ({len(midia_bytes)} bytes < {TAMANHO_MIN_IMAGEM})"}
    try:
        import io as _io
        from PIL import Image
        with Image.open(_io.BytesIO(midia_bytes)) as im:
            im.load()  # decodificação real (falha em arquivo corrompido/sem conteúdo)
        return {"valid": True, "error": ""}
    except Exception as e:
        return {"valid": False, "error": f"imagem não decodificável: {e}"}


PASTAS_PROJETO_V2 = ("audio", "srt", "imagens", "videos", "prompts", "capcut", "cenas")


def garantir_estrutura_pastas(projeto: str) -> dict:
    """
    Garante a criação das pastas padronizadas do Studio 2.0:
    projeto/
      audio/
      srt/
      imagens/
      videos/
      prompts/
      capcut/
    NÃO move nem apaga arquivos antigos. Preserva total compatibilidade.
    """
    pdir = _project_dir(projeto)
    pdir.mkdir(parents=True, exist_ok=True)
    pastas = {}
    for sub in PASTAS_PROJETO_V2:
        sdir = pdir / sub
        sdir.mkdir(parents=True, exist_ok=True)
        pastas[sub] = str(sdir)
    return pastas


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _project_dir(projeto: str) -> Path:
    return PROJETOS_DIR / projeto


def _scene_plan_path(projeto: str) -> Path:
    return _project_dir(projeto) / SCENE_PLAN_FILE


def _fmt_ts(sec: float) -> str:
    sec = max(0.0, float(sec or 0))
    return f"{int(sec // 60):02d}:{int(sec % 60):02d}"


def _safe_slug(texto: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^\w]+", "_", str(texto).lower()).strip("_")
    return slug[:max_len] or "cena"


def formatar_ts_cena(ts_ini: float, ts_fim: float) -> str:
    """Formata o intervalo de tempo da cena no padrão: 00-00-05 (MM-SS-SS)."""
    m_ini = int(float(ts_ini or 0) // 60)
    s_ini = int(float(ts_ini or 0) % 60)
    s_fim = int(float(ts_fim or 0) % 60)
    return f"{m_ini:02d}-{s_ini:02d}-{s_fim:02d}"


def formatar_nome_arquivo_cena_padrao(cid: int, ts_ini: float, ts_fim: float, ext: str = ".png") -> str:
    """Formata o nome da cena exatamente no padrão estrito: 01_[00-00-05].png"""
    if not ext.startswith("."):
        ext = f".{ext}"
    m_ini = int(float(ts_ini or 0) // 60)
    s_ini = int(float(ts_ini or 0) % 60)
    s_fim = int(float(ts_fim or 0) % 60)
    return f"{cid:02d}_[{m_ini:02d}-{s_ini:02d}-{s_fim:02d}]{ext}"


def _storyboard_path(projeto: str) -> Path:
    return _project_dir(projeto) / "storyboard.json"


def _galeria_path(projeto: str) -> Path:
    return _project_dir(projeto) / "galeria.json"


def carregar_storyboard(projeto: str) -> dict:
    """Carrega storyboard.json do projeto."""
    path = _storyboard_path(projeto)
    if not path.exists():
        return {"projeto": projeto, "versao": "2.0", "cenas": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {"projeto": projeto, "versao": "2.0", "cenas": data}
        if "cenas" not in data:
            data["cenas"] = []
        return data
    except Exception as e:
        log_event("STORYBOARD", f"{projeto}: erro ao carregar storyboard.json: {e}", level="warn")
        return {"projeto": projeto, "versao": "2.0", "cenas": []}


def salvar_storyboard(projeto: str, data: dict) -> bool:
    """Salva storyboard.json atomicamente."""
    path = _storyboard_path(projeto)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(path))
        return True
    except Exception as e:
        log_event("STORYBOARD", f"{projeto}: erro ao salvar storyboard.json: {e}", level="error")
        return False


def atualizar_storyboard_cena(
    projeto: str,
    cid: int,
    arquivo_nome: str,
    arquivo_path: str,
    ts_ini: float = 0.0,
    ts_fim: float = 5.0,
    prompt: str = "",
    personagem: str = "",
    modelo: str = "",
    status: str = STATUS_BAIXADA
) -> dict:
    """Atualiza ou insere o registro da cena no storyboard.json do projeto."""
    sb = carregar_storyboard(projeto)
    cenas = sb.get("cenas", [])

    item_existente = None
    for c in cenas:
        if int(c.get("cena") or c.get("id") or c.get("scene_id") or 0) == int(cid):
            item_existente = c
            break

    dur = round(max(0.0, float(ts_fim) - float(ts_ini)), 2)
    dados_cena = {
        "cena": int(cid),
        "arquivo": arquivo_nome,
        "arquivo_path": str(arquivo_path),
        "inicio": _fmt_ts(ts_ini),
        "fim": _fmt_ts(ts_fim),
        "duracao": dur,
        "prompt": prompt or "",
        "personagem": personagem or "",
        "modelo": modelo or "",
        "status": status,
        "atualizado_em": datetime.now().isoformat(sep=" ", timespec="seconds")
    }

    if item_existente:
        item_existente.update(dados_cena)
    else:
        cenas.append(dados_cena)

    cenas.sort(key=lambda x: int(x.get("cena") or x.get("id") or x.get("scene_id") or 0))
    sb["cenas"] = cenas
    sb["atualizado_em"] = datetime.now().isoformat(sep=" ", timespec="seconds")
    salvar_storyboard(projeto, sb)
    return dados_cena


def carregar_galeria(projeto: str) -> dict:
    """Carrega galeria.json do projeto."""
    path = _galeria_path(projeto)
    if not path.exists():
        return {"projeto": projeto, "versao": "2.0", "total_itens": 0, "itens": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "itens" not in data:
            data["itens"] = []
        data["total_itens"] = len(data["itens"])
        return data
    except Exception as e:
        log_event("GALERIA", f"{projeto}: erro ao carregar galeria.json: {e}", level="warn")
        return {"projeto": projeto, "versao": "2.0", "total_itens": 0, "itens": []}


def salvar_galeria(projeto: str, data: dict) -> bool:
    """Salva galeria.json atomicamente."""
    path = _galeria_path(projeto)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["total_itens"] = len(data.get("itens", []))
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(path))
        return True
    except Exception as e:
        log_event("GALERIA", f"{projeto}: erro ao salvar galeria.json: {e}", level="error")
        return False


def atualizar_galeria_item(
    projeto: str,
    arquivo_nome: str,
    arquivo_path: str,
    tipo: str = "imagem",
    cid: Optional[int] = None,
    ts_ini: Optional[float] = None,
    ts_fim: Optional[float] = None,
    modelo: str = "",
    personagem: str = "",
    tamanho_bytes: int = 0
) -> dict:
    """Atualiza ou insere um arquivo de mídia na galeria central do projeto."""
    gal = carregar_galeria(projeto)
    itens = gal.get("itens", [])

    item_existente = None
    for it in itens:
        if it.get("arquivo") == arquivo_nome or it.get("arquivo_path") == str(arquivo_path):
            item_existente = it
            break

    dur = round(max(0.0, float(ts_fim or 0) - float(ts_ini or 0)), 2) if ts_ini is not None and ts_fim is not None else 0

    dados_item = {
        "tipo": tipo,
        "arquivo": arquivo_nome,
        "arquivo_path": str(arquivo_path),
        "cena": int(cid) if cid is not None else None,
        "inicio": _fmt_ts(ts_ini) if ts_ini is not None else "",
        "fim": _fmt_ts(ts_fim) if ts_fim is not None else "",
        "duracao": dur,
        "modelo": modelo or "",
        "personagem": personagem or "",
        "tamanho_bytes": tamanho_bytes or (Path(arquivo_path).stat().st_size if Path(arquivo_path).exists() else 0),
        "data_adicao": datetime.now().isoformat(sep=" ", timespec="seconds")
    }

    if item_existente:
        item_existente.update(dados_item)
    else:
        itens.append(dados_item)

    gal["itens"] = itens
    gal["total_itens"] = len(itens)
    salvar_galeria(projeto, gal)
    return dados_item


def indexar_midias_projeto(projeto: str) -> dict:
    """
    Varre as pastas do projeto (cenas, audio, srt, videos, imagens) e
    registra automaticamente todos os arquivos encontrados em galeria.json,
    storyboard.json e lira_scene_plan.json.
    """
    pdir = _project_dir(projeto)
    if not pdir.exists():
        return {"success": False, "error": f"Projeto '{projeto}' não encontrado."}

    garantir_estrutura_pastas(projeto)
    total_indexados = 0

    # 1. Varre cenas/
    cenas_dir = pdir / "cenas"
    padrao_cena_regex = re.compile(r"^(\d+)_\[(\d{2})-(\d{2})-(\d{2})\]\.(png|jpg|jpeg|mp4|webp)$", re.IGNORECASE)

    if cenas_dir.exists():
        for f in cenas_dir.rglob("*"):
            if f.is_file() and f.suffix.lower() in (IMAGEM_EXT | {".mp4"}):
                fname = f.name
                m = padrao_cena_regex.match(fname)
                cid = None
                ts_ini = 0.0
                ts_fim = 5.0
                if m:
                    cid = int(m.group(1))
                    m_ini = int(m.group(2))
                    s_ini = int(m.group(3))
                    s_fim = int(m.group(4))
                    ts_ini = float(m_ini * 60 + s_ini)
                    ts_fim = float(m_ini * 60 + s_fim)
                else:
                    # Tenta extrair numero de cena simples cena_001 ou 01
                    m_num = re.search(r"(?:cena_)?(\d+)", fname, re.IGNORECASE)
                    if m_num:
                        cid = int(m_num.group(1))

                is_vid = f.suffix.lower() == ".mp4"
                tipo_media = "video" if is_vid else "imagem"
                tamanho = f.stat().st_size

                # Atualiza galeria
                atualizar_galeria_item(
                    projeto=projeto,
                    arquivo_nome=fname,
                    arquivo_path=str(f),
                    tipo=tipo_media,
                    cid=cid,
                    ts_ini=ts_ini,
                    ts_fim=ts_fim,
                    tamanho_bytes=tamanho
                )

                # Se for cena válida, atualiza storyboard e scene_plan
                if cid is not None:
                    atualizar_storyboard_cena(
                        projeto=projeto,
                        cid=cid,
                        arquivo_nome=fname,
                        arquivo_path=str(f),
                        ts_ini=ts_ini,
                        ts_fim=ts_fim,
                        status=STATUS_BAIXADA
                    )
                    atualizar_cena(projeto, cid, {
                        "arquivo_midia": str(f),
                        "status": STATUS_BAIXADA
                    })
                
                total_indexados += 1

    # 1.1 Varre imagens/
    imagens_dir = pdir / "imagens"
    if imagens_dir.exists():
        for f in imagens_dir.iterdir():
            if f.is_file() and f.suffix.lower() in IMAGEM_EXT and f.stat().st_size > 500:
                m_num = re.search(r"^(\d+)", f.stem)
                if m_num:
                    cid = int(m_num.group(1))
                    atualizar_galeria_item(
                        projeto=projeto,
                        arquivo_nome=f.name,
                        arquivo_path=str(f),
                        tipo="imagem",
                        cid=cid,
                        tamanho_bytes=f.stat().st_size
                    )
                    atualizar_storyboard_cena(
                        projeto=projeto,
                        cid=cid,
                        arquivo_nome=f.name,
                        arquivo_path=str(f),
                        status=STATUS_BAIXADA
                    )
                    atualizar_cena(projeto, cid, {
                        "arquivo_midia": str(f),
                        "image_status": IMAGE_STATUS_READY,
                        "status": STATUS_BAIXADA
                    })
                    total_indexados += 1

    # 2. Varre audio/
    audio_dir = pdir / "audio"
    if audio_dir.exists():
        for f in audio_dir.iterdir():
            if f.is_file() and f.suffix.lower() in {".mp3", ".wav", ".m4a", ".aac", ".ogg"}:
                atualizar_galeria_item(
                    projeto=projeto,
                    arquivo_nome=f.name,
                    arquivo_path=str(f),
                    tipo="audio",
                    tamanho_bytes=f.stat().st_size
                )
                total_indexados += 1

    # 3. Varre srt/
    srt_dir = pdir / "srt"
    if srt_dir.exists():
        for f in srt_dir.iterdir():
            if f.is_file() and f.suffix.lower() in {".srt", ".vtt"}:
                atualizar_galeria_item(
                    projeto=projeto,
                    arquivo_nome=f.name,
                    arquivo_path=str(f),
                    tipo="srt",
                    tamanho_bytes=f.stat().st_size
                )
                total_indexados += 1

    # 4. Varre videos/
    videos_dir = pdir / "videos"
    if videos_dir.exists():
        for f in videos_dir.iterdir():
            if f.is_file() and f.suffix.lower() in {".mp4", ".mov", ".webm"}:
                atualizar_galeria_item(
                    projeto=projeto,
                    arquivo_nome=f.name,
                    arquivo_path=str(f),
                    tipo="video",
                    tamanho_bytes=f.stat().st_size
                )
                total_indexados += 1

    try:
        sincronizar_midias_encontradas(projeto)
    except Exception:
        pass

    return {"success": True, "total_indexados": total_indexados}


def salvar_midia_cena_estruturada(
    projeto_id: str,
    cid: int,
    ts_ini: float,
    ts_fim: float,
    prompt_texto: str,
    midia_bytes: bytes,
    is_video: bool = False,
    modelo_usado: str = "",
    personagem_ref: str = ""
) -> dict:
    """Salva a mídia e os metadados da cena na estrutura profissional do projeto:
    projetos/
      └── <projeto_id>/
           ├── cenas/
           │     ├── 01_[00-00-05].png
           │     └── cena_001_00-00-05/
           │           ├── prompt.txt
           │           ├── imagem.png
           │           └── status.json
           ├── storyboard.json
           └── galeria.json
    """
    ext = ".mp4" if is_video else ".png"

    # 0. VALIDAÇÃO REAL DE INTEGRIDADE (FASE 3.2) — ANTES de persistir.
    #    Se falhar: status=ERRO e NUNCA entra em storyboard/galeria.
    val = validar_midia_bytes(midia_bytes, is_video)
    if not val["valid"]:
        msg = f"mídia inválida para a cena {cid}: {val['error']}"
        log_event("MIDIA_VALIDACAO", f"{projeto_id}: {msg}", level="error")
        atualizar_cena(projeto_id, cid, {
            "status": STATUS_ERRO,
            "erro_msg": val["error"],
            "arquivo_midia": "",
        })
        return {"success": False, "error": val["error"], "cid": cid,
                "tipo": "video" if is_video else "image"}

    # 1. Nomenclatura simples para fácil ingestão no CapCut e editores: 001.png
    arquivo_simples = f"{cid:03d}{ext}"
    arquivo_nome_padrao = formatar_nome_arquivo_cena_padrao(cid, ts_ini, ts_fim, ext)

    cenas_dir = PROJETOS_DIR / projeto_id / "cenas"
    cenas_dir.mkdir(parents=True, exist_ok=True)

    # Salva tanto o arquivo simples 001.png quanto a cópia padronizada com timestamp
    arquivo_path_simples = cenas_dir / arquivo_simples
    arquivo_path_simples.write_bytes(midia_bytes)

    arquivo_path_principal = cenas_dir / arquivo_nome_padrao
    arquivo_path_principal.write_bytes(midia_bytes)

    # 2. Mantém subpasta estruturada para auditoria/backup local
    ts_str = formatar_ts_cena(ts_ini, ts_fim)
    pasta_cena_nome = f"cena_{cid:03d}_{ts_str}"
    cena_dir_sub = cenas_dir / pasta_cena_nome
    cena_dir_sub.mkdir(parents=True, exist_ok=True)

    (cena_dir_sub / arquivo_simples).write_bytes(midia_bytes)
    (cena_dir_sub / arquivo_nome_padrao).write_bytes(midia_bytes)
    (cena_dir_sub / ("video.mp4" if is_video else "imagem.png")).write_bytes(midia_bytes)
    (cena_dir_sub / "prompt.txt").write_text(prompt_texto or "", encoding="utf-8")

    status_data = {
        "id": cid,
        "scene_index": cid,
        "status": STATUS_BAIXADA,
        "image_status": IMAGE_STATUS_READY if not is_video else IMAGE_STATUS_DOWNLOADED,
        "video_status": VIDEO_STATUS_READY if is_video else VIDEO_STATUS_NOT_STARTED,
        "pasta": pasta_cena_nome,
        "arquivo_midia": str(arquivo_path_simples),
        "arquivo_nome": arquivo_simples,
        "filename": arquivo_simples,
        "arquivo_nome_timestamp": arquivo_nome_padrao,
        "prompt": prompt_texto,
        "personagem": personagem_ref or "",
        "modelo": modelo_usado or "",
        "tempo_inicio": ts_ini,
        "tempo_fim": ts_fim,
        "start": ts_ini,
        "end": ts_fim,
        "original_timestamp": ts_str,
        "duracao": round(ts_fim - ts_ini, 2),
        "tipo": "video" if is_video else "image",
        "atualizado_em": datetime.now().isoformat(sep=" ", timespec="seconds"),
    }
    (cena_dir_sub / "status.json").write_text(
        json.dumps(status_data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    # 3. Atualiza storyboard.json
    atualizar_storyboard_cena(
        projeto=projeto_id,
        cid=cid,
        arquivo_nome=arquivo_simples,
        arquivo_path=str(arquivo_path_simples),
        ts_ini=ts_ini,
        ts_fim=ts_fim,
        prompt=prompt_texto,
        personagem=personagem_ref,
        modelo=modelo_usado,
        status=STATUS_BAIXADA
    )

    # 4. Atualiza galeria.json
    atualizar_galeria_item(
        projeto=projeto_id,
        arquivo_nome=arquivo_simples,
        arquivo_path=str(arquivo_path_simples),
        tipo="video" if is_video else "imagem",
        cid=cid,
        ts_ini=ts_ini,
        ts_fim=ts_fim,
        modelo=modelo_usado,
        personagem=personagem_ref,
        tamanho_bytes=len(midia_bytes)
    )

    # 4.5. FASE 4.0 — Visual Judgment Engine: avalia qualidade e fidelidade da mídia gerada
    try:
        import services.visual_memory_engine as vme_svc
        import services.visual_judgment_service as vjs_svc
        mem_proj = vme_svc.obter_memoria_visual_projeto(projeto_id)
        
        cena_atual = {
            "id": cid,
            "scene_index": cid,
            "prompt_imagem": prompt_texto,
            "visual_prompt": prompt_texto,
            "uses_character": bool(personagem_ref),
            "character_ref": personagem_ref or "",
            "camera_direction": {"shot": "medium shot"},
            "continuity_context": "Preserve exact character visual identity" if personagem_ref else ""
        }
        plan_atual = carregar_scene_plan(projeto_id)
        if plan_atual and plan_atual.get("cenas"):
            for sc in plan_atual["cenas"]:
                if int(sc.get("id", 0)) == int(cid):
                    cena_atual = sc
                    break

        vj = vjs_svc.avaliar_imagem_cena(
            projeto_id=projeto_id,
            cena=cena_atual,
            memoria_visual=mem_proj,
            caminho_imagem=str(arquivo_path_simples)
        )
        
        status_data["visual_score"] = vj["visual_score"]
        status_data["judgment_status"] = vj["judgment_status"]
        status_data["selection_reason"] = vj["selection_reason"]
        
        atualizar_cena(projeto_id, cid, {
            "visual_score": vj["visual_score"],
            "judgment_status": vj["judgment_status"],
            "selection_reason": vj["selection_reason"],
            "image_status": IMAGE_STATUS_READY if not is_video else IMAGE_STATUS_DOWNLOADED,
            "status": STATUS_BAIXADA
        })

        # FASE 4.2 — Atualiza resultado no histórico de prompt scene_XXX.txt
        try:
            import services.prompt_history_service as prompt_history_svc
            prompt_history_svc.atualizar_historico_resultado_cena(
                projeto_id=projeto_id,
                cid=cid,
                image_path=str(arquivo_path_simples),
                visual_score=vj["visual_score"],
                judgment_status=vj["judgment_status"],
                selection_reason=vj["selection_reason"]
            )
        except Exception:
            pass
    except Exception as e_vj:
        log_event("VISUAL_JUDGMENT", f"Aviso ao avaliar mídia da cena {cid}: {e_vj}", level="warn")

    # 5. Compatibilidade com pastas legadas imagens/ ou videos/
    legacy_dir = PROJETOS_DIR / projeto_id / ("videos" if is_video else "imagens")
    legacy_dir.mkdir(parents=True, exist_ok=True)
    try:
        (legacy_dir / arquivo_simples).write_bytes(midia_bytes)
        (legacy_dir / arquivo_nome_padrao).write_bytes(midia_bytes)
    except Exception:
        pass

    return {
        "success": True,
        "arquivo_path": str(arquivo_path_simples),
        "arquivo_nome": arquivo_simples,
        "arquivo_path_timestamp": str(arquivo_path_principal),
        "arquivo_nome_timestamp": arquivo_nome_padrao,
        "scene_index": cid,
        "start": ts_ini,
        "end": ts_fim,
        "original_timestamp": ts_str,
        "tipo": "video" if is_video else "image",
        "pasta_cena": str(cena_dir_sub),
        "status_data": status_data
    }


def _nome_arquivo_cena(cena: dict, ext: str) -> str:
    """Nome padrão: NN_[MM-SS]_descricao.ext"""
    cid  = int(cena.get("id", 0))
    ini  = float(cena.get("tempo_inicio", 0))
    mm   = int(ini // 60) % 60
    ss   = int(ini % 60)
    slug = _safe_slug(cena.get("prompt_imagem") or cena.get("texto", ""))
    return f"{cid:03d}_[{mm:02d}-{ss:02d}]_{slug}{ext}"


# ---------------------------------------------------------------------------
# Cena template
# ---------------------------------------------------------------------------

def _nova_cena(
    cid: int,
    tempo_inicio: float,
    tempo_fim: float,
    texto: str = "",
    tipo: str = TIPO_IMAGE,
    animar: bool = False,
    nome_personagem: str = "",
    modo_producao: str = "imagem_video",
    referencia_visual: str = "",
    continuidade: bool = True,
) -> dict:
    ts_ini = _fmt_ts(tempo_inicio)
    ts_fim = _fmt_ts(tempo_fim)
    ts_intervalo = formatar_ts_cena(tempo_inicio, tempo_fim)
    return {
        "id":                       cid,
        "scene_index":              cid,
        "start":                    round(float(tempo_inicio), 3),
        "end":                      round(float(tempo_fim), 3),
        "tempo_inicio":             round(float(tempo_inicio), 3),
        "tempo_fim":                round(float(tempo_fim), 3),
        "timestamp":                f"{ts_ini} - {ts_fim}",
        "original_timestamp":       ts_intervalo,
        "narration":                str(texto or ""),
        "texto":                    str(texto or ""),
        "visual_prompt":            "",
        "prompt_imagem":            "",
        "prompt_animacao":          "",
        "scene_type":               "avatar_talking" if bool(nome_personagem) else "broll_macro",
        "visual_role":              "hook" if cid == 1 else "explanation",
        "uses_character":           False,
        "character_ref":            "",
        "emotion":                  "curiosity" if cid == 1 else "trust",
        "energy":                   "high" if cid == 1 else "medium",
        "camera_direction":         {},
        "supporting_visuals":       [],
        "continuity_context":       "",
        "lighting_mood":            "natural morning daylight",
        "animate_later":            bool(animar),
        "animar_depois":            bool(animar),
        "media_intent":             "video" if bool(animar) or tipo == TIPO_VIDEO else "image",
        "duracao":                  round(max(0.0, float(tempo_fim) - float(tempo_inicio)), 3),
        "tipo":                     tipo,            # "image" | "video"
        "animar":                   bool(animar),    # animar imagem → vídeo no Flow
        "image_status":             IMAGE_STATUS_PENDING,
        "video_status":             VIDEO_STATUS_NOT_STARTED,
        "personagem_ref":           "",              # path local da imagem de referência
        "arquivo_midia":            "",              # path local do arquivo gerado/importado
        "download_path":            "",              # path local
        "filename":                 f"{cid:03d}.png",
        "status":                   STATUS_PENDENTE,
        # --- Campos Studio 2.0 (Fase 1) ---
        "nome_personagem":          str(nome_personagem or ""),
        "modo_producao":            str(modo_producao or "imagem_video"),
        "referencia_visual":        str(referencia_visual or ""),
        "continuidade":             bool(continuidade),
        "timestamp_saida":          f"{ts_ini}_{ts_fim}",
        "atualizado_em":            datetime.now().isoformat(sep=" ", timespec="seconds"),
        # --- FASE 4.0 — Memória Visual + Julgamento Visual ---
        "memory_used":              False,
        "continuity_score":         0,
        "visual_score":             0,
        "judgment_status":          "",
        "selection_reason":         "",
        # --- FASE 4.1 + 4.2 — Storyboard Director + Prompt History ---
        "story_role":               "hook" if cid == 1 else "explanation",
        "narrative_purpose":        "Create curiosity in first seconds" if cid == 1 else "",
        "retention_goal":           "very_high" if cid == 1 else "medium",
        "previous_scene_connection": "",
        "next_scene_connection":     "",
        "prompt_history_path":      f"prompt_history/scene_{cid:03d}.txt",
        "decision_logged":          False,
        # --- FASE 5.0 — Image Variation Selector AI ---
        "variations_evaluated":     [],
        "best_variation_index":     0,
        "variation_selection_rationale": "",
        # --- FASE 6.0 — Animation Director AI ---
        "animation_type":           "presenter_speech" if bool(nome_personagem) else "static_macro",
        "animation_priority":       "high" if cid == 1 else "none",
        "motion_vector":            "slow_dolly_push" if cid == 1 else "static",
        "animation_rationale":      "",
        # --- FASE 7.0 — Retention Director AI ---
        "retention_index":          98 if cid == 1 else 85,
        "retention_cues":           [],
        "pattern_interrupt":        False,
        # --- FASE 11.0 — Human Feedback & Performance Metrics ---
        "human_status":             "pending",
        "human_note":               "",
        "approved_by":              "",
        "manual_intervention":      False,
    }


# ---------------------------------------------------------------------------
# FASE 11.1 — CHARACTER IDENTITY LOCK (SCENE PLAN IDENTITY SYNC)
# ---------------------------------------------------------------------------

def sincronizar_trava_identidade_cenas(
    cenas: list,
    projeto_id: str = "",
    nome_pers_default: str = ""
) -> list:
    """
    Garante que qualquer cena do tipo avatar_talking, avatar_action, hybrid, cta
    ou cujo prompt mencione o personagem bloqueado (@Nome) tenha obrigatoriamente:
    - uses_character = True
    - character_ref = "@<Nome>" (referência Flow bloqueada)

    Cenas b-roll puras (broll_macro, environment, comparison, etc.) sem menção humana:
    - uses_character = False
    - character_ref = ""
    """
    import services.character_service as character_svc
    idt = character_svc.obter_identidade_projeto(projeto_id) if projeto_id else None
    nome_oficial = (idt.get("nome") if idt else "") or nome_pers_default or ""
    ref_oficial = (idt.get("referencia_flow") if idt else "") or (f"@{nome_oficial}" if nome_oficial else "")

    tipos_humanos = {"avatar_talking", "avatar_action", "hybrid", "cta"}

    for c in cenas:
        stype = (c.get("scene_type") or "").strip().lower()
        prompt_txt = f"{c.get('prompt_imagem', '')} {c.get('visual_prompt', '')}".lower()
        tem_tag = bool(nome_oficial and f"@{nome_oficial.lower()}" in prompt_txt) or (bool(ref_oficial) and ref_oficial.lower() in prompt_txt)

        if (stype in tipos_humanos or tem_tag or c.get("uses_character") is True) and bool(nome_oficial):
            c["uses_character"] = True
            c["character_ref"] = (c.get("character_ref") or "").strip() or ref_oficial
            if c["character_ref"] and not c["character_ref"].startswith("@"):
                c["character_ref"] = f"@{c['character_ref']}"
        elif stype in ("broll_macro", "environment", "before_after", "comparison", "broll_action") and not tem_tag:
            c["uses_character"] = False
            c["character_ref"] = ""
        elif not bool(nome_oficial):
            c["uses_character"] = False
            c["character_ref"] = ""

    return cenas


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------

def carregar_scene_plan(projeto: str) -> dict | None:
    """Carrega lira_scene_plan.json ou None se não existir."""
    path = _scene_plan_path(projeto)
    if not path.exists():
        return None
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
        if plan and "cenas" in plan:
            plan["cenas"] = sincronizar_trava_identidade_cenas(plan["cenas"], projeto_id=projeto)
        return plan
    except Exception as e:
        log_event("SCENE_PLAN", f"{projeto}: erro ao carregar scene_plan: {e}", level="error")
        return None


def salvar_scene_plan(projeto: str, plan: dict) -> bool:
    """Salva lira_scene_plan.json atomicamente com trava de identidade garantida."""
    path = _scene_plan_path(projeto)
    path.parent.mkdir(parents=True, exist_ok=True)
    if plan and "cenas" in plan:
        plan["cenas"] = sincronizar_trava_identidade_cenas(plan["cenas"], projeto_id=projeto)
    content = json.dumps(plan, indent=2, ensure_ascii=False)
    tmp = path.with_suffix(".json.tmp")
    import time
    for tentativa in range(5):
        try:
            tmp.write_text(content, encoding="utf-8")
            os.replace(str(tmp), str(path))
            return True
        except PermissionError:
            time.sleep(0.08)
        except Exception:
            break
    try:
        path.write_text(content, encoding="utf-8")
        return True
    except Exception as e:
        log_event("SCENE_PLAN", f"{projeto}: erro ao salvar scene_plan: {e}", level="error")
        return False


# ---------------------------------------------------------------------------
# Geração
# ---------------------------------------------------------------------------

def gerar_scene_plan(projeto: str, force: bool = False) -> dict:
    """
    Gera lira_scene_plan.json a partir de cenas.json + storyboard (beats).

    - Se o arquivo já existir e force=False, retorna o existente.
    - Classifica automaticamente cenas com personagem (uses_character, character_ref).
    - Constrói prompts visuais limpos em inglês.
    - Emite logs SCENE_PLAN_CREATED_OK e SCENE_CLASSIFIED_OK.
    """
    # Reaproveita existente
    if not force:
        existing = carregar_scene_plan(projeto)
        if existing and existing.get("cenas"):
            return {"success": True, "total": len(existing["cenas"]), "existente": True}

    project_dir = _project_dir(projeto)

    # --- Carrega cenas.json ---
    cenas_file = project_dir / "cenas.json"
    if not cenas_file.exists():
        return {"success": False, "error": "cenas.json não encontrado"}
    try:
        cenas_raw = json.loads(cenas_file.read_text(encoding="utf-8"))
    except Exception as e:
        return {"success": False, "error": f"cenas.json inválido: {e}"}

    if not isinstance(cenas_raw, list):
        return {"success": False, "error": "cenas.json não é lista"}

    # --- Carrega tipo de mídia do storyboard de beats por sobreposição de tempo ---
    from services.scene_media_type import obter_tipo_media_por_cena
    tipo_por_cena_raw = obter_tipo_media_por_cena(projeto)
    tipo_por_cena: dict[int, str] = {
        cid: (TIPO_VIDEO if mt == "video" else TIPO_IMAGE)
        for cid, mt in tipo_por_cena_raw.items()
    }

    # Garante estrutura de pastas Studio 2.0
    garantir_estrutura_pastas(projeto)

    # --- Preserva estado anterior se existir ---
    anterior: dict[int, dict] = {}
    old = carregar_scene_plan(projeto)
    if old and old.get("cenas"):
        for c in old["cenas"]:
            anterior[int(c.get("id", 0))] = c

    # Obtém identidade oficial do personagem ativo do projeto se existir
    import services.character_service as character_svc
    idt = character_svc.obter_identidade_projeto(projeto)
    nome_pers_default = idt.get("nome", "") if idt else ""
    ref_flow_default = idt.get("referencia_flow", f"@{nome_pers_default}" if nome_pers_default else "") if idt else ""
    estilo_visual = idt.get("visual_style", "") if idt else ""

    modo_producao = "imagem_video"
    meta_file = project_dir / "meta.json"
    if meta_file.exists():
        try:
            meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
            if not nome_pers_default:
                nome_pers_default = meta_data.get("nome_personagem", "")
                if nome_pers_default:
                    ref_flow_default = f"@{nome_pers_default}"
            if not estilo_visual:
                estilo_visual = meta_data.get("estilo_visual", "")
            modo_producao = meta_data.get("modo_producao", "imagem_video")
        except Exception:
            pass

    if not estilo_visual:
        estilo_visual = "photorealistic_cinematic"

    # 1. VISUAL DIRECTOR AI: Análise Macro da Narrativa
    import services.visual_director_service as visual_director_svc
    import services.scene_classifier_service as scene_classifier_svc
    import services.character_decision_service as character_decision_svc
    import services.emotion_director_service as emotion_director_svc
    import services.continuity_memory_service as continuity_memory_svc
    import services.story_rhythm_service as story_rhythm_svc
    import services.camera_director_service as camera_director_svc
    import services.broll_intelligence_service as broll_intelligence_svc
    import services.prompt_builder_service as prompt_builder_svc

    contexto_visual = visual_director_svc.analisar_roteiro_completo(
        projeto_id=projeto,
        cenas_raw=cenas_raw,
        nome_personagem_default=nome_pers_default,
        estilo_visual=estilo_visual
    )

    # 2. Primeira passada: Classificação, Decisão de Personagem, Emoção, Câmera, B-roll e Continuidade
    novas_cenas = []
    camera_anterior = None

    # FASE 4.0 — Visual Memory Engine: bíblia visual consultada por todas as cenas.
    import services.visual_memory_engine as vme_svc
    import services.continuity_checker_service as continuity_checker_svc
    memoria_visual = vme_svc.construir_memoria_visual_projeto(
        projeto_id=projeto,
        contexto_visual=contexto_visual,
        identidade=idt,
        roteiro_texto=" ".join(str(c.get("texto") or c.get("text") or c.get("narration") or "") for c in cenas_raw)
    )

    for idx_loop, c in enumerate(cenas_raw):
        cid  = int(c.get("id", idx_loop + 1))
        ini  = float(c.get("start_time") or c.get("start") or 0)
        fim  = float(c.get("end_time")   or c.get("end")   or ini + 5.0)
        texto = str(c.get("texto") or c.get("text") or c.get("narration") or "")
        dur   = max(0.0, fim - ini)

        if modo_producao == "somente_imagens":
            tipo = TIPO_IMAGE
            animar_default = False
        else:
            tipo = tipo_por_cena.get(cid, TIPO_IMAGE)
            animar_default = (tipo == TIPO_IMAGE and dur >= 2.0)

        # Base da cena
        entrada = _nova_cena(cid, ini, fim, texto, tipo, animar_default, nome_personagem=nome_pers_default, modo_producao=modo_producao)

        # A) Scene Classifier AI
        classif = scene_classifier_svc.classificar_cena(
            cena=entrada,
            contexto_visual=contexto_visual,
            index=idx_loop,
            total_cenas=len(cenas_raw),
            nome_personagem=nome_pers_default
        )
        entrada["scene_type"] = classif["scene_type"]
        entrada["visual_role"] = classif["visual_role"]
        entrada["uses_character"] = classif["uses_character"]

        # B) Character Decision System (Prioridade 1 a 4 sem @Homem)
        char_dec = character_decision_svc.decidir_personagem_cena(
            projeto_id=projeto,
            cena=entrada,
            scene_type=entrada["scene_type"],
            contexto_visual=contexto_visual
        )
        entrada["uses_character"] = char_dec["uses_character"]
        entrada["character_ref"] = char_dec["character_ref"]

        # FASE 3.3 — narrativa em primeira pessoa/experiência/demonstração força
        # personagem; sobrescreve o scene_type para avatar_talking|hybrid quando
        # o classificador tinha caído em b-roll. (origem narrative_first_person)
        if (char_dec.get("origem") == "narrative_first_person"
                and char_dec.get("scene_type_override")
                and entrada.get("scene_type") not in ("avatar_talking", "avatar_action", "hybrid", "cta")):
            overriding = char_dec["scene_type_override"]
            print(f"[LOG] CHARACTER_DECISION_OVERRIDE: Cena {cid:03d} {entrada['scene_type']} -> {overriding} (1ª pessoa + personagem bloqueado)", flush=True)
            entrada["scene_type"] = overriding
            entrada["uses_character"] = True

        # C) Emotion Director AI
        emocao = emotion_director_svc.direcionar_emocao(
            cena=entrada,
            scene_type=entrada["scene_type"],
            index=idx_loop,
            total_cenas=len(cenas_raw),
            contexto_visual=contexto_visual
        )
        entrada["emotion"] = emocao["emotion"]
        entrada["energy"] = emocao["energy"]
        entrada["lighting_mood"] = emocao["lighting_mood"]

        # D) Camera Director AI
        cam = camera_director_svc.direcionar_camera(
            cena=entrada,
            scene_type=entrada["scene_type"],
            index=idx_loop,
            camera_anterior=camera_anterior
        )
        entrada["camera_direction"] = cam
        camera_anterior = cam

        # E) B-Roll Intelligence Layer
        broll = broll_intelligence_svc.gerar_broll_inteligente(
            cena=entrada,
            scene_type=entrada["scene_type"],
            contexto_visual=contexto_visual
        )
        entrada["supporting_visuals"] = broll

        # F) Continuity Memory Layer
        continuidade = continuity_memory_svc.gerar_contexto_continuidade_cena(
            projeto_id=projeto,
            cena=entrada,
            scene_type=entrada["scene_type"],
            uses_character=entrada["uses_character"],
            character_ref=entrada["character_ref"],
            index=idx_loop,
            total_cenas=len(cenas_raw),
            contexto_visual=contexto_visual
        )
        entrada["continuity_context"] = continuidade
        entrada["media_intent"] = "video" if (entrada.get("animate_later") or entrada.get("animar") or entrada.get("tipo") == TIPO_VIDEO) else "image"

        # FASE 4.0 — Continuity Checker: valida a cena contra a bíblia visual.
        cc = continuity_checker_svc.verificar_continuidade_cena(
            cena=entrada,
            memoria_visual=memoria_visual,
            contexto_visual=contexto_visual,
            index=idx_loop
        )
        entrada["memory_used"] = True
        entrada["continuity_score"] = cc.get("continuity_score", 100)
        if cc.get("warnings"):
            entrada["selection_reason"] = "; ".join(cc["warnings"][:2])

        # Se o modo for imagem_video_texto, detecta cenas transicionais / puramente textuais
        if modo_producao in ("imagem_video_texto", "img_video_texto"):
            palavras = re.findall(r"\w+", texto.lower())
            termos_transicao = ["por outro lado", "ou seja", "em resumo", "portanto", "isso nos leva", "enfim", "dito isso", "além disso", "como vimos", "resumindo", "em outras palavras"]
            eh_transicao = any(t in texto.lower() for t in termos_transicao)
            sem_sujeito_concreto = (not entrada.get("uses_character") and entrada.get("scene_type") in ("broll_macro", "environment") and (len(palavras) <= 5 or eh_transicao))
            if eh_transicao or sem_sujeito_concreto:
                entrada["tipo"] = TIPO_TEXT
                entrada["scene_type"] = "text"
                entrada["media_intent"] = "text"

        # Preserva arquivos anteriores se já existiam
        if cid in anterior:
            prev = anterior[cid]
            entrada["arquivo_midia"] = prev.get("arquivo_midia", "")
            entrada["download_path"] = prev.get("download_path", entrada["arquivo_midia"])
            entrada["status"] = prev.get("status", STATUS_PENDENTE)
            entrada["image_status"] = prev.get("image_status", IMAGE_STATUS_PENDING)
            entrada["video_status"] = prev.get("video_status", VIDEO_STATUS_NOT_STARTED)

        novas_cenas.append(entrada)

    # 3. Story Rhythm Director: Otimização de Cadência e Alternância
    novas_cenas = story_rhythm_svc.otimizar_ritmo_cenas(novas_cenas, contexto_visual)

    # 3.5. FASE 4.1 — Storyboard Director AI: estrutura arcos, propósitos dramáticos e conexões narrativas
    import services.storyboard_director_service as storyboard_director_svc
    novas_cenas = storyboard_director_svc.analisar_storyboard_narrativo(
        projeto_id=projeto,
        cenas=novas_cenas,
        contexto_visual=contexto_visual,
        memoria_visual=memoria_visual
    )

    # 3.7. FASE 7.0 — Retention Director AI: otimização de retenção e quebra de monotonia
    import services.retention_director_service as retention_director_svc
    ret_res = retention_director_svc.otimizar_retencao_projeto(novas_cenas, contexto_visual)
    novas_cenas = ret_res["scenes"]

    # 3.8. FASE 6.0 — Animation Director AI: decisão inteligente de movimento por cena
    import services.animation_director_service as animation_director_svc
    for idx_loop, entrada in enumerate(novas_cenas):
        if modo_producao == "somente_imagens":
            entrada["tipo"] = TIPO_IMAGE
            entrada["animate_later"] = False
            entrada["animar_depois"] = False
            entrada["animar"] = False
            entrada["media_intent"] = "image"
            entrada["animation_type"] = "none"
            entrada["animation_priority"] = "none"
            entrada["motion_vector"] = "static"
            entrada["animation_rationale"] = "Modo de produção: Somente Imagens (todas as cenas estáticas sem geração de vídeo/animação)."
            entrada["prompt_animacao"] = ""
        elif entrada.get("tipo") == TIPO_TEXT or entrada.get("scene_type") == "text":
            entrada["tipo"] = TIPO_TEXT
            entrada["scene_type"] = "text"
            entrada["animate_later"] = False
            entrada["animar_depois"] = False
            entrada["animar"] = False
            entrada["media_intent"] = "text"
            entrada["animation_type"] = "none"
            entrada["animation_priority"] = "none"
            entrada["motion_vector"] = "none"
            entrada["animation_rationale"] = "Cena de texto / transição narrativa sem mídia gerada."
            entrada["prompt_animacao"] = ""
            entrada["prompt_imagem"] = ""
        else:
            anim_dec = animation_director_svc.direcionar_animacao_cena(entrada, contexto_visual, idx_loop)
            entrada["animate_later"] = anim_dec["should_animate"]
            entrada["animar_depois"] = anim_dec["should_animate"]
            entrada["animar"] = anim_dec["should_animate"]
            entrada["media_intent"] = "video" if anim_dec["should_animate"] or entrada.get("tipo") == TIPO_VIDEO else "image"
            entrada["animation_type"] = anim_dec["animation_type"]
            entrada["animation_priority"] = anim_dec["animation_priority"]
            entrada["motion_vector"] = anim_dec["motion_vector"]
            entrada["animation_rationale"] = anim_dec["animation_rationale"]
            if anim_dec["prompt_animacao"]:
                entrada["prompt_animacao"] = anim_dec["prompt_animacao"]

    # 4. Prompt Builder AI + Prompt History System (FASE 4.2)
    import services.prompt_history_service as prompt_history_svc
    for idx_loop, entrada in enumerate(novas_cenas):
        if entrada.get("tipo") == TIPO_TEXT or entrada.get("scene_type") == "text":
            entrada["prompt_imagem"] = ""
            entrada["visual_prompt"] = ""
            entrada["prompt_animacao"] = ""
        elif not entrada.get("prompt_imagem"):
            prompts_res = prompt_builder_svc.construir_prompt_diretor(
                projeto_id=projeto,
                cena=entrada,
                contexto_visual=contexto_visual,
                index=idx_loop,
                total_cenas=len(novas_cenas)
            )
            entrada["prompt_imagem"] = prompts_res["prompt_imagem"]
            entrada["visual_prompt"] = prompts_res["prompt_imagem"]
            if modo_producao == "somente_imagens":
                entrada["prompt_animacao"] = ""
            elif not entrada.get("prompt_animacao"):
                entrada["prompt_animacao"] = prompts_res["prompt_animacao"]

        # Registra no histórico de prompt scene_XXX.txt
        p_hist = prompt_history_svc.registrar_historico_prompt_cena(
            projeto_id=projeto,
            cena=entrada,
            memoria_visual=memoria_visual
        )
        entrada["prompt_history_path"] = p_hist
        entrada["decision_logged"] = True

        print(f"[LOG] SCENE_DIRECTOR_OK: Cena {entrada['id']:03d} -> story_role='{entrada['story_role']}', type='{entrada['scene_type']}', uses_character={entrada['uses_character']} (ref: '{entrada['character_ref']}'), retention='{entrada['retention_goal']}', animate={entrada['animate_later']} ({entrada['animation_type']})", flush=True)
        log_event("SCENE_PLAN", f"SCENE_DIRECTOR_OK: Cena {entrada['id']:03d} story_role={entrada['story_role']} type={entrada['scene_type']} animate={entrada['animate_later']}")

    plan = {
        "projeto":    projeto,
        "versao":     2,
        "gerado_em":  datetime.now().isoformat(sep=" ", timespec="seconds"),
        "total":      len(novas_cenas),
        "cenas":      novas_cenas,
        "visual_context": contexto_visual
    }

    ok = salvar_scene_plan(projeto, plan)
    if ok:
        # FASE 11.0 — Cria snapshot da versão inicial e calcula production_metrics.json
        import services.project_version_service as version_svc
        import services.production_metrics_engine as metrics_engine_svc
        version_svc.criar_nova_versao(projeto, changes=["Initial autonomous scene plan generation"])
        metrics_engine_svc.calcular_e_salvar_metricas(projeto)

        print(f"[LOG] SCENE_PLAN_CREATED_OK: Planejamento completo de {len(novas_cenas)} cenas gerado com sucesso pelo Visual Director AI.", flush=True)
        log_event("SCENE_PLAN", f"SCENE_PLAN_CREATED_OK: {len(novas_cenas)} cenas planejadas.")

    return {"success": ok, "total": len(novas_cenas), "existente": False, "plan": plan}


# ---------------------------------------------------------------------------
# Atualização por cena
# ---------------------------------------------------------------------------

def atualizar_cena(projeto: str, scene_id: int, campos: dict) -> dict:
    """
    Atualiza campos de uma cena no scene_plan.json.
    campos pode conter qualquer subconjunto de campos editáveis.
    Retorna {"success": bool, "error": str?}
    """
    plan = carregar_scene_plan(projeto)
    if plan is None:
        return {"success": False, "error": "scene_plan não encontrado"}

    # Validação prévia de campos "status" e "tipo"
    for k, v in campos.items():
        if k == "status" and v not in STATUS_VALIDOS:
            log_event("SCENE_PLAN",
                       f"{projeto}: status inválido '{v}' para cena {scene_id}",
                       level="warn")
            return {"success": False, "error": f"valor inválido para {k}: {v}"}
        if k == "tipo" and v not in (TIPO_IMAGE, TIPO_VIDEO, TIPO_TEXT):
            log_event("SCENE_PLAN",
                       f"{projeto}: tipo inválido '{v}' para cena {scene_id}",
                       level="warn")
            return {"success": False, "error": f"valor inválido para {k}: {v}"}

    CAMPOS_EDITAVEIS = {
        "tipo", "animar", "personagem_ref", "prompt_imagem",
        "prompt_animacao", "arquivo_midia", "status", "erro_msg",
        "nome_personagem", "modo_producao", "referencia_visual",
        "continuidade", "timestamp_saida", "image_status", "video_status",
        "visual_prompt", "narration", "uses_character", "character_ref",
        "animate_later", "animar_depois", "filename", "download_path",
        "start", "end", "timestamp", "original_timestamp", "scene_index",
        "scene_type", "visual_role", "emotion", "energy", "camera_direction",
        "supporting_visuals", "continuity_context", "lighting_mood", "media_intent",
        "memory_used", "continuity_score", "visual_score", "judgment_status", "selection_reason",
        "story_role", "narrative_purpose", "retention_goal", "previous_scene_connection",
        "next_scene_connection", "prompt_history_path", "decision_logged",
        "variations_evaluated", "best_variation_index", "variation_selection_rationale",
        "animation_type", "animation_priority", "motion_vector", "animation_rationale",
        "retention_index", "retention_cues", "pattern_interrupt",
        "human_status", "human_note", "approved_by", "manual_intervention",
    }

    cena_encontrada = False
    for cena in plan.get("cenas", []):
        if int(cena.get("id", -1)) == int(scene_id):
            cena_encontrada = True
            for k, v in campos.items():
                if k not in CAMPOS_EDITAVEIS:
                    continue
                cena[k] = v
            cena["atualizado_em"] = datetime.now().isoformat(sep=" ", timespec="seconds")
            break

    if not cena_encontrada:
        return {"success": False, "error": f"cena {scene_id} não encontrada"}

    ok = salvar_scene_plan(projeto, plan)
    return {"success": ok}


def atualizar_status_cena(projeto: str, scene_id: int, novo_status: str) -> dict:
    """Atalho para atualizar apenas o status de uma cena."""
    return atualizar_cena(projeto, scene_id, {"status": novo_status})


# ---------------------------------------------------------------------------
# Contagem / progresso
# ---------------------------------------------------------------------------

def progresso_scene_plan(projeto: str) -> dict:
    """
    Retorna contagens por status e se a montagem está liberada.
    {total, prontas, por_status, pode_montar}
    """
    plan = carregar_scene_plan(projeto)
    if plan is None:
        return {"total": 0, "prontas": 0, "por_status": {}, "pode_montar": False}

    cenas = plan.get("cenas", [])
    por_status: dict[str, int] = {}
    for c in cenas:
        st = c.get("status", STATUS_PENDENTE)
        por_status[st] = por_status.get(st, 0) + 1

    status_prontos = {
        STATUS_BAIXADA,
        STATUS_GERADA,
        "READY",
        "CONCLUIDA",
        "MIDIA_IMPORTADA",
        "ANIMADA",
        "PRONTA_PARA_MONTAGEM",
        "MONTADA",
    }
    prontas = sum(count for st, count in por_status.items() if st in status_prontos)
    total   = len(cenas)
    return {
        "total":       total,
        "prontas":     prontas,
        "por_status":  por_status,
        "pode_montar": total > 0 and prontas == total,
    }


# ---------------------------------------------------------------------------
# Integração com midias_encontradas.json (compatibilidade video_builder)
# ---------------------------------------------------------------------------

def sincronizar_midias_encontradas(projeto: str) -> int:
    """
    Lê o lira_scene_plan.json e atualiza/cria midias_encontradas.json
    com os arquivos de mídia registrados em arquivo_midia ou presentes no disco.

    Chamado após download pelo Flow para manter compatibilidade com video_builder.
    Retorna o número de cenas sincronizadas.
    """
    plan = carregar_scene_plan(projeto)
    if plan is None:
        return 0

    pdir = _project_dir(projeto)
    midias_file = pdir / "midias_encontradas.json"
    try:
        midias_existentes: list = json.loads(midias_file.read_text(encoding="utf-8")) \
            if midias_file.exists() else []
    except Exception:
        midias_existentes = []

    # Índice por scene_id para upsert
    idx: dict[str, int] = {
        str(m.get("scene_id", "")): i
        for i, m in enumerate(midias_existentes)
    }

    sincronizadas = 0
    modificado = False
    for cena in plan.get("cenas", []):
        arquivo = cena.get("arquivo_midia", "")
        cid = int(cena.get("id", 0))

        # Smart resume: busca em todas as pastas do projeto (cenas, imagens, subpastas)
        if not arquivo or not Path(arquivo).exists() or Path(arquivo).stat().st_size <= 500:
            ts_ini = float(cena.get("tempo_inicio", 0))
            ts_fim = float(cena.get("tempo_fim", ts_ini + 5))
            nome_padrao_png = formatar_nome_arquivo_cena_padrao(cid, ts_ini, ts_fim, ".png")
            nome_padrao_mp4 = formatar_nome_arquivo_cena_padrao(cid, ts_ini, ts_fim, ".mp4")

            candidatos = [
                pdir / "cenas" / f"{cid:03d}.png",
                pdir / "imagens" / f"{cid:03d}.png",
                pdir / "cenas" / f"{cid:03d}.mp4",
                pdir / "cenas" / nome_padrao_png,
                pdir / "cenas" / nome_padrao_mp4,
                pdir / "cenas" / f"cena_{cid:03d}" / "imagem.png",
                pdir / "cenas" / f"cena_{cid:03d}" / "video.mp4",
            ]
            # Adiciona subpastas dinamicas cena_001_*
            cenas_sub = list((pdir / "cenas").glob(f"cena_{cid:03d}_*"))
            for sdir in cenas_sub:
                if sdir.is_dir():
                    candidatos.append(sdir / "imagem.png")
                    candidatos.append(sdir / "video.mp4")
                    candidatos.append(sdir / f"{cid:03d}.png")

            for cand in candidatos:
                if cand.exists() and cand.is_file() and cand.stat().st_size > 500:
                    arquivo = str(cand)
                    cena["arquivo_midia"] = arquivo
                    cena["filename"] = cand.name
                    cena["status"] = STATUS_BAIXADA
                    cena["image_status"] = IMAGE_STATUS_READY if cand.suffix.lower() in IMAGEM_EXT else IMAGE_STATUS_DOWNLOADED
                    modificado = True
                    break

        if not arquivo or not Path(arquivo).exists():
            continue

        ext = Path(arquivo).suffix.lower()
        media_type = "photo" if ext in IMAGEM_EXT else "video"

        entrada = {
            "scene_id":    cena["id"],
            "success":     True,
            "arquivo":     arquivo,
            "quality":     "green",
            "media_type":  media_type,
            "origem_midia": "flow_automation",
        }

        sid = str(cena["id"])
        if sid in idx:
            midias_existentes[idx[sid]] = entrada
        else:
            midias_existentes.append(entrada)
            idx[sid] = len(midias_existentes) - 1

        sincronizadas += 1

    if modificado:
        salvar_scene_plan(projeto, plan)

    midias_file.write_text(
        json.dumps(midias_existentes, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return sincronizadas


# ---------------------------------------------------------------------------
# Nome canônico de arquivo para download do Flow
# ---------------------------------------------------------------------------

def nome_arquivo_para_cena(projeto: str, scene_id: int, ext: str = ".jpg") -> str:
    """
    Retorna o nome de arquivo canônico para a mídia de uma cena.
    Ex.: 003_[00-14]_mulher_correndo.jpg
    """
    plan = carregar_scene_plan(projeto)
    if plan is None:
        return f"{scene_id:03d}{ext}"
    return f"{scene_id:03d}{ext}"


# ---------------------------------------------------------------------------
# Detecção de Personagem na Cena
# ---------------------------------------------------------------------------

_KEYWORDS_PERSONAGEM = {
    "personagem", "character", "person", "pessoa", "man", "homem", "woman", "mulher",
    "gardener", "jardineiro", "jardineira", "guy", "rapaz", "garoto", "garota", "boy", "girl",
    "people", "gente", "alguém", "someone", "narrador", "narrator", "presenter", "apresentador",
    "host", "doctor", "médico", "worker", "trabalhador", "farmer", "fazendeiro", "actor", "ator",
    "atriz", "actress", "speaker", "palestrante", "humano", "human", "face", "rosto", "portrait",
    "retrat", "selfie", "ele ", "ela ", "he ", "she ", "him", "her", "@personagem"
}

def _cena_tem_personagem(texto: str, nome_personagem: str = "") -> bool:
    t = (texto or "").lower()
    if nome_personagem and (nome_personagem.lower() in t or f"@{nome_personagem.lower()}" in t):
        return True
    return any(k in t for k in _KEYWORDS_PERSONAGEM)


def obter_nome_projeto(projeto_id: str) -> str:
    """Retorna o nome amigável do projeto (display_name/name em meta.json ou o próprio ID)."""
    meta_file = PROJETOS_DIR / projeto_id / "meta.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            return meta.get("display_name") or meta.get("name") or meta.get("titulo") or projeto_id
        except Exception:
            pass
    return projeto_id


# Alias de conveniência
sincronizar_galeria_projeto = indexar_midias_projeto


