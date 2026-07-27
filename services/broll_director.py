"""
broll_director.py — Direção de B-roll em 2 camadas:
1. Regras locais (fallback sem IA)
2. Claude batch planner central (via ScenePlanningContext)
"""
import json
import re
from pathlib import Path
from typing import Optional
from config import PROJETOS_DIR, ANTHROPIC_API_KEY


# Palavras genéricas para ignorar na extração de keywords
GENERIC_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "this", "that", "these", "those", "it", "its", "they", "them",
    "he", "she", "we", "you", "i", "me", "my", "your", "his", "her",
    "our", "their", "and", "or", "but", "so", "if", "because", "as",
    "until", "while", "of", "at", "by", "for", "with", "about", "against",
    "between", "into", "through", "during", "before", "after", "above",
    "below", "to", "from", "up", "down", "in", "out", "on", "off", "over",
    "under", "again", "further", "then", "once", "here", "there", "when",
    "where", "why", "how", "all", "each", "every", "both", "few", "more",
    "most", "other", "some", "such", "no", "nor", "not", "only", "own",
    "same", "very", "just", "also", "too", "really", "quite", "actually",
    "de", "da", "do", "em", "para", "com", "uma", "um", "uns", "umas",
    "ele", "ela", "eles", "elas", "nos", "vos", "lhe", "lhes", "seu",
    "sua", "seus", "suas", "meu", "minha", "teu", "tua", "que", "como",
    "mais", "mas", "por", "já", "ainda", "também", "bem", "muito", "pouco",
    "depois", "antes", "entre", "contra", "sem", "sob", "sobre", "até",
    "porque", "pois", "portanto", "embora", "conforme", "mediante"
}

SCENE_TYPE_KEYWORDS = {
    "introducao": ["introduction", "welcome", "hello", "start", "beginning",
                   "começar", "iniciar", "bem-vindo", "olá", "startup", "intro",
                   "apresentação", "apresentar"],
    "explicacao": ["explain", "how", "works", "meaning", "concept", "definition",
                   "explicar", "significa", "conceito", "definição", "funciona",
                   "entender", "compreender", "significado"],
    "exemplo": ["example", "like", "such", "instance", "sample",
                "exemplo", "como", "tal", "qual", "mostrar"],
    "demonstracao": ["demonstrate", "show", "display", "walkthrough", "tutorial",
                     "demonstrar", "mostrar", "exibir", "passo", "tutorial"],
    "comparacao": ["compare", "versus", "vs", "difference", "better", "worse",
                   "comparar", "versus", "diferença", "melhor", "pior"],
    "conclusao": ["conclusion", "summary", "final", "end", "finish",
                  "concluir", "resumo", "final", "fim", "terminar"]
}


def _detect_language(text: str) -> str:
    """Detecta se o texto é inglês ou outro idioma usando langdetect."""
    try:
        import langdetect
        return langdetect.detect(text)
    except Exception:
        # Fallback simples: checa se contém palavras portuguesas comuns
        pt_words = {"de", "da", "do", "para", "com", "uma", "como", "mais",
                    "por", "que", "dos", "das", "aos", "nas", "nos", "pelo",
                    "pela", "isso", "aquele", "aquela", "entre", "sobre"}
        words = set(text.lower().split())
        if words & pt_words:
            return "pt"
        return "en"


def _extract_keywords_local(text: str, max_keywords: int = 3) -> list:
    """
    Extrai keywords reais do texto (camada 1 - fallback sem IA).
    """
    lang = _detect_language(text)

    if lang == "en":
        words = re.findall(r'\b[a-zA-Z]{4,}\b', text.lower())
        words = [w for w in words if w not in GENERIC_WORDS]
        freq = {}
        for w in words:
            freq[w] = freq.get(w, 0) + 1
        sorted_words = sorted(freq.items(), key=lambda x: -x[1])
        return [w for w, _ in sorted_words[:max_keywords]]
    else:
        return []


def _determinar_preferencia_midia(indice_cena: int, total_cenas: int) -> str:
    """
    Determina preferencia de midia por posicao da cena.
    Politica 70/30: 70% foto, 30% video.
    - Primeiros 20%: mais videos para criar energia
    - Restante 80%: videos esparsos
    """
    proporcao = indice_cena / total_cenas if total_cenas > 0 else 0
    if proporcao <= 0.20:
        # Primeiros 20% — video a cada ~2 cenas
        return "video" if indice_cena % 2 == 0 else "photo"
    else:
        # Restantes 80% — video a cada ~10 cenas
        return "video" if indice_cena % 10 == 0 else "photo"


def _gerar_local(project_name: str, cenas: list, storyboard_file: Path) -> dict:
    """Camada 1: regras locais (fallback)."""
    from services.event_logger import log_event
    storyboard = []
    total = len(cenas)
    for idx, cena in enumerate(cenas, 1):
        cid = cena.get("id") or cena.get("scene_id") or idx
        log_event("STORYBOARD", f"Cena {cid}/{total}: extraindo keywords do texto...", level="info")
        texto = cena.get("texto", "")
        keywords = _extract_keywords_local(texto)

        # Detecta scene_type
        scene_type = "explicacao"
        texto_lower = texto.lower()
        for stype, skeywords in SCENE_TYPE_KEYWORDS.items():
            if any(kw in texto_lower for kw in skeywords):
                scene_type = stype
                break

        # Politica de midia 70/30 posicional
        media_preference = _determinar_preferencia_midia(idx - 1, total)
        log_event("STORYBOARD", f"Cena {cid}/{total}: preferencia={media_preference} (posicao={(idx-1)/total*100:.0f}%)", level="info")

        # Gera search_queries mesmo no modo local
        search_queries = _gerar_queries_locais(texto, scene_type, keywords)

        storyboard.append({
            "id": cid,
            "texto": texto,
            "keywords": keywords if keywords else [f"{scene_type}_scene"],
            "scene_type": scene_type,
            "media_preference": media_preference,
            "search_queries": search_queries,
            "fallback_queries": [f"{kw} nature" for kw in (keywords or [f"{scene_type}_scene"])]
        })

    with open(storyboard_file, "w", encoding="utf-8") as f:
        json.dump(storyboard, f, indent=2, ensure_ascii=False)

    return {
        "success": True,
        "project": project_name,
        "camada": "local",
        "cenas_count": len(storyboard),
        "storyboard": storyboard
    }


def _build_batch_prompt(ctx) -> str:
    """
    Monta prompt completo usando ScenePlanningContext.build_batch().
    Claude recebe roteiro completo + todas as cenas com contexto.
    """
    batch = ctx.build_batch()
    full_script = batch["full_script"]
    scenes = batch["scenes"]

    # Formata cada cena com contexto completo
    scenes_text = []
    for c in scenes:
        scenes_text.append(f"""Scene {c['scene_id']}/{batch['segment_count']}:
  Time: {c['start_time']}s - {c['end_time']}s (duration: {c['duration']}s)
  Text: {c['texto']}
  Previous context: {c['previous_context'][:200] if c['previous_context'] else '(none)'}
  Next context: {c['next_context'][:200] if c['next_context'] else '(none)'}
  Topic: {c['topic'] or '(none)'}""")

    scenes_block = "\n\n".join(scenes_text)

    prompt = f"""You are a B-roll director. Analyze the FULL SCRIPT below, then for EACH SCENE suggest the ideal B-roll visual.

FULL SCRIPT ({batch['duration_total']:.0f}s, {batch.get('language', 'pt')}):
{full_script}

SCENES (with context):
{scenes_block}

For each scene, return a JSON array with objects:
{{
  "scene_id": int,
  "visual_intent": "closeup|wide|macro|aerial|action|abstract|establishing|detail",
  "subject": "main subject of the shot",
  "action": "what is happening",
  "environment": "where it takes place",
  "shot_type": "extreme_closeup|closeup|medium|wide|extreme_wide",
  "energy": "calm|moderate|dynamic|intense",
  "emotion": "neutral|curious|surprising|educational|dramatic|inspiring",
  "primary_queries": ["3-5 English search queries for stock footage"],
  "fallback_queries": ["3-5 fallback queries preserving intent"],
  "synonyms": ["alternative English keywords"]
}}

RULES:
- The narration can be in ANY language. Translate concepts by MEANING and CONTEXT.
- NEVER translate word-for-word. Understand the concept.
- NEVER degrade to generic terms in fallback.
- Return ONLY valid JSON, no other text."""

    return prompt


def _gerar_queries_locais(texto: str, scene_type: str, keywords: list) -> list:
    """Gera queries de busca para o modo local (sem Claude).
    Retorna ate 3 queries: especifica, generica, fallback."""
    queries = []
    if keywords:
        # Query especifica: principais keywords
        queries.append(" ".join(keywords[:2]))
        # Query generica: combina com scene_type
        if len(keywords) >= 2:
            queries.append(" ".join(keywords))
    # Query fallback universal baseada no tipo de cena
    tipo_fallback = {
        "introducao": "nature landscape",
        "explicacao": "nature plant",
        "exemplo": "nature closeup",
        "demonstracao": "nature action",
        "comparacao": "nature contrast",
        "conclusao": "nature wide"
    }
    fallback = tipo_fallback.get(scene_type, "nature plant")
    if fallback not in queries:
        queries.append(fallback)
    return queries[:3]


def _parsear_resposta_claude(content: str, cenas: list) -> list:
    """
    Parseia a resposta JSON do Claude e mescla com dados das cenas.
    Retorna lista de storyboard enriquecido com search_strategies.
    """
    json_match = re.search(r'\[\s*\{.*\}\s*\]', content, re.DOTALL)
    if not json_match:
        raise ValueError("Resposta do Claude não contém JSON válido")

    claude_data = json.loads(json_match.group())
    claude_map = {item.get("scene_id"): item for item in claude_data}

    storyboard = []
    for cena in cenas:
        cid = cena.get("id") if isinstance(cena, dict) else 0
        texto = cena.get("texto", "") if isinstance(cena, dict) else ""
        scene_type = cena.get("scene_type", "explicacao") if isinstance(cena, dict) else "explicacao"

        if cid in claude_map:
            item = claude_map[cid]
            # Monta search_strategies a partir do retorno do Claude
            primary_q = item.get("primary_queries", [])
            fallback_q = item.get("fallback_queries", [])
            synonyms = item.get("synonyms", [])

            # Concatena tudo em uma única lista de queries (pool compartilhado)
            # Ordena: primary primeiro, depois fallback, depois sinônimos
            search_queries = list(primary_q)
            for fq in fallback_q:
                if fq not in search_queries:
                    search_queries.append(fq)
            for syn in synonyms:
                if syn not in search_queries:
                    search_queries.append(syn)

            storyboard.append({
                "id": cid,
                "texto": texto,
                "keywords": primary_q[:3] if primary_q else [f"scene_{cid}"],
                "scene_type": item.get("scene_type", scene_type),
                "media_preference": "video" if item.get("energy") in ("dynamic", "intense", "moderate") else "photo",
                "visual_intent": item.get("visual_intent", ""),
                "subject": item.get("subject", ""),
                "action": item.get("action", ""),
                "environment": item.get("environment", ""),
                "shot_type": item.get("shot_type", ""),
                "energy": item.get("energy", ""),
                "emotion": item.get("emotion", ""),
                "primary_queries": primary_q,
                "fallback_queries": fallback_q,
                "synonyms": synonyms,
                "search_queries": search_queries
            })
        else:
            # Fallback local para cena não processada pelo Claude
            storyboard.append({
                "id": cid,
                "texto": texto,
                "keywords": [f"scene_{cid}"],
                "scene_type": scene_type,
                "media_preference": "video",
                "visual_intent": "",
                "subject": "",
                "action": "",
                "environment": "",
                "shot_type": "",
                "energy": "",
                "emotion": "",
                "primary_queries": [],
                "fallback_queries": [],
                "synonyms": [],
                "search_queries": [f"scene_{cid}"]
            })

    return storyboard


def _gerar_com_claude(project_name: str, ctx, cenas: list, storyboard_file: Path) -> dict:
    """
    Camada 2: Claude Batch Planner.
    Usa ScenePlanningContext para construir o prompt completo.
    Uma única chamada para todas as cenas.
    """
    import requests
    from services.event_logger import log_event
    import time as _time

    prompt = _build_batch_prompt(ctx)

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }

    data = {
        "model": "claude-3-sonnet-20241022",
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": prompt}]
    }

    inicio = _time.time()
    log_event("CLAUDE", f"Batch planning started: {ctx.cenas_count} scenes", level="info")

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=headers,
        json=data,
        timeout=180  # maior timeout para batch grande
    )
    response.raise_for_status()
    result = response.json()

    tempo_planejamento = round(_time.time() - inicio, 2)
    content = result["content"][0]["text"]

    # Parseia resposta
    storyboard = _parsear_resposta_claude(content, cenas)

    # Métricas
    queries_geradas = sum(len(s.get("search_queries", [])) for s in storyboard)
    log_event("CLAUDE", f"Single batch call completed: {ctx.cenas_count} scenes, {queries_geradas} queries, {tempo_planejamento}s",
              level="info")

    # Salva storyboard enriquecido
    with open(storyboard_file, "w", encoding="utf-8") as f:
        json.dump(storyboard, f, indent=2, ensure_ascii=False)

    return {
        "success": True,
        "project": project_name,
        "camada": "claude",
        "cenas_count": len(storyboard),
        "storyboard": storyboard,
        "queries_geradas": queries_geradas,
        "tempo_planejamento": tempo_planejamento,
        "claude_calls": 1
    }


def gerar_storyboard(project_name: str, usar_claude: bool = True) -> dict:
    """
    Gera storyboard para todas as cenas.
    Usa ScenePlanningContext como fonte de dados.
    Camada 2 (Claude batch planner) se disponível, senão camada 1 (fallback local).
    """
    from services.event_logger import log_event
    from services.scene_context import ScenePlanningContext

    log_event("STORYBOARD", f"Iniciando geracao de storyboard para {project_name} (Claude={usar_claude})", level="info")

    project_dir = PROJETOS_DIR / project_name
    storyboard_file = project_dir / "storyboard.json"

    # Usa ScenePlanningContext para carregar dados de forma unificada
    ctx = ScenePlanningContext(project_name)
    cenas = ctx.cenas  # cenas normalizadas com contexto

    if not cenas:
        # Fallback: tenta carregar cenas.json diretamente
        cenas_file = project_dir / "cenas.json"
        if not cenas_file.exists():
            return {"success": False, "error": "cenas.json não encontrado"}
        with open(cenas_file, "r", encoding="utf-8") as f:
            cenas = json.load(f)
        if not cenas:
            return {"success": False, "error": "Nenhuma cena encontrada"}

    # Tenta camada 2 (Claude batch planner)
    if usar_claude and ANTHROPIC_API_KEY:
        try:
            return _gerar_com_claude(project_name, ctx, cenas, storyboard_file)
        except Exception as e:
            log_event("STORYBOARD", f"Claude batch falhou: {str(e)} — usando fallback local", level="warn")

    # Camada 1 (fallback local)
    return _gerar_local(project_name, cenas, storyboard_file)