"""
app_web.py — ULTRACUT3 WEB v1.0
================================
Interface web local unificada que CONSONE a GUI desktop (v3.8) e os módulos de
pipeline existentes (transcrição, scene_builder v4.19, broll_director, media_search
v3.6, video_builder, video_encoder) — sem reescrever a lógica interna.

Unifica em UM único fluxo:
  - Fluxo AUTOMÁTICO  (áudio -> vídeo pronto, com polling de eventos via ler_eventos)
  - Fluxo MANUAL      (4 cards: Áudio / Transcrição+Prompts / Imagens / Montar Vídeo)
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

from flask import Flask, request, jsonify, send_from_directory

# Garante que o diretório do projeto esteja no sys.path e no cwd
BASE_DIR = Path(r"C:\ultracut3")
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
os.chdir(str(BASE_DIR))

from config import PROJETOS_DIR, OUTPUT_DIR, ASSETS_CACHE_DIR  # noqa: E402
from services.event_logger import log_event, ler_eventos  # noqa: E402
from services.media_search import (  # noqa: E402
    detectar_modo_local_timestamp,
    casar_midia_por_timestamp,
)

# ---------------------------------------------------------------------------
# Constantes e configuração local da web (fora do pipeline)
# ---------------------------------------------------------------------------

WEB_CONFIG_FILE = BASE_DIR / "web_config.json"

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
# Meta / projeto helpers
# ---------------------------------------------------------------------------

def _meta(projeto: str) -> dict:
    meta_file = PROJETOS_DIR / projeto / "meta.json"
    if meta_file.exists():
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _set_meta(projeto: str, meta: dict):
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
        if details.get("projeto") == projeto:
            novos.append(evt)
        elif evt.get("category") in PIPELINE_CATEGORIES:
            novos.append(evt)
    return novos, len(todos)
# ---------------------------------------------------------------------------
# SRT manual
# ---------------------------------------------------------------------------

def _parse_srt_linha_tempo(texto: str) -> list:
    """Converte linhas '[MM:SS] texto' em segmentos."""
    segmentos = []
    for m in re.finditer(r"\[\s*(\d{1,2}):(\d{2})\s*\]\s*(.+)", texto):
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
    segmentos = _parse_srt_linha_tempo(texto)
    if not segmentos:
        segmentos = _parse_srt_standard(texto)
    return segmentos


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

    _log_web(projeto, f"Transcrição manual carregada: {len(segmentos)} segmentos",
             status="concluido", step=0)
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

def _gerar_storyboard_estrito(projeto: str, timeout: int = STORYBOARD_TIMEOUT) -> dict:
    """
    Chama a função EXISTENTE gerar_storyboard(usar_claude=True) em thread com timeout.

    Retorna:
      {"confiavel": True, "resultado": r}         -> storyboard 100% via Claude
      {"confiavel": False, "motivo": ..., "msg": ...}
        motivos: "timeout" | "erro" | "sem_chave" | "fallback_local" | "falhou"
    """
    from config import ANTHROPIC_API_KEY
    from services.broll_director import gerar_storyboard

    if not ANTHROPIC_API_KEY:
        return {"confiavel": False, "motivo": "sem_chave",
                "msg": "ANTHROPIC_API_KEY não configurada em config_local.py"}

    resultado = {}

    def _run():
        try:
            r = gerar_storyboard(projeto, usar_claude=True)
            resultado["r"] = r
        except Exception as e:  # noqa: BLE001
            resultado["erro"] = str(e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
        return {"confiavel": False, "motivo": "timeout",
                "msg": f"Storyboard via API excedeu {timeout}s de timeout"}

    if "erro" in resultado:
        return {"confiavel": False, "motivo": "erro", "msg": resultado["erro"]}

    r = resultado.get("r", {})
    if not r.get("success"):
        return {"confiavel": False, "motivo": "falhou", "msg": r.get("error", "erro desconhecido")}

    if not r.get("camada_confiavel") or r.get("local_fallback", 0) > 0 or r.get("camada") != "claude":
        return {"confiavel": False, "motivo": "fallback_local",
                "msg": "A API não gerou todas as cenas — o pipeline caiu para extração local "
                       "(fallback NÃO permitido no fluxo automático). Ativando caminho manual."}

    return {"confiavel": True, "resultado": r}


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

    # --- Fluxo via API (exatamente como está hoje: GREEN >= 0.75, anti-reuso) ---
    p = _pipeline(projeto)
    result = p.buscar_midias()

    if result.get("success"):
        meta = _meta(projeto)
        meta["origem_midia"] = "api"
        origem_por_cena = {
            str(r.get("scene_id", 0)): "api" for r in result.get("resultados", [])
        }
        meta["origem_midia_por_cena"] = origem_por_cena
        _set_meta(projeto, meta)

        _set_web_state(projeto, midia_modo="api",
                       midia_green=result.get("green", 0),
                       midia_pendentes=result.get("needs_media", 0))
        return {
            "modo": "api",
            "green": result.get("green", 0),
            "needs_media": result.get("needs_media", 0),
        }

    return {"modo": "api", "error": result.get("error", "busca falhou")}
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


def _executar_etapa_render(projeto: str) -> bool:
    _set_web_state(projeto, etapa="render", status="andamento", mensagem="Montando vídeo...")
    _log_web(projeto, "Montando vídeo...", status="andamento", step=4)
    try:
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

def _esperar_transcricao(projeto: str, timeout_seg: int = 900) -> bool:
    """Aguarda transcricao_completa ou detecta erro."""
    inicio = time.time()
    while time.time() - inicio < timeout_seg:
        meta = _meta(projeto)
        if meta.get("transcricao_completa"):
            return True
        if meta.get("steps", {}).get("transcrever", {}).get("status") == "erro":
            return False
        time.sleep(1)
    return False


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
        sb = _gerar_storyboard_estrito(projeto)
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

        # 3. Storyboard se modo API (modo local timestamp dispensa storyboard)
        pasta = _pasta_midia_projeto(projeto)
        ok_local = False
        if pasta and Path(pasta).exists():
            ok_local, _arquivos = detectar_modo_local_timestamp(pasta)
        if not ok_local and not _storyboard_confiavel_existe(projeto):
            _set_web_state(projeto, etapa="storyboard", status="andamento",
                           mensagem="Planejando cenas (Claude)...")
            sb = _gerar_storyboard_estrito(projeto)
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
    """Thread de transcrição em background (função existente do pipeline)."""
    try:
        p = _pipeline(projeto)
        result = p.transcrever(audio_path)
        if result.get("success"):
            _set_web_state(projeto, transcricao_concluida=True)
            _log_web(projeto,
                     f"Transcrição concluída: {result.get('segments', 0)} segmentos.",
                     status="concluido", step=0)
        else:
            _set_web_state(projeto, transcricao_erro=result.get("error", ""))
            _log_web(projeto, f"Transcrição falhou: {result.get('error', '')}",
                     status="erro", step=0, level="error")
    except Exception as e:  # noqa: BLE001
        _set_web_state(projeto, transcricao_erro=str(e))
        _log_web(projeto, f"Erro na transcrição: {e}", status="erro", step=0, level="error")


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
            sb = _gerar_storyboard_estrito(projeto)
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
        else:
            _executar_fluxo_pos_storyboard(projeto)
    except Exception as e:  # noqa: BLE001
        _set_web_state(projeto, etapa="erro", status="erro", erro=str(e))
        _log_web(projeto, f"Erro ao avançar etapa: {e}", status="erro", level="error")

# ---------------------------------------------------------------------------
# Flask app e rotas
# ---------------------------------------------------------------------------

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1 GB (áudio)


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# --- GET /projeto/<projeto_id> (SPA — dashboard do projeto) ------------------

@app.route("/projeto/<projeto_id>")
def projeto(projeto_id: str):
    """Rota SPA: serve o frontend; o app.js abre o dashboard do projeto pelo path."""
    return send_from_directory("static", "index.html")


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


# --- POST /api/criar_projeto ------------------------------------------------

@app.route("/api/criar_projeto", methods=["POST"])
def api_criar_projeto():
    nome = (request.form.get("nome") or "").strip()
    modo = (request.form.get("modo") or "automatico").strip()
    if not nome:
        return jsonify({"success": False, "error": "Nome do projeto é obrigatório"}), 400
    if modo not in ("automatico", "manual"):
        modo = "automatico"

    arquivo = request.files.get("audio")
    if not arquivo or not arquivo.filename:
        return jsonify({"success": False, "error": "Arquivo de áudio é obrigatório"}), 400

    from services.pipeline_service import PipelineService
    from services.video_encoder import sanitizar_nome_arquivo

    nome_projeto = sanitizar_nome_arquivo(nome)
    p = PipelineService()
    result = p.criar_projeto(nome, "")
    if not result.get("success"):
        return jsonify(result), 409

    project_dir = PROJETOS_DIR / nome_projeto
    ext = Path(arquivo.filename).suffix or ".mp3"
    audio_path = project_dir / f"{nome_projeto}{ext}"
    arquivo.save(str(audio_path))

    meta = _meta(nome_projeto)
    meta["modo_execucao"] = modo
    meta["arquivo_audio"] = str(audio_path)
    meta["origem_midia"] = None
    meta["web_event_start_idx"] = len(ler_eventos(linhas=200000))
    _set_meta(nome_projeto, meta)

    _set_web_state(nome_projeto, fluxo=modo, etapa="transcrever",
                   status="andamento", mensagem="Transcrevendo áudio...")
    _log_web(nome_projeto, f"Projeto '{nome_projeto}' criado (modo: {modo}).",
             status="andamento", step=0)

    # Transcrição SEMPRE em background (função existente)
    _iniciar_thread(nome_projeto, "transcricao", _thread_transcrever,
                    nome_projeto, str(audio_path))

    # Modo automático: fluxo completo em background (espera a transcrição)
    if modo == "automatico":
        _iniciar_thread(nome_projeto, "auto_flow", _fluxo_automatico, nome_projeto)

    return jsonify({"projeto_id": nome_projeto, "status": "transcrevendo"}), 201


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
            "error": "Nenhum segmento válido. Use SRT padrão ou linhas [MM:SS] texto.",
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
        sb = _gerar_storyboard_estrito(projeto_id)
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
        "video": video,
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
def api_download_transcricao(projeto_id: str):
    """Baixa a transcrição em formato .txt (frases [MM:SS]) ou .srt (padrão)."""
    from flask import Response
    formato = (request.args.get("formato") or "txt").strip().lower()
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


# --- GET /api/cena/<projeto_id>/<scene_id>/thumbnail (ITEM 5) ------------------

def _arquivo_midia_cena(projeto: str, scene_id) -> str:
    """Localiza o arquivo de mídia de uma cena (fonte: midias_encontradas.json).

    A fonte é ESTRITAMENTE por-projeto — o assets_cache é global e não deve vazar
    mídia de outros projetos na thumbnail.
    """
    midias_file = PROJETOS_DIR / projeto / "midias_encontradas.json"
    if midias_file.exists():
        try:
            with open(midias_file, "r", encoding="utf-8") as f:
                for m in json.load(f):
                    if str(m.get("scene_id")) == str(scene_id) and m.get("success") and m.get("arquivo"):
                        p = Path(m["arquivo"])
                        if p.exists():
                            return str(p)
        except Exception:
            pass
    return ""


@app.route("/api/cena/<projeto_id>/<int:scene_id>/thumbnail")
def api_cena_thumbnail(projeto_id: str, scene_id: int):
    """Serve a thumbnail da mídia de uma cena assim que disponível em disco.

    Imagem -> serve o arquivo. Vídeo -> extrai um frame com ffmpeg (cacheado).
    """
    from flask import send_file
    arquivo = _arquivo_midia_cena(projeto_id, scene_id)
    if not arquivo:
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


# --- POST /api/upload_audio/<projeto_id> (suporte — Card 1 manual) -------------

@app.route("/api/upload_audio/<projeto_id>", methods=["POST"])
def api_upload_audio(projeto_id: str):
    """Substitui o áudio do projeto (Card 1 manual) e reinicia a transcrição."""
    arquivo = request.files.get("audio")
    if not arquivo or not arquivo.filename:
        return jsonify({"success": False, "error": "Arquivo de áudio obrigatório"}), 400

    project_dir = PROJETOS_DIR / projeto_id
    ext = Path(arquivo.filename).suffix or ".mp3"
    audio_path = project_dir / f"{projeto_id}{ext}"
    arquivo.save(str(audio_path))

    meta = _meta(projeto_id)
    meta["arquivo_audio"] = str(audio_path)
    meta["transcricao_completa"] = False
    meta.pop("fonte_transcricao", None)
    meta.setdefault("steps", {}).pop("transcrever", None)
    _set_meta(projeto_id, meta)

    _set_web_state(projeto_id, etapa="transcrever", status="andamento",
                   mensagem="Transcrevendo áudio...")
    _iniciar_thread(projeto_id, "transcricao", _thread_transcrever,
                    projeto_id, str(audio_path))
    return jsonify({"success": True, "status": "transcrevendo",
                    "arquivo": str(audio_path)})


# --- GET/POST /api/config (suporte — pastas padrão globais) ----------------------

@app.route("/api/config", methods=["GET"])
def api_get_config():
    cfg = _web_config()
    return jsonify({"success": True, **cfg})


@app.route("/api/config", methods=["POST"])
def api_set_config():
    data = request.get_json(force=True, silent=True) or {}
    cfg = _web_config()
    if data.get("pasta_midia_padrao"):
        cfg["pasta_midia_padrao"] = data["pasta_midia_padrao"].strip()
    if data.get("pasta_destino"):
        cfg["pasta_destino"] = data["pasta_destino"].strip()
    _salvar_web_config(cfg)
    return jsonify({"success": True, **cfg})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Garante pastas padrão locais da web
    cfg = _web_config()
    Path(cfg["pasta_midia_padrao"]).mkdir(parents=True, exist_ok=True)
    Path(cfg["pasta_destino"]).mkdir(parents=True, exist_ok=True)
    _salvar_web_config(cfg)

    log_event("WEB", "ULTRACUT3 WEB v1.0 iniciado em http://127.0.0.1:5000", level="info")
    print("=" * 60)
    print("  ULTRACUT3 WEB v1.0")
    print("  http://127.0.0.1:5000")
    print("  (apenas local — não exponha a porta 5000 externamente)")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)


if __name__ == "__main__":
    main()
