r"""
app_web.py — ULTRACUT3 WEB v1.0
================================
Interface web local unificada que CONSONE a GUI desktop (v3.8) e os módulos de
pipeline existentes (transcrição, scene_builder v4.19, broll_director, media_search
v3.6, video_builder, video_encoder) — sem reescrever a lógica interna.

Unifica em UM único fluxo:
  - Fluxo AUTOMÁTICO  (áudio -> vídeo pronto, com polling de eventos via ler_eventos)
  - Fluxo MANUAL      (5 cards: Áudio / Transcrição+Prompts / Buscar Vídeos / Imagens / Montar Vídeo)
  - Modo Local Timestamp (mídia pré-gerada com nome padronizado ^\d+_\[MM-SS\]_...)
  - Fallback manual de storyboard: quando a API Claude falha/timeout (30s), NUNCA
    cai silenciosamente para keywords locais — gera o prompt manual e pausa.

Bind: 127.0.0.1:5000 (apenas local). debug=False.
"""
import os
import re
import sys
import json
import time
import shutil
import threading
from pathlib import Path
from typing import Optional

from flask import Flask, request, jsonify, send_from_directory, session, Response, stream_with_context, send_file
from functools import wraps
import queue

# Garante que o diretório do projeto esteja no sys.path e no cwd
BASE_DIR = Path(__file__).parent.resolve()
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
os.chdir(str(BASE_DIR))

import config_local
from config import PROJETOS_DIR, OUTPUT_DIR, ASSETS_CACHE_DIR  # noqa: E402
from services.event_logger import log_event, ler_eventos, contar_linhas_eventos  # noqa: E402
def detectar_modo_local_timestamp(pasta):
    return False, []

def casar_midia_por_timestamp(cenas, arquivos, tolerancia=3):
    return {}
import services.scene_plan_service as scene_plan_svc

# ---------------------------------------------------------------------------
# Constantes e configuração local da web (fora do pipeline)
# ---------------------------------------------------------------------------

WEB_CONFIG_FILE = BASE_DIR / "web_config.json"

# Chaves de API salvas pela UI (fora do Git — nunca expostas em texto puro).
WEB_KEYS_FILE = BASE_DIR / "web_keys.json"

DEFAULT_PASTA_MIDIA = str(BASE_DIR / "downloads")
DEFAULT_PASTA_DESTINO = str(OUTPUT_DIR / "entregue")

# Timeout do storyboard via API no fluxo automático (regra permanente).
STORYBOARD_TIMEOUT = 30

PIPELINE_CATEGORIES = {
    "TRANSCRIBE", "CHECKPOINT", "SCENES", "STORYBOARD", "CLAUDE", "MEDIA_FETCH",
    "RENDER", "PAUSE", "PIPELINE", "WEB", "SYSTEM",
}

IMAGEM_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
# ---------------------------------------------------------------------------
# Estado em memória dos fluxos web
# ---------------------------------------------------------------------------

_WEB_STATE_LOCK = threading.Lock()
_WEB_STATE = {}  # projeto -> dict: etapa, status, mensagem, prompt_fallback, etc.
_WEB_THREADS = {}  # projeto -> {"tipo": ..., "thread": Thread}


def _web_state(projeto: str) -> dict:
    with _WEB_STATE_LOCK:
        return dict(_WEB_STATE.get(projeto, {}))


def _set_web_state(projeto: str, **campos):
    with _WEB_STATE_LOCK:
        st = _WEB_STATE.setdefault(projeto, {})
        st.update(campos)


def _web_config() -> dict:
    cfg = {
        "pasta_midia_padrao": DEFAULT_PASTA_MIDIA,
        "pasta_destino": DEFAULT_PASTA_DESTINO,
        "pasta_capcut": "",
    }
    try:
        if WEB_CONFIG_FILE.exists():
            with open(WEB_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def _salvar_web_config(cfg: dict):
    with open(WEB_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Chaves de API (Ajuste 1 — Configurações globais)
# ---------------------------------------------------------------------------

_CAMPOS_CHAVE = ("claude", "deepseek", "pexels", "pixabay", "unsplash")

def _chaves_api() -> dict:
    """Chaves de API salvas pela UI (web_keys.json). Vazio se nenhuma salva."""
    try:
        if WEB_KEYS_FILE.exists():
            data = json.loads(WEB_KEYS_FILE.read_text(encoding="utf-8"))
            return {k: str(data[k]).strip() for k in _CAMPOS_CHAVE if data.get(k)}
    except Exception:
        pass
    return {}

def _mascarar_chave(chave: str) -> str:
    """Mascara a chave para exibição: pontos + últimos 4 caracteres."""
    chave = (chave or "").strip()
    if not chave:
        return ""
    if len(chave) <= 4:
        return "••••"
    return "••••" + chave[-4:]

def _chave_efetiva(nome: str) -> str:
    """Chave efetiva: salva pela UI tem prioridade; senão a de config_local ou env."""
    keys = _chaves_api()
    if keys.get(nome):
        return keys[nome]
    if nome == "deepseek":
        import os
        return os.environ.get("DEEPSEEK_API_KEY", "")
    try:
        from config import ANTHROPIC_API_KEY
        return {
            "claude": ANTHROPIC_API_KEY,
        }.get(nome, "")
    except Exception:
        return ""

def _aplicar_chaves_api():
    """Injeta as chaves salvas pela UI nos módulos em tempo de execução."""
    keys = _chaves_api()
    if not keys:
        return
    import config as _config
    import os
    _config.ANTHROPIC_API_KEY = keys.get("claude") or _config.ANTHROPIC_API_KEY
    if keys.get("deepseek"):
        os.environ["DEEPSEEK_API_KEY"] = keys["deepseek"]


# ---------------------------------------------------------------------------
# Meta / projeto helpers
# ---------------------------------------------------------------------------

PASTAS_PROJETO_V2 = ("audio", "srt", "imagens", "videos", "prompts", "capcut")


def _garantir_estrutura_projeto(projeto: str):
    """Garante a estrutura padronizada de pastas do Studio 2.0 (sem mover/apagar arquivos)."""
    project_dir = PROJETOS_DIR / projeto
    project_dir.mkdir(parents=True, exist_ok=True)
    for pasta in PASTAS_PROJETO_V2:
        (project_dir / pasta).mkdir(parents=True, exist_ok=True)


def _meta_defaults(meta: dict) -> dict:
    """Aplica os campos e valores padrão do Studio 2.0 garantindo compatibilidade total."""
    if not isinstance(meta, dict):
        meta = {}
    defaults = {
        "modo_producao": "imagem_video",
        "nome_personagem": "",
        "referencia_visual_global": None,
        "estilo_visual": "photorealistic_cinematic",
        "continuidade_visual": True,
        "studio_version": "v1",
    }
    for k, v in defaults.items():
        if k not in meta:
            meta[k] = v
    return meta


def _meta(projeto: str) -> dict:
    meta_file = PROJETOS_DIR / projeto / "meta.json"
    data = {}
    if meta_file.exists():
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    return _meta_defaults(data)


def _set_meta(projeto: str, meta: dict):
    _garantir_estrutura_projeto(projeto)
    meta = _meta_defaults(meta)
    project_dir = PROJETOS_DIR / projeto
    project_dir.mkdir(parents=True, exist_ok=True)
    meta_file = project_dir / "meta.json"
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)


def _log_web(projeto: str, mensagem: str, status: str = "andamento",
             step: int = None, level: str = "info"):
    """Registra evento na fila EXISTENTE (log_event / ler_eventos) com projeto."""
    log_event("WEB", mensagem, level=level,
              details={"projeto": projeto, "status": status, "step": step})


def _pipeline(projeto: str):
    """Cria PipelineService isolado com project_name definido e callback de progresso."""
    from services.pipeline_service import PipelineService

    p = PipelineService()
    p.project_name = projeto

    def _cb(step, status, msg):
        _log_web(projeto, msg, status=status, step=step)

    p.set_progress_callback(_cb)
    return p


def _audio_do_projeto(projeto: str) -> str:
    """Localiza o áudio original do projeto (para render/transcrição)."""
    meta = _meta(projeto)
    audio = meta.get("arquivo_audio", "")
    if audio and Path(audio).exists():
        return audio
    project_dir = PROJETOS_DIR / projeto
    for ext in [".mp3", ".mp4", ".wav", ".aac", ".m4a", ".ogg", ".mov", ".mkv", ".avi"]:
        for candidato in sorted(project_dir.glob(f"*{ext}")):
            if "_no_silence" in candidato.name:
                continue
            return str(candidato)
    return ""


def _pasta_midia_projeto(projeto: str) -> str:
    meta = _meta(projeto)
    return meta.get("pasta_midia") or _web_config().get("pasta_midia_padrao", "")


# ---------------------------------------------------------------------------
# Eventos (reutiliza o sistema existente via ler_eventos)
# ---------------------------------------------------------------------------

def _eventos_projeto(projeto: str, since: int = 0) -> list:
    """Retorna eventos relacionados ao projeto desde o índice `since`."""
    todos = ler_eventos(linhas=200000)
    start_idx = 0
    try:
        start_idx = int(_meta(projeto).get("web_event_start_idx", 0))
    except Exception:
        pass
    base = max(since, start_idx)
    novos = []
    for evt in todos[base:]:
        details = evt.get("details") or {}
        evt_projeto = details.get("projeto")
        if evt_projeto is not None:
            # ISOLAMENTO POR PROJETO: evento vinculado a um projeto só aparece
            # no polling DESSE projeto (corrige vazamento de contexto entre projetos).
            if evt_projeto == projeto:
                novos.append(evt)
        elif evt.get("category") in PIPELINE_CATEGORIES:
            # Evento global/sem vínculo (ex: boot do servidor, progresso não-atribuído)
            novos.append(evt)
    return novos, len(todos)
# ---------------------------------------------------------------------------
# SRT manual
# ---------------------------------------------------------------------------

def _parse_srt_linha_tempo(texto: str) -> list:
    """Converte linhas '[MM:SS] texto' OU 'MM:SS texto' (sem colchetes) em segmentos."""
    segmentos = []
    # Aceita colchetes opcionais: "[MM:SS] texto" ou "MM:SS texto"
    for m in re.finditer(r"\[?\s*(\d{1,2}):(\d{2})\s*\]?\s+(.+)", texto):
        start = int(m.group(1)) * 60 + int(m.group(2))
        end = start + 5.0
        segmentos.append({
            "start": float(start), "end": end,
            "text": m.group(3).strip(),
            "timestamp": f"{m.group(1).zfill(2)}:{m.group(2)}",
        })
    return segmentos


def _parse_srt_standard(texto: str) -> list:
    """Converte bloco SRT padrão em segmentos."""
    segmentos = []
    blocos = re.split(r"\n\s*\n", texto.strip())
    for bloco in blocos:
        linhas = [l.strip() for l in bloco.strip().splitlines() if l.strip()]
        if len(linhas) < 2:
            continue
        if "-->" in linhas[0]:
            idx_tempo = 0
            linhas_texto = linhas[1:]
        elif "-->" in linhas[1]:
            idx_tempo = 1
            linhas_texto = linhas[2:]
        else:
            continue
        m = re.search(
            r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
            r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})",
            linhas[idx_tempo],
        )
        if not m:
            continue
        start = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                 + int(m.group(4)) / 1000.0)
        end = (int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7))
               + int(m.group(8)) / 1000.0)
        texto = " ".join(linhas_texto)
        if not texto:
            continue
        mm, ss = int(start // 60), int(start % 60)
        segmentos.append({
            "start": round(start, 2), "end": round(end, 2),
            "text": texto, "timestamp": f"{mm:02d}:{ss:02d}",
        })
    return segmentos


def _parse_srt(texto: str) -> list:
    # SRT padrão (contém "-->"): tenta o parser padrão primeiro para não
    # confundir com o formato por linha.
    if "-->" in texto:
        segmentos = _parse_srt_standard(texto)
        if segmentos:
            return segmentos
    return _parse_srt_linha_tempo(texto)


def _salvar_transcricao_manual(projeto: str, segmentos: list):
    """Salva segmentos em roteiro_transcricao.json/.txt e marca transcrição completa."""
    project_dir = PROJETOS_DIR / projeto
    project_dir.mkdir(parents=True, exist_ok=True)

    duracao = segmentos[-1]["end"] if segmentos else 0
    json_data = {
        "segments": segmentos,
        "segment_count": len(segmentos),
        "duration": round(duracao, 2),
        "language": "manual",
        "fonte": "srt_manual",
    }
    with open(project_dir / "roteiro_transcricao.json", "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)

    linhas_txt = [f"[{s['timestamp']}] {s['text']}" for s in segmentos]
    with open(project_dir / "roteiro_transcricao.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(linhas_txt))

    meta = _meta(projeto)
    meta["transcricao_completa"] = True
    meta["fonte_transcricao"] = "srt_manual"
    meta.setdefault("steps", {})["transcrever"] = {
        "status": "concluido",
        "details": {"success": True, "segments": len(segmentos), "fonte": "srt_manual"},
    }
    _set_meta(projeto, meta)
    try:
        scene_plan_svc.gerar_scene_plan(projeto, force=True)
    except Exception as e:
        _log_web(projeto, f"Erro ao gerar scene_plan no SRT manual: {e}", level="warn")

    _log_web(projeto, f"Transcrição manual carregada: {len(segmentos)} segmentos",
             status="concluido", step=0)
    log_event("ROTEIRO", f"{projeto}: {len(segmentos)} falas importadas do SRT")
# ---------------------------------------------------------------------------
# Prompt manual (formato validado: MASTER STYLE locks + transcrição [MM:SS] por linha)
# ---------------------------------------------------------------------------

MASTER_STYLES = {
    "photorealistic_cinematic": {
        "nome": "PHOTOREALISTIC CINEMATIC",
        "estilo": ("Photorealistic cinematic look, natural lighting, shallow depth of "
                   "field, 35mm film grain, high dynamic range, subtle teal-orange color "
                   "grade, crisp realistic textures."),
        "personagem": ("If people appear, keep the same character across all scenes: "
                       "consistent face, build, clothing and age. Realistic proportions, "
                       "no stylization."),
        "mundo": ("Consistent real-world environments. Lighting, weather and time of day "
                  "must be coherent between consecutive scenes."),
        "composicao": ("Rule of thirds, centered subject, 16:9 cinematic framing, "
                       "negative space reserved for text overlays, stable horizon."),
        "negativo": ("no cartoon, no anime, no illustration, no text or watermarks, "
                     "no warped faces or hands, no oversaturation, no CGI look."),
    },
    "boneco_palito": {
        "nome": "STICK FIGURE DOODLE",
        "estilo": ("Hand-drawn stick figure doodle, minimal line art, thin black strokes "
                   "on a plain white or lined-paper background, subtle sketch texture, "
                   "flat 2D."),
        "personagem": ("One consistent stick figure character across all scenes: round "
                       "head, simple line body, same proportions and posture vocabulary "
                       "in every frame."),
        "mundo": ("Minimal props drawn with the same line style; consistent paper "
                  "background and consistent stroke weight."),
        "composicao": ("Subject centered, flat composition, generous margins for text, "
                       "simple readable poses."),
        "negativo": ("no photorealism, no complex shading, no 3D, no colors unless "
                     "explicitly requested, no busy backgrounds."),
    },
    "cartoon": {
        "nome": "CARTOON",
        "estilo": ("Colorful 2D cartoon, clean vector-like shapes, bold clean outlines, "
                   "soft shading, vibrant palette, expressive and playful."),
        "personagem": ("Consistent cartoon character design in every scene: same face, "
                       "same colors, same proportions, recognizable silhouette."),
        "mundo": ("Playful cartoon world with consistent background style and color "
                  "language across all scenes."),
        "composicao": ("Dynamic composition, exaggerated expressions, clear focal "
                       "subject, storybook framing."),
        "negativo": ("no photorealism, no horror, no realistic proportions, no text, "
                     "no watermarks."),
    },
    "cinematografico_dramatico": {
        "nome": "DRAMATIC CINEMATIC",
        "estilo": ("Dramatic cinematic look, high contrast, deep shadows, moody "
                   "atmospheric lighting, anamorphic feel, filmic color grade, "
                   "rich blacks."),
        "personagem": ("Consistent dramatic subject: same silhouette, wardrobe and "
                       "presence in every scene; strong emotional performance."),
        "mundo": ("Dark and atmospheric environments; consistent tone, weather and "
                  "mood continuity between scenes."),
        "composicao": ("Strong leading lines, chiaroscuro lighting, cinematic 16:9 "
                       "framing, deliberate negative space."),
        "negativo": ("no flat lighting, no bright comedy palette, no oversaturation, "
                     "no warped subjects, no text or watermarks."),
    },
}

# ---------------------------------------------------------------------------
# Helper: garante cenas.json a partir da transcrição (elo do pipeline)
# ---------------------------------------------------------------------------

def _garantir_cenas_json(projeto_id: str) -> bool:
    """Cria projetos/<id>/cenas.json a partir de roteiro_transcricao.json se não existir.

    Formato de saída:
        [{"id": 1, "start_time": 0.0, "end_time": 5.0, "texto": "..."}]

    Retorna True se cenas.json existir (ou foi criado agora), False se não há
    transcrição disponível.
    """
    project_dir = PROJETOS_DIR / projeto_id
    cenas_file = project_dir / "cenas.json"
    if cenas_file.exists():
        return True

    rt_file = project_dir / "roteiro_transcricao.json"
    if not rt_file.exists():
        log_event("SCENE_PLAN", f"{projeto_id}: roteiro_transcricao.json não encontrado",
                  level="warn")
        return False

    try:
        rt = json.loads(rt_file.read_text(encoding="utf-8"))
    except Exception as e:
        log_event("SCENE_PLAN", f"{projeto_id}: erro ao ler roteiro_transcricao.json: {e}",
                  level="error")
        return False

    segments = rt.get("segments") or []
    if not segments:
        log_event("SCENE_PLAN", f"{projeto_id}: roteiro_transcricao.json sem segments",
                  level="warn")
        return False

    cenas = [
        {
            "id": i + 1,
            "start_time": float(s.get("start", 0)),
            "end_time": float(s.get("end", float(s.get("start", 0)) + 5.0)),
            "texto": str(s.get("text", s.get("texto", ""))).strip(),
        }
        for i, s in enumerate(segments)
    ]

    try:
        cenas_file.write_text(
            json.dumps(cenas, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log_event("SCENE_PLAN", f"{projeto_id}: cenas.json criado com {len(cenas)} cenas",
                  level="info")
        return True
    except Exception as e:
        log_event("SCENE_PLAN", f"{projeto_id}: erro ao escrever cenas.json: {e}",
                  level="error")
        return False

def _transcricao_linhas(projeto: str) -> str:
    """Monta a transcrição com timestamps no formato '[MM:SS] texto' (uma por linha)."""
    project_dir = PROJETOS_DIR / projeto
    json_path = project_dir / "roteiro_transcricao.json"
    txt_path = project_dir / "roteiro_transcricao.txt"
    linhas = []
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for seg in data.get("segments", []):
                ts = seg.get("timestamp") or _seg_to_ts(seg.get("start", 0))
                linhas.append(f"[{ts}] {seg.get('text', '')}".rstrip())
            return "\n".join(linhas)
        except Exception:
            pass
    if txt_path.exists():
        try:
            with open(txt_path, "r", encoding="utf-8") as f:
                return "\n".join(l.rstrip() for l in f if l.strip())
        except Exception:
            pass
    return ""


def _seg_to_ts(segundos) -> str:
    try:
        s = int(float(segundos))
        return f"{s // 60:02d}:{s % 60:02d}"
    except Exception:
        return "00:00"


def _gerar_prompt_manual(projeto: str, estilo_visual: str) -> str:
    """Gera o texto completo do prompt manual (instruções + locks + transcrição [MM:SS])."""
    style = MASTER_STYLES.get(estilo_visual, MASTER_STYLES["photorealistic_cinematic"])
    # BLOCO 4.6 — se existir storyboard novo (beats), usa linhas
    # "[MM:SS-MM:SS] [VIDEO|IMAGE] texto"; senão mantém a transcrição atual.
    try:
        from services.storyboard_builder import linhas_para_prompt
        linhas_sb = linhas_para_prompt(projeto)
        transcricao = "\n".join(linhas_sb) if linhas_sb else _transcricao_linhas(projeto)
    except Exception:
        transcricao = _transcricao_linhas(projeto)
    if not transcricao:
        transcricao = "(Transcrição não disponível — cole o roteiro com timestamps [MM:SS] aqui.)"

    prompt = f"""You are the visual director of a narrated video. Below you have the
timestamped narration. Create a scene-by-scene visual plan in ENGLISH.

RULES:
- One image/video per [MM:SS] line, matched by timestamp (source of truth for timing).
- Describe each shot with: subject, action, environment, shot type, lighting, composition.
- Keep every scene consistent with the MASTER STYLE LOCKS below (style, character, world,
  composition, negative). Nothing may violate the locks.
- Output each scene as: "MM:SS | <ENGLISH DESCRIPTION>".

MASTER STYLE — {style["nome"]}
STYLE LOCK: {style["estilo"]}
CHARACTER LOCK: {style["personagem"]}
WORLD LOCK: {style["mundo"]}
COMPOSITION LOCK: {style["composicao"]}
NEGATIVE LOCK: {style["negativo"]}

TIMESTAMPED SCRIPT:
{transcricao}
"""
    return prompt
# ---------------------------------------------------------------------------
# Storyboard estrito (API Claude) — regra permanente: sem fallback local silencioso
# ---------------------------------------------------------------------------
# Lira Studio v0.2.0 (Frente 2): o storyboard estrito virou JOB assíncrono.
# Nenhum handler HTTP faz join()/blocking — o fluxo dispara, polla _storyboard_job_status
# com heartbeats de progresso e só então lê o resultado.

_STORYBOARD_JOBS: dict = {}
_STORYBOARD_LOCK = threading.Lock()


def _disparar_storyboard_estrito(projeto: str) -> dict:
    """Dispara o storyboard estrito em job de fundo (não bloqueante).

    Retorna imediatamente {"iniciado": True, "job": {...}}. Se já houver um
    job em andamento para o projeto, retorna o mesmo job (idempotente).
    """
    with _STORYBOARD_LOCK:
        job = _STORYBOARD_JOBS.get(projeto)
        if job and job.get("status") == "andamento":
            return {"iniciado": True, "job": job, "ja_rodando": True}
        job = {"status": "andamento", "iniciado_em": time.time()}
        _STORYBOARD_JOBS[projeto] = job
    threading.Thread(target=_run_storyboard_job, args=(projeto, job), daemon=True).start()
    return {"iniciado": True, "job": job}


def _run_storyboard_job(projeto: str, job: dict):
    """Executa gerar_storyboard(usar_claude=True) dentro do job thread."""
    try:
        _aplicar_chaves_api()
        from config import ANTHROPIC_API_KEY
        from services.broll_director import gerar_storyboard
        if not ANTHROPIC_API_KEY:
            job.update({"confiavel": False, "motivo": "sem_chave",
                        "msg": "ANTHROPIC_API_KEY não configurada em config_local.py",
                        "status": "falhou"})
            return
        r = gerar_storyboard(projeto, usar_claude=True)
        confiavel = bool(r.get("success") and r.get("camada_confiavel")
                         and not r.get("local_fallback", 0) and r.get("camada") == "claude")
        job.update({"confiavel": confiavel, "resultado": r,
                    "motivo": "" if confiavel else "fallback_local",
                    "msg": r.get("error", ""),
                    "status": "concluido" if confiavel else "falhou"})
    except Exception as e:  # noqa: BLE001
        job.update({"confiavel": False, "motivo": "erro", "msg": str(e), "status": "erro"})
    finally:
        job["concluido_em"] = time.time()


def _storyboard_job_status(projeto: str) -> dict:
    job = _STORYBOARD_JOBS.get(projeto) or {}
    return {"status": job.get("status", "idle"),
            "confiavel": job.get("confiavel"),
            "motivo": job.get("motivo", ""),
            "msg": job.get("msg", ""),
            "iniciado_em": job.get("iniciado_em"),
            "concluido_em": job.get("concluido_em")}


def _aguardar_storyboard(projeto: str, timeout: int = STORYBOARD_TIMEOUT) -> dict:
    """Aguarda o job de storyboard com heartbeats de progresso (sem join bloqueante).

    O job roda em thread própria (Claude não trava o caller). Durante a espera,
    emite _log_web a cada 10s para a UI ver progresso real no Console.
    """
    _disparar_storyboard_estrito(projeto)
    inicio = time.time()
    ultimo_heartbeat = 0.0
    while time.time() - inicio < timeout:
        st = _storyboard_job_status(projeto)
        if st["status"] != "andamento":
            return st
        if time.time() - ultimo_heartbeat >= 10:
            ultimo_heartbeat = time.time()
            _log_web(projeto,
                     f"Storyboard via API em andamento... ({int(time.time() - inicio)}s)",
                     status="andamento", step=2)
        time.sleep(1)
    return {"status": "timeout", "confiavel": False, "motivo": "timeout",
            "msg": f"Storyboard via API excedeu {timeout}s de timeout"}


def _marcar_storyboard_confiavel(projeto: str, confiavel: bool):
    """Persiste no meta.json se o storyboard atual foi gerado de forma confiável (Claude)."""
    meta = _meta(projeto)
    meta["storyboard_confiavel"] = bool(confiavel)
    _set_meta(projeto, meta)
    _set_web_state(projeto, storyboard_confiavel=bool(confiavel))


def _storyboard_confiavel_existe(projeto: str) -> bool:
    """True se storyboard.json existe E foi gerado de forma confiável (via Claude).

    Usa APENAS a flag persistida (meta/web state) — nunca infere confiabilidade pelo
    conteúdo do arquivo, pois um storyboard com fallback local silencioso também contém
    search_queries e não pode ser tratado como confiável.
    """
    sb_file = PROJETOS_DIR / projeto / "storyboard.json"
    if not sb_file.exists():
        return False
    if _meta(projeto).get("storyboard_confiavel"):
        return True
    return bool(_web_state(projeto).get("storyboard_confiavel"))


def _gerar_storyboard_local_explicito(projeto: str) -> dict:
    """Gera storyboard local APENAS por decisão explícita do usuário (nunca silencioso).

    Marca a etapa storyboard como concluída no meta (modo local explícito) para que o
    guard de ordem do pipeline (ITEM 6) permita a busca de mídia.
    """
    from services.broll_director import gerar_storyboard
    log_event(
        "STORYBOARD",
        f"USUÁRIO continuou manualmente após falha da API — gerando storyboard local por "
        f"decisão EXPLÍCITA do usuário (não é fallback silencioso).",
        level="warn",
        details={"projeto": projeto},
    )
    _log_web(projeto, "Gerando storyboard local (decisão explícita do usuário)...",
             status="andamento", step=2)
    result = gerar_storyboard(projeto, usar_claude=False)
    if result.get("success"):
        meta = _meta(projeto)
        meta.setdefault("steps", {})["storyboard_broll"] = {
            "status": "concluido",
            "details": {"modo": "local_explicito"},
        }
        _set_meta(projeto, meta)
        _set_web_state(projeto, etapa="storyboard", status="concluido")
    return result
# ---------------------------------------------------------------------------
# Mídias: modo local timestamp ANTES de qualquer fetch via API
# ---------------------------------------------------------------------------

def _media_type_do_arquivo(caminho: str) -> str:
    ext = Path(caminho).suffix.lower()
    return "photo" if ext in IMAGEM_EXT else "video"


def _casar_midias_local_timestamp(projeto: str, arquivos_com_ts: list) -> int:
    """Casa cenas com mídia local (por timestamp) e escreve midias_encontradas.json."""
    cenas_file = PROJETOS_DIR / projeto / "cenas.json"
    if not cenas_file.exists():
        _log_web(projeto, "cenas.json não encontrado para casar mídias locais",
                 status="erro", step=3, level="error")
        return 0

    with open(cenas_file, "r", encoding="utf-8") as f:
        cenas = json.load(f)

    mapeamento = casar_midia_por_timestamp(cenas, arquivos_com_ts, tolerancia_segundos=3)

    resultados = []
    for cena in cenas:
        sid = cena.get("id", 0)
        arquivo = mapeamento.get(sid)
        if arquivo and Path(arquivo).exists():
            resultados.append({
                "scene_id": sid,
                "success": True,
                "arquivo": arquivo,
                "quality": "green",
                "media_type": _media_type_do_arquivo(arquivo),
                "origem_midia": "local_timestamp",
            })
        else:
            resultados.append({
                "scene_id": sid,
                "success": False,
                "needs_media": True,
                "origem_midia": "local_timestamp",
            })

    with open(PROJETOS_DIR / projeto / "midias_encontradas.json", "w", encoding="utf-8") as f:
        json.dump(resultados, f, indent=2, ensure_ascii=False)

    # Registra origem por cena no meta.json
    meta = _meta(projeto)
    meta["origem_midia_por_cena"] = {
        str(r["scene_id"]): "local_timestamp" for r in resultados
    }
    meta["origem_midia"] = "local_timestamp"
    _set_meta(projeto, meta)

    _log_web(projeto,
             f"Mídia local por timestamp: {len(mapeamento)}/{len(cenas)} cenas casadas",
             status="concluido", step=3)
    return len(mapeamento)


# ---------------------------------------------------------------------------
# Card 3 manual — BUSCAR VÍDEOS (AJUSTE 3): reutiliza o fetcher existente,
# busca ESTRITA de vídeo por cena (sem fallback foto), salva com o padrão de
# nome do Elton Flow (NN_[MM-SS]_descricao.mp4) e marca pendências visíveis.
# ---------------------------------------------------------------------------

def _carregar_cenas(projeto: str) -> list:
    """Lista de cenas (cenas.json). [] se ausente/inválido."""
    try:
        with open(PROJETOS_DIR / projeto / "cenas.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _carregar_midias(projeto: str) -> list:
    """Lista de midias_encontradas.json. [] se ausente/inválido."""
    try:
        with open(PROJETOS_DIR / projeto / "midias_encontradas.json", "r",
                  encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _salvar_midias(projeto: str, midias: list):
    with open(PROJETOS_DIR / projeto / "midias_encontradas.json", "w",
              encoding="utf-8") as f:
        json.dump(midias, f, indent=2, ensure_ascii=False)


def _upsert_midia(midias: list, entrada: dict):
    """Insere/atualiza a entrada da cena em midias_encontradas.json."""
    for i, m in enumerate(midias):
        if str(m.get("scene_id", "")) == str(entrada.get("scene_id", "")):
            midias[i] = entrada
            return
    midias.append(entrada)


def _midia_cena_valida(midias: list, sid) -> bool:
    """True se a cena já tem mídia válida em disco (idempotência)."""
    for m in midias:
        if str(m.get("scene_id", "")) == str(sid):
            if m.get("success") and m.get("arquivo") and Path(m["arquivo"]).exists():
                return True
    return False


def _nome_video_elton(cena: dict) -> str:
    """Nome no padrão Elton Flow: NN_[MM-SS]_descricao.mp4 (parser compatível)."""
    sid = int(cena.get("id", 0))
    try:
        inicio = float(cena.get("start_time") or 0)
    except (TypeError, ValueError):
        inicio = 0.0
    mm = int(inicio // 60) % 60
    ss = int(inicio % 60)
    texto = cena.get("texto") or cena.get("keywords") or ""
    if isinstance(texto, list):
        texto = " ".join(str(t) for t in texto)
    slug = re.sub(r"[^\w]+", "_", str(texto).lower()).strip("_")[:40] or "video"
    return f"{sid:03d}_[{mm:02d}-{ss:02d}]_{slug}.mp4"



def _tipo_media_por_cena(projeto: str) -> dict:
    """Mapeia cena (cenas.json id) -> 'video'|'photo' usando o Storyboard Builder.

    Delegado para services.scene_media_type.obter_tipo_media_por_cena.
    """
    from services.scene_media_type import obter_tipo_media_por_cena
    return obter_tipo_media_por_cena(projeto)


def _buscar_videos_status(projeto: str) -> str:
    """Estado da etapa Buscar Vídeos: idle | andamento | concluido | pulado | erro."""
    meta = _meta(projeto)
    if meta.get("buscar_videos_pulado"):
        return "pulado"
    if meta.get("buscar_videos_concluido"):
        return "concluido"
    st = _web_state(projeto)
    if st.get("etapa") == "buscar_videos" and st.get("status") in ("andamento", "erro"):
        return st.get("status")
    return "idle"


def _video_count_status(projeto: str) -> int:
    """Nº de cenas de vídeo do storyboard (usado no Card 3 manual).

    Calculado UMA vez por processo e cacheado no web state. Só é construído o
    storyboard (beats) para projetos MANUAIS (protege o pipeline automático).
    """
    st = _web_state(projeto)
    if st.get("video_count") is not None:
        return st["video_count"]
    meta = _meta(projeto)
    if meta.get("modo_execucao") != "manual" or not meta.get("transcricao_completa"):
        return 0
    try:
        tipos = _tipo_media_por_cena(projeto)
        total = sum(1 for v in tipos.values() if v == "video")
        _set_web_state(projeto, video_count=total)
        return total
    except Exception:  # noqa: BLE001
        return 0


def _resolver_midias(projeto: str) -> dict:
    """
    Ponto único de decisão de mídia (fluxo automático E manual).
    1. Chama detectar_modo_local_timestamp() na pasta de mídia ANTES de qualquer fetch.
    2. Se True -> casar_midia_por_timestamp().
    3. Se False -> fluxo API existente (ThreadPoolExecutor, filtro GREEN, anti-reuso).
    """
    pasta = _pasta_midia_projeto(projeto)

    if pasta and Path(pasta).exists():
        ok, arquivos_ts = detectar_modo_local_timestamp(pasta)
        if ok:
            casadas = _casar_midias_local_timestamp(projeto, arquivos_ts)
            _set_web_state(projeto, midia_modo="local_timestamp",
                           midia_casadas=casadas)
            return {"modo": "local_timestamp", "casadas": casadas}
        _log_web(projeto, "Nenhum padrão de timestamp detectado — mídia será buscada "
                          "via API (Pexels/Pixabay/Unsplash).", status="andamento", step=3)
    else:
        _log_web(projeto, "Pasta de mídia não configurada ou inexistente — usando busca via API.",
                 status="andamento", step=3)

    # --- Fluxo via API (Deprecado - substituído por Google Flow) ---
    _log_web(projeto, "Mídias gerenciadas via Google Flow (etapa stock ignorada).", status="concluido", step=3)
    _set_web_state(projeto, midia_modo="flow",
                   midia_green=0,
                   midia_pendentes=0)
    return {
        "modo": "flow",
        "green": 0,
        "needs_media": 0,
    }
# ---------------------------------------------------------------------------
# Render e info do vídeo final
# ---------------------------------------------------------------------------

def _duracao_video(arquivo: str) -> float:
    from config import FFPROBE_PATH
    import subprocess
    try:
        r = subprocess.run(
            [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
             "-of", "json", arquivo],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return float(json.loads(r.stdout).get("format", {}).get("duration", 0))
    except Exception:
        pass
    return 0.0


def _video_final_info(projeto: str) -> dict:
    """Localiza o MP4 final do projeto e retorna nome/tamanho/duração."""
    meta = _meta(projeto)
    render_step = meta.get("steps", {}).get("renderizar", {})
    arquivo = render_step.get("details", {}).get("arquivo", "")
    if not arquivo or not Path(arquivo).exists():
        candidatos = sorted(OUTPUT_DIR.glob(f"{projeto}.mp4"))
        arquivo = str(candidatos[0]) if candidatos else ""
    if not arquivo or not Path(arquivo).exists():
        return None
    p = Path(arquivo)
    return {
        "nome": p.name,
        "arquivo": str(p),
        "tamanho": p.stat().st_size if p.exists() else 0,
        "duracao": _duracao_video(str(p)),
    }


# Lira Studio v0.2.0 (Frente 2): estatísticas do scene_plan com cache por mtime —
# o api_status é pollado a cada 1s e NÃO pode re-parsear o JSON de 120+ cenas
# nem rodar ffprobe a cada chamada.
_plan_stats_cache: dict = {}


def _plan_stats_cacheados(projeto_id: str) -> dict:
    plan_file = PROJETOS_DIR / projeto_id / "lira_scene_plan.json"
    try:
        mt = plan_file.stat().st_mtime if plan_file.exists() else 0
    except OSError:
        mt = 0
    if mt == 0:
        return {"total": 0, "com_media": 0}
    chave = (projeto_id, mt)
    cache = _plan_stats_cache.get(chave)
    if cache:
        return cache
    plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    cenas = (plan or {}).get("cenas", [])
    dados = {"total": len(cenas),
             "com_media": sum(1 for c in cenas if c.get("arquivo_midia"))}
    _plan_stats_cache[chave] = dados
    if len(_plan_stats_cache) > 200:      # evita crescimento infinito em memória
        _plan_stats_cache.clear()
    return dados


def _executar_etapa_render(projeto: str) -> bool:
    _set_web_state(projeto, etapa="render", status="andamento", mensagem="Montando vídeo...")
    _log_web(projeto, "Montando vídeo...", status="andamento", step=4)
    try:
        try:
            scene_plan_svc.sincronizar_midias_encontradas(projeto)
        except Exception as e:
            _log_web(projeto, f"Aviso ao sincronizar mídias do scene_plan: {e}", level="warn")
        p = _pipeline(projeto)
        result = p.renderizar()
    except Exception as e:  # noqa: BLE001
        _set_web_state(projeto, etapa="render", status="erro", erro=str(e))
        _log_web(projeto, f"Render falhou: {e}", status="erro", step=4, level="error")
        return False

    if result.get("success"):
        video = _video_final_info(projeto)
        _set_web_state(projeto, etapa="pronto", status="concluido",
                       mensagem="Vídeo pronto.", video=video)
        _log_web(projeto, f"Vídeo pronto: {video['nome'] if video else ''}",
                 status="concluido", step=4)
        return True

    _set_web_state(projeto, etapa="render", status="erro", erro=result.get("error", ""))
    _log_web(projeto, f"Render falhou: {result.get('error', '')}",
             status="erro", step=4, level="error")
    return False
# ---------------------------------------------------------------------------
# Fluxo automático (thread principal) — timeline única de 5 estados
# ---------------------------------------------------------------------------

# Eventos de transcrição (Frente 2): em vez de busy-wait de 900s, o fluxo
# automático aguarda um threading.Event que _thread_transcrever seta ao final.
_TRANSCRICAO_EVENTS: dict = {}
_TRANSCRICAO_EVENTS_LOCK = threading.Lock()


def _transcricao_event(projeto: str) -> threading.Event:
    with _TRANSCRICAO_EVENTS_LOCK:
        return _TRANSCRICAO_EVENTS.setdefault(projeto, threading.Event())


def _esperar_transcricao(projeto: str, timeout_seg: int = 900) -> bool:
    """Aguarda transcricao_completa ou detecta erro (evento — sem busy-wait)."""
    meta = _meta(projeto)
    if meta.get("transcricao_completa"):
        return True
    if meta.get("steps", {}).get("transcrever", {}).get("status") == "erro":
        return False
    if not _transcricao_event(projeto).wait(timeout=timeout_seg):
        return False
    meta = _meta(projeto)
    if meta.get("steps", {}).get("transcrever", {}).get("status") == "erro":
        return False
    return bool(meta.get("transcricao_completa"))


def _fluxo_automatico(projeto: str):
    """Executa o pipeline completo do modo automático em background thread."""
    try:
        # 1. Transcrição
        _set_web_state(projeto, fluxo="automatico", etapa="transcrever",
                       status="andamento", mensagem="Transcrevendo áudio...")
        if not _esperar_transcricao(projeto):
            _set_web_state(projeto, etapa="transcrever", status="erro",
                           mensagem="Transcrição falhou ou não concluída.")
            return
        _set_web_state(projeto, etapa="transcrever", status="concluido",
                       mensagem="Transcrição concluída.")

        # 2. Cenas (parte do "Planejando cenas")
        _set_web_state(projeto, etapa="cenas", status="andamento",
                       mensagem="Planejando cenas (Claude)...")
        meta = _meta(projeto)
        if not meta.get("steps", {}).get("gerar_cenas", {}).get("status") == "concluido":
            result = _pipeline(projeto).gerar_cenas()
            if not result.get("success"):
                _set_web_state(projeto, etapa="cenas", status="erro",
                               erro=result.get("error", ""))
                return
        _set_web_state(projeto, etapa="cenas", status="concluido")

        # 3. Storyboard via API (estrito — timeout 30s, sem fallback local silencioso)
        _set_web_state(projeto, etapa="storyboard", status="andamento",
                       mensagem="Planejando cenas (Claude)...")
        sb = _aguardar_storyboard(projeto)
        if not sb.get("confiavel"):
            _marcar_storyboard_confiavel(projeto, False)
            prompt = _gerar_prompt_manual(projeto, "photorealistic_cinematic")
            _set_web_state(
                projeto,
                etapa="storyboard_fallback",
                status="pausado_manual",
                mensagem=("Não consegui gerar o storyboard automaticamente. "
                          "Preparei o prompt para você colar no Claude."),
                prompt_fallback=prompt,
                motivo_fallback=sb.get("motivo", ""),
            )
            _log_web(projeto,
                     f"Storyboard automático não disponível ({sb.get('motivo', '')}) — "
                     "ativando caminho manual com prompt pronto.",
                     status="pausado_manual", step=2, level="warn")
            return

        _marcar_storyboard_confiavel(projeto, True)
        _set_web_state(projeto, etapa="storyboard", status="concluido",
                       storyboard_confiavel=True)
        _log_web(projeto, "Storyboard gerado via Claude com sucesso.",
                 status="concluido", step=2)

        # 4. Mídias + Render
        _executar_fluxo_pos_storyboard(projeto)

    except Exception as e:  # noqa: BLE001
        _set_web_state(projeto, etapa="erro", status="erro", erro=str(e))
        _log_web(projeto, f"Erro no fluxo automático: {e}", status="erro", level="error")
def _storyboard_pronto_ou_local(projeto: str) -> bool:
    """True quando o storyboard está concluído (ou o projeto usa modo local-timestamp,
    que dispensa storyboard). Usado para garantir a ordem do pipeline (ITEM 6)."""
    meta = _meta(projeto)
    if meta.get("steps", {}).get("storyboard_broll", {}).get("status") == "concluido":
        return True
    if _storyboard_confiavel_existe(projeto):
        return True
    pasta = _pasta_midia_projeto(projeto)
    if pasta and Path(pasta).exists():
        ok, _arquivos = detectar_modo_local_timestamp(pasta)
        if ok:
            return True
    return False


def _executar_fluxo_pos_storyboard(projeto: str):
    """Continua o fluxo (automático ou manual) a partir das mídias."""
    meta = _meta(projeto)

    # ITEM 6: busca de mídia NUNCA inicia enquanto o storyboard não estiver concluído.
    if not _storyboard_pronto_ou_local(projeto):
        _set_web_state(projeto, etapa="storyboard", status="andamento",
                       mensagem="Storyboard ainda não concluído — aguardando antes de buscar mídia.")
        _log_web(projeto, "Mídias NÃO iniciada: storyboard não está concluído (ordem do pipeline).",
                 status="andamento", step=2, level="warn")
        return

    # Garante cenas (necessário para casar por timestamp e para render)
    if not meta.get("steps", {}).get("gerar_cenas", {}).get("status") == "concluido":
        r = _pipeline(projeto).gerar_cenas()
        if not r.get("success"):
            _set_web_state(projeto, etapa="cenas", status="erro", erro=r.get("error", ""))
            return

    # Mídias
    _set_web_state(projeto, etapa="midias", status="andamento", mensagem="Buscando mídia...")
    _log_web(projeto, "Buscando mídia...", status="andamento", step=3)
    resultado_midias = _resolver_midias(projeto)
    if resultado_midias.get("error"):
        _set_web_state(projeto, etapa="midias", status="erro",
                       erro=resultado_midias.get("error"))
        return
    _set_web_state(projeto, etapa="midias", status="concluido")

    # Render
    _executar_etapa_render(projeto)


def _continuar_fallback(projeto: str):
    """
    Botão "Já colei e tenho as imagens, continuar" do fallback manual do fluxo automático.
    Decisão EXPLÍCITA do usuário: pode gerar storyboard local se necessário (nunca silencioso).
    """
    try:
        _set_web_state(projeto, etapa="midias", status="andamento",
                       mensagem="Buscando mídia...")
        # Se ainda não há storyboard confiável e a mídia NÃO for local-timestamp,
        # o usuário optou explicitamente pelo caminho manual -> gera storyboard local.
        if not _storyboard_confiavel_existe(projeto):
            pasta = _pasta_midia_projeto(projeto)
            ok_local = False
            if pasta and Path(pasta).exists():
                ok_local, _arquivos = detectar_modo_local_timestamp(pasta)
            if not ok_local:
                _gerar_storyboard_local_explicito(projeto)
        _executar_fluxo_pos_storyboard(projeto)
    except Exception as e:  # noqa: BLE001
        _set_web_state(projeto, etapa="erro", status="erro", erro=str(e))


def _montar_video_manual(projeto: str):
    """Thread do Card 4 do fluxo manual: cenas -> storyboard -> mídias -> render."""
    try:
        _set_web_state(projeto, fluxo="manual", etapa="montar",
                       status="andamento", mensagem="Montando vídeo...")

        # 1. Garante transcrição concluída
        if not _meta(projeto).get("transcricao_completa"):
            _set_web_state(projeto, etapa="montar", status="erro",
                           erro="Transcrição ainda não concluída (aguarde ou cole o SRT no Card 1).")
            return

        # 2. Cenas
        meta = _meta(projeto)
        if not meta.get("steps", {}).get("gerar_cenas", {}).get("status") == "concluido":
            r = _pipeline(projeto).gerar_cenas()
            if not r.get("success"):
                _set_web_state(projeto, etapa="montar", status="erro", erro=r.get("error", ""))
                return

        # AJUSTE 3.4: se o fluxo manual já alocou mídia (Card 3 Buscar Vídeos +
        # Card 4 Imagens), o render combina os vídeos/imagens das
        # midias_encontradas.json na ordem cronológica do storyboard — sem
        # re-executar o storyboard Claude nem a busca automática.
        midias_existentes = _carregar_midias(projeto)
        if any(m.get("success") and m.get("arquivo") and Path(m["arquivo"]).exists()
               for m in midias_existentes):
            _log_web(projeto,
                     "Mídias já alocadas nos cards 3/4 — montando direto com a "
                     "combinação vídeos + imagens existente (sem nova busca).",
                     status="andamento")
            _executar_etapa_render(projeto)
            return

        # 3. Storyboard se modo API (modo local timestamp dispensa storyboard)
        pasta = _pasta_midia_projeto(projeto)
        ok_local = False
        if pasta and Path(pasta).exists():
            ok_local, _arquivos = detectar_modo_local_timestamp(pasta)
        if not ok_local and not _storyboard_confiavel_existe(projeto):
            _set_web_state(projeto, etapa="storyboard", status="andamento",
                           mensagem="Planejando cenas (Claude)...")
            sb = _aguardar_storyboard(projeto)
            if not sb.get("confiavel"):
                _marcar_storyboard_confiavel(projeto, False)
                prompt = _gerar_prompt_manual(projeto, "photorealistic_cinematic")
                _set_web_state(
                    projeto,
                    etapa="storyboard_fallback",
                    status="pausado_manual",
                    mensagem=("Não consegui gerar o storyboard automaticamente. "
                              "Preparei o prompt para você colar no Claude."),
                    prompt_fallback=prompt,
                    motivo_fallback=sb.get("motivo", ""),
                )
                return
            _marcar_storyboard_confiavel(projeto, True)
            _set_web_state(projeto, etapa="storyboard", status="concluido",
                           storyboard_confiavel=True)

        # 4. Mídias + Render
        _executar_fluxo_pos_storyboard(projeto)

    except Exception as e:  # noqa: BLE001
        _set_web_state(projeto, etapa="montar", status="erro", erro=str(e))
        _log_web(projeto, f"Erro ao montar vídeo: {e}", status="erro", level="error")


def _thread_transcrever(projeto: str, audio_path: str):
    """Thread de transcrição em background (função existente do pipeline).

    Lira Studio v0.2.0 (Frente 2): não regenera scene_plan aqui (o plano é
    criado/atualizado sob demanda pelos endpoints Studio 2.0). Mantém apenas
    scene_builder.gerar_cenas() e dispara o evento de conclusão.
    """
    try:
        p = _pipeline(projeto)
        result = p.transcrever(audio_path)
        if result.get("success"):
            try:
                import services.scene_builder as scene_builder
                scene_builder.gerar_cenas(projeto)
            except Exception as e:
                _log_web(projeto, f"Erro ao gerar cenas: {e}", level="warn")

            _set_web_state(projeto, etapa="transcrever", status="concluido",
                           mensagem="Transcrição concluída.", transcricao_concluida=True)
            _log_web(projeto,
                     f"Transcrição concluída: {result.get('segments', 0)} segmentos.",
                     status="concluido", step=0)
        else:
            _set_web_state(projeto, etapa="transcrever", status="erro",
                           mensagem=f"Transcrição falhou: {result.get('error', '')}",
                           transcricao_erro=result.get("error", ""))
            _log_web(projeto, f"Transcrição falhou: {result.get('error', '')}",
                     status="erro", step=0, level="error")
    except Exception as e:  # noqa: BLE001
        _set_web_state(projeto, etapa="transcrever", status="erro",
                       mensagem=f"Erro na transcrição: {e}", transcricao_erro=str(e))
        _log_web(projeto, f"Erro na transcrição: {e}", status="erro", step=0, level="error")
    finally:
        # Acorda o _fluxo_automatico que aguarda em _esperar_transcricao()
        _transcricao_event(projeto).set()


def _iniciar_thread(projeto: str, tipo: str, alvo, *args):
    """Inicia thread de fluxo (única por projeto/tipo)."""
    with _WEB_STATE_LOCK:
        existente = _WEB_THREADS.get(projeto, {})
        if (existente.get("tipo") == tipo and existente.get("thread")
                and existente["thread"].is_alive()):
            return False
        t = threading.Thread(target=alvo, args=args, daemon=True)
        _WEB_THREADS[projeto] = {"tipo": tipo, "thread": t}
        t.start()
        return True


# ---------------------------------------------------------------------------
# Reprocessar / Avançar etapas (ITEM 3) — decisões EXPLÍCITAS do usuário
# ---------------------------------------------------------------------------

STEP_META_KEYS = {
    "transcrever": "transcrever",
    "cenas": "gerar_cenas",
    "storyboard": "storyboard_broll",
    "midias": "buscar_midias",
    "render": "renderizar",
}
ORDEM_ETAPAS = ["transcrever", "cenas", "storyboard", "midias", "render"]


def _thread_reprocessar(projeto: str, etapa: str):
    """Reprocessa uma etapa específica do pipeline (decisão explícita do usuário)."""
    try:
        if etapa not in STEP_META_KEYS:
            return
        meta = _meta(projeto)
        meta.setdefault("steps", {})[STEP_META_KEYS[etapa]] = {"status": "andamento", "details": {}}
        if etapa == "transcrever":
            meta["transcricao_completa"] = False
        _set_meta(projeto, meta)
        _set_web_state(projeto, etapa=etapa, status="andamento",
                       mensagem=f"Reprocessando etapa '{etapa}'...")
        _log_web(projeto, f"Reprocessando etapa '{etapa}' (solicitação do usuário).",
                 status="andamento", step=ORDEM_ETAPAS.index(etapa))

        if etapa == "transcrever":
            audio = _audio_do_projeto(projeto)
            if not audio:
                raise ValueError("Áudio do projeto não encontrado para reprocessar a transcrição")
            _thread_transcrever(projeto, audio)
        elif etapa == "cenas":
            r = _pipeline(projeto).gerar_cenas()
            if not r.get("success"):
                raise ValueError(r.get("error", "Falha ao gerar cenas"))
            _set_web_state(projeto, etapa="cenas", status="concluido")
        elif etapa == "storyboard":
            _marcar_storyboard_confiavel(projeto, False)
            sb = _aguardar_storyboard(projeto)
            if not sb.get("confiavel"):
                prompt = _gerar_prompt_manual(projeto, "photorealistic_cinematic")
                _set_web_state(projeto, etapa="storyboard_fallback", status="pausado_manual",
                               mensagem="Não consegui gerar o storyboard automaticamente. "
                                        "Preparei o prompt para você colar no Claude.",
                               prompt_fallback=prompt, motivo_fallback=sb.get("motivo", ""))
                _log_web(projeto,
                         f"Storyboard reprocessado falhou ({sb.get('motivo', '')}) — caminho manual ativado.",
                         status="pausado_manual", step=2, level="warn")
                return
            _marcar_storyboard_confiavel(projeto, True)
            _set_web_state(projeto, etapa="storyboard", status="concluido", storyboard_confiavel=True)
            _log_web(projeto, "Storyboard reprocessado com sucesso via Claude.",
                     status="concluido", step=2)
        elif etapa == "midias":
            midias_file = PROJETOS_DIR / projeto / "midias_encontradas.json"
            if midias_file.exists():
                midias_file.unlink()
            r = _resolver_midias(projeto)
            if r.get("error"):
                raise ValueError(r.get("error", "Falha ao buscar mídias"))
            _set_web_state(projeto, etapa="midias", status="concluido")
        elif etapa == "render":
            _executar_etapa_render(projeto)
            return
    except Exception as e:  # noqa: BLE001
        _set_web_state(projeto, etapa=etapa, status="erro", erro=str(e))
        _log_web(projeto, f"Reprocessar '{etapa}' falhou: {e}",
                 status="erro",
                 step=ORDEM_ETAPAS.index(etapa) if etapa in ORDEM_ETAPAS else None,
                 level="error")


def _thread_avancar(projeto: str, etapa: str):
    """Força a etapa indicada como concluída e avança para o restante do pipeline."""
    try:
        if etapa not in STEP_META_KEYS:
            return
        meta = _meta(projeto)
        meta.setdefault("steps", {})[STEP_META_KEYS[etapa]] = {
            "status": "concluido", "details": {"forcado_manualmente": True}}
        if etapa == "transcrever":
            meta["transcricao_completa"] = True
        _set_meta(projeto, meta)
        idx = ORDEM_ETAPAS.index(etapa)
        _log_web(projeto, f"Etapa '{etapa}' avançada manualmente (forçada como concluída).",
                 status="concluido", step=idx)

        if etapa == "midias":
            _executar_etapa_render(projeto)
        elif etapa == "render":
            video = _video_final_info(projeto)
            _set_web_state(projeto, etapa="pronto", status="concluido",
                           mensagem="Vídeo pronto (avanço manual).", video=video)
        elif etapa == "transcrever" and (meta.get("modo_execucao") or "automatico") == "automatico":
            # Projetos automáticos (inclusive legados sem modo_execucao) retomam o fluxo
            # completo: transcrição já está concluída no disco → cenas → storyboard → mídias → render.
            _fluxo_automatico(projeto)
        else:
            _executar_fluxo_pos_storyboard(projeto)
    except Exception as e:  # noqa: BLE001
        _set_web_state(projeto, etapa="erro", status="erro", erro=str(e))
        _log_web(projeto, f"Erro ao avançar etapa: {e}", status="erro", level="error")

# ---------------------------------------------------------------------------
# Flow Queues (SSE Bridge)
# ---------------------------------------------------------------------------
_FLOW_QUEUES = []  # List of queue.Queue() to push events to sidepanel SSE
_FLOW_RESULTS = {} # jobId -> {result_dict, error}
# Estado da conexão/fila por projeto (memória, por projeto)
#   {"conectado": bool, "conta": str, "fila_parada": bool,
#    "ultimo_ping": float, "contadores": {...}}
_FLOW_STATE = {}

FLOW_URL = "https://labs.google/fx/tools/flow"

# ---------------------------------------------------------------------------
# Flask app e rotas
# ---------------------------------------------------------------------------

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1 GB (áudio)
app.secret_key = "lira-studio-secret-" + getattr(config_local, "ACCESS_CODE", "dev")

# Registra Rotas v2 (Studio 2.0 / Shadow Routing)
from services.api_v2 import api_v2_bp
app.register_blueprint(api_v2_bp, url_prefix="/api/v2")


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/auth/status")
def auth_status():
    return jsonify({"autenticado": True})

@app.route("/api/auth", methods=["POST"])
def auth():
    session["authenticated"] = True
    return jsonify({"success": True})


# --- GET /projeto/<projeto_id> (SPA — dashboard do projeto) ------------------

@app.route("/projeto/<projeto_id>")
def projeto(projeto_id: str):
    """Rota SPA: serve o frontend; o app.js abre o dashboard do projeto pelo path."""
    return send_from_directory("static", "index.html")


@app.route("/projeto/<projeto_id>/imagens/<filename>")
def serve_project_image(projeto_id: str, filename: str):
    """Serve imagens/vídeos de cena para o frontend.

    ANTIGRAVITY — resolução robusta:
      1. Nome exato em imagens/;
      2. Nome exato em cenas/;
      3. Por ID numérico do filename (001.png -> cena 1) via _arquivo_midia_cena,
         pois as mídias são salvas com padrões canônicos
         (001_[MM-SS]_slug.png / 1_<timestamp>.png / 01_[MM-SS-SS].png);
      4. Fallback: pasta imagens (404 se não existir).
    Sempre via send_from_directory (proteção contra path traversal) e com
    Cache-Control no-cache (mídia muda com regeneração — frescor garantido
    mesmo sem cache buster).
    """
    # Força basename — nunca resolve fora da pasta do projeto (anti-traversal)
    nome = Path(filename or "").name
    if not nome:
        return jsonify({"success": False, "error": "nome inválido"}), 400

    projeto_limpo = Path(projeto_id or "").name  # remove qualquer separador
    pasta = os.path.join(PROJETOS_DIR, projeto_limpo, "imagens")
    cenas_dir = os.path.join(PROJETOS_DIR, projeto_limpo, "cenas")

    def _responder(pasta_alvo: str, arquivo_nome: str):
        resp = send_from_directory(pasta_alvo, arquivo_nome)
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp

    # 1. Nome exato em imagens/
    if os.path.exists(os.path.join(pasta, nome)):
        return _responder(pasta, nome)
    # 2. Nome exato em cenas/
    if os.path.exists(os.path.join(cenas_dir, nome)):
        return _responder(cenas_dir, nome)
    # 3. Resolução por ID de cena (001.png / 1.png / 001.mp4 / 001_[...].png)
    m_id = re.match(r"^(\d+)[\._]", nome, re.IGNORECASE)
    if m_id:
        sid = int(m_id.group(1))
        arq = _arquivo_midia_cena(projeto_limpo, sid)
        if arq and Path(arq).exists():
            return _responder(str(Path(arq).parent), Path(arq).name)
    # 4. Fallback: pasta imagens (gera 404 se não existir)
    return _responder(pasta, nome)


@app.route("/projeto/<projeto_id>/imagens_zip")
@app.route("/api/v2/projeto/<projeto_id>/imagens/zip")
def download_project_images_zip(projeto_id: str):
    """Gera e faz download de um arquivo ZIP contendo todas as imagens do projeto."""
    import io
    import zipfile
    pasta = os.path.join(PROJETOS_DIR, projeto_id, "imagens")
    cenas_dir = os.path.join(PROJETOS_DIR, projeto_id, "cenas")

    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, "w", zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(pasta):
            for root, _, files in os.walk(pasta):
                for f in files:
                    if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        zf.write(os.path.join(root, f), f)
        if os.path.exists(cenas_dir):
            for root, _, files in os.walk(cenas_dir):
                for f in files:
                    if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) and f not in zf.namelist():
                        zf.write(os.path.join(root, f), f)

    memory_file.seek(0)
    return send_file(memory_file, download_name=f"{projeto_id}_imagens.zip", as_attachment=True, mimetype="application/zip")


# --- GET /api/projetos ---------------------------------------------------------

@app.route("/api/projetos")
def api_projetos():
    """Lista os projetos existentes: {id, nome, modo, status, criado_em}.

    Lê o meta.json de cada subpasta de projetos/ (formato usado pelos projetos
    existentes). Projetos antigos sem modo_execucao são tratados como "automatico".
    """
    projetos = []
    if PROJETOS_DIR.exists():
        for p in sorted(PROJETOS_DIR.iterdir()):
            if not p.is_dir():
                continue
            meta = {}
            meta_file = p / "meta.json"
            if meta_file.exists():
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except Exception:
                    meta = {}
            steps = meta.get("steps", {})
            if meta.get("transcricao_completa"):
                if steps.get("renderizar", {}).get("status") == "concluido":
                    status = "pronto"
                elif steps.get("buscar_midias", {}).get("status") == "concluido":
                    status = "midias"
                elif steps.get("storyboard_broll", {}).get("status") == "concluido":
                    status = "storyboard"
                elif steps.get("gerar_cenas", {}).get("status") == "concluido":
                    status = "cenas"
                else:
                    status = "transcrito"
            else:
                status = "transcrevendo" if meta.get("arquivo_audio") else "criado"
            projetos.append({
                "id": p.name,
                "nome": meta.get("display_name") or meta.get("name") or p.name,
                "modo": meta.get("modo_execucao") or "automatico",
                "status": status,
                "criado_em": meta.get("created", ""),
                "transcricao_completa": bool(meta.get("transcricao_completa")),
            })
    return jsonify({"success": True, "projetos": projetos})


# --- DELETE /api/projeto/<projeto_id> ------------------------------------------

@app.route("/api/projeto/<projeto_id>", methods=["DELETE"])
def api_excluir_projeto(projeto_id: str):
    """Exclui completamente o projeto e seus arquivos da pasta projetos/."""
    if not projeto_id or projeto_id in (".", "..", "null"):
        return jsonify({"success": False, "error": "projeto_id inválido"}), 400
    project_dir = PROJETOS_DIR / projeto_id
    try:
        if project_dir.exists():
            shutil.rmtree(project_dir)
        _WEB_STATE.pop(projeto_id, None)
        _WEB_THREADS.pop(projeto_id, None)
        log_event("WEB", f"Projeto '{projeto_id}' excluído.", details={"projeto": projeto_id})
        return jsonify({"success": True, "mensagem": f"Projeto '{projeto_id}' excluído com sucesso."})
    except Exception as e:
        return jsonify({"success": False, "error": f"Erro ao excluir projeto: {e}"}), 500


# --- POST /api/criar_projeto ------------------------------------------------

@app.route("/api/criar_projeto", methods=["POST"])
def api_criar_projeto():
    nome = (request.form.get("nome") or "").strip()
    modo = (request.form.get("modo") or "automatico").strip()
    if not nome:
        return jsonify({"success": False, "error": "Nome do projeto é obrigatório"}), 400
    if modo not in ("automatico", "manual"):
        modo = "automatico"

    from services.pipeline_service import PipelineService
    from services.video_encoder import sanitizar_nome_arquivo

    nome_projeto = sanitizar_nome_arquivo(nome)
    p = PipelineService()
    result = p.criar_projeto(nome, "")
    if not result.get("success"):
        return jsonify(result), 409

    project_dir = PROJETOS_DIR / nome_projeto
    audio_path = ""
    arquivo = request.files.get("audio")
    if arquivo and arquivo.filename:
        ext = Path(arquivo.filename).suffix or ".mp3"
        audio_path = str(project_dir / f"{nome_projeto}{ext}")
        arquivo.save(audio_path)

    meta = _meta(nome_projeto)
    meta["modo_execucao"] = modo
    if audio_path:
        meta["arquivo_audio"] = audio_path
    meta["origem_midia"] = None
    meta["web_event_start_idx"] = contar_linhas_eventos()
    _set_meta(nome_projeto, meta)

    # AJUSTE 2: a criação NÃO pede áudio. O áudio é anexado DENTRO do fluxo:
    #   - Manual: Card 1 (POST /api/upload_audio/<id> ou SRT colado)
    #   - Automático: painel de áudio da tela Automático (POST /api/upload_audio/<id>)
    # O pipeline automático só inicia DEPOIS do upload (api_upload_audio dispara o auto_flow).
    if modo == "automatico":
        _set_web_state(nome_projeto, fluxo="automatico", etapa="aguardando_audio",
                       status="andamento",
                       mensagem="Projeto criado — selecione o áudio para iniciar o pipeline.")
    else:
        _set_web_state(nome_projeto, fluxo="manual", etapa="aguardando_audio",
                       status="andamento",
                       mensagem="Projeto criado — anexe o áudio ou cole o SRT no Card 1.")
    _log_web(nome_projeto,
             f"Projeto '{nome_projeto}' criado (modo: {modo}) — aguardando áudio no fluxo.",
             status="andamento", step=0)

    return jsonify({"projeto_id": nome_projeto, "status": "aguardando_audio"}), 201


# --- GET /api/eventos/<projeto_id> -------------------------------------------

@app.route("/api/eventos/<projeto_id>")
def api_eventos(projeto_id: str):
    try:
        since = int(request.args.get("since", 0))
    except (TypeError, ValueError):
        since = 0
    novos, cursor = _eventos_projeto(projeto_id, since)
    return jsonify({"eventos": novos, "since": cursor})


# --- POST /api/srt_manual ------------------------------------------------------

@app.route("/api/srt_manual", methods=["POST"])
def api_srt_manual():
    data = request.get_json(force=True, silent=True) or {}
    projeto = (data.get("projeto_id") or "").strip()
    srt = (data.get("srt") or "").strip()
    if not projeto:
        return jsonify({"success": False, "error": "projeto_id obrigatório"}), 400
    if not srt:
        return jsonify({"success": False, "error": "SRT vazio"}), 400

    segmentos = _parse_srt(srt)
    if not segmentos:
        return jsonify({
            "success": False,
            "error": ("Nenhum segmento válido. Use SRT padrão, linhas [MM:SS] texto "
                      "ou MM:SS texto sem colchetes (ex: '0:00 Olá mundo')."),
        }), 400

    _salvar_transcricao_manual(projeto, segmentos)
    _set_web_state(projeto, transcricao_concluida=True)
    return jsonify({"success": True, "segmentos": len(segmentos)})


# --- GET /api/transcricao/<projeto_id> -----------------------------------------

@app.route("/api/transcricao/<projeto_id>")
def api_transcricao(projeto_id: str):
    project_dir = PROJETOS_DIR / projeto_id
    txt = ""
    segmentos = []
    json_path = project_dir / "roteiro_transcricao.json"
    txt_path = project_dir / "roteiro_transcricao.txt"
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            segmentos = data.get("segments", [])
            txt = "\n".join(
                f"[{s.get('timestamp', _seg_to_ts(s.get('start', 0)))}] {s.get('text', '')}"
                for s in segmentos
            )
        except Exception:
            pass
    if not txt and txt_path.exists():
        try:
            txt = txt_path.read_text(encoding="utf-8")
        except Exception:
            pass
    return jsonify({
        "success": True,
        "projeto_id": projeto_id,
        "texto": txt,
        "segmentos": segmentos,
        "completa": bool(_meta(projeto_id).get("transcricao_completa")),
    })


# --- POST /api/gerar_prompt/<projeto_id> ---------------------------------------

@app.route("/api/gerar_prompt/<projeto_id>", methods=["POST"])
def api_gerar_prompt(projeto_id: str):
    data = request.get_json(force=True, silent=True) or {}
    estilo = data.get("estilo_visual") or "photorealistic_cinematic"
    if estilo not in MASTER_STYLES:
        estilo = "photorealistic_cinematic"
    prompt = _gerar_prompt_manual(projeto_id, estilo)
    return jsonify({"success": True, "prompt": prompt, "estilo_visual": estilo})
# --- POST /api/storyboard_api/<projeto_id> -------------------------------------

@app.route("/api/storyboard_api/<projeto_id>", methods=["POST"])
def api_storyboard_api(projeto_id: str):
    """Chama storyboard via Claude API (função existente) de forma ESTRITA.

    Executa em background; o frontend acompanha por /api/status. Se a API falhar,
    timeout (30s) ou cair para fallback local, ativa o caminho manual.
    """
    data = request.get_json(force=True, silent=True) or {}
    estilo = data.get("estilo_visual") or "photorealistic_cinematic"

    if not _iniciar_thread(projeto_id, "storyboard_api", _thread_storyboard_api,
                           projeto_id, estilo):
        return jsonify({"success": False, "error": "Storyboard já está em execução"}), 409

    _set_web_state(projeto_id, etapa="storyboard", status="andamento",
                   mensagem="Planejando cenas (Claude)...")
    return jsonify({"success": True, "status": "iniciado"})


def _thread_storyboard_api(projeto_id: str, estilo_visual: str):
    """Thread do /api/storyboard_api — aplica a regra de não-fallback-silencioso."""
    try:
        sb = _aguardar_storyboard(projeto_id)
        if sb.get("confiavel"):
            _marcar_storyboard_confiavel(projeto_id, True)
            _set_web_state(projeto_id, etapa="storyboard", status="concluido",
                           storyboard_confiavel=True)
            _log_web(projeto_id, "Storyboard gerado via Claude API com sucesso.",
                     status="concluido", step=2)
        else:
            _marcar_storyboard_confiavel(projeto_id, False)
            prompt = _gerar_prompt_manual(projeto_id, estilo_visual)
            _set_web_state(
                projeto_id,
                etapa="storyboard_fallback",
                status="pausado_manual",
                mensagem=("Não consegui gerar o storyboard automaticamente. "
                          "Preparei o prompt para você colar no Claude."),
                prompt_fallback=prompt,
                motivo_fallback=sb.get("motivo", ""),
            )
            _log_web(projeto_id,
                     f"Storyboard via API indisponível ({sb.get('motivo', '')}) — "
                     "caminho manual ativado.", status="pausado_manual",
                     step=2, level="warn")
    except Exception as e:  # noqa: BLE001
        _set_web_state(projeto_id, etapa="storyboard", status="erro", erro=str(e))
        _log_web(projeto_id, f"Erro no storyboard via API: {e}",
                 status="erro", step=2, level="error")


# --- POST /api/selecionar_midia/<projeto_id> ------------------------------------

@app.route("/api/selecionar_midia/<projeto_id>", methods=["POST"])
def api_selecionar_midia(projeto_id: str):
    data = request.get_json(force=True, silent=True) or {}
    caminho = (data.get("caminho") or "").strip().strip('"').strip("'")
    media_type = (data.get("media_type") or "video").strip()

    if not caminho or not Path(caminho).exists() or not Path(caminho).is_dir():
        return jsonify({"success": False,
                        "error": f"Pasta não encontrada: {caminho}"}), 400

    meta = _meta(projeto_id)
    meta["pasta_midia"] = caminho
    meta["media_type"] = media_type if media_type in ("video", "photo") else "video"
    _set_meta(projeto_id, meta)

    ok, arquivos_ts = detectar_modo_local_timestamp(caminho)
    if ok:
        _set_web_state(projeto_id, midia_modo="local_timestamp")
        _log_web(projeto_id,
                 "✓ Detectado padrão de nome com timestamp — usando correspondência "
                 "direta por cena (sem busca por API).",
                 status="concluido", step=3)
        return jsonify({
            "success": True,
            "modo": "local_timestamp",
            "arquivos_casados": len(arquivos_ts),
            "mensagem": "✓ Detectado padrão de nome com timestamp — usando correspondência "
                        "direta por cena (sem busca por API).",
        })

    _set_web_state(projeto_id, midia_modo="api")
    _log_web(projeto_id,
             "Nenhum padrão de timestamp detectado — mídia será buscada via API "
             "(Pexels/Pixabay/Unsplash) usando os prompts do storyboard.",
             status="andamento", step=3)
    return jsonify({
        "success": True,
        "modo": "api",
        "mensagem": "Nenhum padrão de timestamp detectado — mídia será buscada via API "
                    "(Pexels/Pixabay/Unsplash) usando os prompts do storyboard.",
    })
# --- POST /api/montar_video/<projeto_id> ---------------------------------------

@app.route("/api/montar_video/<projeto_id>", methods=["POST"])
def api_montar_video(projeto_id: str):
    """Dispara o render final em background (fluxo manual Card 4)."""
    if not _iniciar_thread(projeto_id, "montar", _montar_video_manual, projeto_id):
        return jsonify({"success": False, "error": "Já há uma montagem em execução"}), 409
    _set_web_state(projeto_id, etapa="montar", status="andamento",
                   mensagem="Montando vídeo...")
    return jsonify({"success": True, "status": "montando"})


# --- POST /api/buscar_videos/<projeto_id> e pular (AJUSTE 3 — Card 3 manual) -----

@app.route("/api/buscar_midias/<projeto_id>", methods=["POST"])
@app.route("/api/buscar_videos/<projeto_id>", methods=["POST"])
def api_buscar_videos(projeto_id: str):
    """Deprecada: busca de vídeos via API foi removida (Studio 2.0 usa Google Flow)."""
    return jsonify({"success": False,
                    "error": "Rota deprecada. Use o Google Flow (Studio 2.0)."}), 410


@app.route("/api/pular_buscar_videos/<projeto_id>", methods=["POST"])
def api_pular_buscar_videos(projeto_id: str):
    """Pula a etapa Buscar Vídeos por decisão EXPLÍCITA do usuário (nunca silencioso)."""
    meta = _meta(projeto_id)
    meta["buscar_videos_pulado"] = True
    _set_meta(projeto_id, meta)
    _set_web_state(projeto_id, etapa="buscar_videos", status="pulado",
                   buscar_videos="pulado",
                   mensagem="Etapa Buscar Vídeos pulada pelo usuário.")
    _log_web(projeto_id, "Etapa 'Buscar Vídeos' pulada pelo usuário.",
             status="concluido")
    return jsonify({"success": True, "status": "pulado"})


# --- POST /api/continuar_fallback/<projeto_id> ----------------------------------

@app.route("/api/continuar_fallback/<projeto_id>", methods=["POST"])
def api_continuar_fallback(projeto_id: str):
    """Botão 'Já colei e tenho as imagens, continuar' (fallback manual do automático)."""
    if not _iniciar_thread(projeto_id, "auto_flow", _continuar_fallback, projeto_id):
        return jsonify({"success": False, "error": "Fluxo já em execução"}), 409
    return jsonify({"success": True, "status": "continuando"})


# --- GET /api/status/<projeto_id> ------------------------------------------------

@app.route("/api/status/<projeto_id>")
def api_status(projeto_id: str):
    meta = _meta(projeto_id)
    st = _web_state(projeto_id)

    steps = meta.get("steps", {})
    etapas_concluidas = {
        "transcrever": bool(meta.get("transcricao_completa"))
        or steps.get("transcrever", {}).get("status") == "concluido",
        "gerar_cenas": steps.get("gerar_cenas", {}).get("status") == "concluido",
        "storyboard": steps.get("storyboard_broll", {}).get("status") == "concluido"
        or bool(st.get("storyboard_confiavel")),
        "midias": steps.get("buscar_midias", {}).get("status") == "concluido",
        "render": steps.get("renderizar", {}).get("status") == "concluido",
    }

    video = st.get("video") or _video_final_info(projeto_id)

    # ETAPA 3 — PRODUÇÃO NO FLOW: presença do scene_plan para habilitar o card 3
    # Frente 2: estatísticas cacheadas por mtime (sem re-parse do JSON a cada poll)
    _sp_stats = _plan_stats_cacheados(projeto_id)
    scene_plan_total = _sp_stats.get("total", 0)
    scene_plan_com_media = _sp_stats.get("com_media", 0)

    return jsonify({
        "success": True,
        "projeto_id": projeto_id,
        "modo_execucao": meta.get("modo_execucao", st.get("fluxo", "automatico")),
        "etapa": st.get("etapa", "transcrever"),
        "status": st.get("status", "andamento"),
        "mensagem": st.get("mensagem", ""),
        "erro": st.get("erro", ""),
        "etapas_concluidas": etapas_concluidas,
        "transcricao_completa": etapas_concluidas["transcrever"],
        "prompt_fallback": st.get("prompt_fallback", ""),
        "motivo_fallback": st.get("motivo_fallback", ""),
        "midia_modo": st.get("midia_modo") or meta.get("origem_midia"),
        "midia_casadas": st.get("midia_casadas", 0),
        "pasta_midia": meta.get("pasta_midia", ""),
        "arquivo_audio": meta.get("arquivo_audio", ""),
        "video": video,
        # AJUSTE 3 — Card 3 manual: estado da busca de vídeos
        "buscar_videos_status": _buscar_videos_status(projeto_id),
        "buscar_videos_pulado": bool(meta.get("buscar_videos_pulado")),
        "video_count": _video_count_status(projeto_id),
        "videos_ok": st.get("videos_ok", 0) or 0,
        "videos_pendentes": st.get("videos_pendentes", 0) or 0,
        # ETAPA 3 — produção no Flow
        "scene_plan_total": scene_plan_total,
        "scene_plan_com_media": scene_plan_com_media,
        # Frente 2: status do job de storyboard estrito (andamento/concluido/falhou/timeout)
        "storyboard_status": _storyboard_job_status(projeto_id),
    })


# --- POST /api/salvar_video/<projeto_id> -----------------------------------------

@app.route("/api/salvar_video/<projeto_id>", methods=["POST"])
def api_salvar_video(projeto_id: str):
    data = request.get_json(force=True, silent=True) or {}
    caminho_destino = (data.get("caminho") or "").strip().strip('"').strip("'")

    video = _video_final_info(projeto_id)
    if not video:
        return jsonify({"success": False, "error": "Vídeo final não encontrado"}), 404

    cfg = _web_config()
    if caminho_destino:
        cfg["pasta_destino"] = caminho_destino
        _salvar_web_config(cfg)
    destino = cfg.get("pasta_destino") or DEFAULT_PASTA_DESTINO

    try:
        destino_dir = Path(destino)
        destino_dir.mkdir(parents=True, exist_ok=True)
        arquivo_destino = destino_dir / video["nome"]
        shutil.copy2(video["arquivo"], str(arquivo_destino))
        _log_web(projeto_id, f"Vídeo salvo em: {arquivo_destino}",
                 status="concluido", step=4)
        return jsonify({"success": True, "destino": str(arquivo_destino),
                        "tamanho": arquivo_destino.stat().st_size})
    except Exception as e:  # noqa: BLE001
        return jsonify({"success": False, "error": str(e)}), 500


# --- POST /api/abrir_pasta/<projeto_id> ------------------------------------------

@app.route("/api/abrir_pasta/<projeto_id>", methods=["POST"])
def api_abrir_pasta(projeto_id: str):
    """Abre a pasta de saída no explorador do Windows (os.startfile)."""
    video = _video_final_info(projeto_id)
    cfg = _web_config()
    destino = cfg.get("pasta_destino") or DEFAULT_PASTA_DESTINO

    # Abre a pasta de destino se ela existir e tiver o vídeo; senão, a de saída.
    if video:
        destino_dir = Path(destino)
        if destino_dir.exists() and (destino_dir / video["nome"]).exists():
            pasta = str(destino_dir)
        else:
            pasta = str(Path(video["arquivo"]).parent)
    else:
        pasta = str(OUTPUT_DIR)
        Path(pasta).mkdir(parents=True, exist_ok=True)

    try:
        os.startfile(pasta)  # abertura local intencional no Windows
        return jsonify({"success": True, "pasta": pasta})
    except Exception as e:  # noqa: BLE001
        return jsonify({"success": False, "error": str(e)}), 500


# --- POST /api/deletar_projeto/<projeto_id> (ITEM 1) ---------------------------

@app.route("/api/deletar_projeto/<projeto_id>", methods=["POST"])
@require_auth
def api_deletar_projeto(projeto_id: str):
    """Exclui permanentemente o projeto (pasta projetos/<nome>/)."""
    project_dir = PROJETOS_DIR / projeto_id
    if not project_dir.exists() or not project_dir.is_dir():
        return jsonify({"success": False, "error": "Projeto não encontrado"}), 404
    with _WEB_STATE_LOCK:
        _WEB_STATE.pop(projeto_id, None)
        _WEB_THREADS.pop(projeto_id, None)
    try:
        shutil.rmtree(str(project_dir))
        log_event("WEB", f"Projeto '{projeto_id}' excluído.", level="info",
                  details={"projeto": projeto_id})
        return jsonify({"success": True, "deleted": projeto_id})
    except Exception as e:  # noqa: BLE001
        return jsonify({"success": False, "error": str(e)}), 500


# --- POST /api/reprocessar e /api/avancar (ITEM 3) -----------------------------

@app.route("/api/reprocessar/<projeto_id>", methods=["POST"])
def api_reprocessar(projeto_id: str):
    """Reprocessa uma etapa específica em background."""
    data = request.get_json(force=True, silent=True) or {}
    etapa = (data.get("etapa") or "").strip()
    if etapa not in STEP_META_KEYS:
        return jsonify({"success": False, "error": f"Etapa inválida: {etapa}"}), 400
    if not _iniciar_thread(projeto_id, "reprocessar", _thread_reprocessar, projeto_id, etapa):
        return jsonify({"success": False, "error": "Já há uma operação em execução"}), 409
    return jsonify({"success": True, "status": "reprocessando", "etapa": etapa})


@app.route("/api/avancar/<projeto_id>", methods=["POST"])
def api_avancar(projeto_id: str):
    """Força o avanço manual da etapa (marca como concluída e segue o pipeline)."""
    data = request.get_json(force=True, silent=True) or {}
    etapa = (data.get("etapa") or "").strip()
    if etapa not in STEP_META_KEYS:
        return jsonify({"success": False, "error": f"Etapa inválida: {etapa}"}), 400
    if not _iniciar_thread(projeto_id, "avancar", _thread_avancar, projeto_id, etapa):
        return jsonify({"success": False, "error": "Já há uma operação em execução"}), 409
    return jsonify({"success": True, "status": "avancando", "etapa": etapa})


# --- GET /api/transcricao/<projeto_id>/download (ITEM 4) -----------------------

def _formatar_srt(segmentos: list) -> str:
    """Gera conteúdo SRT padrão a partir dos segmentos da transcrição."""
    def _ts(s):
        s = max(0.0, float(s))
        h = int(s // 3600)
        m = int(s % 3600 // 60)
        sec = s % 60
        return f"{h:02d}:{m:02d}:{int(sec):02d},{int((sec - int(sec)) * 1000):03d}"

    blocos = []
    for i, seg in enumerate(segmentos, 1):
        texto = (seg.get("text") or "").strip()
        if not texto:
            continue
        fim = seg.get("end", seg.get("start", 0) + 5)
        blocos.append(f"{i}\n{_ts(seg.get('start', 0))} --> {_ts(fim)}\n{texto}\n")
    return "\n".join(blocos)


@app.route("/api/transcricao/<projeto_id>/download")
@app.route("/api/download_transcricao/<projeto_id>")
@app.route("/api/download_transcricao/<projeto_id>/<formato>")
def api_download_transcricao(projeto_id: str, formato: str = "txt"):
    """Baixa a transcrição em formato .txt (frases [MM:SS]) ou .srt (padrão)."""
    from flask import Response
    formato_req = (request.args.get("formato") or formato or "txt").strip().lower()
    formato = formato_req
    project_dir = PROJETOS_DIR / projeto_id
    segmentos = []
    json_path = project_dir / "roteiro_transcricao.json"
    txt_path = project_dir / "roteiro_transcricao.txt"
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                segmentos = json.load(f).get("segments", [])
        except Exception:
            segmentos = []
    if not segmentos and txt_path.exists():
        try:
            for line in txt_path.read_text(encoding="utf-8").splitlines():
                m = re.match(r"\[(\d{1,2}):(\d{2})\]\s*(.*)", line)
                if m:
                    start = int(m.group(1)) * 60 + int(m.group(2))
                    segmentos.append({
                        "start": start, "end": start + 5,
                        "text": m.group(3),
                        "timestamp": f"{int(m.group(1)):02d}:{m.group(2)}",
                    })
        except Exception:
            pass
    if not segmentos:
        return jsonify({"success": False, "error": "Transcrição não disponível"}), 404

    if formato == "srt":
        conteudo = _formatar_srt(segmentos)
        nome = f"{projeto_id}.srt"
        mimetype = "application/x-subrip"
    else:
        linhas = [
            f"[{s.get('timestamp', _seg_to_ts(s.get('start', 0)))}] {s.get('text', '').strip()}"
            for s in segmentos
        ]
        conteudo = "\n".join(linhas)
        nome = f"{projeto_id}.txt"
        mimetype = "text/plain; charset=utf-8"

    return Response(
        conteudo,
        mimetype=mimetype,
        headers={"Content-Disposition": f"attachment; filename={nome}"},
    )


# --- Cenas detalhadas (ITEM 6/7) — prompt de imagem, animação, classificação -----

def _montar_prompt_imagem(sb: dict) -> str:
    """Monta o prompt de imagem (Google Flow) a partir do storyboard."""
    estilo = ("Photorealistic cinematic, natural lighting, shallow depth of field, "
              "35mm film grain, subtle teal-orange grade")
    partes = []
    if sb.get("subject"):
        partes.append(f"Subject: {sb['subject']}")
    if sb.get("action"):
        partes.append(f"Action: {sb['action']}")
    if sb.get("environment"):
        partes.append(f"Environment: {sb['environment']}")
    if sb.get("shot_type"):
        partes.append(f"Shot: {sb['shot_type']}")
    if sb.get("energy"):
        partes.append(f"Energy: {sb['energy']}")
    if sb.get("emotion"):
        partes.append(f"Mood: {sb['emotion']}")
    partes.append("Style: " + estilo)
    partes.append("Negative: no text, no watermark, no warped faces, no oversaturation")
    return "; ".join(partes)


def _animacao_para_cena(sb: dict) -> str:
    """Deriva a animação (movimento da câmera) a partir do storyboard."""
    energia = (sb.get("energy") or "moderate").lower()
    shot = (sb.get("shot_type") or "").lower()
    if energia == "high":
        return "Slow zoom-in (1.00 -> 1.06) com leve balanço de câmera, 2s ease"
    if "wide" in shot:
        return "Slow zoom-out (1.06 -> 1.00), pan suave da esquerda para a direita"
    if "close" in shot:
        return "Slow zoom-in (1.00 -> 1.05), foco no detalhe, 2s ease"
    return "Ken Burns zoom sutil (1.00 -> 1.04), 2s ease"


def _carregar_cenas_detalhadas(projeto: str) -> list:
    """Mescla cenas.json + storyboard.json + midias_encontradas.json por cena."""
    project_dir = PROJETOS_DIR / projeto
    cenas = []
    try:
        with open(project_dir / "cenas.json", "r", encoding="utf-8") as f:
            cenas = json.load(f)
    except Exception:
        pass

    storyboard = {}
    try:
        with open(project_dir / "storyboard.json", "r", encoding="utf-8") as f:
            for s in json.load(f):
                storyboard[str(s.get("id", s.get("scene_id", 0)))] = s
    except Exception:
        pass

    midias = {}
    try:
        with open(project_dir / "midias_encontradas.json", "r", encoding="utf-8") as f:
            for m in json.load(f):
                midias[str(m.get("scene_id", 0))] = m
    except Exception:
        pass

    resultado = []
    for c in cenas:
        sid = c.get("id", 0)
        sb = storyboard.get(str(sid), {})
        mid = midias.get(str(sid), {})
        arquivo = mid.get("arquivo", "")
        tem_midia = bool(mid.get("success") and arquivo and Path(arquivo).exists())
        media_pref = sb.get("media_preference", "video")

        if tem_midia:
            ext = Path(arquivo).suffix.lower()
            tipo = "video" if ext in (".mp4", ".mov", ".webm", ".avi", ".mkv") else "image_prompt"
        else:
            tipo = "image_prompt" if media_pref == "photo" else "video"

        resultado.append({
            "id": sid,
            "nome": f"Cena {sid}",
            "texto": c.get("texto", ""),
            "start": c.get("start_time", 0),
            "end": c.get("end_time", 0),
            "duracao": c.get("duration", 0),
            "timestamps": c.get("timestamps", []),
            "tipo_midia": tipo,          # "video" | "image_prompt"
            "tem_midia": tem_midia,
            "arquivo": arquivo if tem_midia else "",
            "origem_midia": mid.get("origem_midia", sb.get("origem_midia", "")),
            "status": "ok" if tem_midia else "pendente",
            "image_prompt": _montar_prompt_imagem(sb),
            "animacao": _animacao_para_cena(sb),
            "search_query": (sb.get("search_queries") or [""])[0],
            "thumb_url": f"/api/cena/{projeto}/{sid}/thumbnail" if tem_midia else "",
        })
    return resultado


def _extrair_cena_id(nome_arquivo: str, cena_ids: set) -> int:
    """Extrai o id da cena a partir do nome: '007_x.jpg' -> 7 (se existir cena 7)."""
    m = re.match(r"^\d+_", nome_arquivo)
    if m:
        num = int(m.group(0)[:-1])
        if str(num) in cena_ids:
            return num
    m = re.search(r"\[(\d{2})-(\d{2})\]|(\d{2})-(\d{2})_", nome_arquivo)
    if m:
        min_v = int(m.group(1) or m.group(3))
        seg_v = int(m.group(2) or m.group(4))
        return -(min_v * 60 + seg_v)  # negativo = timestamp (associar por start_time)
    return 0


def _marcar_storyboard_imagem(projeto: str, sid, caminho: str):
    """Marca a cena no storyboard.json como imagem importada."""
    sb_file = PROJETOS_DIR / projeto / "storyboard.json"
    try:
        with open(sb_file, "r", encoding="utf-8") as f:
            sb = json.load(f)
        for s in sb:
            if str(s.get("id", s.get("scene_id", 0))) == str(sid):
                s["origem_midia"] = "imagem_importada"
                s["arquivo"] = caminho
                s["media_preference"] = "photo"
                s["tipo_midia"] = "image_prompt"
                break
        with open(sb_file, "w", encoding="utf-8") as f:
            json.dump(sb, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _atualizar_midia_importada(projeto: str, sid, caminho: str):
    """Registra a imagem importada em midias_encontradas.json (por cena)."""
    midias_file = PROJETOS_DIR / projeto / "midias_encontradas.json"
    try:
        midias = []
        if midias_file.exists():
            with open(midias_file, "r", encoding="utf-8") as f:
                midias = json.load(f)
        atualizado = False
        for m in midias:
            if str(m.get("scene_id", 0)) == str(sid):
                m.update({
                    "success": True, "arquivo": caminho, "media_type": "photo",
                    "quality": "green", "origem_midia": "imagem_importada",
                })
                atualizado = True
                break
        if not atualizado:
            midias.append({
                "scene_id": sid, "success": True, "arquivo": caminho,
                "media_type": "photo", "quality": "green",
                "origem_midia": "imagem_importada",
            })
        with open(midias_file, "w", encoding="utf-8") as f:
            json.dump(midias, f, indent=2, ensure_ascii=False)

        # Registra origem por cena no meta.json (debug futuro)
        meta = _meta(projeto)
        origem = meta.setdefault("origem_midia_por_cena", {})
        origem[str(sid)] = "imagem_importada"
        meta["origem_midia"] = "imagem_importada"
        _set_meta(projeto, meta)
    except Exception:
        pass


def _arquivo_midia_cena(projeto: str, scene_id) -> str:
    """Localiza o arquivo de mídia de uma cena varrendo todas as fontes e pastas estruturadas."""
    sid_int = int(scene_id) if str(scene_id).isdigit() else 0
    pdir = PROJETOS_DIR / projeto

    # 1. Checa lira_scene_plan.json
    plan = scene_plan_svc.carregar_scene_plan(projeto)
    if plan and plan.get("cenas"):
        for c in plan["cenas"]:
            if str(c.get("id")) == str(scene_id) or str(c.get("scene_index")) == str(scene_id):
                for campo in ["arquivo_midia", "download_path"]:
                    if c.get(campo):
                        p = Path(c[campo])
                        if not p.is_absolute():
                            p = pdir / p
                        if p.exists():
                            return str(p)

    # 2. Checa pastas estruturadas oficiais do projeto
    candidatos = [
        pdir / "cenas" / f"{sid_int:03d}.png",
        pdir / "cenas" / f"{sid_int:03d}.mp4",
        pdir / "cenas" / f"{sid_int:03d}" / f"{sid_int:03d}.png",
        pdir / "cenas" / f"{sid_int:03d}" / f"{sid_int:03d}.mp4",
        pdir / "cenas" / f"{sid_int:03d}" / "imagem.png",
        pdir / "cenas" / f"{sid_int:03d}" / "video.mp4",
        pdir / "imagens" / f"{sid_int:03d}.png",
        pdir / "videos" / f"{sid_int:03d}.mp4",
        pdir / "cenas" / f"{scene_id}.png",
        pdir / "cenas" / f"{scene_id}.mp4",
    ]
    for cand in candidatos:
        if cand.exists():
            return str(cand)

    # 2b. Busca ampliada (BLOCO 3A aprovado), na ordem:
    #     1. {cid}_* / {cid:02d}_* / {cid:03d}_* em cenas/ e imagens/
    #     2. cena_{cid:03d}_*/ ou cena_{cid}_*/ (subpasta de auditoria)
    for glob_fn in (
        lambda: sorted((pdir / "cenas").glob(f"{sid_int}_*"), key=lambda x: x.stat().st_mtime, reverse=True),
        lambda: sorted((pdir / "cenas").glob(f"{sid_int:02d}_*"), key=lambda x: x.stat().st_mtime, reverse=True),
        lambda: sorted((pdir / "cenas").glob(f"{sid_int:03d}_*"), key=lambda x: x.stat().st_mtime, reverse=True),
        lambda: sorted((pdir / "imagens").glob(f"{sid_int}_*"), key=lambda x: x.stat().st_mtime, reverse=True),
        lambda: sorted((pdir / "imagens").glob(f"{sid_int:02d}_*"), key=lambda x: x.stat().st_mtime, reverse=True),
        lambda: sorted((pdir / "imagens").glob(f"{sid_int:03d}_*"), key=lambda x: x.stat().st_mtime, reverse=True),
        lambda: sorted((pdir / "cenas").glob("cena_{:03d}_*".format(sid_int)), key=lambda x: str(x)),
        lambda: sorted((pdir / "cenas").glob("cena_{}_*".format(scene_id)), key=lambda x: str(x)),
    ):
        try:
            encontrados = glob_fn()
        except Exception:
            encontrados = []
        if encontrados:
            for cand in encontrados:
                if cand.is_file() and cand.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov"):
                    return str(cand)
                if cand.is_dir():
                    # dentro da subpasta de auditoria: procura arquivos de mídia
                    for sub in ["imagem.png", "video.mp4", f"{sid_int:03d}.png", f"{sid_int:03d}.mp4", f"{sid_int}.png", f"{sid_int}.mp4"]:
                        f = cand / sub
                        if f.exists() and f.is_file():
                            return str(f)
                    # qualquer imagem/vídeo dentro da subpasta
                    for f in sorted(cand.iterdir()):
                        if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov"):
                            return str(f)
                elif cand.is_file():
                    return str(cand)

    # 3. Checa midias_encontradas.json
    midias_file = pdir / "midias_encontradas.json"
    if midias_file.exists():
        try:
            with open(midias_file, "r", encoding="utf-8") as f:
                for m in json.load(f):
                    if str(m.get("scene_id")) == str(scene_id) and m.get("success") and m.get("arquivo"):
                        p = Path(m["arquivo"])
                        if not p.is_absolute():
                            p = pdir / p
                        if p.exists():
                            return str(p)
        except Exception:
            pass

    # 4. Checa pasta midias do projeto
    midias_dir = pdir / "midias"
    if midias_dir.exists():
        for f in midias_dir.iterdir():
            if f.is_file() and (f.name.startswith(f"{sid_int:03d}_") or f.name.startswith(f"{scene_id}_") or f.name.startswith(f"{sid_int:03d}.")):
                return str(f)
    return ""


@app.route("/api/flow/thumb/<int:scene_id>")
def flow_thumb(scene_id):
    projeto_id = request.args.get("projeto", "")
    arquivo = _arquivo_midia_cena(projeto_id, scene_id)
    if not arquivo or not Path(arquivo).exists():
        return jsonify({"success": False, "error": "Mídia não encontrada"}), 404
    return send_file(arquivo)


@app.route("/api/cena/<projeto_id>/<int:scene_id>/thumbnail")
def api_cena_thumbnail(projeto_id: str, scene_id: int):
    """Serve a thumbnail da mídia de uma cena assim que disponível em disco.

    Imagem -> serve o arquivo. Vídeo -> extrai um frame com ffmpeg (cacheado).
    """
    arquivo = _arquivo_midia_cena(projeto_id, scene_id)
    if not arquivo or not Path(arquivo).exists():
        return jsonify({"success": False, "error": "mídia ainda não disponível"}), 404
    ext = Path(arquivo).suffix.lower()
    if ext in IMAGEM_EXT:
        mime = "image/png" if ext in (".png", ".webp") else "image/jpeg"
        return send_file(arquivo, mimetype=mime)
    # Vídeo: gera/usa frame thumbnail cacheado
    thumb_dir = ASSETS_CACHE_DIR / f"scene_{scene_id}"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb = thumb_dir / "_thumbnail.jpg"
    if not thumb.exists():
        from config import FFMPEG_PATH
        import subprocess as _sp
        try:
            _sp.run([FFMPEG_PATH, "-y", "-v", "error", "-ss", "0.5", "-i", arquivo,
                     "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(thumb)],
                    capture_output=True, timeout=30)
        except Exception:
            pass
    if thumb.exists():
        return send_file(str(thumb), mimetype="image/jpeg")
    return jsonify({"success": False, "error": "thumbnail indisponível"}), 404


@app.route("/api/cena_media/<projeto_id>/<int:scene_id>")
@app.route("/api/cena/<projeto_id>/<int:scene_id>/media")
@app.route("/api/v2/cena_media/<projeto_id>/<int:scene_id>")
def api_cena_media(projeto_id: str, scene_id: int):
    """Serve a mídia original (imagem ou vídeo) para visualização ou download."""
    download = request.args.get("download", "0") in ("1", "true", "True")
    arquivo = _arquivo_midia_cena(projeto_id, scene_id)
    if not arquivo or not Path(arquivo).exists():
        return jsonify({"success": False, "error": "Mídia não encontrada"}), 404
    ext = Path(arquivo).suffix.lower()
    mimetypes = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".mp4": "video/mp4", ".webm": "video/webm",
        ".mov": "video/quicktime",
    }
    mime = mimetypes.get(ext, "application/octet-stream")
    return send_file(
        arquivo,
        mimetype=mime,
        as_attachment=download,
        download_name=Path(arquivo).name,
        conditional=True,
        max_age=3600
    )


@app.route("/api/cena/<projeto_id>/<int:scene_id>/excluir_midia", methods=["POST"])
@require_auth
def api_cena_excluir_midia(projeto_id: str, scene_id: int):
    """Exclui a mídia gerada/importada da cena e redefine o status no scene_plan."""
    arquivo = _arquivo_midia_cena(projeto_id, scene_id)
    if arquivo and Path(arquivo).exists():
        try:
            os.remove(arquivo)
        except Exception as e:
            log_event("SCENE_PLAN", f"{projeto_id}: erro ao remover {arquivo}: {e}", level="warn")

    # Limpa no scene_plan
    scene_plan_svc.atualizar_cena(projeto_id, scene_id, {
        "arquivo_midia": "",
        "status": scene_plan_svc.STATUS_PROMPT_PRONTO,
    })
    scene_plan_svc.sincronizar_midias_encontradas(projeto_id)
    return jsonify({"success": True, "scene_id": scene_id})


@app.route("/api/flow/auto_importar/<projeto_id>", methods=["POST"])
@require_auth
def api_flow_auto_importar(projeto_id: str):
    """Escaneia pasta de downloads do Flow ou pasta local e importa automaticamente."""
    data = request.get_json(force=True, silent=True) or {}
    custom_pasta = (data.get("caminho") or "").strip().strip('"').strip("'")
    
    cfg = _web_config()
    pastas_candidatas = []
    if custom_pasta and Path(custom_pasta).exists():
        pastas_candidatas.append(Path(custom_pasta))
    
    cfg_pasta = cfg.get("pasta_midia_local", "")
    if cfg_pasta and Path(cfg_pasta).exists():
        pastas_candidatas.append(Path(cfg_pasta))
        
    flow_down = BASE_DIR / "downloads" / "flow"
    if flow_down.exists():
        pastas_candidatas.append(flow_down)
    flow_down2 = BASE_DIR / "downloads"
    if flow_down2.exists() and flow_down2 not in pastas_candidatas:
        pastas_candidatas.append(flow_down2)

    plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    if not plan or not plan.get("cenas"):
        return jsonify({"success": True, "importados": 0, "mensagem": "Nenhum scene_plan ativo."})

    cenas = plan["cenas"]
    cena_ids = {str(c["id"]) for c in cenas}
    cena_por_ts = {round(float(c.get("tempo_inicio", c.get("start_time", 0)))): c["id"] for c in cenas}
    
    dest_dir = PROJETOS_DIR / projeto_id / "midias"
    dest_dir.mkdir(parents=True, exist_ok=True)

    importados = 0
    tolerancia = 3

    for pasta in pastas_candidatas:
        if not pasta.exists() or not pasta.is_dir():
            continue
        for arq in sorted(pasta.iterdir()):
            if not arq.is_file() or arq.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".webm"):
                continue
            
            sid = _extrair_cena_id(arq.name, cena_ids)
            if sid < 0:
                ts = -sid
                for t, cid in cena_por_ts.items():
                    if abs(t - ts) <= tolerancia:
                        sid = cid
                        break
            if sid <= 0:
                continue

            cena_atual = next((c for c in cenas if c["id"] == sid), None)
            if cena_atual and cena_atual.get("arquivo_midia") and Path(cena_atual["arquivo_midia"]).exists():
                continue

            ext = arq.suffix.lower()
            is_video = ext in (".mp4", ".mov", ".webm")
            dest_file = dest_dir / f"{sid:03d}_{arq.name}"
            try:
                shutil.copy2(str(arq), str(dest_file))
                new_status = scene_plan_svc.STATUS_ANIMADA if is_video else scene_plan_svc.STATUS_MIDIA_IMPORTADA
                scene_plan_svc.atualizar_cena(projeto_id, sid, {
                    "arquivo_midia": str(dest_file),
                    "status": new_status,
                })
                importados += 1
            except Exception as e:
                log_event("FLOW", f"Erro ao auto-importar {arq.name}: {e}", level="warn")

    if importados > 0:
        scene_plan_svc.sincronizar_midias_encontradas(projeto_id)
        log_event("FLOW", f"{projeto_id}: {importados} mídia(s) auto-importada(s) das pastas de monitoramento", level="info")

    plan_atualizado = scene_plan_svc.carregar_scene_plan(projeto_id)
    return jsonify({
        "success": True,
        "importados": importados,
        "total_cenas": len(cenas),
        "plan": plan_atualizado,
    })


# --- GET /api/cenas/<projeto_id> (cards com prompt/animação) --------------------

@app.route("/api/cenas/<projeto_id>")
def api_cenas(projeto_id: str):
    """Retorna as cenas detalhadas (cenas.json + storyboard + mídias) para os cards."""
    cenas = _carregar_cenas_detalhadas(projeto_id)
    total = len(cenas)
    com_midia = sum(1 for c in cenas if c["tem_midia"])
    pendentes = total - com_midia
    return jsonify({
        "success": True,
        "projeto_id": projeto_id,
        "cenas": cenas,
        "total": total,
        "com_midia": com_midia,
        "pendentes": pendentes,
    })


# --- POST /api/importar_imagens/<projeto_id> (ITEM 6) ---------------------------

@app.route("/api/importar_imagens/<projeto_id>", methods=["POST"])
def api_importar_imagens(projeto_id: str):
    """Importa imagens geradas externamente (Google Flow) e associa às cenas.

    Associação: '007_...' -> cena 7  OU  '[MM-SS]' / 'MM-SS_' -> timestamp.
    Copia para assets_cache/scene_N/ e atualiza storyboard.json + midias.
    """
    data = request.get_json(force=True, silent=True) or {}
    caminho = (data.get("caminho") or "").strip().strip('"').strip("'")
    if not caminho or not Path(caminho).exists() or not Path(caminho).is_dir():
        return jsonify({"success": False, "error": f"Pasta não encontrada: {caminho}"}), 400

    cenas = _carregar_cenas_detalhadas(projeto_id)
    cena_ids = {str(c["id"]) for c in cenas}
    cena_por_ts = {round(float(c["start"])): c["id"] for c in cenas}
    # AJUSTE 3.3: o matching cruza apenas com cenas do tipo IMAGEM do storyboard
    tipos = _tipo_media_por_cena(projeto_id)
    tolerancia = 3

    arquivos = []
    if Path(caminho).exists():
        for arq in sorted(Path(caminho).iterdir()):
            if arq.is_file() and arq.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".webm"):
                arquivos.append(arq)

    importados = 0
    puladas_video = 0
    detalhes = []
    for arq in arquivos:
        sid = _extrair_cena_id(arq.name, cena_ids)
        if sid == 0:
            continue
        if sid < 0:
            # associação por timestamp
            ts = -sid
            melhor = None
            for t, cid in cena_por_ts.items():
                if abs(t - ts) <= tolerancia:
                    melhor = cid
                    break
            if melhor is None:
                continue
            sid = melhor

        # AJUSTE 3.3: cenas marcadas como VÍDEO no storyboard aceitam mp4/mov ou foto
        is_video_arq = arq.suffix.lower() in (".mp4", ".mov", ".webm")
        if tipos.get(sid, "photo") != "photo" and not is_video_arq:
            puladas_video += 1
            continue

        destino_dir = ASSETS_CACHE_DIR / f"scene_{sid}"
        destino_dir.mkdir(parents=True, exist_ok=True)
        # ANTIGRAVITY Passo 3 (reset de cache de imagens): remove o thumbnail
        # stale de vídeo antes de copiar a nova mídia — senão o endpoint
        # /api/cena/<id>/thumbnail continuaria servindo o frame antigo.
        thumb_stale = destino_dir / "_thumbnail.jpg"
        if thumb_stale.exists():
            try:
                thumb_stale.unlink()
            except Exception:
                pass
        destino = destino_dir / f"imported_{arq.stem[:40]}{arq.suffix.lower()}"
        try:
            shutil.copy2(str(arq), str(destino))
        except Exception:
            continue
        _marcar_storyboard_imagem(projeto_id, sid, str(destino))
        _atualizar_midia_importada(projeto_id, sid, str(destino))
        importados += 1
        detalhes.append({"cena": sid, "arquivo": str(destino)})

    _log_web(projeto_id, f"Mídias importadas: {importados} de {len(arquivos)} arquivos "
                         f"({puladas_video} ignoradas por formato incompatível).",
             status="concluido")

    # Resumo
    cenas_atual = _carregar_cenas_detalhadas(projeto_id)
    pendentes = sum(1 for c in cenas_atual if not c["tem_midia"])
    msg = f"{importados} mídias importadas, {pendentes} cenas ainda pendentes"
    if puladas_video:
        msg += (f" — {puladas_video} arquivo(s) ignorado(s).")
    return jsonify({
        "success": True,
        "importadas": importados,
        "pendentes": pendentes,
        "puladas_video": puladas_video,
        "detalhes": detalhes,
        "mensagem": msg,
        "cenas": cenas_atual,
    })


# --- POST /api/flow/montar_e_exportar_capcut/<projeto_id> ----------------------

@app.route("/api/flow/montar_e_exportar_capcut/<projeto_id>", methods=["POST"])
def api_flow_montar_e_exportar_capcut(projeto_id: str):
    """Sincroniza as mídias da pasta das cenas e exporta o rascunho com timestamps para o CapCut."""
    from capcut_draft_imagens import criar_draft_imagens, detectar_pasta_drafts

    data = request.get_json(force=True, silent=True) or {}
    caminho = (data.get("caminho") or "").strip().strip('"').strip("'")

    # 1. Se informou caminho ou se a pasta padrão C:\Users\Administrator\Videos\PROJETO existe, sincroniza as mídias
    pastas_busca = []
    if caminho and Path(caminho).exists():
        pastas_busca.append(Path(caminho))

    default_videos = Path(r"C:\Users\Administrator\Videos\PROJETO")
    if default_videos.exists() and default_videos not in pastas_busca:
        pastas_busca.append(default_videos)

    proj_midias = PROJETOS_DIR / projeto_id / "midias"
    if proj_midias.exists() and proj_midias not in pastas_busca:
        pastas_busca.append(proj_midias)

    cenas = _carregar_cenas_detalhadas(projeto_id)
    cena_ids = {str(c["id"]) for c in cenas}
    cena_por_ts = {round(float(c.get("start", 0))): c["id"] for c in cenas}
    tolerancia = 3

    importados = 0
    for p_dir in pastas_busca:
        if not p_dir.is_dir():
            continue
        for arq in sorted(p_dir.iterdir()):
            if not arq.is_file() or arq.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".webm"):
                continue
            sid = _extrair_cena_id(arq.name, cena_ids)
            if sid < 0:
                ts = -sid
                for t, cid in cena_por_ts.items():
                    if abs(t - ts) <= tolerancia:
                        sid = cid
                        break
            if sid <= 0:
                continue

            destino_dir = ASSETS_CACHE_DIR / f"scene_{sid}"
            destino_dir.mkdir(parents=True, exist_ok=True)
            destino = destino_dir / f"imported_{arq.stem[:40]}{arq.suffix.lower()}"
            try:
                shutil.copy2(str(arq), str(destino))
            except Exception:
                continue
            _marcar_storyboard_imagem(projeto_id, sid, str(destino))
            _atualizar_midia_importada(projeto_id, sid, str(destino))
            importados += 1

    # 2. Recarrega as cenas sincronizadas
    cenas_atual = _carregar_cenas_detalhadas(projeto_id)
    if not cenas_atual:
        return jsonify({"success": False, "error": "Projeto sem cenas para exportar"}), 400

    # 3. Detecta pasta do CapCut
    pasta_capcut = _web_config().get("pasta_capcut", "")
    if not pasta_capcut or not Path(pasta_capcut).exists():
        pasta_capcut = detectar_pasta_drafts()
    if not pasta_capcut or not Path(pasta_capcut).exists():
        pasta_capcut = str(Path.home() / r"AppData\Local\CapCut\User Data\Projects\com\lveditor\draft")
        Path(pasta_capcut).mkdir(parents=True, exist_ok=True)

    meta = _meta(projeto_id)
    audio = meta.get("arquivo_audio", "")
    if not audio or not Path(audio).exists():
        audio = _audio_do_projeto(projeto_id)

    # PADRÃO LIRA STUDIO v0.3.0+ (SEM QUEBRAS): normaliza nomes das mídias e
    # valida antes de exportar (as 3 fontes sincronizadas).
    validacao = {}
    try:
        from services.media_standard import sincronizar_todas_cenas, validar_pre_capcut
        sincronizar_todas_cenas(projeto_id)
        validacao = validar_pre_capcut(projeto_id)
        if not validacao.get("ok"):
            _log_web(projeto_id,
                     f"Validação pré-CapCut: {validacao.get('msg', '')} "
                     f"({validacao.get('com_media', 0)}/{validacao.get('total', 0)} com mídia). "
                     f"Exportando mesmo assim (modo sem quebra).",
                     status="andamento", level="warn")
    except Exception as e_val:
        _log_web(projeto_id, f"Aviso na validação pré-CapCut: {e_val}", level="warn")

    # 4. Constrói a lista de cenas com sincronização de timestamps
    lista = []
    for c in cenas_atual:
        ext = Path(c.get("arquivo", "")).suffix.lower()
        is_vid = ext in (".mp4", ".mov", ".webm")
        lista.append({
            "start": float(c.get("start") or 0),
            "duracao": float(c.get("duracao") or 3.0),
            "arquivo": c.get("arquivo") or None,
            "media_type": "video" if is_vid else "photo",
        })

    # 5. Cria o rascunho CapCut com os cortes no ritmo de diretor de YouTube
    result = criar_draft_imagens(
        projeto_id, lista, audio, pasta_capcut,
        nome_projeto=meta.get("display_name") or projeto_id,
    )
    if not result.get("success"):
        return jsonify({"success": False, "error": result.get("error", "Falha ao gerar rascunho CapCut")}), 400

    _log_web(projeto_id, f"Projeto montado e exportado para o CapCut com sucesso: {result['draft_dir']}", status="concluido")
    return jsonify({
        "success": True,
        "importadas": importados,
        "draft_dir": result["draft_dir"],
        "nome": result["nome"],
        "mensagem": f"Projeto montado e sincronizado com ritmo e timestamps de diretor! Rascunho '{result['nome']}' criado para o CapCut.",
        "cenas": cenas_atual,
        "validacao": validacao,
    })


# --- POST /api/v2/validar_capcut/<projeto_id> (padrão v0.3.0+) -----------------

@app.route("/api/v2/validar_capcut/<projeto_id>", methods=["POST", "GET"])
def api_v2_validar_capcut(projeto_id: str):
    """Valida o projeto ANTES de exportar para o CapCut (item 4 do padrão v0.3.0+):
    - 86 cenas / quantas têm mídia;
    - nomes padronizados {id:02d}_[{MM:SS}-{MM:SS}] e arquivos em imagens/;
    - draft_content.json paths == arquivo_midia;
    - timestamps sem sobreposição.
    """
    try:
        from services.media_standard import validar_pre_capcut, sincronizar_todas_cenas
        sincronizar_todas_cenas(projeto_id)
        validacao = validar_pre_capcut(projeto_id)
        return jsonify({"success": True, "validacao": validacao})
    except Exception as e:
        log_event("MEDIA_STANDARD", f"Erro na validação pré-CapCut de '{projeto_id}': {e}", level="error")
        return jsonify({"success": False, "error": str(e)}), 500


# --- POST /api/exportar_capcut/<projeto_id> (ITEM 7) ----------------------------

@app.route("/api/exportar_capcut/<projeto_id>", methods=["POST"])
def api_exportar_capcut(projeto_id: str):
    """Exporta o projeto para uma pasta de draft do CapCut."""
    from capcut_draft_imagens import criar_draft_imagens, detectar_pasta_drafts

    data = request.get_json(force=True, silent=True) or {}
    pasta = (data.get("pasta_capcut") or "").strip().strip('"').strip("'")
    if not pasta:
        pasta = _web_config().get("pasta_capcut", "")
    if not pasta:
        pasta = detectar_pasta_drafts()
    if not pasta or not Path(pasta).exists() or not Path(pasta).is_dir():
        return jsonify({
            "success": False,
            "precisa_caminho": True,
            "error": ("Pasta de rascunhos do CapCut não encontrada. "
                      "Informe o caminho (ex: C:\\Users\\SEU_USUARIO\\AppData\\Local\\CapCut"
                      "\\User Data\\Projects\\com\\lveditor\\draft)."),
        }), 400

    # Salva o caminho para as próximas vezes
    cfg = _web_config()
    cfg["pasta_capcut"] = pasta
    _salvar_web_config(cfg)

    cenas = _carregar_cenas_detalhadas(projeto_id)
    if not cenas:
        return jsonify({"success": False, "error": "Projeto sem cenas para exportar"}), 400

    meta = _meta(projeto_id)
    audio = meta.get("arquivo_audio", "")
    if not audio or not Path(audio).exists():
        audio = _audio_do_projeto(projeto_id)

    lista = []
    for c in cenas:
        lista.append({
            "start": float(c["start"] or 0),
            "duracao": float(c["duracao"] or 3.0),
            "arquivo": c["arquivo"] or None,
            "media_type": "photo" if c["tipo_midia"] == "image_prompt" else "video",
        })

    result = criar_draft_imagens(
        projeto_id, lista, audio, pasta,
        nome_projeto=meta.get("display_name") or projeto_id,
    )
    if not result.get("success"):
        return jsonify({"success": False, "error": result.get("error", "Falha ao exportar")}), 400

    _log_web(projeto_id, f"Projeto exportado para o CapCut: {result['draft_dir']}",
             status="concluido")
    return jsonify({
        "success": True,
        "draft_dir": result["draft_dir"],
        "nome": result["nome"],
        "mensagem": (f"✅ Projeto enviado ao CapCut! Abra o CapCut e procure "
                     f"'{result['nome']}' na lista de rascunhos."),
    })


# --- POST /api/upload_audio/<projeto_id> (suporte — Card 1 manual) -------------

@app.route("/api/upload_audio/<projeto_id>", methods=["POST"])
def api_upload_audio(projeto_id: str):
    """Anexa/substitui o áudio do projeto (Card 1 manual / painel do automático)."""
    arquivo = request.files.get("audio")
    if not arquivo or not arquivo.filename:
        return jsonify({"success": False, "error": "Arquivo de áudio obrigatório"}), 400
    if not projeto_id or projeto_id == "null":
        return jsonify({"success": False, "error": "projeto_id inválido ou ausente"}), 400
    try:
        project_dir = PROJETOS_DIR / projeto_id
        project_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(arquivo.filename).suffix or ".mp3"
        audio_path = project_dir / f"{projeto_id}{ext}"
        arquivo.save(str(audio_path))
        meta = _meta(projeto_id)
        meta["arquivo_audio"] = str(audio_path)
        meta["transcricao_completa"] = False
        meta.pop("fonte_transcricao", None)
        meta.setdefault("steps", {}).pop("transcrever", None)
        # Projetos legados (criados antes do AJUSTE 2) podem não ter modo_execucao.
        # Convenção do resto do sistema (/api/projetos e app.js): ausência = "automatico".
        meta["modo_execucao"] = meta.get("modo_execucao") or "automatico"
        _set_meta(projeto_id, meta)
        _set_web_state(projeto_id, etapa="transcrever", status="andamento",
                       mensagem="Transcrevendo áudio...")
        _iniciar_thread(projeto_id, "transcricao", _thread_transcrever,
                        projeto_id, str(audio_path))
        # AJUSTE 2: no modo AUTOMÁTICO o pipeline completo só inicia após o áudio
        # ser anexado (a criação não pede mais áudio).
        if meta["modo_execucao"] == "automatico":
            _iniciar_thread(projeto_id, "auto_flow", _fluxo_automatico, projeto_id)
        return jsonify({"success": True, "status": "transcrevendo",
                        "arquivo": str(audio_path)})
    except Exception as e:
        return jsonify({"success": False, "error": f"Falha ao salvar áudio: {str(e)}"}), 500


# --- GET/POST /api/config (suporte — pastas padrão globais) ----------------------

@app.route("/api/config", methods=["GET"])
def api_get_config():
    cfg = _web_config()
    # AJUSTE 1: chaves SEMPRE mascaradas — a chave completa nunca vai ao frontend.
    for nome in _CAMPOS_CHAVE:
        cfg[f"has_{nome}_key"] = bool(_chave_efetiva(nome))
        cfg[f"{nome}_key_mascarada"] = _mascarar_chave(_chave_efetiva(nome))
    return jsonify({"success": True, **cfg})


@app.route("/api/config", methods=["POST"])
def api_set_config():
    data = request.get_json(force=True, silent=True) or {}
    cfg = _web_config()
    if data.get("pasta_midia_padrao"):
        cfg["pasta_midia_padrao"] = data["pasta_midia_padrao"].strip()
    if data.get("pasta_destino"):
        cfg["pasta_destino"] = data["pasta_destino"].strip()
    if data.get("pasta_capcut"):
        cfg["pasta_capcut"] = data["pasta_capcut"].strip()
    _salvar_web_config(cfg)

    # AJUSTE 1: chaves de API em web_keys.json (fora do Git). Só salva quando o
    # usuário digita uma chave NOVA (vazia ou placeholder mascarado → mantém).
    campos_chave = {
        "claude_api_key": "claude",
        "deepseek_api_key": "deepseek",
        "pexels_api_key": "pexels",
        "pixabay_api_key": "pixabay",
        "unsplash_api_key": "unsplash",
    }
    novas = {}
    for campo, nome in campos_chave.items():
        valor = (data.get(campo) or "").strip()
        if not valor or valor == _mascarar_chave(_chave_efetiva(nome)):
            continue
        novas[nome] = valor
    if novas:
        try:
            keys = _chaves_api()
            keys.update(novas)
            WEB_KEYS_FILE.write_text(
                json.dumps(keys, indent=2, ensure_ascii=False), encoding="utf-8")
            _aplicar_chaves_api()
            log_event("WEB",
                      "Chaves de API atualizadas pela UI: " + ", ".join(sorted(novas)),
                      level="info")
        except Exception as e:  # noqa: BLE001
            return jsonify({"success": False,
                            "error": f"Falha ao salvar chaves: {e}"}), 500

    # NUNCA devolve a chave completa — apenas os campos mascarados.
    cfg = _web_config()
    for nome in _CAMPOS_CHAVE:
        cfg[f"has_{nome}_key"] = bool(_chave_efetiva(nome))
        cfg[f"{nome}_key_mascarada"] = _mascarar_chave(_chave_efetiva(nome))
    return jsonify({"success": True, **cfg})


# ---------------------------------------------------------------------------
# Lira Flow (SSE, Web UI callbacks)
# ---------------------------------------------------------------------------
# Lira Flow (SSE, Web UI callbacks)
# ---------------------------------------------------------------------------
@app.route("/api/flow/events")
def flow_events():
    def stream():
        q = queue.Queue()
        _FLOW_QUEUES.append(q)
        try:
            # Envia ping inicial de conexão
            yield f"data: {json.dumps({'type': 'LIRA_PING', 'ts': time.time()})}\n\n"

            # Registra estado global de conexão
            est_global = _FLOW_STATE.setdefault("global", {})
            est_global["conectado"] = True
            est_global["ultimo_ping"] = time.time()

            # Envia jobs pendentes de todos os projetos ativos
            projetos_para_checar = set(_FLOW_STATE.keys())
            try:
                for d in PROJETOS_DIR.iterdir():
                    if d.is_dir() and (d / "lira_scene_plan.json").exists():
                        projetos_para_checar.add(d.name)
            except Exception:
                pass

            for proj_id in projetos_para_checar:
                if not proj_id or proj_id == "global":
                    continue
                est = _FLOW_STATE.setdefault(proj_id, {"conectado": True, "ultimo_ping": time.time()})
                if not est.get("fila_parada"):
                    plan = scene_plan_svc.carregar_scene_plan(proj_id)
                    if plan and plan.get("cenas"):
                        for cena in plan["cenas"]:
                            st = cena.get("status")
                            if st in (scene_plan_svc.STATUS_ENVIADA, scene_plan_svc.STATUS_GERANDO, scene_plan_svc.STATUS_PRONTA_PARA_ANIMAR) and not cena.get("arquivo_midia"):
                                cid = int(cena["id"])
                                is_anim = st == scene_plan_svc.STATUS_PRONTA_PARA_ANIMAR or cena.get("tipo") == "video"
                                prompt = cena.get("prompt_animacao") if is_anim else (cena.get("prompt_imagem") or cena.get("texto", ""))
                                if not prompt:
                                    continue
                                job_id = f"job-{'anim-' if is_anim else ''}{proj_id}-{cid}-{int(time.time()*1000)}"
                                msg = {
                                    "type": "LIRA_FLOW_JOB",
                                    "jobId": job_id,
                                    "projetoId": proj_id,
                                    "sceneId": cid,
                                    "prompts": [prompt],
                                    "videoMode": is_anim
                                }
                                yield f"data: {json.dumps(msg)}\n\n"

            while True:
                msg = q.get()
                yield f"data: {json.dumps(msg)}\n\n"
        except GeneratorExit:
            if q in _FLOW_QUEUES:
                _FLOW_QUEUES.remove(q)
    resp = Response(stream_with_context(stream()), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


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


@app.route("/api/flow/enqueue/<projeto_id>", methods=["POST"])
@require_auth
def flow_enqueue(projeto_id: str, data=None):
    if data is None:
        data = request.get_json(force=True, silent=True) or {}
    scenes = data.get("scene_ids") or data.get("scenes") or []
    if isinstance(scenes, (int, str)):
        scenes = [int(scenes)]
    modo = data.get("modo", "imagem")  # "imagem" | "animacao"

    plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    if not plan or not plan.get("cenas"):
        return jsonify({"success": False, "error": "scene_plan não encontrado"}), 400

    est = _FLOW_STATE.setdefault(projeto_id, {})
    if est.get("fila_parada"):
        return jsonify({"success": False,
                        "error": "Fila parada — clique em 'Parar fila' novamente para retomar."}), 409

    target_ids = [int(x) for x in scenes if str(x).isdigit()] if scenes else None
    
    # Atualiza status inicial das cenas selecionadas
    enviados = 0
    for cena in plan.get("cenas", []):
        cid = int(cena.get("id", 0))
        if target_ids is None or cid in target_ids:
            new_st = scene_plan_svc.STATUS_PRONTA_PARA_ANIMAR if modo == "animacao" else scene_plan_svc.STATUS_ENVIADA
            scene_plan_svc.atualizar_status_cena(projeto_id, cid, new_st)
            enviados += 1

    from services.playwright_flow import ensure_chrome_cdp, FlowQueueWorker
    success, msg = ensure_chrome_cdp(9222)
    if not success:
        return jsonify({"success": False, "error": f"Chrome CDP falhou: {msg}"}), 500
    worker = FlowQueueWorker
    worker.start_worker(projeto_id, target_ids, modo=modo)

    log_event("FLOW", f"{enviados} cena(s) enviadas para a fila Playwright (projeto={projeto_id}, modo={modo})", level="info")
    return jsonify({"success": True, "enviados": enviados})


@app.route("/api/flow/enqueue_anim/<projeto_id>", methods=["POST"])
@require_auth
def flow_enqueue_anim(projeto_id: str):
    # Atalho para enqueue em modo animação (repassa scene_ids/modo)
    data = request.get_json(force=True, silent=True) or {}
    data.setdefault("modo", "animacao")
    return flow_enqueue(projeto_id, data)


@app.route("/api/flow/job-progress", methods=["POST"])
def flow_job_progress():
    """Recebe updates de progresso em tempo real."""
    data = request.get_json(force=True, silent=True) or {}
    projeto_id = data.get("projetoId", "")
    scene_id = data.get("sceneId")
    status = data.get("status", "")
    mensagem = data.get("mensagem", "")
    err = data.get("error", "")

    if projeto_id:
        est = _FLOW_STATE.setdefault(projeto_id, {})
        est["ultimo_ping"] = time.time()
        est["conectado"] = True
        est["cena_ativa"] = {
            "scene_id": scene_id,
            "status": status,
            "mensagem": mensagem,
            "ts": time.time()
        }
        if scene_id:
            try:
                sid = int(scene_id)
                campos = {}
                if status in scene_plan_svc.STATUS_VALIDOS:
                    campos["status"] = status
                if err:
                    campos["erro_msg"] = str(err)
                if campos:
                    scene_plan_svc.atualizar_cena(projeto_id, sid, campos)
            except Exception:
                pass
        log_event("FLOW", f"Cena {scene_id} progresso: {status} ({mensagem})", level="info")

    return jsonify({"success": True})


@app.route("/api/flow/job-result", methods=["POST"])
def flow_job_result():
    data = request.get_json(force=True, silent=True) or {}
    job_id = data.get("jobId", "")
    scene_id = data.get("sceneId")
    projeto_id = data.get("projetoId")
    res = data.get("result", {})

    log_event("FLOW", f"Resultado do Job {job_id} recebido (projeto={projeto_id}, cena={scene_id})", level="info",
              details={"res_keys": list(res.keys())})

    _FLOW_RESULTS[job_id] = res

    if projeto_id:
        est = _FLOW_STATE.setdefault(projeto_id, {})
        est["ultimo_ping"] = time.time()
        est["conectado"] = True
        est["cena_ativa"] = {
            "scene_id": scene_id,
            "status": "CONCLUIDO" if not res.get("error") else "ERRO",
            "mensagem": f"Cena {scene_id} concluída!" if not res.get("error") else str(res.get("error")),
            "ts": time.time()
        }

    # Caso de erro
    if res.get("error") and scene_id and projeto_id:
        scene_plan_svc.atualizar_cena(projeto_id, int(scene_id), {
            "status": scene_plan_svc.STATUS_ERRO,
            "erro_msg": str(res.get("error"))
        })
        log_event("FLOW", f"Cena {scene_id} falhou: {res.get('error')}", level="warn")
        return jsonify({"success": True})

    if "files" in res and len(res["files"]) > 0 and scene_id and projeto_id:
        scene_id = int(scene_id)
        is_anim = "anim" in str(job_id).lower() or res.get("videoMode", False)

        # FASE 3.2 — recupera metadados da cena no scene_plan (fonte única) p/ delegar
        # TODO a escrita para salvar_midia_cena_estruturada(). Nenhum caminho paralelo.
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        cena_data = {}
        if plan and plan.get("cenas"):
            for c in plan["cenas"]:
                if int(c.get("id", -1)) == scene_id:
                    cena_data = c
                    break

        ts_ini = float(cena_data.get("tempo_inicio") or 0.0)
        ts_fim = float(cena_data.get("tempo_fim")
                       or (ts_ini + float(cena_data.get("duracao") or 5.0)))
        prompt_texto = (cena_data.get("prompt_animacao") if is_anim
                        else cena_data.get("prompt_imagem")) or cena_data.get("texto") or ""
        personagem_ref = cena_data.get("character_ref") or cena_data.get("personagem_ref") or ""
        modelo_usado = cena_data.get("modelo") or res.get("model", "") or ""

        import base64
        file_saved = False
        erro_salvar = ""
        for file_obj in res["files"]:
            data_url = file_obj.get("dataUrl", "")
            if "," not in data_url:
                continue
            try:
                midia_bytes = base64.b64decode(data_url.split(",", 1)[1])
            except Exception as e:
                erro_salvar = f"erro ao decodificar base64: {e}"
                log_event("FLOW", f"Erro ao decodificar mídia da cena {scene_id}: {e}", level="error")
                continue

            # Escrita CANÔNICA (valida, grava, atualiza scene_plan/storyboard/galeria)
            r = scene_plan_svc.salvar_midia_cena_estruturada(
                projeto_id=projeto_id,
                cid=scene_id,
                ts_ini=ts_ini,
                ts_fim=ts_fim,
                prompt_texto=prompt_texto,
                midia_bytes=midia_bytes,
                is_video=is_anim,
                modelo_usado=modelo_usado,
                personagem_ref=personagem_ref,
            )
            if not r.get("success", False):
                erro_salvar = r.get("error", "mídia inválida")
                log_event("FLOW", f"Validação de mídia falhou p/ cena {scene_id}: {erro_salvar}", level="error")
                # status=ERRO já setado dentro de salvar_midia_cena_estruturada
                continue

            fname = r.get("arquivo_nome", "")
            new_status = scene_plan_svc.STATUS_ANIMADA if is_anim else scene_plan_svc.STATUS_MIDIA_IMPORTADA
            scene_plan_svc.atualizar_cena(projeto_id, scene_id, {
                "arquivo_midia": r["arquivo_path"],
                "status": new_status,
                "erro_msg": ""
            })
            scene_plan_svc.sincronizar_midias_encontradas(projeto_id)
            log_event("FLOW", f"Cena {scene_id} mídia salva via salvar_midia_cena_estruturada: {fname} (status={new_status})", level="info")
            file_saved = True
            break

        if not file_saved:
            scene_plan_svc.atualizar_cena(projeto_id, scene_id, {
                "status": scene_plan_svc.STATUS_ERRO,
                "erro_msg": erro_salvar or "nenhum arquivo de mídia válido recebido"
            })
    else:
        log_event("FLOW", f"Job {job_id} sem arquivos salvos (files presente: {'files' in res}, scene_id: {scene_id})", level="warn")

    return jsonify({"success": True})


@app.route("/api/v2/projeto/<projeto_id>/galeria", methods=["GET"])
@require_auth
def api_v2_obter_galeria(projeto_id: str):
    """Retorna a galeria central de mídias do projeto (galeria.json)."""
    gal = scene_plan_svc.carregar_galeria(projeto_id)
    return jsonify({"success": True, "galeria": gal})


@app.route("/api/v2/projeto/<projeto_id>/storyboard", methods=["GET"])
@require_auth
def api_v2_obter_storyboard(projeto_id: str):
    """Retorna o storyboard oficial do projeto (storyboard.json)."""
    sb = scene_plan_svc.carregar_storyboard(projeto_id)
    return jsonify({"success": True, "storyboard": sb})


@app.route("/api/v2/projeto/<projeto_id>/indexar_midias", methods=["POST"])
@require_auth
def api_v2_indexar_midias(projeto_id: str):
    """Executa a indexação e varredura de mídias de todas as pastas do projeto."""
    res = scene_plan_svc.indexar_midias_projeto(projeto_id)
    return jsonify(res)



@app.route("/api/flow/selector-log", methods=["POST"])
def flow_selector_log():
    data = request.get_json(force=True, silent=True) or {}
    est = _FLOW_STATE.setdefault("", {})
    est["ultimo_ping"] = time.time()
    log_event("FLOW_SELECTOR", str(data.get("message", "")), level="info")
    return jsonify({"success": True})


@app.route("/api/v2/log", methods=["POST"])
def api_v2_log():
    """Registra evento de navegação/UI da web no console (fila de eventos).

    Categoria fixa NAVEGACAO, isolado por projeto (details.projeto) para aparecer
    no polling do projeto ativo. Consistente com os demais logs da UI (log_event).
    """
    data = request.get_json(force=True, silent=True) or {}
    projeto_id = str(data.get("projeto_id", "") or "")
    mensagem = str(data.get("message", "") or "")
    level = str(data.get("level", "info") or "info")
    details = {"projeto": projeto_id}
    status = str(data.get("status", "andamento") or "andamento")
    if status:
        details["status"] = status
    log_event("NAVEGACAO", mensagem, level=level, details=details)
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Conexão com o Flow + fila (ETAPA 3 — HUB DE PRODUÇÃO)
# ---------------------------------------------------------------------------

def _flow_accounts_path() -> Path:
    """Caminho do arquivo de contas do Google Flow (config/flow_accounts.json)."""
    return Path("config/flow_accounts.json")


def _ler_flow_accounts() -> dict:
    """Lê config/flow_accounts.json (lista de contas). Nunca lança exceção."""
    p = _flow_accounts_path()
    if not p.exists():
        return {"contas": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        log_event("FLOW", f"Erro ao ler flow_accounts.json: {e}", level="error")
        return {"contas": []}


def _salvar_flow_accounts(accounts: dict) -> bool:
    """Salva config/flow_accounts.json atomicamente. Retorna True em sucesso."""
    p = _flow_accounts_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        log_event("FLOW", f"Erro ao salvar flow_accounts.json: {e}", level="error")
        return False


def _ativar_conta_flow(conta_id) -> Optional[dict]:
    """Ativa a conta `conta_id` (desativa as demais) e persiste.

    Retorna a conta ativada (dict) ou None se a conta não existir.
    """
    accounts = _ler_flow_accounts()
    contas = accounts.get("contas", [])
    alvo = None
    for c in contas:
        if int(c.get("id", 0)) == int(conta_id):
            c["ativa"] = True
            alvo = c
        else:
            c["ativa"] = False
    if alvo is None:
        return None
    _salvar_flow_accounts(accounts)
    log_event("FLOW", f"Conta ativa alterada para: {alvo.get('nome')} (id={conta_id})", level="info")
    return alvo


@app.route("/api/flow/abrir", methods=["POST"])
@require_auth
def flow_abrir():
    data = request.get_json(force=True, silent=True) or {}
    projeto_id = str(data.get("projeto_id", ""))
    conta_id = data.get("conta_id")
    if conta_id is not None:
        # Usa a conta selecionada no dropdown (ativa antes de abrir o Chrome)
        _ativar_conta_flow(conta_id)
    from services.playwright_flow import FlowSessionManager
    ok, msg = FlowSessionManager.start_session(projeto_id)
    est = _FLOW_STATE.setdefault(projeto_id, {})
    est["conectado"] = ok
    est["conta"] = "Google Chrome (Automação Playwright/CDP)"
    est["ultimo_ping"] = time.time()
    est["fila_parada"] = False
    log_event("FLOW", f"Conexão CDP: {msg} (projeto={projeto_id})", level="info" if ok else "error")
    if ok:
        log_event("PLAYWRIGHT_FLOW", f"{projeto_id}: CDP conectado, projeto Flow pronto")
    return jsonify({"success": ok, "conectado": ok, "conta": est["conta"], "message": msg})


@app.route("/api/flow/status")
@require_auth
def flow_status():
    projeto_id = request.args.get("projeto_id", "")
    from services.playwright_flow import FlowSessionManager, FlowQueueWorker
    est = _FLOW_STATE.get(projeto_id, {})
    conectado = FlowSessionManager.is_active()
    qstatus = FlowQueueWorker.get_status()
    prog = scene_plan_svc.progresso_scene_plan(projeto_id)
    por = prog.get("por_status", {})
    contadores = {
        "pendentes": por.get(scene_plan_svc.STATUS_PENDENTE, 0),
        "enviadas":  por.get(scene_plan_svc.STATUS_ENVIADA, 0) + por.get(scene_plan_svc.STATUS_PROMPT_PRONTO, 0),
        "gerando":   por.get(scene_plan_svc.STATUS_GERANDO, 0) + por.get(scene_plan_svc.STATUS_PRONTA_PARA_ANIMAR, 0),
        "prontos":   prog.get("prontas", 0),
        "erros":     por.get(scene_plan_svc.STATUS_ERRO, 0),
    }
    return jsonify({
        "success": True, "projeto_id": projeto_id, "conectado": conectado,
        "conta": est.get("conta", "") if conectado else "",
        "fila_parada": bool(est.get("fila_parada")),
        "worker_rodando": qstatus.get("rodando_fila", False),
        "contadores": contadores, "progresso": prog,
        "cena_ativa": qstatus.get("cena_ativa"),
    })


@app.route("/api/flow/desconectar", methods=["POST"])
@require_auth
def flow_desconectar():
    data = request.get_json(force=True, silent=True) or {}
    projeto_id = str(data.get("projeto_id", ""))
    from services.playwright_flow import FlowSessionManager, FlowQueueWorker
    FlowQueueWorker.stop_worker()
    FlowSessionManager.close_session()
    est = _FLOW_STATE.setdefault(projeto_id, {})
    est["conectado"] = False
    est["conta"] = ""
    return jsonify({"success": True, "conectado": False})


@app.route("/api/flow/reconectar", methods=["POST"])
@require_auth
def flow_reconectar():
    """Reconexão MANUAL da aba do Google Flow à URL salva do projeto
    (mesma lógica do início da produção, sem processar a fila)."""
    data = request.get_json(force=True, silent=True) or {}
    projeto_id = str(data.get("projeto_id", "")).strip()
    if not projeto_id:
        return jsonify({"success": False, "conectado": False,
                        "message": "projeto_id é obrigatório."}), 400
    if not (PROJETOS_DIR / projeto_id).exists():
        return jsonify({"success": False, "conectado": False,
                        "message": f"Projeto '{projeto_id}' não encontrado."}), 400

    from services.playwright_flow import (
        FlowSessionManager, carregar_projeto_flow_url,
    )
    ok, msg = FlowSessionManager.reconectar(projeto_id)
    est = _FLOW_STATE.setdefault(projeto_id, {})
    est["conectado"] = bool(ok)
    est["ultimo_ping"] = time.time()
    log_event("FLOW", f"Reconexão manual: {msg} (projeto={projeto_id})",
              level="info" if ok else "warn")
    return jsonify({
        "success": bool(ok),
        "conectado": bool(ok),
        "message": msg,
        "flow_url": carregar_projeto_flow_url(projeto_id) or "",
    })


@app.route("/api/flow/contas", methods=["GET"])
@require_auth
def flow_contas():
    """Retorna a lista de contas do config/flow_accounts.json."""
    accounts = _ler_flow_accounts()
    contas = accounts.get("contas", [])
    return jsonify({
        "success": True,
        "contas": contas,
        "ativa_id": next((c.get("id") for c in contas if c.get("ativa")), None),
    })


@app.route("/api/flow/contas/ativar", methods=["POST"])
@require_auth
def flow_contas_ativar():
    """Define uma conta como ativa (ativa=true) e as demais como ativa=false."""
    data = request.get_json(force=True, silent=True) or {}
    conta_id = data.get("conta_id")
    if conta_id is None:
        return jsonify({"success": False, "error": "conta_id é obrigatório"}), 400
    alvo = _ativar_conta_flow(conta_id)
    if alvo is None:
        return jsonify({"success": False, "error": f"Conta '{conta_id}' não encontrada"}), 404
    return jsonify({"success": True, "conta": alvo})


@app.route("/api/flow/contas/login_guiado", methods=["POST"])
@require_auth
def flow_contas_login_guiado():
    """Abre o Chrome com o perfil da conta selecionada, aguarda 30s para login
    manual e extrai o email via _extrair_email_via_painel_conta()."""
    data = request.get_json(force=True, silent=True) or {}
    conta_id = data.get("conta_id")
    if conta_id is None:
        return jsonify({"success": False, "error": "conta_id é obrigatório"}), 400
    accounts = _ler_flow_accounts()
    contas = accounts.get("contas", [])
    conta = next((c for c in contas if int(c.get("id", 0)) == int(conta_id)), None)
    if conta is None:
        return jsonify({"success": False, "error": f"Conta '{conta_id}' não encontrada"}), 404

    # Ativa a conta selecionada (ensure_chrome_cdp lê a conta ativa p/ profile_dir)
    for c in contas:
        c["ativa"] = (int(c.get("id", 0)) == int(conta_id))
    _salvar_flow_accounts(accounts)

    from services.playwright_flow import ensure_chrome_cdp, FlowQueueWorker
    ok, msg = ensure_chrome_cdp(9222, force_restart=True)
    if not ok:
        return jsonify({"success": False, "error": msg}), 500

    log_event("FLOW", f"Login guiado: Chrome aberto com o perfil da conta "
                      f"'{conta.get('nome')}' (id={conta_id}). Faça o login manualmente na janela do Chrome.",
              level="info")
    time.sleep(30)

    email = None
    worker = FlowQueueWorker.get_worker()
    if not worker.is_running_queue:
        try:
            ok_sess, msg_sess = worker._iniciar_sessao_thread()
            if ok_sess:
                email = worker._extrair_email_via_painel_conta()
                worker._encerrar_sessao()
        except Exception as e:
            log_event("FLOW", f"Falha ao extrair email no login guiado: {e}", level="warn")
    else:
        log_event("FLOW", "Fila de produção em execução — extração de email adiada.", level="warn")

    if email:
        conta["email"] = email
        _salvar_flow_accounts(accounts)
        log_event("FLOW", f"Email capturado para a conta '{conta.get('nome')}': {email}", level="info")

    return jsonify({"success": True, "email": email, "conta": conta, "message": msg})


@app.route("/api/flow/fila/parar", methods=["POST"])
@require_auth
def flow_fila_parar():
    """Alterna parar/retomar a fila de envio no worker."""
    data = request.get_json(force=True, silent=True) or {}
    projeto_id = str(data.get("projeto_id", ""))
    est = _FLOW_STATE.setdefault(projeto_id, {})
    
    from services.playwright_flow import ensure_chrome_cdp, FlowQueueWorker
    worker = FlowQueueWorker
    
    est["fila_parada"] = not bool(est.get("fila_parada"))
    if est["fila_parada"]:
        worker.stop_queue()
    else:
        success, msg = ensure_chrome_cdp(9222)
        if not success:
            est["fila_parada"] = True  # reverte: não conseguiu retomar a fila
            return jsonify({"success": False, "error": f"Chrome CDP falhou: {msg}"}), 500
        worker.start_worker(projeto_id)
        
    log_event("FLOW", f"Fila {'parada' if est['fila_parada'] else 'retomada'} (projeto={projeto_id})",
              level="info")
    return jsonify({"success": True, "fila_parada": est["fila_parada"]})


@app.route("/api/flow/fila/limpar", methods=["POST"])
@require_auth
def flow_fila_limpar():
    """Limpa a fila: zera contadores e devolve status de fila ao PENDENTE."""
    data = request.get_json(force=True, silent=True) or {}
    projeto_id = str(data.get("projeto_id", ""))
    
    from services.playwright_flow import FlowQueueWorker
    FlowQueueWorker.stop_queue()
    
    est = _FLOW_STATE.setdefault(projeto_id, {})
    est["fila_parada"] = False
    est["contadores"] = {"pendentes": 0, "gerando": 0, "prontos": 0, "erros": 0}
    
    plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    if plan and plan.get("cenas"):
        for c in plan["cenas"]:
            if c.get("status") in (scene_plan_svc.STATUS_PROMPT_PRONTO,
                                   scene_plan_svc.STATUS_ENVIADA,
                                   scene_plan_svc.STATUS_GERANDO,
                                   scene_plan_svc.STATUS_PRONTA_PARA_ANIMAR):
                scene_plan_svc.atualizar_status_cena(projeto_id, c["id"],
                                                      scene_plan_svc.STATUS_PENDENTE)
    log_event("FLOW", f"Fila limpa (projeto={projeto_id})", level="info")
    return jsonify({"success": True})



@app.route("/api/scene_plan/<projeto_id>", methods=["GET"])
@require_auth
def get_scene_plan(projeto_id: str):
    plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    if not plan or not plan.get("cenas"):
        _garantir_cenas_json(projeto_id)
        scene_plan_svc.gerar_scene_plan(projeto_id, force=True)
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    return jsonify({"success": True, "plan": plan or {}})


@app.route("/api/scene_plan/<projeto_id>/<int:scene_id>", methods=["PATCH"])
@require_auth
def patch_scene_plan_cena(projeto_id: str, scene_id: int):
    data = request.get_json(force=True, silent=True) or {}
    res = scene_plan_svc.atualizar_cena(projeto_id, scene_id, data)
    if not res.get("success"):
        return jsonify(res), 400
    return jsonify(res)


@app.route("/api/scene_plan/<projeto_id>/progresso", methods=["GET"])
@require_auth
def get_scene_plan_progresso(projeto_id: str):
    res = scene_plan_svc.progresso_scene_plan(projeto_id)
    return jsonify({"success": True, **res})


@app.route("/api/scene_plan/<projeto_id>/gerar_prompts", methods=["POST"])
@require_auth
def api_gerar_prompts_scene_plan(projeto_id: str):
    data = request.get_json(force=True, silent=True) or {}
    estilo_visual = data.get("estilo_visual", "photorealistic_cinematic")
    style_lock = MASTER_STYLES.get(estilo_visual, MASTER_STYLES["photorealistic_cinematic"])

    # 1. Garante cenas.json (cria a partir de roteiro_transcricao.json se necessário)
    if not _garantir_cenas_json(projeto_id):
        return jsonify({"success": False,
                        "error": "Transcrição não encontrada. Processe o áudio primeiro (Card 1)."}), 400

    # 2. Gera / recarrega o scene_plan
    plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    if not plan or not plan.get("cenas"):
        scene_plan_svc.gerar_scene_plan(projeto_id, force=True)
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)

    if not plan or not plan.get("cenas"):
        return jsonify({"success": False,
                        "error": "Não foi possível gerar o plano de cenas. Verifique a transcrição."}), 400
    nome_personagem_request = data.get("nome_personagem", "").strip()

    cenas = plan["cenas"]
    for c in cenas:
        cid = c["id"]
        texto = c.get("texto", "")
        tem_personagem = _cena_tem_personagem(texto)

        if tem_personagem:
            nome_usar = nome_personagem_request if nome_personagem_request else "personagem"
            pref = f"@{nome_usar} "
        else:
            pref = ""

        prompt_img = (
            f"{pref}{style_lock['estilo']}, {texto}, {style_lock['composicao']}"
            .strip(", ")
        )

        dur = float(c.get("duracao") or 3.0)
        if dur < 2.5:
            prompt_anim = "Slow zoom-in (1.00 -> 1.05), subtle camera shake, 2s ease"
        elif dur > 5.0:
            prompt_anim = "Slow zoom-out (1.05 -> 1.00), smooth panning left to right, 4s ease"
        else:
            prompt_anim = "Ken Burns zoom sutil (1.00 -> 1.04), 2s ease"

        scene_plan_svc.atualizar_cena(projeto_id, cid, {
            "prompt_imagem": prompt_img,
            "prompt_animacao": prompt_anim,
            "status": scene_plan_svc.STATUS_PROMPT_PRONTO,
        })

    plan_atualizado = scene_plan_svc.carregar_scene_plan(projeto_id)
    return jsonify({"success": True, "total": len(cenas), "plan": plan_atualizado})




@app.route("/api/scene_plan/<projeto_id>/<int:scene_id>/personagem", methods=["POST"])
@require_auth
def api_upload_personagem_cena(projeto_id: str, scene_id: int):
    arquivo = request.files.get("personagem") or request.files.get("file")
    if not arquivo or not arquivo.filename:
        return jsonify({"success": False, "error": "Arquivo de imagem de personagem é obrigatório"}), 400

    project_dir = PROJETOS_DIR / projeto_id / "personagens"
    project_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(arquivo.filename).suffix or ".jpg"
    dest_path = project_dir / f"{scene_id:03d}_personagem{ext}"
    arquivo.save(str(dest_path))

    res = scene_plan_svc.atualizar_cena(projeto_id, scene_id, {"personagem_ref": str(dest_path)})
    if not res.get("success"):
        return jsonify(res), 400
    return jsonify({"success": True, "personagem_ref": str(dest_path)})


@app.route("/api/scene_plan/<projeto_id>/personagem_global", methods=["POST"])
@require_auth
def api_upload_personagem_global(projeto_id: str):
    arquivo = request.files.get("personagem") or request.files.get("file")
    if not arquivo or not arquivo.filename:
        return jsonify({"success": False, "error": "Arquivo de imagem do avatar/personagem é obrigatório"}), 400

    project_dir = PROJETOS_DIR / projeto_id
    project_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(arquivo.filename).suffix or ".jpg"
    dest_path = project_dir / f"personagem_global{ext}"
    arquivo.save(str(dest_path))

    # Salva no meta.json (ambas as chaves para compatibilidade)
    meta = _meta(projeto_id)
    meta["personagem_ref_global"] = str(dest_path)
    meta["personagem_global_path"] = str(dest_path)
    _set_meta(projeto_id, meta)

    # Aplica a todas as cenas já existentes no scene_plan.json
    plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    if plan and plan.get("cenas"):
        for c in plan["cenas"]:
            scene_plan_svc.atualizar_cena(projeto_id, c["id"], {"personagem_ref": str(dest_path)})

    log_event("SCENE_PLAN", f"{projeto_id}: avatar global atualizado para {dest_path.name}", level="info")
    return jsonify({"success": True, "personagem_ref": str(dest_path)})



@app.route("/api/scene_plan/<projeto_id>/personagem_avatar")
def api_get_personagem_avatar(projeto_id: str):
    path_param = request.args.get("path", "")
    scene_id = request.args.get("scene_id")

    target_file = None
    if path_param and Path(path_param).exists():
        target_file = Path(path_param)
    elif scene_id:
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if plan and plan.get("cenas"):
            for c in plan["cenas"]:
                if str(c.get("id")) == str(scene_id) and c.get("personagem_ref"):
                    p = Path(c["personagem_ref"])
                    if p.exists():
                        target_file = p
                        break

    if not target_file:
        meta = _meta(projeto_id)
        glob_ref = meta.get("personagem_ref_global", "")
        if glob_ref and Path(glob_ref).exists():
            target_file = Path(glob_ref)
        else:
            for ext in (".jpg", ".png", ".jpeg", ".webp"):
                candidate = PROJETOS_DIR / projeto_id / f"personagem_global{ext}"
                if candidate.exists():
                    target_file = candidate
                    break

    if target_file and target_file.exists():
        from flask import send_file
        return send_file(str(target_file))

    return jsonify({"error": "Avatar não encontrado"}), 404


@app.route("/api/log/global")
def log_global():
    todos = ler_eventos(linhas=200)
    return jsonify({"success": True, "logs": [e for e in todos if "timestamp" in e]})

# ---------------------------------------------------------------------------
# Main & Encerramento Limpo
# ---------------------------------------------------------------------------

import atexit
import signal

def _clean_exit(signum=None, frame=None):
    try:
        from services.playwright_flow import FlowQueueWorker
        FlowQueueWorker.stop_worker()
    except Exception:
        pass
    if signum is not None:
        sys.exit(0)

try:
    signal.signal(signal.SIGINT, _clean_exit)
    signal.signal(signal.SIGTERM, _clean_exit)
except Exception:
    pass
atexit.register(_clean_exit)


def main():
    # Garante pastas padrão locais da web
    cfg = _web_config()
    Path(cfg["pasta_midia_padrao"]).mkdir(parents=True, exist_ok=True)
    Path(cfg["pasta_destino"]).mkdir(parents=True, exist_ok=True)
    _salvar_web_config(cfg)

    log_event("WEB", "ULTRACUT3 WEB v1.0 iniciado em http://127.0.0.1:5000", level="info")
    print("=" * 60, flush=True)
    print("  ULTRACUT3 WEB v1.0", flush=True)
    print("  http://127.0.0.1:5000", flush=True)
    print("  (apenas local — não exponha a porta 5000 externamente)", flush=True)
    print("=" * 60, flush=True)
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)


if __name__ == "__main__":
    main()
