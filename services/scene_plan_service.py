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

from config import PROJETOS_DIR
from services.event_logger import log_event

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SCENE_PLAN_FILE = "lira_scene_plan.json"

STATUS_PENDENTE             = "PENDENTE"
STATUS_ENVIADA              = "ENVIADA"
STATUS_GERANDO              = "GERANDO"
STATUS_PROMPT_PRONTO        = "PROMPT_PRONTO"
STATUS_MIDIA_IMPORTADA      = "MIDIA_IMPORTADA"
STATUS_PRONTA_PARA_ANIMAR   = "PRONTA_PARA_ANIMAR"
STATUS_ANIMADA              = "ANIMADA"
STATUS_PRONTA_PARA_MONTAGEM = "PRONTA_PARA_MONTAGEM"
STATUS_MONTADA              = "MONTADA"
STATUS_ERRO                 = "ERRO"

STATUS_VALIDOS = (
    STATUS_PENDENTE, STATUS_ENVIADA, STATUS_GERANDO, STATUS_PROMPT_PRONTO,
    STATUS_MIDIA_IMPORTADA, STATUS_PRONTA_PARA_ANIMAR, STATUS_ANIMADA,
    STATUS_PRONTA_PARA_MONTAGEM, STATUS_MONTADA, STATUS_ERRO,
)

TIPO_IMAGE = "image"
TIPO_VIDEO = "video"

IMAGEM_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


PASTAS_PROJETO_V2 = ("audio", "srt", "imagens", "videos", "prompts", "capcut")


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
    return {
        "id":                       cid,
        "texto":                    str(texto or ""),
        "tempo_inicio":             round(float(tempo_inicio), 3),
        "tempo_fim":                round(float(tempo_fim), 3),
        "duracao":                  round(max(0.0, float(tempo_fim) - float(tempo_inicio)), 3),
        "tipo":                     tipo,            # "image" | "video"
        "animar":                   bool(animar),    # animar imagem → vídeo no Flow
        "personagem_ref":           "",              # path local da imagem de referência
        "prompt_imagem":            "",              # prompt para geração de imagem
        "prompt_animacao":          "",              # prompt para animação (modo vídeo)
        "arquivo_midia":            "",              # path local do arquivo gerado/importado
        "status":                   STATUS_PENDENTE,
        # --- Campos Studio 2.0 (Fase 1) ---
        "nome_personagem":          str(nome_personagem or ""),
        "modo_producao":            str(modo_producao or "imagem_video"),
        "referencia_visual":        str(referencia_visual or ""),
        "continuidade":             bool(continuidade),
        "timestamp_saida":          f"{ts_ini}_{ts_fim}",
        "atualizado_em":            datetime.now().isoformat(sep=" ", timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------

def carregar_scene_plan(projeto: str) -> dict | None:
    """Carrega lira_scene_plan.json ou None se não existir."""
    path = _scene_plan_path(projeto)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log_event("SCENE_PLAN", f"{projeto}: erro ao carregar scene_plan: {e}", level="error")
        return None


def salvar_scene_plan(projeto: str, plan: dict) -> bool:
    """Salva lira_scene_plan.json atomicamente."""
    path = _scene_plan_path(projeto)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(path))
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
    - tipo por cena vem de services.scene_media_type.obter_tipo_media_por_cena
      (mesma função usada por app_web._tipo_media_por_cena): casa os beats do
      storyboard por SOBREPOSIÇÃO DE TEMPO com a cena e usa o tipo dominante
      ("video" | "photo" → "image"). Nunca mapeia por id direto.
    - animar=True para cenas de imagem com duração >= 2s (padrão editável depois).
    - Preserva prompts e status de uma versão anterior se existir.
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

    # --- Monta plano ---
    novas_cenas = []
    for c in cenas_raw:
        cid  = int(c.get("id", 0))
        ini  = float(c.get("start_time") or c.get("start") or 0)
        fim  = float(c.get("end_time")   or c.get("end")   or ini + 5.0)
        texto = str(c.get("texto") or c.get("text") or "")
        tipo  = tipo_por_cena.get(cid, TIPO_IMAGE)
        dur   = max(0.0, fim - ini)

        # animar=True padrão para imagens com duração >= 2s
        animar_default = (tipo == TIPO_IMAGE and dur >= 2.0)

        if cid in anterior:
            prev = anterior[cid]
            entrada = _nova_cena(
                cid, ini, fim, texto, tipo, animar_default,
                nome_personagem=prev.get("nome_personagem", ""),
                modo_producao=prev.get("modo_producao", "imagem_video"),
                referencia_visual=prev.get("referencia_visual", ""),
                continuidade=prev.get("continuidade", True),
            )
            # Preserva campos editáveis do anterior
            entrada["animar"]            = prev.get("animar", animar_default)
            entrada["personagem_ref"]    = prev.get("personagem_ref", "")
            entrada["prompt_imagem"]     = prev.get("prompt_imagem", "")
            entrada["prompt_animacao"]   = prev.get("prompt_animacao", "")
            entrada["arquivo_midia"]     = prev.get("arquivo_midia", "")
            entrada["status"]            = prev.get("status", STATUS_PENDENTE)
            entrada["nome_personagem"]   = prev.get("nome_personagem", "")
            entrada["modo_producao"]     = prev.get("modo_producao", "imagem_video")
            entrada["referencia_visual"] = prev.get("referencia_visual", "")
            entrada["continuidade"]      = prev.get("continuidade", True)
            entrada["timestamp_saida"]   = prev.get("timestamp_saida", f"{_fmt_ts(ini)}_{_fmt_ts(fim)}")
            entrada["atualizado_em"]     = prev.get("atualizado_em",
                                            datetime.now().isoformat(sep=" ", timespec="seconds"))
        else:
            entrada = _nova_cena(cid, ini, fim, texto, tipo, animar_default)

        novas_cenas.append(entrada)

    plan = {
        "projeto":    projeto,
        "versao":     1,
        "gerado_em":  datetime.now().isoformat(sep=" ", timespec="seconds"),
        "total":      len(novas_cenas),
        "cenas":      novas_cenas,
    }

    ok = salvar_scene_plan(projeto, plan)
    if ok:
        log_event("SCENE_PLAN", f"{projeto}: scene_plan gerado com {len(novas_cenas)} cenas",
                  level="info", details={"projeto": projeto, "total": len(novas_cenas)})

    return {"success": ok, "total": len(novas_cenas), "existente": False}


# ---------------------------------------------------------------------------
# Atualização por cena
# ---------------------------------------------------------------------------

def atualizar_cena(projeto: str, scene_id: int, campos: dict) -> dict:
    """
    Atualiza campos de uma cena no scene_plan.json.
    campos pode conter qualquer subconjunto de:
      tipo, animar, personagem_ref, prompt_imagem, prompt_animacao,
      arquivo_midia, status, nome_personagem, modo_producao,
      referencia_visual, continuidade, timestamp_saida
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
        if k == "tipo" and v not in (TIPO_IMAGE, TIPO_VIDEO):
            log_event("SCENE_PLAN",
                       f"{projeto}: tipo inválido '{v}' para cena {scene_id}",
                       level="warn")
            return {"success": False, "error": f"valor inválido para {k}: {v}"}

    CAMPOS_EDITAVEIS = {
        "tipo", "animar", "personagem_ref", "prompt_imagem",
        "prompt_animacao", "arquivo_midia", "status", "erro_msg",
        "nome_personagem", "modo_producao", "referencia_visual",
        "continuidade", "timestamp_saida",
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

    prontas = (
        por_status.get(STATUS_MIDIA_IMPORTADA, 0)
        + por_status.get(STATUS_ANIMADA, 0)
        + por_status.get(STATUS_PRONTA_PARA_MONTAGEM, 0)
        + por_status.get(STATUS_MONTADA, 0)
    )
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
    com os arquivos de mídia registrados em arquivo_midia.

    Chamado após download pelo Flow para manter compatibilidade com video_builder.
    Retorna o número de cenas sincronizadas.
    """
    plan = carregar_scene_plan(projeto)
    if plan is None:
        return 0

    midias_file = _project_dir(projeto) / "midias_encontradas.json"
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
    for cena in plan.get("cenas", []):
        arquivo = cena.get("arquivo_midia", "")
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

def _cena_tem_personagem(texto: str) -> bool:
    t = (texto or "").lower()
    return any(k in t for k in _KEYWORDS_PERSONAGEM)

