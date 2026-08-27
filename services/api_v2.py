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
import subprocess
from datetime import datetime
from pathlib import Path
from flask import Blueprint, request, jsonify, send_file, current_app

from config import PROJETOS_DIR, OUTPUT_DIR
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
        for campo in ("modo_producao", "nome_personagem", "estilo_visual", "continuidade_visual", "referencia_visual_global"):
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
    srt_file.write_text(texto_srt, encoding="utf-8")

    # Extrai segmentos com timestamps [MM:SS]
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

    if not segmentos:
        # Fallback para parsing padrão de blocos SRT
        linhas = [l.strip() for l in texto_srt.splitlines() if l.strip()]
        tempo_atual = 0.0
        for linha in linhas:
            if re.match(r"^\d+$", linha) or "-->" in linha:
                continue
            segmentos.append({
                "start": round(tempo_atual, 2),
                "end": round(tempo_atual + 4.0, 2),
                "text": linha,
                "timestamp": f"{int(tempo_atual//60):02d}:{int(tempo_atual%60):02d}",
            })
            tempo_atual += 4.0

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

    # Gera scene plan automaticamente
    scene_plan_svc.gerar_scene_plan(projeto_id, force=True)
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



@api_v2_bp.route("/prompts/<projeto_id>/gerar", methods=["POST"])
def v2_gerar_prompts(projeto_id: str):
    """
    Gera prompts inteligentes com continuidade visual (Fase 5) e @NomePersonagem.
    A Cena 01 estabelece a âncora visual e as seguintes herdam coerência.
    """
    data = request.get_json(force=True, silent=True) or {}
    meta = _get_meta(projeto_id)

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


# ---------------------------------------------------------------------------
# PROMPT INTELLIGENCE — GERAÇÃO COM IA (DEEPSEEK)
# ---------------------------------------------------------------------------

@api_v2_bp.route("/projeto/<projeto_id>/gerar_prompts_ia", methods=["POST"])
def v2_gerar_prompts_ia(projeto_id: str):
    """
    Executa o DeepSeek Prompt Intelligence completo:
    Análise Global (Context Pack) + Batch Prompt Director + Automatic Critic.
    Atualiza atomicamente o lira_scene_plan.json e exporta para prompts/storyboard_prompts.txt.
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        estilo_id = data.get("estilo_id") or data.get("estilo_visual") or "photorealistic_cinematic"
        instrucao_custom = (data.get("instrucao_custom") or data.get("custom_prompt") or "").strip()

        # Garante que o plano de cenas exista a partir do áudio/SRT
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            scene_plan_svc.gerar_scene_plan(projeto_id, force=True)
            plan = scene_plan_svc.carregar_scene_plan(projeto_id)

        if not plan or not plan.get("cenas"):
            return jsonify({"success": False, "error": "Cenas não encontradas. Adicione áudio ou SRT primeiro."}), 400

        # Executa o pipeline de inteligência
        resultado = deepseek_svc.executar_pipeline_prompt_intelligence(
            projeto_id=projeto_id,
            estilo_id=estilo_id,
            instrucao_custom=instrucao_custom,
        )

        plan_atualizado = scene_plan_svc.carregar_scene_plan(projeto_id)
        return jsonify({
            "success": True,
            "resultado": resultado,
            "plan": plan_atualizado,
        })
    except Exception as e:
        log_event("PROMPT_IA_ERRO", f"Erro no DeepSeek Prompt Intelligence para '{projeto_id}': {e}", level="error")
        return jsonify({"success": False, "error": str(e)}), 500


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
    # Sincroniza com as mídias reais presentes no disco para garantir dados exatos
    scene_plan_svc.sincronizar_midias_encontradas(projeto_id)
    progresso = scene_plan_svc.progresso_scene_plan(projeto_id)
    plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    cenas = plan.get("cenas", []) if plan else []

    # Verifica status do Flow via CDP e SSE
    cdp_conectado = False
    try:
        from services.playwright_flow import FlowQueueWorker
        cdp_conectado = FlowQueueWorker.is_connected()
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
        if tem_arquivo or st == scene_plan_svc.STATUS_BAIXADA:
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
        import importlib
        import services.playwright_flow as pw_module
        importlib.reload(pw_module)
        FlowQueueWorker = pw_module.FlowQueueWorker

        # Sincroniza estado com o disco para garantir retomada exata
        scene_plan_svc.sincronizar_midias_encontradas(projeto_id)
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            return jsonify({"success": False, "error": "Nenhuma cena disponível"}), 400

        data = request.get_json(silent=True) or {}
        custom_scene_ids = data.get("scene_ids")
        modo = data.get("modo", "imagem")

        pdir = scene_plan_svc._project_dir(projeto_id)
        if custom_scene_ids:
            cenas_pendentes = [int(sid) for sid in custom_scene_ids]
        else:
            # Seleciona apenas as cenas que REALMENTE não possuem arquivo no disco
            cenas_pendentes = []
            for c in plan["cenas"]:
                cid = int(c["id"])
                arq_disco = scene_plan_svc.resolver_arquivo_cena(projeto_id, cid, float(c.get("tempo_inicio", 0)))
                tem_arquivo = False
                if modo == "animacao":
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
            success, msg = pw_module.ensure_chrome_cdp(9222)
            if not success:
                return jsonify({"success": False, "error": f"Chrome CDP falhou: {msg}"}), 500
        ok = True if is_testing else FlowQueueWorker.start_worker(projeto_id, scene_ids=cenas_pendentes or None, modo=modo)
        if not ok:
            worker = FlowQueueWorker.get_worker()
            if worker.is_running_queue:
                return jsonify({"success": True, "already_running": True, "enfileiradas": len(cenas_pendentes)})
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

        return jsonify({
            "success": True,
            "worker": {
                "is_running": worker.is_running_queue,
                "model": worker.current_model,
                "cena_ativa": cena_ativa,
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
# 4. MONTAGEM v2
# ===========================================================================

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
        })

    pode_montar = (com_midia == total and total > 0)
    meta = _get_meta(projeto_id)
    tem_audio = bool(meta.get("arquivo_audio") and Path(meta["arquivo_audio"]).exists())

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
        cenas = plan["cenas"]
        lista_cenas_capcut = []
        for c in cenas:
            cid = int(c["id"])
            arq_obj = scene_plan_svc.resolver_arquivo_cena(projeto_id, cid, float(c.get("tempo_inicio", 0)))
            arq_resolvido = str(arq_obj) if arq_obj else None

            lista_cenas_capcut.append({
                "start": float(c.get("tempo_inicio", 0)),
                "arquivo": arq_resolvido,
                "media_type": "video" if c.get("tipo") == "video" else "photo",
                "duracao": float(c.get("duracao", 5.0)),
            })

        meta = _get_meta(projeto_id)
        audio = meta.get("arquivo_audio") or ""
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


@api_v2_bp.route("/projeto/<projeto_id>/audio")
def v2_projeto_audio(projeto_id: str):
    """Serve o áudio original do projeto para o Player de Montagem interativo."""
    meta = _get_meta(projeto_id)
    arq_audio = meta.get("arquivo_audio")
    pdir = _project_dir(projeto_id)
    if not arq_audio or not Path(arq_audio).exists():
        cands = list(pdir.glob("audio.*")) + list(pdir.glob("*.mp3")) + list(pdir.glob("*.wav")) + list(pdir.glob("*.m4a"))
        if cands:
            arq_audio = str(cands[0])
    if not arq_audio or not Path(arq_audio).exists():
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


@api_v2_bp.route("/personagem/<projeto_id>/criar_flow", methods=["POST"])
def v2_personagem_criar_flow(projeto_id: str):
    """
    Cria o personagem no Google Flow via CDP com modelo Nano Banana 2 e upload real de foto:
    1. Salva a foto de referência enviada ou pré-existente
    2. Dispara a criação nativa no Flow via Playwright
    3. Retorna o ID, tag @Nome e metadados oficiais
    """
    try:
        from services.playwright_flow import criar_personagem_no_flow_direto

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

        # 1. Salva a identidade inicial no projeto para persistir a imagem local
        salvo = character_svc.salvar_identidade_projeto(
            projeto_id=projeto_id,
            tipo="personagem",
            nome=nome,
            referencia_flow=ref_flow,
            imagem_bytes=img_bytes,
            visual_style=estilo
        )

        idt = character_svc.obter_identidade_projeto(projeto_id)
        img_abs = (idt.get("imagem_abs") if idt else None) or ""

        # 2. Executa a criação no Google Flow
        res_flow = criar_personagem_no_flow_direto(projeto_id=projeto_id, nome=nome, imagem_abs=img_abs)
        if not res_flow.get("success"):
            return jsonify(res_flow), 500

        return jsonify(res_flow), 200
    except Exception as e:
        return jsonify({"success": False, "error": f"Falha ao integrar com Google Flow: {str(e)}"}), 500


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




