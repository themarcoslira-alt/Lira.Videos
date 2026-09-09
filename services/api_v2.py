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
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from pathlib import Path
from flask import Blueprint, request, jsonify, send_file, current_app

from config import PROJETOS_DIR, OUTPUT_DIR, FFMPEG_PATH
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

api_v2_bp = Blueprint("api_v2", __name__)


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
        for campo in ("modo_producao", "nome_personagem", "estilo_visual", "continuidade_visual", "referencia_visual_global", "prod_modelo", "prod_modelo_imagem", "prod_modelo_video", "prod_qualidade", "prod_qualidade_imagem", "prod_qualidade_video", "prod_qualidade_download", "prod_tipo_saida", "prod_proporcao", "provedor_storyboard", "provedor_prompts"):
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

    # Gera cenas.json
    cenas = []
    for idx, seg in enumerate(segmentos, 1):
        cenas.append({
            "id": idx,
            "start_time": seg["start"],
            "end_time": seg["end"],
            "texto": seg["text"],
            "timestamps": [seg.get("timestamp", f"{int(seg['start']//60):02d}:{int(seg['start']%60):02d}")],
        })
    (pdir / "cenas.json").write_text(json.dumps(cenas, indent=2, ensure_ascii=False), encoding="utf-8")

    meta = _get_meta(projeto_id)
    meta["transcricao_completa"] = True
    _save_meta(projeto_id, meta)

    log_event("ROTEIRO", f"{projeto_id}: {len(segmentos)} falas importadas do SRT")

    # Gera scene plan automaticamente
    plan = scene_plan_svc.gerar_scene_plan(projeto_id, force=True)
    return jsonify({"success": True, "total_cenas": len(cenas), "plan": plan})


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
    """Testa a conectividade de um provedor específico (claude, deepseek, pexels, pixabay, unsplash)."""
    import time
    import requests
    data = request.get_json(force=True, silent=True) or {}
    provedor = (data.get("provedor") or "").strip().lower()
    
    if not provedor:
        return jsonify({"success": False, "error": "Provedor não especificado"}), 400

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
        elif provedor == "pexels":
            from config import PEXELS_API_KEY
            key = PEXELS_API_KEY
        elif provedor == "pixabay":
            from config import PIXABAY_API_KEY
            key = PIXABAY_API_KEY
        elif provedor == "unsplash":
            from config import UNSPLASH_API_KEY
            key = UNSPLASH_API_KEY

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

        elif provedor == "pexels":
            url = "https://api.pexels.com/v1/search?query=nature&per_page=1"
            headers = {"Authorization": key}
            r = requests.get(url, headers=headers, timeout=10)
            elapsed = time.time() - t0
            if r.status_code == 200:
                return jsonify({"success": True, "valida": True, "status": "Conectada", "mensagem": "API respondendo corretamente.", "latency": elapsed})
            else:
                return jsonify({"success": True, "valida": False, "status": "Inválida", "mensagem": f"Erro HTTP {r.status_code}: {r.text[:200]}", "latency": elapsed})

        elif provedor == "pixabay":
            url = f"https://pixabay.com/api/?key={key}&q=nature&per_page=3"
            r = requests.get(url, timeout=10)
            elapsed = time.time() - t0
            if r.status_code == 200:
                return jsonify({"success": True, "valida": True, "status": "Conectada", "mensagem": "API respondendo corretamente.", "latency": elapsed})
            else:
                return jsonify({"success": True, "valida": False, "status": "Inválida", "mensagem": f"Erro HTTP {r.status_code}: {r.text[:200]}", "latency": elapsed})

        elif provedor == "unsplash":
            url = f"https://api.unsplash.com/photos/?client_id={key}&per_page=1"
            r = requests.get(url, timeout=10)
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

    for c in plan["cenas"]:
        if int(c.get("id", 0)) == cid or int(c.get("scene_index", 0)) == cid:
            return jsonify({"success": True, "cena": c})

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
    for c in plan["cenas"]:
        if int(c.get("id", 0)) == cid or int(c.get("scene_index", 0)) == cid:
            c["prompt_imagem"] = novo_prompt
            c["visual_prompt"] = novo_prompt
            if novo_anim:
                c["prompt_animacao"] = novo_anim
            c["status"] = scene_plan_svc.STATUS_PROMPT_PRONTO
            c["manual_intervention"] = True
            c["atualizado_em"] = datetime.now().isoformat(sep=" ", timespec="seconds")
            cena_encontrada = c
            break

    if not cena_encontrada:
        return jsonify({"success": False, "error": f"Cena {cid} não encontrada"}), 404

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
    try:
        from services.playwright_flow import FlowQueueWorker, _cdp_port_open
        status = FlowQueueWorker.get_status()
        cdp_conectado = bool(status.get("conectado", False)) or _cdp_port_open(9222)
    except Exception:
        pass

    flow_status = {"conectado": cdp_conectado, "modo": "CDP"}
    try:
        from app_web import _FLOW_STATE, _FLOW_QUEUES
        est = _FLOW_STATE.get(projeto_id, {})
        esta_conectado = cdp_conectado or bool(est.get("conectado", False)) or len(_FLOW_QUEUES) > 0
        flow_status = {
            "conectado": esta_conectado,
            "conta": est.get("conta", "Google Flow CDP (Porta 9222)"),
            "modo": "CDP",
            "fila_parada": bool(est.get("fila_parada", False))
        }
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
    cenas_prontas = []
    cenas_com_erro = []
    cenas_pendentes = []
    pdir = scene_plan_svc._project_dir(projeto_id)

    for c in cenas:
        cid = int(c.get("id", 0))
        st = c.get("status", "")
        arq_disco = scene_plan_svc.resolver_arquivo_cena(projeto_id, cid, float(c.get("tempo_inicio", 0)))
        tem_arquivo = bool(arq_disco and arq_disco.exists() and arq_disco.stat().st_size > 500)
        if tem_arquivo and st == scene_plan_svc.STATUS_BAIXADA:
            cenas_prontas.append(cid)
        elif st == scene_plan_svc.STATUS_ERRO:
            cenas_com_erro.append(cid)
            cenas_pendentes.append(cid)
        else:
            cenas_pendentes.append(cid)

    proxima_cid = cenas_pendentes[0] if cenas_pendentes else None
    resume_info = {
        "total": len(cenas),
        "prontas_count": len(cenas_prontas),
        "pendentes_count": len(cenas_pendentes),
        "erros_count": len(cenas_com_erro),
        "proxima_cena_id": proxima_cid,
        "cenas_erro_ids": cenas_com_erro,
        "cenas_pendentes_ids": cenas_pendentes,
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
def v2_producao_iniciar_fila(projeto_id: str):
    try:
        from services.playwright_flow import FlowQueueWorker, ensure_chrome_cdp

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
                # animacao / animacao_apenas: filtra estritamente cenas de vídeo/animação pendente
                if modo in ("animacao", "animacao_apenas"):
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

        # Reseta qualquer cena que estivesse marcada com status transitório prévio
        for c in plan["cenas"]:
            if int(c["id"]) in cenas_pendentes:
                scene_plan_svc.atualizar_status_cena(projeto_id, int(c["id"]), scene_plan_svc.STATUS_PENDENTE)

        import sys
        is_testing = getattr(current_app, "testing", False) or current_app.config.get("TESTING", False) or ("unittest" in sys.modules)
        if not is_testing:
            success, msg = ensure_chrome_cdp(9222)
            if not success:
                return jsonify({"success": False, "error": f"Chrome CDP falhou: {msg}"}), 500
        ok = True if is_testing else FlowQueueWorker.start_worker(projeto_id, scene_ids=cenas_pendentes or None, modo=modo_worker)
        if not ok:
            worker = FlowQueueWorker.get_worker()
            if worker.is_running_queue:
                return jsonify({"success": False, "already_running": True, "message": "Fila já em execução. Aguarde ou reinicie o servidor.", "enfileiradas": len(cenas_pendentes)})
            return jsonify({"success": False, "error": "Falha ao iniciar (já em execução ou erro de conexão)."}), 409
        return jsonify({
            "success": True,
            "enfileiradas": len(cenas_pendentes),
            "proxima_cena_id": cenas_pendentes[0] if cenas_pendentes else None
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


@api_v2_bp.route("/producao/<projeto_id>/retomar", methods=["POST"])
def v2_producao_retomar_fila(projeto_id: str):
    """Retoma a produção a partir exatamente da primeira cena pendente ou com erro."""
    return v2_producao_iniciar_fila(projeto_id)


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

        total = len(cenas)
        prog = scene_plan_svc.progresso_scene_plan(projeto_id)
        baixadas = prog.get("prontas", 0)
        pendentes = max(0, total - baixadas)
        gerando = sum(1 for c in cenas if c.get("status") in (scene_plan_svc.STATUS_GERANDO, scene_plan_svc.STATUS_ENVIANDO))
        erro = prog.get("por_status", {}).get(scene_plan_svc.STATUS_ERRO, 0)

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

        logs_raw = ler_eventos(linhas=100)
        logs_fmt = []
        for ev in logs_raw:
            cat = ev.get("category", "")
            if cat in ("PLAYWRIGHT_FLOW", "SCENE_PLAN", "FLOW", "VISUAL_DIRECTOR", "WEB", "SYSTEM"):
                logs_fmt.append({
                    "ts": ev.get("ts", "")[-8:] if ev.get("ts") else "",
                    "level": ev.get("level", "INFO"),
                    "category": cat,
                    "message": ev.get("message", "")
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
                "pct": round((baixadas / total * 100) if total > 0 else 0, 1)
            },
            "logs": logs_fmt
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


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

        cena = next((c for c in plan.get("cenas", [])
                     if int(c.get("id", -1)) == int(cena_id)
                     or int(c.get("scene_index", -1)) == int(cena_id)), None)
        if not cena:
            return jsonify({"ok": False, "error": f"Cena {cena_id} não encontrada"}), 404

        arquivo_midia = cena.get("arquivo_midia") or ""
        removidos_disco = []

        def _tentar_remover(p):
            try:
                if p.exists() and p.is_file():
                    p.unlink()
                    removidos_disco.append(str(p))
                    return True
            except Exception as e:
                log_event("MIDIA_REMOVER", f"{projeto_id}: erro ao apagar {p}: {e}", level="warn")
            return False

        # 1. Apaga o arquivo físico registrado na cena
        if arquivo_midia:
            _tentar_remover(Path(str(arquivo_midia)))

        # 1.1. Apaga o arquivo canônico REAL resolvido (cobre nomes canônicos em cenas/
        #      ex: 01_[00-00-03].png e variantes em conteudo/videos/imagens)
        try:
            ts_ini_c = float(cena.get("tempo_inicio") or 0)
            arq_resolvido = scene_plan_svc.resolver_arquivo_cena(projeto_id, int(cena_id), ts_ini_c)
            if arq_resolvido and str(arq_resolvido) != str(arquivo_midia):
                _tentar_remover(arq_resolvido)
        except Exception as e:
            log_event("MIDIA_REMOVER", f"{projeto_id}: aviso resolver_arquivo_cena: {e}", level="warn")

        # 2. Apaga também cópias canônicas/consolidadas/legadas que casem com a cena.
        #    Cobre padrões de nome com prefixo numérico da cena: NN_[...].png,
        #    001.png, NN_MM-SS_MM-SS.png etc. — sem apagar arquivos de outras cenas.
        pdir = _project_dir(projeto_id)
        ext = ".mp4" if str(arquivo_midia).lower().endswith((".mp4", ".mov", ".webm")) else ".png"
        import re as _re_midia
        prefixos = (f"{int(cena_id)}_", f"{int(cena_id):02d}_", f"{int(cena_id):03d}_",
                    f"{int(cena_id):02d}.", f"{int(cena_id):03d}.", f"{int(cena_id)}.")
        for pasta in ("cenas", "conteudo", "videos", "imagens"):
            pd = pdir / pasta
            if not pd.exists():
                continue
            for f in pd.iterdir():
                if not f.is_file():
                    continue
                nome = f.name
                if not any(nome.startswith(pre) for pre in prefixos):
                    continue
                if f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".webm"):
                    continue
                # Confirma que o número inicial do nome é EXATAMENTE a cena (evita 1_* apagar 10_*)
                m_num = _re_midia.match(r"^(\d+)", nome)
                if m_num and int(m_num.group(1)) == int(cena_id):
                    _tentar_remover(f)

        # 3. Limpa os campos de mídia da cena e volta para PENDENTE
        scene_plan_svc.atualizar_cena(projeto_id, cena_id, {
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


@api_v2_bp.route("/montagem/<projeto_id>/transicoes_lote", methods=["POST"])
def v2_montagem_transicoes_lote(projeto_id: str):
    """Aplica uma transição padrão em todas as cenas do projeto."""
    data = request.get_json(silent=True) or {}
    tipo = str(data.get("tipo") or "fade_out")
    duracao_ms = int(data.get("duracao_ms") or 300)
    lado = str(data.get("lado") or "saida")
    ok, msg, plan = scene_plan_svc.aplicar_transicoes_em_lote(
        projeto_id, tipo=tipo, duracao_ms=duracao_ms, lado=lado
    )
    if not ok:
        return jsonify({"success": False, "error": msg}), 400
    return jsonify({"success": True, "msg": msg, "plan": plan})


# ---------------------------------------------------------------------------
# REDESIGN F1 — Automação de Movimento (motion_preset por cena)
# ---------------------------------------------------------------------------
_MOTION_PRESETS_VALIDOS = ("zoom_in", "zoom_out", "pan_right", "pan_left", "estatico")
_MOTION_CICLO = ("zoom_in", "pan_right", "zoom_out", "pan_left")


def _calcular_motion_preset_cena(cena: dict, index: int = 0) -> str:
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
            resumo[preset] = resumo.get(preset, 0) + 1

        gravadas = 0
        if acao == "aprovar":
            for cid, preset in presets.items():
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
    cenas = plan["cenas"]
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

        cenas_detalhe.append({
            "id": cid,
            "tempo_inicio": c.get("tempo_inicio", 0),
            "tempo_fim": c.get("tempo_fim", 0),
            "duracao": c.get("duracao", 0),
            "tipo": c.get("tipo", "image"),
            "status": c.get("status", scene_plan_svc.STATUS_PENDENTE),
            "arquivo_midia": arq_resolvido or "",
            # CORREÇÃO 1 — extrai os campos REAIS do scene_plan (prompt_imagem/visual_prompt e narração/texto)
            "prompt": c.get("prompt_en", c.get("prompt_imagem", c.get("visual_prompt", c.get("prompt", "")))),
            "fala": c.get("narration", c.get("texto", "")),
            "texto": c.get("texto", c.get("narration", "")),
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
        cenas = sorted(
            plan["cenas"], key=lambda c: float(c.get("tempo_inicio", 0) or 0)
        )
        duracao_audio_total = _duracao_audio_segundos(audio)  # 0.0 se falhar
        lista_cenas_capcut = []
        for idx, c in enumerate(cenas):
            cid = int(c["id"])
            t_ini = float(c.get("tempo_inicio", 0) or 0)
            arq_obj = scene_plan_svc.resolver_arquivo_cena(projeto_id, cid, t_ini)
            arq_resolvido = str(arq_obj) if arq_obj else None

            duracao_fala = float(c.get("duracao") or 0) or 5.0
            if idx + 1 < len(cenas):
                # clipe cobre o intervalo [t_ini, início da próxima fala) — sem lacuna
                t_prox = float(cenas[idx + 1].get("tempo_inicio", 0) or t_ini)
                duracao_export = max(duracao_fala, t_prox - t_ini)
            elif duracao_audio_total and duracao_audio_total > t_ini:
                # última cena: até o FIM REAL do áudio (não a fala isolada)
                duracao_export = max(duracao_fala, duracao_audio_total - t_ini)
            else:
                duracao_export = duracao_fala  # fallback: áudio não mensurável

            lista_cenas_capcut.append({
                "start": t_ini,
                "arquivo": arq_resolvido,
                "media_type": "video" if c.get("tipo") == "video" else "photo",
                "duracao": float(round(duracao_export, 6)),
                # REDESIGN F1 / CORREÇÃO CRÍTICA: campos que eram descartados —
                # Ken Burns (keyframe CapCut) e motion_preset + transição real da cena.
                "ken_burns_ativo": c.get("ken_burns_ativo", False),
                "motion_preset": c.get("motion_preset", ""),
                "transicao_saida": c.get("transicao_saida") or scene_plan_svc.TRANSICION_SAIDA_DEFAULT,
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

        log_event("MONTAGEM_V2", f"Exportação CapCut concluída para '{projeto_id}'", level="info")
        return jsonify({"success": True, "resultado": resultado, "capcut_dir": str(capcut_dir)})
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

        # Monta filtro xfade se houver transições não-none entre cenas
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
        from services.playwright_flow import criar_avatar_flow_via_playwright

        idt = character_svc.obter_identidade_projeto(projeto_id)
        img_abs = (idt.get("imagem_abs") if idt else None) or ""

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
        ref_flow = f"@{nome}"

        # 1. Salva a identidade inicial no projeto (rápido, síncrono)
        character_svc.salvar_identidade_projeto(
            projeto_id=projeto_id,
            tipo="personagem",
            nome=nome,
            referencia_flow=ref_flow,
            imagem_bytes=img_bytes,
            visual_style=estilo
        )

        # 2. Verifica se já há um job em andamento
        job = _AVATAR_FLOW_JOBS.get(projeto_id)
        if job and job.get("status") == "executando":
            return jsonify({
                "success": True,
                "status": "executando",
                "mensagem": "Criação do avatar já está em andamento.",
                "job": job,
            })

        # 3. Inicia automação em background (NUNCA bloqueia o request handler)
        t = threading.Thread(
            target=_executar_criar_avatar_background,
            args=(projeto_id, nome, estilo),
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
            character_svc.atualizar_status_flow_referencia(
                projeto_id=projeto_id,
                alias=alias,
                created=True,
                flow_char_id=flow_id,
                flow_char_name=ref.get("alias", f"@{nome_clean}")
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
        nome = "PersonagemPrincipal"

    arquivo = request.files.get("imagem") or request.files.get("personagem")
    if not arquivo or not arquivo.filename:
        return jsonify({"success": False, "error": "Imagem de referência obrigatória"}), 400

    img_bytes = arquivo.read()
    if not img_bytes:
        return jsonify({"success": False, "error": "Arquivo de imagem vazio"}), 400

    estilo = request.form.get("estilo_visual") or "photorealistic_cinematic"
    ref_flow = request.form.get("referencia_flow") or f"@{nome}"

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
        return jsonify({"success": False, "error": "Mídia não encontrada"}), 404
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

