"""
services/enhanced_scene_classifier.py — Enhanced Narrative Scene Classifier (Lira Studio Fase 1)
================================================================================================
Classificação NARRATIVA de cada cena (não só técnica), complementando o
scene_classifier_service.py (que mantém o scene_type técnico):

  narrative_role: HOOK | AVATAR | BROLL | CTA | CLOSING

Comporta textos em PT e EN (os projetos reais do Lira Studio têm SRT em inglês).
Regras 100% determinísticas (sem LLM): zero custo, zero latência e totalmente
testável. Não substitui nada — é ADITIVO ao fluxo existente.
"""

import re
from typing import Dict, Any, Optional

NARRATIVE_ROLES = ("HOOK", "AVATAR", "BROLL", "CTA", "CLOSING", "TRANSITION")

# Termos e frases de transição narrativa estratégica (momentos-chave para reengajamento do avatar)
_FRASES_TRANSICAO = [
    "agora vamos", "o próximo passo", "o proximo passo", "mas preste atenção",
    "mas preste atencao", "por outro lado", "no entanto", "agora que você",
    "agora que voce", "passando para", "o grande segredo", "o segredo é",
    "o segredo e", "veja a diferença", "veja a diferenca", "um ponto importante",
    "mudando de assunto", "vamos ao que interessa", "agora preste atenção",
    "now let's", "next up", "moving on to", "the next step", "here is the secret",
    "pay close attention", "on the other hand", "however", "now that you saw",
    "here is what you need", "the key takeaway", "turning point", "let's dive into"
]

# ---------------------------------------------------------------------------
# Tabelas de palavras-chave (PT + EN)
# ---------------------------------------------------------------------------

# Ação concreta -> verbo canônico (inglês, usado em action_verb)
_VERBOS_ACAO = {
    # === Português ===
    "cortar": "cutting", "cortando": "cutting",
    "plantar": "planting", "plantando": "planting",
    "aplicar": "applying", "aplicando": "applying",
    "misturar": "mixing", "misturando": "mixing",
    "regar": "watering", "regando": "watering",
    "cavar": "digging", "cavando": "digging",
    "podar": "pruning", "semear": "sowing",
    "adubar": "applying", "puxar": "pulling", "arrancar": "pulling",
    "remover": "removing", "removendo": "removing",
    # === Inglês ===
    "cut": "cutting", "cutting": "cutting",
    "planting": "planting", "applying": "applying", "mixing": "mixing",
    "watering": "watering", "digging": "digging", "pulling": "pulling",
    "removing": "removing", "pruning": "pruning", "sowing": "sowing",
    "spreading": "spreading", "pouring": "pouring", "loosening": "loosening",
    "trimming": "trimming", "harvesting": "harvesting",
    "doing": "doing", "making": "making",
}

# Termos de CTA (clique/compre/acesse/inscreva)
_PALAVRAS_CTA = [
    "clique", "clica", "compre", "comprar", "acesse", "acessar", "inscreva",
    "compartilhe", "comente", "siga",
    "click", "buy", "purchase", "access", "subscribe", "share", "comment",
    "follow", "visit", "sign",
]
_FRASES_CTA = [
    "inscreva-se", "se inscreva", "se inscrever", "curta o vídeo",
    "curta o video", "curtir o vídeo", "deixe seu like", "link na descrição",
    "link na descricao", "link in the description", "link in description",
    "check out", "like this video", "leave a like",
]

# Termos de encerramento
_PALAVRAS_FIM = ["adeus", "tchau", "valeu", "bye", "goodbye"]
_FRASES_FIM = [
    "próximo vídeo", "próximo video", "no próximo", "até a próxima",
    "ate a proxima", "é isso por", "é só isso", "encerrando", "encerro",
    "obrigado por assistir", "obrigada por assistir", "nos vemos",
    "vejo vocês", "vejo voces",
    "next video", "next time", "see you next", "see you soon",
    "see you guys", "see you later", "see you in the next",
    "thanks for watching", "thank you for watching", "that's it for today",
    "that's all for today", "until next time", "catch you later",
    "wrapping up", "wrap up", "that's all folks", "é isso pessoal",
]

# Frases explícitas de abertura / gancho
_PALAVRAS_HOOK = ["olá", "oi", "sou", "hello", "hi", "welcome"]
_FRASES_HOOK = [
    "eu sou", "meu nome é", "seja bem-vindo", "sejam bem-vindos",
    "bem-vindo", "bem-vindos", "bem vindos", "você sabia", "voce sabia",
    "sabia que", "eu descobri", "descobri um", "quero te mostrar",
    "vou te mostrar",
    "you won't believe", "i'm going to show", "i will show",
    "let me show you", "in this video", "today i", "i discovered",
    "did you know", "guess what", "welcome back", "my name is",
    "i am", "this is how i", "how i",
]

# Termos de b-roll (natureza/ambiente, sem sujeito)
_PALAVRAS_BROLL = [
    "grama", "gramado", "verde", "flor", "folha", "raiz", "solo", "terra",
    "jardim", "paisagem", "ambiente", "planta", "pétala", "caule", "casca",
    "roseira", "árvore", "natureza",
    "grass", "green", "flower", "leaf", "root", "soil", "ground", "garden",
    "landscape", "environment", "plant", "petal", "stem", "bark", "bush",
    "tree", "nature", "bloom", "bud", "seed", "rose", "dirt", "lawn",
    "foliage", "branch", "trunk", "vegetation",
]

# Marcadores de sujeito (primeira pessoa / apresentador)
_PALAVRAS_SUJEITO = [
    "eu", "sou", "vou", "vamos", "meu", "minha", "meus", "minhas", "estou",
    "nosso", "nossa", "i", "my", "me", "mine", "we", "our", "us", "let",
]
_FRASES_SUJEITO = ["let me", "i'm", "i'll"]

# Palavras emocionais para calcular intensidade
_EMOCOES_POSITIVAS = [
    "incrível", "incrivel", "surpreendente", "amei", "adorei", "fantástico",
    "fantastico", "maravilhoso", "perfeito", "impressionante", "espetacular",
    "lindo", "linda", "beautiful", "amazing", "incredible", "fantastic",
    "wonderful", "perfect", "stunning", "spectacular", "gorgeous", "love",
    "great", "wow", "unbelievable", "brilliant",
]
_EMOCOES_NEGATIVAS = [
    "ruim", "péssimo", "pessimo", "horrível", "horrivel", "triste",
    "frustrante", "seca", "morto", "morta", "queimada", "queimado",
    "horrible", "terrible", "sad", "frustrating", "dead", "dying", "burned",
    "burnt", "ugly", "bad", "problem", "destroyed", "wilting", "drooping",
    "yellow",
]

_ROLE_EMOTION = {
    "HOOK": "curious", "AVATAR": "engaging", "TRANSITION": "engaging",
    "BROLL": "calm", "CTA": "persuasive", "CLOSING": "warm",
}
_ROLE_DEFAULT_ACTION = {
    "HOOK": "greeting", "AVATAR": "presenting", "TRANSITION": "transitioning",
    "BROLL": "showing", "CTA": "calling", "CLOSING": "wrapping",
}
_ROLE_BASE_INTENSITY = {
    "HOOK": 0.8, "AVATAR": 0.6, "TRANSITION": 0.6, "BROLL": 0.4, "CTA": 0.7, "CLOSING": 0.4,
}
_DURATION_RECOMMENDED = {
    "HOOK": 5.0, "AVATAR": 6.0, "TRANSITION": 4.5, "BROLL": 5.0, "CTA": 4.0, "CLOSING": 3.0,
}

# Substantivos em inglês priorizados para montar a query do Pexels
_BROLL_NOUNS_EN = [
    "roses", "rose", "flower", "leaf", "root", "soil", "grass", "garden",
    "plant", "petal", "stem", "bark", "tree", "branch", "bloom", "bud",
    "seed", "ground", "lawn", "vegetation", "foliage", "water", "dirt",
    "compost", "fern", "moss", "dandelion",
]
_PT_EN_QUERY = {
    "grama": "grass", "gramado": "lawn", "verde": "green", "flor": "flower",
    "folha": "leaf", "raiz": "root", "solo": "soil", "terra": "soil",
    "jardim": "garden", "paisagem": "landscape", "ambiente": "garden",
    "planta": "plant", "pétala": "petal", "caule": "stem", "casca": "bark",
    "roseira": "rose bush", "árvore": "tree", "natureza": "nature",
    "rosa": "rose", "semente": "seed",
}


# ---------------------------------------------------------------------------
# Helpers de matching
# ---------------------------------------------------------------------------

def _contem_palavra(texto: str, token: str) -> bool:
    """Word-boundary match — evita falso positivo em prefixos ("high" vs "hi")."""
    return bool(re.search(r"\b" + re.escape(token) + r"\w*", texto))


def _tem_sujeito(texto: str) -> bool:
    tl = texto.lower()
    for frase in _FRASES_SUJEITO:
        if frase in tl:
            return True
    for palavra in _PALAVRAS_SUJEITO:
        if _contem_palavra(tl, palavra):
            return True
    return False


def _tem_acao(texto: str) -> bool:
    tl = texto.lower()
    return any(_contem_palavra(tl, t) for t in _VERBOS_ACAO)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def get_narrative_role(scene_text: str, scene_index: Optional[int] = None) -> str:
    """Retorna HOOK | TRANSITION | CTA | CLOSING | BROLL.

    Regra Estratégica:
    - CTA: menção explícita a chamada para ação / link / inscrição
    - CLOSING: encerramento / despedida / próximo vídeo
    - HOOK: primeira cena (scene_index <= 1) ou frases explícitas de abertura
    - TRANSITION: marcadores de transição de bloco narrativo
    - BROLL: default para todas as cenas intermediárias (explicação, ação, detalhes)
    """
    tl = (scene_text or "").lower().strip()
    if not tl:
        return "BROLL"

    if any(k in tl for k in _FRASES_CTA) or any(_contem_palavra(tl, w) for w in _PALAVRAS_CTA):
        return "CTA"
    if any(k in tl for k in _FRASES_FIM) or any(_contem_palavra(tl, w) for w in _PALAVRAS_FIM):
        return "CLOSING"
    if scene_index is not None and int(scene_index) <= 1:
        return "HOOK"
    if any(k in tl for k in _FRASES_HOOK) or any(_contem_palavra(tl, w) for w in _PALAVRAS_HOOK):
        return "HOOK"
    if any(t in tl for t in _FRASES_TRANSICAO):
        return "TRANSITION"

    # Todas as demais cenas focadas em B-roll de cobertura visual
    return "BROLL"


def estimate_duration(narrative_role: str) -> float:
    """Segundos sugeridos por papel narrativo (Hook 3-5s, Avatar 5-15s, etc.)."""
    return _DURATION_RECOMMENDED.get((narrative_role or "").upper(), 5.0)


def get_action_verb(scene_text: str, narrative_role: str = "AVATAR") -> str:
    """Ação concreta encontrada no texto; fallback por papel narrativo."""
    role = (narrative_role or "").upper()
    default = _ROLE_DEFAULT_ACTION.get(role, "showing")
    tl = (scene_text or "").lower()
    for token in _VERBOS_ACAO:
        if _contem_palavra(tl, token):
            return _VERBOS_ACAO[token]
    return default


def estimate_intensity(scene_text: str, narrative_role: str = "AVATAR") -> float:
    """Intensidade emocional 0-1 (heurística)."""
    role = (narrative_role or "").upper()
    base = _ROLE_BASE_INTENSITY.get(role, 0.5)
    tl = (scene_text or "").lower()
    if any(e in tl for e in _EMOCOES_POSITIVAS) or any(e in tl for e in _EMOCOES_NEGATIVAS):
        base += 0.15
    if "!" in (scene_text or "") or "?" in (scene_text or ""):
        base += 0.1
    if _tem_acao(tl):
        base += 0.1
    return round(max(0.0, min(1.0, base)), 2)


def get_emotion(scene_text: str, narrative_role: str = "AVATAR") -> str:
    """Emoção narrativa derivada (papel + polaridade de palavras)."""
    role = (narrative_role or "").upper()
    base = _ROLE_EMOTION.get(role, "neutral")
    tl = (scene_text or "").lower()
    pos = any(e in tl for e in _EMOCOES_POSITIVAS)
    neg = any(e in tl for e in _EMOCOES_NEGATIVAS)
    if pos and not neg:
        return {"HOOK": "excited", "AVATAR": "enthusiastic", "TRANSITION": "enthusiastic",
                "BROLL": "peaceful", "CTA": "excited", "CLOSING": "grateful"}.get(role, "engaged")
    if neg:
        return {"HOOK": "worried", "AVATAR": "concerned", "TRANSITION": "focused",
                "BROLL": "somber", "CTA": "urgent", "CLOSING": "tired"}.get(role, "concerned")
    return base


def _build_query(texto: str, scene_type: str = "") -> str:
    """Monta uma query para o Pexels a partir de um texto BROLL."""
    tl = (texto or "").lower()
    termos: list = []
    for n in _BROLL_NOUNS_EN:
        if n in termos:
            continue
        if _contem_palavra(tl, n):
            termos.append(n)
    if not termos:
        for pt, en in _PT_EN_QUERY.items():
            if pt in tl and en not in termos:
                termos.append(en)
    st = (scene_type or "").lower()
    if st == "broll_macro" and "close" not in " ".join(termos):
        termos.append("close up")
    termos = termos[:3]
    if not termos:
        termos = ["garden", "nature", "macro"] if st == "broll_macro" else ["garden", "nature"]
    return " ".join(termos)


def get_broll_query(scene_text: str, scene_type: str = "") -> Optional[str]:
    """Se a cena é BROLL retorna a query Pexels; caso contrário retorna None."""
    if get_narrative_role(scene_text) != "BROLL":
        return None
    return _build_query(scene_text, scene_type)


def _timestamp_para_segundos(ts) -> Optional[float]:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    s = str(ts).strip().replace("[", "").replace("]", "").replace(",", ".")
    if not s:
        return None
    m = re.match(r"(\d{1,2}):(\d{2})", s)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    m = re.match(r"(\d{1,2}):(\d{2}):(\d{2})", s)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return None


def _tem_sujeito_apresentador(texto: str) -> bool:
    """True se o texto tem sujeito em 1ª pessoa (apresentador falando)."""
    tl = texto.lower()
    return any((" " + p + " ") in (" " + tl + " ") for p in (
        "eu", "meu", "minha", "meus", "minhas", "estou", "vou", "vamos",
        "i", "i'm", "i am", "my", "myself", "i'll", "i will", "i've", "we", "our", "us",
    ))


def _ajustar_por_scene_type(role: str, texto: str, scene_type: str) -> str:
    """Refina o papel usando o scene_type técnico já conhecido do plano.

    Lira Studio v0.2.0 (Frente 1): avatar deixa de depender de léxico de transição.
      - avatar_action = demonstração do apresentador (sujeito/verbo de ação) -> AVATAR
      - avatar_talking = fala do apresentador -> AVATAR em pontos estratégicos
      - demais casos -> BROLL (cobertura visual), evitando saturação de avatar.
    """
    st = (scene_type or "").strip().lower()
    if role in ("HOOK", "CTA", "CLOSING", "TRANSITION"):
        return role
    if st.startswith("broll_") or st in ("environment", "before_after", "comparison", "hybrid"):
        return "BROLL"
    if st.startswith("avatar_"):
        if any(t in texto.lower() for t in _FRASES_TRANSICAO):
            return "TRANSITION"
        if st == "avatar_action" and (_tem_sujeito_apresentador(texto) or role == "AVATAR"):
            return "AVATAR"
        if st == "avatar_talking" and _tem_sujeito_apresentador(texto):
            return "AVATAR"
        return "BROLL"
    return role


def _requires_avatar_for(role: str) -> bool:
    """True apenas para pontos estratégicos (HOOK, TRANSITION, CTA, CLOSING)."""
    return (role or "").upper() in ("HOOK", "CTA", "CLOSING", "TRANSITION", "AVATAR")


def classify_scene(
    scene_text: str,
    scene_type: str = "",
    timestamp: str = "",
    scene_index: Optional[int] = None,
    tempo_inicio_secs: Optional[float] = None,
    is_first: Optional[bool] = None,
) -> Dict[str, Any]:
    """Classificação narrativa completa de uma cena com decisão estratégica de avatar.

    Regras Estratégicas:
      1. primeira cena OU tempo < 5s                -> HOOK (avatar)
      2. menção a CTA (clique/compre/inscreva)      -> CTA (avatar)
      3. encerramento (próximo vídeo/valeu/adeus)   -> CLOSING (avatar)
      4. frases de gancho (olá/eu sou/... )         -> HOOK (avatar)
      5. frases de transição narrativa              -> TRANSITION (avatar)
      6. restante das cenas (explicação/ação/demo)  -> BROLL (broll visual de cobertura)
    """
    texto = str(scene_text or "").strip()
    if tempo_inicio_secs is None:
        tempo_inicio_secs = _timestamp_para_segundos(timestamp)

    primeiro = bool(is_first) if is_first is not None else (
        scene_index is not None and int(scene_index) <= 1)

    if primeiro:
        role = "HOOK"
    elif tempo_inicio_secs is not None and float(tempo_inicio_secs) < 5.0:
        role = "HOOK"
    else:
        role = get_narrative_role(texto, scene_index=scene_index)

    role = _ajustar_por_scene_type(role, texto, scene_type)

    broll_query = get_broll_query(texto, scene_type) if role == "BROLL" else None

    return {
        "scene_index": scene_index if scene_index is not None else 0,
        "scene_type": str(scene_type or ""),
        "narrative_role": role,
        "intensity": estimate_intensity(texto, role),
        "action_verb": get_action_verb(texto, role),
        "emotion": get_emotion(texto, role),
        "recommended_duration": estimate_duration(role),
        "requires_avatar": _requires_avatar_for(role),
        "broll_query": broll_query,
    }