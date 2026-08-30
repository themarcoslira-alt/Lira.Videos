"""
services/scene_classifier_service.py — Scene Classifier AI
===========================================================
Responsabilidade:
- Classificar cada bloco de tempo do SRT em uma tipologia visual precisa:
  * avatar_talking: Apresentador falando diretamente para a câmera (Hook / Explicação / Diálogo)
  * avatar_action: Apresentador realizando uma ação física (caminhando, colhendo, operando ferramenta)
  * broll_action: Close nas mãos ou objetos em ação sem mostrar o rosto
  * broll_macro: Super close-up de textura, folha, raiz, solo, gota, detalhe microscópico
  * environment: Plano aberto de ambientação (establishing shot do mundo)
  * before_after: Revelação de transformação / resultado comparativo
  * comparison: Comparação direta entre elementos
  * hybrid: Apresentador segurando ou demonstrando um objeto em primeiro plano
  * cta: Chamada para ação final / encerramento
"""

import re
from typing import Dict, Any, Optional
from services.event_logger import log_event


TIPOS_CENA_VALIDOS = (
    "avatar_talking",
    "avatar_action",
    "broll_action",
    "broll_macro",
    "environment",
    "before_after",
    "comparison",
    "hybrid",
    "cta"
)


def classificar_cena(
    cena: Dict[str, Any],
    contexto_visual: Optional[Dict[str, Any]] = None,
    index: int = 0,
    total_cenas: int = 1,
    nome_personagem: str = ""
) -> Dict[str, Any]:
    """
    Analisa o texto e metadados da cena para determinar o scene_type e se uses_character é True.
    Regra Estratégica:
      - Avatar apenas em pontos estratégicos:
        * Hook inicial (Cena 1 / Abertura)
        * Transições narrativas explícitas
        * CTA / Fechamento final (Última cena)
      - Restante das cenas (explicação, ação, detalhes, macro, ambiente): B-roll de cobertura visual.
    """
    fala = f"{cena.get('narration', '')} {cena.get('texto', '')} {cena.get('text', '')}".strip()
    fala_lower = fala.lower()
    duracao = float(cena.get("duracao", 5.0))

    # Palavras de transição estratégica
    transicao_keywords = [
        "agora vamos", "o próximo passo", "o proximo passo", "mas preste atenção",
        "mas preste atencao", "por outro lado", "no entanto", "agora que você",
        "agora que voce", "passando para", "o grande segredo", "o segredo é",
        "now let's", "next up", "moving on to", "the next step", "here is the secret",
        "pay close attention", "on the other hand", "however", "now that you saw",
        "turning point", "let's dive into"
    ]

    # 1. Checa se é CTA final (por palavras-chave explícitas ou encerramento com apresentador)
    cta_keywords = [
        "inscreva-se", "curta o vídeo", "deixe seu like", "se inscreva", "compartilhe", "link na descrição",
        "subscribe", "comente abaixo", "até a próxima", "valeu", "tchau", "vejo vocês",
        "like this video", "leave a like", "share", "link in description", "comment below",
        "see you next time", "see you guys", "thanks for watching", "hit subscribe", "drop a comment"
    ]
    is_explicit_cta = any(k in fala_lower for k in cta_keywords)
    is_last_scene = (index >= total_cenas - 1 and total_cenas > 1)
    
    if is_explicit_cta or (is_last_scene and any(k in fala_lower for k in ["eu", "meu", "comigo", "próximo", "abraço", "i", "my", "me", "next", "bye", "thanks"])):
        stype = "cta"
        uses_char = bool(nome_personagem)
        role = "call_to_action"

    # 2. Checa Avatar Talking Estratégico (Hook inicial / Abertura)
    elif index == 0 or (index == 1 and any(k in fala_lower for k in [
        "olá", "eu sou", "hoje eu vou", "meu nome é", "bem-vindos", "bem vindo", "descobri um segredo",
        "hello", "hi", "welcome", "welcome back", "today i will", "today i'm going to", "today i am going to",
        "in this video", "hi everyone"
    ])):
        stype = "avatar_talking"
        uses_char = bool(nome_personagem)
        role = "hook"

    # 3. Checa Transição Narrativa Estratégica
    elif any(k in fala_lower for k in transicao_keywords):
        stype = "avatar_talking"
        uses_char = bool(nome_personagem)
        role = "strategic_transition"

    # 4. Checa Before / After e Comparison (B-Roll)
    elif any(k in fala_lower for k in [
        "antes e depois", "olha a diferença", "veja a transformação", "resultado final",
        "before and after", "look at the difference", "the result", "final result", "see the difference", "the transformation"
    ]):
        stype = "before_after"
        uses_char = False
        role = "proof_and_transformation"

    elif any(k in fala_lower for k in [
        "comparado com", "diferente de", "ao contrário de", "versus", "vs", "compared to",
        "unlike", "as opposed to", "different from", "in contrast to"
    ]):
        stype = "comparison"
        uses_char = False
        role = "comparative_analysis"

    # 5. Checa B-Roll Macro (Super close de textura, raiz, folha, elemento visual)
    elif any(k in fala_lower for k in [
        "observe essas folhas", "observem essas folhas", "detalhe da", "close na", "textura do", "vejam as pétalas",
        "microscópico", "as raízes estavam", "as raízes", "close-up macro", "macro shot", "texture of",
        "the root", "the leaves", "the flower", "the stem", "close-up of", "microscopic detail", "texture of the", "botanical detail"
    ]):
        stype = "broll_macro"
        uses_char = False
        role = "sensory_detail"

    # 6. Checa B-Roll Action (Mãos ou ação física com foco nos objetos/processo, sem rosto)
    elif any(k in fala_lower for k in [
        "comece aplicando", "aplicar a adubação", "colocar no solo", "cortar com", "regar a planta", "misturar o adubo",
        "pouring", "cutting", "watering", "yanking a plant", "pulling out", "mixing the", "applying the", "adding water",
        "loosening the soil", "thrown away", "cutting them into", "eu aplico", "eu coloco", "eu misturo", "eu preparo",
        "i apply", "i mix", "i prepare", "i yank", "i pull", "i cut", "i pour", "i take", "i grab", "i loosen", "i water"
    ]):
        stype = "broll_action"
        uses_char = False
        role = "step_by_step_action"

    # 7. Checa Environment (Plano aberto do local / mundo)
    elif any(k in fala_lower for k in [
        "nesse ambiente", "aqui no campo", "pelo espaço", "estufa", "laboratório", "cenário",
        "landscape", "establishing", "in the garden", "in the backyard", "greenhouse", "backyard"
    ]):
        stype = "environment"
        uses_char = False
        role = "world_establishing"

    # 8. Fallback Geral para cenas intermediárias: Foco 100% em B-Roll Visual de Cobertura
    else:
        stype = "broll_macro" if any(w in fala_lower for w in [
            "planta", "folha", "flor", "raiz", "terra", "solo", "água",
            "plant", "leaf", "leaves", "flower", "root", "soil", "stem", "water", "ground", "dandelion", "rose", "banana"
        ]) else "broll_action"
        uses_char = False
        role = "supporting_visual"

    result = {
        "scene_type": stype,
        "uses_character": uses_char,
        "visual_role": role
    }

    log_event("SCENE_CLASSIFIER", f"Cena {cena.get('id', index+1)}: type={stype}, uses_character={uses_char}, role={role}")
    return result
