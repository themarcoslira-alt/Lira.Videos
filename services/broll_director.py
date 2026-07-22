"""
broll_director.py — Direção de B-roll em 2 camadas:
1. Regras locais (fallback sem IA)
2. Claude batch (para todas as cenas de uma vez)
"""
import json
import re
from pathlib import Path
from typing import Optional
from config import PROJETOS_DIR, OPENAI_API_KEY


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
    Se for inglês, extrai palavras do texto.
    Se não for, retorna templates genéricos por scene_type.
    """
    lang = _detect_language(text)

    if lang == "en":
        words = re.findall(r'\b[a-zA-Z]{4,}\b', text.lower())
        words = [w for w in words if w not in GENERIC_WORDS]
        # Conta frequência
        freq = {}
        for w in words:
            freq[w] = freq.get(w, 0) + 1
        sorted_words = sorted(freq.items(), key=lambda x: -x[1])
        return [w for w, _ in sorted_words[:max_keywords]]
    else:
        # Texto em português ou outro idioma — fallback genérico
        return []


def _build_batch_prompt(cenas: list) -> str:
    """
    Monta o prompt para Claude batch (camada 2).
    Uma chamada só para todas as cenas.
    """
    cenas_text = "\n".join([
        f"Cena {c['id']}: \"{c['texto'][:200]}\""
        for c in cenas
    ])

    prompt = f"""You are a B-roll director. For each scene below, suggest the ideal B-roll visual in natural English keywords. Think like a stock photographer describing a scene.

RULES:
- The narration text can be in ANY language. Translate concepts by MEANING and CONTEXT, never word-for-word.
- Example: 'placas de grama' (Portuguese for sod/turf) must NEVER become 'pavers' (sidewalk).
- Return ONLY a JSON array with objects: {{"id": int, "keywords": [string], "scene_type": string, "media_preference": "video"|"photo"}}

Scenes:
{cenas_text}

Return ONLY valid JSON, no other text."""

    return prompt


def gerar_storyboard(project_name: str, usar_claude: bool = True) -> dict:
    """
    Gera storyboard para todas as cenas.
    Camada 2 (Claude batch) se disponível, senão camada 1 (fallback local).
    """
    from services.event_logger import log_event
    log_event("STORYBOARD", f"Iniciando geracao de storyboard para {project_name} (Claude={usar_claude})", level="info")
    project_dir = PROJETOS_DIR / project_name
    cenas_file = project_dir / "cenas.json"
    storyboard_file = project_dir / "storyboard.json"

    if not cenas_file.exists():
        return {"success": False, "error": "cenas.json não encontrado"}

    with open(cenas_file, "r", encoding="utf-8") as f:
        cenas = json.load(f)

    if not cenas:
        return {"success": False, "error": "Nenhuma cena encontrada"}

    # Tenta camada 2 (Claude batch)
    if usar_claude and OPENAI_API_KEY:
        try:
            return _gerar_com_claude(project_name, cenas, storyboard_file)
        except Exception:
            # Fallback para camada 1
            pass

    # Camada 1 (fallback local)
    return _gerar_local(project_name, cenas, storyboard_file)


def _gerar_local(project_name: str, cenas: list, storyboard_file: Path) -> dict:
    """Camada 1: regras locais."""
    storyboard = []
    for cena in cenas:
        texto = cena.get("texto", "")
        keywords = _extract_keywords_local(texto)

        # Detecta scene_type
        scene_type = "explicacao"
        texto_lower = texto.lower()
        for stype, skeywords in SCENE_TYPE_KEYWORDS.items():
            if any(kw in texto_lower for kw in skeywords):
                scene_type = stype
                break

        # Preferência de mídia
        media_preference = "video"
        if scene_type in ("comparacao", "conclusao"):
            media_preference = "photo"

        storyboard.append({
            "id": cena["id"],
            "texto": texto,
            "keywords": keywords if keywords else [f"{scene_type}_scene"],
            "scene_type": scene_type,
            "media_preference": media_preference
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


def _gerar_com_claude(project_name: str, cenas: list, storyboard_file: Path) -> dict:
    """Camada 2: Claude batch para todas as cenas."""
    import requests

    prompt = _build_batch_prompt(cenas)

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    # Compatível com APIs compatíveis com OpenAI (Claude via Anthropic API ou similar)
    data = {
        "model": "claude-3-sonnet-20241022",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}]
    }

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=headers,
        json=data,
        timeout=120
    )
    response.raise_for_status()
    result = response.json()

    # Extrai JSON da resposta
    content = result["content"][0]["text"]
    # Procura JSON array na resposta
    json_match = re.search(r'\[\s*\{.*\}\s*\]', content, re.DOTALL)
    if not json_match:
        raise ValueError("Resposta do Claude não contém JSON válido")

    claude_data = json.loads(json_match.group())

    # Mapeia resultados do Claude para as cenas
    claude_map = {item["id"]: item for item in claude_data}

    storyboard = []
    for cena in cenas:
        cid = cena["id"]
        if cid in claude_map:
            item = claude_map[cid]
            storyboard.append({
                "id": cid,
                "texto": cena.get("texto", ""),
                "keywords": item.get("keywords", []),
                "scene_type": item.get("scene_type", "explicacao"),
                "media_preference": item.get("media_preference", "video")
            })
        else:
            # Fallback para cena não processada
            storyboard.append({
                "id": cid,
                "texto": cena.get("texto", ""),
                "keywords": [],
                "scene_type": "explicacao",
                "media_preference": "video"
            })

    with open(storyboard_file, "w", encoding="utf-8") as f:
        json.dump(storyboard, f, indent=2, ensure_ascii=False)

    return {
        "success": True,
        "project": project_name,
        "camada": "claude",
        "cenas_count": len(storyboard),
        "storyboard": storyboard
    }