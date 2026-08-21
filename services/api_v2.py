"""
services/api_v2.py — Rotas da API v2 / Shadow Routing para ULTRACUT3 Studio 2.0
================================================================================
Endpoints modernos sob o prefixo /api/v2/... isolados da v1:
  - STUDIO v2: Criação, configuração, transcrição/storyboard e geração de prompts
  - PRODUÇÃO v2: Fila, status do Google Flow, envio de cena e auto-importação
  - ARQUIVOS v2: Listagem categorizada (audio, srt, imagens, videos, prompts, capcut) e downloads
  - MONTAGEM v2: Sincronização de timeline e exportação para CapCut
"""
import os
import json
import shutil
from datetime import datetime
from pathlib import Path
from flask import Blueprint, request, jsonify, send_file, current_app

from config import PROJETOS_DIR, OUTPUT_DIR
from services.event_logger import log_event
import services.scene_plan_service as scene_plan_svc
from services.video_encoder import sanitizar_nome_arquivo

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
    Gera prompts inteligentes para todas as cenas com identificador de personagem @Nome
    e salva tanto no lira_scene_plan.json quanto na pasta prompts/storyboard_prompts.txt.
    """
    data = request.get_json(force=True, silent=True) or {}
    meta = _get_meta(projeto_id)

    estilo_visual = data.get("estilo_visual") or meta.get("estilo_visual") or "photorealistic_cinematic"
    nome_personagem = data.get("nome_personagem") or meta.get("nome_personagem") or ""
    style_lock = MASTER_STYLES.get(estilo_visual, MASTER_STYLES["photorealistic_cinematic"])

    plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    if not plan or not plan.get("cenas"):
        scene_plan_svc.gerar_scene_plan(projeto_id, force=True)
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)

    if not plan or not plan.get("cenas"):
        return jsonify({"success": False, "error": "Cenas não encontradas. Verifique a transcrição."}), 400

    cenas = plan["cenas"]
    linhas_storyboard = []

    for c in cenas:
        cid = c["id"]
        texto = c.get("texto", "")
        tem_personagem = scene_plan_svc._cena_tem_personagem(texto, nome_personagem=nome_personagem)

        if tem_personagem and nome_personagem:
            pref = f"@{nome_personagem} "
        elif tem_personagem:
            pref = "@personagem "
        else:
            pref = ""

        prompt_img = f"{pref}{style_lock['estilo']}, {texto}, {style_lock['composicao']}".strip(", ")
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
            "nome_personagem": nome_personagem if tem_personagem else "",
            "status": scene_plan_svc.STATUS_PROMPT_PRONTO,
        })

        ts_ini = scene_plan_svc._fmt_ts(c.get("tempo_inicio", 0))
        tipo_tag = "[VIDEO]" if c.get("tipo") == "video" else "[IMAGEM]"
        linhas_storyboard.append(f"Cena {cid:03d} [{ts_ini}] {tipo_tag}\n{prompt_img}\n")

    # Salva cópia na pasta prompts/
    prompts_dir = _project_dir(projeto_id) / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "storyboard_prompts.txt").write_text("\n".join(linhas_storyboard), encoding="utf-8")

    plan_atualizado = scene_plan_svc.carregar_scene_plan(projeto_id)
    return jsonify({
        "success": True,
        "total": len(cenas),
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

    # Verifica status do Flow
    flow_status = {"conectado": False, "modo": "CDP"}
    try:
        from services.playwright_flow import FlowSessionManager
        sessao = FlowSessionManager.obter_sessao()
        if sessao:
            flow_status = sessao.obter_status()
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
    """Envia uma única cena para geração no Google Flow."""
    data = request.get_json(force=True, silent=True) or {}
    scene_id = data.get("scene_id")
    if scene_id is None:
        return jsonify({"success": False, "error": "scene_id é obrigatório"}), 400

    try:
        from services.playwright_flow import FlowQueueWorker
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan:
            return jsonify({"success": False, "error": "Plano de cenas não encontrado"}), 404

        cena = next((c for c in plan.get("cenas", []) if int(c["id"]) == int(scene_id)), None)
        if not cena:
            return jsonify({"success": False, "error": f"Cena {scene_id} não encontrada"}), 404

        tipo_override = data.get("tipo")
        video_mode = (tipo_override == "video") if tipo_override else (cena.get("tipo") == "video")

        worker = FlowQueueWorker.obter_instancia()
        job_id = f"v2_{projeto_id}_scene_{scene_id}_{int(datetime.now().timestamp())}"
        worker.enfileirar_cena(job_id, projeto_id, cena, video_mode=video_mode)

        return jsonify({"success": True, "job_id": job_id, "scene_id": scene_id, "status": "enfileirada"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_v2_bp.route("/producao/<projeto_id>/iniciar_fila", methods=["POST"])
def v2_producao_iniciar_fila(projeto_id: str):
    """Enfileira todas as cenas pendentes com prompt pronto para produção no Flow."""
    try:
        from services.playwright_flow import FlowQueueWorker
        plan = scene_plan_svc.carregar_scene_plan(projeto_id)
        if not plan or not plan.get("cenas"):
            return jsonify({"success": False, "error": "Nenhuma cena disponível"}), 400

        worker = FlowQueueWorker.obter_instancia()
        enfileiradas = 0
        for cena in plan["cenas"]:
            if cena.get("status") in (scene_plan_svc.STATUS_PENDENTE, scene_plan_svc.STATUS_PROMPT_PRONTO, scene_plan_svc.STATUS_ERRO):
                job_id = f"v2_{projeto_id}_scene_{cena['id']}_{int(datetime.now().timestamp())}"
                worker.enfileirar_cena(job_id, projeto_id, cena, video_mode=(cena.get("tipo") == "video"))
                enfileiradas += 1

        return jsonify({"success": True, "enfileiradas": enfileiradas})
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

    for c in cenas:
        cid = c["id"]
        arq = c.get("arquivo_midia", "")
        if arq and Path(arq).exists():
            com_midia += 1
        else:
            faltantes.append(cid)

    pode_montar = (com_midia == total and total > 0)
    meta = _get_meta(projeto_id)
    tem_audio = bool(meta.get("arquivo_audio") and Path(meta["arquivo_audio"]).exists())

    return jsonify({
        "success": True,
        "total_cenas": total,
        "cenas_com_midia": com_midia,
        "cenas_faltantes": faltantes,
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
            lista_cenas_capcut.append({
                "scene_id": c["id"],
                "start_time": c.get("tempo_inicio", 0),
                "end_time": c.get("tempo_fim", 5.0),
                "arquivo_midia": c.get("arquivo_midia", ""),
                "tipo": c.get("tipo", "image"),
            })

        pasta_drafts = detectar_pasta_drafts()
        resultado = criar_draft_imagens(
            nome_projeto=f"Studio2_{projeto_id}",
            cenas=lista_cenas_capcut,
            pasta_drafts=pasta_drafts,
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
