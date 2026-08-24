"""
services/storyboard_director_service.py — Storyboard Director AI
================================================================
Responsabilidade:
- Analisar a transcrição completa e o contexto visual antes de criar/finalizar cenas.
- Decidir para cada cena:
  * story_role: Função narrativa canônica na estrutura dramática
    (hook, problem, discovery, explanation, demonstration, process, comparison, result, proof, cta).
  * narrative_purpose: Justificativa estratégica do porquê a cena existe.
  * retention_goal: Nível de retenção almejado (very_high, high, medium, low).
  * previous_scene_connection: Ponte narrativa explicando a cena anterior.
  * next_scene_connection: Ponte narrativa guiando a atenção para a próxima cena.
- Regras de Ritmo Narrativo:
  Garante sequência fluida: hook -> problem -> discovery -> process/demonstration -> result/proof -> cta.
"""

from typing import List, Dict, Any, Optional
from services.event_logger import log_event


MAPA_ROLES_KEYWORDS = [
    ("cta", ["inscreva-se", "curta", "like", "compartilhe", "comente", "subscribe", "canal", "até a próxima", "tchau", "like this video", "leave a like", "share", "comment below", "see you next time", "hit subscribe"]),
    ("proof", ["olha a diferença", "transformação", "veja o resultado", "prova", "antes e depois", "before and after", "look at the difference", "the result", "final result", "see the difference"]),
    ("result", ["resultado", "florescendo", "vitalidade", "maravilhoso", "recuperou", "lindo", "thriving", "blooming", "recovered", "healthy", "lush"]),
    ("comparison", ["comparado", "versus", "ao contrário", "diferente de", "vs", "compared", "unlike", "as opposed to", "different from", "in contrast to"]),
    ("process", ["comece aplicando", "passo a passo", "misture", "aplique", "corte", "rego", "preparo", "step by step", "how to make", "how to prepare", "mix", "apply", "pour", "cut", "soak", "loosen"]),
    ("demonstration", ["aqui eu mostro", "estou mostrando", "olha como eu", "segurando", "na prática", "demonstrando", "walk you through", "show you", "let me show", "holding", "watch this", "show you exactly", "here is how"]),
    ("discovery", ["descobri", "segredo", "o que funcionou", "solução", "eureka", "encontrei", "revelação", "discovered", "secret", "what worked", "solution", "found out", "breakthrough"]),
    ("problem", ["erro", "cuidado", "perigo", "folhas amareladas", "sufocadas", "morrendo", "danificada", "seco", "compacto", "mistake", "danger", "warning", "yellow leaves", "dying", "damaged", "dry", "struggling", "yanked", "thrown away"]),
    ("explanation", ["porque", "funciona", "motivo", "ciência", "nutrientes", "absorvem", "entenda", "because", "how it works", "reason", "science", "nutrients", "absorb", "understand"]),
]


def determinar_story_role(
    cena: Dict[str, Any],
    index: int = 0,
    total_cenas: int = 1
) -> str:
    """Classifica a função dramática/narrativa da cena."""
    fala = f"{cena.get('narration', '')} {cena.get('texto', '')} {cena.get('text', '')}".lower()
    
    # 1. Hook garantido na primeira cena ou abertura intrigante
    if index == 0:
        return "hook"
    
    # 2. CTA garantido no encerramento com termos de chamada
    if index == total_cenas - 1 and any(k in fala for k in ["inscreva", "canal", "comente", "like", "próxima"]):
        return "cta"
    
    # 3. Varredura semântica por palavras-chave
    for role, termos in MAPA_ROLES_KEYWORDS:
        if any(t in fala for t in termos):
            return role
            
    # 4. Fallbacks inteligentes baseados no scene_type
    stype = cena.get("scene_type", "")
    if stype == "avatar_talking":
        return "explanation" if index < total_cenas // 2 else "result"
    elif stype == "hybrid" or stype == "avatar_action":
        return "demonstration"
    elif stype == "broll_action":
        return "process"
    elif stype == "before_after":
        return "proof"
    elif stype == "broll_macro":
        return "discovery" if index <= 2 else "process"
    elif stype == "environment":
        return "result" if index > total_cenas // 2 else "explanation"
    
    return "explanation"


def determinar_narrative_purpose(story_role: str, cena: Dict[str, Any], index: int = 0) -> str:
    """Gera a justificativa dramática estratégica de por que a cena existe."""
    mapa_purpose = {
        "hook": "Create magnetic curiosity and stop the scroll in the opening seconds",
        "problem": "Show the initial pain point and reveal the core botanical frustration",
        "discovery": "Introduce the breakthrough organic solution and spark insight",
        "explanation": "Provide authoritative scientific context and explain the mechanism",
        "demonstration": "Demonstrate the hands-on technique clearly with presenter authenticity",
        "process": "Guide the viewer step-by-step through the precise physical execution",
        "comparison": "Highlight the tangible visual contrast between treated and untreated elements",
        "result": "Reward viewer retention with the flourishing, vibrant visual outcome",
        "proof": "Provide undeniable empirical evidence of the method's effectiveness",
        "cta": "Drive audience engagement, channel subscription, and final call-to-action"
    }
    return mapa_purpose.get(story_role, "Progress the visual narrative with clarity and aesthetic engagement")


def determinar_retention_goal(story_role: str, index: int = 0, total_cenas: int = 1) -> str:
    """Atribui o nível de retenção estratégica almejado para a cena."""
    if story_role in ("hook", "proof") or index == 0:
        return "very_high"
    elif story_role in ("problem", "discovery", "result", "cta"):
        return "high"
    elif story_role in ("demonstration", "process", "comparison"):
        return "medium"
    return "low"


def construir_conexoes_narrativas(
    cenas: List[Dict[str, Any]],
    contexto_visual: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Constrói as pontes narrativas inter-cenas (previous_scene_connection e next_scene_connection).
    """
    total = len(cenas)
    for i, c in enumerate(cenas):
        role = c.get("story_role", "explanation")
        
        # Conexão com cena anterior
        if i == 0:
            prev_conn = "Narrative opening: viewer is introduced to the core hook"
        else:
            prev_cena = cenas[i - 1]
            prev_role = prev_cena.get("story_role", "previous scene")
            prev_conn = f"Transitioning from {prev_role}: viewer was guided through {prev_cena.get('narrative_purpose', 'previous step')}"
        
        # Conexão com próxima cena
        if i == total - 1:
            next_conn = "Narrative finale: video concludes and invites viewer action"
        else:
            next_cena = cenas[i + 1]
            next_role = next_cena.get("story_role", "next scene")
            next_conn = f"Bridging to {next_role}: viewer attention is directed toward {next_cena.get('narrative_purpose', 'upcoming insight')}"
        
        c["previous_scene_connection"] = prev_conn
        c["next_scene_connection"] = next_conn

    return cenas


def analisar_storyboard_narrativo(
    projeto_id: str,
    cenas: List[Dict[str, Any]],
    contexto_visual: Optional[Dict[str, Any]] = None,
    memoria_visual: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    STORYBOARD DIRECTOR AI:
    Audita todo o roteiro e atribui story_role, narrative_purpose, retention_goal
    e conexões de continuidade entre todas as cenas.
    """
    total = len(cenas)
    
    # 1. Atribui funções narrativas individuais
    for idx, c in enumerate(cenas):
        role = determinar_story_role(c, index=idx, total_cenas=total)
        purpose = determinar_narrative_purpose(role, c, index=idx)
        retention = determinar_retention_goal(role, index=idx, total_cenas=total)
        
        c["story_role"] = role
        c["narrative_purpose"] = purpose
        c["retention_goal"] = retention

    # 2. Constrói pontes e conexões entre cenas
    cenas = construir_conexoes_narrativas(cenas, contexto_visual)

    log_event("STORYBOARD_DIRECTOR", f"{projeto_id}: Storyboard narrativo estruturado com {total} cenas.")
    return cenas
