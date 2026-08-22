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
    """
    fala = f"{cena.get('narration', '')} {cena.get('texto', '')} {cena.get('text', '')}".strip()
    fala_lower = fala.lower()
    duracao = float(cena.get("duracao", 5.0))

    # 1. Checa se é CTA final (por palavras-chave explícitas ou encerramento com apresentador)
    is_explicit_cta = any(k in fala_lower for k in ["inscreva-se", "curta o vídeo", "deixe seu like", "se inscreva", "compartilhe", "link na descrição", "subscribe", "comente abaixo", "até a próxima", "valeu", "tchau", "vejo vocês"])
    if is_explicit_cta or (index == total_cenas - 1 and nome_personagem and any(k in fala_lower for k in ["eu", "meu", "comigo", "próximo", "abraço"])):
        if nome_personagem or any(k in fala_lower for k in ["eu", "me", "meu", "comigo"]):
            stype = "cta"
            uses_char = bool(nome_personagem)
        else:
            stype = "cta"
            uses_char = False
        role = "call_to_action"

    # 2. Checa Avatar Talking (Hook inicial, apresentação pessoal ou fala direta à câmera)
    elif any(k in fala_lower for k in ["olá", "eu sou", "hoje eu vou", "meu nome é", "bem-vindos", "bem vindo", "hello", "today i will", "i am", "descobri um segredo"]) and (nome_personagem or any(k in fala_lower for k in ["eu", "meu", "sou", "olá", "hoje"])):
        stype = "avatar_talking"
        uses_char = True
        role = "hook" if index == 0 else "direct_address"

    # 3. Checa Cena Híbrida (Apresentador segurando/mostrando objeto em primeiro plano)
    elif any(k in fala_lower for k in ["aqui eu mostro", "eu mostro", "estou segurando", "olha como eu", "olhe na minha mão", "vejam comigo", "holding", "showing you"]):
        stype = "hybrid"
        uses_char = True
        role = "practical_demonstration"

    # 4. Checa Avatar Action (Apresentador executando ação no cenário)
    elif any(k in fala_lower for k in ["eu aplico", "eu coloco", "eu misturo", "eu ando", "eu caminho", "eu preparo", "i apply", "i mix", "i prepare"]) and (nome_personagem or any(k in fala_lower for k in ["eu", "meu"])):
        stype = "avatar_action"
        uses_char = True
        role = "active_demonstration"

    # 5. Checa Before / After e Comparison
    elif any(k in fala_lower for k in ["antes e depois", "olha a diferença", "veja a transformação", "resultado final", "before and after", "look at the difference"]):
        stype = "before_after"
        uses_char = False
        role = "proof_and_transformation"

    elif any(k in fala_lower for k in ["comparado com", "diferente de", "ao contrário de", "versus", "vs", "compared to"]):
        stype = "comparison"
        uses_char = False
        role = "comparative_analysis"

    # 6. Checa B-Roll Macro (Super close de textura, raiz, folha sem apresentador)
    elif any(k in fala_lower for k in ["observe essas folhas", "observem essas folhas", "detalhe da", "close na", "textura do", "vejam as pétalas", "microscópico", "as raízes estavam", "as raízes", "close-up macro", "macro shot", "texture of"]):
        stype = "broll_macro"
        uses_char = False
        role = "sensory_detail"

    # 7. Checa B-Roll Action (Mãos ou ação física sem foco no rosto)
    elif any(k in fala_lower for k in ["comece aplicando", "aplicar a adubação", "colocar no solo", "cortar com", "regar a planta", "misturar o adubo", "pouring", "cutting", "watering"]):
        stype = "broll_action"
        uses_char = False
        role = "step_by_step_action"

    # 8. Checa Environment (Plano aberto do local / mundo)
    elif any(k in fala_lower for k in ["nesse ambiente", "aqui no campo", "pelo espaço", "estufa", "laboratório", "cenário", "landscape", "establishing"]):
        if index == 0 or index == 1:
            stype = "environment"
            uses_char = False
            role = "world_establishing"
        else:
            stype = "avatar_talking" if (nome_personagem and any(w in fala_lower for w in ["eu", "meu", "olá"])) else "environment"
            uses_char = bool(stype == "avatar_talking")
            role = "atmosphere"

    # 9. Fallback Inteligente baseado em termos humanos vs objetos
    else:
        from services.character_service import detectar_presenca_personagem_cena
        tem_humano = detectar_presenca_personagem_cena(cena, nome_personagem)
        if tem_humano and nome_personagem:
            stype = "avatar_talking" if index < 2 else "avatar_action"
            uses_char = True
            role = "narrative_subject"
        else:
            stype = "broll_macro" if any(w in fala_lower for w in ["planta", "folha", "flor", "raiz", "terra", "solo", "água"]) else "broll_action"
            uses_char = False
            role = "supporting_visual"

    result = {
        "scene_type": stype,
        "uses_character": uses_char,
        "visual_role": role
    }

    log_event("SCENE_CLASSIFIER", f"Cena {cena.get('id', index+1)}: type={stype}, uses_character={uses_char}, role={role}")
    return result
