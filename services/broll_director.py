"""
broll_director.py — Direção de B-roll em 2 camadas:
1. Regras locais (fallback sem IA)
2. Claude batch planner central (via ScenePlanningContext)
"""
import json
import re
from pathlib import Path
from typing import Optional
from config import PROJETOS_DIR, ANTHROPIC_API_KEY, ANTHROPIC_MODEL


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

ACTION_VERBS = {"pulling","growing","spreading","blooming","moving","throwing","running",
                "breaking","yanking","harvesting","cutting","planting","digging","flowing",
                "falling","rising","cooking","mixing","pouring","building","climbing"}

# Objetos visuais dominantes — lista fixa para o mapa de tema por bloco de tempo
# Usada por get_dominant_visual para injetar keyword obrigatoria em cenas de transicao
DOMINANT_VISUALS = [
    "root", "leaf", "leaves", "flower", "stem", "seed", "petal", "bark",
    "fruit", "berry", "branch", "trunk", "soil", "ground", "garden", "plant",
]


def _detectar_tema_central(scenes: list) -> Optional[str]:
    """
    Identifica o tema central do video analisando as primeiras cenas.
    Retorna o objeto visual da lista fixa mais frequente nas cenas iniciais,
    ou None se nenhum objeto visual for mencionado.
    """
    if not scenes:
        return None
    import re as _re
    primeiras = scenes[:3]
    texto = " ".join(c.get("texto", "") for c in primeiras).lower()
    palavras = _re.findall(r'\b[a-zA-Z]{3,}\b', texto)
    freq = {}
    for p in palavras:
        if p in DOMINANT_VISUALS:
            freq[p] = freq.get(p, 0) + 1
    if not freq:
        return None
    return max(freq, key=freq.get)


def get_dominant_visual(scenes: list, current_index: int,
                        window_seconds: int = 30) -> Optional[str]:
    """
    Identifica um objeto visual dominante mencionado nas cenas anteriores
    dentro de uma janela de tempo (default 30s).

    Regra:
      - Olha as cenas anteriores cujo start_time esteja a menos de window_seconds
        da cena atual
      - Se alguma mencionou um objeto visual da lista fixa (DOMINANT_VISUALS),
        retorna esse objeto
      - Se não, tenta o tema central do vídeo (detectado nas primeiras cenas)
      - Se nada encontrado: retorna None (usa só keywords do texto atual)
    """
    if not scenes or current_index <= 0:
        return None

    cena_atual = scenes[current_index]
    start_atual = cena_atual.get("start_time")

    # Janela de tempo: cenas anteriores dentro de window_seconds da cena atual
    texto_janela = []
    if start_atual is not None:
        for idx in range(current_index - 1, -1, -1):
            cena_prev = scenes[idx]
            start_prev = cena_prev.get("start_time")
            if start_prev is None:
                texto_janela.append(cena_prev.get("texto", ""))
                continue
            if start_atual - start_prev > window_seconds:
                break
            texto_janela.append(cena_prev.get("texto", ""))
    else:
        # Fallback (projetos antigos sem start_time): janela de até 5 cenas
        for idx in range(max(0, current_index - 5), current_index):
            texto_janela.append(scenes[idx].get("texto", ""))

    if not texto_janela:
        return None

    import re as _re
    palavras = set(_re.findall(r'\b[a-zA-Z]{3,}\b', " ".join(texto_janela).lower()))

    # 1. Objeto visual dominante da lista fixa
    for v in DOMINANT_VISUALS:
        if v in palavras:
            return v

    # 2. Tema central do vídeo (se identificável nas primeiras cenas)
    tema_central = _detectar_tema_central(scenes)
    if tema_central and tema_central in palavras:
        return tema_central

    return None


def _calcular_video_score(item: dict) -> int:
    score = 0
    energy = item.get("energy", "")
    score += {"intense": 3, "dynamic": 2, "moderate": 1, "calm": 0}.get(energy, 0)
    visual_intent = item.get("visual_intent", "")
    if visual_intent == "action":
        score += 2
    elif visual_intent in ("macro", "demonstracao"):
        score += 1
    action_text = (item.get("action", "") or "").lower()
    if any(v in action_text for v in ACTION_VERBS):
        score += 1
    if not energy:
        scene_type = item.get("scene_type", "")
        if scene_type in ("demonstracao", "exemplo"):
            score += 2
        elif scene_type == "explicacao":
            score += 1
        keywords_text = " ".join(item.get("keywords", [])).lower()
        if any(v in keywords_text for v in ACTION_VERBS):
            score += 1
    return score


def _aplicar_ranking_midia(storyboard: list, proporcao_video: float = 0.30) -> None:
    """
    Aplica mix de midia TEMPORAL (não baseado em score de conteúdo):
      - Inicio (0-20%): 70% video / 30% photo
      - Meio (20-80%): 50% video / 50% photo
      - Final (80-100%): 30% video / 70% photo
    """
    from services.event_logger import log_event
    total = len(storyboard)
    if total == 0:
        return

    for i, item in enumerate(storyboard):
        progress = i / total  # 0.0 a 1.0

        if progress < 0.20:
            # Início (0-20%): 70% vídeo
            item["media_preference"] = "video" if (i % 10 < 7) else "photo"
        elif progress < 0.80:
            # Meio (20-80%): 50% vídeo
            item["media_preference"] = "video" if (i % 2 == 0) else "photo"
        else:
            # Final (80-100%): 30% vídeo
            item["media_preference"] = "video" if (i % 10 < 3) else "photo"

    n_video = sum(1 for item in storyboard if item["media_preference"] == "video")
    log_event("STORYBOARD",
              f"Mix temporal: {n_video}/{total} video ({n_video/total*100:.0f}%), "
              f"{total-n_video}/{total} photo", level="info")


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
    Extrai keywords do texto priorizando substantivos concretos e fotografáveis.
    Camada 1 - fallback sem IA.
    Usa lista de prioridade de substantivos (PRIORITY_NOUNS) + score heurístico.
    """
    import re as _re

    # Substantivos concretos/fotografáveis que têm prioridade máxima
    PRIORITY_NOUNS = {
        "dandelion", "rose", "daisy", "tulip", "sunflower", "lily", "orchid",
        "oak", "pine", "maple", "willow", "palm", "bamboo", "cactus",
        "mushroom", "fungus", "moss", "fern", "algae", "seaweed",
        "lavender", "rosemary", "thyme", "basil", "mint", "sage",
        "butterfly", "bee", "bird", "eagle", "hawk", "owl", "crow",
        "robin", "swan", "duck", "goose", "horse", "cow", "sheep",
        "rabbit", "deer", "fox", "wolf", "bear", "lion", "tiger",
        "fish", "shark", "whale", "dolphin", "snake", "lizard", "turtle",
        "frog", "ant", "spider", "worm", "snail", "caterpillar",
        "root", "leaf", "leaves", "flower", "petal", "stem", "branch",
        "bark", "trunk", "seed", "fruit", "nut", "berry", "thorn",
        "mountain", "river", "lake", "ocean", "sea", "forest", "tree",
        "field", "meadow", "garden", "soil", "rock", "stone", "sand",
        "sky", "cloud", "rain", "snow", "ice", "fire", "smoke",
        "sun", "moon", "star", "planet", "earth",
        "hand", "hands", "face", "eye", "eyes", "mouth", "head", "finger",
        "arm", "leg", "foot", "feet", "heart", "brain", "bone",
        "man", "woman", "child", "baby", "person", "people",
        "doctor", "chef", "farmer", "gardener", "scientist",
        "cup", "glass", "bottle", "plate", "bowl", "knife", "spoon",
        "fork", "pot", "pan", "oven", "table", "chair",
        "book", "paper", "pen", "pencil", "phone", "camera", "computer",
        "car", "truck", "bicycle", "boat", "plane", "train", "bus",
        "house", "door", "window", "wall", "roof", "floor", "stairs",
        "bridge", "road", "path", "trail", "gate", "fence", "tower",
        "clock", "watch", "lamp", "candle", "mirror", "frame", "box",
        "basket", "bag", "hat", "shoe", "dress", "shirt", "coat",
        "medicine", "pill", "jar", "tube", "syringe",
        "bread", "cheese", "milk", "egg", "butter", "honey", "sugar",
        "salt", "pepper", "rice", "pasta", "soup", "salad", "meat",
        "apple", "banana", "orange", "grape", "lemon", "lime", "berry",
        "corn", "wheat", "grain", "herb", "spice", "weed",
        "kitchen", "garden", "yard", "park", "beach", "cave", "desert",
        "jungle", "swamp", "farm", "barn", "shed", "greenhouse",
        "laboratory", "workshop", "studio", "office", "factory",
        "museum", "library", "school", "church", "temple", "castle",
        "city", "town", "village", "street", "square", "market",
        "guitar", "piano", "violin", "drum", "flute", "trumpet",
        "liver", "kidney", "lungs", "blood", "skin",
        "tea", "tincture", "salve", "oil", "cream", "poultice",
        "backyard", "lawn", "sidewalk", "crack", "concrete",
        "dandelions",
    }

    EXTRA_STOPWORDS = {
        "before", "after", "during", "while", "until", "since", "because",
        "although", "though", "through", "between", "among", "beneath",
        "beside", "behind", "beyond", "above", "below", "across",
        "around", "within", "without", "along", "toward", "towards",
        "every", "each", "both", "either", "neither", "several",
        "which", "that", "this", "those", "these", "what", "when",
        "where", "why", "how", "who", "whom", "whose",
        "been", "being", "having", "doing", "getting", "become",
        "became", "began", "begun", "broken", "built", "bought",
        "caught", "chose", "chosen", "come", "came", "drawn",
        "drank", "drunk", "driven", "drove", "eaten", "fell",
        "fallen", "found", "given", "gone", "grown", "known",
        "laid", "lain", "led", "lost", "made", "meant",
        "paid", "proven", "put", "ran", "run", "said",
        "seen", "sent", "shown", "sold", "spent", "stood",
        "taken", "thought", "told", "torn", "won", "worn",
        "written", "wrote",
        "long", "short", "tall", "wide", "deep", "broad",
        "still", "already", "yet", "always", "never", "often",
        "sometimes", "usually", "finally", "quickly", "slowly",
        "carefully", "easily", "hardly", "nearly", "almost",
        "quite", "rather", "pretty", "fairly", "extremely",
        "very", "too", "enough", "just", "even", "only",
        "much", "many", "more", "most", "less", "least",
        "little", "few", "plenty",
        "such", "same", "different", "other", "another",
        "important", "significant", "necessary", "possible",
        "common", "simple", "complex", "basic", "major", "minor",
        "known", "called", "considered", "regarded", "viewed",
        "exists", "existed", "existing", "remains", "remained",
        "appears", "appeared", "seems", "seemed", "looks", "looked",
        "becomes", "becoming", "causes", "caused", "creates", "created",
        "forms", "formed", "produces", "produced", "provides", "provided",
        "allows", "allowed", "enables", "enabled", "helps", "helped",
        "makes", "made", "uses", "used", "using", "takes", "taking",
        "found", "finds", "finding", "shows", "showing", "shown",
        "gives", "giving", "given", "brings", "bringing", "brought",
        "people", "things", "thing", "way", "ways", "part", "parts",
        "place", "places", "time", "times", "world", "life", "lives",
        "year", "years", "day", "days", "week", "weeks", "month",
        "number", "numbers", "system", "systems", "process", "processes",
        "result", "results", "example", "examples", "type", "types",
        "form", "forms", "kind", "kinds", "sort", "sorts",
        "right", "watching", "video", "someone", "some",
        "yanking", "out", "ground", "throwing", "away",
        "heals", "relied", "thousands", "today", "going",
        "walk", "through", "incredible", "healing", "properties",
        "probably", "walked", "past", "entire",
        "detox", "cleanse", "even", "help", "digestion",
        # Palavras de contexto histórico — não são o objeto visual da foto
        "pharmacies", "pharmacy", "medicine",
        "century", "centuries",
        "culture", "cultures",
        "history",
        "tradition",
        "purpose", "reason",
        "reference",
        "appearance",
        "name", "names",
        # Adjetivos e verbos genéricos que vazam no score heurístico
        "traditional",
        "appearing",
    }

    lang = _detect_language(text)

    if lang == "en":
        # Detecta substantivos próprios (maiúscula no original)
        proper_nouns = set(_re.findall(r'\b[A-Z][a-z]{2,}\b', text))

        words = _re.findall(r'\b[a-zA-Z]{4,}\b', text.lower())
        words_set = set(words)

        # Filtra: remove GENERIC_WORDS + EXTRA_STOPWORDS
        filtered = []
        for w in words:
            if w in GENERIC_WORDS:
                continue
            if w in EXTRA_STOPWORDS:
                continue
            # Palavras de 4 letras só passam se forem PRIORITY_NOUNS
            if len(w) == 4 and w not in PRIORITY_NOUNS:
                continue
            filtered.append(w)

        # Pontua cada candidato
        freq = {}
        for w in filtered:
            freq[w] = freq.get(w, 0) + 1

        scored = []
        for w, f in freq.items():
            score = 0
            if w in proper_nouns:
                score += 10  # Substantivo próprio = máxima prioridade
            if w in PRIORITY_NOUNS:
                score += 5   # Substantivo concreto/fotografável
            score += min(f, 5)  # Frequência (até +5)
            if len(w) >= 7:
                score += 2   # Palavras longas = mais específicas
            scored.append((score, w))

        scored.sort(key=lambda x: (-x[0], -len(x[1])))
        result = [w for _, w in scored[:max_keywords]]

        if result:
            return result

        # Fallback: priority noun no texto
        for w in words_set:
            if w in PRIORITY_NOUNS:
                return [w]

        # Fallback final: qualquer palavra que não seja GENERIC
        fallback = [w for w in words if w not in GENERIC_WORDS]
        if fallback:
            return fallback[:1]

        return []
    else:
        return []


def _gerar_local_scenes(cenas: list) -> list:
    resultado = []
    for idx, cena in enumerate(cenas):
        cid = cena.get("id") or cena.get("scene_id")
        texto = cena.get("texto", "")
        keywords = _extract_keywords_local(texto)

        # --- Keywords por bloco temático ---
        # Se um objeto visual dominante foi mencionado nos ultimos 30s do roteiro,
        # ele entra como keyword OBRIGATORIA (mesmo em cenas de contexto/transicao)
        dominant = get_dominant_visual(cenas, idx, window_seconds=30)
        if dominant and dominant not in keywords:
            keywords.insert(0, dominant)

        scene_type = "explicacao"
        texto_lower = texto.lower()
        for stype, skeywords in SCENE_TYPE_KEYWORDS.items():
            if any(kw in texto_lower for kw in skeywords):
                scene_type = stype
                break
        search_queries = _gerar_queries_locais(texto, scene_type, keywords)
        resultado.append({
            "id": cid, "texto": texto,
            "keywords": keywords if keywords else [f"{scene_type}_scene"],
            "scene_type": scene_type, "energy": "", "visual_intent": "", "action": "",
            "search_queries": search_queries,
            "fallback_queries": [f"{kw} nature" for kw in (keywords or [f"{scene_type}_scene"])]
        })
    return resultado


def _gerar_local(project_name: str, cenas: list, storyboard_file: Path) -> dict:
    storyboard = _gerar_local_scenes(cenas)
    _aplicar_ranking_midia(storyboard)
    with open(storyboard_file, "w", encoding="utf-8") as f:
        json.dump(storyboard, f, indent=2, ensure_ascii=False)
    return {"success": True, "project": project_name, "camada": "local",
            "camada_confiavel": False, "cenas_count": len(storyboard), "storyboard": storyboard}


def _build_cacheable_system_block(full_script: str, language: str, duration_total: float) -> dict:
    """
    Monta o bloco 1 do content (cacheável via cache_control ephemeral).
    Contém full_script + instruções gerais — IDÊNTICO entre todos os lotes.
    """
    text = f"""You are a B-roll director. Analyze the FULL SCRIPT below for overall context, then for EACH SCENE in the current batch suggest the ideal B-roll visual.

FULL SCRIPT ({duration_total:.0f}s, {language}):
{full_script}

OUTPUT FORMAT — For each scene, return a JSON array with objects:
{{
  "scene_id": int, "visual_intent": "closeup|wide|macro|aerial|action|abstract|establishing|detail",
  "subject": "main subject of the shot", "action": "what is happening",
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
    return {
        "type": "text",
        "text": text,
        "cache_control": {"type": "ephemeral"}
    }


def _build_chunk_block(scenes: list, total_scenes: int) -> dict:
    """
    Monta o bloco 2 do content (NÃO cacheável — varia por lote).
    Contém apenas as cenas do lote atual.
    """
    scenes_text = []
    for c in scenes:
        scenes_text.append(f"""Scene {c['scene_id']}/{total_scenes}:
  Time: {c['start_time']}s - {c['end_time']}s (duration: {c['duration']}s)
  Text: {c['texto']}
  Previous context: {c['previous_context'][:200] if c['previous_context'] else '(none)'}
  Next context: {c['next_context'][:200] if c['next_context'] else '(none)'}
  Topic: {c['topic'] or '(none)'}""")
    scenes_block = "\n\n".join(scenes_text)
    text = f"""SCENES IN THIS BATCH (with context):
{scenes_block}

Responda apenas com o JSON array conforme o formato especificado acima."""
    return {
        "type": "text",
        "text": text
    }


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
        cid = cena.get("scene_id") or cena.get("id") if isinstance(cena, dict) else 0
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
    import requests, time as _time, traceback
    from services.event_logger import log_event

    batch_full = ctx.build_batch()
    full_script = batch_full["full_script"]
    language = batch_full.get("language", "pt")
    duration_total = batch_full["duration_total"]
    all_scenes = batch_full["scenes"]
    total_scenes = len(all_scenes)
    CHUNK_SIZE = 20
    model = ANTHROPIC_MODEL

    # Bloco 1 — cacheável (full_script + instruções gerais, idêntico entre lotes)
    cacheable_block = _build_cacheable_system_block(full_script, language, duration_total)

    headers = {"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
               "content-type": "application/json",
               "anthropic-beta": "prompt-caching-2024-07-31"}
    storyboard_final = []
    total_claude_ok = 0
    total_local_fallback = 0
    inicio_total = _time.time()

    for chunk_start in range(0, total_scenes, CHUNK_SIZE):
        chunk_scenes = all_scenes[chunk_start:chunk_start + CHUNK_SIZE]
        chunk_ids = [c["scene_id"] for c in chunk_scenes]
        cenas_chunk = [c for c in cenas if (c.get("scene_id") or c.get("id")) in chunk_ids]
        # Bloco 2 — varia por lote (sem cache)
        chunk_block = _build_chunk_block(chunk_scenes, total_scenes)
        data = {"model": model, "max_tokens": 8192, "messages": [{"role": "user", "content": [cacheable_block, chunk_block]}]}
        try:
            inicio = _time.time()
            response = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=data, timeout=90)
            if response.status_code != 200:
                log_event("CLAUDE", f"Lote {chunk_start+1}-{chunk_start+len(chunk_scenes)}/{total_scenes} ERRO {response.status_code}: {response.text[:1000]}", level="error")
            response.raise_for_status()
            resp_data = response.json()
            # content é uma lista de content blocks (text, thinking, etc.)
            # Pega o texto do primeiro bloco do tipo "text"
            blocks = resp_data.get("content", [])
            content = "".join(
                block.get("text", "") for block in blocks if block.get("type") == "text"
            )
            if not content and blocks:
                content = blocks[0].get("text", blocks[0].get("thinking", ""))
            storyboard_chunk = _parsear_resposta_claude(content, cenas_chunk)
            storyboard_final.extend(storyboard_chunk)
            total_claude_ok += len(storyboard_chunk)
            log_event("CLAUDE", f"Lote {chunk_start+1}-{chunk_start+len(chunk_scenes)}/{total_scenes} OK em {round(_time.time()-inicio,2)}s", level="info")
        except Exception:
            log_event("CLAUDE", f"Lote {chunk_start+1}-{chunk_start+len(chunk_scenes)}/{total_scenes} FALHOU: {traceback.format_exc()}", level="error")
            local_result = _gerar_local_scenes(cenas_chunk)
            storyboard_final.extend(local_result)
            total_local_fallback += len(local_result)

    storyboard_final.sort(key=lambda s: s["id"] if isinstance(s["id"], int) else 0)
    _aplicar_ranking_midia(storyboard_final)

    with open(storyboard_file, "w", encoding="utf-8") as f:
        json.dump(storyboard_final, f, indent=2, ensure_ascii=False)

    tempo_total = round(_time.time() - inicio_total, 2)
    camada_confiavel = total_local_fallback == 0
    log_event("STORYBOARD",
              f"Storyboard finalizado: {total_claude_ok} via Claude, {total_local_fallback} via fallback, {tempo_total}s",
              level="info" if camada_confiavel else "error")

    return {"success": True, "project": project_name,
            "camada": "claude" if total_claude_ok > 0 else "local",
            "camada_confiavel": camada_confiavel, "cenas_count": len(storyboard_final),
            "storyboard": storyboard_final, "claude_ok": total_claude_ok,
            "local_fallback": total_local_fallback, "tempo_total": tempo_total}


def _gerar_com_deepseek(project_name: str, ctx, cenas: list, storyboard_file: Path) -> dict:
    import json, time as _time, traceback
    from services.event_logger import log_event
    from services.deepseek_prompt_service import _chamar_deepseek_api, obter_api_key_deepseek

    batch_full = ctx.build_batch()
    full_script = batch_full["full_script"]
    language = batch_full.get("language", "pt")
    duration_total = batch_full["duration_total"]
    all_scenes = batch_full["scenes"]
    total_scenes = len(all_scenes)
    CHUNK_SIZE = 20

    # Bloco 1 — instruções gerais + full_script
    cacheable_block = _build_cacheable_system_block(full_script, language, duration_total)
    system_prompt = cacheable_block["text"]

    storyboard_final = []
    total_deepseek_ok = 0
    total_local_fallback = 0
    inicio_total = _time.time()

    for chunk_start in range(0, total_scenes, CHUNK_SIZE):
        chunk_scenes = all_scenes[chunk_start:chunk_start + CHUNK_SIZE]
        chunk_ids = [c["scene_id"] for c in chunk_scenes]
        cenas_chunk = [c for c in cenas if (c.get("scene_id") or c.get("id")) in chunk_ids]
        
        # Bloco 2 — lote de cenas atual
        chunk_block = _build_chunk_block(chunk_scenes, total_scenes)
        user_prompt = chunk_block["text"]

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        try:
            inicio = _time.time()
            resp_data = _chamar_deepseek_api(messages, model="deepseek-chat", temperature=0.2, response_json=True)
            content = resp_data.get("choices", [{}])[0].get("message", {}).get("content", "")
            storyboard_chunk = _parsear_resposta_claude(content, cenas_chunk)
            storyboard_final.extend(storyboard_chunk)
            total_deepseek_ok += len(storyboard_chunk)
            log_event("DEEPSEEK", f"Lote {chunk_start+1}-{chunk_start+len(chunk_scenes)}/{total_scenes} OK em {round(_time.time()-inicio,2)}s", level="info")
        except Exception:
            log_event("DEEPSEEK", f"Lote {chunk_start+1}-{chunk_start+len(chunk_scenes)}/{total_scenes} FALHOU: {traceback.format_exc()}", level="error")
            local_result = _gerar_local_scenes(cenas_chunk)
            storyboard_final.extend(local_result)
            total_local_fallback += len(local_result)

    storyboard_final.sort(key=lambda s: s["id"] if isinstance(s["id"], int) else 0)
    _aplicar_ranking_midia(storyboard_final)

    with open(storyboard_file, "w", encoding="utf-8") as f:
        json.dump(storyboard_final, f, indent=2, ensure_ascii=False)

    tempo_total = round(_time.time() - inicio_total, 2)
    camada_confiavel = total_local_fallback == 0
    log_event("STORYBOARD",
              f"Storyboard finalizado: {total_deepseek_ok} via DeepSeek, {total_local_fallback} via fallback, {tempo_total}s",
              level="info" if camada_confiavel else "error")

    return {"success": True, "project": project_name,
            "camada": "deepseek" if total_deepseek_ok > 0 else "local",
            "camada_confiavel": camada_confiavel, "cenas_count": len(storyboard_final),
            "storyboard": storyboard_final, "claude_ok": 0, "deepseek_ok": total_deepseek_ok,
            "local_fallback": total_local_fallback, "tempo_total": tempo_total}


def gerar_storyboard(project_name: str, usar_claude: bool = True) -> dict:
    """
    Gera storyboard para todas as cenas.
    Usa ScenePlanningContext como fonte de dados.
    Verifica no meta.json qual o provedor configurado (Claude, DeepSeek ou Local).
    """
    from services.event_logger import log_event
    from services.scene_context import ScenePlanningContext

    project_dir = PROJETOS_DIR / project_name
    storyboard_file = project_dir / "storyboard.json"

    # Carrega config do projeto
    meta = {}
    meta_file = project_dir / "meta.json"
    if meta_file.exists():
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass

    provedor = meta.get("provedor_storyboard") or "claude"
    log_event("STORYBOARD", f"Iniciando geracao de storyboard para {project_name} (Provedor={provedor}, usar_claude={usar_claude})", level="info")

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

    # Executa o provedor correto
    if usar_claude:  # Se usar_claude for explicitamente falso (como em alguns fallbacks), forçamos local
        if provedor == "claude" and ANTHROPIC_API_KEY:
            try:
                return _gerar_com_claude(project_name, ctx, cenas, storyboard_file)
            except Exception as e:
                log_event("STORYBOARD", f"Claude batch falhou: {str(e)} — usando fallback local", level="warn")
        elif provedor == "deepseek":
            try:
                from services.deepseek_prompt_service import obter_api_key_deepseek
                if obter_api_key_deepseek():
                    return _gerar_com_deepseek(project_name, ctx, cenas, storyboard_file)
                else:
                    log_event("STORYBOARD", "DeepSeek API Key não configurada — usando fallback local", level="warn")
            except Exception as e:
                log_event("STORYBOARD", f"DeepSeek storyboard falhou: {str(e)} — usando fallback local", level="warn")
        elif provedor == "local":
            log_event("STORYBOARD", "Usando provedor local determinístico (Modo Local configurado)", level="info")

    # Camada 1 (fallback local)
    return _gerar_local(project_name, cenas, storyboard_file)