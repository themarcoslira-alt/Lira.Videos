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
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any, Set, Tuple

from config import PROJETOS_DIR
from services.event_logger import log_event
from services.srt_tag_service import ler_srt_caminho

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
IMAGE_STATUS_ERROR      = "ERROR"

# VIDEO STATUS (Máquina de Estados de Vídeo)
VIDEO_STATUS_NOT_STARTED = "NOT_STARTED"
VIDEO_STATUS_QUEUED      = "QUEUED"
VIDEO_STATUS_GENERATING  = "GENERATING"
VIDEO_STATUS_READY       = "READY"
VIDEO_STATUS_ERROR       = "ERROR"

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

# --- Transiciones (CapCut Nativo & Legado) ---
TRANSICIONES_TIPOS = (
    "none", "fade_in", "fade_out", "dissolve", "slow_in", "slow_out",
    "bordas_difusas", "sobrepor", "combinar", "circulo", "retalhos_do_caos",
    "barra_de_luz", "espelho"
)
TRANSICION_DURACION_MIN_MS = 100
TRANSICION_DURACION_MAX_MS = 2000
TRANSICION_ENTRADA_DEFAULT = {"tipo": "bordas_difusas", "duracao_ms": 500}
TRANSICION_SAIDA_DEFAULT = {"tipo": "bordas_difusas", "duracao_ms": 500}

# ---------------------------------------------------------------------------
# Locks de escrita por arquivo (threads do MESMO processo)
# ---------------------------------------------------------------------------
# A UI (polling) e o worker Playwright rodam em threads diferentes do mesmo
# processo. Sem serialização, duas escritas concorrentes podem cair no fallback
# não-atômico (path.write_text) e CONCATENAR conteúdo no JSON ("Extra data").
# O lock cobre o corpo inteiro de salvar_scene_plan — inclusive o fallback.

_WRITE_LOCKS: Dict[str, threading.Lock] = {}
_WRITE_LOCKS_GUARD = threading.Lock()


def _obter_lock_escrita(projeto: str) -> threading.Lock:
    """Retorna (criando sob demanda) o lock de escrita do scene_plan do projeto."""
    chave = str(_scene_plan_path(projeto))
    with _WRITE_LOCKS_GUARD:
        lock = _WRITE_LOCKS.get(chave)
        if lock is None:
            lock = threading.Lock()
            _WRITE_LOCKS[chave] = lock
        return lock


# ---------------------------------------------------------------------------
# DIAGNÓSTICO DE TRAVAMENTO — trace com timestamp + contenção de lock
# ---------------------------------------------------------------------------
# O fluxo Playwright congelava entre IMAGE_DOWNLOADED_OK e FILE_SAVED_OK. O lock
# de escrita acima era adquirido com `with lock:` = espera INFINITA e SILENCIOSA
# quando outra thread (ex.: polling da UI salvando o plano) o segurava.
# `_adquirir_lock_escrita` NÃO remove o lock (removê-lo voltaria a concatenar
# conteúdo no JSON — "Extra data", ver comentário acima): ele apenas ANUNCIA a
# espera a cada `LOCK_ESCRITA_TIMEOUT_SEG` e imprime o stack de TODAS as threads,
# revelando exatamente quem está segurando o lock e em qual linha.
TRACE_DIAGNOSTICO_ATIVO = True
LOCK_ESCRITA_TIMEOUT_SEG = 10.0

# ---------------------------------------------------------------------------
# I/O DO SCENE_PLAN COM PRAZO RÍGIDO (REQ — demora em "Retomar Projeto/gerar restantes")
# ---------------------------------------------------------------------------
# Sintoma: ao clicar em "gerar restantes"/"Retomar Projeto (N restantes)" a UI podia
# levar 8-12s. Duas causas:
#   1. `carregar_scene_plan` tentava LER o JSON até 3 vezes SEM PRAZO algum (um
#      volume lento ou um lock de arquivo do Windows segurava a requisição);
#   2. o chamador (`/producao/<id>/iniciar_fila`) regravava o plano INTEIRO uma vez
#      por cena pendente (`atualizar_status_cena` em loop) = 2N operações de disco.
# Aqui fica o PRAZO da leitura (5s) e o teto de tentativas (leitura + 1 retry, sem
# loop). O item 2 é resolvido por `resetar_status_cenas` (1 leitura + 1 gravação).
SCENE_PLAN_IO_TIMEOUT_SEG = 5.0
SCENE_PLAN_MAX_TENTATIVAS_LEITURA = 2   # leitura + 1 retry (nunca 3-5x)


def _ler_texto_scene_plan(path: Path, timeout_s: float = SCENE_PLAN_IO_TIMEOUT_SEG) -> str:
    """Lê o scene_plan com PRAZO rígido — nunca segura a requisição indefinidamente.

    Lê em blocos de 256 KB conferindo o prazo ENTRE os blocos: um arquivo grande em
    volume lento deixa de bloquear por tempo indeterminado. Levanta `TimeoutError`
    quando o prazo expira (o chamador decide se ainda cabe 1 retry).
    """
    limite = time.monotonic() + max(0.05, float(timeout_s))
    partes: List[bytes] = []
    with open(path, "rb") as fh:
        while True:
            if time.monotonic() > limite:
                raise TimeoutError(f"leitura do scene_plan excedeu {timeout_s:.1f}s")
            bloco = fh.read(262144)
            if not bloco:
                break
            partes.append(bloco)
    return b"".join(partes).decode("utf-8", errors="replace")


def _print_seguro(texto: str) -> None:
    """`print` que sobrevive a codepage legado (cp850/cp1252/cp437).

    Com stdout redirecionado (pipe/arquivo), `print("→")`/`print("❌")` levanta
    UnicodeEncodeError — e o próprio trace derrubaria a cena. Reencoda com
    'replace' para o diagnóstico continuar legível em vez de quebrar o fluxo.
    """
    try:
        print(texto, flush=True)
    except Exception:
        try:
            import sys as _sys
            enc = getattr(_sys.stdout, "encoding", None) or "ascii"
            print(texto.encode(enc, "replace").decode(enc, "replace"), flush=True)
        except Exception:
            pass


def trace_plan(msg: str, projeto: str = "", level: str = "info") -> None:
    """Trace com timestamp (HH:MM:SS.mmm) no CMD e no console web (fila de eventos)."""
    if not TRACE_DIAGNOSTICO_ATIVO:
        return
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    prefixo = f"[{projeto}] " if projeto else ""
    linha = f"[{ts}] [TRACE] {prefixo}{msg}"
    _print_seguro(linha)
    try:
        log_event("TRACE_SCENE_PLAN", linha, level=level)
    except Exception:
        pass


def dump_threads_stacks(max_frames: int = 12) -> str:
    """Snapshot dos stacks de todas as threads (quem está segurando o lock)."""
    import sys as _sys
    import traceback as _tb
    nomes = {t.ident: t.name for t in threading.enumerate()}
    partes = []
    for tid, frame in _sys._current_frames().items():
        nome = nomes.get(tid, str(tid))
        stack = "".join(_tb.format_stack(frame, limit=max_frames)).rstrip()
        partes.append(f"--- thread '{nome}' (id={tid}) ---\n{stack}")
    return "\n".join(partes)[:6000]


def _adquirir_lock_escrita(projeto: str, timeout_s: float = LOCK_ESCRITA_TIMEOUT_SEG):
    """Adquire o lock do projeto com VISIBILIDADE (nunca desiste — semântica igual).

    Retorna o lock JÁ ADQUIRIDO (o chamador deve liberar em `finally`).
    """
    lock = _obter_lock_escrita(projeto)
    if lock.acquire(timeout=timeout_s):
        return lock

    inicio = time.time()
    trace_plan(f"LOCK DE ESCRITA OCUPADO — aguardando >= {timeout_s:.0f}s. "
               f"Stacks das threads abaixo revelam quem segura:", projeto, level="warn")
    while True:
        dump = dump_threads_stacks()
        _print_seguro(dump)
        try:
            log_event("TRACE_SCENE_PLAN", dump, level="warn")
        except Exception:
            pass
        esperado = time.time() - inicio
        if lock.acquire(timeout=timeout_s):
            trace_plan(f"LOCK DE ESCRITA liberado após {esperado:.1f}s de espera.", projeto, level="warn")
            return lock
        trace_plan(f"LOCK DE ESCRITA ainda ocupado ({time.time() - inicio:.1f}s acumulados).",
                   projeto, level="error")



def tipo_efetivo_cena(cena: dict) -> str:
    """Fonte ÚNICA de tipo de mídia de uma cena.

    Prioridade:
      1. campo 'tipo' (authority): 'image' | 'video'
      2. CORREÇÃO 2: 'animar: true' NÃO força modo vídeo quando 'tipo' é
         explicitamente 'image' — o campo animar indica que a imagem PODE ser
         animada depois, não que deve ser gerada como vídeo agora.
      3. fallback legado por 'animar' (True → video) APENAS quando não há tipo
         explícito definido.
    NUNCA permite que 'animar_depois'/'animate_later' alterem o tipo.
    Retorna sempre 'image' ou 'video'.
    """
    cena = cena or {}
    t = str(cena.get("tipo") or "").lower().strip().strip('"').strip("'")
    if t == TIPO_VIDEO or t == "video":
        return TIPO_VIDEO
    # tipo explícito "image" é autoritativo: animar não converte para vídeo
    if t == TIPO_IMAGE or t == "image":
        return TIPO_IMAGE
    # sem 'tipo' definido → fallback legado por animar
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


PASTAS_PROJETO_V2 = ("audio", "metadata", "prompts", "export", ".temp", "cenas")


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


def formatar_nome_midia_canonico(scene_index: int, tempo_inicio: float, estilo_slug: str = "", ext: str = ".png") -> str:
    """
    Formata o nome da mídia no padrão oficial canônico do Lira Studio:
    {scene_index}_[{MM-SS}]_{style_slug}{ext}

    Regras obrigatórias:
      - scene_index sem zeros à esquerda (1, 2, 14, 100, 120)
      - timestamp = início da cena [MM-SS] com ':' convertido para '-'
      - style_slug sanitizado (ex: Photorealistic_ci, Blender_3D)
      - ext: extensão real recebida (.png, .jpg, .webp, .mp4)
    """
    if not ext.startswith("."):
        ext = f".{ext}"
    ini = float(tempo_inicio or 0)
    mm = int(ini // 60)
    ss = int(ini % 60)
    from services.visual_presets_service import sanitizar_slug_estilo
    slug = sanitizar_slug_estilo(estilo_slug) if estilo_slug else "Photorealistic_ci"
    return f"{int(scene_index)}_[{mm:02d}-{ss:02d}]_{slug}{ext}"


def formatar_nome_arquivo_cena_padrao(cid: int, ts_ini: float, ts_fim: float, ext: str = ".png") -> str:
    """Formata o nome da cena no padrão com intervalo (compatibilidade): 01_[00-00-05].png"""
    if not ext.startswith("."):
        ext = f".{ext}"
    m_ini = int(float(ts_ini or 0) // 60)
    s_ini = int(float(ts_ini or 0) % 60)
    s_fim = int(float(ts_fim or 0) % 60)
    return f"{cid:02d}_[{m_ini:02d}-{s_ini:02d}-{s_fim:02d}]{ext}"


def _nome_cena_timecode(projeto_id: str, cid: int, ts_ini: float, ts_fim: float,
                        ext: str = ".png", is_video: bool = False) -> str:
    """Gera o nome da mídia no padrão canônico aprovado:
    {cid:02d}_[{tc_inicio}-{tc_fim}][ext]  — ex: 01_[00-00-05].png

    - Tenta o campo "timecode" do cena no lira_scene_plan.json (ex: "00:00 - 00:05"),
      convertendo ":" em "-" e removendo espaços.
    - Se ausente (comportamento real atual — nenhum projeto tem 'timecode'), cai no
      fallback derivado de tempo_inicio/tempo_fim via formatar_nome_arquivo_cena_padrao()
      (formato {cid:02d}_[MM-SS-SS].png).

    Aplica para .png e .mp4 (via `ext`/`is_video`).
    """
    if not ext.startswith("."):
        ext = f".{ext}"

    tc = None
    try:
        plan = carregar_scene_plan(projeto_id)
        if plan and plan.get("cenas"):
            for c in plan["cenas"]:
                if int(c.get("id", 0)) == int(cid) or int(c.get("scene_index", 0)) == int(cid):
                    val = c.get("timecode")
                    if isinstance(val, str) and val.strip():
                        tc = val.strip()
                    break
    except Exception:
        tc = None

    if tc:
        # "00:10 - 00:14" → tenta separar o intervalo pelo hífen com espaços primeiro
        partes = re.split(r"\s*[-–—]\s*", tc)
        partes = [p.strip() for p in partes if p.strip()]
        if len(partes) >= 2:
            tc_ini = partes[0].replace(":", "-").replace(" ", "")
            tc_fim = partes[1].replace(":", "-").replace(" ", "")
            return f"{int(cid):02d}_[{tc_ini}-{tc_fim}]{ext}"
        else:
            return f"{int(cid):02d}_[{tc.replace(' ', '').replace(':', '-')}]{ext}"

    return formatar_nome_arquivo_cena_padrao(cid, ts_ini, ts_fim, ext)


def resolver_arquivo_cena(
    projeto_id: str,
    cid: int,
    tempo_inicio: float = 0.0,
    estilo_slug: str = "",
    ext: str = ""
) -> Optional[Path]:
    """
    Resolvedor canônico resiliente de mídias por cena.
    Ordem de resolução:
      1. Campo arquivo_midia da cena (se existir no disco e tamanho > 500 bytes)
      2. Padrão canônico novo: cenas/{cid}_[{MM-SS}]_{style_slug}{ext}
      3. Glob por ID da cena em cenas/: cenas/{cid}_* (FAIL-CLOSED: só resolve com
         UM ÚNICO candidato; com 2+ arquivos ambíguos retorna None)
      4. Padrões legados:
         - cenas/{cid:03d}.png / cenas/{cid:03d}.mp4
         - imagens/{cid:03d}.png
         - cenas/{cid:02d}_[{MM-SS-SS}].png / .mp4 (ex: 01_[00-00-05].png)
         - cenas/{cid:03d}_{MM-SS}_{MM-SS}.png / .mp4 (ex: 001_00-00_00-04.png)

    Retorna o Path do arquivo encontrado ou None se não existir.
    """
    pdir = _project_dir(projeto_id)
    cenas_dir = pdir / "cenas"

    # 1. Arquivo registrado no plano de cenas (fonte mais sólida)
    plan = carregar_scene_plan(projeto_id)
    scene_data = None
    if plan and "cenas" in plan:
        for c in plan.get("cenas", []):
            if int(c.get("id", 0)) == int(cid) or int(c.get("scene_index", 0)) == int(cid):
                scene_data = c
                arq = c.get("arquivo_midia")
                if arq and Path(arq).exists() and Path(arq).is_file() and Path(arq).stat().st_size > 500:
                    # Se solicitou ext específico (ex: .mp4) e o arquivo_midia atual não for essa ext, segue busca
                    if not ext or Path(arq).suffix.lower() == ext.lower():
                        return Path(arq)
                if not tempo_inicio and c.get("tempo_inicio") is not None:
                    tempo_inicio = float(c.get("tempo_inicio", 0))
                break

    e_video = (ext.lower() == ".mp4") or (scene_data and (
        scene_data.get("video_status") == "READY"
        or scene_data.get("tipo") == "video"
        or scene_data.get("scene_type") in ["video_acao", "broll_action"]
        or scene_data.get("animate_later") is True
        or scene_data.get("animar") is True
    ))

    exts = ([ext] if ext else ([".mp4"] if e_video else [".png", ".jpg", ".jpeg", ".webp"]))

    # 2. Padrão novo em cenas/: cenas/{cid}.png / cenas/{cid}.mp4 (+ variantes de zero)
    if cenas_dir.exists():
        for cext in exts:
            for nome in (f"{cid}{cext}", f"{cid:02d}{cext}", f"{cid:03d}{cext}"):
                cand = cenas_dir / nome
                if cand.exists() and cand.is_file() and cand.stat().st_size > 500:
                    return cand
        # Glob por ID em cenas/ (cobre 01_[00-00-05].png / 001_MM-SS_MM-SS.png etc.)
        # FAIL-CLOSED: só resolve com UM ÚNICO candidato. Com 2+ arquivos ambíguos,
        # o antigo critério "mais recente por mtime" atribuía a mídia pelo relógio
        # (palpite) — agora retorna None para a cena ser reprocessada.
        for pat in (f"{cid}_*", f"{cid:02d}_*", f"{cid:03d}_*"):
            cands = [f for f in cenas_dir.glob(pat) if f.is_file() and f.stat().st_size > 500]
            if not cands:
                continue
            if len(cands) == 1:
                return cands[0]
            log_event("SCENE_PLAN",
                      f"{projeto_id}: cena {cid} tem {len(cands)} mídias em cenas/ "
                      f"(padrão {pat}) — ambíguo, resolver retorna None (fail-closed)",
                      level="warn")
            return None

    # 4. Compatibilidade legada (imagens/, videos/, conteudo/)
    return resolver_arquivo_cena_legado(projeto_id, cid, e_video, ext)


def resolver_arquivo_cena_legado(projeto_id: str, cid: int, e_video: bool = False,
                                 ext: str = "") -> Optional[Path]:
    """Busca a mídia da cena nas pastas legadas (imagens/, videos/, conteudo/).

    Mantém compatibilidade com projetos antigos que tinham mídia em imagens/ ou
    videos/ antes da unificação em cenas/. Ordem: glob por ID -> padrão direto.

    FAIL-CLOSED: o glob por ID só resolve com UM ÚNICO candidato (ou um único
    vídeo, quando e_video). Com 2+ arquivos ambíguos retorna None em vez de
    escolher o "mais recente por mtime" (palpite pelo relógio).
    """
    pdir = _project_dir(projeto_id)
    conteudo_dir = pdir / "conteudo"
    imagens_dir = pdir / "imagens"
    videos_dir = pdir / "videos"
    ordem = ([conteudo_dir, videos_dir, imagens_dir] if e_video
             else [conteudo_dir, imagens_dir, videos_dir])

    for c_dir in ordem:
        if not c_dir.exists():
            continue
        for pat in (f"{cid}_*", f"{cid:02d}_*", f"{cid:03d}_*"):
            cands = [f for f in c_dir.glob(pat) if f.is_file() and f.stat().st_size > 500]
            if not cands:
                continue
            if e_video:
                vids = [f for f in cands if f.suffix.lower() in (".mp4", ".mov", ".webm")]
                if len(vids) == 1:
                    return vids[0]
                if len(vids) > 1:
                    log_event("SCENE_PLAN",
                              f"{projeto_id}: cena {cid} tem {len(vids)} vídeos legados em "
                              f"{c_dir.name}/ — ambíguo, resolver retorna None (fail-closed)",
                              level="warn")
                    return None
            if len(cands) == 1:
                return cands[0]
            log_event("SCENE_PLAN",
                      f"{projeto_id}: cena {cid} tem {len(cands)} mídias legadas em "
                      f"{c_dir.name}/ — ambíguo, resolver retorna None (fail-closed)",
                      level="warn")
            return None

    # Padrões diretos legados por ID
    candidatos = ([
        conteudo_dir / f"{cid:03d}.mp4",
        videos_dir / f"{cid:03d}.mp4",
        videos_dir / f"{cid:02d}.mp4",
        videos_dir / f"{cid}.mp4",
        conteudo_dir / f"{cid:03d}.png",
        imagens_dir / f"{cid:03d}.png",
    ] if e_video else [
        conteudo_dir / f"{cid:03d}.png",
        imagens_dir / f"{cid:03d}.png",
        conteudo_dir / f"{cid:03d}.jpg",
        imagens_dir / f"{cid:03d}.jpg",
    ])
    for cand in candidatos:
        if cand.exists() and cand.is_file() and cand.stat().st_size > 500:
            return cand
    return None


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
    padrao_cena_regex = re.compile(r"^(\d+)_\[(\d{2})-(\d{2})-(\d{2})\]\.(png|jpg|jpeg|mp4|webp)$", re.IGNORECASE)

    # 0. Varre conteudo/ (pasta unificada consolidada)
    conteudo_dir = pdir / "conteudo"
    if conteudo_dir.exists():
        for f in conteudo_dir.rglob("*"):
            if f.is_file() and f.suffix.lower() in (IMAGEM_EXT | {".mp4", ".mov", ".webm"}):
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
                    m_num = re.search(r"(?:cena_)?(\d+)", fname, re.IGNORECASE)
                    if m_num:
                        cid = int(m_num.group(1))

                is_vid = f.suffix.lower() in {".mp4", ".mov", ".webm"}
                tipo_media = "video" if is_vid else "imagem"
                tamanho = f.stat().st_size

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
                    upd = {
                        "arquivo_midia": str(f),
                        "filename": fname,
                        "status": STATUS_BAIXADA
                    }
                    if is_vid:
                        upd["video_status"] = VIDEO_STATUS_READY
                    else:
                        upd["image_status"] = IMAGE_STATUS_READY
                    atualizar_cena(projeto, cid, upd)
                total_indexados += 1

    # 1. Varre cenas/
    cenas_dir = pdir / "cenas"
    if cenas_dir.exists():
        for f in cenas_dir.rglob("*"):
            if f.is_file() and f.suffix.lower() in (IMAGEM_EXT | {".mp4", ".mov", ".webm"}):
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

                is_vid = f.suffix.lower() in {".mp4", ".mov", ".webm"}
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
                    upd = {
                        "arquivo_midia": str(f),
                        "filename": fname,
                        "status": STATUS_BAIXADA
                    }
                    if is_vid:
                        upd["video_status"] = VIDEO_STATUS_READY
                    else:
                        upd["image_status"] = IMAGE_STATUS_READY
                    atualizar_cena(projeto, cid, upd)
                
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
            if f.is_file() and f.suffix.lower() in {".mp4", ".mov", ".webm"} and f.stat().st_size > 500:
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
                    m_num = re.search(r"^(\d+)", fname)
                    if m_num:
                        cid = int(m_num.group(1))

                atualizar_galeria_item(
                    projeto=projeto,
                    arquivo_nome=fname,
                    arquivo_path=str(f),
                    tipo="video",
                    cid=cid,
                    ts_ini=ts_ini,
                    ts_fim=ts_fim,
                    tamanho_bytes=f.stat().st_size
                )
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
                        "filename": fname,
                        "video_status": VIDEO_STATUS_READY,
                        "status": STATUS_BAIXADA
                    })
                total_indexados += 1

    try:
        sincronizar_midias_encontradas(projeto, force=True)
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
           │     └── 01_[00-00-05].png      (arquivo canônico na raiz — sem subpastas)
           ├── storyboard.json
           └── galeria.json
    """
    trace_plan(f"CENA {cid:03d}: salvar_midia_cena_estruturada iniciou "
               f"({len(midia_bytes)} bytes, is_video={is_video}).", projeto_id)
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

    # 1. Nomenclatura canônica (RODADA APROVADA):
    #    {cid:02d}_[{tc_inicio}-{tc_fim}][ext]  — ex: 01_[00-00-05].png
    #    Usa o campo "timecode" (ex: "00:00 - 00:05") do cena; se ausente,
    #    deriva de tempo_inicio/tempo_fim via formatar_nome_arquivo_cena_padrao().
    ext_sem_ponto = ext[1:] if ext.startswith(".") else ext
    arquivo_nome = _nome_cena_timecode(
        projeto_id, cid, ts_ini, ts_fim,
        ext if ext.startswith(".") else f".{ext}",
        is_video=is_video,
    )
    arquivo_simples = arquivo_nome

    from services.visual_presets_service import obter_slug_estilo
    slug_estilo = obter_slug_estilo(modelo_usado)
    arquivo_canonico = formatar_nome_midia_canonico(cid, ts_ini, slug_estilo, ext)
    arquivo_nome_padrao = formatar_nome_arquivo_cena_padrao(cid, ts_ini, ts_fim, ext)

    cenas_dir = PROJETOS_DIR / projeto_id / "cenas"
    cenas_dir.mkdir(parents=True, exist_ok=True)

    # Grava o arquivo com o novo padrão oficial {cid}_{timestamp}.png na pasta cenas/
    arquivo_path_principal = cenas_dir / arquivo_nome
    trace_plan(f"CENA {cid:03d}: gravando bytes em {arquivo_nome}...", projeto_id)
    arquivo_path_principal.write_bytes(midia_bytes)
    trace_plan(f"CENA {cid:03d}: bytes gravados em disco.", projeto_id)

    # 2. (Removido: subpasta estruturada cena_{cid:03d}_{ts_str}/ com cópias
    #    video.mp4/imagem.png/prompt.txt/status.json — arquivo canônico fica SOMENTE na raiz de cenas/)

    status_data = {
        "id": cid,
        "scene_index": cid,
        "status": STATUS_BAIXADA,
        "image_status": IMAGE_STATUS_READY if not is_video else IMAGE_STATUS_DOWNLOADED,
        "video_status": VIDEO_STATUS_READY if is_video else VIDEO_STATUS_NOT_STARTED,
        "pasta": "",
        "arquivo_midia": str(arquivo_path_principal),
        "arquivo_nome": arquivo_nome,
        "filename": arquivo_nome,
        "arquivo_nome_timestamp": arquivo_nome,
        "prompt": prompt_texto,
        "personagem": personagem_ref or "",
        "modelo": modelo_usado or "",
        "tempo_inicio": ts_ini,
        "tempo_fim": ts_fim,
        "start": ts_ini,
        "end": ts_fim,
        "original_timestamp": formatar_ts_cena(ts_ini, ts_fim),
        "duracao": round(ts_fim - ts_ini, 2),
        "tipo": "video" if is_video else "image",
        "atualizado_em": datetime.now().isoformat(sep=" ", timespec="seconds"),
    }

    # 3. Atualiza storyboard.json
    trace_plan(f"CENA {cid:03d}: atualizando storyboard.json...", projeto_id)
    atualizar_storyboard_cena(
        projeto=projeto_id,
        cid=cid,
        arquivo_nome=arquivo_nome,
        arquivo_path=str(arquivo_path_principal),
        ts_ini=ts_ini,
        ts_fim=ts_fim,
        prompt=prompt_texto,
        personagem=personagem_ref,
        modelo=modelo_usado,
        status=STATUS_BAIXADA
    )
    trace_plan(f"CENA {cid:03d}: storyboard.json atualizado.", projeto_id)

    # 4. Atualiza galeria.json
    trace_plan(f"CENA {cid:03d}: atualizando galeria.json...", projeto_id)
    atualizar_galeria_item(
        projeto=projeto_id,
        arquivo_nome=arquivo_nome,
        arquivo_path=str(arquivo_path_principal),
        tipo="video" if is_video else "imagem",
        cid=cid,
        ts_ini=ts_ini,
        ts_fim=ts_fim,
        modelo=modelo_usado,
        personagem=personagem_ref,
        tamanho_bytes=len(midia_bytes)
    )
    trace_plan(f"CENA {cid:03d}: galeria.json atualizada. Iniciando VISUAL JUDGMENT interno...", projeto_id)

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
            caminho_imagem=str(arquivo_path_principal)
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
                image_path=str(arquivo_path_principal),
                visual_score=vj["visual_score"],
                judgment_status=vj["judgment_status"],
                selection_reason=vj["selection_reason"]
            )
        except Exception:
            pass
    except Exception as e_vj:
        log_event("VISUAL_JUDGMENT", f"Aviso ao avaliar mídia da cena {cid}: {e_vj}", level="warn")
        trace_plan(f"CENA {cid:03d}: ❌ VISUAL JUDGMENT interno FALHOU: {e_vj}", projeto_id, level="error")
    else:
        trace_plan(f"CENA {cid:03d}: VISUAL JUDGMENT interno concluiu.", projeto_id)

    # 4.9. Grava na pasta consolidada 'conteudo/' com a nomenclatura canônica oficial {cid}_[{timecode}].{ext}
    conteudo_dir = PROJETOS_DIR / projeto_id / "conteudo"
    conteudo_dir.mkdir(parents=True, exist_ok=True)
    try:
        (conteudo_dir / arquivo_nome).write_bytes(midia_bytes)
    except Exception:
        pass

    # 6. PADRÃO LIRA STUDIO v0.3.0+ (SEM QUEBRAS): nomenclatura canônica
    #    {id:02d}_[{MM:SS}-{MM:SS}]{ext} + integridade das 3 fontes
    #    (lira_scene_plan.json -> imagens/ -> metadata/cena_XXX/ + midias_encontradas).
    arquivo_path_padrao = str(arquivo_path_principal)
    arquivo_nome_padrao_oficial = arquivo_nome
    try:
        from services.media_standard import garantir_cena_padrao
        r_padrao = garantir_cena_padrao(projeto_id, cid, mover=True)
        if r_padrao.get("ok"):
            arquivo_path_padrao = r_padrao["caminho"]
            arquivo_nome_padrao_oficial = r_padrao["nome"]
    except Exception as e_std:
        log_event("MEDIA_STANDARD", f"{projeto_id}: aviso ao aplicar padrão v0.3.0 na cena {cid}: {e_std}",
                  level="warn")

    trace_plan(f"CENA {cid:03d}: padrão de mídia garantido ({arquivo_nome_padrao_oficial}).", projeto_id)

    # 7. Sincroniza storyboard.json com o caminho físico REAL (padrão v0.3.0+):
    #    garantir_cena_padrao pode ter RENOMEADO o arquivo em disco (3 blocos -> 4
    #    blocos). Sem esta re-gravação, o arquivo_path gravado acima (linha 929)
    #    apontaria para um arquivo inexistente.
    if arquivo_path_padrao and arquivo_path_padrao != str(arquivo_path_principal):
        atualizar_storyboard_cena(
            projeto=projeto_id,
            cid=cid,
            arquivo_nome=arquivo_nome_padrao_oficial,
            arquivo_path=arquivo_path_padrao,
            ts_ini=ts_ini,
            ts_fim=ts_fim,
            prompt=prompt_texto,
            personagem=personagem_ref,
            modelo=modelo_usado,
            status=STATUS_BAIXADA
        )

    trace_plan(f"CENA {cid:03d}: salvar_midia_cena_estruturada COMPLETOU.", projeto_id)
    return {
        "success": True,
        "arquivo_path": arquivo_path_padrao,
        "arquivo_nome": arquivo_nome_padrao_oficial,
        "arquivo_path_timestamp": str(arquivo_path_principal),
        "arquivo_nome_timestamp": arquivo_nome,
        "scene_index": cid,
        "start": ts_ini,
        "end": ts_fim,
        "original_timestamp": formatar_ts_cena(ts_ini, ts_fim),
        "tipo": "video" if is_video else "image",
        "pasta_cena": "",
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
        # --- Transiciones (P6) ---
        "transicao_entrada":        dict(TRANSICION_ENTRADA_DEFAULT),
        "transicao_saida":          dict(TRANSICION_SAIDA_DEFAULT),
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
        # --- FASE 1 (Lira Studio) — Narrativa + Avatar/B-roll ---
        "narrative_role":           "HOOK" if cid == 1 else "AVATAR",
        "avatar_required":          True,
        "broll_query":              None,
        "recommended_duration":     5.0,
        "action_verb":              "speaking",
        "intensity":                0.6,
        "video_url":                None,
        "broll_url":                None,
        "broll_status":             VIDEO_STATUS_NOT_STARTED,
        # --- Estratégia de Retenção + Avatar Inteligente (aditivo) ---
        "avatar_role":              None,   # HOOK|VALUE|CHECKPOINT|REFORÇO|AÇÃO|CONCLUSÃO|CTA
        "is_pattern_interrupt":     False,
        "expected_viewer_drop":     False,
        "retencao_impact":          "low",  # low|medium|high|critical
        "timestamp_desde_ultimo_avatar": 0,
        "should_have_avatar":       False,
        "motivo_retencao":          "",
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
    Garante sincronização de referências de personagem de forma compatível
    com o catálogo multirreferência (references.json) e o legado (identidade.json).
    """
    import services.character_service as character_svc
    refs_list = []
    if projeto_id:
        try:
            r = character_svc.listar_referencias_projeto(projeto_id)
            refs_list = r if isinstance(r, list) else r.get("referencias", [])
        except Exception:
            refs_list = []

    aliases_validos = {
        ref.get("alias", "").lower(): ref.get("alias", "")
        for ref in refs_list
        if ref.get("tipo") == "character" and ref.get("alias")
    }

    idt = character_svc.obter_identidade_projeto(projeto_id) if projeto_id else None
    nome_oficial = (idt.get("nome") if idt else "") or nome_pers_default or ""
    ref_oficial = (idt.get("referencia_flow") if idt else "") or (f"@{nome_oficial}" if nome_oficial else "")
    if ref_oficial:
        aliases_validos[ref_oficial.lower()] = ref_oficial

    for c in cenas:
        # REGRA ANTI-SATURAÇÃO (Lira Studio v0.2.0): cena de B-roll puro
        # (narrative_role == "BROLL" ou avatar_required == False) NUNCA recebe
        # a trava de identidade — mesmo que o texto do prompt ainda contenha
        # "@presenter" residual de planos antigos. Isso impede que a trava
        # reative uses_character/character_ref em cenas que o rebalanceamento
        # (quota 8% avatar / 92% broll) rebaixou para cobertura visual.
        # Também limpa flags residuais de planos legados saturados.
        if c.get("narrative_role") == "BROLL" or c.get("avatar_required") is False:
            c["uses_character"] = False
            c["character_ref"] = ""
            continue

        prompt_txt = f"{c.get('prompt_imagem', '')} {c.get('visual_prompt', '')}".lower()
        c_refs = c.get("references") or []
        char_ref = (c.get("character_ref") or "").strip()

        # Verifica se alguma referência conhecida está presente no prompt ou nas referências da cena
        ref_encontrada = ""
        for alias_low, alias_orig in aliases_validos.items():
            if alias_low in prompt_txt or (char_ref and char_ref.lower() == alias_low) or any(str(r).lower() == alias_low for r in c_refs):
                ref_encontrada = alias_orig
                break

        # Preserva character_ref explícito já registrado na cena
        if not ref_encontrada and char_ref:
            ref_encontrada = char_ref

        if ref_encontrada:
            c["uses_character"] = True
            c["character_ref"] = ref_encontrada if ref_encontrada.startswith("@") else f"@{ref_encontrada}"
        elif c.get("uses_character") is True and ref_oficial:
            c["character_ref"] = ref_oficial
        else:
            if not c.get("uses_character"):
                c["character_ref"] = ""

    return cenas


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------

def _garantizar_transiciones(plan: dict | None) -> None:
    """Backfill idempotente (P6): agrega los campos de transición faltantes a cada cena."""
    if not plan or "cenas" not in plan:
        return
    for c in plan.get("cenas", []):
        if not isinstance(c.get("transicao_entrada"), dict):
            c["transicao_entrada"] = dict(TRANSICION_ENTRADA_DEFAULT)
        if not isinstance(c.get("transicao_saida"), dict):
            c["transicao_saida"] = dict(TRANSICION_SAIDA_DEFAULT)


def aplicar_transicion_cena(
    projeto: str,
    scene_id: int,
    lado: str,
    tipo: str = "",
    duracao_ms: int = 0,
) -> tuple:
    """Guarda a transición (entrada/saida) dunha cena en lira_scene_plan.json.

    Validacións (item 5 do requerimento):
      - lado en {"entrada", "saida"}
      - tipo en TRANSICIONES_TIPOS ("none" resetea ao default por defecto)
      - duracao_ms entre TRANSICION_DURACION_MIN_MS e MAX_MS (100-1000)
      - duracao_ms <= duración da cena (nunca supera a duración real)

    Retorna (ok: bool, msg: str, plan: dict | None).
    """
    plan = carregar_scene_plan(projeto)
    if plan is None:
        return (False, "scene_plan non encontrado", None)

    cena = next(
        (c for c in plan.get("cenas", []) if int(c.get("id", 0)) == int(scene_id)),
        None,
    )
    if cena is None:
        return (False, f"cena {scene_id} non encontrada", plan)

    if lado not in ("entrada", "saida"):
        return (False, "lado inválido: entrada|saida", plan)

    tipo = str(tipo or "").strip()
    if tipo not in TRANSICIONES_TIPOS:
        return (False, f"tipo inválido: {', '.join(TRANSICIONES_TIPOS)}", plan)

    duracao_ms = int(duracao_ms or TRANSICION_ENTRADA_DEFAULT["duracao_ms"])
    if duracao_ms < TRANSICION_DURACION_MIN_MS or duracao_ms > TRANSICION_DURACION_MAX_MS:
        return (False, f"duracao_ms fuera de rango ({TRANSICION_DURACION_MIN_MS}-{TRANSICION_DURACION_MAX_MS})", plan)

    dur_cena_ms = int(round(float(cena.get("duracao") or 0) * 1000))
    if dur_cena_ms > 0 and duracao_ms > dur_cena_ms:
        return (False, f"transición ({duracao_ms}ms) supera a duración da cena ({dur_cena_ms}ms)", plan)

    if tipo == "none":
        cena[f"transicao_{lado}"] = dict(
            TRANSICION_ENTRADA_DEFAULT if lado == "entrada" else TRANSICION_SAIDA_DEFAULT
        )
    else:
        cena[f"transicao_{lado}"] = {"tipo": tipo, "duracao_ms": duracao_ms}
    cena["atualizado_em"] = datetime.now().isoformat(sep=" ", timespec="seconds")

    if not salvar_scene_plan(projeto, plan):
        return (False, "erro ao salvar scene_plan", plan)
    return (True, f"transición de {lado} gardada para cena {scene_id}", plan)


def aplicar_transicoes_em_lote(
    projeto: str,
    tipo: str = "fade_out",
    duracao_ms: int = 300,
    lado: str = "saida"
) -> tuple:
    """Aplica uma transição padrão para todas as cenas do projeto em lote."""
    plan = carregar_scene_plan(projeto)
    if plan is None:
        return (False, "scene_plan não encontrado", None)

    cenas = plan.get("cenas", [])
    if not cenas:
        return (False, "Nenhuma cena no plano", plan)

    tipo = str(tipo or "bordas_difusas").strip().lower()
    duracao_ms = max(TRANSICION_DURACION_MIN_MS, min(TRANSICION_DURACION_MAX_MS, int(duracao_ms or 500)))

    for c in cenas:
        if lado in ("saida", "ambas"):
            c["transicao_saida"] = {"tipo": tipo, "duracao_ms": duracao_ms}
        if lado in ("entrada", "ambas"):
            c["transicao_entrada"] = {"tipo": tipo, "duracao_ms": duracao_ms}

    salvar_scene_plan(projeto, plan)
    return (True, f"Transição '{tipo}' ({duracao_ms}ms) aplicada a todas as {len(cenas)} cenas.", plan)


def carregar_scene_plan(projeto: str) -> dict | None:
    """Carrega lira_scene_plan.json ou None se não existir.

    I/O com PRAZO RÍGIDO (SCENE_PLAN_IO_TIMEOUT_SEG) e no máximo 1 retry — antes
    eram até 3 tentativas sem prazo, o que fazia a UI de "gerar restantes" esperar
    8-12s quando o arquivo estava travado/em volume lento.
    """
    path = _scene_plan_path(projeto)
    if not path.exists():
        return None
    try:
        from config import normalizar_caminho
        raw_text = None
        ultimo_erro = None
        inicio_leitura = time.monotonic()
        for tentativa in range(SCENE_PLAN_MAX_TENTATIVAS_LEITURA):
            restante = SCENE_PLAN_IO_TIMEOUT_SEG - (time.monotonic() - inicio_leitura)
            if restante <= 0.05:
                break
            try:
                raw_text = _ler_texto_scene_plan(path, restante)
                break
            except (PermissionError, OSError, TimeoutError) as e_io:
                # Locks esporádicos do Windows (Errno 13) são transientes: 1 retry.
                ultimo_erro = e_io
                if tentativa + 1 >= SCENE_PLAN_MAX_TENTATIVAS_LEITURA:
                    break
                time.sleep(min(0.05, max(0.0, restante)))
        if raw_text is None:
            _decorrido = time.monotonic() - inicio_leitura
            log_event("SCENE_PLAN",
                      f"{projeto}: leitura do scene_plan falhou em {_decorrido:.1f}s "
                      f"(teto {SCENE_PLAN_IO_TIMEOUT_SEG:.1f}s, "
                      f"{SCENE_PLAN_MAX_TENTATIVAS_LEITURA} tentativa(s)): {ultimo_erro}",
                      level="error")
            return None
        raw_text = normalizar_caminho(raw_text)
        plan = json.loads(raw_text)
        if plan and "cenas" in plan:
            plan["cenas"] = sincronizar_trava_identidade_cenas(plan["cenas"], projeto_id=projeto)
        # Transiciones (P6): backfill idempotente — agrega campos faltantes (planos anteriores)
        try:
            _garantizar_transiciones(plan)
        except Exception:
            pass
        # CORREÇÃO (Item 3B): leitura NÃO grava em disco. Os backfills acima
        # (transições) e o auto-healing narrativo abaixo aplicam-se APENAS em
        # memória; a persistência explícita acontece no próximo salvar_scene_plan
        # (atualizar_cena/atualizar_status_cena/worker etc.), serializado pelo lock.
        # Isso elimina a janela de escrita concorrente disparada pelo polling de
        # leitura enquanto o worker grava.
        try:
            from services.narrative_distributor import NARRATIVA_VERSAO
            if plan and plan.get("narrativa_versao") != NARRATIVA_VERSAO:
                from services.narrative_distributor import aplicar_reclassificacao_narrativa
                aplicar_reclassificacao_narrativa(plan, projeto=projeto)  # memória apenas
        except Exception:
            pass
        return plan
    except Exception as e:
        log_event("SCENE_PLAN", f"{projeto}: erro ao carregar scene_plan: {e}", level="error")
        return None


def salvar_scene_plan(projeto: str, plan: dict) -> bool:
    """Salva lira_scene_plan.json atomicamente, serializado por projeto (thread).

    - Lock de escrita por projeto (threading.Lock) cobre TODO o corpo — incluindo
      o fallback não-atômico `path.write_text` — para impedir que duas threads do
      mesmo processo (polling da UI x worker Playwright) concorram e concatenem
      conteúdo no arquivo (JSONDecodeError "Extra data").
    - Entre processos, a atomicidade continua garantida por tmp + os.replace.
    """
    lock = _adquirir_lock_escrita(projeto)
    try:
        return _salvar_scene_plan_lockado(projeto, plan)
    finally:
        lock.release()


def _salvar_scene_plan_lockado(projeto: str, plan: dict) -> bool:
    """Implementação interna — chamar SOMENTE sob `_obter_lock_escrita(projeto)`.

    Integridade em TODAS as tentativas: grava em arquivo temporário, flush+fsync
    e os.replace (atômico). O fallback final também é atômico — só cai para
    escrita direta (logada) se o replace falhar repetidamente, evitando arquivos
    corrompidos por escrita interrompida.
    """
    path = _scene_plan_path(projeto)
    path.parent.mkdir(parents=True, exist_ok=True)
    if plan and "cenas" in plan:
        plan["cenas"] = sincronizar_trava_identidade_cenas(plan["cenas"], projeto_id=projeto)
    content = json.dumps(plan, indent=2, ensure_ascii=False)
    tmp = path.with_suffix(".json.tmp")

    def _escrita_atomica() -> None:
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(path))

    for _tentativa in range(5):
        try:
            _escrita_atomica()
            return True
        except PermissionError:
            time.sleep(0.08)
        except Exception:
            break
    # Fallback: mais uma tentativa atômica; se ainda falhar, escrita direta logada
    try:
        _escrita_atomica()
        return True
    except PermissionError:
        time.sleep(0.08)
    except Exception:
        pass
    try:
        path.write_text(content, encoding="utf-8")
        return True
    except Exception as e:
        log_event("SCENE_PLAN", f"{projeto}: erro ao salvar scene_plan: {e}", level="error")
        return False


def force_narrativa_v3_update(projeto_id: str) -> int:
    """Força reclassificação narrativa e rebalanceamento v3 no plano de cenas.

    Remove o marcador narrativa_versao para garantir a re-execução completa
    de aplicar_reclassificacao_narrativa, persiste o plano atualizado e
    retorna o total de modificações efetuadas.
    """
    path = _scene_plan_path(projeto_id)
    if not path.exists():
        log_event("SCENE_PLAN", f"{projeto_id}: plano de cenas inexistente para force_v3", level="warn")
        return 0
    try:
        from config import normalizar_caminho
        raw_text = normalizar_caminho(path.read_text(encoding="utf-8"))
        plan = json.loads(raw_text)
    except Exception as e:
        log_event("SCENE_PLAN", f"{projeto_id}: erro ao ler plano para force_v3: {e}", level="error")
        return 0

    if not plan or not plan.get("cenas"):
        return 0

    plan.pop("narrativa_versao", None)
    from services.narrative_distributor import NARRATIVA_VERSAO, rebalancear_narrativa
    cenas = plan["cenas"]
    for idx, cena in enumerate(cenas):
        aplicar_classificacao_narrativa_cena(cena, index=idx)
    mudancas = rebalancear_narrativa(cenas, projeto=projeto_id)
    plan["narrativa_versao"] = NARRATIVA_VERSAO
    salvar_scene_plan(projeto_id, plan)
    log_event("SCENE_PLAN", f"{projeto_id}: force_narrativa_v3_update concluído ({mudancas} mudanças)", level="info")
    return mudancas


def aplicar_classificacao_narrativa_cena(cena: dict, index: int = 0) -> dict:
    """FASE 1 (Lira Studio) — Enriquece uma cena com classificação narrativa
    (HOOK|AVATAR|BROLL|CTA|CLOSING) e decisão avatar/b-roll.

    ADITIVO: nunca remove/sobrescreve campos existentes (estado do pipeline,
    arquivos, prompts, aprovações). Campos novos:
      narrative_role, avatar_required, broll_query, recommended_duration,
      action_verb, intensity, video_url, broll_url, broll_status.
    """
    if not isinstance(cena, dict):
        return cena
    from services.enhanced_scene_classifier import classify_scene as _classificar_narrativa
    from services.avatar_decision_service import decide_avatar_or_broll as _decidir_avatar_broll

    texto = str(cena.get("texto") or cena.get("narration") or cena.get("text") or "")
    t0 = cena.get("tempo_inicio")
    t0sec = float(t0) if isinstance(t0, (int, float)) else None
    cid = cena.get("id")
    cidx = cena.get("scene_index")
    try:
        scene_index_val = int(cid) if isinstance(cid, (int, float)) else (
            int(cidx) if isinstance(cidx, (int, float)) else index + 1)
    except (TypeError, ValueError):
        scene_index_val = index + 1

    clf = _classificar_narrativa(
        scene_text=texto,
        scene_type=str(cena.get("scene_type") or ""),
        timestamp=str(cena.get("timestamp") or cena.get("tempo_inicio") or ""),
        scene_index=scene_index_val,
        tempo_inicio_secs=t0sec,
    )
    decisao, valor = _decidir_avatar_broll(clf["narrative_role"], scene=cena)

    cena["narrative_role"] = clf["narrative_role"]
    cena["intensity"] = clf["intensity"]
    cena["action_verb"] = clf["action_verb"]
    cena["recommended_duration"] = clf["recommended_duration"]
    cena["avatar_required"] = (decisao == "avatar")
    cena["broll_query"] = valor if decisao == "broll" else clf["broll_query"]
    cena["video_url"] = cena.get("video_url") or None
    cena["broll_url"] = cena.get("broll_url") or None
    cena["broll_status"] = cena.get("broll_status") or VIDEO_STATUS_NOT_STARTED
    cena.setdefault("video_status", VIDEO_STATUS_NOT_STARTED)
    return cena


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

    # --- Carrega o SRT (timing canônico) para a distribuição de tipos de cena ---
    # Fonte: <projeto>/srt/roteiro_transcricao.srt — mesma pasta gravada pelo
    # transcriber e por POST /api/v2/transcricao/<id>/usar_srt. Projetos sem SRT
    # seguem com lista vazia: a distribuição por tempo simplesmente não se aplica.
    srt_data: List[Dict[str, Any]] = []
    _srt_caminho = os.path.join(PROJETOS_DIR, projeto, "srt", "roteiro_transcricao.srt")
    try:
        if os.path.exists(_srt_caminho):
            srt_data = ler_srt_caminho(_srt_caminho)
    except Exception as _e_srt:
        log_event("SCENE_PLAN",
                  f"{projeto}: aviso — SRT ilegível ({_srt_caminho}): {_e_srt}",
                  level="warn")
    if not srt_data:
        srt_data = []

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
        _fim_raw = c.get("end_time") or c.get("end")
        if _fim_raw:
            fim = float(_fim_raw)
        else:
            fim = ini + 5.0
            print(f"[WARN] scene_plan: cena sem end_time real, usando fallback +5.0s (start={ini})", flush=True)
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
            # prompt_animacao NÃO vem mais do director (template fixo de 4 strings).
            # É gerado no passo 4.5 (DeepSeek) a partir do prompt_imagem real.
            # Fica vazio aqui — e permanece vazio em cenas estáticas.
            entrada["prompt_animacao"] = ""

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
            elif not entrada.get("animate_later"):
                # Cena estática (should_animate=False) NUNCA carrega prompt_animacao.
                entrada["prompt_animacao"] = ""
            # Cenas que animam recebem o prompt no passo 4.5 (DeepSeek).

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

    # FASE 1 — Lira Studio: Classificação Narrativa + Decisão Avatar/B-roll (aditivo)
    for _idx_narr, _cena_narr in enumerate(novas_cenas):
        aplicar_classificacao_narrativa_cena(_cena_narr, index=_idx_narr)

    # Lira Studio v0.2.0 (Frente 1): balanço GLOBAL avatar/b-roll — quota +
    # espaçamento (nunca 2 avatares consecutivos) + âncoras duras preservadas.
    from services.narrative_distributor import rebalancear_narrativa, NARRATIVA_VERSAO
    rebalancear_narrativa(novas_cenas, projeto=projeto)

    # -----------------------------------------------------------------------
    # 4.4. DISTRIBUIÇÃO AUTOMÁTICA DE TIPOS DE CENA (retenção + corpo + gancho)
    #      baseada no SRT timing (calculate_retencao_gancho/detect_fala_em_intervalo).
    #      Aplicada DEPOIS de todos os diretores (é aqui que a lista `novas_cenas`
    #      está fechada em número e ordem) e ANTES do passo 4.5, para que as cenas
    #      promovidas a vídeo recebam prompt_animacao.
    #        RETENÇÃO → vídeo / HOOK        (abertura muda, antes da 1ª fala)
    #        GANCHO   → vídeo / CTA         (últimos 10s)
    #        CORPO    → vídeo / AVATAR se há fala no intervalo
    #                   imagem estática / BROLL se é silêncio
    # -----------------------------------------------------------------------
    if srt_data and novas_cenas and modo_producao != "somente_imagens":
        total_duration = sum(float(c.get("duracao") or 0.0) for c in novas_cenas)
        retencao_gancho = calculate_retencao_gancho(srt_data, novas_cenas, total_duration)
        retencao_indices = retencao_gancho["retencao_indices"]
        gancho_indices = retencao_gancho["gancho_indices"]
        corpo_indices = retencao_gancho["corpo_indices"]

        def _eh_cena_texto(c: Dict[str, Any]) -> bool:
            """Cenas de texto (modo imagem_video_texto) não têm mídia gerada."""
            return c.get("tipo") == TIPO_TEXT or c.get("scene_type") == "text"

        def _marcar_video(c: Dict[str, Any], role: str) -> None:
            """Força a cena para VÍDEO + papel narrativo indicado."""
            if _eh_cena_texto(c):
                return
            c["tipo"] = TIPO_VIDEO
            c["media_intent"] = "video"
            c["animar"] = True
            c["animate_later"] = True      # dono da decisão "deve animar" no passo 4.5
            c["animar_depois"] = True
            c["narrative_role"] = role
            c["avatar_required"] = True    # HOOK/CTA/AVATAR sempre com apresentador

        for idx in retencao_indices:
            _marcar_video(novas_cenas[idx], "HOOK")

        for idx in gancho_indices:
            _marcar_video(novas_cenas[idx], "CTA")

        for idx in corpo_indices:
            cena = novas_cenas[idx]
            if _eh_cena_texto(cena):
                continue
            _ini_cena, _fim_cena = _limites_cena(cena)
            tem_fala = detect_fala_em_intervalo(srt_data, _ini_cena, _fim_cena)
            if tem_fala:
                _marcar_video(cena, "AVATAR")
            else:
                cena["tipo"] = TIPO_IMAGE
                cena["media_intent"] = "image"
                cena["animar"] = False
                cena["animate_later"] = False
                cena["animar_depois"] = False
                cena["narrative_role"] = "BROLL"
                cena["avatar_required"] = False
                # Invariante do narrative_distributor: todo BROLL tem broll_query.
                if not cena.get("broll_query"):
                    from services.enhanced_scene_classifier import get_broll_query
                    cena["broll_query"] = get_broll_query(
                        str(cena.get("texto") or cena.get("narration") or ""),
                        str(cena.get("scene_type") or ""),
                    ) or "garden nature macro"

        log_event("SCENE_PLAN",
                  f"{projeto}: distribuição por SRT aplicada — "
                  f"retenção={len(retencao_indices)} corpo={len(corpo_indices)} "
                  f"gancho={len(gancho_indices)} (total {total_duration:.1f}s)")
    elif modo_producao == "somente_imagens" and srt_data and novas_cenas:
        log_event("SCENE_PLAN",
                  f"{projeto}: distribuição por SRT ignorada (modo_producao=somente_imagens).",
                  level="info")

    # 4.5. Lira Studio — prompt_animacao via DeepSeek (continuidade do prompt_imagem)
    # A 3.8 decide SE anima e COMO (animation_type/motion_vector/media_intent).
    # Só o TEXTO do prompt migra para o DeepSeek, alimentado com o prompt_imagem
    # (etapa 4), narration/texto, duracao real e narrative_role/scene_type.
    # Regra: a API só é chamada com force=True E chave DeepSeek configurada; caso
    # contrário (ou em erro/timeout) cada cena animável recebe o fallback
    # determinístico. Cenas estáticas permanecem com prompt_animacao="".
    try:
        import services.deepseek_prompt_service as deepseek_svc
        _res_anim = deepseek_svc.aplicar_prompts_animacao_deepseek(
            cenas=novas_cenas,
            context_pack=contexto_visual or {},
            total_cenas=len(novas_cenas),
            force=bool(force),
        )
        log_event("SCENE_PLAN",
                  f"prompt_animacao: fonte={_res_anim.get('fonte')} "
                  f"cenas={_res_anim.get('total')} fallback={_res_anim.get('fallback')}")
    except Exception as _e_anim:
        log_event("SCENE_PLAN",
                  f"Aviso: passo 4.5 (prompt_animacao DeepSeek) indisponível: {_e_anim}",
                  level="warn")

    plan = {
        "projeto":    projeto,
        "versao":     2,
        "narrativa_versao": NARRATIVA_VERSAO,
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
# Distribuição automática de tipos de cena (retenção | corpo | gancho) via SRT
# ---------------------------------------------------------------------------

def _limites_cena(cena: Dict[str, Any]) -> Tuple[float, float]:
    """(start, end) em segundos de uma cena.

    Aceita o formato do scene_plan (`start`/`end`/`tempo_inicio`/`tempo_fim`) e
    também o do `cenas.json` (`start_time`/`end_time`). Sem `end`, estima +5.0s
    (mesmo fallback usado na montagem do plano).
    """
    ini = cena.get("start", cena.get("tempo_inicio", cena.get("start_time", 0.0)))
    fim = cena.get("end", cena.get("tempo_fim", cena.get("end_time", None)))
    try:
        ini = float(ini or 0.0)
    except (TypeError, ValueError):
        ini = 0.0
    try:
        fim = float(fim) if fim is not None else ini + 5.0
    except (TypeError, ValueError):
        fim = ini + 5.0
    return ini, fim


def calculate_retencao_gancho(srt_data: List[Dict[str, Any]],
                              cenas: List[Dict[str, Any]],
                              total_duration_video: float) -> Dict[str, List[int]]:
    """Divide as cenas em 3 blocos a partir do timing do SRT.

    - RETENÇÃO: cenas que terminam até 3s ANTES da primeira fala (abertura muda;
      é onde o espectador ainda não ouviu nada e precisa ser segurado).
    - GANCHO: cenas que começam nos últimos 10s do vídeo.
    - CORPO: todo o resto (alterna conforme há fala no intervalo de cada cena).

    Retorna índices POSICIONAIS na lista `cenas` (não ids), prontos para uso
    direto por quem chamou. Sem fala alguma no SRT, retorna só CORPO (toda a
    lista) — nunca devolve um dict sem `corpo_indices`.
    """
    primeira_fala_tempo = None
    for bloco in srt_data:
        if bloco.get("text", "").strip():
            primeira_fala_tempo = bloco["start"]
            break

    if primeira_fala_tempo is None:
        return {
            "retencao_indices": [],
            "gancho_indices": [],
            "corpo_indices": list(range(len(cenas))),
        }

    retencao_fim = max(0, primeira_fala_tempo - 3.0)
    retencao_indices = [i for i, c in enumerate(cenas) if _limites_cena(c)[1] <= retencao_fim]
    gancho_inicio = total_duration_video - 10.0
    gancho_indices = [i for i, c in enumerate(cenas) if _limites_cena(c)[0] >= gancho_inicio]

    return {
        "retencao_indices": retencao_indices,
        "gancho_indices": gancho_indices,
        "corpo_indices": [i for i in range(len(cenas)) if i not in retencao_indices and i not in gancho_indices]
    }


def detect_fala_em_intervalo(srt_data: List[Dict[str, Any]],
                             start_seg: float, end_seg: float) -> bool:
    """True se ALGUM bloco com texto do SRT sobrepõe (`start_seg`, `end_seg`)."""
    for bloco in srt_data:
        if bloco["text"].strip():
            if not (bloco["end"] < start_seg or bloco["start"] > end_seg):
                return True
    return False


# ---------------------------------------------------------------------------
# Atualização por cena
# ---------------------------------------------------------------------------

# TAREFA 2 — personalização de legenda por cena (overrides opcionais sobre o preset base).
# Ausência de qualquer campo = o preset escolhido decide (comportamento anterior intacto).
_CAPTION_CUSTOM_FONTES = ("System Font", "Montserrat", "Arial Black", "Roboto")
_CAPTION_CUSTOM_POSICOES = (
    "top-left", "top-center", "top-right",
    "middle-left", "middle-center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
)


def _normalizar_caption_custom(valor) -> dict:
    """
    Normaliza/sanitiza o bloco caption_custom de uma cena:
      font_size   : 12..48 (int, px)
      font_family : uma de _CAPTION_CUSTOM_FONTES
      font_color  : #RRGGBB (aceita #RGB e normaliza para maiúsculas)
      position    : uma de _CAPTION_CUSTOM_POSICOES (ex.: "bottom-center")
    Valores inválidos são DESCARTADOS em silêncio (não derrubam o PATCH).
    """
    if not isinstance(valor, dict):
        return {}

    out: Dict[str, Any] = {}

    tam = valor.get("font_size")
    try:
        if tam not in (None, ""):
            n = int(float(tam))
            if 12 <= n <= 48:
                out["font_size"] = n
    except (TypeError, ValueError):
        pass

    fam = str(valor.get("font_family") or "").strip()
    if fam in _CAPTION_CUSTOM_FONTES:
        out["font_family"] = fam

    cor = str(valor.get("font_color") or "").strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", cor):
        out["font_color"] = cor.upper()
    elif re.fullmatch(r"#[0-9A-Fa-f]{3}", cor):
        out["font_color"] = ("#" + "".join(ch * 2 for ch in cor[1:])).upper()

    pos = str(valor.get("position") or "").strip().lower()
    if pos in _CAPTION_CUSTOM_POSICOES:
        out["position"] = pos

    return out


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
        # PADRÃO LIRA STUDIO v0.3.0+ — campos de integridade de mídia
        "timecode_padrao", "arquivo_nome", "pasta", "midia_padrao",
        # Ken Burns (editor de montagem NLE) — exportado como keyframes no CapCut
        "ken_burns_ativo",
        # REDESIGN F1 — preset de movimento (B-Roll) calculado pela automação
        "motion_preset",
        # Legendas (editor de montagem NLE) — exportadas como trilha de texto no CapCut
        "caption_ativo",
        "caption_style",
        # TAREFA 2 — personalização de legenda por cena (overrides opcionais sobre o
        # preset base): {font_size, font_family, font_color, position}
        "caption_custom",
        # Lira Studio 2.0 Aba 5 (Montagem & CapCut)
        "legenda_ativa",
        "texto_transcricao",
        "estilo_legenda",
        "imagem_path",
        "thumb_path",
        "transicao_saida",
        "transicao_entrada",
        # TAREFA 2/4 (playwright_flow) — prova de que a tentativa de VÍDEO aconteceu e
        # falhou de verdade (substitui a ativação preventiva do fallback video→imagem).
        # Sem estes campos na whitelist, `atualizar_cena` os descartaria em silêncio.
        "video_tentado",
        "video_falhou",
        "video_falhou_motivo",
    }

    cena_encontrada = False
    for cena in plan.get("cenas", []):
        if int(cena.get("id", -1)) == int(scene_id):
            cena_encontrada = True
            for k, v in campos.items():
                if k not in CAMPOS_EDITAVEIS:
                    continue
                cena[k] = v
                # Sincroniza campos equivalentes para compatibilidade total
                if k == "legenda_ativa":
                    cena["caption_ativo"] = bool(v)
                elif k == "caption_ativo":
                    cena["legenda_ativa"] = bool(v)
                elif k == "estilo_legenda":
                    cena["caption_style"] = v
                elif k == "caption_style":
                    cena["estilo_legenda"] = v
                elif k == "texto_transcricao":
                    cena["texto"] = v
                    cena["narration"] = v
                elif k == "caption_custom":
                    # TAREFA 2: sanitiza (descarta valores fora de faixa/desconhecidos).
                    cena["caption_custom"] = _normalizar_caption_custom(v)
            cena["atualizado_em"] = datetime.now().isoformat(sep=" ", timespec="seconds")
            break

    if not cena_encontrada:
        return {"success": False, "error": f"cena {scene_id} não encontrada"}

    ok = salvar_scene_plan(projeto, plan)
    return {"success": ok}


def atualizar_status_cena(projeto: str, scene_id: int, novo_status: str) -> dict:
    """Atalho para atualizar apenas o status de uma cena."""
    return atualizar_cena(projeto, scene_id, {"status": novo_status})


def resetar_status_cenas(projeto: str, ids, novo_status: str = STATUS_PENDENTE) -> dict:
    """Reseta o status de VÁRIAS cenas com UMA leitura + UMA gravação.

    REQ (demora em "gerar restantes"/"Retomar Projeto"): o endpoint
    `/producao/<id>/iniciar_fila` fazia `atualizar_status_cena()` por cena pendente —
    e CADA chamada recarrega e regrava o `lira_scene_plan.json` INTEIRO, sob o lock
    de escrita. Com 100+ cenas pendentes isso eram 2N operações de disco (a causa
    dos 8-12s no clique). Aqui o plano é lido uma vez, ajustado em memória e salvo
    uma única vez — e nada é gravado quando nenhuma cena precisa mudar.
    """
    alvos = {int(i) for i in (ids or [])}
    if not alvos:
        return {"success": True, "atualizadas": 0}
    if novo_status not in STATUS_VALIDOS:
        log_event("SCENE_PLAN", f"{projeto}: status inválido '{novo_status}' em resetar_status_cenas",
                  level="warn")
        return {"success": False, "error": f"status inválido: {novo_status}", "atualizadas": 0}

    plan = carregar_scene_plan(projeto)
    if plan is None:
        return {"success": False, "error": "scene_plan não encontrado", "atualizadas": 0}

    atualizadas = 0
    for c in plan.get("cenas", []) or []:
        try:
            cid = int(c.get("id", -1))
        except (TypeError, ValueError):
            continue
        if cid in alvos and c.get("status") != novo_status:
            c["status"] = novo_status
            atualizadas += 1
    if not atualizadas:
        return {"success": True, "atualizadas": 0}     # nada a mudar: NÃO grava
    ok = salvar_scene_plan(projeto, plan)               # UMA gravação (com lock)
    return {"success": bool(ok)} | {"atualizadas": atualizadas}


def remover_cena(projeto: str, scene_id: int) -> dict:
    """
    Remove uma cena do scene_plan.json e recalcula sequencialmente os timestamps
    das cenas remanescentes na linha do tempo.
    """
    plan = carregar_scene_plan(projeto)
    if plan is None:
        return {"success": False, "error": "scene_plan não encontrado"}

    cenas = plan.get("cenas", [])
    cena_alvo = None
    cenas_restantes = []
    for c in cenas:
        if int(c.get("id", -1)) == int(scene_id):
            cena_alvo = c
        else:
            cenas_restantes.append(c)

    if not cena_alvo:
        return {"success": False, "error": f"Cena {scene_id} não encontrada"}

    # Recalibra os timestamps sequenciais
    curr_time = 0.0
    for idx, c in enumerate(cenas_restantes):
        dur = float(c.get("duracao") or (float(c.get("tempo_fim", 0)) - float(c.get("tempo_inicio", 0))) or 4.0)
        if dur <= 0:
            dur = 4.0
        c["tempo_inicio"] = round(curr_time, 3)
        c["start"] = round(curr_time, 3)
        curr_time += dur
        c["tempo_fim"] = round(curr_time, 3)
        c["end"] = round(curr_time, 3)
        c["duracao"] = round(dur, 3)
        c["scene_index"] = idx + 1
        m_ini = int(c["tempo_inicio"] // 60)
        s_ini = int(c["tempo_inicio"] % 60)
        m_fim = int(c["tempo_fim"] // 60)
        s_fim = int(c["tempo_fim"] % 60)
        c["timestamp"] = f"{m_ini:02d}:{s_ini:02d} - {m_fim:02d}:{s_fim:02d}"
        c["original_timestamp"] = formatar_ts_cena(c["tempo_inicio"], c["tempo_fim"])

    plan["cenas"] = cenas_restantes
    plan["total_cenas"] = len(cenas_restantes)
    salvar_scene_plan(projeto, plan)
    log_event("SCENE_PLAN", f"{projeto}: cena {scene_id} removida. Total restante: {len(cenas_restantes)}")
    return {"success": True, "total": len(cenas_restantes), "plan": plan}


def reordenar_cenas(projeto: str, nova_ordem_ids: list) -> dict:
    """
    Reordena as cenas do scene_plan.json de acordo com a lista de IDs recebida
    e recalibra a linha do tempo sequencialmente.
    """
    plan = carregar_scene_plan(projeto)
    if plan is None:
        return {"success": False, "error": "scene_plan não encontrado"}

    cenas = plan.get("cenas", [])
    if not cenas:
        return {"success": False, "error": "Nenhuma cena para reordenar"}

    # Mapeia por id
    mapa_cenas = {int(c.get("id", -1)): c for c in cenas}
    ids_ordenados = []
    for x in nova_ordem_ids:
        try:
            ids_ordenados.append(int(x))
        except (ValueError, TypeError):
            continue

    cenas_reordenadas = []
    for cid in ids_ordenados:
        if cid in mapa_cenas:
            cenas_reordenadas.append(mapa_cenas.pop(cid))

    # Cenas que não estavam na lista de reordenação (se houver) mantêm-se no final
    for c in cenas:
        if int(c.get("id", -1)) in mapa_cenas:
            cenas_reordenadas.append(c)

    # Recalibra os timestamps sequenciais
    curr_time = 0.0
    for idx, c in enumerate(cenas_reordenadas):
        dur = float(c.get("duracao") or (float(c.get("tempo_fim", 0)) - float(c.get("tempo_inicio", 0))) or 4.0)
        if dur <= 0:
            dur = 4.0
        c["tempo_inicio"] = round(curr_time, 3)
        c["start"] = round(curr_time, 3)
        curr_time += dur
        c["tempo_fim"] = round(curr_time, 3)
        c["end"] = round(curr_time, 3)
        c["duracao"] = round(dur, 3)
        c["scene_index"] = idx + 1
        m_ini = int(c["tempo_inicio"] // 60)
        s_ini = int(c["tempo_inicio"] % 60)
        m_fim = int(c["tempo_fim"] // 60)
        s_fim = int(c["tempo_fim"] % 60)
        c["timestamp"] = f"{m_ini:02d}:{s_ini:02d} - {m_fim:02d}:{s_fim:02d}"
        c["original_timestamp"] = formatar_ts_cena(c["tempo_inicio"], c["tempo_fim"])

    plan["cenas"] = cenas_reordenadas
    salvar_scene_plan(projeto, plan)
    log_event("SCENE_PLAN", f"{projeto}: {len(cenas_reordenadas)} cenas reordenadas com sucesso")
    return {"success": True, "plan": plan}


def aplicar_estilo_legenda_em_lote(projeto: str, estilo_id: str, ativar_todas: bool = True,
                                   caption_custom: Optional[Dict[str, Any]] = None) -> tuple:
    """Aplica o estilo de legenda selecionado em todas as cenas do projeto.

    TAREFA 2: `caption_custom` (opcional) propaga os overrides de
    tamanho/fonte/cor/posição junto com o preset base. Quando None, as
    personalizações já existentes de cada cena são PRESERVADAS.
    """
    plan = carregar_scene_plan(projeto)
    if plan is None:
        return (False, "scene_plan não encontrado", None)

    cenas = plan.get("cenas", [])
    if not cenas:
        return (False, "Nenhuma cena no plano", plan)

    custom_norm = _normalizar_caption_custom(caption_custom) if caption_custom else None

    for c in cenas:
        c["estilo_legenda"] = estilo_id
        c["caption_style"] = estilo_id
        if ativar_todas:
            c["legenda_ativa"] = True
            c["caption_ativo"] = True
        if custom_norm is not None:
            c["caption_custom"] = dict(custom_norm)

    salvar_scene_plan(projeto, plan)
    return (True, f"Estilo de legenda '{estilo_id}' aplicado a {len(cenas)} cenas.", plan)


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
# ---------------------------------------------------------------------------
# Integração com midias_encontradas.json (compatibilidade video_builder)
# ---------------------------------------------------------------------------

_LAST_MIDIAS_SYNC: Dict[str, float] = {}

def sincronizar_midias_encontradas(projeto: str, force: bool = False) -> int:
    """
    Lê o lira_scene_plan.json e atualiza/cria midias_encontradas.json
    com os arquivos de mídia registrados em arquivo_midia ou presentes no disco.
    Otimizado com debounce de 2.0s para evitar saturação de I/O em polling frequente.
    """
    now = time.time()
    if not force and (now - _LAST_MIDIAS_SYNC.get(projeto, 0.0) < 2.0):
        # Evita re-scan pesado de I/O a cada segundo se acabou de rodar
        return 0
    _LAST_MIDIAS_SYNC[projeto] = now
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
        cid = int(cena.get("id") or cena.get("scene_index", 0))
        arquivo = cena.get("arquivo_midia", "")
        ts_ini = float(cena.get("tempo_inicio", 0))
        # Smart resume resiliente: busca usando o resolvedor canônico
        arq_encontrado = resolver_arquivo_cena(projeto, cid, ts_ini)
        if arq_encontrado:
            arquivo = str(arq_encontrado)
            if cena.get("arquivo_midia") != arquivo or cena.get("status") != STATUS_BAIXADA:
                cena["arquivo_midia"] = arquivo
                cena["filename"] = arq_encontrado.name
                cena["status"] = STATUS_BAIXADA
                cena["image_status"] = IMAGE_STATUS_READY if arq_encontrado.suffix.lower() in IMAGEM_EXT else IMAGE_STATUS_DOWNLOADED
                modificado = True

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


def reclassificar_animacoes_roteiro(projeto_id: str) -> dict:
    """Executa o estudo do roteiro/SRT através do Animation Director AI para
    classificar estritamente as cenas que merecem animação e gerar os prompts cinematográficos."""
    plan = carregar_scene_plan(projeto_id)
    if not plan or not plan.get("cenas"):
        return {"success": False, "error": "Plano de cenas não encontrado.", "animadas": 0, "total": 0}

    import services.animation_director_service as animation_director_svc
    from services.event_logger import log_event

    cenas = plan["cenas"]
    total_animadas = 0
    for idx, c in enumerate(cenas):
        # Ignora cenas puramente de texto
        if c.get("tipo") == TIPO_TEXT or c.get("scene_type") == "text":
            c["animate_later"] = False
            c["animar_depois"] = False
            c["animar"] = False
            c["prompt_animacao"] = ""
            continue

        anim_dec = animation_director_svc.direcionar_animacao_cena(c, index=idx)
        should_anim = bool(anim_dec.get("should_animate", False))
        c["animate_later"] = should_anim
        c["animar_depois"] = should_anim
        c["animar"] = should_anim
        c["animation_type"] = anim_dec.get("animation_type", "none")
        c["animation_priority"] = anim_dec.get("animation_priority", "none")
        c["motion_vector"] = anim_dec.get("motion_vector", "static")
        c["animation_rationale"] = anim_dec.get("animation_rationale", "")
        # Preserva prompt_animacao se já existir
        if not c.get("prompt_animacao"):
            c["prompt_animacao"] = ""

        if should_anim:
            total_animadas += 1

    # ── ORÇAMENTADOR DE COTA 70/30 ──────────────────────────────────────────
    COTA_VIDEO = 0.30

    W_RETENTION = 0.40
    W_INTENSITY  = 0.25
    W_HOOK       = 0.20
    W_ROLE       = 0.15
    ROLE_WEIGHT  = {"hook": 1.0, "result": 0.8, "problem": 0.7, "process": 0.5, "support": 0.3}
    duracao_total = max((c.get("tempo_fim", 0) for c in cenas), default=1.0) or 1.0
    HOOK_LIMITE_S = min(15.0, duracao_total * 0.20)
    teto_video = round(len(cenas) * COTA_VIDEO)

    candidatos = [
        c for c in cenas
        if c.get("animar")
        and not c.get("uses_character")
        and c.get("tipo") != "text"
        and c.get("scene_type") != "comparison"
        and c.get("duracao", 0) >= 2.0
    ]

    def _score(c):
        hook_boost = 1.0 if c.get("tempo_inicio", 0) <= HOOK_LIMITE_S else 0.0
        role_w = ROLE_WEIGHT.get(c.get("story_role", ""), 0.3)
        return (
            W_RETENTION * (c.get("retention_index", 50) / 100)
            + W_INTENSITY * c.get("intensity", 0.5)
            + W_HOOK      * hook_boost
            + W_ROLE      * role_w
        )

    candidatos_ordenados = sorted(candidatos, key=_score, reverse=True)
    ids_video = {c["id"] for c in candidatos_ordenados[:teto_video]}

    for c in cenas:
        if c.get("uses_character") or c.get("tipo") == "text":
            c["animar"] = False
            c["animate_later"] = False
            c["animar_depois"] = False
            if c.get("tipo") != "text":
                c["tipo"] = "image"
                c["media_intent"] = "image"
            continue
        if c["id"] in ids_video:
            c["tipo"] = "video"
            c["media_intent"] = "video"
            c["animar"] = True
            c["animate_later"] = True
            c["animar_depois"] = True
            c["video_status"] = c.get("video_status") or "NOT_STARTED"
            c["ken_burns_ativo"] = False
        else:
            c["tipo"] = "image"
            c["media_intent"] = "image"
            c["animar"] = False
            c["animate_later"] = False
            c["animar_depois"] = False
            # Efeitos são definidos pelo usuário na aba de produção — preserva escolhas manuais
            if "ken_burns_ativo" not in c:
                c["ken_burns_ativo"] = False
            if "motion_preset" not in c:
                c["motion_preset"] = ""
    total_animadas = len(ids_video)
    # ── FIM DO ORÇAMENTADOR ──────────────────────────────────────────────────

    # prompt_animacao via DeepSeek — chamada apenas se faltar prompt em cenas animáveis
    precisa_prompts = any(c.get("animate_later") and not c.get("prompt_animacao") for c in cenas)
    if precisa_prompts:
        try:
            _res_anim = __import__("services.deepseek_prompt_service",
                                   fromlist=["aplicar_prompts_animacao_deepseek"])
            _res_anim = _res_anim.aplicar_prompts_animacao_deepseek(
                cenas=cenas,
                context_pack=(plan.get("context_pack") or plan.get("visual_context") or {}),
                total_cenas=len(cenas),
                force=True,
            )
            log_event("SCENE_PLAN",
                      f"prompt_animacao (reclassificacao): fonte={_res_anim.get('fonte')} "
                      f"cenas={_res_anim.get('total')} fallback={_res_anim.get('fallback')}")
        except Exception as e:
            log_event("SCENE_PLAN",
                      f"Aviso: prompt_animacao DeepSeek indisponível: {e}", level="warn")
    else:
        log_event("SCENE_PLAN", "prompt_animacao já presente nas cenas animáveis.")

    salvar_scene_plan(projeto_id, plan)

    # Atualiza meta.json para modo imagem_video se houver cenas animadas
    meta_file = PROJETOS_DIR / projeto_id / "meta.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            meta["modo_producao"] = "imagem_video" if total_animadas > 0 else "somente_imagens"
            meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    log_event("ANIMATION_DIRECTOR", f"Reclassificação de animações concluída para {projeto_id}: {total_animadas}/{len(cenas)} cenas animadas.")
    return {
        "success": True,
        "total": len(cenas),
        "animadas": total_animadas,
        "estaticas": len(cenas) - total_animadas
    }


