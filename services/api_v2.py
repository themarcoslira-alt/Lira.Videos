"""
services/api_v2.py — Rotas da API v2 / Shadow Routing para ULTRACUT3 Studio 2.0
================================================================================
Endpoints modernos sob o prefixo /api/v2/... isolados da v1:
  - STUDIO v2: Criação, configuração, transcrição, SRT, storyboard e prompts com continuidade
  - PRODUÇÃO v2: Fila, status do Google Flow, envio por cena, acompanhamento e auto-importação
  - ARQUIVOS v2: Listagem categorizada, downloads, abrir pasta e limpeza segura
  - MONTAGEM v2: Sincronização de timeline, geração de vídeo final e exportação CapCut
"""
import os
import re
import json
import time
import shutil
import threading
import subprocess
import urllib.parse
import unicodedata
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from pathlib import Path
from flask import Blueprint, request, jsonify, send_file, current_app, Response
from flask import stream_with_context

from config import PROJETOS_DIR, OUTPUT_DIR, FFMPEG_PATH, MAX_CONTAS_SIMULTANEAS

# CAMINHO CONFIGURÁVEL (ANTIGRAVITY — isolamento da suíte): produção usa EXATAMENTE o
# mesmo valor; tests/conftest.py redireciona para um arquivo temporário.
CONTEXTO_CDP_PATH = Path("tmp/lira_context_cdp.json")
from services.event_logger import log_event
import services.scene_plan_service as scene_plan_svc
from services.video_encoder import sanitizar_nome_arquivo
from services.visual_profile import VisualProfile, PRESETS
from services.prompt_engine import PromptEngine
import services.character_service as character_svc
import services.visual_memory_service as visual_memory_svc
import services.prompt_engine as prompt_engine_svc
import services.visual_presets_service as presets_svc
import services.deepseek_prompt_service as deepseek_svc
from services.flow_account_manager import FlowAccountManager

api_v2_bp = Blueprint("api_v2", __name__)

@api_v2_bp.route("/health", methods=["GET"])
def v2_health():
    """Endpoint de verificação de saúde da aplicação para monitoramento."""
    return jsonify({
        "status": "ok",
        "service": "Lira.Videos API v2",
        "timestamp": datetime.now().isoformat()
    }), 200

# Gerenciador de contas Google Flow (reset diário de créditos + rotação + troca manual)
flow_mgr = FlowAccountManager()

# ===========================================================================
# FLOW QUEUE WORKER MANAGER (WORKERS POR PROJETO)
# ===========================================================================
fila_lock = threading.Lock()


def get_or_create_worker(projeto_id: str):
    from services.playwright_flow import FlowQueueWorker
    return FlowQueueWorker.get_queue_worker(projeto_id, PROJETOS_DIR)


def acordar_worker(projeto_id: str):
    with fila_lock:
        w = get_or_create_worker(projeto_id)
        w.start_processing()
        return w


# ---------------------------------------------------------------------------
# SSE — atualização em tempo real da fila de produção (PHASE 2, ERRO 6).
# Substituye el polling de 3s de la UI por un stream de eventos: contadores
# por estado + estado del Flow (conectado/fallback créditos) + ping cada 15s.
#
# PERFORMANCE: sem cache este helper custa ~1,05s por chamada
# (`socket.create_connection(timeout=1)` do probe CDP) e ainda relê o meta.json
# (que pode ter centenas de KB). Com 1 tick/s o stream ficava em ~2s/tick —
# o que atrasava o ping de 15 ticks para ~30s e degradava o "tempo real".
# Os caches abaixo levam o tick para ~0ms.
# ---------------------------------------------------------------------------
_SSE_CDP_CACHE: Dict[str, Any] = {"ts": 0.0, "valor": False}
_SSE_CDP_TTL = 5.0  # sonda CDP é cara (timeout de 1s quando a porta está fechada)
_SSE_META_CACHE: Dict[str, Tuple[Tuple[int, int], Dict[str, Any]]] = {}


def _cdp_cached(port: int = 9222) -> bool:
    """Sonda a porta CDP reaproveitando o resultado por alguns segundos."""
    agora = time.time()
    if (agora - _SSE_CDP_CACHE["ts"]) < _SSE_CDP_TTL:
        return bool(_SSE_CDP_CACHE["valor"])
    try:
        from services.playwright_flow import _cdp_port_open
        valor = bool(_cdp_port_open(port))
    except Exception:
        valor = False
    _SSE_CDP_CACHE["valor"] = valor
    _SSE_CDP_CACHE["ts"] = agora
    return valor


def _meta_cached(proyecto_id: str, chave_extra: Any = None) -> dict:
    """meta.json com cache por (mtime, size) — evita reler o JSON a cada tick."""
    try:
        meta_file = _project_dir(proyecto_id) / "meta.json"
        if not meta_file.is_file():
            return {}
        st = meta_file.stat()
        chave = (st.st_mtime_ns, st.st_size)
        hit = _SSE_META_CACHE.get(proyecto_id)
        if hit is not None and hit[0] == chave:
            return hit[1]
        meta = _get_meta(proyecto_id) or {}
        _SSE_META_CACHE[proyecto_id] = (chave, meta)
        return meta
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# REQ 4 — NAVEGAÇÃO AUTOMÁTICA PARA A ABA DE MONTAGEM QUANDO A FILA PAUSA
# ---------------------------------------------------------------------------
# Quando a fila é interrompida por CRÉDITOS (vídeo/imagem/fim de fila zerado) o
# operador não precisa continuar olhando a aba 3 (PRODUÇÃO FLOW): o backend emite
# um evento SSE dedicado `{"tipo": "navegarAba", "aba": "montagem", ...}` e o
# frontend executa `window.irParaAbaS2("montagem")` (já existente) para seguir
# para a edição final. O canal é SEPARADO do progresso (tipo próprio) para o
# frontend tratar em um handler dedicado, sem interferir na assinatura do status.
NAVEGACAO_POR_MOTIVO = {
    "credito_esgotado_video": "montagem",
    "credito_esgotado_imagem": "montagem",
    "fim_fila_credito_zerado": "montagem",
}

# Fila de eventos por projeto + memória do último evento entregue (dedup: nunca
# navega duas vezes pelo mesmo motivo, mesmo com reconexão do EventSource).
_NAV_LOCK = threading.Lock()
_NAV_PENDENTES: Dict[str, List[Dict[str, Any]]] = {}
_NAV_ULTIMO_ENTREGUE: Dict[str, str] = {}


def _motivo_navegavel(pause_reason: str) -> bool:
    """Checagem SILENCIOSA (rodada a cada tick do SSE — não pode logar)."""
    return str(pause_reason or "").strip() in NAVEGACAO_POR_MOTIVO


def should_navigate_on_pause(pause_reason: str) -> bool:
    """True se o motivo da pausa exige navegar para a aba de Montagem (REQ 4)."""
    resultado = _motivo_navegavel(pause_reason)
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}] [NAV] should_navigate_on_pause({pause_reason!r}) = {resultado}", flush=True)
    return resultado


def aba_destino_para_pausa(pause_reason: str) -> str:
    """Aba a abrir para o motivo informado ('' quando não há navegação)."""
    return NAVEGACAO_POR_MOTIVO.get(str(pause_reason or "").strip(), "")


def emit_navigate_event(projeto_id: str, aba: str = "montagem",
                        motivo: str = "") -> Optional[Dict[str, Any]]:
    """Enfileira o evento SSE `navegarAba` do projeto (thread-safe, nunca levanta).

    Chamado pelo worker (services/playwright_flow.py) logo após `set_pause_reason`
    nos motivos de crédito. A entrega é feita pelo stream SSE do projeto.
    """
    pid = str(projeto_id or "").strip()
    if not pid or not _motivo_navegavel(motivo):
        return None
    aba = str(aba or "montagem").strip() or "montagem"
    motivo = str(motivo or "").strip()
    evento = {
        "tipo": "navegarAba",
        "aba": aba,
        "motivo": motivo,
        "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
    }
    try:
        with _NAV_LOCK:
            _NAV_PENDENTES.setdefault(pid, []).append(evento)
        print(f"[{evento['timestamp']}] [NAV] emit_navigate_event({aba}) "
              f"projeto={pid} motivo={motivo}", flush=True)
        log_event("NAVEGACAO",
                  f"[NAV] Navegação automática para '{aba}' (motivo: {motivo}) — projeto {pid}.",
                  level="info")
    except Exception:
        pass
    return evento


def _consumir_eventos_navegacao(proyecto_id: str, pause_reason: str) -> List[Dict[str, Any]]:
    """Eventos `navegarAba` a emitir AGORA (fila explícita + transição detectada).

    A detecção por TRANSIÇÃO garante a navegação mesmo quando a aba do Studio 2 é
    aberta/reconectada DEPOIS da pausa (SSE não tem replay). O dedup por
    (projeto, motivo, aba) evita navegar duas vezes pelo mesmo motivo; quando a
    fila volta a rodar (pause_reason vazio) o dedup é liberado para a próxima pausa.
    """
    eventos: List[Dict[str, Any]] = []
    motivo_norm = str(pause_reason or "").strip()
    try:
        with _NAV_LOCK:
            for ev in (_NAV_PENDENTES.pop(proyecto_id, []) or []):
                chave = f"{proyecto_id}:{ev.get('motivo', '')}:{ev.get('aba', '')}"
                if _NAV_ULTIMO_ENTREGUE.get(proyecto_id) == chave:
                    continue
                _NAV_ULTIMO_ENTREGUE[proyecto_id] = chave
                eventos.append(ev)
            if _motivo_navegavel(motivo_norm):
                aba = aba_destino_para_pausa(motivo_norm)
                chave = f"{proyecto_id}:{motivo_norm}:{aba}"
                if _NAV_ULTIMO_ENTREGUE.get(proyecto_id) != chave:
                    _NAV_ULTIMO_ENTREGUE[proyecto_id] = chave
                    eventos.append({
                        "tipo": "navegarAba",
                        "aba": aba,
                        "motivo": motivo_norm,
                        "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                    })
            elif not motivo_norm:
                _NAV_ULTIMO_ENTREGUE.pop(proyecto_id, None)
    except Exception:
        return eventos
    for ev in eventos:
        try:
            print(f"[{ev.get('timestamp', '')}] [NAV] Emitindo navegação: "
                  f"{ev.get('aba')} (motivo: {ev.get('motivo')})", flush=True)
        except Exception:
            pass
    return eventos


def _flow_sse_status(proyecto_id: str) -> dict:
    """Estado compacto del Flow para el stream SSE (sin disco intenso: meta por stat)."""
    try:
        from services.playwright_flow import FlowQueueWorker
        st = FlowQueueWorker.get_status()
        conectado = bool(st.get("conectado", False)) or _cdp_cached(9222)
        fallback = bool(st.get("fallback_video_imagen", False))
        # REQ 3 — motivo CANÔNICO da parada + flag de interrupção, para o frontend
        # saber POR QUE a fila parou (crédito de vídeo/imagem, fim de fila, manual).
        stop_requested = bool(st.get("stop_requested", False))
        pause_reason = str(st.get("pause_reason") or "")
    except Exception:
        conectado, fallback = False, False
        stop_requested, pause_reason = False, ""
    try:
        from app_web import _FLOW_STATE
        est = _FLOW_STATE.get(proyecto_id, {}) or {}
    except Exception:
        est = {}
    try:
        alerta = (_meta_cached(proyecto_id) or {}).get("alerta_credito") or {}
    except Exception:
        alerta = {}
    return {
        "conectado": bool(conectado),
        "fallback_video_ativo": bool(fallback or alerta.get("fallback_video", False)),
        "fila_parada": bool(est.get("fila_parada", False)),
        # REQ 3 — vão DENTRO de "flow", que já faz parte da assinatura do SSE:
        # assim o evento é emitido também quando apenas o MOTIVO muda.
        "stop_requested": stop_requested,
        "pause_reason": pause_reason,
    }


@api_v2_bp.route("/producao/<proyecto_id>/stream", methods=["GET"])
def producao_sse(proyecto_id: str):
    """Server-Sent Events: progresso de la fila en tiempo real (<1s)."""

    def _generar():
        plan_path = _project_dir(proyecto_id) / "lira_scene_plan.json"
        ultimo = None
        ultimo_stat = None
        # Última contagem válida: evita reenviar total=0 quando o plano não mudou
        contagem = {"total": 0, "por_status": {}}
        # Ping por relógio (não por ticks): o tick pode variar de duração, e com
        # contagem de ticks o keep-alive atrasava (era ~30s antes do cache CDP).
        ultimo_ping = time.time()
        try:
            while True:
                try:
                    _st = None
                    if plan_path.is_file():
                        try:
                            _st = plan_path.stat()
                        except OSError:
                            _st = None
                    mudou = _st is not None and (
                        ultimo_stat is None
                        or (ultimo_stat.st_mtime_ns, ultimo_stat.st_size) != (_st.st_mtime_ns, _st.st_size)
                    )
                    if mudou:
                        try:
                            plan = json.loads(plan_path.read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            plan = None
                        cenas = plan.get("cenas") if isinstance(plan, dict) else plan
                        if isinstance(cenas, list):
                            por_status = {}
                            for c in cenas:
                                _stk = str(c.get("status") or "UNKNOWN")
                                por_status[_stk] = por_status.get(_stk, 0) + 1
                            contagem = {"total": len(cenas), "por_status": por_status}
                        ultimo_stat = _st
                    payload = {
                        "tipo": "progresso_fila",
                        "total": contagem["total"],
                        "por_status": contagem["por_status"],
                        "flow": _flow_sse_status(proyecto_id),
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    # REQ 3 — espelha o motivo da parada no TOPO do payload (conveniência
                    # para consumidores que não descem em "flow").
                    payload["stop_requested"] = bool(payload["flow"].get("stop_requested", False))
                    payload["pause_reason"] = str(payload["flow"].get("pause_reason") or "")
                    # Assinatura SEM timestamp: só emite quando algo realmente muda.
                    # REQ 3 — stop_requested/pause_reason entram na assinatura para o
                    # evento ser emitido quando APENAS O MOTIVO da parada muda.
                    _assinatura = json.dumps(
                        {
                            "tipo": payload["tipo"],
                            "total": payload["total"],
                            "por_status": payload["por_status"],
                            "flow": payload["flow"],
                            "stop_requested": payload["stop_requested"],
                            "pause_reason": payload["pause_reason"],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    if _assinatura != ultimo:
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                        ultimo = _assinatura
                    # REQ 4 — evento DEDICADO de navegação automática (aba Montagem)
                    # quando a pausa é por crédito. Canal separado da assinatura de
                    # progresso: o frontend trata em handler próprio ("navegarAba"),
                    # então irParaAbaS2 roda mesmo sem mudança de contadores.
                    for _ev_nav in _consumir_eventos_navegacao(proyecto_id, payload["pause_reason"]):
                        yield f"data: {json.dumps(_ev_nav, ensure_ascii=False)}\n\n"
                except Exception as _e_sse:
                    _err = json.dumps({"tipo": "error", "erro": str(_e_sse)}, ensure_ascii=False)
                    if _err != ultimo:
                        yield f"data: {_err}\n\n"
                        ultimo = _err
                # Keep-alive por relógio: independente da duração do tick.
                if (time.time() - ultimo_ping) >= 15:
                    ultimo_ping = time.time()
                    yield f"data: {json.dumps({'tipo': 'ping', 'ts': time.time()})}\n\n"
                time.sleep(1)
        except GeneratorExit:
            raise
        except Exception as _e_sse:
            yield f"data: {json.dumps({'erro': str(_e_sse)})}\n\n"

    return Response(
        stream_with_context(_generar()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# NO-CACHE — o browser nunca deve servir respostas de /api/v2/* do cache
# ---------------------------------------------------------------------------
@api_v2_bp.after_request
def _v2_no_cache(response):
    """Força recarga sem cache em toda a API v2 (a UI sempre vê o estado atual).

    Nota: apenas o `after_request` altera a resposta — um `before_request` que
    só importa `make_response` sem retornar nada não tem efeito algum.
    """
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ---------------------------------------------------------------------------
# LISTAGEM GERAL DE PROJETOS V2
# ---------------------------------------------------------------------------

@api_v2_bp.route("/projetos", methods=["GET"])
@api_v2_bp.route("/projetos/listar", methods=["GET"])
def v2_listar_projetos():
    """Lista todos os projetos existentes com metadados detalhados para o Studio 2.0 e tela inicial."""
    projetos = []
    if PROJETOS_DIR.exists():
        for p in sorted(PROJETOS_DIR.iterdir(), key=lambda d: d.stat().st_mtime if d.is_dir() else 0, reverse=True):
            if not p.is_dir():
                continue
            meta = {}
            meta_file = p / "meta.json"
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}

            # Carrega scene plan se disponível para contagens rápidas
            plan_file = p / "lira_scene_plan.json"
            total_cenas = 0
            cenas_prontas = 0
            if plan_file.exists():
                try:
                    plan = json.loads(plan_file.read_text(encoding="utf-8"))
                    cenas = plan.get("cenas", [])
                    total_cenas = len(cenas)
                    cenas_prontas = sum(1 for c in cenas if c.get("status") in ("BAIXADA", "PRONTO", "CONCLUIDO") or c.get("image_status") in ("READY", "DOWNLOADED"))
                except Exception:
                    pass

            projetos.append({
                "id": p.name,
                "nome": meta.get("display_name") or meta.get("name") or meta.get("titulo") or p.name,
                "modo": meta.get("modo_execucao") or "manual",
                "studio_version": meta.get("studio_version") or ("v2" if plan_file.exists() else "v1"),
                "modo_producao": meta.get("modo_producao") or "somente_imagens",
                "nome_personagem": meta.get("nome_personagem") or "",
                "total_cenas": total_cenas,
                "cenas_prontas": cenas_prontas,
                "criado_em": meta.get("created", ""),
                "status": "pronto" if (total_cenas > 0 and cenas_prontas >= total_cenas) else ("em_producao" if cenas_prontas > 0 else "criado"),
                "transcricao_completa": bool(meta.get("transcricao_completa")),
            })

    return jsonify({"success": True, "projetos": projetos, "total": len(projetos)})

MASTER_STYLES = {
    "photorealistic_cinematic": {
        "estilo": "photorealistic, hyperrealistic, cinematic lighting, 8k resolution, photorealistic textures",
        "composicao": "cinematic framing, rule of thirds, dynamic angle, depth of field",
    },
    "boneco_palito": {
        "estilo": "minimalist stick figure drawing, clean black lines on crisp solid white background, 2d doodle art, simple expressive lines, humorous cartoon style, uncluttered, no shading",
        "composicao": "centered, wide composition, clean empty background",
    },
    "cartoon": {
        "estilo": "vibrant 3d animation style, Pixar inspired, colorful, expressive characters, soft lighting",
        "composicao": "cinematic camera, dynamic framing, balanced lighting",
    },
    "cinematografico_dramatico": {
        "estilo": "dramatic film still, high contrast, anamorphic lens flare, gritty realism, atmospheric haze",
        "composicao": "close-up, moody framing, cinematic color grading",
    },
}

def _project_dir(projeto_id: str) -> Path:
    return PROJETOS_DIR / projeto_id

def _get_meta(projeto_id: str) -> dict:
    meta_file = _project_dir(projeto_id) / "meta.json"
    plan_file = _project_dir(projeto_id) / "lira_scene_plan.json"
    meta = {}
    if meta_file.exists():
        try:
            from config import normalizar_caminho
            raw_text = meta_file.read_text(encoding="utf-8")
            raw_text = normalizar_caminho(raw_text)
            meta = json.loads(raw_text)
        except Exception:
            meta = {}
    
    defaults = {
        "modo_producao": "imagem_video",
        "nome_personagem": "",
        "referencia_visual_global": None,
        "estilo_visual": "photorealistic_cinematic",
        "continuidade_visual": True,
        "studio_version": "v2" if plan_file.exists() else "v1",
    }
    for k, v in defaults.items():
        if k not in meta or (k == "studio_version" and plan_file.exists()):
            meta[k] = v
    return meta

def _save_meta(projeto_id: str, meta: dict):
    pdir = _project_dir(projeto_id)
    pdir.mkdir(parents=True, exist_ok=True)
    scene_plan_svc.garantir_estrutura_pastas(projeto_id)
    meta_file = pdir / "meta.json"
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

# AUDIOS_EXT: extensões de áudio aceitas em todas as resoluções
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}


def resolver_audio_projeto(projeto_id: str):
    """Resolve o caminho do áudio master do projeto — FONTE ÚNICA (ANTIGRAVITY Passo 1).

    Usada pelas rotas /montagem/<id>/sincronizar, /projeto/<id>/audio e exportação,
    para que o player nunca perca o áudio por caminho desalinhado em meta.json.

    Ordem de busca:
      1. meta['arquivo_audio'] (caminho absoluto) se existir em disco;
      2. audio/audio_original.{mp3,wav,m4a,aac,ogg} (padrão Studio 2.0);
      3. qualquer áudio dentro de audio/;
      4. <projeto_id>.mp3/.wav (espelho legado v1 na raiz);
      5. qualquer áudio na raiz do projeto.

    ANTIGRAVITY (migração): o áudio encontrado em padrão legado (raiz <id>.MP3, etc.)
    é automaticamente NORMALIZADO para projetos/<id>/audio/audio_original.mp3
    (extensão minúscula) antes de retornar — resolve "Mídia perdida" no CapCut e
    mantém um único arquivo canônico. Não altera meta.json.

    Retorna str (caminho canônico resolvido) ou None.
    """
    def _canonical(fonte):
        """Copia o arquivo para audio/audio_original.mp3 (minúsculo) e devolve esse path."""
        try:
            src = Path(str(fonte))
            if not src.is_file():
                return str(fonte)
            audio_dir = _project_dir(projeto_id) / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            dest = audio_dir / "audio_original.mp3"
            if src.resolve() != dest.resolve():
                shutil.copy2(str(src), str(dest))
            return str(dest)
        except Exception:
            return str(fonte)

    meta = _get_meta(projeto_id)
    caminho = (meta.get("arquivo_audio") or "").strip()
    if caminho and Path(caminho).exists():
        return _canonical(caminho)

    pdir = _project_dir(projeto_id)
    audio_dir = pdir / "audio"
    if audio_dir.is_dir():
        for f in sorted(audio_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in _AUDIO_EXTS:
                return _canonical(f)

    for ext in _AUDIO_EXTS:
        cand = pdir / f"{projeto_id}{ext}"
        if cand.is_file():
            return _canonical(cand)

    for cand in sorted(pdir.glob("*.*")):
        if cand.is_file() and cand.suffix.lower() in _AUDIO_EXTS:
            return _canonical(cand)

    return None


def _duracao_audio_segundos(arquivo_audio) -> float:
    """Duração (em segundos) real de um arquivo de áudio via ffprobe. 0.0 se falhar/ausente.

    Usada APENAS no export CapCut (montagem v2) para que a ÚLTIMA cena cubra até o
    fim físico do áudio — a duração de fala (tempo_fim - tempo_inicio) não inclui o
    silêncio final, e sem isto o fim da trilha de vídeo fica aquém do áudio.
    """
    try:
        if not arquivo_audio or not Path(str(arquivo_audio)).is_file():
            return 0.0
        from config import FFPROBE_PATH
        if not FFPROBE_PATH:
            return 0.0
        r = subprocess.run(
            [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(arquivo_audio)],
            capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and r.stdout.strip():
            return float(r.stdout.strip())
    except Exception:
        pass
    return 0.0


def _padronizar_nome_arquivo(cid: int, tempo_inicio: float, tempo_fim: float, ext: str = ".png") -> str:
    """Gera nome padronizado Studio 2.0: 001_00-00_00-05.png"""
    ini_m = int(tempo_inicio // 60)
    ini_s = int(tempo_inicio % 60)
    fim_m = int(tempo_fim // 60)
    fim_s = int(tempo_fim % 60)
    return f"{cid:03d}_{ini_m:02d}-{ini_s:02d}_{fim_m:02d}-{fim_s:02d}{ext}"


# ===========================================================================
# Pertinência do SRT — guarda anti-sobrescrita usada por v2_usar_srt
# ===========================================================================

# Projetos com MAIS cenas que este limiar em cenas.json já têm conteúdo real
# (roteiro + scene plan) que seria destruído por um SRT estranho; só acima dele
# a guarda bloqueia. Projetos novos/pequenos continuam aceitando qualquer SRT.
LIMIAR_CENAS_PERTINENCIA = 10

# Palavras ignoradas na comparação (inglês + português): artigos, pronomes,
# preposições e conectivos que aparecem em praticamente qualquer roteiro e não
# identificam o projeto.
_STOPWORDS_PERTINENCIA = {
    "this", "that", "these", "those", "there", "then", "than", "them", "they",
    "what", "when", "where", "which", "while", "will", "would", "were", "was",
    "are", "have", "has", "had", "been", "being", "into", "over", "just",
    "like", "about", "because", "very", "some", "more", "most", "your",
    "yours", "you", "the", "and", "for", "not", "but", "its", "it's", "with",
    "from", "here", "also", "even", "still", "only", "does", "did", "doing",
    "para", "porque", "como", "mais", "menos", "esse", "essa", "isso", "este",
    "esta", "isto", "aquele", "aquela", "com", "sem", "dos", "das", "uma",
    "uns", "umas", "pelo", "pela", "nos", "nas", "que", "nao", "seu", "sua",
    "seus", "suas", "ele", "ela", "eles", "elas", "voce", "voces", "muito",
    "muita", "aqui", "onde", "quando", "entao",
}


def _normalizar_palavra(palavra: str) -> str:
    """Minúsculas e sem acentos — compara textos digitados de formas diferentes."""
    base = unicodedata.normalize("NFKD", palavra.lower())
    return "".join(c for c in base if not unicodedata.combining(c))


def _primeiras_palavras_significativas(texto: str, limite: int = 3) -> List[str]:
    """
    Primeiras `limite` palavras relevantes de um roteiro/SRT.

    Ignora números de timestamp (1-3 dígitos), palavras curtas e stopwords, para
    comparar CONTEÚDO real e não as marcações de tempo do arquivo.
    """
    palavras: List[str] = []
    for bruta in re.findall(r"[0-9A-Za-zÀ-ÿ']+", texto or ""):
        limpa = _normalizar_palavra(bruta).strip("'")
        if len(limpa) < 4 or limpa in _STOPWORDS_PERTINENCIA:
            continue
        palavras.append(limpa)
        if len(palavras) >= limite:
            break
    return palavras


def _contar_cenas_json(projeto_id: str) -> int:
    """Nº de cenas já existentes em cenas.json do projeto (0 se ausente/ilegível)."""
    arquivo = _project_dir(projeto_id) / "cenas.json"
    if not arquivo.exists():
        return 0
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if isinstance(dados, dict):
        dados = dados.get("cenas") or []
    return len(dados) if isinstance(dados, list) else 0


# ===========================================================================
# 1. STUDIO v2
# ===========================================================================

@api_v2_bp.route("/projeto/criar", methods=["POST"])
def v2_criar_projeto():
    """
    Cria um novo projeto no padrão Studio 2.0 (studio_version = 'v2').
    Gera automaticamente a estrutura de 6 pastas padronizadas.
    """
    data = request.get_json(silent=True) if request.is_json else (request.form or {})
    if not data:
        data = request.form or {}

    nome = (data.get("nome") or "").strip()
    if not nome:
        return jsonify({"success": False, "error": "Nome do projeto é obrigatório"}), 400

    modo_producao = (data.get("modo_producao") or "imagem_video").strip()
    nome_personagem = (data.get("nome_personagem") or "").strip()
    estilo_visual = (data.get("estilo_visual") or "photorealistic_cinematic").strip()
    continuidade = data.get("continuidade_visual")
    continuidade_visual = True if continuidade in (True, "true", "1", None) else False

    projeto_id = sanitizar_nome_arquivo(nome)
    project_dir = _project_dir(projeto_id)
    if project_dir.exists() and (project_dir / "meta.json").exists():
        return jsonify({"success": False, "error": f"Projeto '{projeto_id}' já existe"}), 409

    project_dir.mkdir(parents=True, exist_ok=True)
    scene_plan_svc.garantir_estrutura_pastas(projeto_id)

    # Processa áudio inicial se enviado
    audio_path = ""
    arquivo_audio = request.files.get("audio")
    if arquivo_audio and arquivo_audio.filename:
        ext = Path(arquivo_audio.filename).suffix or ".mp3"
        dest_audio = project_dir / "audio" / f"audio_original{ext}"
        arquivo_audio.save(str(dest_audio))
        audio_path = str(dest_audio)
        # Cópia para raiz (compatibilidade com v1)
        shutil.copy2(str(dest_audio), str(project_dir / f"{projeto_id}{ext}"))

    meta = {
        "name": nome,
        "display_name": nome,
        "modo_execucao": "manual",
        "modo_producao": modo_producao,
        "nome_personagem": nome_personagem,
        "referencia_visual_global": None,
        "estilo_visual": estilo_visual,
        "continuidade_visual": continuidade_visual,
        "studio_version": "v2",
        "arquivo_audio": audio_path,
        "transcricao_completa": False,
        "created": datetime.now().isoformat(sep=" ", timespec="seconds"),
    }
    _save_meta(projeto_id, meta)
    log_event("STUDIO_V2", f"Projeto Studio 2.0 '{projeto_id}' criado com sucesso", level="info")

    return jsonify({
        "success": True,
        "projeto_id": projeto_id,
        "studio_version": "v2",
        "meta": meta,
    }), 201


@api_v2_bp.route("/projeto/<projeto_id>/config", methods=["GET", "POST"])
def v2_projeto_config(projeto_id: str):
    """Lê ou atualiza as configurações do Studio 2.0 para um projeto."""
    meta = _get_meta(projeto_id)
    plan_file = _project_dir(projeto_id) / "lira_scene_plan.json"
    has_plan = plan_file.exists()
    studio_ver = "v2" if (has_plan or meta.get("studio_version") == "v2") else meta.get("studio_version", "v1")

    if has_plan and meta.get("studio_version") != "v2":
        meta["studio_version"] = "v2"
        _save_meta(projeto_id, meta)

    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        for campo in ("modo_producao", "nome_personagem", "estilo_visual", "continuidade_visual", "referencia_visual_global", "prod_modelo", "prod_modelo_imagem", "prod_modelo_video", "prod_qualidade", "prod_qualidade_imagem", "prod_qualidade_video", "prod_qualidade_download", "prod_qualidade_download_video", "prod_tipo_saida", "prod_proporcao", "provedor_storyboard", "provedor_prompts"):
            if campo in data:
                meta[campo] = data[campo]
        _save_meta(projeto_id, meta)
        return jsonify({"success": True, "meta": meta, "has_scene_plan": has_plan, "studio_version": studio_ver})

    return jsonify({"success": True, "meta": meta, "has_scene_plan": has_plan, "studio_version": studio_ver})


@api_v2_bp.route("/transcricao/<projeto_id>/upload_audio", methods=["POST"])
def v2_upload_audio(projeto_id: str):
    """Upload ou substituição de áudio do projeto no Studio 2.0."""
    arquivo = request.files.get("audio") or request.files.get("file")
    if not arquivo or not arquivo.filename:
        return jsonify({"success": False, "error": "Nenhum arquivo de áudio enviado"}), 400

    pdir = _project_dir(projeto_id)
    scene_plan_svc.garantir_estrutura_pastas(projeto_id)
    ext = Path(arquivo.filename).suffix or ".mp3"
    dest_audio = pdir / "audio" / f"audio_original{ext}"
    arquivo.save(str(dest_audio))

    # Cópia para raiz (compatibilidade v1)
    shutil.copy2(str(dest_audio), str(pdir / f"{projeto_id}{ext}"))

    meta = _get_meta(projeto_id)
    meta["arquivo_audio"] = str(dest_audio)
    meta["transcricao_completa"] = False
    _save_meta(projeto_id, meta)

    return jsonify({"success": True, "arquivo_audio": str(dest_audio)})


@api_v2_bp.route("/transcricao/<projeto_id>/usar_srt", methods=["POST"])
def v2_usar_srt(projeto_id: str):
    """Salva e processa SRT manual ou colado no Studio 2.0."""
    data = request.get_json(force=True, silent=True) or {}
    texto_srt = (data.get("srt_texto") or "").strip()
    if not texto_srt:
        return jsonify({"success": False, "error": "Texto do SRT é obrigatório"}), 400

    pdir = _project_dir(projeto_id)
    scene_plan_svc.garantir_estrutura_pastas(projeto_id)

    # ── Guarda de PERTINÊNCIA do SRT (anti-destruição de projeto) ─────────────
    # Qualquer SRT era aceito cegamente: as linhas seguintes sobrescrevem o .srt,
    # o roteiro_transcricao.json e o cenas.json do projeto e ainda regeneram o
    # scene plan com force=True — ou seja, o SRT de OUTRO projeto destruía o
    # roteiro do projeto atual (ex: SRT do Gypsum enviado para Tomato Plants).
    # A fonte de verdade do áudio original é o roteiro_transcricao.txt do próprio
    # projeto. Comparamos as 3 primeiras palavras significativas dos dois textos:
    # sem NENHUMA coincidência, e com o projeto já tendo mais de
    # LIMIAR_CENAS_PERTINENCIA cenas em cenas.json (conteúdo real que seria
    # perdido), a operação é bloqueada ANTES de qualquer escrita — nada é gravado,
    # nem mesmo o arquivo .srt.
    # Se o projeto não tem roteiro_transcricao.txt, é projeto novo/sem transcrição
    # anterior: não existe referência para comparar e a operação segue normal.
    _txt_projeto = pdir / "roteiro_transcricao.txt"
    if _txt_projeto.exists():
        _palavras_srt = _primeiras_palavras_significativas(texto_srt)
        _palavras_projeto = _primeiras_palavras_significativas(
            _txt_projeto.read_text(encoding="utf-8", errors="replace")
        )
        _n_cenas_existentes = _contar_cenas_json(projeto_id)
        if (_palavras_srt and _palavras_projeto
                and not (set(_palavras_srt) & set(_palavras_projeto))
                and _n_cenas_existentes > LIMIAR_CENAS_PERTINENCIA):
            log_event("ROTEIRO", (
                f"{projeto_id}: SRT BLOQUEADO (não pertence ao projeto) — "
                f"srt={_palavras_srt} projeto={_palavras_projeto} "
                f"cenas={_n_cenas_existentes}"
            ))
            return jsonify({
                "success": False,
                "error": "SRT não pertence a este projeto",
                "projeto_id": projeto_id,
                "blocked": True,
                "primeiras_palavras_srt": _palavras_srt,
                "primeiras_palavras_projeto": _palavras_projeto,
                "n_cenas_existentes": _n_cenas_existentes,
            }), 409

    # Salva arquivo .srt na pasta srt/
    srt_file = pdir / "srt" / "roteiro_transcricao.srt"
    # Projetos antigos podem não ter a pasta srt/ (garantir_estrutura_pastas
    # não a cria — PASTAS_PROJETO_V2 não inclui "srt"). Cria antes de escrever
    # para evitar FileNotFoundError como em projetos sem transcrição Whisper.
    srt_file.parent.mkdir(parents=True, exist_ok=True)
    srt_file.write_text(texto_srt, encoding="utf-8")

    # ANTIGRAVITY Passo 3: extração de segmentos com timestamps REAIS.
    # Formato aceito: '[MM:SS] texto' / 'MM:SS texto' OU blocos SRT padrão (-->).
    # NUNCA inventa timestamps: se o parsing falhar, retorna erro sem tocar em disco.
    segmentos = []
    for m in re.finditer(r"\[?\s*(\d{1,2}):(\d{2})\s*\]?\s+(.+)", texto_srt):
        start = int(m.group(1)) * 60 + int(m.group(2))
        end = start + 5.0
        segmentos.append({
            "start": float(start),
            "end": float(end),
            "text": m.group(3).strip(),
            "timestamp": f"{int(m.group(1)):02d}:{m.group(2)}",
        })

    # Fallback NÃO-destrutivo: tenta blocos SRT padrão (1\nHH:MM:SS,mmm --> ...)
    if not segmentos and "-->" in texto_srt:
        for bloco in re.split(r"\n\s*\n", texto_srt.strip()):
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
                r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*"
                r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})",
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
                "start": round(start, 2),
                "end": round(end, 2),
                "text": texto,
                "timestamp": f"{mm:02d}:{ss:02d}",
            })

    # Se NÃO há timestamps válidos, retorna erro detalhado SEM sobrescrever
    # cenas.json/roteiro_transcricao.json nem disparar gerar_scene_plan(force=True).
    if not segmentos:
        return jsonify({
            "success": False,
            "error": ("Não foi possível extrair timestamps válidos do texto. "
                      "Use o formato '[MM:SS] texto' (uma fala por linha) ou "
                      "blocos SRT padrão. Nenhum dado existente foi alterado."),
        }), 400

    # Salva roteiro_transcricao.json
    dados_transcricao = {
        "project": projeto_id,
        "segments": segmentos,
        "duration": segmentos[-1]["end"] if segmentos else 0.0,
        "language": "pt",
    }
    (pdir / "roteiro_transcricao.json").write_text(json.dumps(dados_transcricao, indent=2, ensure_ascii=False), encoding="utf-8")
    (pdir / "srt" / "roteiro_transcricao.json").write_text(json.dumps(dados_transcricao, indent=2, ensure_ascii=False), encoding="utf-8")

    # Reagrupa falas inteligentemente em cenas de 3-6s via scene_builder (evita micro-cenas 1:1)
    import services.scene_builder as scene_builder
    scene_builder.PROJETOS_DIR = PROJETOS_DIR
    res_cenas = scene_builder.gerar_cenas(projeto_id)
    total_cenas = res_cenas.get("cenas_count", 0) if isinstance(res_cenas, dict) else 0

    meta = _get_meta(projeto_id)
    meta["transcricao_completa"] = True
    _save_meta(projeto_id, meta)

    log_event("ROTEIRO", f"{projeto_id}: {len(segmentos)} falas importadas do SRT ({total_cenas} cenas reagrupadas)")

    # Gera scene plan automaticamente
    plan = scene_plan_svc.gerar_scene_plan(projeto_id, force=True)

    # Disparo automático do Brain System Director em background (idempotente e não-bloqueante)
    try:
        disparar_brain_background(projeto_id)
    except Exception as _eb:
        log_event("ROTEIRO", f"Brain Director hook aviso: {_eb}", level="warn")

    return jsonify({"success": True, "total_cenas": total_cenas, "plan": plan})


@api_v2_bp.route("/transcricao/<projeto_id>/status", methods=["GET"])
def v2_transcricao_status(projeto_id: str):
    """Retorna o status e conteúdo da transcrição (SRT e TXT) do projeto."""
    pdir = _project_dir(projeto_id)
    srt_file = pdir / "srt" / "roteiro_transcricao.srt"
    txt_file = pdir / "roteiro_transcricao.txt"
    json_file = pdir / "roteiro_transcricao.json"

    srt_texto = ""
    if srt_file.exists():
        srt_texto = srt_file.read_text(encoding="utf-8")
    elif txt_file.exists():
        srt_texto = txt_file.read_text(encoding="utf-8")
    elif json_file.exists():
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            linhas = [f"[{s.get('timestamp', '00:00')}] {s.get('text', '')}" for s in data.get("segments", [])]
            srt_texto = "\n".join(linhas)
        except Exception:
            srt_texto = ""

    meta = _get_meta(projeto_id)
    return jsonify({
        "success": True,
        "transcricao_completa": bool(meta.get("transcricao_completa") or srt_texto),
        "transcricao": {
            "srt_texto": srt_texto,
            "has_transcription": bool(srt_texto),
        }
    })


@api_v2_bp.route("/projeto/<projeto_id>/scene_plan", methods=["GET"])
def v2_get_scene_plan(projeto_id: str):
    """Retorna o plano de cenas (lira_scene_plan.json) do projeto no Studio 2.0."""
    plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    if not plan:
        return jsonify({"success": False, "cenas": [], "error": "Plano de cenas não encontrado"}), 404
    return jsonify({"success": True, "plan": plan, "cenas": plan.get("cenas", [])})


@api_v2_bp.route("/storyboard/<projeto_id>/gerar", methods=["POST"])
@api_v2_bp.route("/projeto/<projeto_id>/gerar_scene_plan", methods=["POST"])
def v2_gerar_storyboard(projeto_id: str):
    """
    Gera / atualiza o plano de cenas do Studio 2.0 (lira_scene_plan.json).
    Propaga modo_producao, estilo_visual e nome_personagem configurados no projeto.
    """
    res = scene_plan_svc.gerar_scene_plan(projeto_id, force=True)
    if not res.get("success"):
        return jsonify(res), 400

    plan_atualizado = scene_plan_svc.carregar_scene_plan(projeto_id)
    return jsonify({"success": True, "total": len(plan_atualizado.get("cenas", [])), "plan": plan_atualizado})


@api_v2_bp.route("/projeto/<projeto_id>/force_narrativa_v3", methods=["POST"])
def v2_force_narrativa_v3(projeto_id: str):
    """
    Força reclassificação e rebalanceamento narrativo v3 (Frente 4).
    """
    try:
        mudancas = scene_plan_svc.force_narrativa_v3_update(projeto_id)
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        return jsonify({
            "success": True,
            "projeto_id": projeto_id,
            "mudancas": mudancas,
            "narrativa_versao": plan.get("narrativa_versao") if plan else None,
            "total_cenas": len(plan.get("cenas", [])) if plan else 0,
            "plan": plan
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/prompts/<projeto_id>/gerar", methods=["POST"])
def v2_gerar_prompts(projeto_id: str):
    """
    Gera prompts inteligentes com continuidade visual (Fase 5) e @NomePersonagem.
    A Cena 01 estabelece a âncora visual e as seguintes herdam coerência.
    """
    data = request.get_json(force=True, silent=True) or {}
    meta = _get_meta(projeto_id)

    # Validação explícita: o plano de cenas (lira_scene_plan.json) precisa existir
    # (Tab 1) antes de gerar prompts — mensagem clara em vez de falha confusa.
    scene_plan_path = _project_dir(projeto_id) / "lira_scene_plan.json"
    if not scene_plan_path.exists():
        return jsonify({
            "success": False,
            "error": "Plano de cenas não encontrado. Gere a estrutura de cenas primeiro (Tab 1).",
            "code": "SCENE_PLAN_MISSING"
        }), 400

    estilo_visual = data.get("estilo_visual") or meta.get("estilo_visual") or "photorealistic_cinematic"
    nome_personagem = (data.get("nome_personagem") or meta.get("nome_personagem") or "").strip()
    continuidade_ativa = meta.get("continuidade_visual", True)

    style_preset = PRESETS.get(estilo_visual, PRESETS["photorealistic_cinematic"])
    style_lock = MASTER_STYLES.get(estilo_visual, MASTER_STYLES["photorealistic_cinematic"])

    plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    if not plan or not plan.get("cenas"):
        scene_plan_svc.gerar_scene_plan(projeto_id, force=True)
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)

    if not plan or not plan.get("cenas"):
        return jsonify({"success": False, "error": "Cenas não encontradas. Verifique a transcrição."}), 400

    cenas = plan["cenas"]
    linhas_storyboard = []

    # 1. Inicializa ou atualiza a memória visual com o contexto da transcrição completa
    texto_completo_roteiro = " ".join(c.get("texto", "") for c in cenas)
    visual_memory_svc.inicializar_memoria_visual(projeto_id, estilo_visual=estilo_visual, transcricao_texto=texto_completo_roteiro)

    # 2. Constrói prompts estruturados com Character Locks & World Locks
    prompts_txt_formatados = []

    for i, c in enumerate(cenas):
        cid = c["id"]
        res_prompt = prompt_engine_svc.construir_prompt_cena(
            projeto_id=projeto_id,
            cena=c,
            index=i,
            total_cenas=len(cenas),
            nome_personagem=nome_personagem,
            estilo_visual=estilo_visual
        )

        prompt_img = res_prompt["prompt_imagem"]
        prompt_anim = res_prompt["prompt_animacao"]
        tem_personagem = res_prompt["tem_personagem"]
        char_final = res_prompt["nome_personagem"]

        dur = float(c.get("duracao") or 3.0)
        ts_ini = float(c.get("tempo_inicio", 0))
        ts_fim = float(c.get("tempo_fim", ts_ini + dur))
        nome_arquivo_esperado = _padronizar_nome_arquivo(cid, ts_ini, ts_fim, ".png" if c.get("tipo") != "video" else ".mp4")

        scene_plan_svc.atualizar_cena(projeto_id, cid, {
            "prompt_imagem": prompt_img,
            "prompt_animacao": prompt_anim,
            "nome_personagem": char_final,
            "tem_personagem": tem_personagem,
            "continuidade": continuidade_ativa,
            "timestamp_saida": f"{scene_plan_svc._fmt_ts(ts_ini)}_{scene_plan_svc._fmt_ts(ts_fim)}",
            "status": scene_plan_svc.STATUS_PROMPT_PRONTO,
        })

        prompts_txt_formatados.append(prompt_img)

    # Salva na pasta prompts/ com formato limpo
    prompts_dir = _project_dir(projeto_id) / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    prompts_conteudo = "\n\n".join(prompts_txt_formatados)
    (prompts_dir / "storyboard_prompts.txt").write_text(prompts_conteudo, encoding="utf-8")
    (prompts_dir / "prompts.txt").write_text(prompts_conteudo, encoding="utf-8")

    memoria_vis = visual_memory_svc.obter_memoria_visual(projeto_id)
    plan_atualizado = scene_plan_svc.carregar_scene_plan(projeto_id)
    return jsonify({
        "success": True,
        "total": len(cenas),
        "referencia_visual_global": memoria_vis.get("environment", ""),
        "memoria_visual": memoria_vis,
        "plan": plan_atualizado,
        "prompts_file": str(prompts_dir / "storyboard_prompts.txt"),
    })


# ---------------------------------------------------------------------------
# PRESETS VISUAIS CANÔNICOS
# ---------------------------------------------------------------------------

@api_v2_bp.route("/presets/estilos", methods=["GET"])
def v2_presets_estilos():
    """Retorna a lista canônica dos 11 presets visuais do Lira Studio."""
    return jsonify({
        "success": True,
        "presets": presets_svc.listar_presets_estilos(),
        "default": presets_svc.ESTILO_PADRAO_ID
    })


# ---------------------------------------------------------------------------
# CONFIGURAÇÃO SEGURA DEEPSEEK
# ---------------------------------------------------------------------------

@api_v2_bp.route("/deepseek/status", methods=["GET"])
def v2_deepseek_status():
    """Retorna status seguro de configuração da API DeepSeek (nunca expõe a chave completa)."""
    st = deepseek_svc.obter_status_deepseek()
    return jsonify({"success": True, **st})


@api_v2_bp.route("/deepseek/config", methods=["POST"])
def v2_deepseek_config():
    """Salva com segurança a chave da API DeepSeek no web_keys.json local."""
    data = request.get_json(force=True, silent=True) or {}
    key = (data.get("api_key") or data.get("key") or "").strip()
    if not key:
        return jsonify({"success": False, "error": "Chave de API não pode ser vazia"}), 400

    ok = deepseek_svc.salvar_api_key_deepseek(key)
    if not ok:
        return jsonify({"success": False, "error": "Falha ao salvar chave localmente"}), 500

    return jsonify({"success": True, **deepseek_svc.obter_status_deepseek()})


@api_v2_bp.route("/config/testar_provedor", methods=["POST"])
def v2_testar_provedor():
    """
    Testa a conectividade de um provedor de IA específico (claude, deepseek).
    """
    import time
    import requests
    data = request.get_json(force=True, silent=True) or {}
    provedor = (data.get("provedor") or "").strip().lower()
    
    if not provedor:
        return jsonify({"success": False, "error": "Provedor não especificado"}), 400

    if provedor not in ("claude", "deepseek"):
        return jsonify({
            "success": False,
            "error": f"Provedor não suportado: {provedor}"
        }), 400

    # 1. Carrega a chave correspondente (web_keys.json de prioridade)
    key = ""
    from config import BASE_DIR
    web_keys_file = BASE_DIR / "web_keys.json"
    if web_keys_file.exists():
        try:
            wdata = json.loads(web_keys_file.read_text(encoding='utf-8'))
            key = wdata.get(provedor, "")
        except Exception:
            pass

    # Fallback para config_local/env
    if not key:
        if provedor == "claude":
            from config import ANTHROPIC_API_KEY
            key = ANTHROPIC_API_KEY
        elif provedor == "deepseek":
            import os
            key = os.environ.get("DEEPSEEK_API_KEY", "")

    if not key:
        return jsonify({
            "success": True,
            "valida": False,
            "status": "Não configurada",
            "mensagem": "Chave de API vazia ou não configurada.",
            "latency": 0.0
        })

    t0 = time.time()
    try:
        if provedor == "claude":
            url = "https://api.anthropic.com/v1/messages"
            headers = {
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
            payload = {
                "model": "claude-3-haiku-20240307",
                "max_tokens": 5,
                "messages": [{"role": "user", "content": "Ping"}]
            }
            r = requests.post(url, headers=headers, json=payload, timeout=10)
            elapsed = time.time() - t0
            if r.status_code == 200:
                return jsonify({"success": True, "valida": True, "status": "Conectada", "mensagem": "API respondendo corretamente.", "latency": elapsed})
            else:
                return jsonify({"success": True, "valida": False, "status": "Inválida", "mensagem": f"Erro HTTP {r.status_code}: {r.text[:200]}", "latency": elapsed})

        elif provedor == "deepseek":
            url = "https://api.deepseek.com/chat/completions"
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "deepseek-chat",
                "max_tokens": 5,
                "messages": [{"role": "user", "content": "Ping"}]
            }
            r = requests.post(url, headers=headers, json=payload, timeout=10)
            elapsed = time.time() - t0
            if r.status_code == 200:
                return jsonify({"success": True, "valida": True, "status": "Conectada", "mensagem": "API respondendo corretamente.", "latency": elapsed})
            else:
                return jsonify({"success": True, "valida": False, "status": "Inválida", "mensagem": f"Erro HTTP {r.status_code}: {r.text[:200]}", "latency": elapsed})

        else:
            return jsonify({"success": False, "error": f"Provedor desconhecido: {provedor}"}), 400

    except Exception as e:
        return jsonify({
            "success": True,
            "valida": False,
            "status": "Erro de conexão",
            "mensagem": str(e),
            "latency": time.time() - t0
        })


# ---------------------------------------------------------------------------
# PROMPT INTELLIGENCE — GERAÇÃO COM IA (DEEPSEEK) ASSÍNCRONA
# ---------------------------------------------------------------------------

_PROMPT_IA_JOBS: Dict[str, Dict[str, Any]] = {}

def _executar_prompt_ia_background(projeto_id: str, estilo_id: str, instrucao_custom: str,
                                   eeat_enabled: bool = False):
    """Executa o pipeline DeepSeek em background thread para não travar o servidor HTTP.

    Lira Studio v0.2.0 (Frente 2): garante o plano de cenas AQUI (fora do request
    handler), para que o POST /api/v2/.../gerar_prompts_ia responda em <50ms.
    """
    job = _PROMPT_IA_JOBS.setdefault(projeto_id, {
        "status": "executando",
        "etapa": "Iniciando análise...",
        "progresso": 0,
        "erro": None,
        "resultado": None,
        "iniciado_em": time.time()
    })
    job["status"] = "executando"
    job["erro"] = None
    job["progresso"] = 3
    job["etapa"] = "Garantindo plano de cenas..."
    job["eeat_enabled"] = eeat_enabled
    job["total_cenas"] = 0
    job["custo_estimado_usd"] = 0.0

    def progresso_cb(etapa: str, atual: int, total: int):
        job["etapa"] = etapa
        job["progresso"] = atual
        job["total"] = total

    try:
        # Garante o plano (só gera se não existir) — sem bloquear o HTTP handler.
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            scene_plan_svc.gerar_scene_plan(projeto_id, force=True)
            plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            job["status"] = "erro"
            job["erro"] = "Cenas não encontradas. Adicione áudio ou SRT primeiro."
            job["etapa"] = "Erro: cenas não encontradas"
            return

        log_event("DEEPSEEK", f"{projeto_id}: Iniciando geração de prompts - {len(plan['cenas'])} cenas")

        resultado = deepseek_svc.executar_pipeline_prompt_intelligence(
            projeto_id=projeto_id,
            estilo_id=estilo_id,
            instrucao_custom=instrucao_custom,
            callback_progresso=progresso_cb,
            status_holder=job,
            eeat_enabled=eeat_enabled,
        )
        job["status"] = "concluido"
        job["progresso"] = 100
        job["etapa"] = "Prompts gerados com sucesso!"
        job["resultado"] = resultado
        job["total_cenas"] = (resultado or {}).get("total_cenas") or job.get("total_cenas", 0)
        job["custo_estimado_usd"] = (resultado or {}).get("custo_estimado_usd", 0)
        job["concluido_em"] = time.time()
        n_cenas = (resultado or {}).get("total_cenas") or len(plan.get("cenas") or [])
        log_event("DEEPSEEK", f"{projeto_id}: Prompts concluídos - {n_cenas} cenas validadas")
    except Exception as e:
        job["status"] = "erro"
        job["erro"] = str(e)
        job["etapa"] = f"Erro: {e}"
        log_event("PROMPT_IA_ERRO", f"Erro background DeepSeek para '{projeto_id}': {e}", level="error")


@api_v2_bp.route("/projeto/<projeto_id>/gerar_prompts_ia", methods=["POST"])
def v2_gerar_prompts_ia(projeto_id: str):
    """
    Executa o DeepSeek Prompt Intelligence SEMPRE em background (Frente 2):
    o request responde em <50ms com {"status": "iniciado"} e o front acompanha
    via GET /api/v2/projeto/<id>/prompt_ia_status (polling).
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        estilo_id = data.get("estilo_id") or data.get("estilo_visual") or "photorealistic_cinematic"
        instrucao_custom = (data.get("instrucao_custom") or data.get("custom_prompt") or "").strip()
        eeat_enabled = bool(data.get("eeat_enabled", False))

        # Sempre assíncrono — o plano de cenas é garantido dentro do job (fora do handler).
        job = _PROMPT_IA_JOBS.get(projeto_id)
        if job and job.get("status") == "executando":
            return jsonify({
                "success": True,
                "status": "executando",
                "mensagem": "Geração já em andamento",
                "job": job
            })

        t = threading.Thread(
            target=_executar_prompt_ia_background,
            args=(projeto_id, estilo_id, instrucao_custom, eeat_enabled),
            daemon=True
        )
        t.start()

        return jsonify({
            "success": True,
            "status": "iniciado",
            "mensagem": "Geração de prompts com DeepSeek iniciada em segundo plano.",
            "job": _PROMPT_IA_JOBS.get(projeto_id)
        })
    except Exception as e:
        log_event("PROMPT_IA_ERRO", f"Erro no DeepSeek Prompt Intelligence para '{projeto_id}': {e}", level="error")
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/projeto/<projeto_id>/prompt_ia_status", methods=["GET"])
def v2_prompt_ia_status(projeto_id: str):
    """Consulta o status em tempo real do job assíncrono de IA."""
    job = _PROMPT_IA_JOBS.get(projeto_id)
    if not job:
        return jsonify({"success": True, "status": "idle", "progresso": 0})
    return jsonify({"success": True, **job})


@api_v2_bp.route("/projeto/<projeto_id>/cena/<int:cid>", methods=["GET"])
def v2_obter_cena(projeto_id: str, cid: int):
    """Retorna os dados de uma cena individual do plano de cenas."""
    plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    if not plan or not plan.get("cenas"):
        return jsonify({"success": False, "error": "Plano de cenas não encontrado"}), 404

    cena_encontrada = None
    cenas_lista = plan.get("cenas", [])
    # Busca estrita por scene_index primeiro (padrão dominante)
    for c in cenas_lista:
        s_idx = c.get("scene_index")
        if s_idx is not None and str(s_idx).strip() != "" and int(s_idx) == cid:
            cena_encontrada = c
            break
    # Fallback para id se não encontrou por scene_index e a cena não tem scene_index preenchido
    if cena_encontrada is None:
        for c in cenas_lista:
            if (c.get("scene_index") is None or str(c.get("scene_index")).strip() == "") and int(c.get("id", 0)) == cid:
                cena_encontrada = c
                break

    if cena_encontrada:
        return jsonify({"success": True, "cena": cena_encontrada})

    return jsonify({"success": False, "error": f"Cena {cid} não encontrada"}), 404


@api_v2_bp.route("/projeto/<projeto_id>/cena/<int:cid>/prompt", methods=["PATCH"])
def v2_editar_prompt_cena(projeto_id: str, cid: int):
    """Atualiza o prompt de uma cena individual mantendo a edição manual preservada."""
    data = request.get_json(force=True, silent=True) or {}
    novo_prompt = (data.get("prompt_imagem") or data.get("prompt") or "").strip()
    novo_anim = (data.get("prompt_animacao") or "").strip()

    if not novo_prompt:
        return jsonify({"success": False, "error": "Prompt não pode ser vazio"}), 400

    plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    if not plan or not plan.get("cenas"):
        return jsonify({"success": False, "error": "Plano de cenas não encontrado"}), 404

    cena_encontrada = None
    cenas_lista = plan.get("cenas", [])
    # Busca estrita por scene_index primeiro (padrão dominante)
    for c in cenas_lista:
        s_idx = c.get("scene_index")
        if s_idx is not None and str(s_idx).strip() != "" and int(s_idx) == cid:
            cena_encontrada = c
            break
    # Fallback para id se não encontrou por scene_index e a cena não tem scene_index preenchido
    if cena_encontrada is None:
        for c in cenas_lista:
            if (c.get("scene_index") is None or str(c.get("scene_index")).strip() == "") and int(c.get("id", 0)) == cid:
                cena_encontrada = c
                break

    if not cena_encontrada:
        return jsonify({"success": False, "error": f"Cena {cid} não encontrada"}), 404

    cena_encontrada["prompt_imagem"] = novo_prompt
    cena_encontrada["visual_prompt"] = novo_prompt
    if novo_anim:
        cena_encontrada["prompt_animacao"] = novo_anim
    cena_encontrada["status"] = scene_plan_svc.STATUS_PROMPT_PRONTO
    cena_encontrada["manual_intervention"] = True
    cena_encontrada["atualizado_em"] = datetime.now().isoformat(sep=" ", timespec="seconds")

    scene_plan_svc.salvar_scene_plan(projeto_id, plan)
    log_event("CENA_EDIT_PROMPT", f"[{projeto_id}] Cena {cid} editada manualmente", level="info")
    return jsonify({"success": True, "cena": cena_encontrada})


@api_v2_bp.route("/projeto/<projeto_id>/abrir_pasta_cenas", methods=["POST"])
def v2_abrir_pasta_cenas(projeto_id: str):
    """Abre a pasta de mídias baixadas (cenas/) no gerenciador de arquivos do sistema operacional."""
    pdir = _project_dir(projeto_id)
    cenas_dir = pdir / "cenas"
    cenas_dir.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt":
            os.startfile(str(cenas_dir))
        else:
            subprocess.Popen(["xdg-open", str(cenas_dir)])
        return jsonify({"success": True, "pasta": str(cenas_dir)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ===========================================================================
# 2. PRODUÇÃO v2
# ===========================================================================

@api_v2_bp.route("/producao/<projeto_id>/status", methods=["GET"])
def v2_producao_status(projeto_id: str):
    # ANTIGRAVITY Passo 3: REMOVIDO sincronizar_midias_encontradas() deste GET —
    # era executado a cada 3s pelo polling (tempestade de I/O no disco: ~300 ops
    # por ciclo em projetos de 60 cenas). A sincronização agora acontece apenas em
    # conclusão de download (iniciar_fila/auto-import) ou solicitação explícita de
    # montagem (/montagem/<id>/sincronizar e /montagem/<id>/exportar_capcut).
    progresso = scene_plan_svc.progresso_scene_plan(projeto_id)
    plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    cenas = plan.get("cenas", []) if plan else []

    # Verifica status do Flow via CDP e SSE
    cdp_conectado = False
    worker_status: Dict[str, Any] = {}
    try:
        from services.playwright_flow import FlowQueueWorker, _cdp_port_open
        status = FlowQueueWorker.get_status()
        worker_status = status if isinstance(status, dict) else {}
        cdp_conectado = bool(status.get("conectado", False)) or _cdp_port_open(9222)
    except Exception:
        pass

    # REQ 3 — motivo CANÔNICO da parada da fila + flag de interrupção (vêm do worker).
    # Sem isto o frontend só sabia QUE a fila parou, nunca POR QUÊ.
    _stop_requested = bool(worker_status.get("stop_requested", False))
    _pause_reason = str(worker_status.get("pause_reason") or "")

    flow_status = {"conectado": cdp_conectado, "modo": "CDP"}
    try:
        from app_web import _FLOW_STATE, _FLOW_QUEUES
        est = _FLOW_STATE.get(projeto_id, {})
        esta_conectado = cdp_conectado or bool(est.get("conectado", False)) or len(_FLOW_QUEUES) > 0
        flow_status = {
            "conectado": esta_conectado,
            "conta": est.get("conta", "Google Flow CDP (Porta 9222)"),
            "modo": "CDP",
            "fila_parada": bool(est.get("fila_parada", False)),
            # REQ 3 — mesma forma do SSE (consumido pelo banner/toast do frontend).
            "stop_requested": _stop_requested,
            "pause_reason": _pause_reason,
        }
    except Exception:
        pass
    # Garante as chaves mesmo quando o bloco de _FLOW_STATE falha.
    flow_status.setdefault("stop_requested", _stop_requested)
    flow_status.setdefault("pause_reason", _pause_reason)

    # PHASE 2 (ERRO 2): créditos reales de las cuentas + estado del fallback
    # video→imagen, para el banner/confirmación de la UI de producción.
    try:
        _contas_flow = flow_mgr.listar_contas_ativas()
        _creditos_total = sum(int(c.get("creditos_disponiveis", 0) or 0) for c in _contas_flow)
    except Exception:
        _contas_flow, _creditos_total = [], 0
    try:
        _alerta_meta = (_get_meta(proyecto_id) or {}).get("alerta_credito") or {}
    except Exception:
        _alerta_meta = {}
    _fallback_ativo = bool(_alerta_meta.get("fallback_video", False))
    try:
        from services.playwright_flow import FlowQueueWorker
        _w_st = FlowQueueWorker.get_status()
        _fallback_ativo = _fallback_ativo or bool(_w_st.get("fallback_video_imagen", False))
    except Exception:
        pass

    # Contagem granular por tipo de mídia (IMAGEM / IMAGEM+ANIMAR / VÍDEO / TEXTO)
    cnt_imagem = 0
    cnt_imagem_animar = 0
    cnt_video = 0
    cnt_texto = 0
    for c in cenas:
        t = c.get("tipo", "image")
        anim = bool(c.get("animate_later", c.get("animar_depois", c.get("animar", False))))
        if t == "text":
            cnt_texto += 1
        elif t == "video":
            cnt_video += 1
        elif anim:
            cnt_imagem_animar += 1
        else:
            cnt_imagem += 1

    contagem_midia = {
        "imagem": cnt_imagem,
        "imagem_animar": cnt_imagem_animar,
        "video": cnt_video,
        "texto": cnt_texto
    }

    # Análise de Retomada Inteligente (Smart Resume)
    # CONTAGEM EXCLUSIVA (correção FASE 3): cada cena cai em UMA única categoria.
    #   prontas  : arquivo real no disco (ou status BAIXADA) → não regenera
    #   erros    : status ERRO sem arquivo
    #   gerando  : status GERANDO/ENVIANDO/ENVIADA sem arquivo
    #   pendentes: status PENDENTE sem arquivo — NÃO inclui GERANDO nem ERRO
    cenas_prontas = []
    cenas_com_erro = []
    cenas_gerando = []
    cenas_pendentes = []
    pdir = scene_plan_svc._project_dir(projeto_id)

    for c in cenas:
        cid = int(c.get("id", 0))
        st = c.get("status", "")
        arq_disco = scene_plan_svc.resolver_arquivo_cena(projeto_id, cid, float(c.get("tempo_inicio", 0)))
        tem_arquivo = bool(arq_disco and arq_disco.exists() and arq_disco.stat().st_size > 500)
        if tem_arquivo or st == scene_plan_svc.STATUS_BAIXADA:
            cenas_prontas.append(cid)
        elif st == scene_plan_svc.STATUS_ERRO:
            cenas_com_erro.append(cid)
        elif st in (scene_plan_svc.STATUS_GERANDO, scene_plan_svc.STATUS_ENVIANDO, scene_plan_svc.STATUS_ENVIADA):
            cenas_gerando.append(cid)
        else:
            cenas_pendentes.append(cid)

    proxima_cid = cenas_pendentes[0] if cenas_pendentes else None
    resume_info = {
        "total": len(cenas),
        "prontas_count": len(cenas_prontas),
        "pendentes_count": len(cenas_pendentes),
        "gerando_count": len(cenas_gerando),
        "erros_count": len(cenas_com_erro),
        "proxima_cena_id": proxima_cid,
        "cenas_erro_ids": cenas_com_erro,
        "cenas_pendentes_ids": cenas_pendentes,
        "cenas_gerando_ids": cenas_gerando,
        "pode_retomar": len(cenas_prontas) > 0 and len(cenas_pendentes) > 0,
        "concluido": len(cenas_prontas) == len(cenas) and len(cenas) > 0
    }

    return jsonify({
        "success": True,
        "progresso": progresso,
        "contagem_midia": contagem_midia,
        "resume_info": resume_info,
        "cenas": cenas,
        "flow": flow_status,
        # REQ 3 — motivo da parada da fila exposto no /status: o frontend mostra a
        # mensagem correta (crédito de vídeo/imagem esgotado, fim de fila, manual)
        # em vez de simplesmente "a fila parou".
        "stop_requested": _stop_requested,
        "pause_reason": _pause_reason or "nao_pausado",
        # PHASE 2 (ERRO 2): expuestos para banner + diálogo de confirmación
        "creditos_restantes_total": _creditos_total,
        "fallback_video_ativo": _fallback_ativo,
        "contas_ativas": len(_contas_flow),
    })



@api_v2_bp.route("/producao/<projeto_id>/enviar_cena", methods=["POST"])
def v2_producao_enviar_cena(projeto_id: str):
    """Envia uma única cena para geração no Google Flow via extensão SSE."""
    data = request.get_json(force=True, silent=True) or {}
    scene_id = data.get("scene_id")
    if scene_id is None:
        return jsonify({"success": False, "error": "scene_id é obrigatório"}), 400

    try:
        from app_web import _FLOW_STATE, _FLOW_QUEUES
        est = _FLOW_STATE.setdefault(projeto_id, {})
        est["conectado"] = True
        est["ultimo_ping"] = time.time()

        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan:
            return jsonify({"success": False, "error": "Plano de cenas não encontrado"}), 404

        cena = next((c for c in plan.get("cenas", []) if int(c["id"]) == int(scene_id)), None)
        if not cena:
            return jsonify({"success": False, "error": f"Cena {scene_id} não encontrada"}), 404

        cid = int(scene_id)
        tipo_override = data.get("tipo")
        video_mode = (tipo_override == "video") if tipo_override else (cena.get("tipo") == "video")
        prompt = cena.get("prompt_animacao") if video_mode else (cena.get("prompt_imagem") or cena.get("texto", ""))

        scene_plan_svc.atualizar_status_cena(projeto_id, cid, scene_plan_svc.STATUS_ENVIADA)
        job_id = f"job-{'anim-' if video_mode else ''}{projeto_id}-{cid}-{int(time.time()*1000)}"
        msg = {
            "type": "LIRA_FLOW_JOB",
            "jobId": job_id,
            "projetoId": projeto_id,
            "sceneId": cid,
            "prompts": [prompt],
            "videoMode": video_mode
        }
        for q in _FLOW_QUEUES:
            try:
                q.put(msg)
            except Exception:
                pass

        return jsonify({"success": True, "scene_id": cid, "status": "enviada", "message": "Cena enviada para a extensão."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/producao/<projeto_id>/iniciar_fila", methods=["POST"])
def v2_producao_iniciar_fila(projeto_id: str, bypass_rate_limit: bool = False):
    try:
        # TAREFA 2 — EXCLUSÃO DO CAMINHO LEGADO: se a fila ASSÍNCRONA deste projeto
        # ainda tem item "enfileirada" ou "gerando", o caminho legado NÃO pode ser
        # iniciado (ele abre uma 2ª sessão Playwright disputando o mesmo Chrome).
        # Nada é iniciado: 409 com motivo explícito.
        from services.playwright_flow import _ler_fila as _ler_fila_guard
        _fila_guard = _ler_fila_guard(projeto_id, PROJETOS_DIR) or {}
        _itens_guard = _fila_guard.get("itens") if isinstance(_fila_guard.get("itens"), list) else []
        if any(
            isinstance(it, dict) and str(it.get("status") or "") in ("enfileirada", "gerando")
            for it in _itens_guard
        ):
            return jsonify({
                "success": False,
                # ANTIGRAVITY: MESMA condição do Bloqueio 2 (fila já ativa). O app.js
                # converte 'already_running' no toast informativo
                # "⚡ Produção já em andamento." em vez do alert() genérico.
                "already_running": True,
                # 'erro' é o campo pedido na especificação; 'error' é o MESMO texto no
                # campo que o app.js já consome (`r.error`) — sem ele o operador veria
                # apenas o alerta genérico "Não foi possível iniciar a fila.".
                "erro": "Fila assíncrona em andamento neste projeto; aguarde ou cancele.",
                "error": "Fila assíncrona em andamento neste projeto; aguarde ou cancele."
            }), 409

        import importlib
        import services.playwright_flow
        from services.playwright_flow import FlowQueueWorker, ensure_chrome_cdp
        if not FlowQueueWorker.get_worker().is_running_queue:
            try:
                services.playwright_flow = importlib.reload(services.playwright_flow)
                from services.playwright_flow import FlowQueueWorker, ensure_chrome_cdp
            except Exception:
                pass

        # Sincroniza estado com o disco para garantir retomada exata
        scene_plan_svc.sincronizar_midias_encontradas(projeto_id)
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            return jsonify({"success": False, "error": "Nenhuma cena disponível"}), 400

        data = request.get_json(silent=True) or {}
        custom_scene_ids = data.get("scene_ids")
        modo = data.get("modo", "imagem")
        # v0.3.6+: 'animacao' e 'animacao_apenas' são equivalentes — o worker roda
        # em modo só-vídeo e o filtro abaixo seleciona ESTRITAMENTE as cenas com
        # vídeo/animação necessária (cenas só-imagem nunca entram na fila de animação).
        e_animacao = modo in ("animacao", "animacao_apenas")
        modo_worker = "animacao" if e_animacao else modo

        pdir = scene_plan_svc._project_dir(projeto_id)
        if custom_scene_ids:
            cenas_pendentes = [int(sid) for sid in custom_scene_ids]
        else:
            # Seleciona apenas as cenas que REALMENTE não possuem arquivo no disco
            cenas_pendentes = []
            for c in plan["cenas"]:
                cid = int(c["id"])
                # animacao / animacao_apenas: filtra estritamente cenas de vídeo/animação pendente (nunca avatar)
                if modo in ("animacao", "animacao_apenas"):
                    eh_avatar = bool(
                        c.get("uses_character") is True
                        or c.get("scene_type") in ("avatar_talking", "avatar_action")
                        or "avatar" in str(c.get("narrative_role", "")).lower()
                        or "avatar" in str(c.get("visual_role", "")).lower()
                    )
                    if eh_avatar:
                        continue
                    precisa_animar = (
                        scene_plan_svc.tipo_efetivo_cena(c) == scene_plan_svc.TIPO_VIDEO
                        or c.get("animate_later") is True
                        or c.get("animar_depois") is True
                        or c.get("animar") is True
                        or c.get("tipo") == "video"
                    )
                    if not precisa_animar:
                        continue
                arq_disco = scene_plan_svc.resolver_arquivo_cena(projeto_id, cid, float(c.get("tempo_inicio", 0)))
                tem_arquivo = False
                if e_animacao:
                    if arq_disco and arq_disco.suffix.lower() in [".mp4", ".mov", ".webm"]:
                        tem_arquivo = True
                else:
                    tem_arquivo = bool(arq_disco and arq_disco.exists() and arq_disco.stat().st_size > 500)
                if not tem_arquivo:
                    cenas_pendentes.append(cid)

        # ------------------------------------------------------------------
        # ORÇAMENTO DE VÍDEO (v9.19) — limita quantos vídeos ESTE clique
        # enfileira. Antes, um clique em modo "animacao" enfileirava TODAS as
        # cenas elegíveis (206 itens) e esgotava os créditos de todas as contas
        # (Veo 3.1 Lite = 10 créditos; 5 contas = 25 vídeos/dia).
        # A escolha é por prioridade de retenção (prioridade_video >
        # secao_critica > retention_index), SEM alterar a ordem cronológica da
        # fila. Vale para as cenas filtradas E para `scene_ids` customizados.
        # Sem `max_videos` no body o comportamento é EXATAMENTE o de antes
        # (sem limite). Não grava nada: o lira_scene_plan.json fica intocado e
        # o ledger de custos continua em flow_account_manager.py.
        # Modo "imagem", ANIM_SKIP e o fallback reativo não são tocados aqui.
        # ------------------------------------------------------------------
        limite_videos = data.get("max_videos")
        videos_elegiveis = len(cenas_pendentes)
        if e_animacao and limite_videos is not None and cenas_pendentes:
            try:
                from services.video_budget import limitar_videos
                _cenas_por_id = {
                    int(c["id"]): c
                    for c in (plan.get("cenas") or [])
                    if isinstance(c, dict) and c.get("id") is not None
                }
                _objs_elegiveis = [
                    _cenas_por_id.get(int(cid), {"id": int(cid)})
                    for cid in cenas_pendentes
                ]
                _objs_selecionados = limitar_videos(_objs_elegiveis, limite_videos)
                cenas_pendentes = [int(c["id"]) for c in _objs_selecionados]
            except Exception as _e_budget:
                log_event(
                    "VIDEO_BUDGET",
                    f"{projeto_id}: falha ao aplicar o limite de vídeos "
                    f"({_e_budget}); seguindo SEM limite.",
                    level="warn",
                )
        videos_enfileirados = len(cenas_pendentes)
        if e_animacao:
            log_event(
                "VIDEO_BUDGET",
                f"{projeto_id}: videos_elegiveis={videos_elegiveis} "
                f"videos_enfileirados={videos_enfileirados} limite={limite_videos}",
                level="info",
            )

        # Reseta qualquer cena que estivesse marcada com status transitório prévio.
        # REQ (demora em "gerar restantes"/"Retomar Projeto"): UMA leitura + UMA
        # gravação para TODAS as cenas. Antes era `atualizar_status_cena()` POR CENA,
        # e cada chamada recarrega/regrava o lira_scene_plan.json inteiro sob o lock
        # de escrita — com 100+ cenas pendentes isso eram 2N operações de disco
        # (a causa dos 8-12s no clique). Nunca derruba o início da fila.
        if cenas_pendentes:
            try:
                _res_reset = scene_plan_svc.resetar_status_cenas(
                    projeto_id, cenas_pendentes, scene_plan_svc.STATUS_PENDENTE
                )
                if _res_reset.get("atualizadas"):
                    log_event("PRODUCAO",
                              f"{projeto_id}: {_res_reset['atualizadas']} cena(s) resetadas para "
                              f"PENDENTE em uma única gravação.", level="info")
            except Exception as _e_reset:
                log_event("PRODUCAO",
                          f"{projeto_id}: falha ao resetar status das cenas pendentes: {_e_reset}",
                          level="warn")

        import sys
        is_testing = getattr(current_app, "testing", False) or current_app.config.get("TESTING", False) or ("unittest" in sys.modules)
        if not is_testing:
            success, msg = ensure_chrome_cdp(9222)
            if not success:
                return jsonify({"success": False, "error": f"Chrome CDP falhou: {msg}"}), 500
        ok = True if is_testing else FlowQueueWorker.start_worker(
            projeto_id, scene_ids=cenas_pendentes or None, modo=modo_worker,
            bypass_rate_limit=bypass_rate_limit,
        )
        if not ok:
            worker = FlowQueueWorker.get_worker()
            if worker.is_running_queue:
                return jsonify({"success": False, "already_running": True, "message": "Fila já em execução. Aguarde ou reinicie o servidor.", "enfileiradas": len(cenas_pendentes)})
            return jsonify({"success": False, "error": "Falha ao iniciar (já em execução ou erro de conexão)."}), 409
        return jsonify({
            "success": True,
            "enfileiradas": len(cenas_pendentes),
            "proxima_cena_id": cenas_pendentes[0] if cenas_pendentes else None,
            # ORÇAMENTO DE VÍDEO (v9.19) — transparência do limite aplicado.
            "videos_elegiveis": videos_elegiveis,
            "videos_enfileirados": videos_enfileirados,
            "limite": limite_videos
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/producao/<projeto_id>/reclassificar_animacoes", methods=["POST"])
def v2_producao_reclassificar_animacoes(projeto_id: str):
    """Executa o Animation Director AI no roteiro/SRT para marcar com precisão as cenas a animar."""
    try:
        res = scene_plan_svc.reclassificar_animacoes_roteiro(projeto_id)
        return jsonify(res)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/producao/<projeto_id>/marcar_hook_video", methods=["POST"])
def v2_marcar_hook_video(projeto_id: str):
    """Marca cenas como animar=True e tipo=video para fila de B-Roll vídeo.

    Permite que o botão "B-Roll Vídeo" enfileire cenas que o reclassificador
    70/30 normalmente rejeitaria, sem passar pelo orçamentador.
    """
    data = request.get_json(silent=True) or {}
    scene_ids = [int(x) for x in (data.get("scene_ids") or [])]
    if not scene_ids:
        return jsonify({"success": False, "error": "scene_ids vazio"}), 400
    try:
        atualizadas = 0
        for cid in scene_ids:
            scene_plan_svc.atualizar_cena(projeto_id, cid, {
                "animar": True,
                "animate_later": True,
                "animar_depois": True,
                "tipo": "video",
                "media_intent": "video",
            })
            atualizadas += 1
        return jsonify({"success": True, "atualizadas": atualizadas})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/producao/<projeto_id>/retomar", methods=["POST"])
def v2_producao_retomar_fila(projeto_id: str):
    """Retoma a produção a partir exatamente da primeira cena pendente ou com erro."""
    return v2_producao_iniciar_fila(projeto_id)


# ITEM 8 — ALIAS explícito da retomada. Mesmo handler de /retomar, mas com
# `bypass_rate_limit=True`: uma retomada de projeto já em andamento não precisa da
# espera de 10s (rate-limit protection) que existe para INICIAR uma sequência nova.
# Rota separada (em vez de alterar /retomar) para não mudar comportamento existente.
@api_v2_bp.route("/producao/<projeto_id>/retomar_projeto", methods=["POST"])
def v2_producao_retomar_projeto(projeto_id: str):
    """Alias de /retomar SEM a espera inicial de 10s (rate-limit) da fila."""
    return v2_producao_iniciar_fila(projeto_id, bypass_rate_limit=True)


@api_v2_bp.route("/producao/<projeto_id>/retentar_erros", methods=["POST"])
def v2_producao_retentar_erros(projeto_id: str):
    """Re-executa apenas as cenas marcadas com erro no projeto."""
    try:
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            return jsonify({"success": False, "error": "Nenhuma cena disponível"}), 400

        cenas_erro = [int(c["id"]) for c in plan["cenas"] if c.get("status") == scene_plan_svc.STATUS_ERRO]
        if not cenas_erro:
            return jsonify({"success": True, "enfileiradas": 0, "message": "Nenhuma cena com erro encontrada."})

        from services.playwright_flow import FlowQueueWorker, ensure_chrome_cdp
        for cid in cenas_erro:
            scene_plan_svc.atualizar_status_cena(projeto_id, cid, scene_plan_svc.STATUS_PENDENTE)

        import sys
        is_testing = getattr(current_app, "testing", False) or current_app.config.get("TESTING", False) or ("unittest" in sys.modules)
        if not is_testing:
            success, msg = ensure_chrome_cdp(9222)
            if not success:
                return jsonify({"success": False, "error": f"Chrome CDP falhou: {msg}"}), 500
        ok = True if is_testing else FlowQueueWorker.start_worker(projeto_id, scene_ids=cenas_erro, modo="imagem")
        return jsonify({"success": ok, "enfileiradas": len(cenas_erro), "scene_ids": cenas_erro})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ===========================================================================
# ENDPOINTS DA FILA DE PRODUÇÃO (BLOCO 2 & BLOCO 4)
# ===========================================================================

@api_v2_bp.route("/producao/<projeto_id>/fila/adicionar", methods=["POST"])
def v2_producao_fila_adicionar(projeto_id: str):
    """
    Enfileira cenas para geração de imagens no Google Flow via FlowQueueWorker.
    Valida existência das cenas no lira_scene_plan.json e preenchimento de prompt visual.
    Executa leitura, alteração e salvamento de flow_queue.json sob transacao_fila atômica.
    """
    from services.playwright_flow import transacao_fila, legacy_queue_ativa

    try:
        if legacy_queue_ativa():
            return jsonify({
                "success": False,
                "erro": "Fila legada em andamento; aguarde a conclusão.",
                "error": "Fila legada em andamento; aguarde a conclusão."
            }), 409

        data = request.get_json(silent=True) or {}
        scene_ids = data.get("scene_ids", [])
        modo = str(data.get("modo") or "imagem").lower()

        if not isinstance(scene_ids, list) or len(scene_ids) == 0:
            return jsonify({"success": False, "error": "scene_ids deve ser uma lista não vazia de IDs de cena"}), 400

        plano = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plano or not plano.get("cenas"):
            return jsonify({"success": False, "error": f"Plano de cenas não encontrado para o projeto '{projeto_id}'"}), 400

        cenas_map = {}
        for c in plano.get("cenas", []):
            try:
                cenas_map[int(c.get("id", 0))] = c
            except Exception:
                pass

        # Validação fora da transação: todas as cenas devem existir e ter prompt
        for sid in scene_ids:
            try:
                cid = int(sid)
            except Exception:
                return jsonify({"success": False, "error": f"ID de cena inválido: {sid}"}), 400

            if cid not in cenas_map:
                return jsonify({"success": False, "error": f"Cena {cid} não encontrada no plano de cenas"}), 400

            cena = cenas_map[cid]
            if modo in ("video", "animacao"):
                prompt = (cena.get("prompt_animacao") or cena.get("visual_prompt") or cena.get("prompt_imagem") or cena.get("texto") or "").strip()
                if not prompt:
                    try:
                        from services.deepseek_prompt_service import _montar_prompt_animacao_fallback
                        prompt = _montar_prompt_animacao_fallback(cena.get("texto") or cena.get("narration") or "")
                    except Exception:
                        prompt = ""
            else:
                prompt = (cena.get("visual_prompt") or cena.get("prompt_imagem") or cena.get("texto") or "").strip()

            if not prompt:
                return jsonify({"success": False, "error": f"Cena {cid} não possui prompt preenchido para o modo {modo}"}), 400

        novos_enfileirados = 0
        ignoradas = 0
        agora = datetime.now().isoformat()

        # TAREFA 3d — ANTES da transação: cura itens "gerando" órfãos (sem execução
        # viva) para que uma cena interrompida por queda/cancelamento não fique presa.
        try:
            from services.playwright_flow import _curar_orfaos_fila as _curar_orfaos
            _curados_add = _curar_orfaos(projeto_id, PROJETOS_DIR)
            if _curados_add:
                log_event("PRODUCAO", f"{projeto_id}: itens órfãos curados antes de enfileirar: {_curados_add}", level="warn")
        except Exception as _e_cura_add:
            log_event("PRODUCAO", f"{projeto_id}: falha na cura de órfãos: {_e_cura_add}", level="warn")

        # Única transação atômica para leitura -> alteração -> salvamento
        with transacao_fila(projeto_id, PROJETOS_DIR) as fila:
            itens = fila.get("itens") if isinstance(fila.get("itens"), list) else []
            itens_map = {int(it.get("scene_id") or it.get("id") or 0): it for it in itens}

            for sid in scene_ids:
                cid = int(sid)
                cena = cenas_map[cid]
                if modo in ("video", "animacao"):
                    prompt = (cena.get("prompt_animacao") or cena.get("visual_prompt") or cena.get("prompt_imagem") or cena.get("texto") or "").strip()
                    if not prompt:
                        try:
                            from services.deepseek_prompt_service import _montar_prompt_animacao_fallback
                            prompt = _montar_prompt_animacao_fallback(cena.get("texto") or cena.get("narration") or "")
                        except Exception:
                            prompt = ""
                else:
                    prompt = (cena.get("visual_prompt") or cena.get("prompt_imagem") or cena.get("texto") or "").strip()

                if cid in itens_map:
                    it = itens_map[cid]
                    st = it.get("status")
                    if st in ("enfileirada", "gerando", "pendente"):
                        ignoradas += 1
                        continue
                    # Se existe com status "concluida" ou "erro", reseta in place
                    it["status"] = "enfileirada"
                    it["prompt"] = prompt
                    it["modo"] = modo
                    it["tentativa_atual"] = 0
                    it["attempt_count"] = 0
                    it["max_tentativas"] = 3
                    it["ultimo_erro"] = None
                    it["error_msg"] = ""
                    it["enfileirada_em"] = agora
                    it["iniciada_em"] = None
                    it["started_at"] = None
                    it["completed_at"] = None
                    it["concluida_em"] = None
                    novos_enfileirados += 1
                else:
                    item = {
                        "scene_id": cid,
                        "id": cid,
                        "posicao": len(itens) + 1,
                        "modo": modo,
                        "status": "enfileirada",
                        "tentativa_atual": 0,
                        "attempt_count": 0,
                        "max_tentativas": 3,
                        "enfileirada_em": agora,
                        "iniciada_em": None,
                        "started_at": None,
                        "completed_at": None,
                        "concluida_em": None,
                        "ultimo_erro": None,
                        "error_msg": "",
                        "prompt": prompt
                    }
                    itens.append(item)
                    itens_map[cid] = item
                    novos_enfileirados += 1

            fila["itens"] = itens
            fila["total"] = len(itens)
            fila["ativa"] = True
            fila["atualizado_em"] = agora
            total_final = len(itens)

        # Acorda worker thread fora da transação
        acordar_worker(projeto_id)

        return jsonify({
            "success": True,
            "enfileiradas": novos_enfileirados,
            "ignoradas": ignoradas,
            "total_fila": total_final
        }), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/producao/<projeto_id>/fila_status", methods=["GET"])
def v2_producao_fila_status(projeto_id: str):
    """
    Retorna o snapshot atual da fila de geração (flow_queue.json) com contadores
    e lista de itens para polling da UI.
    """
    from services.playwright_flow import _ler_fila

    try:
        fila = _ler_fila(projeto_id, PROJETOS_DIR)
        plano = scene_plan_svc.carregar_scene_plan(projeto_id) or {}
        cenas_plano = {int(c.get("id", 0)): c for c in plano.get("cenas", [])}

        itens = fila.get("itens") if isinstance(fila.get("itens"), list) else []
        total = len(itens)

        # Contadores calculados estritamente sobre o escopo dos itens de flow_queue.json
        pendentes = sum(1 for it in itens if it.get("status") in ("enfileirada", "pendente"))
        gerando = sum(1 for it in itens if it.get("status") == "gerando")
        concluidas = sum(
            1 for it in itens
            if it.get("status") == "concluida" or (
                it.get("status") not in ("enfileirada", "pendente", "gerando")
                and cenas_plano.get(int(it.get("scene_id") or 0), {}).get("status") == "BAIXADA"
            )
        )
        erros = sum(
            1 for it in itens
            if it.get("status") == "erro" or (
                it.get("status") not in ("enfileirada", "pendente", "gerando")
                and cenas_plano.get(int(it.get("scene_id") or 0), {}).get("image_status") == "ERROR"
            )
        )

        cena_em_andamento = None
        if fila.get("cena_ativa") and isinstance(fila["cena_ativa"], dict):
            cena_em_andamento = fila["cena_ativa"].get("scene_id")
        else:
            for it in itens:
                if it.get("status") == "gerando":
                    cena_em_andamento = it.get("scene_id")
                    break

        # TAREFA 4 — POLLING NÃO PODE CRIAR WORKERS: consulta tanto FlowQueueWorker
        # (fila de refazer por projeto) quanto PlaywrightCDPWorker (fila em lote)
        from services.playwright_flow import FlowQueueWorker as _FlowQueueWorkerPeek
        w = _FlowQueueWorkerPeek.peek_queue_worker(projeto_id)
        cdp_w = _FlowQueueWorkerPeek.get_worker()
        cdp_ativo = bool(
            cdp_w and getattr(cdp_w, "is_running_queue", False)
            and getattr(cdp_w, "current_project_id", None) == projeto_id
        )
        fila_ativa = bool((w and w.is_active()) or cdp_ativo or fila.get("ativa", False))
        pausada = bool(
            fila.get("pausada", False) or
            (cdp_ativo and getattr(cdp_w, "stop_requested", None) is not None and cdp_w.stop_requested.is_set())
        )
        if not cena_em_andamento and cdp_ativo and getattr(cdp_w, "cena_ativa", None) and isinstance(cdp_w.cena_ativa, dict):
            cena_em_andamento = cdp_w.cena_ativa.get("scene_id")

        itens_formatados = []
        for it in itens:
            itens_formatados.append({
                "scene_id": it.get("scene_id"),
                "id": it.get("scene_id"),
                "posicao": it.get("posicao"),
                "status": it.get("status"),
                "modo": it.get("modo", "imagem"),
                "tentativa_atual": it.get("attempt_count") or it.get("tentativa_atual") or 0,
                "max_tentativas": it.get("max_tentativas", 3),
                "iniciada_em": it.get("iniciada_em") or it.get("started_at"),
                "concluida_em": it.get("concluida_em") or it.get("completed_at"),
                "ultimo_erro": it.get("error_msg") or it.get("ultimo_erro"),
                "caminho_imagem": it.get("caminho_imagem")
            })

        return jsonify({
            "sucesso": True,
            "success": True,
            "total": total,
            "concluidas": concluidas,
            "pendentes": pendentes,
            "gerando": gerando,
            "erros": erros,
            "fila_ativa": fila_ativa,
            "pausada": pausada,
            "cena_em_andamento": cena_em_andamento,
            "itens": itens_formatados
        }), 200

    except Exception as e:
        return jsonify({"sucesso": False, "success": False, "error": str(e)}), 500


@api_v2_bp.route("/producao/<projeto_id>/fila/pausar", methods=["POST"])
def v2_producao_fila_pausar(projeto_id: str):
    """Pausa o processamento da fila sem interromper a cena em andamento."""
    from services.playwright_flow import transacao_fila

    try:
        with transacao_fila(projeto_id, PROJETOS_DIR) as fila:
            fila["pausada"] = True
            fila["motivo_pausa"] = "Pausado pelo usuário"

        w = get_or_create_worker(projeto_id)
        if w:
            w.pausada = True

        return jsonify({"success": True, "pausada": True}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/producao/<projeto_id>/fila/retomar", methods=["POST"])
def v2_producao_fila_retomar(projeto_id: str):
    """Retoma o processamento da fila que estava pausada."""
    from services.playwright_flow import transacao_fila

    try:
        with transacao_fila(projeto_id, PROJETOS_DIR) as fila:
            fila["pausada"] = False
            fila["motivo_pausa"] = None
            fila["ativa"] = True

        w = get_or_create_worker(projeto_id)
        if w:
            w.pausada = False

        # TAREFA 3d — cura itens "gerando" órfãos ANTES de acordar o worker
        # (uma cena presa por queda/cancelamento volta a ser processada).
        try:
            from services.playwright_flow import _curar_orfaos_fila as _curar_orfaos_ret
            _curados_ret = _curar_orfaos_ret(projeto_id, PROJETOS_DIR)
            if _curados_ret:
                log_event("PRODUCAO", f"{projeto_id}: itens órfãos curados na retomada: {_curados_ret}", level="warn")
        except Exception as _e_cura_ret:
            log_event("PRODUCAO", f"{projeto_id}: falha na cura de órfãos na retomada: {_e_cura_ret}", level="warn")

        acordar_worker(projeto_id)
        return jsonify({"success": True, "pausada": False}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/producao/<projeto_id>/fila/cancelar", methods=["POST"])
def v2_producao_fila_cancelar(projeto_id: str):
    """Cancela a fila e esvazia todos os itens pendentes."""
    from services.playwright_flow import transacao_fila

    try:
        itens_anteriores = 0
        cenas_gerando = []
        with transacao_fila(projeto_id, PROJETOS_DIR) as fila:
            itens_anteriores = len(fila.get("itens", []))
            # TAREFA 4a — ANTES de esvaziar, coleta as cenas em "gerando": a
            # execução que está em voo pode terminar DEPOIS do cancelamento.
            for it in (fila.get("itens") or []):
                if isinstance(it, dict) and it.get("status") == "gerando":
                    try:
                        cenas_gerando.append(int(it.get("scene_id") or it.get("id") or 0))
                    except Exception:
                        pass
            fila["itens"] = []
            fila["ativa"] = False
            fila["cena_ativa"] = None
            fila["pausada"] = False

        # TAREFA 4a — fora da transação: cenas que estavam GERANDO no plan voltam
        # para PENDENTE/IMAGE_PENDING (se a execução em voo concluir com sucesso, a
        # Fase D do worker regrava o plan como BAIXADA/READY).
        from services.playwright_flow import sincronizar_cenas_para_pendente
        try:
            _n_pend = sincronizar_cenas_para_pendente(projeto_id, cenas_gerando)
            if _n_pend:
                log_event("PRODUCAO", f"{projeto_id}: {_n_pend} cena(s) em GERANDO voltaram para PENDENTE após o cancelamento.", level="info")
        except Exception as _e_pend:
            log_event("PRODUCAO", f"{projeto_id}: falha ao voltar cenas GERANDO para PENDENTE: {_e_pend}", level="warn")

        w = get_or_create_worker(projeto_id)
        if w:
            w.cancelar_execucao()

        return jsonify({
            "success": True,
            "fila_esvaziada": True,
            "itens_removidos": itens_anteriores
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/producao/<projeto_id>/cenas/<int:cena_id>/refazer", methods=["POST"])
def v2_producao_cena_refazer(projeto_id: str, cena_id: int):
    """
    Refaz uma cena existente no Google Flow:
    1. Move arquivos físicos de mídia existentes da cena para _backup_refazer/.
    2. Reseta o status da cena no lira_scene_plan.json para GERANDO.
    3. Adiciona ou reseta o item correspondente em flow_queue.json com status 'enfileirada' sob transacao_fila.
    4. Acorda a worker thread para processamento assíncrono.
    """
    from services.playwright_flow import transacao_fila, legacy_queue_ativa

    try:
        if legacy_queue_ativa():
            return jsonify({
                "success": False,
                "erro": "Fila legada em andamento; aguarde a conclusão.",
                "error": "Fila legada em andamento; aguarde a conclusão."
            }), 409

        data = request.get_json(silent=True) or {}
        modo = str(data.get("modo") or "imagem").lower()

        plano = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plano or not plano.get("cenas"):
            return jsonify({"success": False, "error": f"Plano de cenas não encontrado para o projeto '{projeto_id}'"}), 400

        cena_alvo = None
        for c in plano.get("cenas", []):
            s_idx = c.get("scene_index")
            if s_idx is not None and str(s_idx).strip() != "" and int(s_idx) == int(cena_id):
                cena_alvo = c
                break
        if cena_alvo is None:
            for c in plano.get("cenas", []):
                if (c.get("scene_index") is None or str(c.get("scene_index")).strip() == "") and int(c.get("id", 0)) == int(cena_id):
                    cena_alvo = c
                    break

        if not cena_alvo:
            return jsonify({"success": False, "error": f"Cena {cena_id} não encontrada no plano de cenas"}), 404

        if modo in ("video", "animacao"):
            prompt = (cena_alvo.get("prompt_animacao") or cena_alvo.get("visual_prompt") or cena_alvo.get("prompt_imagem") or cena_alvo.get("texto") or "").strip()
            if not prompt:
                try:
                    from services.deepseek_prompt_service import _montar_prompt_animacao_fallback
                    prompt = _montar_prompt_animacao_fallback(cena_alvo.get("texto") or cena_alvo.get("narration") or "")
                except Exception:
                    prompt = ""
        else:
            prompt = (cena_alvo.get("visual_prompt") or cena_alvo.get("prompt_imagem") or cena_alvo.get("texto") or "").strip()

        if not prompt:
            return jsonify({"success": False, "error": f"Cena {cena_id} não possui prompt preenchido para o modo {modo}"}), 400

        # 1. Arquivar mídias antigas em _backup_refazer/ e atualizar status para GERANDO no scene_plan
        res_arq = scene_plan_svc.arquivar_midias_antigas_cena(projeto_id, cena_id)
        if not res_arq.get("success"):
            return jsonify({"success": False, "error": res_arq.get("error", "Falha ao arquivar mídia antiga")}), 500

        # TAREFA 3d — cura itens órfãos antes da transação
        try:
            from services.playwright_flow import _curar_orfaos_fila as _curar_orfaos
            _curados = _curar_orfaos(projeto_id, PROJETOS_DIR)
            if _curados:
                log_event("PRODUCAO", f"{projeto_id}: itens órfãos curados antes de refazer cena {cena_id}: {_curados}", level="warn")
        except Exception as _e_cura:
            log_event("PRODUCAO", f"{projeto_id}: falha na cura de órfãos: {_e_cura}", level="warn")

        # 2. Transação atômica em flow_queue.json
        agora = datetime.now().isoformat()
        cid = int(cena_id)
        with transacao_fila(projeto_id, PROJETOS_DIR) as fila:
            itens = fila.get("itens") if isinstance(fila.get("itens"), list) else []
            if not fila.get("ativa") and not any(it.get("status") in ("gerando", "enfileirada", "pendente") for it in itens):
                itens = []
            itens_map = {int(it.get("scene_id") or it.get("id") or 0): it for it in itens}

            if cid in itens_map:
                it = itens_map[cid]
                it["status"] = "enfileirada"
                it["prompt"] = prompt
                it["modo"] = modo
                it["tentativa_atual"] = 0
                it["attempt_count"] = 0
                it["max_tentativas"] = 3
                it["ultimo_erro"] = None
                it["error_msg"] = ""
                it["enfileirada_em"] = agora
                it["iniciada_em"] = None
                it["started_at"] = None
                it["completed_at"] = None
                it["concluida_em"] = None
            else:
                item = {
                    "scene_id": cid,
                    "id": cid,
                    "posicao": len(itens) + 1,
                    "modo": modo,
                    "status": "enfileirada",
                    "tentativa_atual": 0,
                    "attempt_count": 0,
                    "max_tentativas": 3,
                    "enfileirada_em": agora,
                    "iniciada_em": None,
                    "started_at": None,
                    "completed_at": None,
                    "concluida_em": None,
                    "ultimo_erro": None,
                    "error_msg": "",
                    "prompt": prompt
                }
                itens.append(item)

            fila["itens"] = itens
            fila["total"] = len(itens)
            fila["ativa"] = True
            fila["pausada"] = False
            fila["atualizado_em"] = agora
            total_final = len(itens)

        # 3. Acorda worker fora da transação
        acordar_worker(projeto_id)

        log_event("PRODUCAO", f"{projeto_id}: cena {cena_id} reenviada para refazer no Flow ({len(res_arq.get('arquivados', []))} arquivos arquivados)")

        return jsonify({
            "success": True,
            "cena_id": cid,
            "scene_id": cid,
            "modo": modo,
            "status": "GERANDO",
            "total_fila": total_final,
            "arquivos_arquivados": res_arq.get("arquivados", [])
        }), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500



# BLOCO 3B (aprovado) — estado em memória para detectar transições do worker.
# Guarda o último status observado por projeto para registrar WORKER: antigo → novo.
_WORKER_STATUS_ANTERIOR: dict = {}


def _transicao_status_worker(worker, cena_ativa: dict, baixadas: int, erro: int):
    """Registra via log_event transições de status do worker (BLOCO 3B).

    Emite somente quando observa mudança de estado, evitando spam de log:
      - WORKER: {status_anterior} → {status_novo}
      - FLOW_RESPOSTA: Cena {cid} OK  (quando conclui)
      - FLOW_ERRO: Cena {cid} — {motivo}  (quando erro)
    """
    from services.event_logger import log_event

    cid = int(cena_ativa.get("scene_id") or 0)
    status_novo = cena_ativa.get("status") or ("PARADO" if not getattr(worker, "is_running_queue", False) else "GERANDO")
    chave = getattr(worker, "current_project_id", "") or ""

    prev = _WORKER_STATUS_ANTERIOR.get(chave)
    if prev != status_novo:
        _WORKER_STATUS_ANTERIOR[chave] = status_novo
        log_event(
            "PLAYWRIGHT_FLOW",
            f"WORKER: {prev or 'PARADO'} → {status_novo}",
            level="info",
        )

    # Respostas de status da cena (transições de andamento)
    if cid:
        if status_novo in ("CONCLUIDO", "SUCESSO") and prev not in ("CONCLUIDO", "SUCESSO"):
            log_event("PLAYWRIGHT_FLOW", f"FLOW_RESPOSTA: Cena {cid} OK", level="info")
        elif status_novo in ("ERRO", "FALHA") and prev not in ("ERRO", "FALHA"):
            motivo = cena_ativa.get("etapa") or cena_ativa.get("msg") or "erro não especificado"
            log_event("PLAYWRIGHT_FLOW", f"FLOW_ERRO: Cena {cid} — {motivo}", level="error")


@api_v2_bp.route("/producao/<projeto_id>/live_console", methods=["GET"])
def v2_producao_live_console(projeto_id: str):
    """Retorna estado do worker em tempo real, métricas de tempo e logs do terminal."""
    try:
        from services.playwright_flow import FlowQueueWorker
        from services.event_logger import ler_eventos

        worker = FlowQueueWorker.get_worker()
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        cenas = plan.get("cenas", []) if plan else []

        # CONTAGEM EXCLUSIVA (correção FASE 3): cada cena cai em UMA única categoria.
        #   baixadas : status em status_prontos (BAIXADA/GERADA/READY/...)
        #   gerando  : status GERANDO/ENVIANDO
        #   erro     : status ERRO
        #   pendentes: status PENDENTE (igualdade EXATA — não inclui gerando/erro)
        #   outros   : status residual (guarda de invariante soma == total)
        # Antes: pendentes = total - baixadas → contava GERANDO e ERRO em DUAS
        # categorias ao mesmo tempo (gerando+pendentes, erro+pendentes).
        # Nota: STATUS_ENVIADA é alias de STATUS_ENVIANDO (scene_plan_service),
        # por isso usamos por_status (contagem única) em vez de somar o tuple.
        total = len(cenas)
        prog = scene_plan_svc.progresso_scene_plan(projeto_id)
        por_status = prog.get("por_status", {}) or {}
        baixadas = prog.get("prontas", 0)  # soma de status_prontos (BAIXADA/GERADA/READY/...)
        gerando = (
            por_status.get(scene_plan_svc.STATUS_GERANDO, 0)
            + por_status.get(scene_plan_svc.STATUS_ENVIANDO, 0)
        )
        erro = por_status.get(scene_plan_svc.STATUS_ERRO, 0)
        pendentes = por_status.get(scene_plan_svc.STATUS_PENDENTE, 0)
        # Residual: mantém total == baixadas + gerando + erro + pendentes + outros.
        outros = max(0, total - baixadas - gerando - erro - pendentes)

        cena_ativa = dict(worker.cena_ativa) if worker.cena_ativa else {}
        if cena_ativa and cena_ativa.get("inicio_ts"):
            cena_ativa["tempo_decorrido"] = time.time() - cena_ativa["inicio_ts"]
        if worker.queue_start_time and worker.is_running_queue:
            cena_ativa["tempo_total"] = time.time() - worker.queue_start_time

        # BLOCO 3B (aprovado) — registra em tempo real eventos de status do worker via log_event.
        # Não requisita produção externa; apenas observa transições de estado já reportadas.
        try:
            _transicao_status_worker(worker, cena_ativa, baixadas, erro)
        except Exception:
            pass

        logs_raw = ler_eventos(linhas=120)
        logs_fmt = []
        categorias_permitidas = {
            "PLAYWRIGHT_FLOW", "SCENE_PLAN", "FLOW", "VISUAL_DIRECTOR", "WEB", "SYSTEM",
            "TRANSCRIBE", "ROTEIRO", "AUDIO", "PRODUCAO", "ANIMATION_DIRECTOR", "BGM",
            "NAVEGACAO", "FLOW_CONTAS", "PIPELINE", "AVATAR", "PERSONAGEM"
        }
        for ev in logs_raw:
            cat = (ev.get("category") or "").upper()
            if cat in categorias_permitidas or not cat:
                raw_msg = ev.get("message", "")
                if "Locator.click: Timeout" in raw_msg or "intercepts pointer events" in raw_msg:
                    clean_msg = "Lentidão temporária no Google Flow. Liberando tela e retentando..."
                elif "TimeoutError" in raw_msg and "wait_for_selector" in raw_msg:
                    clean_msg = "Aguardando resposta da interface do Google Flow..."
                elif len(raw_msg) > 300 and ("Traceback" in raw_msg or "Call log:" in raw_msg):
                    clean_msg = raw_msg.splitlines()[0]
                else:
                    clean_msg = raw_msg

                logs_fmt.append({
                    "ts": ev.get("ts", "")[-8:] if ev.get("ts") else "",
                    "level": ev.get("level", "INFO"),
                    "category": cat or "SISTEMA",
                    "message": clean_msg
                })

        # Metadados de sessão e delay em tempo real
        from services.playwright_flow import carregar_projeto_flow_url
        project_url = carregar_projeto_flow_url(projeto_id)
        if not project_url and getattr(worker, "page", None):
            try:
                if not worker.page.is_closed():
                    project_url = worker.page.url
            except Exception:
                pass

        account_email = getattr(worker, "account_email", None)
        project_name = getattr(worker, "current_project_name", None) or projeto_id

        delay_info = getattr(worker, "current_delay_info", None)
        current_delay = None
        if delay_info and delay_info.get("inicio_ts"):
            decorrido = time.time() - delay_info["inicio_ts"]
            current_delay = max(0.0, round(delay_info.get("delay_total", 5) - decorrido, 1))

        return jsonify({
            "success": True,
            "worker": {
                "is_running": worker.is_running_queue,
                "model": worker.current_model,
                "cena_ativa": cena_ativa,
                "project_url": project_url,
                "project_name": project_name,
                "account_email": account_email,
                "current_delay": current_delay,
            },
            "stats": {
                "total": total,
                "baixadas": baixadas,
                "gerando": gerando,
                "pendentes": max(0, pendentes),
                "erro": erro,
                # 'outros' = status residual (fora das 4 categorias); normal = 0
                "outros": outros,
                "pct": round((baixadas / total * 100) if total > 0 else 0, 1)
            },
            "logs": logs_fmt
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/console/logs_globais", methods=["GET"])
def v2_console_logs_globais():
    """Retorna os eventos mais recentes do sistema para exibição contínua no HUD (mesmo sem projeto aberto)."""
    try:
        from services.event_logger import ler_eventos
        logs_raw = ler_eventos(linhas=80)
        logs_fmt = []
        categorias_permitidas = {
            "PLAYWRIGHT_FLOW", "SCENE_PLAN", "FLOW", "VISUAL_DIRECTOR", "WEB", "SYSTEM",
            "TRANSCRIBE", "ROTEIRO", "AUDIO", "PRODUCAO", "ANIMATION_DIRECTOR", "BGM",
            "NAVEGACAO", "FLOW_CONTAS", "PIPELINE", "AVATAR", "PERSONAGEM"
        }
        for ev in logs_raw:
            cat = (ev.get("category") or "").upper()
            if cat in categorias_permitidas or not cat:
                raw_msg = ev.get("message", "")
                if "Locator.click: Timeout" in raw_msg or "intercepts pointer events" in raw_msg:
                    clean_msg = "Lentidão temporária no Google Flow. Retentando..."
                elif "TimeoutError" in raw_msg and "wait_for_selector" in raw_msg:
                    clean_msg = "Aguardando resposta da interface do Google Flow..."
                elif len(raw_msg) > 300 and ("Traceback" in raw_msg or "Call log:" in raw_msg):
                    clean_msg = raw_msg.splitlines()[0]
                else:
                    clean_msg = raw_msg

                logs_fmt.append({
                    "ts": ev.get("ts", "")[-8:] if ev.get("ts") else "",
                    "level": ev.get("level", "INFO"),
                    "category": cat or "SISTEMA",
                    "message": clean_msg
                })
        return jsonify({"success": True, "logs": logs_fmt})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "logs": []}), 500



@api_v2_bp.route("/producao/<projeto_id>/auto_importar", methods=["POST"])
def v2_producao_auto_importar(projeto_id: str):
    """Executa a rotina de auto-importação e pareamento de mídias geradas com cenas."""
    try:
        from services.playwright_flow import auto_importar_midias_projeto
        resultado = auto_importar_midias_projeto(projeto_id)
        return jsonify({"success": True, "resultado": resultado})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ===========================================================================
# 3. ARQUIVOS v2
# ===========================================================================

@api_v2_bp.route("/arquivos/<projeto_id>/listar", methods=["GET"])
def v2_arquivos_listar(projeto_id: str):
    """
    Lista todos os arquivos organizados por pastas do Studio 2.0:
    audio/, srt/, imagens/, videos/, prompts/, capcut/ e raiz.
    """
    project_dir = _project_dir(projeto_id)
    if not project_dir.exists():
        return jsonify({"success": False, "error": "Projeto não encontrado"}), 404

    scene_plan_svc.garantir_estrutura_pastas(projeto_id)
    estrutura = {}
    total_count = 0

    for pasta in scene_plan_svc.PASTAS_PROJETO_V2:
        p_dir = project_dir / pasta
        arquivos = []
        if p_dir.exists():
            for f in sorted(p_dir.iterdir()):
                if f.is_file():
                    stat = f.stat()
                    arquivos.append({
                        "nome": f.name,
                        "tamanho_bytes": stat.st_size,
                        "tamanho_kb": round(stat.st_size / 1024, 1),
                        "modificado_em": datetime.fromtimestamp(stat.st_mtime).isoformat(sep=" ", timespec="seconds"),
                        "caminho_relativo": f"{pasta}/{f.name}",
                    })
                    total_count += 1
        estrutura[pasta] = arquivos

    # Arquivos na raiz do projeto (JSONs e legados)
    raiz_arquivos = []
    for f in sorted(project_dir.iterdir()):
        if f.is_file():
            stat = f.stat()
            raiz_arquivos.append({
                "nome": f.name,
                "tamanho_bytes": stat.st_size,
                "tamanho_kb": round(stat.st_size / 1024, 1),
                "modificado_em": datetime.fromtimestamp(stat.st_mtime).isoformat(sep=" ", timespec="seconds"),
                "caminho_relativo": f.name,
            })
            total_count += 1
    estrutura["raiz"] = raiz_arquivos

    return jsonify({
        "success": True,
        "projeto_id": projeto_id,
        "total_arquivos": total_count,
        "estrutura": estrutura,
    })


@api_v2_bp.route("/arquivos/<projeto_id>/download/<categoria>/<path:nome_arquivo>", methods=["GET"])
def v2_arquivos_download(projeto_id: str, categoria: str, nome_arquivo: str):
    """Serve um arquivo específico da estrutura de pastas do projeto."""
    project_dir = _project_dir(projeto_id)
    if categoria == "raiz":
        target = project_dir / nome_arquivo
    else:
        target = project_dir / categoria / nome_arquivo

    if not target.exists() or not target.is_file():
        return jsonify({"success": False, "error": f"Arquivo '{nome_arquivo}' não encontrado em '{categoria}'"}), 404

    return send_file(str(target), as_attachment=False)


@api_v2_bp.route("/arquivos/<projeto_id>/abrir_pasta", methods=["POST"])
def v2_arquivos_abrir_pasta(projeto_id: str):
    """Abre a pasta do projeto no Explorador de Arquivos do Windows."""
    pdir = _project_dir(projeto_id)
    if not pdir.exists():
        return jsonify({"success": False, "error": "Pasta não encontrada"}), 404
    try:
        os.startfile(str(pdir))
        return jsonify({"success": True, "caminho": str(pdir)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/arquivos/<projeto_id>/limpar_temporarios", methods=["POST"])
def v2_arquivos_limpar_temporarios(projeto_id: str):
    """Remove arquivos temporários (.tmp, caches soltos) preservando mídias."""
    pdir = _project_dir(projeto_id)
    removidos = 0
    if pdir.exists():
        for f in pdir.rglob("*.tmp"):
            try:
                f.unlink()
                removidos += 1
            except Exception:
                pass
    return jsonify({"success": True, "arquivos_removidos": removidos})


# ===========================================================================
# 3.5 MÍDIA DA CENA — REMOÇÃO (DELETE)
# ===========================================================================

@api_v2_bp.route("/projetos/<projeto_id>/cenas/<int:cena_id>/midia", methods=["DELETE"])
def v2_remover_midia_cena(projeto_id: str, cena_id: int):
    """Remove a mídia (imagem/vídeo) de uma cena do projeto.

    - Lê a cena no lira_scene_plan.json e obtém arquivo_midia.
    - Apaga o arquivo físico do disco (e cópias consolidadas) se existir.
    - Limpa os campos de mídia da cena e volta o status para PENDENTE.
    - Ressincroniza midias_encontradas.json.
    """
    try:
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            return jsonify({"ok": False, "error": "Plano de cenas não encontrado"}), 404

        cena = None
        cenas_lista = plan.get("cenas", [])
        # Busca estrita por scene_index primeiro (padrão dominante)
        for c in cenas_lista:
            s_idx = c.get("scene_index")
            if s_idx is not None and str(s_idx).strip() != "" and int(s_idx) == int(cena_id):
                cena = c
                break
        # Fallback para id se não encontrou por scene_index e a cena não tem scene_index preenchido
        if cena is None:
            for c in cenas_lista:
                if (c.get("scene_index") is None or str(c.get("scene_index")).strip() == "") and int(c.get("id", -1)) == int(cena_id):
                    cena = c
                    break

        if not cena:
            return jsonify({"ok": False, "error": f"Cena {cena_id} não encontrada"}), 404

        arquivo_midia = cena.get("arquivo_midia") or ""
        removidos_disco = []

        def _tentar_remover(p):
            try:
                if p and p.exists() and p.is_file():
                    p_abs = str(p.resolve())
                    log_event("MIDIA_REMOVER", f"{projeto_id}: removendo arquivo físico: {p_abs}", level="info")
                    p.unlink()
                    removidos_disco.append(p_abs)
                    return True
            except Exception as e:
                log_event("MIDIA_REMOVER", f"{projeto_id}: erro ao apagar {p}: {e}", level="warn")
            return False

        # 1. Resolve o arquivo canônico REAL ANTES de remover do disco
        arq_resolvido = None
        try:
            ts_ini_c = float(cena.get("tempo_inicio") or 0)
            arq_resolvido = scene_plan_svc.resolver_arquivo_cena(projeto_id, int(cena_id), ts_ini_c)
        except Exception as e:
            log_event("MIDIA_REMOVER", f"{projeto_id}: aviso resolver_arquivo_cena: {e}", level="warn")

        # 1.1. Apaga o arquivo físico registrado na cena
        if arquivo_midia:
            _tentar_remover(Path(str(arquivo_midia)))

        # 1.2. Apaga o arquivo canônico REAL resolvido se for caminho diferente
        if arq_resolvido and str(arq_resolvido) != str(arquivo_midia):
            _tentar_remover(arq_resolvido)

        # 2. Apaga também cópias canônicas/consolidadas/legadas que casem com a cena.
        #    Cobre padrões de nome com prefixo numérico da cena: NN_[...].png,
        #    001.png, NN_MM-SS_MM-SS.png etc. — sem apagar arquivos de outras cenas.
        pdir = _project_dir(projeto_id)
        ext = ".mp4" if str(arquivo_midia).lower().endswith((".mp4", ".mov", ".webm")) else ".png"
        import re as _re_midia
        prefixos = (f"{int(cena_id)}_", f"{int(cena_id):02d}_", f"{int(cena_id):03d}_",
                    f"{int(cena_id):02d}.", f"{int(cena_id):03d}.", f"{int(cena_id)}.")

        # Mídias ativas registradas em OUTRAS cenas — nunca apagar por varredura de prefixo
        outras_midias = {
            Path(other[campo]).name.lower()
            for other in plan.get("cenas", [])
            if other is not cena
            for campo in ("arquivo_midia", "download_path", "filename")
            if other.get(campo)
        }

        for pasta in ("cenas", "conteudo", "videos", "imagens"):
            pd = pdir / pasta
            if not pd.exists():
                continue
            for f in pd.iterdir():
                if not f.is_file():
                    continue
                nome = f.name
                if nome.lower() in outras_midias:
                    continue
                if not any(nome.startswith(pre) for pre in prefixos):
                    continue
                if f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".webm"):
                    continue
                # Confirma que o número inicial do nome é EXATAMENTE a cena (evita 1_* apagar 10_*)
                m_num = _re_midia.match(r"^(\d+)", nome)
                if m_num and int(m_num.group(1)) == int(cena_id):
                    _tentar_remover(f)

        # 3. Limpa os campos de mídia da cena e volta para PENDENTE
        target_cid = int(cena.get("id", cena_id))
        scene_plan_svc.atualizar_cena(projeto_id, target_cid, {
            "arquivo_midia": "",
            "download_path": "",
            "filename": "",
            "status": scene_plan_svc.STATUS_PENDENTE,
            "image_status": scene_plan_svc.IMAGE_STATUS_PENDING,
        })

        # 4. Ressincroniza midias_encontradas.json (remove a entrada órfã)
        try:
            scene_plan_svc.sincronizar_midias_encontradas(projeto_id, force=True)
        except Exception as e:
            log_event("MIDIA_REMOVER", f"{projeto_id}: aviso ao sincronizar mídias: {e}", level="warn")

        return jsonify({"ok": True, "cena_id": cena_id, "removidos_disco": removidos_disco})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@api_v2_bp.route("/projetos/<projeto_id>/limpar_midia_cenas", methods=["POST"])
@api_v2_bp.route("/projeto/<projeto_id>/limpar_midia_cenas", methods=["POST"])
def v2_limpar_midia_cenas(projeto_id: str):
    """Limpa mídias físicas de uma lista de cenas e reseta o status para PENDENTE.

    Body: {"cena_ids": [1, 2, 3, 4, 5]}
    Aceita IDs ou scene_index das cenas. Preserva prompts e metadados no lira_scene_plan.json.
    """
    try:
        data = request.get_json(silent=True) or {}
        cena_ids = data.get("cena_ids", [1, 2, 3, 4, 5])
        res = scene_plan_svc.limpar_midia_cenas_por_ids(projeto_id, cena_ids)
        if not res.get("success"):
            return jsonify({"ok": False, "success": False, "error": res.get("error", "Erro ao limpar mídias")}), 400
        return jsonify({
            "ok": True,
            "success": True,
            "cenas_limpas": res.get("cenas_limpas", []),
            "total_limpas": res.get("total_limpas", 0),
            "removidos_disco": res.get("removidos_disco", [])
        })
    except Exception as e:
        return jsonify({"ok": False, "success": False, "error": str(e)}), 500


# ===========================================================================
# 4. MONTAGEM v2
# ===========================================================================

@api_v2_bp.route("/montagem/<projeto_id>/transicion", methods=["POST"])
def v2_montagem_save_transicion(projeto_id: str):
    """Guarda la transición de entrada/salida de una cena en lira_scene_plan.json.

    Body: {"scene_id": int, "lado": "entrada"|"saida", "tipo": str, "duracao_ms": int}
    tipo "none" resetea al default por defecto (fade_in/fade_out, 300ms).
    """
    data = request.get_json(silent=True) or {}
    try:
        scene_id = int(data.get("scene_id") or 0)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "scene_id inválido"}), 400
    lado = str(data.get("lado") or "")
    tipo = str(data.get("tipo") or "")
    try:
        duracao_ms = int(data.get("duracao_ms") or 300)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "duracao_ms inválido"}), 400

    ok, msg, plan = scene_plan_svc.aplicar_transicion_cena(
        projeto_id, scene_id, lado, tipo, duracao_ms
    )
    if not ok:
        return jsonify({"success": False, "error": msg}), 400
    return jsonify({"success": True, "msg": msg, "plan": plan})


@api_v2_bp.route("/capcut/transicoes", methods=["GET"])
def v2_capcut_transicoes():
    """Retorna as transições da biblioteca nativa do CapCut Desktop disponíveis nesta máquina."""
    try:
        from services.capcut_library_service import capcut_library
        itens = capcut_library.obter_transicoes_disponiveis()
        return jsonify({"success": True, "transicoes": itens})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/montagem/<projeto_id>/transicoes_lote", methods=["POST"])
def v2_montagem_transicoes_lote(projeto_id: str):
    """Aplica uma transição padrão em todas as cenas do projeto (CapCut Nativo)."""
    data = request.get_json(silent=True) or {}
    tipo = str(data.get("tipo") or "bordas_difusas")
    duracao_ms = int(data.get("duracao_ms") or 500)
    lado = str(data.get("lado") or "saida")
    ok, msg, plan = scene_plan_svc.aplicar_transicoes_em_lote(
        projeto_id, tipo=tipo, duracao_ms=duracao_ms, lado=lado
    )
    if not ok:
        return jsonify({"success": False, "error": msg}), 400
    return jsonify({"success": True, "msg": msg, "plan": plan})


@api_v2_bp.route("/capcut/legendas", methods=["GET"])
def v2_capcut_legendas():
    """Retorna a biblioteca de presets de legenda nativa do CapCut."""
    try:
        from services.capcut_library_service import capcut_library
        presets = capcut_library.obter_presets_legendas()
        return jsonify({"success": True, "presets": presets})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/montagem/<projeto_id>/legendas_lote", methods=["POST"])
def v2_montagem_legendas_lote(projeto_id: str):
    """Aplica o estilo de legenda selecionado a todas as cenas em lote."""
    data = request.get_json(silent=True) or {}
    estilo_id = str(data.get("estilo_id") or "amarelo_capcut").strip()
    ativar_todas = bool(data.get("ativar_todas", True))
    # TAREFA 2: overrides opcionais de legenda propagados junto com o preset base
    # (quando ausente, as personalizações por cena são preservadas).
    caption_custom = data.get("caption_custom")
    ok, msg, plan = scene_plan_svc.aplicar_estilo_legenda_em_lote(
        projeto_id, estilo_id=estilo_id, ativar_todas=ativar_todas,
        caption_custom=caption_custom
    )
    if not ok:
        return jsonify({"success": False, "error": msg}), 400
    return jsonify({"success": True, "msg": msg, "plan": plan})


@api_v2_bp.route("/montagem/<projeto_id>/legendas_lote/adicionar", methods=["POST"])
def v2_montagem_adicionar_legendas_lote(projeto_id: str):
    """Ativa legendas em todas as cenas do projeto (bulk add)."""
    ok, msg, plan = scene_plan_svc.adicionar_legenda_em_lote(projeto_id)
    if not ok:
        return jsonify({"success": False, "error": msg}), 400
    return jsonify({"success": True, "msg": msg})


@api_v2_bp.route("/montagem/<projeto_id>/legendas_lote/remover", methods=["POST"])
def v2_montagem_remover_legendas_lote(projeto_id: str):
    """Desativa legendas de todas as cenas do projeto (bulk remove)."""
    ok, msg, plan = scene_plan_svc.remover_legenda_em_lote(projeto_id)
    if not ok:
        return jsonify({"success": False, "error": msg}), 400
    return jsonify({"success": True, "msg": msg})


@api_v2_bp.route("/montagem/<projeto_id>/cena/<int:cena_id>/sfx", methods=["POST"])
def v2_montagem_adicionar_sfx(projeto_id: str, cena_id: int):
    """Adicionar SFX a uma cena específica."""
    data = request.get_json(silent=True) or {}
    res = scene_plan_svc.adicionar_sfx_a_cena(projeto_id, int(cena_id), data)
    ok = res.get("success", False) if isinstance(res, dict) else bool(res)
    msg = res.get("msg", "") if isinstance(res, dict) else "SFX adicionado"
    plan = res.get("plan") if isinstance(res, dict) else None
    return jsonify({"success": ok, "msg": msg, "plan": plan}), 200 if ok else 400


@api_v2_bp.route("/montagem/<projeto_id>/cena/<int:cena_id>/sincronizar_fala", methods=["POST"])
def v2_montagem_sincronizar_fala(projeto_id: str, cena_id: int):
    """Sincronizar fala com texto usando word_timestamps."""
    try:
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            return jsonify({"success": False, "error": "Plano de cenas não encontrado"}), 404

        word_ts_path = Path(PROJETOS_DIR) / projeto_id / "word_timestamps.json"
        if not word_ts_path.exists():
            return jsonify({"success": False, "error": "word_timestamps.json não encontrado"}), 400

        with open(word_ts_path, "r", encoding="utf-8") as f:
            word_data = json.load(f)

        segments = word_data.get("segments", []) if isinstance(word_data, dict) else word_data
        cena_alvo = next((c for c in plan.get("cenas", []) if int(c.get("id", -1)) == int(cena_id)), None)
        if not cena_alvo:
            return jsonify({"success": False, "error": f"Cena {cena_id} não encontrada"}), 404

        idx = int(cena_id) - 1
        words_cena = []
        if 0 <= idx < len(segments):
            words_cena = segments[idx].get("words", []) if isinstance(segments[idx], dict) else []
        elif isinstance(segments, list):
            words_cena = [w for w in segments if isinstance(w, dict) and w.get("cena_id") == int(cena_id)]

        if words_cena:
            ultima_palavra = words_cena[-1]
            novo_fim = float(ultima_palavra.get("e", ultima_palavra.get("end", cena_alvo.get("tempo_fim", 0))))
            t_ini = float(cena_alvo.get("tempo_inicio", 0))
            if novo_fim > t_ini:
                cena_alvo["tempo_fim"] = round(novo_fim, 3)
                cena_alvo["duracao"] = round(novo_fim - t_ini, 3)
        elif 0 <= idx < len(segments) and isinstance(segments[idx], dict):
            novo_fim = float(segments[idx].get("end", cena_alvo.get("tempo_fim", 0)))
            t_ini = float(cena_alvo.get("tempo_inicio", 0))
            if novo_fim > t_ini:
                cena_alvo["tempo_fim"] = round(novo_fim, 3)
                cena_alvo["duracao"] = round(novo_fim - t_ini, 3)

        scene_plan_svc.salvar_scene_plan(projeto_id, plan)
        return jsonify({"success": True, "msg": "Fala sincronizada com texto", "plan": plan}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/montagem/<projeto_id>/cena/<int:cena_id>/cortar_silencio", methods=["POST"])
def v2_montagem_cortar_silencio(projeto_id: str, cena_id: int):
    """Cortar silêncios do áudio da cena."""
    try:
        from services.audio_processor import cortar_silencio

        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            return jsonify({"success": False, "error": "Plano de cenas não encontrado"}), 404

        cena_alvo = next((c for c in plan.get("cenas", []) if int(c.get("id", -1)) == int(cena_id)), None)
        if not cena_alvo:
            return jsonify({"success": False, "error": f"Cena {cena_id} não encontrada"}), 404

        arquivo_audio = cena_alvo.get("audio_path") or resolver_audio_projeto(projeto_id) or ""
        if not arquivo_audio or not Path(arquivo_audio).exists():
            return jsonify({"success": False, "error": "Arquivo de áudio não encontrado"}), 404

        p_audio = Path(arquivo_audio)
        saida = str(p_audio.parent / f"no_silence_{p_audio.name}")
        t_ini = float(cena_alvo.get("tempo_inicio", 0))
        t_fim = float(cena_alvo.get("tempo_fim", t_ini + 5.0))
        segs = [{"start": t_ini, "end": t_fim}]

        saida_gerada, _ = cortar_silencio(segs, str(p_audio), saida)
        cena_alvo["audio_path"] = saida_gerada or str(p_audio)
        cena_alvo["silencio_cortado"] = True

        scene_plan_svc.salvar_scene_plan(projeto_id, plan)
        return jsonify({"success": True, "msg": "Silêncios cortados", "plan": plan}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/montagem/<projeto_id>/cena/<int:cena_id>/regenerar_story_card", methods=["POST"])
def v2_montagem_regenerar_story_card(projeto_id: str, cena_id: int):
    """Regenera o Story Card da cena com prompt estruturado."""
    try:
        from services.story_card_prompt_builder import build_story_card_prompt
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            return jsonify({"success": False, "error": "Plano não encontrado"}), 404

        cena_alvo = next((c for c in plan.get("cenas", []) if int(c.get("id", -1)) == int(cena_id)), None)
        if not cena_alvo:
            return jsonify({"success": False, "error": f"Cena {cena_id} não encontrada"}), 404

        estilo = cena_alvo.get("story_card_style") or "step_intro"
        if estilo == "none":
            estilo = "step_intro"
        cena_alvo["is_story_card"] = True
        cena_alvo["story_card_style"] = estilo
        marker = {
            "story_card_type": cena_alvo.get("story_card_type") or estilo,
            "story_card_style": estilo,
            "story_card_config": cena_alvo.get("story_card_config") or {
                "title": estilo.upper().replace("_", " "),
                "subtitle": (cena_alvo.get("texto_transcricao") or cena_alvo.get("texto") or cena_alvo.get("narration") or "")[:50]
            }
        }
        prompt_card = build_story_card_prompt(marker)
        cena_alvo["story_card_prompt_imagem"] = prompt_card
        cena_alvo["prompt_imagem"] = prompt_card

        scene_plan_svc.salvar_scene_plan(projeto_id, plan)
        return jsonify({
            "success": True,
            "is_story_card": True,
            "story_card_style": estilo,
            "prompt": prompt_card,
            "plan": plan
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/montagem/<projeto_id>/cena/<int:cena_id>/smart_effects", methods=["GET", "POST"])
def v2_montagem_smart_effects(projeto_id: str, cena_id: int):
    """Analisa prompt visual, animação e áudio para recomendar e aplicar efeitos inteligentes."""
    try:
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            return jsonify({"success": False, "error": "Plano de cenas não encontrado"}), 404

        cena_alvo = next((c for c in plan.get("cenas", []) if int(c.get("id", -1)) == int(cena_id)), None)
        if not cena_alvo:
            return jsonify({"success": False, "error": f"Cena {cena_id} não encontrada"}), 404

        data = request.get_json(silent=True) or {}
        salvar = bool(data.get("salvar", False))

        # Contextual analysis from scene prompts and text
        p_img = str(cena_alvo.get("prompt_imagem") or cena_alvo.get("visual_prompt") or "").lower()
        p_anim = str(cena_alvo.get("prompt_animacao") or "").lower()
        txt = str(cena_alvo.get("texto_transcricao") or cena_alvo.get("texto") or cena_alvo.get("narration") or cena_alvo.get("fala") or "").lower()
        full_context = f"{p_img} {p_anim} {txt}"

        duracao = float(cena_alvo.get("duracao") or 5.0)

        # Classification heuristics
        if any(w in full_context for w in ["jardim", "garden", "planta", "árvore", "natureza", "campo", "sol", "calmo", "suave", "verde", "tomate", "horta", "manhã", "flor"]):
            mood = "🌿 Cena Natural & Orgânica • Iluminação Quente • Ritmo Suave"
            sugestao_lut = [
                {"id": "golden_hour", "nome": "Golden Hour Warm", "score": 98, "desc": "Tonalidade dourada e suave para ambiente natural"},
                {"id": "vintage", "nome": "Vintage 70s", "score": 85, "desc": "Cores quentes e ricas com contraste orgânico"},
                {"id": "teal_orange", "nome": "Teal & Orange", "score": 75, "desc": "Destaque cinematográfico para folhagens"}
            ]
            sugestao_trans = [
                {"id": "dissolve", "nome": "Dissolve Suave", "icono": "🌫️", "duracao_ms": 500},
                {"id": "fade_out", "nome": "Fade Out Clássico", "icono": "⬛", "duracao_ms": 400}
            ]
            sugestao_anim = [
                {"id": "loop_zoom_slow", "nome": "Slow Zoom In (Ken Burns)", "dur_ms": 800, "tipo": "loop"},
                {"id": "loop_float", "nome": "Flutuação Suave (Drift)", "dur_ms": 1000, "tipo": "loop"},
                {"id": "in_zoom", "nome": "Zoom In Suave", "dur_ms": 600, "tipo": "in"}
            ]
            sugestao_sfx = [
                {"arquivo": "ambient_nature.mp3", "nome": "Natureza & Pássaros", "vol": 60},
                {"arquivo": "transition_whoosh.mp3", "nome": "Whoosh Suave", "vol": 50}
            ]
        elif any(w in full_context for w in ["tecnologia", "robô", "código", "futuro", "cyber", "digital", "neon", "rede", "ia", "ai", "computador", "dados", "gráfico", "hacker"]):
            mood = "👾 Estética Cyberpunk & Digital • Alto Contraste • Ritmo Dinâmico"
            sugestao_lut = [
                {"id": "cyberpunk", "nome": "Cyberpunk Neon", "score": 97, "desc": "Tons púrpuras e cianos com alto contraste"},
                {"id": "teal_orange", "nome": "Teal & Orange", "score": 88, "desc": "Look moderno blockbuster"},
                {"id": "noir", "nome": "Film Noir P&B", "score": 70, "desc": "Monocromático de alto impacto"}
            ]
            sugestao_trans = [
                {"id": "whip_pan", "nome": "Whip Pan Rápido", "icono": "⚡", "duracao_ms": 300},
                {"id": "dissolve", "nome": "Dissolve Suave", "icono": "🌫️", "duracao_ms": 400}
            ]
            sugestao_anim = [
                {"id": "in_scale_pop", "nome": "Scale Pop Enérgico", "dur_ms": 450, "tipo": "in"},
                {"id": "loop_glitch", "nome": "Glitch Periódico", "dur_ms": 600, "tipo": "loop"},
                {"id": "in_whip_pan", "nome": "Whip Pan Lateral", "dur_ms": 400, "tipo": "in"}
            ]
            sugestao_sfx = [
                {"arquivo": "warning_beep.mp3", "nome": "Alerta / Digital Beep", "vol": 70},
                {"arquivo": "transition_whoosh.mp3", "nome": "Whoosh Transição", "vol": 75}
            ]
        elif any(w in full_context for w in ["impacto", "forte", "urgente", "alerta", "choque", "ação", "fogo", "explosão", "dinheiro", "lucro", "venda"]):
            mood = "💥 Cena de Alto Impacto • Ritmo Acelerado • Alta Atenção"
            sugestao_lut = [
                {"id": "teal_orange", "nome": "Teal & Orange", "score": 96, "desc": "Destaque agressivo e saturado"},
                {"id": "moody_dark", "nome": "Moody Dark", "score": 84, "desc": "Sombras profundas e dramáticas"},
                {"id": "golden_hour", "nome": "Golden Hour", "score": 72, "desc": "Destaque caloroso"}
            ]
            sugestao_trans = [
                {"id": "whip_pan", "nome": "Whip Pan Impacto", "icono": "⚡", "duracao_ms": 250},
                {"id": "fade_out", "nome": "Fade Rápido", "icono": "⬛", "duracao_ms": 300}
            ]
            sugestao_anim = [
                {"id": "loop_shake", "nome": "Camera Shake Impacto", "dur_ms": 500, "tipo": "loop"},
                {"id": "in_bounce", "nome": "Bounce Elástico", "dur_ms": 500, "tipo": "in"},
                {"id": "in_scale_pop", "nome": "Scale Pop", "dur_ms": 400, "tipo": "in"}
            ]
            sugestao_sfx = [
                {"arquivo": "cash_register.mp3", "nome": "Caixa Registradora ($)", "vol": 80},
                {"arquivo": "transition_whoosh.mp3", "nome": "Whoosh Rápido", "vol": 75}
            ]
        else:
            mood = "🎬 Visual Cinemático • Equilíbrio Narrativo • Foco na Clareza"
            sugestao_lut = [
                {"id": "cinematic", "nome": "Cinematic Film", "score": 95, "desc": "Graduação balanceada profissional"},
                {"id": "golden_hour", "nome": "Golden Hour", "score": 86, "desc": "Calor suave com tons agradáveis"},
                {"id": "vintage", "nome": "Vintage Film", "score": 78, "desc": "Textura analógica suave"}
            ]
            sugestao_trans = [
                {"id": "fade_out", "nome": "Fade Out Suave", "icono": "⬛", "duracao_ms": 400},
                {"id": "dissolve", "nome": "Dissolve Suave", "icono": "🌫️", "duracao_ms": 500}
            ]
            sugestao_anim = [
                {"id": "in_zoom", "nome": "Zoom In Suave", "dur_ms": 600, "tipo": "in"},
                {"id": "loop_zoom_slow", "nome": "Slow Zoom In (Ken Burns)", "dur_ms": 800, "tipo": "loop"},
                {"id": "in_slide_up", "nome": "Slide Up com Fade", "dur_ms": 550, "tipo": "in"}
            ]
            sugestao_sfx = [
                {"arquivo": "transition_whoosh.mp3", "nome": "Whoosh Transição", "vol": 65},
                {"arquivo": "success_chime.mp3", "nome": "Success Chime", "vol": 60}
            ]

        # Selected defaults
        combo_selecionado = {
            "lut": data.get("lut") or sugestao_lut[0]["id"],
            "transicao": data.get("transicao") or sugestao_trans[0]["id"],
            "animacao": data.get("animacao") or sugestao_anim[0]["id"],
            "sfx": data.get("sfx") or sugestao_sfx[0]["arquivo"],
            "sfx_nome": data.get("sfx_nome") or sugestao_sfx[0]["nome"]
        }

        # If save requested, persist directly to scene
        if salvar:
            cena_alvo["efeitos_visuais"] = {
                "lut": combo_selecionado["lut"],
                "saturacao": 105,
                "brilho": 100,
                "contraste": 105,
                "blur": 0,
                "aberracao_cromatica": False
            }
            cena_alvo["animacao_movimento"] = {
                "preset": combo_selecionado["animacao"],
                "duracao_ms": 600,
                "delay_ms": 0,
                "easing": "ease-in-out"
            }
            cena_alvo["transicao_saida"] = {
                "tipo": combo_selecionado["transicao"],
                "duracao_ms": 400
            }
            cena_alvo["sfx"] = {
                "arquivo": combo_selecionado["sfx"],
                "nome": combo_selecionado["sfx_nome"],
                "volume": 70,
                "pan": 0,
                "timestamp_cena": 0.0
            }
            cena_alvo["smart_effects"] = {
                "aplicado": True,
                "mood": mood,
                "combo": combo_selecionado
            }
            scene_plan_svc.salvar_scene_plan(projeto_id, plan)

        return jsonify({
            "success": True,
            "cena_id": cena_id,
            "duracao": duracao,
            "mood": mood,
            "sugestao_lut": sugestao_lut,
            "sugestao_trans": sugestao_trans,
            "sugestao_anim": sugestao_anim,
            "sugestao_sfx": sugestao_sfx,
            "combo_recomendado": combo_selecionado,
            "salvo": salvar
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/projeto/<projeto_id>/cena/<int:cena_id>/story_card", methods=["PATCH", "POST"])
def v2_projeto_salvar_story_card(projeto_id: str, cena_id: int):
    """Atualiza o estilo de Story Card de uma cena."""
    try:
        from services.story_card_prompt_builder import build_story_card_prompt
        data = request.get_json(silent=True) or {}
        estilo = data.get("estilo_selecionado") or data.get("story_card_style") or "none"
        is_card = (estilo != "none")

        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            return jsonify({"success": False, "error": "Plano não encontrado"}), 404

        cena_alvo = next((c for c in plan.get("cenas", []) if int(c.get("id", -1)) == int(cena_id)), None)
        if not cena_alvo:
            return jsonify({"success": False, "error": f"Cena {cena_id} não encontrada"}), 404

        cena_alvo["is_story_card"] = is_card
        cena_alvo["story_card_style"] = estilo if is_card else ""
        if is_card:
            cfg = cena_alvo.get("story_card_config") or {
                "title": estilo.upper().replace("_", " "),
                "subtitle": (cena_alvo.get("texto_transcricao") or cena_alvo.get("texto") or cena_alvo.get("narration") or "")[:50],
                "background_color": "#E6F1FB",
                "title_color": "#185FA5"
            }
            cena_alvo["story_card_config"] = cfg
            marker = {
                "story_card_type": estilo,
                "story_card_style": estilo,
                "story_card_config": cfg
            }
            prompt_card = build_story_card_prompt(marker)
            cena_alvo["story_card_prompt_imagem"] = prompt_card

        scene_plan_svc.salvar_scene_plan(projeto_id, plan)
        return jsonify({"success": True, "is_story_card": is_card, "story_card_style": estilo, "plan": plan}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500



@api_v2_bp.route("/montagem/<projeto_id>/cena/<int:scene_id>", methods=["PUT", "POST"])
def v2_montagem_atualizar_cena(projeto_id: str, scene_id: int):
    """Atualiza campos de uma cena no scene_plan (texto, legenda_ativa, estilo_legenda, etc.)."""
    data = request.get_json(silent=True) or {}
    res = scene_plan_svc.atualizar_cena(projeto_id, scene_id, data)
    if not res.get("success"):
        return jsonify(res), 400
    return jsonify(res), 200


@api_v2_bp.route("/montagem/<projeto_id>/cena/<int:scene_id>", methods=["DELETE"])
@api_v2_bp.route("/montagem/<projeto_id>/cena/<int:scene_id>/remover", methods=["POST"])
def v2_montagem_remover_cena(projeto_id: str, scene_id: int):
    """Remove uma cena do projeto e recalcula os timestamps sequenciais."""
    res = scene_plan_svc.remover_cena(projeto_id, scene_id)
    if not res.get("success"):
        return jsonify(res), 400
    return jsonify(res), 200


@api_v2_bp.route("/montagem/<projeto_id>/reordenar_cenas", methods=["POST"])
def v2_montagem_reordenar_cenas(projeto_id: str):
    """Reordena as cenas do projeto com base na nova lista de IDs e recalcula a linha do tempo."""
    data = request.get_json(silent=True) or {}
    nova_ordem = data.get("ordem") or data.get("ordem_ids") or []
    if not nova_ordem:
        return jsonify({"success": False, "error": "Lista 'ordem' ou 'ordem_ids' não fornecida"}), 400
    res = scene_plan_svc.reordenar_cenas(projeto_id, nova_ordem)
    if not res.get("success"):
        return jsonify(res), 400
    return jsonify(res), 200


# ---------------------------------------------------------------------------
# REDESIGN F1 — Automação de Movimento (motion_preset por cena)
# ---------------------------------------------------------------------------
_MOTION_PRESETS_VALIDOS = (
    "zoom_in", "zoom_out", "zoom_in_slow", "zoom_out_slow",
    "pan_right", "pan_left", "pan_up", "pan_down", "estatico"
)
_MOTION_CICLO = ("zoom_in", "pan_right", "zoom_out", "pan_left")


def _calcular_motion_preset_cena(cena: dict, index: int = 0) -> Optional[str]:
    """Calcula um preset de movimento (câmera) para uma cena, com base nos sinais
    já existentes do projeto (narrative_role, scene_type, duração e no
    animation_director_service). Nunca levanta exceção — sempre devolve um token
    válido de _MOTION_PRESETS_VALIDOS.

    Regras:
      - comparação / cena declarada estática -> 'estatico'
      - fala direta / hook / cta / apresentador (ênfase/fala) -> 'zoom_in'
      - prova/revelação/ação física (antes&depois, proof, kinetic) -> 'pan_right'
      - b-roll de abertura/natureza ou cena longa (>=5s) -> 'zoom_out'
      - demais cenas com movimento -> 'pan_left' se avatar/contraste, senão ciclo
      - fallback determinístico pelo índice (ciclo de 4)
    """
    tipo_cena = str(cena.get("tipo") or cena.get("narrative_role") or "").upper()

    # Hook de retenção: imagens nas primeiras 5 posições recebem
    # movimento de alto impacto quando não há vídeo
    _HOOK_PRESETS = ["zoom_in", "pan_right", "zoom_out", "pan_left", "zoom_in_slow"]
    _tipo_midia = str(cena.get("tipo") or "").lower()
    if index <= 4 and _tipo_midia not in ("video", "b-roll", "broll"):
        return _HOOK_PRESETS[index]

    if tipo_cena in ("B-ROLL", "BROLL"):
        return None

    stype = str(cena.get("scene_type") or "")
    role = str(cena.get("narrative_role") or "").upper()
    story_role = str(cena.get("story_role") or "")
    uses_char = bool(cena.get("uses_character", False))
    try:
        duracao = float(cena.get("duracao") or 0)
    except (TypeError, ValueError):
        duracao = 0.0

    if stype == "comparison":
        return "estatico"

    try:
        from services.animation_director_service import direcionar_animacao_cena
        diretriz = direcionar_animacao_cena(cena, None, index) or {}
    except Exception:
        diretriz = {}
    if diretriz.get("should_animate") is False:
        return "estatico"
    at = str(diretriz.get("animation_type") or "")
    mv = str(diretriz.get("motion_vector") or "")

    # 1. Ênfase/fala direta (zoom aproximando do centro)
    if role in ("HOOK", "CTA", "CLOSING") or stype in ("avatar_talking", "cta") or at == "presenter_speech":
        return "zoom_in"
    # 2. Revelação / prova / ação física (deslize horizontal para a direita)
    if at in ("transformation_reveal", "kinetic_action") or stype == "before_after" or \
            story_role in ("proof", "result") or "glide" in mv or "reveal" in mv:
        return "pan_right"
    # 3. Abertura / natureza / plano geral (zoom recuando)
    if role == "BROLL" or stype in ("broll_macro", "ambient_fluid_motion") or duracao >= 5.0:
        return "zoom_out"
    # 4. Avatar em cena / contraste (deslize para a esquerda)
    if uses_char or role == "AVATAR":
        return "pan_left"
    # 5. Fallback determinístico (ciclo dos 4 presets, igual ao video_builder)
    return _MOTION_CICLO[index % len(_MOTION_CICLO)]


@api_v2_bp.route("/montagem/<projeto_id>/direcionar_movimentos", methods=["POST"])
def v2_montagem_direcionar_movimentos(projeto_id: str):
    """Calcula (e, se aprovado, grava) motion_preset para todas as cenas.

    body: { "acao": "calcular" | "aprovar" }
      - "calcular": retorna resumo + mapa cena->preset SEM persistir;
      - "aprovar": recalcula de forma determinística e persiste motion_preset
        em todas as cenas (campo novo opcional — ken_burns_ativo é preservado).
    """
    try:
        data = request.get_json(silent=True) or {}
        acao = str(data.get("acao") or "calcular")
        if acao not in ("calcular", "aprovar"):
            acao = "calcular"

        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            return jsonify({"success": False, "error": "Plano de cenas não encontrado"}), 400

        cenas = sorted(
            plan["cenas"],
            key=lambda c: float(c.get("tempo_inicio", 0) or 0),
        )
        presets = {}
        resumo = {p: 0 for p in _MOTION_PRESETS_VALIDOS}
        for idx, cena in enumerate(cenas):
            cid = int(cena.get("id", idx))
            preset = _calcular_motion_preset_cena(cena, idx)
            presets[cid] = preset
            if preset is not None:
                resumo[preset] = resumo.get(preset, 0) + 1

        gravadas = 0
        if acao == "aprovar":
            for cid, preset in presets.items():
                if preset is not None:
                    try:
                        r = scene_plan_svc.atualizar_cena(projeto_id, cid, {"motion_preset": preset})
                        if r.get("success"):
                            gravadas += 1
                    except Exception:
                        pass

        return jsonify({
            "success": True,
            "acao": acao,
            "total": len(cenas),
            "gravadas": gravadas if acao == "aprovar" else 0,
            "resumo": resumo,
            "presets": presets if acao == "calcular" else {},
        })
    except Exception as e:
        log_event("MONTAGEM_V2", f"Erro ao direcionar movimentos de '{projeto_id}': {e}", level="error")
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/montagem/<projeto_id>/sincronizar", methods=["POST", "GET"])
def v2_montagem_sincronizar(projeto_id: str):
    """
    Verifica se todos os elementos (áudio, SRT, imagens, vídeos) estão prontos
    e sincronizados na timeline para montagem.
    """
    scene_plan_svc.sincronizar_midias_encontradas(projeto_id)
    plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    if not plan or not plan.get("cenas"):
        return jsonify({"success": True, "total_cenas": 0, "cenas": []}), 200

    pdir = _project_dir(projeto_id)
    cenas = [c for c in plan["cenas"] if not c.get("removida") and c.get("status") != "REMOVIDA"]
    total = len(cenas)
    com_midia = 0
    faltantes = []
    cenas_detalhe = []

    for c in cenas:
        cid = int(c["id"])
        arq_obj = scene_plan_svc.resolver_arquivo_cena(projeto_id, cid, float(c.get("tempo_inicio", 0)))
        arq_resolvido = str(arq_obj) if arq_obj else None

        existe = bool(arq_resolvido)
        if existe:
            com_midia += 1
        else:
            faltantes.append(cid)

        texto_cena = c.get("texto_transcricao") or c.get("narration") or c.get("texto") or ""
        legenda_on = c.get("legenda_ativa")
        if legenda_on is None:
            legenda_on = c.get("caption_ativo", True)
        estilo_leg = c.get("estilo_legenda") or c.get("caption_style") or "amarelo_capcut"

        cenas_detalhe.append({
            "id": cid,
            "scene_index": c.get("scene_index", cid),
            "tempo_inicio": c.get("tempo_inicio", 0),
            "tempo_fim": c.get("tempo_fim", 0),
            "duracao": c.get("duracao", 0),
            "tipo": c.get("tipo", "image"),
            "narrative_role": c.get("narrative_role", ""),
            "status": c.get("status", scene_plan_svc.STATUS_PENDENTE),
            "arquivo_midia": arq_resolvido or c.get("arquivo_midia", ""),
            # CORREÇÃO 1 — extrai os campos REAIS do scene_plan (prompt_imagem/visual_prompt e narração/texto)
            "prompt": c.get("prompt_en", c.get("prompt_imagem", c.get("visual_prompt", c.get("prompt", "")))),
            "fala": texto_cena,
            "texto": texto_cena,
            "texto_transcricao": texto_cena,
            "legenda_ativa": bool(legenda_on),
            "caption_ativo": bool(legenda_on),
            "estilo_legenda": estilo_leg,
            "caption_style": estilo_leg,
            "nome_padrao": _padronizar_nome_arquivo(cid, float(c.get("tempo_inicio", 0)), float(c.get("tempo_fim", 0)), ".png" if c.get("tipo") != "video" else ".mp4"),
            "tem_midia": existe,
            # Transiciones (P6) — vêm do lira_scene_plan.json (backfill garante defaults)
            "transicao_entrada": c.get("transicao_entrada") or scene_plan_svc.TRANSICION_ENTRADA_DEFAULT,
            "transicao_saida": c.get("transicao_saida") or scene_plan_svc.TRANSICION_SAIDA_DEFAULT,
            "ken_burns_ativo": c.get("ken_burns_ativo", False),
            # REDESIGN F1: preset de movimento calculado pela automação (campo novo opcional)
            "motion_preset": c.get("motion_preset") or "",
        })

    pode_montar = (com_midia == total and total > 0)
    # ANTIGRAVITY Passo 1: validação de áudio UNIFICADA (meta → audio/* → espelho raiz),
    # garantindo que o player receba tem_audio:true mesmo se o path de meta.json desalinhar.
    tem_audio = bool(resolver_audio_projeto(projeto_id))

    return jsonify({
        "success": True,
        "total_cenas": total,
        "cenas_com_midia": com_midia,
        "cenas_faltantes": faltantes,
        "cenas": cenas_detalhe,
        "tem_audio": tem_audio,
        "pode_montar": pode_montar and tem_audio,
        "porcentagem_concluida": round((com_midia / total) * 100, 1) if total else 0,
    })


@api_v2_bp.route("/montagem/<projeto_id>/exportar_capcut", methods=["POST"])
def v2_montagem_exportar_capcut(projeto_id: str):
    """
    Monta e exporta o rascunho de timeline diretamente para o CapCut Desktop,
    salvando também uma cópia no diretório capcut/ do projeto.
    """
    try:
        from capcut_draft_imagens import criar_draft_imagens, detectar_pasta_drafts

        scene_plan_svc.sincronizar_midias_encontradas(projeto_id)
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            return jsonify({"success": False, "error": "Plano de cenas não encontrado"}), 400

        pdir = _project_dir(projeto_id)

        # ANTIGRAVITY Passo 1: FONTE ÚNICA de áudio (meta → audio/* → raiz). Resolvida
        # ANTES do loop para medir a duração real do áudio (última cena do export).
        audio = resolver_audio_projeto(projeto_id) or ""

        # ANTIGRAVITY (fala×cena): a trilha de vídeo do CapCut é magnética (Auto
        # Ripple). Se cada clipe tiver só a duração da FALA (tempo_fim - tempo_inicio),
        # as lacunas de silêncio entre frases são fechadas pelo CapCut e cada cena
        # desliza progressivamente para trás (offset de -9,75s na cena 92 do Joaquim).
        # No EXPORT, cada clipe cobre ATÉ o início da próxima cena (silêncio incluso)
        # e a última cena vai até o FIM REAL do áudio. A duração de fala original
        # (c["duracao"]) NÃO é alterada — métricas/retenção/Studio seguem intactos.
        cenas_ativas = [c for c in plan["cenas"] if not c.get("removida") and c.get("status") != "REMOVIDA"]
        cenas = sorted(
            cenas_ativas, key=lambda c: float(c.get("tempo_inicio", 0) or 0)
        )
        duracao_audio_total = _duracao_audio_segundos(audio)  # 0.0 se falhar
        lista_cenas_capcut = []
        for idx, c in enumerate(cenas):
            cid = int(c["id"])
            t_ini = float(c.get("tempo_inicio", 0) or 0)
            t_ini_export = 0.0 if idx == 0 else t_ini
            arq_obj = scene_plan_svc.resolver_arquivo_cena(projeto_id, cid, t_ini)
            arq_resolvido = str(arq_obj) if arq_obj else None

            duracao_fala = float(c.get("duracao") or 0) or 5.0
            if idx + 1 < len(cenas):
                # clipe cobre o intervalo [t_ini_export, início da próxima fala) — sem lacuna
                t_prox = float(cenas[idx + 1].get("tempo_inicio", 0) or t_ini_export)
                duracao_export = max(duracao_fala, t_prox - t_ini_export)
            elif duracao_audio_total and duracao_audio_total > t_ini_export:
                # última cena: até o FIM REAL do áudio (não a fala isolada)
                duracao_export = max(duracao_fala, duracao_audio_total - t_ini_export)
            else:
                duracao_export = duracao_fala  # fallback: áudio não mensurável

            texto_export = c.get("texto_transcricao") or c.get("narration") or c.get("texto") or ""
            legenda_ativa_export = c.get("legenda_ativa")
            if legenda_ativa_export is None:
                legenda_ativa_export = c.get("caption_ativo", True)
            estilo_legenda_export = c.get("estilo_legenda") or c.get("caption_style") or "amarelo_capcut"

            lista_cenas_capcut.append({
                "start": t_ini_export,
                "arquivo": arq_resolvido,
                "tipo": c.get("tipo", "image"),
                "narrative_role": c.get("narrative_role", ""),
                "media_type": "video" if (c.get("tipo") == "video" or (arq_resolvido and arq_resolvido.lower().endswith((".mp4", ".mov", ".mkv", ".webm")))) else "photo",
                "duracao": float(round(duracao_export, 6)),
                # REDESIGN F1 / CORREÇÃO CRÍTICA: campos que eram descartados —
                # Ken Burns (keyframe CapCut) e motion_preset + transição real da cena.
                "ken_burns_ativo": c.get("ken_burns_ativo", True),
                "motion_preset": c.get("motion_preset", ""),
                "transicao_saida": c.get("transicao_saida") or {"tipo": "combinar", "duracao_ms": 500},
                "texto": texto_export,
                "texto_transcricao": texto_export,
                "caption_style": estilo_legenda_export,
                "estilo_legenda": estilo_legenda_export,
                "caption_ativo": bool(legenda_ativa_export),
                "legenda_ativa": bool(legenda_ativa_export),
                # TAREFA 4: overrides de legenda por cena (tamanho/fonte/cor/posição)
                # propagados até _gerar_trilha_texto -> resolver_material_legenda.
                "caption_custom": c.get("caption_custom") or None,
                # GAP CORRIGIDO (backfill v2): o Story Card (badge editorial) é
                # consumido por _gerar_trilha_texto, mas este builder NÃO propagava
                # o campo — o badge era silenciosamente descartado na rota oficial
                # (medido: 0 badges no draft_content.json com o plano carimbado).
                "editorial_style": c.get("editorial_style") or None,
                # ETAPA 3 — Story Cards Intelligence
                "is_story_card": bool(c.get("is_story_card")),
                "story_card_type": c.get("story_card_type"),
                "story_card_style": c.get("story_card_style"),
                "story_card_config": c.get("story_card_config"),
                "story_card_sync_timecode": c.get("story_card_sync_timecode", 0.0),
                "story_card_position": c.get("story_card_position", "before"),
                # TAREFA 5: SFX e metadados de cena
                "id": cid,
                "tempo_inicio": t_ini,
                "sfx_lista": c.get("sfx_lista") or [],
            })

        pasta_drafts = detectar_pasta_drafts()
        resultado = criar_draft_imagens(
            projeto_id,
            lista_cenas_capcut,
            audio,
            pasta_drafts,
            nome_projeto=projeto_id,
        )

        # Salva registro na pasta capcut/
        capcut_dir = _project_dir(projeto_id) / "capcut"
        capcut_dir.mkdir(parents=True, exist_ok=True)
        (capcut_dir / "ultimo_export.json").write_text(json.dumps(resultado, indent=2, ensure_ascii=False), encoding="utf-8")

        # PHASE 2 (ERRO 4): validación estructural del draft recién escrito.
        # Si el draft está corrompido, NO se reporta éxito — se devuelve 422.
        try:
            from services.capcut_validator import validar_draft_content
            _validacion_draft = validar_draft_content(projeto_id, draft_path=resultado.get("draft_dir"))
        except Exception as _ve_draft:
            _validacion_draft = {"ok": False, "erro": str(_ve_draft)}
        if not _validacion_draft.get("ok"):
            log_event("MONTAGEM_V2", f"EXPORT CapCut: draft inválido: {_validacion_draft}", level="warn")
            return jsonify({
                "success": False,
                "error": f"Draft corrompido: {_validacion_draft.get('erro')}",
                "validacion": _validacion_draft,
            }), 422

        log_event("MONTAGEM_V2", f"Exportación CapCut concluida para '{projeto_id}' (draft validado)", level="info")
        return jsonify({
            "success": True,
            "resultado": resultado,
            "capcut_dir": str(capcut_dir),
            "draft_validado": _validacion_draft,
        })
    except Exception as e:
        log_event("MONTAGEM_V2", f"Erro na exportação CapCut: {e}", level="error")
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/montagem/<projeto_id>/exportar_zip", methods=["GET"])
def v2_montagem_exportar_zip(projeto_id: str):
    """Gera e envia o pacote ZIP completo do projeto com todas as mídias, áudio, srt e planos."""
    import zipfile
    import io
    pdir = _project_dir(projeto_id)
    if not pdir.exists():
        return jsonify({"success": False, "error": "Projeto não encontrado"}), 404

    scene_plan_svc.sincronizar_midias_encontradas(projeto_id)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Cenas
        cenas_dir = pdir / "cenas"
        if cenas_dir.exists():
            for f in cenas_dir.rglob("*"):
                if f.is_file():
                    zf.write(f, str(f.relative_to(pdir)))
        # 2. Áudios, SRT, JSONs
        for f_name in ["meta.json", "lira_scene_plan.json", "storyboard.json", "galeria.json", "identidade.json"]:
            f_path = pdir / f_name
            if f_path.exists():
                zf.write(f_path, f_name)
        # 3. Pastas extras
        for folder in ["audio", "srt", "prompts", "imagens", "videos"]:
            f_dir = pdir / folder
            if f_dir.exists():
                for f in f_dir.rglob("*"):
                    if f.is_file():
                        zf.write(f, str(f.relative_to(pdir)))

    zip_buffer.seek(0)
    safe_proj = re.sub(r'[^\w\-]', '_', projeto_id)
    nome_zip = f"{safe_proj}_completo.zip"
    return send_file(zip_buffer, mimetype="application/zip", as_attachment=True, download_name=nome_zip)


# ===========================================================================
# 4.1 B-ROLL MOTION & BGM ENGINE
# ===========================================================================
_BROLL_JOBS: Dict[str, Dict[str, Any]] = {}

def _executar_broll_background(projeto_id: str):
    """Executa a conversão de todas as imagens para clipes MP4 B-Roll com Ken Burns em background."""
    from services.video_builder import converter_todas_imagens_projeto_para_broll_mp4
    job = _BROLL_JOBS.setdefault(projeto_id, {
        "status": "processando",
        "progresso": 0,
        "mensagem": "Iniciando conversão de B-Roll...",
        "total": 0,
        "convertidas": 0,
        "erros": 0,
        "iniciado_em": time.time(),
        "concluido_em": None
    })
    job["status"] = "processando"
    job["progresso"] = 0
    job["mensagem"] = "Analisando cenas para converter em MP4 B-Roll..."

    def _prog(atual, total, msg):
        pct = int(round((atual / total) * 100)) if total > 0 else 0
        job["progresso"] = pct
        job["convertidas"] = atual
        job["total"] = total
        job["mensagem"] = msg

    try:
        res = converter_todas_imagens_projeto_para_broll_mp4(projeto_id, callback_progresso=_prog)
        if res.get("success"):
            job["status"] = "concluido"
            job["progresso"] = 100
            job["mensagem"] = res.get("mensagem", "Conversão de B-Roll concluída!")
            job["convertidas"] = res.get("convertidas", 0)
            job["total"] = res.get("total", 0)
            job["erros"] = res.get("erros", 0)
            job["concluido_em"] = time.time()
        else:
            job["status"] = "erro"
            job["mensagem"] = res.get("error", "Erro na conversão de B-Roll")
    except Exception as e:
        job["status"] = "erro"
        job["mensagem"] = str(e)


@api_v2_bp.route("/montagem/<projeto_id>/gerar_broll_mp4", methods=["POST"])
def v2_montagem_gerar_broll_mp4(projeto_id: str):
    """Dispara a conversão de todas as imagens PNG para clipes MP4 B-Roll com efeito Ken Burns."""
    job = _BROLL_JOBS.get(projeto_id)
    if job and job.get("status") == "processando":
        return jsonify({
            "success": True,
            "status": "processando",
            "mensagem": "Conversão de B-Roll já em andamento",
            "job": job
        })

    t = threading.Thread(target=_executar_broll_background, args=(projeto_id,), daemon=True)
    t.start()

    return jsonify({
        "success": True,
        "status": "iniciado",
        "mensagem": "Geração de clipes MP4 B-Roll com movimento iniciada em segundo plano."
    })


@api_v2_bp.route("/montagem/<projeto_id>/broll_status", methods=["GET"])
def v2_montagem_broll_status(projeto_id: str):
    """Retorna o progresso atual da conversão das imagens em clipes MP4 B-Roll."""
    job = _BROLL_JOBS.get(projeto_id, {
        "status": "parado",
        "progresso": 0,
        "mensagem": "Nenhum processo de B-Roll ativo",
        "total": 0,
        "convertidas": 0
    })
    return jsonify({"success": True, "job": job})


@api_v2_bp.route("/montagem/<projeto_id>/musica/perfil", methods=["GET"])
def v2_montagem_musica_perfil(projeto_id: str):
    """Analisa o nicho e o roteiro do projeto e retorna sugestões e perfil musical ideal."""
    try:
        from services.bgm_service import analisar_perfil_musical_projeto
        perfil = analisar_perfil_musical_projeto(projeto_id)
        return jsonify({"success": True, "perfil": perfil})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/montagem/<projeto_id>/musica/definir", methods=["POST"])
def v2_montagem_musica_definir(projeto_id: str):
    """Define a trilha sonora e o ducking para o projeto."""
    try:
        from services.bgm_service import vincular_trilha_projeto
        data = request.get_json(silent=True) or {}
        caminho = data.get("arquivo", "")
        volume = float(data.get("volume", 0.14))
        ducking = bool(data.get("ducking", True))
        res = vincular_trilha_projeto(projeto_id, caminho, volume=volume, ducking=ducking)
        return jsonify(res)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


_RENDER_JOBS: Dict[str, Dict[str, Any]] = {}


def _executar_render_background(projeto_id: str, cmd: list, output_mp4: Path, duracao_aprox: float = 0.0, clips_temporarios: list = None):
    """Executa FFmpeg via Popen em background com progresso % (stderr time=).

    Lira Studio v0.2.0 (Frente 2): subprocess.run(timeout=600) vira Popen com
    leitura incremental do stderr — o job["progresso"] atualiza em tempo real e
    o front acompanha via GET /montagem/<id>/render_status (sem freeze).
    """
    job = _RENDER_JOBS.setdefault(projeto_id, {
        "status": "renderizando",
        "progresso": 5,
        "mensagem": "Renderizando vídeo final via FFmpeg...",
        "arquivo": str(output_mp4),
        "erro": None,
        "iniciado_em": time.time()
    })
    job["status"] = "renderizando"
    job["progresso"] = 5
    job["erro"] = None
    job["mensagem"] = "Iniciando FFmpeg..."

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace"
        )
        tempo_atual = 0.0
        while True:
            linha = proc.stdout.readline() if proc.stdout else ""
            if not linha:
                if proc.poll() is not None:
                    break
                time.sleep(0.2)
                continue
            # stderr do ffmpeg: "time=00:01:23.45"
            m = re.search(r"time=(\d+):(\d+):(\d+\.?\d*)", linha)
            if m:
                tempo_atual = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
                if duracao_aprox > 0:
                    job["progresso"] = min(99, 5 + int((tempo_atual / duracao_aprox) * 90))
                    job["mensagem"] = f"Renderizando... {job['progresso']}%"
                    job["tempo_atual"] = round(tempo_atual, 2)

        rc = proc.wait()
        if rc != 0:
            job["status"] = "erro"
            job["erro"] = f"Erro no FFmpeg (exit {rc})"
            job["mensagem"] = "Erro no FFmpeg"
            log_event("RENDER_MP4", f"FFmpeg error para '{projeto_id}' (exit {rc})", level="error")
            return

        job["status"] = "concluido"
        job["progresso"] = 100
        job["mensagem"] = "Renderização concluída"
        job["tamanho_bytes"] = output_mp4.stat().st_size if output_mp4.exists() else 0
        job["url_download"] = f"/api/v2/montagem/{urllib.parse.quote(projeto_id)}/download_video_final"
        job["concluido_em"] = time.time()
        log_event("RENDER_MP4", f"Vídeo final renderizado com sucesso para '{projeto_id}'", level="info")
    except Exception as e:
        job["status"] = "erro"
        job["erro"] = str(e)
        job["mensagem"] = "Erro na renderização"
        log_event("RENDER_MP4", f"Erro na renderização background '{projeto_id}': {e}", level="error")
    finally:
        # Limpeza dos clipes temporários após o render
        if clips_temporarios:
            for tmp in clips_temporarios:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass


@api_v2_bp.route("/montagem/<projeto_id>/renderizar_mp4", methods=["POST"])
def v2_montagem_renderizar_mp4(projeto_id: str):
    """
    Renderiza o vídeo completo .mp4 concatenando as imagens/vídeos sincronizados
    com o áudio original do projeto via FFmpeg de forma assíncrona/não-bloqueante.
    """
    try:
        pdir = _project_dir(projeto_id)
        scene_plan_svc.sincronizar_midias_encontradas(projeto_id, force=True)
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            return jsonify({"success": False, "error": "Plano de cenas não encontrado"}), 400

        data = request.get_json(force=True, silent=True) or {}

        meta = _get_meta(projeto_id)
        arq_audio = meta.get("arquivo_audio")
        if not arq_audio or not Path(arq_audio).exists():
            cands = list(pdir.glob("audio.*")) + list(pdir.glob("*.mp3")) + list(pdir.glob("*.wav")) + list(pdir.glob("*.m4a"))
            if cands:
                arq_audio = str(cands[0])

        cenas = plan["cenas"]
        concat_txt = pdir / "ffmpeg_concat.txt"
        linhas_concat = []
        pasta_render = str(pdir.resolve())
        import tempfile as _tempfile
        clips_temporarios = []

        for c in cenas:
            cid = int(c["id"])
            # Tenta resolver vídeo primeiro caso exista clipe .mp4 gerado para a cena
            arq_obj = scene_plan_svc.resolver_arquivo_cena(projeto_id, cid, float(c.get("tempo_inicio", 0)), ext=".mp4")
            if not arq_obj or not arq_obj.exists():
                # Caso não encontre .mp4, busca imagem ou outro formato
                arq_obj = scene_plan_svc.resolver_arquivo_cena(projeto_id, cid, float(c.get("tempo_inicio", 0)))

            if not arq_obj or not arq_obj.exists():
                continue
            dur = max(0.5, float(c.get("duracao", 5.0)))
            arquivo = str(arq_obj.resolve())

            # Converter PNG/JPEG em clipe de vídeo temporário com Ken Burns (se não tiver mp4 pré-gerado)
            if str(arquivo).lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                tmp_clip = _tempfile.mktemp(suffix=".mp4", dir=pasta_render)
                clips_temporarios.append(tmp_clip)
                try:
                    from services.video_builder import converter_imagem_para_broll_mp4
                    presets = ["zoom_in", "pan_right", "zoom_out", "pan_left"]
                    preset = presets[cid % len(presets)]
                    res_kb = converter_imagem_para_broll_mp4(arquivo, tmp_clip, duracao=dur, preset=preset)
                    if not res_kb.get("success"):
                        cmd_img = [
                            FFMPEG_PATH, "-y",
                            "-loop", "1", "-framerate", "25",
                            "-i", str(arquivo),
                            "-t", str(dur),
                            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,format=yuv420p,setsar=1,fps=25",
                            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                            "-r", "25", "-pix_fmt", "yuv420p", "-an",
                            tmp_clip
                        ]
                        subprocess.run(cmd_img, capture_output=True, timeout=60)
                except Exception:
                    cmd_img = [
                        FFMPEG_PATH, "-y",
                        "-loop", "1", "-framerate", "25",
                        "-i", str(arquivo),
                        "-t", str(dur),
                        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,format=yuv420p,setsar=1,fps=25",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                        "-r", "25", "-pix_fmt", "yuv420p", "-an",
                        tmp_clip
                    ]
                    subprocess.run(cmd_img, capture_output=True, timeout=60)
                arquivo = tmp_clip

            p_str = str(Path(arquivo).resolve()).replace("\\", "/")
            linhas_concat.append(f"file '{p_str}'")
            linhas_concat.append(f"duration {dur:.3f}")

        if not linhas_concat:
            return jsonify({"success": False, "error": "Nenhuma mídia gerada encontrada para renderizar."}), 400

        # Coletar durações e transições para xfade
        _trans_info = []
        _offset_acum = 0.0
        for c in cenas:
            dur = max(0.5, float(c.get("duracao", 5.0)))
            trans_saida = (c.get("transicao_saida") or {})
            tipo_trans = trans_saida.get("tipo", "none")
            dur_trans = float(trans_saida.get("duracao_ms", 300)) / 1000.0
            _trans_info.append({
                "offset": _offset_acum,
                "dur": dur,
                "tipo": tipo_trans,
                "dur_trans": dur_trans
            })
            _offset_acum += dur

        linhas_concat.append(linhas_concat[-2])
        concat_txt.write_text("\n".join(linhas_concat), encoding="utf-8")

        # Mixar trilha sonora (BGM) se configurada em meta.json
        trilha_info = meta.get("trilha_sonora", {})
        trilha_path = trilha_info.get("arquivo")
        if trilha_path and Path(trilha_path).exists() and arq_audio and Path(arq_audio).exists():
            try:
                from services.bgm_service import mixar_voz_e_musica_ffmpeg
                audio_mixado = pdir / f"audio_mixado_{int(time.time())}.wav"
                vol_musica = float(trilha_info.get("volume", 0.14))
                usar_ducking = bool(trilha_info.get("ducking", True))
                log_event("RENDER_MP4", f"Mixando voz com trilha sonora ({Path(trilha_path).name}) volume={vol_musica} ducking={usar_ducking}...", level="info")
                sucesso_mix = mixar_voz_e_musica_ffmpeg(
                    voz_path=arq_audio,
                    musica_path=trilha_path,
                    output_path=audio_mixado,
                    volume_musica=vol_musica,
                    ducking=usar_ducking
                )
                if sucesso_mix and audio_mixado.exists() and audio_mixado.stat().st_size > 1000:
                    arq_audio = str(audio_mixado)
                    clips_temporarios.append(str(audio_mixado))
                    log_event("RENDER_MP4", "Trilha sonora mixada com sucesso na narração.", level="info")
            except Exception as e_mix:
                log_event("RENDER_MP4", f"Falha ao mixar trilha sonora (usando narração original): {e_mix}", level="warning")

        # Duração aproximada = soma das durações das cenas (usada no progresso %)
        duracao_aprox = sum(
            float(c.get("duracao", 5.0)) for c in cenas
            if scene_plan_svc.resolver_arquivo_cena(
                projeto_id, int(c.get("id", 0)), float(c.get("tempo_inicio", 0))
            )
        )

        # Filtro base para normalização de formato e resolução
        _vf_base = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
        _vf_final = _vf_base  # xfade requer inputs separados; mantém vf_base no concat simples

        output_mp4 = pdir / "video_final.mp4"
        cmd = [
            FFMPEG_PATH, "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_txt.resolve()),
        ]
        if arq_audio and Path(arq_audio).exists():
            cmd.extend(["-i", str(Path(arq_audio).resolve()), "-c:a", "aac", "-b:a", "192k", "-shortest"])

        cmd.extend([
            "-vf", _vf_final,
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            str(output_mp4.resolve())
        ])

        # Frente 2: SEMPRE assíncrono (Popen + progresso por stderr).
        job = _RENDER_JOBS.get(projeto_id)
        if job and job.get("status") == "renderizando":
            return jsonify({
                "success": True,
                "status": "renderizando",
                "mensagem": "Renderização já em andamento",
                "job": job
            })

        t = threading.Thread(
            target=_executar_render_background,
            args=(projeto_id, cmd, output_mp4, duracao_aprox, clips_temporarios),
            daemon=True
        )
        t.start()

        return jsonify({
            "success": True,
            "status": "iniciado",
            "mensagem": "Renderização de vídeo iniciada em segundo plano.",
            "job": _RENDER_JOBS.get(projeto_id)
        })
    except Exception as e:
        log_event("RENDER_MP4", f"Erro na renderização: {e}", level="error")
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/montagem/<projeto_id>/render_status", methods=["GET"])
def v2_montagem_render_status(projeto_id: str):
    """Consulta o status em tempo real da renderização de vídeo."""
    job = _RENDER_JOBS.get(projeto_id)
    if not job:
        # Se arquivo já existe no disco, informa concluído
        pdir = _project_dir(projeto_id)
        out_mp4 = pdir / "video_final.mp4"
        if out_mp4.exists():
            return jsonify({
                "success": True,
                "status": "concluido",
                "progresso": 100,
                "arquivo": str(out_mp4),
                "tamanho_bytes": out_mp4.stat().st_size,
                "url_download": f"/api/v2/montagem/{urllib.parse.quote(projeto_id)}/download_video_final"
            })
        return jsonify({"success": True, "status": "idle", "progresso": 0})
    return jsonify({"success": True, **job})


@api_v2_bp.route("/montagem/<projeto_id>/download_video_final", methods=["GET"])
def v2_montagem_download_video(projeto_id: str):
    """Serve o arquivo .mp4 renderizado final para download."""
    pdir = _project_dir(projeto_id)
    out_mp4 = pdir / "video_final.mp4"
    if not out_mp4.exists():
        return jsonify({"success": False, "error": "Vídeo final ainda não foi renderizado."}), 404
    # Correcao ANTIGRAVITY: f-string com backslash na expressao e SyntaxError em Python 3.11.
    safe_proj = re.sub(r'[^\w\-]', '_', projeto_id)
    nome_dl = f"{safe_proj}_video_final.mp4"
    return send_file(str(out_mp4), mimetype="video/mp4", as_attachment=True, download_name=nome_dl)


@api_v2_bp.route("/projeto/<projeto_id>/audio")
def v2_projeto_audio(projeto_id: str):
    """Serve o áudio original do projeto ou trilha customizada para o Player de Montagem."""
    custom_file = request.args.get("file")
    if custom_file and Path(custom_file).exists() and Path(custom_file).is_file():
        ext = Path(custom_file).suffix.lower()
        mime = "audio/mpeg" if ext == ".mp3" else ("audio/wav" if ext == ".wav" else "audio/mp4")
        return send_file(custom_file, mimetype=mime, conditional=True)

    arq_audio = resolver_audio_projeto(projeto_id)
    if not arq_audio:
        return jsonify({"success": False, "error": "Áudio não encontrado"}), 404
    ext = Path(arq_audio).suffix.lower()
    mime = "audio/mpeg" if ext == ".mp3" else ("audio/wav" if ext == ".wav" else "audio/mp4")
    return send_file(arq_audio, mimetype=mime, conditional=True)




# ===========================================================================
# 1.1 CONFIGURAÇÃO DE IDENTIDADE DO PROJETO & MEMÓRIA VISUAL
# ===========================================================================

@api_v2_bp.route("/identidade/<projeto_id>/salvar", methods=["POST"])
@api_v2_bp.route("/projeto/<projeto_id>/identidade", methods=["POST"])
def v2_identidade_salvar(projeto_id: str):
    """
    Salva a identidade permanente do projeto:
    - Opção 1: 'personagem' (Nome, Referência @Nome e Imagem de referência)
    - Opção 2: 'avatar' (Nome, Referência @me nativa do Flow)
    """
    tipo = request.form.get("tipo") or "personagem"
    nome = (request.form.get("nome") or "").strip()
    referencia = (request.form.get("referencia_flow") or "").strip()
    estilo = request.form.get("estilo_visual") or "photorealistic_cinematic"

    if not nome:
        data = request.get_json(silent=True) or {}
        tipo = data.get("tipo") or tipo
        nome = (data.get("nome") or "").strip()
        referencia = (data.get("referencia_flow") or "").strip()
        estilo = data.get("estilo_visual") or estilo

    if not nome:
        return jsonify({"success": False, "error": "Nome do personagem é obrigatório."}), 400

    img_bytes = None
    arquivo = request.files.get("imagem") or request.files.get("foto") or request.files.get("personagem")
    if arquivo and arquivo.filename:
        img_bytes = arquivo.read()

    # Validação de segurança: se tipo for personagem e não tiver imagem nem salva antes
    ident_existente = character_svc.obter_identidade_projeto(projeto_id)
    if tipo == "personagem" and not img_bytes:
        if not ident_existente or not ident_existente.get("imagem_abs") or not Path(ident_existente["imagem_abs"]).exists():
            return jsonify({
                "success": False,
                "error": "Crie o personagem no Google Flow com foto de referência antes de salvar."
            }), 400

    res = character_svc.salvar_identidade_projeto(
        projeto_id=projeto_id,
        tipo=tipo,
        nome=nome,
        referencia_flow=referencia,
        imagem_bytes=img_bytes,
        visual_style=estilo
    )
    return jsonify(res or {"success": True}), 200


# AVATAR FLOW — CRIAÇÃO ASSÍNCRONA (evita "Failed to fetch" por timeout)
# ---------------------------------------------------------------------------
_AVATAR_FLOW_JOBS: Dict[str, Dict[str, Any]] = {}
# Job "executando" mais antigo que isto é considerado PRESO (a automação CDP
# travou/foi fechada no meio) e libera uma nova tentativa — sem isso um único
# travamento bloqueava a criação de avatar do projeto até reiniciar o servidor.
_AVATAR_FLOW_JOB_TIMEOUT_S = 900


def _executar_criar_avatar_background(projeto_id: str, nome: str, estilo: str):
    """Executa a automação do Google Flow em background thread.

    O POST /api/v2/personagem/<id>/criar_flow responde em <100ms; o front
    acompanha via GET /api/v2/personagem/<id>/criar_flow_status (polling).
    """
    job = _AVATAR_FLOW_JOBS.setdefault(projeto_id, {
        "status": "executando",
        "etapa": "Iniciando criação do avatar no Flow...",
        "erro": None,
        "resultado": None,
        "iniciado_em": time.time(),
    })
    job["status"] = "executando"
    job["erro"] = None
    job["etapa"] = "Salvando identidade e enviando foto..."
    job["progresso"] = 5

    try:
        from services.playwright_flow import criar_avatar_flow_via_playwright, log_char

        # VALIDAÇÃO OBRIGATÓRIA (anti-genérico): a automação do Flow só roda
        # com o personagem OFICIAL do projeto lido de identidade.json. Sem
        # identidade válida o job falha com mensagem CLARA em vez de criar
        # um "@avatar" genérico na conta do Google Flow.
        _t_log = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        personagem_projeto, erro_identidade = character_svc.validar_personagem_do_projeto(projeto_id)
        if not personagem_projeto:
            msg = (f"Criação do personagem cancelada: {erro_identidade or 'personagem não configurado'} "
                   f"(nunca criamos personagem genérico).")
            print(f"[{_t_log}] [CHAR] projeto={projeto_id} {msg}", flush=True)
            log_event("AVATAR_FLOW_ERRO", f"[CHAR] {msg}", level="error")
            job["status"] = "erro"
            job["erro"] = msg
            job["etapa"] = f"Erro: {msg}"
            return

        # Usa SEMPRE o nome/tag oficiais da identidade (nunca o valor solto do request).
        nome = personagem_projeto.get("nome") or nome
        try:
            log_char(f"iniciando criação do personagem oficial '{personagem_projeto.get('referencia_flow')}' "
                     f"no Google Flow.", projeto_id=projeto_id)
        except Exception:
            pass

        idt = character_svc.obter_identidade_projeto(projeto_id)
        img_abs = (idt.get("imagem_abs") if idt else None) or personagem_projeto.get("imagem_abs") or ""

        job["etapa"] = "Conectando ao Google Flow (Chrome CDP)..."
        job["progresso"] = 15
        res_flow = criar_avatar_flow_via_playwright(
            projeto_id=projeto_id,
            nome=nome,
            imagem_abs=img_abs,
        )

        if res_flow.get("success"):
            job["status"] = "concluido"
            job["progresso"] = 100
            job["resultado"] = res_flow
            job["etapa"] = "Avatar criado e vinculado com sucesso!"
            job["concluido_em"] = time.time()
        else:
            job["status"] = "erro"
            job["erro"] = res_flow.get("error", "Falha desconhecida")
            job["etapa"] = f"Erro: {job['erro']}"
    except Exception as e:
        job["status"] = "erro"
        job["erro"] = str(e)
        job["etapa"] = f"Erro: {e}"
        log_event("AVATAR_FLOW_ERRO", f"Erro background criar avatar para '{projeto_id}': {e}", level="error")


@api_v2_bp.route("/personagem/<projeto_id>/criar_flow", methods=["POST"])
def v2_personagem_criar_flow(projeto_id: str):
    """
    Cria o avatar no Google Flow via CDP (ASSÍNCRONO):

    O request responde em <100ms com {"status": "iniciado"} e a automação
    roda em background thread. O front acompanha via GET
    /api/v2/personagem/<id>/criar_flow_status (polling).
    """
    try:
        nome = (request.form.get("nome") or "").strip()
        if not nome:
            data = request.get_json(silent=True) or {}
            nome = (data.get("nome") or "").strip()

        if not nome:
            return jsonify({"success": False, "error": "Falha na criação do personagem: nome do personagem não informado."}), 400

        img_bytes = None
        arquivo = request.files.get("imagem") or request.files.get("foto") or request.files.get("personagem")
        if arquivo and arquivo.filename:
            img_bytes = arquivo.read()

        ident_atual = character_svc.obter_identidade_projeto(projeto_id)
        if not img_bytes and (not ident_atual or not ident_atual.get("imagem_abs") or not Path(ident_atual["imagem_abs"]).exists()):
            return jsonify({"success": False, "error": "Falha na criação do personagem: etapa de upload da imagem não concluída (imagem ausente)."}), 400

        estilo = request.form.get("estilo_visual") or "photorealistic_cinematic"
        # CORREÇÃO 2: el front ya envía el nome con '@' (ex: '@Marcos'); sanitizar
        # antes de prefixar evita persistir '@@Marcos' en identidade.json/character.json.
        nome_limpo = str(nome or "").lstrip("@").strip()
        ref_flow = f"@{nome_limpo}"

        # LOG [CHAR] com timestamp (CMD + console web) — rastreia a criação/
        # validação do personagem do projeto (bug do "@avatar" genérico).
        _t_char = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        personagem_existente, _erro_idt_char = character_svc.validar_personagem_do_projeto(projeto_id)
        print(f"[{_t_char}] [CHAR] projeto={projeto_id} personagem solicitado '{ref_flow}' "
              f"(identidade_existente={'sim' if personagem_existente else 'nao'})", flush=True)
        if personagem_existente:
            log_event("AVATAR_FLOW",
                      f"[{_t_char}] [CHAR] projeto={projeto_id} personagem oficial ja configurado: "
                      f"'{personagem_existente.get('referencia_flow')}'.",
                      level="info")
        else:
            log_event("AVATAR_FLOW",
                      f"[{_t_char}] [CHAR] projeto={projeto_id} sem identidade valida "
                      f"({_erro_idt_char}) - salvando '{ref_flow}' antes de criar no Flow.",
                      level="warn")

        # TAREFA 2/3 — identidade.json é a fonte de verdade. Se o projeto JÁ tem
        # personagem oficial, ele prevalece sobre o nome digitado: nunca criamos
        # um segundo personagem nem um genérico no Flow.
        if personagem_existente:
            _nome_oficial = str(personagem_existente.get("nome") or "").strip().lstrip("@").strip()
            _ref_oficial = str(personagem_existente.get("referencia_flow") or "").strip()
            if _nome_oficial:
                nome_limpo = _nome_oficial
                ref_flow = _ref_oficial or f"@{_nome_oficial}"
                if not ref_flow.startswith("@"):
                    ref_flow = f"@{ref_flow}"
                print(f"[{_t_char}] [CHAR] projeto={projeto_id} usando personagem oficial "
                      f"'{ref_flow}' de identidade.json (nome digitado era '{nome}').", flush=True)

        # TAREFA 4 — TRAVA ANTI-GENÉRICO: sem identidade válida, 'avatar'/
        # 'personagem'/'@me' é RECUSADO antes de gravar identidade e antes de
        # abrir o navegador. Antes desta trava a automação criava um "@avatar"
        # genérico na conta do Google Flow no lugar do personagem do projeto.
        elif character_svc.personagem_e_generico(nome_limpo):
            _msg_gen = (f"Falha na criação do personagem: '{nome_limpo or '(vazio)'}' é um nome genérico "
                        f"e não identifica o personagem oficial do projeto. Configure o personagem "
                        f"(ex: @Marcos) na aba Identidade e tente novamente.")
            print(f"[{_t_char}] [CHAR] projeto={projeto_id} {_msg_gen}", flush=True)
            log_event("AVATAR_FLOW_ERRO", f"[CHAR] projeto={projeto_id} {_msg_gen}", level="error")
            return jsonify({"success": False, "error": _msg_gen}), 400

        # 1. Salva a identidade inicial no projeto (rápido, síncrono)
        res_identidade = character_svc.salvar_identidade_projeto(
            projeto_id=projeto_id,
            tipo="personagem",
            nome=nome_limpo or nome,
            referencia_flow=ref_flow,
            imagem_bytes=img_bytes,
            visual_style=estilo
        )
        # TAREFA 3 — falha ao persistir a identidade NÃO pode seguir para o Flow
        # (seria exatamente o caminho do personagem sem identidade/genérico).
        if isinstance(res_identidade, dict) and res_identidade.get("success") is False:
            _erro_idt_salva = res_identidade.get("error") or "falha ao salvar identidade do personagem"
            print(f"[{_t_char}] [CHAR] projeto={projeto_id} criação cancelada: {_erro_idt_salva}", flush=True)
            log_event("AVATAR_FLOW_ERRO", f"[CHAR] projeto={projeto_id} {_erro_idt_salva}", level="error")
            return jsonify({"success": False, "error": _erro_idt_salva}), 400

        # 2. Verifica se já há um job em andamento. IMPORTANTE: o job era criado
        # apenas DENTRO do worker (setdefault), então dois cliques quase simultâneos
        # passavam por esta checagem e subiam DUAS automações CDP no mesmo Chrome —
        # isso deixa o navegador (e a janela de escolha de arquivo) "não respondendo".
        # Agora o job é RESERVADO aqui, antes de subir a thread.
        job = _AVATAR_FLOW_JOBS.get(projeto_id)
        if job and job.get("status") == "executando":
            _idade_s = time.time() - float(job.get("iniciado_em") or 0)
            if _idade_s < _AVATAR_FLOW_JOB_TIMEOUT_S:
                log_event("AVATAR_FLOW",
                          f"[CHAR] projeto={projeto_id} clique ignorado: criação de avatar já em "
                          f"andamento há {int(_idade_s)}s (evita duas automações CDP simultâneas "
                          f"no mesmo Chrome).", level="warn")
                return jsonify({
                    "success": True,
                    "status": "executando",
                    "mensagem": "Criação do avatar já está em andamento.",
                    "job": job,
                })
            # Job preso: libera para uma nova tentativa.
            log_event("AVATAR_FLOW",
                      f"[CHAR] projeto={projeto_id} job anterior preso há {int(_idade_s)}s — "
                      f"liberando para nova tentativa.", level="warn")
            job["status"] = "erro"
            job["erro"] = "Execução anterior expirou (timeout)."

        _AVATAR_FLOW_JOBS[projeto_id] = {
            "status": "executando",
            "etapa": "Iniciando criação do avatar no Flow...",
            "erro": None,
            "resultado": None,
            "progresso": 1,
            "iniciado_em": time.time(),
        }

        # 3. Inicia automação em background (NUNCA bloqueia o request handler)
        #    O nome enviado é o RESOLVIDO (identidade.json prevalece): o
        #    personagem criado no Flow é sempre o oficial do projeto.
        t = threading.Thread(
            target=_executar_criar_avatar_background,
            args=(projeto_id, nome_limpo or nome, estilo),
            daemon=True,
        )
        t.start()

        return jsonify({
            "success": True,
            "status": "iniciado",
            "mensagem": "Criação do avatar no Google Flow iniciada em segundo plano.",
            "job": _AVATAR_FLOW_JOBS.get(projeto_id),
        })
    except Exception as e:
        log_event("AVATAR_FLOW_ERRO", f"Erro ao iniciar criação do avatar para '{projeto_id}': {e}", level="error")
        return jsonify({"success": False, "error": f"Falha ao integrar com Google Flow: {str(e)}"}), 500


@api_v2_bp.route("/personagem/<projeto_id>/criar_flow_status", methods=["GET"])
def v2_personagem_criar_flow_status(projeto_id: str):
    """Consulta o status em tempo real do job assíncrono de criação de avatar."""
    job = _AVATAR_FLOW_JOBS.get(projeto_id)
    if not job:
        return jsonify({"success": True, "status": "idle", "progresso": 0})
    return jsonify({"success": True, **job})


@api_v2_bp.route("/personagens/biblioteca", methods=["GET"])
def v2_personagens_biblioteca():
    """Retorna todos os personagens disponíveis na biblioteca para reutilização."""
    try:
        chars = character_svc.listar_biblioteca_personagens()
        return jsonify({"success": True, "personagens": chars})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "personagens": []}), 500


@api_v2_bp.route("/personagens/biblioteca/vincular", methods=["POST"])
def v2_personagens_biblioteca_vincular():
    """Vincula um personagem existente da biblioteca ao projeto."""
    data = request.get_json(force=True, silent=True) or {}
    projeto_id = data.get("projeto_id")
    nome = data.get("nome")
    if not projeto_id or not nome:
        return jsonify({"success": False, "error": "projeto_id e nome são obrigatórios"}), 400

    res = character_svc.vincular_personagem_da_biblioteca(projeto_id, nome)
    return jsonify(res or {"success": True})


@api_v2_bp.route("/personagens/biblioteca/<nome>/avatar", methods=["GET"])
def v2_personagens_biblioteca_avatar(nome: str):
    """Serve a imagem de referência de um personagem da biblioteca."""
    from config import BIBLIOTECA_DIR
    ref_path = BIBLIOTECA_DIR / "Personagens" / nome / "reference.png"
    if ref_path.exists():
        return send_file(str(ref_path), mimetype="image/png")

    # Fallback nos projetos
    for pdir in PROJETOS_DIR.iterdir():
        if pdir.is_dir():
            p_img = pdir / "characters" / nome / "reference.png"
            if p_img.exists():
                return send_file(str(p_img), mimetype="image/png")

    return jsonify({"error": "Avatar não encontrado"}), 404


@api_v2_bp.route("/identidade/<projeto_id>", methods=["GET"])
@api_v2_bp.route("/projeto/<projeto_id>/identidade", methods=["GET"])
def v2_identidade_obter(projeto_id: str):
    """Retorna a identidade configurada no projeto."""
    identidade = character_svc.obter_identidade_projeto(projeto_id)
    if not identidade:
        return jsonify({"success": True, "has_identity": False, "identidade": None})
    return jsonify({"success": True, "has_identity": True, "identidade": identidade})


@api_v2_bp.route("/identidade/<projeto_id>/remover", methods=["POST", "DELETE"])
@api_v2_bp.route("/projeto/<projeto_id>/identidade", methods=["DELETE"])
def v2_identidade_remover(projeto_id: str):
    """Remove a identidade do projeto."""
    ok = character_svc.remover_identidade_projeto(projeto_id)
    return jsonify({"success": ok})


@api_v2_bp.route("/identidade/<projeto_id>/avatar", methods=["GET"])
@api_v2_bp.route("/projeto/<projeto_id>/identidade/avatar", methods=["GET"])
def v2_identidade_avatar(projeto_id: str):
    """Serve a imagem de referência salva no projeto se houver."""
    identidade = character_svc.obter_identidade_projeto(projeto_id)
    if identidade and identidade.get("imagem_abs"):
        ref_path = Path(identidade["imagem_abs"])
        if ref_path.exists():
            return send_file(str(ref_path), mimetype="image/png")
    return jsonify({"error": "Avatar não encontrado"}), 404


# ---------------------------------------------------------------------------
# ENDPOINTS MULTIRREFERÊNCIA (PERSONAGENS & ESTILOS) — FASE A.1
# ---------------------------------------------------------------------------

@api_v2_bp.route("/referencias/<projeto_id>", methods=["GET"])
def v2_referencias_listar(projeto_id: str):
    """Lista todas as referências (personagens e estilos) cadastradas no projeto."""
    try:
        refs = character_svc.listar_referencias_projeto(projeto_id)
        return jsonify({"success": True, "referencias": refs, "total": len(refs)})
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve), "corrupted": True}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "referencias": []}), 500


@api_v2_bp.route("/referencias/<projeto_id>/adicionar", methods=["POST"])
def v2_referencias_adicionar(projeto_id: str):
    """Adiciona uma nova referência visual (character ou style) no projeto."""
    try:
        data = request.form or {}
        if not data and request.is_json:
            data = request.get_json(silent=True) or {}

        alias = (data.get("alias") or data.get("nome") or "").strip()
        nome = (data.get("nome") or alias).strip()
        tipo = (data.get("tipo") or "character").strip()
        estilo = (data.get("estilo_visual") or "photorealistic_cinematic").strip()
        descricao = (data.get("descricao") or "").strip()

        if not alias:
            return jsonify({"success": False, "error": "Nome ou alias da referência é obrigatório."}), 400

        img_bytes = None
        arquivo = request.files.get("imagem") or request.files.get("foto") or request.files.get("reference")
        if arquivo and arquivo.filename:
            img_bytes = arquivo.read()

        res = character_svc.adicionar_referencia_projeto(
            projeto_id=projeto_id,
            alias=alias,
            nome=nome,
            tipo=tipo,
            imagem_bytes=img_bytes,
            visual_style=estilo,
            descricao=descricao
        )
        return jsonify(res), 201
    except ValueError as ve:
        msg = str(ve)
        if "já existe" in msg:
            return jsonify({"success": False, "error": msg, "conflict": True}), 409
        return jsonify({"success": False, "error": msg}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/referencias/<projeto_id>/<alias>", methods=["PATCH"])
@api_v2_bp.route("/referencias/<projeto_id>/<alias>/renomear", methods=["POST", "PATCH"])
def v2_referencias_renomear(projeto_id: str, alias: str):
    """Renomeia uma referência visual existente."""
    try:
        data = request.get_json(silent=True) or request.form or {}
        novo_nome = (data.get("novo_nome") or data.get("novo_alias") or data.get("nome") or data.get("alias") or "").strip()

        if not novo_nome:
            return jsonify({"success": False, "error": "Novo nome ou alias é obrigatório."}), 400

        res = character_svc.renomear_referencia_projeto(
            projeto_id=projeto_id,
            alias_atual=alias,
            novo_nome_ou_alias=novo_nome
        )
        return jsonify(res), 200
    except KeyError as ke:
        return jsonify({"success": False, "error": str(ke)}), 404
    except ValueError as ve:
        msg = str(ve)
        if "Conflito" in msg or "já existe" in msg:
            return jsonify({"success": False, "error": msg, "conflict": True}), 409
        return jsonify({"success": False, "error": msg}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/referencias/<projeto_id>/<alias>", methods=["DELETE"])
@api_v2_bp.route("/referencias/<projeto_id>/<alias>/remover", methods=["POST", "DELETE"])
def v2_referencias_remover(projeto_id: str, alias: str):
    """Remove uma referência visual do projeto."""
    try:
        ok = character_svc.remover_referencia_projeto(projeto_id, alias)
        return jsonify({"success": ok}), 200
    except KeyError as ke:
        return jsonify({"success": False, "error": str(ke)}), 404
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/referencias/<projeto_id>/<alias>/avatar", methods=["GET"])
def v2_referencias_avatar(projeto_id: str, alias: str):
    """Serve a imagem de referência associada a um alias."""
    ref = character_svc.obter_referencia_por_alias(projeto_id, alias)
    if ref and ref.get("imagem_abs") and Path(ref["imagem_abs"]).exists():
        return send_file(str(ref["imagem_abs"]), mimetype="image/png")
    return jsonify({"error": "Imagem da referência não encontrada"}), 404


@api_v2_bp.route("/referencias/<projeto_id>/<alias>/criar_flow", methods=["POST"])
def v2_referencias_criar_flow(projeto_id: str, alias: str):
    """Dispara a criação nativa do personagem/referência no Google Flow via CDP."""
    try:
        from services.playwright_flow import criar_personagem_no_flow_direto
        ref = character_svc.obter_referencia_por_alias(projeto_id, alias)
        if not ref:
            return jsonify({"success": False, "error": f"Referência '{alias}' não encontrada."}), 404

        img_abs = ref.get("imagem_abs", "")
        if not img_abs or not Path(img_abs).exists():
            return jsonify({"success": False, "error": f"Foto da referência '{alias}' não encontrada no disco."}), 400

        nome_clean = ref.get("nome") or alias.lstrip("@")
        res_flow = criar_personagem_no_flow_direto(projeto_id=projeto_id, nome=nome_clean, imagem_abs=img_abs)
        if res_flow.get("success"):
            flow_id = res_flow.get("flow_character_id", "")
            # CORREÇÃO 2: un único '@' en flow_char_name (evita '@@alias' persistido).
            _ref_exhib = f"@{str(ref.get('alias') or '').lstrip('@') or nome_clean}"
            character_svc.atualizar_status_flow_referencia(
                projeto_id=projeto_id,
                alias=alias,
                created=True,
                flow_char_id=flow_id,
                flow_char_name=_ref_exhib
            )
        return jsonify(res_flow)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500




@api_v2_bp.route("/personagem/<projeto_id>/cadastrar", methods=["POST"])
def v2_personagem_cadastrar(projeto_id: str):
    """Cadastra permanentemente o personagem com imagem e trava sua identidade (retrocompatibilidade)."""
    nome = (request.form.get("nome") or "").strip()
    if not nome:
        data = request.get_json(silent=True) or {}
        nome = (data.get("nome") or "").strip()

    if not nome:
        return jsonify({
            "success": False,
            "error": ("Falha ao cadastrar personagem: nome não informado. Informe o "
                      "personagem oficial do projeto (ex: @Marcos) — nunca criamos "
                      "personagem genérico.")
        }), 400

    arquivo = request.files.get("imagem") or request.files.get("personagem")
    if not arquivo or not arquivo.filename:
        return jsonify({"success": False, "error": "Imagem de referência obrigatória"}), 400

    img_bytes = arquivo.read()
    if not img_bytes:
        return jsonify({"success": False, "error": "Arquivo de imagem vazio"}), 400

    estilo = request.form.get("estilo_visual") or "photorealistic_cinematic"
    # CORREÇÃO 2: sanitizar referência llegada con '@@' (y el fallback sobre nome_limpo).
    nome_limpo = str(nome or "").lstrip("@").strip()
    ref_flow = (request.form.get("referencia_flow") or "").strip() or f"@{nome_limpo}"
    if ref_flow.startswith("@"):
        ref_flow = f"@{ref_flow.lstrip('@')}"

    res = character_svc.salvar_identidade_projeto(
        projeto_id=projeto_id,
        tipo="personagem",
        nome=nome,
        referencia_flow=ref_flow,
        imagem_bytes=img_bytes,
        visual_style=estilo
    )
    return jsonify(res), 201


@api_v2_bp.route("/personagem/<projeto_id>/ativo", methods=["GET"])
def v2_personagem_ativo(projeto_id: str):
    """Retorna os dados completos do personagem ativo no projeto."""
    identidade = character_svc.obter_identidade_projeto(projeto_id)
    if not identidade:
        return jsonify({"success": True, "has_character": False, "character": None})
    return jsonify({
        "success": True,
        "has_character": True,
        "character": {
            "name": identidade.get("nome"),
            "referencia_flow": identidade.get("referencia_flow"),
            "tipo": identidade.get("tipo"),
            "reference_image": identidade.get("imagem"),
            "reference_image_abs": identidade.get("imagem_abs"),
            "has_image": bool(identidade.get("imagem_abs") and Path(identidade["imagem_abs"]).exists())
        }
    })


@api_v2_bp.route("/personagem/<projeto_id>/remover", methods=["POST"])
def v2_personagem_remover(projeto_id: str):
    """Remove o personagem ativo do projeto."""
    ok = character_svc.remover_identidade_projeto(projeto_id)
    return jsonify({"success": ok})


@api_v2_bp.route("/personagem/<projeto_id>/avatar", methods=["GET"])
def v2_personagem_avatar(projeto_id: str):
    """Serve a imagem oficial de referência do personagem ativo."""
    return v2_identidade_avatar(projeto_id)


@api_v2_bp.route("/memoria/<projeto_id>", methods=["GET", "POST"])
def v2_memoria_visual(projeto_id: str):
    """Consulta ou atualiza a memória visual do projeto."""
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        salva = visual_memory_svc.salvar_memoria_visual(projeto_id, data)
        return jsonify({"success": True, "memoria": salva})
    mem = visual_memory_svc.obter_memoria_visual(projeto_id)
    return jsonify({"success": True, "memoria": mem})


@api_v2_bp.route("/producao/<projeto_id>/animar_todos_videos", methods=["POST"])
def v2_producao_animar_todos_videos(projeto_id: str):
    """FASE 2 (SEGUNDA ETAPA): Envia para animação apenas as cenas marcadas como animate_later=true que já possuem imagem gerada."""
    try:
        from services.playwright_flow import FlowQueueWorker, ensure_chrome_cdp
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            return jsonify({"success": False, "error": "Nenhuma cena disponível no planejamento."}), 400

        cenas_video = [
            c["id"] for c in plan["cenas"]
            if (c.get("animate_later") is True or c.get("animar_depois") is True or c.get("tipo") == "video" or c.get("animar") is True)
        ]
        if not cenas_video:
            # Estudo automático do roteiro pelo Animation Director
            scene_plan_svc.reclassificar_animacoes_roteiro(projeto_id)
            plan = scene_plan_svc.carregar_scene_plan(projeto_id)
            cenas_video = [
                c["id"] for c in plan["cenas"]
                if (c.get("animate_later") is True or c.get("animar_depois") is True or c.get("tipo") == "video" or c.get("animar") is True)
            ]

        if not cenas_video:
            return jsonify({"success": False, "error": "Nenhuma cena classificada para animação de acordo com o roteiro."}), 400

        import sys
        is_testing = getattr(current_app, "testing", False) or current_app.config.get("TESTING", False) or ("unittest" in sys.modules)
        if not is_testing:
            success, msg = ensure_chrome_cdp(9222)
            if not success:
                return jsonify({"success": False, "error": f"Chrome CDP falhou: {msg}"}), 500
        ok = FlowQueueWorker.start_worker(projeto_id, scene_ids=cenas_video, modo="animacao")
        if not ok and FlowQueueWorker.get_worker().is_running_queue:
            return jsonify({"success": True, "already_running": True, "total_animar": len(cenas_video), "scene_ids": cenas_video})
        return jsonify({"success": ok, "total_animar": len(cenas_video), "scene_ids": cenas_video})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/cena_media/<projeto_id>/<int:scene_id>", methods=["GET"])
def v2_cena_media(projeto_id: str, scene_id: int):
    """Serve a mídia física da cena diretamente a partir das pastas estruturadas do projeto."""
    from app_web import _arquivo_midia_cena
    arquivo = _arquivo_midia_cena(projeto_id, scene_id)
    if not arquivo or not Path(arquivo).exists():
        # ITEM 7 — o 404 agora explica POR QUE: o arquivo registrado na cena não existe
        # mais em disco (excluído manualmente / movido). O frontend usa `reason` para
        # parar o polling e mostrar "Arquivo deletado — aguardando reconstrução"
        # em vez de exibir um erro genérico.
        return jsonify({
            "success": False,
            "error": "Mídia não encontrada",
            "reason": "arquivo_deletado",
            "cena_id": scene_id,
            "scene_id": scene_id,
            "projeto_id": projeto_id,
        }), 404
    ext = Path(arquivo).suffix.lower()
    mimetypes = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".mp4": "video/mp4", ".webm": "video/webm",
        ".mov": "video/quicktime",
    }
    mime = mimetypes.get(ext, "application/octet-stream")
    return send_file(arquivo, mimetype=mime)


@api_v2_bp.route("/diretor3/<projeto_id>", methods=["GET"])
def v2_diretor3_dados(projeto_id: str):
    """Retorna a visão holística do Diretor 3.0: Projeto, Memória Visual, Personagem, Retenção e Qualidade."""
    import services.visual_memory_engine as vme_svc
    import services.character_service as character_svc
    import services.scene_plan_service as scene_plan_svc
    import services.content_learning_engine as learning_svc
    from config import PROJETOS_DIR

    pdir = PROJETOS_DIR / projeto_id
    if not pdir.exists():
        return jsonify({"success": False, "error": "Projeto não encontrado"}), 404

    # 1. Memória Visual
    mem_visual = vme_svc.obter_memoria_visual_projeto(projeto_id)

    # 2. Contexto Visual Macro
    p_ctx = pdir / "project_visual_context.json"
    ctx_visual = {}
    if p_ctx.exists():
        try:
            ctx_visual = json.loads(p_ctx.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 3. Identidade
    identidade = character_svc.obter_identidade_projeto(projeto_id) or {}

    # 4. Scene Plan & Métricas
    plan = scene_plan_svc.carregar_scene_plan(projeto_id) or {"cenas": []}
    cenas = plan.get("cenas", [])

    total_cenas = len(cenas)
    avatar_cenas = sum(1 for c in cenas if c.get("uses_character"))
    broll_cenas = total_cenas - avatar_cenas
    
    scores_visuais = [c.get("visual_score", 0) for c in cenas if c.get("visual_score", 0) > 0]
    avg_score = int(sum(scores_visuais) / len(scores_visuais)) if scores_visuais else (95 if total_cenas > 0 else 0)

    ret_scores = [c.get("retention_index", 85) for c in cenas]
    avg_ret = int(sum(ret_scores) / len(ret_scores)) if ret_scores else 90
    pacing_grade = "A+" if avg_ret >= 92 else ("A" if avg_ret >= 82 else "B")

    # 5. Aprendizado
    recs = learning_svc.obter_recomendacoes_aprendidas()

    return jsonify({
        "success": True,
        "projeto_id": projeto_id,
        "visual_memory": mem_visual,
        "visual_context": ctx_visual,
        "identidade": identidade,
        "learning_insights": recs,
        "total_cenas": total_cenas,
        "summary": {
            "total_cenas": total_cenas,
            "avatar_cenas": avatar_cenas,
            "broll_cenas": broll_cenas,
            "avg_visual_score": avg_score,
            "project_retention_score": avg_ret,
            "pacing_grade": pacing_grade,
            "character_name": identidade.get("nome") or mem_visual.get("personagem", {}).get("name", ""),
            "character_ref": identidade.get("referencia_flow") or mem_visual.get("personagem", {}).get("reference", ""),
            "world": mem_visual.get("ambiente", {}).get("location", "Rustic Botanical Garden")
        }
    })


@api_v2_bp.route("/autonomous_direct/<projeto_id>", methods=["POST"])
def v2_autonomous_direct(projeto_id: str):
    """Executa a direção autônoma do projeto pelo Autonomous Director AI."""
    import services.autonomous_director_service as auto_director_svc
    data = request.get_json(silent=True) or {}
    roteiro = data.get("roteiro") or data.get("srt_texto") or data.get("texto") or ""
    nome_pers = data.get("nome_personagem", "")
    estilo = data.get("estilo_visual", "photorealistic_cinematic")

    if not roteiro.strip():
        return jsonify({"success": False, "error": "Roteiro ou SRT é obrigatório para direção autônoma."}), 400

    res = auto_director_svc.dirigir_producao_autonoma(
        projeto_id=projeto_id,
        roteiro_texto=roteiro,
        nome_personagem=nome_pers,
        estilo_visual=estilo
    )
    status_code = 200 if res.get("success") else 500
    return jsonify(res), status_code


# ===========================================================================
# BRAIN SYSTEM DIRECTOR — SERVIÇO, IDEMPOTÊNCIA & STATUS EM BACKGROUND
# ===========================================================================

import hashlib

_BRAIN_STATUS_LOCK = threading.Lock()
_BRAIN_STATUS: Dict[str, Dict[str, Any]] = {}
_BRAIN_THREADS: Dict[str, threading.Thread] = {}

# ===========================================================================
# BRAIN MAESTRO v4.0 — ESTADO GLOBAL, INSTÂNCIAS E LOCKS DE CONCORRÊNCIA
# ===========================================================================
_MAESTRO_STATUS_LOCK = threading.Lock()
_MAESTRO_INSTANCES: Dict[str, Any] = {}
_MAESTRO_THREADS: Dict[str, threading.Thread] = {}
_MAESTRO_STATUS: Dict[str, Dict[str, Any]] = {}



def calcular_hash_insumos_brain(projeto_id: str) -> str:
    """Calcula hash determinístico dos insumos vitais do Brain (word_timestamps + cenas + áudio)."""
    pdir = PROJETOS_DIR / projeto_id
    h = hashlib.sha256()

    # 1. word_timestamps.json
    wt_path = pdir / "word_timestamps.json"
    if wt_path.exists():
        try:
            h.update(wt_path.read_bytes())
        except Exception:
            h.update(str(wt_path.stat().st_mtime).encode())
    else:
        h.update(b"no_wt")

    # 2. cenas.json
    cenas_path = pdir / "cenas.json"
    if cenas_path.exists():
        try:
            h.update(cenas_path.read_bytes())
        except Exception:
            h.update(str(cenas_path.stat().st_mtime).encode())
    else:
        h.update(b"no_cenas")

    # 3. Áudio
    audio_path = resolver_audio_projeto(projeto_id)
    if audio_path and os.path.exists(audio_path):
        try:
            stat = os.stat(audio_path)
            h.update(f"{stat.st_size}_{stat.st_mtime}".encode())
        except Exception:
            h.update(b"audio_stat_err")
    else:
        h.update(b"no_audio")

    # 4. manual_overrides.json
    ov_path = pdir / "manual_overrides.json"
    if ov_path.exists():
        try:
            h.update(ov_path.read_bytes())
        except Exception:
            h.update(str(ov_path.stat().st_mtime).encode())
    else:
        h.update(b"no_overrides")

    return h.hexdigest()[:16]


def obter_status_brain(projeto_id: str) -> dict:
    """Retorna o status atual do Brain System Director para o projeto."""
    with _BRAIN_STATUS_LOCK:
        mem = _BRAIN_STATUS.get(projeto_id)
        if mem and mem.get("status") == "rodando":
            return dict(mem)

    pdir = PROJETOS_DIR / projeto_id
    out_p = pdir / "brain_director_output.json"

    if out_p.exists():
        try:
            dados = json.loads(out_p.read_text(encoding="utf-8"))
            metricas = dados.get("metricas", {})
            cenas = dados.get("cenas", {})
            dur_s = metricas.get("duracao_total_s")
            if dur_s is None:
                dur_s = (metricas.get("duracao_total_ms", 0) / 1000.0)
            return {
                "status": "ok",
                "mensagem": "Brain System Director sincronizado",
                "total_cenas": metricas.get("total_cenas", len(cenas)),
                "total_transicoes": metricas.get("total_transicoes", 0),
                "total_story_cards": metricas.get("total_story_cards", 0),
                "total_sfx": metricas.get("total_sfx", 0),
                "duracao_total_s": round(float(dur_s), 2),
                "executado_em": dados.get("gerado_em") or metricas.get("gerado_em", ""),
                "hash": dados.get("_insumos_hash", "")
            }
        except Exception:
            pass

    with _BRAIN_STATUS_LOCK:
        if mem and mem.get("status") == "falhou":
            return dict(mem)

    return {
        "status": "pendente",
        "mensagem": "Aguardando execução do Brain Director",
        "total_cenas": 0,
        "total_transicoes": 0,
        "total_story_cards": 0,
        "total_sfx": 0,
        "duracao_total_s": 0.0,
        "executado_em": "",
        "hash": ""
    }


def executar_brain_system_director(
    projeto_id: str,
    entrada: Optional[Dict[str, Any]] = None,
    options: Optional[Dict[str, Any]] = None,
    force: bool = False,
    eeat_enabled: bool = True
) -> Dict[str, Any]:
    """
    Executa o Brain System Director unificado de forma síncrona com suporte a idempotência
    e preservação de edições manuais.
    """
    import services.brain_system_director as brain_svc
    pdir = PROJETOS_DIR / projeto_id
    if not pdir.exists():
        raise FileNotFoundError(f"Projeto '{projeto_id}' não encontrado em {pdir}")

    h_atual = calcular_hash_insumos_brain(projeto_id)
    out_p = pdir / "brain_director_output.json"

    # IDEMPOTÊNCIA: se já existe e o hash dos insumos é idêntico e não forçado, pula
    if out_p.exists() and not force:
        try:
            dados_existentes = json.loads(out_p.read_text(encoding="utf-8"))
            if dados_existentes.get("_insumos_hash") == h_atual:
                log_event("BRAIN_DIRECTOR", f"Insumos inalterados (hash {h_atual}) para '{projeto_id}'. Execução do Brain ignorada (idempotente).", level="info")
                with _BRAIN_STATUS_LOCK:
                    m = dados_existentes.get("metricas", {})
                    _BRAIN_STATUS[projeto_id] = {
                        "status": "ok",
                        "mensagem": "Brain sincronizado (idempotente)",
                        "total_cenas": m.get("total_cenas", len(dados_existentes.get("cenas", {}))),
                        "total_transicoes": m.get("total_transicoes", 0),
                        "total_story_cards": m.get("total_story_cards", 0),
                        "total_sfx": m.get("total_sfx", 0),
                        "duracao_total_s": round(float(m.get("duracao_total_s") or (m.get("duracao_total_ms", 0) / 1000.0)), 2),
                        "executado_em": dados_existentes.get("gerado_em", ""),
                        "hash": h_atual
                    }
                return {
                    "success": True,
                    "projeto_id": projeto_id,
                    "pulou": True,
                    "motivo": "idempotente",
                    "hash": h_atual,
                    "output": dados_existentes,
                    "metricas": dados_existentes.get("metricas", {}),
                    "deepseek": dados_existentes.get("deepseek", {
                        "status": "ok",
                        "erro": None,
                        "prompts_fonte": "deepseek"
                    })
                }
        except Exception:
            pass

    opts = {
        "include_transicoes": True,
        "include_story_cards": True,
        "include_audio_bgm": True,
        "include_efeitos_visuais": True,
        "include_animacoes": True,
        "include_sfx": True,
        "smart_fx_modo": "sugestoes",
    }
    if options and isinstance(options, dict):
        opts.update(options)

    if not entrada or not isinstance(entrada, dict):
        cenas_raw = []
        cenas_p = pdir / "cenas.json"
        plan_p = pdir / "lira_scene_plan.json"
        if cenas_p.exists():
            try:
                cenas_raw = json.loads(cenas_p.read_text(encoding="utf-8"))
            except Exception:
                pass
        elif plan_p.exists():
            try:
                plan_data = json.loads(plan_p.read_text(encoding="utf-8"))
                cenas_raw = plan_data.get("cenas", [])
            except Exception:
                pass

        if not cenas_raw:
            raise ValueError(f"Nenhuma cena encontrada no projeto '{projeto_id}' para processar.")

        transcricao_text = " ".join(str(c.get("texto") or c.get("narration") or "") for c in cenas_raw)
        word_ts = []
        wt_path = pdir / "word_timestamps.json"
        if wt_path.exists():
            try:
                wt_data = json.loads(wt_path.read_text(encoding="utf-8"))
                word_ts = wt_data.get("segments", [])
            except Exception:
                pass

        import services.character_service as char_svc
        idt = char_svc.obter_identidade_projeto(projeto_id) or {}
        personagem = idt.get("referencia_flow") or (f"@{idt.get('nome')}" if idt.get("nome") else "@Marcos")
        estilo_visual = idt.get("visual_style") or "photorealistic_cinematic"

        audio_file = resolver_audio_projeto(projeto_id) or ""
        dur_total = 0.0
        for c in cenas_raw:
            d_val = c.get("duracao") or c.get("duration")
            if d_val is not None:
                try:
                    dur_total += float(d_val)
                    continue
                except (ValueError, TypeError):
                    pass
            e_val = c.get("end") if c.get("end") is not None else c.get("end_time")
            s_val = c.get("start") if c.get("start") is not None else c.get("start_time")
            if e_val is not None and s_val is not None:
                try:
                    dur_total += max(0.0, float(e_val) - float(s_val))
                except (ValueError, TypeError):
                    dur_total += 4.0
            else:
                dur_total += 4.0

        entrada = {
            "projeto_id": projeto_id,
            "roteiro_transcricao": {"text": transcricao_text, "duration": dur_total},
            "word_timestamps": word_ts,
            "cenas": cenas_raw,
            "audio_metadata": {
                "path": audio_file,
                "duracao_total": dur_total,
                "silencio_table": []
            },
            "personagem": personagem,
            "estilo_visual": estilo_visual
        }

    # Guarda campos editados manualmente no scene_plan existente antes do merge
    edicoes_manuais = {}
    plan_atual = scene_plan_svc.carregar_scene_plan(projeto_id)
    if plan_atual and plan_atual.get("cenas") and not force:
        for c in plan_atual["cenas"]:
            cid = c.get("id")
            if not cid:
                continue
            preservar = {}
            if c.get("human_status") in ("approved", "revision_requested"):
                preservar["human_status"] = c["human_status"]
                if c.get("human_note"):
                    preservar["human_note"] = c["human_note"]
            if c.get("aprovado_em_lote"):
                preservar["aprovado_em_lote"] = True
            if c.get("motion_preset"):
                preservar["motion_preset"] = c["motion_preset"]
            if c.get("caption_custom"):
                preservar["caption_custom"] = c["caption_custom"]
            if "legenda_ativa" in c:
                preservar["legenda_ativa"] = c["legenda_ativa"]
            if c.get("status"):
                preservar["status"] = c["status"]
            if c.get("image_status"):
                preservar["image_status"] = c["image_status"]
            if c.get("caminho_imagem"):
                preservar["caminho_imagem"] = c["caminho_imagem"]
            if c.get("caminho_video"):
                preservar["caminho_video"] = c["caminho_video"]
            if c.get("avatar_style"):
                preservar["avatar_style"] = c["avatar_style"]
            if c.get("manual_override"):
                preservar["manual_override"] = True
                for fld in ("narrative_role", "uses_character", "character_ref", "scene_type", "prompt_imagem", "prompt_animacao", "visual_prompt", "imagem_com_personagem", "avatar_style"):
                    if c.get(fld) is not None:
                        preservar[fld] = c[fld]
            if preservar:
                edicoes_manuais[cid] = preservar

    # Executa orquestração
    director = brain_svc.BrainSystemDirector(projeto_id=projeto_id)
    saida = director.orquestar_producao_completa(entrada_consolidada=entrada, options=opts, eeat_enabled=eeat_enabled)
    saida["_insumos_hash"] = h_atual

    # Grava hash junto no output JSON para verificações futuras
    try:
        out_p.write_text(json.dumps(saida, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as _e_save:
        log_event("BRAIN_DIRECTOR", f"Aviso ao salvar _insumos_hash: {_e_save}", level="warn")

    # Reaplica edições manuais salvas se não forçado
    if edicoes_manuais and not force:
        plan_pos = scene_plan_svc.carregar_scene_plan(projeto_id)
        if plan_pos and plan_pos.get("cenas"):
            alterado = False
            for c in plan_pos["cenas"]:
                cid = c.get("id")
                if cid in edicoes_manuais:
                    c.update(edicoes_manuais[cid])
                    alterado = True
            if alterado:
                scene_plan_svc.salvar_scene_plan(projeto_id, plan_pos)

    metricas = saida.get("metricas", {})
    with _BRAIN_STATUS_LOCK:
        _BRAIN_STATUS[projeto_id] = {
            "status": "ok",
            "mensagem": "Brain System Director concluído",
            "total_cenas": metricas.get("total_cenas", len(saida.get("cenas", {}))),
            "total_transicoes": metricas.get("total_transicoes", 0),
            "total_story_cards": metricas.get("total_story_cards", 0),
            "total_sfx": metricas.get("total_sfx", 0),
            "duracao_total_s": round(float(metricas.get("duracao_total_s") or (metricas.get("duracao_total_ms", 0) / 1000.0)), 2),
            "executado_em": saida.get("gerado_em") or metricas.get("gerado_em", ""),
            "hash": h_atual
        }

    return {
        "success": True,
        "projeto_id": projeto_id,
        "hash": h_atual,
        "output": saida,
        "metricas": metricas,
        "deepseek": saida.get("deepseek", {
            "status": "ok",
            "erro": None,
            "prompts_fonte": "deepseek"
        })
    }


def disparar_brain_background(projeto_id: str, force: bool = False, options: Optional[Dict[str, Any]] = None) -> bool:
    """
    Dispara o Brain em background thread (idempotente e não-bloqueante).
    Se falhar, registra o status 'falhou' sem quebrar o pipeline.
    """
    with _BRAIN_STATUS_LOCK:
        th_existente = _BRAIN_THREADS.get(projeto_id)
        if th_existente and th_existente.is_alive():
            log_event("BRAIN_DIRECTOR", f"Brain já está em execução para '{projeto_id}'. Chamada concorrente ignorada.", level="info")
            return False

        _BRAIN_STATUS[projeto_id] = {
            "status": "rodando",
            "mensagem": "Processando 12 inteligências audiovisuais...",
            "total_cenas": 0,
            "total_transicoes": 0,
            "total_story_cards": 0,
            "total_sfx": 0,
            "duracao_total_s": 0.0,
            "executado_em": "",
            "hash": ""
        }

    def _worker():
        try:
            eeat = bool(options.get("eeat_enabled", True)) if options else True
            executar_brain_system_director(projeto_id, entrada=None, options=options, force=force, eeat_enabled=eeat)
        except Exception as e:
            log_event("BRAIN_DIRECTOR", f"Falha no Brain Director em background para '{projeto_id}': {e}", level="warn")
            with _BRAIN_STATUS_LOCK:
                _BRAIN_STATUS[projeto_id] = {
                    "status": "falhou",
                    "mensagem": f"Erro no Brain: {str(e)}",
                    "total_cenas": 0,
                    "total_transicoes": 0,
                    "total_story_cards": 0,
                    "total_sfx": 0,
                    "duracao_total_s": 0.0,
                    "executado_em": "",
                    "hash": ""
                }

    t = threading.Thread(target=_worker, daemon=True, name=f"BrainDirector-{projeto_id}")
    with _BRAIN_STATUS_LOCK:
        _BRAIN_THREADS[projeto_id] = t
    t.start()
    return True


@api_v2_bp.route("/producao/<projeto_id>/brain_status", methods=["GET"])
def v2_producao_brain_status(projeto_id: str):
    """
    GET /api/v2/producao/<projeto_id>/brain_status
    Retorna o status do Brain: pendente, rodando, ok, falhou e contadores.
    """
    st = obter_status_brain(projeto_id)
    return jsonify({"success": True, "projeto_id": projeto_id, **st})


@api_v2_bp.route("/producao/<projeto_id>/gerar_brain_completo", methods=["POST"])
def v2_gerar_brain_completo(projeto_id: str):
    """
    POST /api/v2/producao/<projeto_id>/gerar_brain_completo
    Executa o Brain System Director unificado, consumindo dados do projeto em disco
    ou payload enviado, e produz o mapa audiovisual atômico por cena.
    """
    pdir = PROJETOS_DIR / projeto_id
    if not pdir.exists():
        return jsonify({"success": False, "error": f"Projeto '{projeto_id}' não encontrado"}), 404

    data = request.get_json(silent=True) or {}
    options = {
        "include_transicoes": bool(data.get("include_transicoes", True)),
        "include_story_cards": bool(data.get("include_story_cards", True)),
        "include_audio_bgm": bool(data.get("include_audio_bgm", True)),
        "include_efeitos_visuais": bool(data.get("include_efeitos_visuais", True)),
        "include_animacoes": bool(data.get("include_animacoes", True)),
        "include_sfx": bool(data.get("include_sfx", True)),
        "smart_fx_modo": str(data.get("smart_fx_modo", "sugestoes")),
    }
    entrada = data.get("entrada_consolidada")
    force = bool(data.get("force", False))
    eeat_enabled = bool(data.get("eeat_enabled", True))

    try:
        res = executar_brain_system_director(projeto_id, entrada=entrada, options=options, force=force, eeat_enabled=eeat_enabled)
        saida = res.get("output", {})
        metricas = res.get("metricas", {})

        return jsonify({
            "success": True,
            "projeto_id": projeto_id,
            "brain_json_url": f"/api/v2/arquivos/{projeto_id}/download/documentos/brain_director_output.json",
            "cenas_processadas": metricas.get("total_cenas", len(saida.get("cenas", {}))),
            "duracao_total_ms": metricas.get("duracao_total_ms", 0),
            "efeitos_gerados": {
                "total_transicoes": metricas.get("total_transicoes", 0),
                "total_story_cards": metricas.get("total_story_cards", 0),
                "total_sfx": metricas.get("total_sfx", 0),
            },
            "output": saida,
            "deepseek": res.get("deepseek") or saida.get("deepseek", {
                "status": "ok",
                "erro": None,
                "prompts_fonte": "deepseek"
            })
        })
    except Exception as e:
        log_event("BRAIN_DIRECTOR", f"Erro no endpoint gerar_brain_completo: {e}", level="error")
        return jsonify({"success": False, "error": str(e)}), 500


# ===========================================================================
# BRAIN MAESTRO v4.0 — WORKER, DISPARO E ENDPOINTS REST
# ===========================================================================

def _maestro_worker(
    projeto_id: str,
    maestro: Any,
    audio_path: Optional[str],
    avatar_metadata: Optional[Dict[str, Any]],
    options: Optional[Dict[str, Any]]
) -> None:
    """Worker executado na thread daemon em background para o Brain Maestro v4.0."""
    log_event("MAESTRO", f"Iniciando thread de execução Maestro para '{projeto_id}' (Job {maestro.job_id})", level="info")
    try:
        resultado = maestro.executar(
            audio_path=audio_path,
            avatar_metadata=avatar_metadata,
            options=options
        )
        with _MAESTRO_STATUS_LOCK:
            _MAESTRO_STATUS[projeto_id] = {
                "success": bool(resultado.get("sucesso", False)),
                "status": resultado.get("status", "CONCLUIDO"),
                "job_id": maestro.job_id,
                "resultado": resultado,
                "concluido_em": datetime.now().isoformat()
            }
        log_event("MAESTRO", f"Maestro finalizado para '{projeto_id}': status={resultado.get('status')}", level="info")
    except Exception as e:
        log_event("MAESTRO", f"Falha na execução do Maestro para '{projeto_id}': {e}", level="error")
        with _MAESTRO_STATUS_LOCK:
            _MAESTRO_STATUS[projeto_id] = {
                "success": False,
                "status": "ERRO",
                "error": str(e),
                "job_id": maestro.job_id,
                "falhou_em": datetime.now().isoformat()
            }
    finally:
        with _MAESTRO_STATUS_LOCK:
            _MAESTRO_THREADS.pop(projeto_id, None)


def disparar_maestro_background(
    projeto_id: str,
    audio_path: Optional[str] = None,
    avatar_metadata: Optional[Dict[str, Any]] = None,
    options: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Inicializa o BrainMaestroV4 e despacha sua execução em thread daemon.
    Garante atomicidade, prevenção de concorrência e retorno de job_id.
    """
    from services.brain_maestro_v4 import BrainMaestroV4

    pdir = _project_dir(projeto_id)
    if not pdir.exists():
        return {
            "success": False,
            "error": f"Projeto '{projeto_id}' não encontrado",
            "codigo_erro": "PROJETO_NAO_ENCONTRADO"
        }

    resolved_audio = audio_path or resolver_audio_projeto(projeto_id)
    if not resolved_audio or not Path(resolved_audio).exists():
        return {
            "success": False,
            "error": f"Arquivo de áudio não encontrado para o projeto '{projeto_id}'",
            "codigo_erro": "AUDIO_NOT_FOUND"
        }

    with _MAESTRO_STATUS_LOCK:
        th_existente = _MAESTRO_THREADS.get(projeto_id)
        if th_existente and th_existente.is_alive():
            maestro_ativo = _MAESTRO_INSTANCES.get(projeto_id)
            return {
                "success": False,
                "error": f"Orquestração Maestro já em andamento para '{projeto_id}'",
                "codigo_erro": "MAESTRO_ALREADY_RUNNING",
                "job_id_ativo": getattr(maestro_ativo, "job_id", "")
            }

        stop_event = threading.Event()
        maestro = BrainMaestroV4(projeto_id=projeto_id, stop_event=stop_event)
        _MAESTRO_INSTANCES[projeto_id] = maestro

        t = threading.Thread(
            target=_maestro_worker,
            args=(projeto_id, maestro, resolved_audio, avatar_metadata, options),
            daemon=True,
            name=f"BrainMaestro-{projeto_id}"
        )
        _MAESTRO_THREADS[projeto_id] = t
        _MAESTRO_STATUS[projeto_id] = {
            "success": True,
            "status": "iniciado",
            "job_id": maestro.job_id,
            "etapas_totais": 8,
            "iniciado_em": datetime.now().isoformat()
        }
        t.start()

    return {
        "success": True,
        "job_id": maestro.job_id,
        "status": "iniciado",
        "etapas_totais": 8,
        "status_url": f"/api/v2/producao/{projeto_id}/maestro/status"
    }


@api_v2_bp.route("/producao/<projeto_id>/maestro/iniciar", methods=["POST"])
def v2_maestro_iniciar(projeto_id: str):
    """
    POST /api/v2/producao/<projeto_id>/maestro/iniciar
    Dispara o orquestrador Brain Maestro v4.0 em background para o projeto.
    """
    pdir = _project_dir(projeto_id)
    if not pdir.exists():
        return jsonify({
            "success": False,
            "error": f"Projeto '{projeto_id}' não encontrado",
            "codigo_erro": "PROJETO_NAO_ENCONTRADO"
        }), 404

    data = request.get_json(silent=True) or {}
    audio_path = data.get("arquivo_audio")
    avatar_metadata = data.get("avatar")
    if isinstance(avatar_metadata, str):
        avatar_metadata = {"alias": avatar_metadata}

    options = {
        "estilo_visual": data.get("estilo_visual", "photorealistic_cinematic"),
        "etapas_ativas": data.get("etapas_ativas"),
        "num_contas_flow": int(data.get("num_contas_flow", 3)),
        "pular_flow": bool(data.get("pular_flow", False)),
        "forcar": bool(data.get("forcar", False)),
        "include_audio_bgm": bool(data.get("include_audio_bgm", True)),
        "include_sfx": bool(data.get("include_sfx", True)),
        "include_story_cards": bool(data.get("include_story_cards", True)),
        "include_transicoes": bool(data.get("include_transicoes", True)),
        "include_animacoes": bool(data.get("include_animacoes", True)),
        "pasta_capcut": data.get("pasta_capcut")
    }

    res = disparar_maestro_background(
        projeto_id=projeto_id,
        audio_path=audio_path,
        avatar_metadata=avatar_metadata,
        options=options
    )

    if not res.get("success"):
        status_code = 409 if res.get("codigo_erro") == "MAESTRO_ALREADY_RUNNING" else 400
        return jsonify(res), status_code

    return jsonify({
        "success": True,
        "projeto_id": projeto_id,
        **res
    }), 202


@api_v2_bp.route("/producao/<projeto_id>/maestro/status", methods=["GET"])
def v2_maestro_status(projeto_id: str):
    """
    GET /api/v2/producao/<projeto_id>/maestro/status
    Retorna telemetria granular em tempo real para os 8 sub-sistemas do Maestro v4.0.
    """
    pdir = _project_dir(projeto_id)
    if not pdir.exists():
        return jsonify({
            "success": False,
            "error": f"Projeto '{projeto_id}' não encontrado",
            "codigo_erro": "PROJETO_NAO_ENCONTRADO"
        }), 404

    # 1. Se houver instância de BrainMaestroV4 em memória, extrai snapshot thread-safe
    with _MAESTRO_STATUS_LOCK:
        maestro = _MAESTRO_INSTANCES.get(projeto_id)
        if maestro and hasattr(maestro, "get_progresso"):
            progresso = maestro.get_progresso()
            return jsonify({
                "success": True,
                "projeto_id": projeto_id,
                **progresso
            }), 200

    # 2. Se não houver instância em memória, lê do arquivo de checkpoint em disco
    chk_file = pdir / "brain_maestro_checkpoint.json"
    if chk_file.exists():
        try:
            chk_data = json.loads(chk_file.read_text(encoding="utf-8"))
            progresso = chk_data.get("progresso", {})
            return jsonify({
                "success": True,
                "projeto_id": projeto_id,
                "job_id": chk_data.get("job_id", ""),
                "estado_atual": chk_data.get("status", "CONCLUIDO"),
                "etapa_atual": chk_data.get("etapa_atual", ""),
                "progresso_global_pct": progresso.get("progresso_global_pct", 100 if chk_data.get("status") == "CONCLUIDO" else 0),
                "etapas_concluidas": chk_data.get("etapas_concluidas", []),
                "sub_sistemas": progresso.get("sub_sistemas", {}),
                "origem": "checkpoint_disco"
            }), 200
        except Exception:
            pass

    # 3. Fallback: nenhuma orquestração registrada
    return jsonify({
        "success": True,
        "projeto_id": projeto_id,
        "job_id": "",
        "estado_atual": "IDLE",
        "etapa_atual": "",
        "progresso_global_pct": 0,
        "etapas_concluidas": [],
        "sub_sistemas": {},
        "status": "pendente",
        "mensagem": "Nenhuma orquestração Maestro registrada para este projeto."
    }), 200


@api_v2_bp.route("/producao/<projeto_id>/maestro/cancelar", methods=["POST"])
def v2_maestro_cancelar(projeto_id: str):
    """
    POST /api/v2/producao/<projeto_id>/maestro/cancelar
    Interrompe a execução ativa do Brain Maestro v4.0 para o projeto.
    """
    pdir = _project_dir(projeto_id)
    if not pdir.exists():
        return jsonify({
            "success": False,
            "error": f"Projeto '{projeto_id}' não encontrado",
            "codigo_erro": "PROJETO_NAO_ENCONTRADO"
        }), 404

    with _MAESTRO_STATUS_LOCK:
        th = _MAESTRO_THREADS.get(projeto_id)
        maestro = _MAESTRO_INSTANCES.get(projeto_id)
        if not th or not th.is_alive() or not maestro:
            return jsonify({
                "success": False,
                "error": f"Nenhum processo Maestro ativo para o projeto '{projeto_id}'.",
                "codigo_erro": "NO_ACTIVE_MAESTRO"
            }), 409

        maestro.cancelar()
        _MAESTRO_STATUS[projeto_id] = {
            "success": False,
            "status": "CANCELADO",
            "job_id": maestro.job_id,
            "cancelado_em": datetime.now().isoformat()
        }

    return jsonify({
        "success": True,
        "projeto_id": projeto_id,
        "status": "CANCELADO",
        "mensagem": "Solicitação de cancelamento enviada com sucesso ao Maestro."
    }), 200


@api_v2_bp.route("/metrics/<projeto_id>", methods=["GET"])
def v2_production_metrics(projeto_id: str):
    """Retorna o relatório consolidado de telemetria e métricas de desempenho da produção."""
    import services.production_metrics_engine as metrics_engine_svc
    from config import PROJETOS_DIR

    pdir = PROJETOS_DIR / projeto_id
    if not pdir.exists():
        return jsonify({"success": False, "error": "Projeto não encontrado"}), 404

    metricas = metrics_engine_svc.obter_metricas_producao(projeto_id)
    if not metricas:
        metricas = metrics_engine_svc.calcular_e_salvar_metricas(projeto_id)

    return jsonify({"success": True, "metrics": metricas})


@api_v2_bp.route("/human_feedback/<projeto_id>/<int:scene_id>", methods=["POST"])
def v2_human_feedback(projeto_id: str, scene_id: int):
    """Registra aprovação ou solicitação de revisão humana para uma cena."""
    import services.human_feedback_service as feedback_svc
    import services.project_version_service as version_svc
    
    data = request.get_json(silent=True) or {}
    status = data.get("status", "approved")
    note = data.get("note", "")
    approved_by = data.get("approved_by", "User")

    res = feedback_svc.registrar_feedback_humano(
        projeto_id=projeto_id,
        scene_id=scene_id,
        status=status,
        note=note,
        approved_by=approved_by
    )

    if res.get("success"):
        version_svc.registrar_alteracao_cena_versao(
            projeto_id=projeto_id,
            scene_id=scene_id,
            descricao_alteracao=f"Human feedback: {status} ({note})" if note else f"Human feedback: {status}"
        )

    status_code = 200 if res.get("success") else 400
    return jsonify(res), status_code


@api_v2_bp.route("/versions/<projeto_id>", methods=["GET"])
def v2_project_versions(projeto_id: str):
    """Retorna o histórico de versões e snapshots do projeto."""
    import services.project_version_service as version_svc
    from config import PROJETOS_DIR

    pdir = PROJETOS_DIR / projeto_id
    if not pdir.exists():
        return jsonify({"success": False, "error": "Projeto não encontrado"}), 404

    hist = version_svc.obter_historico_versoes(projeto_id)
    return jsonify({"success": True, "versions": hist})




import zipfile
from services.capcut_export_service import CapCutExportService


# DEPRECADO — use /api/v2/montagem/<projeto_id>/exportar_capcut
@api_v2_bp.route("/projeto/<projeto_id>/exportar_capcut", methods=["POST"])
def exportar_capcut(projeto_id: str):
    """Dispara exportação CapCut para o projeto informado."""
    try:
        service = CapCutExportService(projeto_id)
        resultado = service.exportar()
        if resultado.get("success"):
            return jsonify(resultado), 200
        else:
            return jsonify(resultado), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/projeto/<projeto_id>/baixar_capcut", methods=["GET"])
def baixar_capcut(projeto_id: str):
    """Retorna ZIP da pasta export_capcut do projeto."""
    try:
        from config import PROJETOS_DIR
        pasta_export = Path(PROJETOS_DIR) / projeto_id / "export_capcut"
        if not pasta_export.exists():
            return jsonify({"success": False, "error": "Export não encontrado. Rode exportar_capcut primeiro."}), 404

        zip_path = pasta_export.parent / f"{projeto_id}_capcut.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for arquivo in pasta_export.rglob("*"):
                if arquivo.is_file():
                    zf.write(arquivo, arquivo.relative_to(pasta_export.parent))

        return send_file(str(zip_path), as_attachment=True, download_name=f"{projeto_id}_capcut.zip")
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500



@api_v2_bp.route("/abrir_pasta", methods=["POST"])
def abrir_pasta_generica():
    """Abre uma pasta no Explorer do Windows. Recebe { path: str }."""
    try:
        data = request.get_json(force=True) or {}
        path = data.get("path", "").strip()
        if not path:
            return jsonify({"success": False, "error": "path não informado"}), 400

        pasta = Path(path)
        if not pasta.exists():
            return jsonify({"success": False, "error": f"Pasta não encontrada: {path}"}), 404

        import subprocess
        subprocess.Popen(["explorer", str(pasta)])
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# CONTAS GOOGLE FLOW (v2) — troca manual + auto-reset diário
# ---------------------------------------------------------------------------

@api_v2_bp.route("/flow/contas", methods=["GET"])
def v2_flow_contas():
    """Retorna todas as contas do Flow + status.

    O auto-reset diário é aplicado nesta leitura: se virou o dia, os créditos
    de todas as contas são repostos antes de devolver a lista.
    """
    flow_mgr.resetar_creditos_diarios()
    return jsonify(flow_mgr.listar_contas_ativas())


@api_v2_bp.route("/flow/contas/<int:conta_id>/renomear", methods=["POST"])
def v2_flow_conta_renomear(conta_id: int):
    """Renomeia SOMENTE o nome de exibição (`nome`) de uma conta do Flow.

    Campo alterado: `nome` (o MESMO lido por `FlowAccountManager.listar_contas_ativas`
    e pelo card "CONTAS GOOGLE FLOW" em `static/app.js`). Nunca toca em id, email,
    profile_dir, ativa nem créditos. Lê/grava o `config/flow_accounts.json` do
    MESMO jeito da rota de selecionar conta (`/api/flow/contas/ativar`).

    Corpo: `{"nome": "..."}` — 1 a 40 caracteres, sem espaços nas pontas.
    400 (nome inválido) | 404 (conta inexistente) | 200 {"success", "conta": {id, nome}}.
    """
    data = request.get_json(silent=True) or {}
    nome = str(data.get("nome", "") or "").strip()
    if not nome:
        return jsonify({"success": False,
                        "error": "O nome não pode ficar vazio."}), 400
    if len(nome) > 40:
        return jsonify({"success": False,
                        "error": f"O nome deve ter no máximo 40 caracteres "
                                 f"(recebido: {len(nome)})."}), 400
    try:
        # Import tardio: `app_web` registra este blueprint, então importar no topo
        # criaria import circular (mesmo padrão usado nas rotas de contexto CDP).
        from app_web import _ler_flow_accounts, _salvar_flow_accounts
        accounts = _ler_flow_accounts()
        contas = accounts.get("contas", []) or []
        alvo = next((c for c in contas if int(c.get("id", 0)) == int(conta_id)), None)
        if alvo is None:
            return jsonify({"success": False,
                            "error": f"Conta '{conta_id}' não encontrada"}), 404
        alvo["nome"] = nome          # ÚNICO campo alterado
        _salvar_flow_accounts(accounts)
        return jsonify({"success": True, "conta": {"id": alvo.get("id"), "nome": alvo.get("nome")}})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/flow/trocar/<email>", methods=["POST"])
def v2_flow_trocar_conta(email: str):
    """Troca a conta ativa manualmente.

    Se a conta escolhida estiver com créditos esgotados, dispara a rotação
    inteligente e devolve a conta que ficou realmente ativa.
    """
    flow_mgr.resetar_creditos_diarios()  # auto-reset diário
    result = flow_mgr.trocar_conta(email)
    if result.get("status") != "ok":
        return jsonify({"success": False, **result}), 404
    nova = flow_mgr.rotacao_inteligente()  # verifica se pode auto-rotacionar
    if nova:
        result["conta_ativa"] = nova
        result["rotacionada_automaticamente"] = True
    result["success"] = True
    return jsonify(result)


# ---------------------------------------------------------------------------
# PARALELIZAÇÃO MULTI-CONTA (ORCHESTRADOR PARALELO)
# ---------------------------------------------------------------------------

@api_v2_bp.route("/flow/prelogin_lote", methods=["POST"])
def v2_flow_prelogin_lote():
    """Pré-login paralelo: inicia subprocessos Chrome com perfis dedicados e verifica CDP."""
    data = request.get_json(force=True, silent=True) or {}
    num_contas = int(data.get("num_contas", MAX_CONTAS_SIMULTANEAS))
    projeto_id = data.get("projeto_id", "")
    if num_contas > MAX_CONTAS_SIMULTANEAS:
        log_event("PRODUCAO", f"Pré-login paralelo (projeto={projeto_id}): "
                              f"{num_contas} contas pedidas; limitando a "
                              f"{MAX_CONTAS_SIMULTANEAS} (MAX_CONTAS_SIMULTANEAS).",
                  level="warn")
        num_contas = MAX_CONTAS_SIMULTANEAS

    accounts_path = Path("config/flow_accounts.json")
    contas = []
    if accounts_path.exists():
        try:
            acc_data = json.loads(accounts_path.read_text(encoding="utf-8"))
            contas = acc_data.get("contas", [])
        except Exception as e:
            return jsonify({"success": False, "error": f"Erro ao ler contas: {e}"}), 500

    if not contas:
        return jsonify({"success": False, "error": "Nenhuma conta cadastrada em config/flow_accounts.json"}), 400

    num_contas = max(1, min(num_contas, len(contas), MAX_CONTAS_SIMULTANEAS))
    PORTA_BASE = 9222
    workers_disponiveis = []
    bloqueios = []

    from services.playwright_flow import ensure_chrome_cdp, _cdp_port_open

    # 1. Inicia os Chrome subprocesses para cada conta (Worker 0 = projeto + flow; Workers 1+ = flow só)
    for i in range(num_contas):
        conta = contas[i]
        port = PORTA_BASE + i
        profile_rel = conta.get("profile_dir", f"lira_profiles/conta_{conta.get('id', i+1):02d}")
        profile_dir = str(Path.home() / profile_rel)
        ensure_chrome_cdp(
            port=port,
            force_restart=False,
            profile_dir=profile_dir,
            projeto_id=projeto_id,
            worker_id=i
        )

    # 2. Aguarda estabilização das portas
    time.sleep(3.0)

    # 3. Testa socket e status de login
    import urllib.request
    for i in range(num_contas):
        conta = contas[i]
        port = PORTA_BASE + i
        cid = conta.get("id", i + 1)
        email = conta.get("email", f"conta_{cid}")

        if not _cdp_port_open(port):
            bloqueios.append({"worker_id": i, "conta_id": cid, "email": email, "port": port, "erro": "Porta CDP fechada"})
            continue

        status = "pronto"
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/json", headers={"User-Agent": "Mozilla/5.0"})
            tabs = json.loads(urllib.request.urlopen(req, timeout=3).read())
            urls = [t.get("url", "") for t in tabs if t.get("type") == "page"]
            if any("accounts.google.com" in u for u in urls):
                status = "login_necessario"
            elif any("labs.google" in u or "flow" in u for u in urls):
                status = "logada"
            else:
                status = "pronto"
        except Exception:
            status = "verificar_manualmente"

        workers_disponiveis.append({
            "worker_id": i,
            "conta_id": cid,
            "email": email,
            "port": port,
            "status": status
        })

    prontas_count = len([w for w in workers_disponiveis if w["status"] in ("pronto", "logada")])

    return jsonify({
        "success": True,
        "total_solicitadas": num_contas,
        "prontas": prontas_count,
        "workers_disponiveis": workers_disponiveis,
        "bloqueios": bloqueios
    })


@api_v2_bp.route("/producao/<projeto_id>/iniciar_paralelo", methods=["POST"])
def v2_producao_iniciar_paralelo(projeto_id: str):
    """Inicia a produção de vídeo particionada em N workers paralelos."""
    data = request.get_json(force=True, silent=True) or {}
    num_contas = int(data.get("num_contas", MAX_CONTAS_SIMULTANEAS))
    modo = data.get("modo", "imagem")
    if num_contas > MAX_CONTAS_SIMULTANEAS:
        log_event("PRODUCAO", f"Produção paralela (projeto={projeto_id}): "
                              f"{num_contas} contas pedidas; limitando a "
                              f"{MAX_CONTAS_SIMULTANEAS} (MAX_CONTAS_SIMULTANEAS).",
                  level="warn")
        num_contas = MAX_CONTAS_SIMULTANEAS

    from services.parallel_flow_orchestrator import OrchestradorParalelo, registrar_orquestrador, obter_orquestrador
    orq_existente = obter_orquestrador(projeto_id)
    if orq_existente and orq_existente.is_running:
        return jsonify({"success": False, "error": "Orquestrador paralelo já está em execução para este projeto."}), 409

    orquestrador = OrchestradorParalelo(projeto_id, num_contas=num_contas)
    res = orquestrador.iniciar_fila_paralela(projeto_id, num_contas=num_contas, modo=modo)
    if res.get("success"):
        registrar_orquestrador(projeto_id, orquestrador)
        return jsonify(res), 200
    else:
        return jsonify(res), 400


@api_v2_bp.route("/producao/<projeto_id>/relancar_residual", methods=["POST"])
@api_v2_bp.route("/producao/<projeto_id>/relançar_residual", methods=["POST"])
def v2_producao_relancar_residual(projeto_id: str):
    """
    Redivide dinamicamente as cenas restantes/não-feitas entre N workers e relança a fila paralela.
    """
    data = request.get_json(force=True, silent=True) or {}
    num_workers_novo = int(data.get("num_workers_novo", 8))
    modo = data.get("modo", "imagem")

    from services.parallel_flow_orchestrator import OrchestradorParalelo, registrar_orquestrador, obter_orquestrador
    orquestrador = obter_orquestrador(projeto_id)
    if not orquestrador:
        orquestrador = OrchestradorParalelo(projeto_id, num_contas=num_workers_novo)
        registrar_orquestrador(projeto_id, orquestrador)

    res = orquestrador.relancar_fila_residual(projeto_id, num_workers_novo=num_workers_novo, modo=modo)
    if res.get("success"):
        return jsonify(res), 200
    elif res.get("error") == "Projeto já completo":
        return jsonify(res), 200
    else:
        return jsonify(res), 400


@api_v2_bp.route("/producao/<projeto_id>/telemetria", methods=["GET"])
def v2_producao_telemetria(projeto_id: str):
    """Retorna telemetria consolidada dos workers paralelos ou status legado."""
    from services.parallel_flow_orchestrator import obter_orquestrador
    orquestrador = obter_orquestrador(projeto_id)
    if orquestrador and (orquestrador.is_running or orquestrador.workers):
        return jsonify({"success": True, "modo": "paralelo", **orquestrador.obter_telemetria_consolidada()})

    from services.playwright_flow import FlowQueueWorker
    status = FlowQueueWorker.get_status()
    return jsonify({
        "success": True,
        "modo": "sequencial",
        "is_running": status.get("rodando_fila", False),
        "cena_ativa": status.get("cena_ativa"),
        "flow": status
    })


@api_v2_bp.route("/producao/<projeto_id>/pausar", methods=["PATCH", "POST"])
def v2_producao_pausar_paralelo(projeto_id: str):
    """Pausa a produção (paralela ou sequencial)."""
    from services.parallel_flow_orchestrator import obter_orquestrador
    orq = obter_orquestrador(projeto_id)
    if orq and (orq.is_running or orq.workers):
        orq.pausar_tudo()
        return jsonify({"success": True, "mensagem": "Workers paralelos pausados."})

    from services.playwright_flow import FlowQueueWorker
    FlowQueueWorker.stop_worker()
    return jsonify({"success": True, "mensagem": "Fila sequencial pausada."})


@api_v2_bp.route("/producao/<projeto_id>/cancelar", methods=["PATCH", "POST"])
def v2_producao_cancelar_paralelo(projeto_id: str):
    """Cancela a produção (paralela ou sequencial)."""
    from services.parallel_flow_orchestrator import obter_orquestrador
    orq = obter_orquestrador(projeto_id)
    if orq and (orq.is_running or orq.workers):
        orq.cancelar_tudo()
        return jsonify({"success": True, "mensagem": "Workers paralelos cancelados."})

    from services.playwright_flow import FlowQueueWorker
    FlowQueueWorker.stop_worker()
    return jsonify({"success": True, "mensagem": "Fila sequencial cancelada."})


@api_v2_bp.route("/contexto_ativo", methods=["GET"])
def v2_obter_contexto_ativo():
    """Retorna o contexto ativo do projeto para restauração após reinício do CDP."""
    ctx_file = CONTEXTO_CDP_PATH
    if ctx_file.exists():
        try:
            data = json.loads(ctx_file.read_text(encoding="utf-8"))
            return jsonify({"success": True, "contexto": data})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
    return jsonify({"success": False, "contexto": None})


@api_v2_bp.route("/restaurar_contexto", methods=["POST"])
def v2_restaurar_contexto():
    """Salva ou atualiza o contexto ativo do projeto."""
    data = request.get_json(force=True, silent=True) or {}
    projeto_id = data.get("projeto_id")
    if not projeto_id:
        return jsonify({"success": False, "error": "projeto_id obrigatório"}), 400
    try:
        ctx_file = CONTEXTO_CDP_PATH
        ctx_file.parent.mkdir(parents=True, exist_ok=True)
        ctx_data = {
            "projeto_id": projeto_id,
            "timestamp": time.time(),
            "modo": data.get("modo", "imagem")
        }
        ctx_file.write_text(json.dumps(ctx_data, ensure_ascii=False, indent=2), encoding="utf-8")
        return jsonify({"success": True, "contexto": ctx_data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


