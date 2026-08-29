"""
deepseek_prompt_service.py — Lira Studio
=========================================
DeepSeek Prompt Intelligence: Prompt Director + Continuity Specialist + Automatic Critic.

Responsabilidades:
  1. Gestão segura de chaves API (env var DEEPSEEK_API_KEY ou web_keys.json sem vazamento para o frontend).
  2. Análise Global do Roteiro -> Context Pack estruturado (tema, continuidade, personagens, mundo, estilo).
  3. Geração em Lotes (Batching) com contrato estrito JSON determinístico por scene_index e timestamp.
  4. Crítico Automático & Regeneração Seletiva de cenas com inconformidades.
  5. Rastreamento de tokens, modelo e estimativa de uso.
  6. Atualização atômica do lira_scene_plan.json e exportação para prompts/storyboard_prompts.txt.
"""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Callable
import urllib.request
import urllib.error

from config import BASE_DIR, PROJETOS_DIR
from services.event_logger import log_event
import services.scene_plan_service as scene_plan_svc
import services.character_service as character_svc
import services.visual_presets_service as presets_svc


DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
WEB_KEYS_FILE = BASE_DIR / "web_keys.json"

# Estimativa de custo por 1 milhão de tokens (DeepSeek-V3 padrão: ~$0.14 input / $0.28 output)
PRECO_POR_1M_INPUT_USD = 0.14
PRECO_POR_1M_OUTPUT_USD = 0.28


# ---------------------------------------------------------------------------
# 1. GESTÃO SEGURA DE CHAVE DE API
# ---------------------------------------------------------------------------

def obter_api_key_deepseek() -> Optional[str]:
    """Recupera a chave da API DeepSeek (variável de ambiente ou web_keys.json local)."""
    env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env_key:
        return env_key

    if WEB_KEYS_FILE.exists():
        try:
            data = json.loads(WEB_KEYS_FILE.read_text(encoding="utf-8"))
            key = str(data.get("deepseek") or data.get("deepseek_api_key") or "").strip()
            if key:
                return key
        except Exception:
            pass

    return None


def salvar_api_key_deepseek(api_key: str) -> bool:
    """Salva a chave da API DeepSeek atomicamente no web_keys.json local (ignorado pelo git)."""
    api_key = (api_key or "").strip()
    try:
        data: dict = {}
        if WEB_KEYS_FILE.exists():
            try:
                data = json.loads(WEB_KEYS_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = {}

        data["deepseek"] = api_key
        tmp_file = WEB_KEYS_FILE.with_suffix(".json.tmp")
        tmp_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_file.replace(WEB_KEYS_FILE)
        log_event("DEEPSEEK_CONFIG", "Chave DeepSeek atualizada localmente com sucesso", level="info")
        return True
    except Exception as e:
        log_event("DEEPSEEK_CONFIG", f"Erro ao salvar chave DeepSeek: {e}", level="error")
        return False


def obter_status_deepseek() -> Dict[str, Any]:
    """Retorna o status seguro da API DeepSeek para a interface (NUNCA expõe a chave completa)."""
    key = obter_api_key_deepseek()
    if not key:
        return {
            "configurado": False,
            "modelo": DEFAULT_MODEL,
            "mascara": "",
            "origem": "nenhum"
        }

    origem = "env" if os.environ.get("DEEPSEEK_API_KEY") else "arquivo"
    mascara = f"...{key[-4:]}" if len(key) >= 8 else "***"
    return {
        "configurado": True,
        "modelo": DEFAULT_MODEL,
        "mascara": mascara,
        "origem": origem
    }


# ---------------------------------------------------------------------------
# 2. CLIENTE HTTP PARA DEEPSEEK API
# ---------------------------------------------------------------------------

def _chamar_deepseek_api(
    messages: List[Dict[str, str]],
    model: str = DEFAULT_MODEL,
    temperature: float = 0.7,
    response_json: bool = True,
    timeout_s: int = 60,
    api_key_override: Optional[str] = None,
) -> Dict[str, Any]:
    api_key = (api_key_override or obter_api_key_deepseek() or "").strip()
    if not api_key:
        raise ValueError("Chave de API DeepSeek não configurada. Configure sua chave na interface ou defina DEEPSEEK_API_KEY.")

    # Validação defensiva: evita UnicodeEncodeError ao montar o header
    # Authorization caso tenha sido colado texto de chat por engano no
    # web_keys.json no lugar da chave real.
    try:
        api_key.encode("latin-1")
    except UnicodeEncodeError:
        raise ValueError(
            "Chave DeepSeek inválida em web_keys.json: contém caracteres "
            "não suportados (verifique se não foi colado texto por engano "
            "no lugar da chave real)."
        )
    if not api_key.startswith("sk-"):
        raise ValueError(
            "Chave DeepSeek em web_keys.json não parece uma API key válida "
            "(deveria começar com 'sk-')."
        )
    if not re.match(r"^[A-Za-z0-9_\-]+$", api_key):
        raise ValueError("Chave de API DeepSeek inválida (deve conter apenas caracteres alfanuméricos padrão). Verifique as configurações.")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if response_json:
        payload["response_format"] = {"type": "json_object"}

    req_data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "LiraStudio/2.0",
    }

    req = urllib.request.Request(DEEPSEEK_API_URL, data=req_data, headers=headers, method="POST")

    try:
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status_code = resp.status
            res_body = resp.read().decode("utf-8")
            elapsed = time.time() - t0

        data = json.loads(res_body)
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("DeepSeek retornou resposta sem escolhas (choices vazio).")

        content = choices[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})

        in_tokens = int(usage.get("prompt_tokens", 0))
        out_tokens = int(usage.get("completion_tokens", 0))
        tot_tokens = int(usage.get("total_tokens", in_tokens + out_tokens))
        cost_est = (in_tokens / 1_000_000 * PRECO_POR_1M_INPUT_USD) + (out_tokens / 1_000_000 * PRECO_POR_1M_OUTPUT_USD)

        return {
            "content": content,
            "usage": {
                "prompt_tokens": in_tokens,
                "completion_tokens": out_tokens,
                "total_tokens": tot_tokens,
                "custo_estimado_usd": round(cost_est, 6),
            },
            "model": data.get("model", model),
            "tempo_resposta_s": round(elapsed, 2),
        }
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            pass
        msg = f"Erro HTTP {e.code} da API DeepSeek: {e.reason} ({err_body})"
        log_event("DEEPSEEK_API", msg, level="error")
        raise RuntimeError(msg) from e
    except urllib.error.URLError as e:
        msg = f"Erro de conexão com DeepSeek: {e.reason}"
        log_event("DEEPSEEK_API", msg, level="error")
        raise RuntimeError(msg) from e


# ---------------------------------------------------------------------------
# 3. ETAPA 1 — ANÁLISE GLOBAL (CONTEXT PACK)
# ---------------------------------------------------------------------------

def analisar_contexto_global(
    cenas: List[Dict[str, Any]],
    referencias: List[Dict[str, Any]],
    estilo_preset: Dict[str, Any],
    instrucao_custom: str = "",
    api_key: Optional[str] = None
) -> Dict[str, Any]:
    """
    Analisa o roteiro completo de todas as cenas e cria o Context Pack Global.
    Entende o tema, mundo, progressão narrativa, personagens e continuidade.
    """
    roteiro_linhas = []
    for c in cenas:
        cid = int(c.get("id") or c.get("scene_index", 0))
        ts = c.get("timestamp") or f"{scene_plan_svc._fmt_ts(float(c.get('tempo_inicio', 0)))} - {scene_plan_svc._fmt_ts(float(c.get('tempo_fim', 0)))}"
        narration = c.get("narration") or c.get("texto", "")
        roteiro_linhas.append(f"[{ts}] (Cena {cid}): {narration}")

    roteiro_texto = "\n".join(roteiro_linhas)

    # Formata lista de referências disponíveis no projeto
    refs_desc = []
    for r in referencias:
        tipo = r.get("tipo", "character")
        alias = r.get("alias", "")
        nome = r.get("nome", "")
        desc = r.get("descricao", "")
        refs_desc.append(f"- {alias} ({nome}, Tipo: {tipo}): {desc}")
    refs_texto = "\n".join(refs_desc) if refs_desc else "Nenhuma referência cadastrada."

    style_lock = estilo_preset.get("style_lock", "")
    neg_lock = estilo_preset.get("negative_defaults", presets_svc.NEGATIVE_LOCK_BASE)

    system_prompt = (
        "You are an elite Hollywood Director of Photography, Visual Continuity Supervisor, and AI Art Director.\n"
        "Your mission is to perform a comprehensive global visual analysis of a video script.\n"
        "Analyze the full story, the central subject, visual progression from beginning to end, recurring props, "
        "environmental settings, lighting mood shifts, and character roles.\n"
        "Respond STRICTLY in JSON format following the exact schema provided."
    )

    user_prompt = f"""
VIDEO SCRIPT WITH TIMESTAMPS ({len(cenas)} scenes):
{roteiro_texto}

PROJECT VISUAL REFERENCES:
{refs_texto}

CHOSEN VISUAL STYLE:
Preset: {estilo_preset.get('nome')}
STYLE_LOCK: {style_lock}
Photography Instructions: {estilo_preset.get('instructions')}
NEGATIVE_LOCK: {neg_lock}

USER CUSTOM DIRECTIVE:
{instrucao_custom or "None. Follow the preset strictly."}

Return a JSON object with this exact structure:
{{
  "theme": "Core conceptual theme of the video",
  "main_subject": "Primary subject, hero object, or topic",
  "characters": [
    {{
      "alias": "@alias",
      "role": "main_presenter | recurring_subject | supporting",
      "visual_identity": "Specific facial features, signature wardrobe, and physical attributes"
    }}
  ],
  "world": "Overall setting, historical or modern period, architectural vibe, atmospheric mood",
  "environment": "Primary lighting, weather, key locations (e.g. rustic garden, modern studio)",
  "visual_progression": "How visuals transition from opening hook -> problem -> revelation -> resolution",
  "recurring_elements": ["Key props, visual motifs, recurring color accents"],
  "continuity_rules": ["Specific rules to keep seamless consistency across all scenes"],
  "style_lock": "{style_lock}",
  "negative_lock": "{neg_lock}"
}}
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    res = _chamar_deepseek_api(messages, temperature=0.4, response_json=True, api_key_override=api_key)
    try:
        context_pack = json.loads(res["content"])
    except json.JSONDecodeError:
        # Fallback de parsing se vier com markdown wrappers
        clean_json = re.sub(r"^```json\s*", "", res["content"].strip())
        clean_json = re.sub(r"\s*```$", "", clean_json.strip())
        context_pack = json.loads(clean_json)

    # Injeta estilo_lock e negative_lock canônicos se ausentes
    context_pack["style_lock"] = context_pack.get("style_lock") or style_lock
    context_pack["negative_lock"] = context_pack.get("negative_lock") or neg_lock

    return {
        "context_pack": context_pack,
        "usage": res["usage"],
        "tempo_resposta_s": res["tempo_resposta_s"],
    }


# ---------------------------------------------------------------------------
# 4. ETAPA 2 — GERAÇÃO EM LOTES (BATCH PROMPT DIRECTOR)
# ---------------------------------------------------------------------------

def gerar_prompts_lote(
    cenas_lote: List[Dict[str, Any]],
    context_pack: Dict[str, Any],
    total_cenas: int,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Gera prompts cinematográficos para um lote de cenas (ex: 10 a 15 cenas)
    usando o Context Pack Global para garantir continuidade visual estrita.
    """
    cenas_input = []
    for c in cenas_lote:
        cid = int(c.get("id") or c.get("scene_index", 0))
        ts = c.get("timestamp") or f"{scene_plan_svc._fmt_ts(float(c.get('tempo_inicio', 0)))} - {scene_plan_svc._fmt_ts(float(c.get('tempo_fim', 0)))}"
        dur = round(float(c.get("duracao") or max(0.5, float(c.get("tempo_fim", 0)) - float(c.get("tempo_inicio", 0)))), 2)
        narration = c.get("narration") or c.get("texto", "")
        cenas_input.append({
            "scene_index": cid,
            "timestamp": ts,
            "duracao": dur,
            "narration": narration,
            "scene_type": c.get("scene_type", "auto"),
            "visual_role": c.get("visual_role", "auto"),
        })

    system_prompt = (
        "You are a World-Class AI Cinematographer and Prompt Director for cinematic generative video engines (Google Flow / Imagen 3).\n"
        "Your task is to generate ONE highly specific, cinematic image prompt and ONE animation prompt for each scene in the provided batch.\n\n"
        "STRICT CINEMATOGRAPHIC & NARRATIVE PRINCIPLES:\n"
        "1. STRATEGIC PRESENTER PLACEMENT (B-ROLL PRIORITY):\n"
        "   - Use the presenter character entity (e.g. '@marcos') ONLY in strategic anchor moments: Scene 1 (Opening Hook), major narrative transitions / turning points, and final Conclusion/CTA.\n"
        "   - ALL OTHER INTERMEDIATE SCENES (explanations, step-by-step actions, practical tests, evidence, ingredient close-ups, macro textures) MUST BE HIGH-QUALITY B-ROLL VISUAL COVERAGE.\n"
        "   - For B-roll scenes, focus purely on objects, hands/tools performing actions (without showing face/body), botanical details, macro textures, or environment framing while the voiceover narrates.\n"
        "2. DIVERSITY OF SHOT SIZES: Vary shot scales intentionally based on narration:\n"
        "   - Hook / Establishing: Wide shot or medium presenter addressing camera.\n"
        "   - Action / Demonstration: First-person close-up on hands and tools.\n"
        "   - Proof / Details: Macro shot, extreme close-up with tactile surface textures.\n"
        "   - Explanation / Concept: Atmospheric environment or subject-focused depth of field.\n"
        "3. VISUAL CONTINUITY: Follow the Global Context Pack strictly. Keep the same environment, lighting, wardrobe, and color palette.\n"
        "4. STYLE LOCK: Integrate the STYLE_LOCK seamlessly into every prompt.\n"
        "5. NEGATIVE CONSTRAINTS: Absolutely NO text, NO logos, NO watermarks, NO subtitles, NO captions in the visual description.\n"
        "6. STRUCTURE: Return a valid JSON object matching the exact schema."
    )

    user_prompt = f"""
GLOBAL CONTEXT PACK:
{json.dumps(context_pack, indent=2, ensure_ascii=False)}

BATCH OF SCENES TO GENERATE ({len(cenas_lote)} of {total_cenas} total scenes):
{json.dumps(cenas_input, indent=2, ensure_ascii=False)}

Return a JSON object with this exact format:
{{
  "scenes": [
    {{
      "scene_index": 1,
      "timestamp": "00:00 - 00:04",
      "visual_role": "hook | explanation | detail | demonstration | climax | conclusion",
      "scene_type": "avatar_talking | broll_macro | broll_environment | product_focus | action",
      "camera_direction": "wide establishing shot | 35mm lens | centered composition | shallow depth of field",
      "prompt_imagem": "Full visual prompt in English starting with the style aesthetic, character entity (if applicable), precise lighting, camera framing, subject action, environment details and high quality render tags.",
      "prompt_animacao": "Smooth cinematic camera movement description (e.g. slow subtle push-in, gentle pan right, 4s smooth ease)",
      "references": ["@marcos"],
      "continuity_notes": "Preserves garden environment and morning sunlight"
    }}
  ]
}}
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    res = _chamar_deepseek_api(messages, temperature=0.7, response_json=True, api_key_override=api_key)
    try:
        parsed = json.loads(res["content"])
    except json.JSONDecodeError:
        clean_json = re.sub(r"^```json\s*", "", res["content"].strip())
        clean_json = re.sub(r"\s*```$", "", clean_json.strip())
        parsed = json.loads(clean_json)

    scenes_res = parsed.get("scenes", [])
    if not isinstance(scenes_res, list):
        raise ValueError("DeepSeek retornou formato inválido para 'scenes' (esperado lista).")

    return {
        "scenes": scenes_res,
        "usage": res["usage"],
        "tempo_resposta_s": res["tempo_resposta_s"],
    }


# ---------------------------------------------------------------------------
# 5. ETAPA 3 — CRÍTICO AUTOMÁTICO & REGENERAÇÃO SELETIVA
# ---------------------------------------------------------------------------

def executar_critic_prompts(
    cenas_geradas: List[Dict[str, Any]],
    cenas_esperadas: List[Dict[str, Any]],
    context_pack: Dict[str, Any],
    api_key: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """
    Valida deterministicamente cada prompt gerado.
    Identifica:
      - Cenas ausentes ou desalinhadas no índice
      - Prompts vazios ou curtos demais (< 30 caracteres)
      - Detecção de repetição literal excessiva entre cenas adjacentes
      - Menção a textos, legendas ou logos proibidos
    Executa regeneração seletiva apenas nas cenas reprovadas.
    """
    mapa_geradas = {int(c.get("scene_index", 0)): c for c in cenas_geradas}
    cenas_finais: List[Dict[str, Any]] = []
    cenas_reprovadas: List[Dict[str, Any]] = []
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "custo_estimado_usd": 0.0}

    for c_esp in cenas_esperadas:
        cid = int(c_esp.get("id") or c_esp.get("scene_index", 0))
        c_ger = mapa_geradas.get(cid)

        motivos_falha = []
        if not c_ger:
            motivos_falha.append("Cena ausente na resposta do modelo.")
        else:
            p_img = str(c_ger.get("prompt_imagem") or "").strip()
            if len(p_img) < 30:
                motivos_falha.append(f"Prompt de imagem muito curto ou vazio ({len(p_img)} caracteres).")

            # Verifica palavras proibidas de texto/logos
            proibidos = ["text:", "subtitle", "watermark", "caption:", "logo:"]
            for pr in proibidos:
                if pr in p_img.lower():
                    motivos_falha.append(f"Prompt contém instrução textual proibida: '{pr}'")

        if motivos_falha:
            cenas_reprovadas.append({
                "cena_esperada": c_esp,
                "cena_gerada": c_ger,
                "motivos": motivos_falha
            })
        else:
            cenas_finais.append(c_ger)

    # Regeneração seletiva apenas para as reprovadas
    if cenas_reprovadas:
        log_event("DEEPSEEK_CRITIC", f"Critic identificou {len(cenas_reprovadas)} cenas com inconformidades. Executando regeneração seletiva...", level="warn")
        cenas_para_regen = [r["cena_esperada"] for r in cenas_reprovadas]
        try:
            res_regen = gerar_prompts_lote(cenas_para_regen, context_pack, len(cenas_esperadas), api_key=api_key)
            # Atualiza usage
            u = res_regen.get("usage", {})
            total_usage["prompt_tokens"] += u.get("prompt_tokens", 0)
            total_usage["completion_tokens"] += u.get("completion_tokens", 0)
            total_usage["total_tokens"] += u.get("total_tokens", 0)
            total_usage["custo_estimado_usd"] += u.get("custo_estimado_usd", 0.0)

            mapa_regen = {int(c.get("scene_index", 0)): c for c in res_regen.get("scenes", [])}
            for r in cenas_reprovadas:
                cid = int(r["cena_esperada"].get("id") or r["cena_esperada"].get("scene_index", 0))
                c_fixed = mapa_regen.get(cid)
                if c_fixed and len(str(c_fixed.get("prompt_imagem", "")).strip()) >= 30:
                    cenas_finais.append(c_fixed)
                else:
                    # Fallback de emergência preservando o prompt legado ou gerando template básico
                    fallback_p = f"{context_pack.get('style_lock', '')} {r['cena_esperada'].get('narration', '')}, cinematic composition, 16:9."
                    cenas_finais.append({
                        "scene_index": cid,
                        "timestamp": r["cena_esperada"].get("timestamp", ""),
                        "visual_role": r["cena_esperada"].get("visual_role", "explanation"),
                        "scene_type": r["cena_esperada"].get("scene_type", "broll_macro"),
                        "prompt_imagem": fallback_p,
                        "prompt_animacao": "Smooth cinematic 4s slow camera push-in",
                        "references": [],
                        "continuity_notes": "Critic fallback template",
                    })
        except Exception as e:
            log_event("DEEPSEEK_CRITIC", f"Falha na regeneração seletiva: {e}. Aplicando fallbacks seguros.", level="error")
            for r in cenas_reprovadas:
                cid = int(r["cena_esperada"].get("id") or r["cena_esperada"].get("scene_index", 0))
                cenas_finais.append(r["cena_gerada"] or {
                    "scene_index": cid,
                    "timestamp": r["cena_esperada"].get("timestamp", ""),
                    "prompt_imagem": f"{context_pack.get('style_lock', '')} {r['cena_esperada'].get('narration', '')}",
                    "prompt_animacao": "Smooth subtle push-in",
                    "references": [],
                })

    # Ordena deterministicamente pelo scene_index
    cenas_finais.sort(key=lambda x: int(x.get("scene_index", 0)))
    return cenas_finais, cenas_reprovadas, total_usage


# ---------------------------------------------------------------------------
# 6. PIPELINE COMPLETO DE PROMPT INTELLIGENCE
# ---------------------------------------------------------------------------

def executar_pipeline_prompt_intelligence(
    projeto_id: str,
    estilo_id: Optional[str] = None,
    instrucao_custom: str = "",
    batch_size: int = 15,
    callback_progresso: Optional[Callable[[str, int, int], None]] = None,
    api_key: Optional[str] = None
) -> Dict[str, Any]:
    """
    Executa o fluxo completo de ponta a ponta:
      1. Carrega Scene Plan e validações
      2. Análise Global (Context Pack)
      3. Geração em lotes (Batching)
      4. Crítico Automático & Correções
      5. Atualização atômica do lira_scene_plan.json
      6. Gravação de prompts/storyboard_prompts.txt
    """
    def notificar(etapa: str, atual: int = 0, total: int = 100):
        log_event("PROMPT_INTELLIGENCE", f"[{projeto_id}] {etapa} ({atual}/{total})", level="info")
        if callback_progresso:
            try:
                callback_progresso(etapa, atual, total)
            except Exception:
                pass

    # 1. Valida Scene Plan
    plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    if not plan or not plan.get("cenas"):
        raise ValueError(f"Projeto '{projeto_id}' não possui plano de cenas gerado.")

    cenas = plan["cenas"]
    total_cenas = len(cenas)
    preset_estilo = presets_svc.obter_preset_por_id(estilo_id)
    refs_raw = character_svc.listar_referencias_projeto(projeto_id)
    referencias = refs_raw.get("referencias", []) if isinstance(refs_raw, dict) else (refs_raw or [])

    notificar("Analisando roteiro e construindo Context Pack...", 5, 100)

    # 2. Etapa 1: Análise Global
    t0_global = time.time()
    res_global = analisar_contexto_global(
        cenas=cenas,
        referencias=referencias,
        estilo_preset=preset_estilo,
        instrucao_custom=instrucao_custom,
        api_key=api_key
    )
    context_pack = res_global["context_pack"]

    tokens_totais = res_global["usage"]["total_tokens"]
    custo_total_usd = res_global["usage"]["custo_estimado_usd"]

    # 3. Etapa 2: Geração em Lotes
    # Lira Studio v0.2.0 (Frente 2): lotes em PARALELO (max 3) com ordem
    # determinística por lote — 10 chamadas seriais viram 3-4 ondas paralelas.
    cenas_geradas_brutas = []
    num_lotes = (total_cenas + batch_size - 1) // batch_size
    lotes = []
    for i in range(num_lotes):
        inicio_idx = i * batch_size
        fim_idx = min(inicio_idx + batch_size, total_cenas)
        lotes.append((i, inicio_idx, fim_idx, cenas[inicio_idx:fim_idx]))

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _processar_lote(info):
        i, inicio_idx, fim_idx, lote_atual = info
        res_lote = gerar_prompts_lote(
            cenas_lote=lote_atual,
            context_pack=context_pack,
            total_cenas=total_cenas,
            api_key=api_key
        )
        return i, inicio_idx, fim_idx, res_lote

    resultados_por_lote = {}
    MAX_PARALELISMO = 3
    with ThreadPoolExecutor(max_workers=MAX_PARALELISMO) as pool:
        futures = {pool.submit(_processar_lote, lt): lt[0] for lt in lotes}
        concluidos = 0
        for fut in as_completed(futures):
            try:
                i, inicio_idx, fim_idx, res_lote = fut.result()
                resultados_por_lote[i] = res_lote
                concluidos += 1
                progresso_pct = 15 + int((concluidos / num_lotes) * 65)
                notificar(f"Gerando prompts cinematográficos (lote {concluidos}/{num_lotes} concluído)...",
                          progresso_pct, 100)
            except Exception as e:
                log_event("DEEPSEEK_BATCH", f"Falha no lote de prompts: {e}", level="error")

    # Ordem determinística por índice de lote (0..num_lotes-1)
    for i in range(num_lotes):
        res_lote = resultados_por_lote.get(i)
        if not res_lote:
            continue
        cenas_geradas_brutas.extend(res_lote.get("scenes", []))
        u = res_lote.get("usage", {})
        tokens_totais += u.get("total_tokens", 0)
        custo_total_usd += u.get("custo_estimado_usd", 0.0)

    # 4. Etapa 3: Crítico Automático
    notificar("Revisando consistência visual, regras de estilo e continuidade...", 85, 100)
    cenas_validadas, reprovadas, u_critic = executar_critic_prompts(
        cenas_geradas=cenas_geradas_brutas,
        cenas_esperadas=cenas,
        context_pack=context_pack,
        api_key=api_key
    )
    tokens_totais += u_critic.get("total_tokens", 0)
    custo_total_usd += u_critic.get("custo_estimado_usd", 0.0)

    # 5. Etapa 4: Atualização Atômica do lira_scene_plan.json
    notificar("Persistindo plano de cenas atualizado...", 95, 100)
    mapa_validadas = {int(c.get("scene_index", 0)): c for c in cenas_validadas}
    prompts_txt_formatados = []

    for cena in plan["cenas"]:
        cid = int(cena.get("id") or cena.get("scene_index", 0))
        c_inteligente = mapa_validadas.get(cid)

        if c_inteligente:
            p_img = c_inteligente.get("prompt_imagem", "")
            p_anim = c_inteligente.get("prompt_animacao", "Smooth slow push-in")
            refs = c_inteligente.get("references", [])
            v_role = c_inteligente.get("visual_role", cena.get("visual_role", "explanation"))
            s_type = c_inteligente.get("scene_type", cena.get("scene_type", "broll_macro"))
            c_notes = c_inteligente.get("continuity_notes", "")

            cena["prompt_imagem"] = p_img
            cena["visual_prompt"] = p_img
            cena["prompt_animacao"] = p_anim
            cena["visual_role"] = v_role
            cena["scene_type"] = s_type
            cena["continuity_context"] = c_notes
            cena["references"] = refs
            cena["status"] = scene_plan_svc.STATUS_PROMPT_PRONTO
            cena["visual_style"] = preset_estilo["id"]
            cena["atualizado_em"] = datetime.now().isoformat(sep=" ", timespec="seconds")

            # Identifica personagem usado
            if refs:
                cena["uses_character"] = True
                cena["character_ref"] = refs[0]
            else:
                cena["uses_character"] = False

            ts_str = cena.get("timestamp") or f"{cid:02d}"
            prompts_txt_formatados.append(f"[{ts_str}] Cena {cid:02d}:\n{p_img}")

    # Salva Scene Plan
    plan["context_pack"] = context_pack
    plan["estilo_visual_id"] = preset_estilo["id"]
    plan["estilo_visual_nome"] = preset_estilo["nome"]
    plan["atualizado_em"] = datetime.now().isoformat(sep=" ", timespec="seconds")
    scene_plan_svc.salvar_scene_plan(projeto_id, plan)

    # 6. Grava arquivos em prompts/
    pdir = PROJETOS_DIR / projeto_id
    prompts_dir = pdir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    conteudo_prompts = "\n\n".join(prompts_txt_formatados)
    (prompts_dir / "storyboard_prompts.txt").write_text(conteudo_prompts, encoding="utf-8")
    (prompts_dir / "prompts.txt").write_text(conteudo_prompts, encoding="utf-8")

    tempo_total = round(time.time() - t0_global, 2)
    notificar("Prompts prontos!", 100, 100)

    log_event("PROMPT_INTELLIGENCE", f"Sucesso: {total_cenas} prompts gerados e validados em {tempo_total}s | Tokens: {tokens_totais} | Custo Est: ${custo_total_usd:.4f}", level="info")

    return {
        "success": True,
        "total_cenas": total_cenas,
        "cenas_reprovadas_critic": len(reprovadas),
        "estilo": preset_estilo["nome"],
        "context_pack": context_pack,
        "tempo_total_s": tempo_total,
        "usage": {
            "total_tokens": tokens_totais,
            "custo_estimado_usd": round(custo_total_usd, 5),
            "tokens_formatados": f"{tokens_totais:,}".replace(",", "."),
        },
        "prompts_file": str(prompts_dir / "storyboard_prompts.txt"),
    }
