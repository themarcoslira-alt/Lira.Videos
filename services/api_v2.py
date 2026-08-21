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

api_v2_bp = Blueprint("api_v2", __name__)

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
    if meta_file.exists():
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "modo_producao": "imagem_video",
        "nome_personagem": "",
        "referencia_visual_global": None,
        "estilo_visual": "photorealistic_cinematic",
        "continuidade_visual": True,
        "studio_version": "v2",
    }

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
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        for campo in ("modo_producao", "nome_personagem", "estilo_visual", "continuidade_visual", "referencia_visual_global"):
            if campo in data:
                meta[campo] = data[campo]
        _save_meta(projeto_id, meta)
        return jsonify({"success": True, "meta": meta})

    return jsonify({"success": True, "meta": meta, "studio_version": meta.get("studio_version", "v2")})


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
    plan = scene_plan_svc.carregar_scene_plan(projeto_id)

    return jsonify({"success": True, "total_cenas": len(cenas), "plan": plan})


@api_v2_bp.route("/storyboard/<projeto_id>/gerar", methods=["POST"])
def v2_gerar_storyboard(projeto_id: str):
    """
    Gera / atualiza o plano de cenas do Studio 2.0 (lira_scene_plan.json).
    Propaga modo_producao e nome_personagem configurados no projeto.
    """
    meta = _get_meta(projeto_id)
    res = scene_plan_svc.gerar_scene_plan(projeto_id, force=True)
    if not res.get("success"):
        return jsonify(res), 400

    plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    if plan and plan.get("cenas"):
        modo_prod = meta.get("modo_producao", "imagem_video")
        nome_pers = meta.get("nome_personagem", "")
        for c in plan["cenas"]:
            cid = c["id"]
            campos_up = {}
            if not c.get("modo_producao"):
                campos_up["modo_producao"] = modo_prod
            if not c.get("nome_personagem"):
                campos_up["nome_personagem"] = nome_pers
            if modo_prod == "somente_imagens":
                campos_up["tipo"] = "image"
            if campos_up:
                scene_plan_svc.atualizar_cena(projeto_id, cid, campos_up)

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

    # Salva na pasta prompts/ com uma linha em branco entre cada prompt
    prompts_dir = _project_dir(projeto_id) / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "storyboard_prompts.txt").write_text("\n\n\n".join(prompts_txt_formatados), encoding="utf-8")

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


# ===========================================================================
# 2. PRODUÇÃO v2
# ===========================================================================

@api_v2_bp.route("/producao/<projeto_id>/status", methods=["GET"])
def v2_producao_status(projeto_id: str):
    """Retorna o status completo da produção: cenas, status do Flow e contadores."""
    progresso = scene_plan_svc.progresso_scene_plan(projeto_id)
    plan = scene_plan_svc.carregar_scene_plan(projeto_id)

    # Verifica status do Flow via estado registrado e conexões SSE
    flow_status = {"conectado": False, "modo": "SSE"}
    try:
        from app_web import _FLOW_STATE, _FLOW_QUEUES
        est = _FLOW_STATE.get(projeto_id, {})
        esta_conectado = bool(est.get("conectado", False)) or len(_FLOW_QUEUES) > 0
        flow_status = {
            "conectado": esta_conectado,
            "conta": est.get("conta", "Extensão ELTON FLOW"),
            "modo": "SSE",
            "fila_parada": bool(est.get("fila_parada", False))
        }
    except Exception:
        pass

    return jsonify({
        "success": True,
        "progresso": progresso,
        "cenas": plan.get("cenas", []) if plan else [],
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
    """Enfileira todas as cenas pendentes e envia os jobs para a extensão ELTON FLOW via SSE."""
    try:
        from app_web import _FLOW_STATE, _FLOW_QUEUES
        est = _FLOW_STATE.setdefault(projeto_id, {})
        est["conectado"] = True
        est["conta"] = "Extensão ELTON FLOW (Chrome)"
        est["ultimo_ping"] = time.time()
        est["fila_parada"] = False

        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            return jsonify({"success": False, "error": "Nenhuma cena disponível"}), 400

        cenas_pendentes = [
            c for c in plan["cenas"]
            if c.get("status") in (scene_plan_svc.STATUS_PENDENTE, scene_plan_svc.STATUS_PROMPT_PRONTO, scene_plan_svc.STATUS_ERRO)
        ]

        enviadas_count = 0
        for cena in cenas_pendentes:
            cid = int(cena["id"])
            scene_plan_svc.atualizar_status_cena(projeto_id, cid, scene_plan_svc.STATUS_ENVIADA)
            prompt = cena.get("prompt_imagem") or cena.get("texto", "")
            is_anim = cena.get("tipo") == "video"
            job_id = f"job-{projeto_id}-{cid}-{int(time.time()*1000)}"
            msg = {
                "type": "LIRA_FLOW_JOB",
                "jobId": job_id,
                "projetoId": projeto_id,
                "sceneId": cid,
                "prompts": [prompt],
                "videoMode": is_anim
            }
            for q in _FLOW_QUEUES:
                try:
                    q.put(msg)
                except Exception:
                    pass
            enviadas_count += 1

        return jsonify({
            "success": True,
            "enfileiradas": enviadas_count,
            "message": f"{enviadas_count} cenas enviadas para a extensão ELTON FLOW."
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
    plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    if not plan or not plan.get("cenas"):
        return jsonify({"success": False, "error": "Plano de cenas não encontrado"}), 404

    cenas = plan["cenas"]
    total = len(cenas)
    com_midia = 0
    faltantes = []
    cenas_detalhe = []

    for c in cenas:
        cid = c["id"]
        arq = c.get("arquivo_midia", "")
        existe = bool(arq and Path(arq).exists())
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
            "arquivo_midia": arq if existe else "",
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

        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            return jsonify({"success": False, "error": "Plano de cenas não encontrado"}), 400

        cenas = plan["cenas"]
        lista_cenas_capcut = []
        for c in cenas:
            arq = c.get("arquivo_midia", "")
            lista_cenas_capcut.append({
                "start": float(c.get("tempo_inicio", 0)),
                "arquivo": arq if (arq and Path(arq).exists()) else None,
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
            nome_projeto=f"Studio2_{projeto_id}",
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



# ===========================================================================
# 1.1 CHARACTER INTELLIGENCE LAYER & MEMÓRIA VISUAL
# ===========================================================================

@api_v2_bp.route("/personagem/<projeto_id>/cadastrar", methods=["POST"])
def v2_personagem_cadastrar(projeto_id: str):
    """Cadastra permanentemente o personagem com imagem e trava sua identidade."""
    nome = (request.form.get("nome") or "").strip()
    if not nome:
        # Tenta pegar do JSON
        data = request.get_json(silent=True) or {}
        nome = (data.get("nome") or "").strip()

    if not nome:
        nome = "PersonagemPrincipal"

    arquivo = request.files.get("imagem")
    if not arquivo or not arquivo.filename:
        return jsonify({"success": False, "error": "Imagem de referência obrigatória"}), 400

    img_bytes = arquivo.read()
    if not img_bytes:
        return jsonify({"success": False, "error": "Arquivo de imagem vazio"}), 400

    estilo = request.form.get("estilo_visual") or "photorealistic_cinematic"
    res = character_svc.cadastrar_personagem(projeto_id, nome=nome, imagem_bytes=img_bytes, visual_style=estilo)
    return jsonify(res), 201


@api_v2_bp.route("/personagem/<projeto_id>/ativo", methods=["GET"])
def v2_personagem_ativo(projeto_id: str):
    """Retorna os dados completos do personagem ativo no projeto."""
    char_data = character_svc.obter_personagem_ativo(projeto_id)
    if not char_data:
        return jsonify({"success": True, "has_character": False, "character": None})
    return jsonify({"success": True, "has_character": True, "character": char_data})


@api_v2_bp.route("/personagem/<projeto_id>/remover", methods=["POST"])
def v2_personagem_remover(projeto_id: str):
    """Remove o personagem ativo do projeto."""
    data = request.get_json(silent=True) or {}
    nome = (data.get("nome") or "").strip()
    if not nome:
        char_data = character_svc.obter_personagem_ativo(projeto_id)
        if char_data:
            nome = char_data.get("name", "")

    if not nome:
        return jsonify({"success": False, "error": "Nome do personagem não informado"}), 400

    ok = character_svc.remover_personagem(projeto_id, nome)
    return jsonify({"success": ok, "nome": nome})


@api_v2_bp.route("/personagem/<projeto_id>/avatar", methods=["GET"])
def v2_personagem_avatar(projeto_id: str):
    """Serve a imagem oficial de referência do personagem ativo."""
    from flask import send_file
    char_data = character_svc.obter_personagem_ativo(projeto_id)
    if char_data and char_data.get("reference_image_abs"):
        ref_path = Path(char_data["reference_image_abs"])
        if ref_path.exists():
            return send_file(str(ref_path), mimetype="image/png")
    return jsonify({"error": "Avatar não encontrado"}), 404


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
    """FASE 2: Envia para animação apenas as cenas marcadas como vídeo que já possuem imagem gerada."""
    try:
        from services.playwright_flow import FlowQueueWorker
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            return jsonify({"success": False, "error": "Nenhuma cena disponível"}), 400

        cenas_video = [c["id"] for c in plan["cenas"] if (c.get("tipo") == "video" or c.get("animar") is True)]
        if not cenas_video:
            return jsonify({"success": False, "error": "Nenhuma cena marcada como vídeo para animar."}), 400

        ok = FlowQueueWorker.start_worker(projeto_id, scene_ids=cenas_video, modo="animacao")
        if not ok and FlowQueueWorker.get_worker().is_running_queue:
            return jsonify({"success": True, "already_running": True, "total_animar": len(cenas_video), "scene_ids": cenas_video})
        return jsonify({"success": ok, "total_animar": len(cenas_video), "scene_ids": cenas_video})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
