"""
services/animation_director_service.py — Animation Director AI (FASE 6)
========================================================================
Responsabilidade:
- Decidir com precisão quais cenas merecem movimento cinematográfico (Veo 3.1 / Flow Video).
- Regras de Decisão:
  * ANIMAR (should_animate = True):
    - avatar_talking: Apresentador falando (fala natural, micro-expressões, slow push-in).
    - avatar_action / broll_action: Ação física (mãos trabalhando, adubação, corte, rega).
    - hybrid: Apresentador demonstrando prop em primeiro plano.
    - before_after / proof: Revelação de transformação / florescimento dinâmico.
  * NÃO ANIMAR (should_animate = False):
    - comparison: Comparações estáticas lado a lado.
    - broll_macro estático: Detalhes finos de textura sem ação mecânica ou fluxo.
- Retorno:
  {
    "should_animate": bool,
    "animation_type": str,
    "prompt_animacao": str,
    "animation_priority": "high" | "medium" | "low" | "none",
    "motion_vector": str,
    "animation_rationale": str
  }
"""

from typing import Dict, Any, Optional
from services.event_logger import log_event


def direcionar_animacao_cena(
    cena: Dict[str, Any],
    contexto_visual: Optional[Dict[str, Any]] = None,
    index: int = 0
) -> Dict[str, Any]:
    """
    Analisa a tipologia e o propósito narrativo da cena para definir se ela deve ser animada.
    """
    stype = cena.get("scene_type", "broll_macro")
    story_role = cena.get("story_role", "explanation")
    uses_char = bool(cena.get("uses_character", False))
    duracao = float(cena.get("duracao", 4.0))
    fala = f"{cena.get('narration', '')} {cena.get('texto', '')}".lower()

    # 1. Cenas que NUNCA devem ser animadas (estáticas)
    if stype == "comparison":
        return {
            "should_animate": False,
            "animation_type": "static_freeze",
            "prompt_animacao": "",
            "animation_priority": "none",
            "motion_vector": "static",
            "animation_rationale": "Comparações técnicas lado a lado exigem estabilidade estática para leitura visual clara."
        }

    # 2. Apresentador Falando (avatar_talking / cta)
    if uses_char and stype in ("avatar_talking", "cta"):
        return {
            "should_animate": True,
            "animation_type": "presenter_speech",
            "prompt_animacao": "Smooth cinematic slow push-in, subtle natural head gestures and authentic presenter micro-movements, 4s gentle ease",
            "animation_priority": "high" if story_role in ("hook", "cta") else "medium",
            "motion_vector": "slow_dolly_push",
            "animation_rationale": "Apresentador em diálogo direto com a câmera exige movimento natural de fala e aproximação suave."
        }

    # 3. Ação Física com Personagem ou Mãos (avatar_action / broll_action / hybrid)
    if stype in ("avatar_action", "broll_action", "hybrid"):
        return {
            "should_animate": True,
            "animation_type": "kinetic_action",
            "prompt_animacao": "Cinematic fluid camera drift, capturing organic hands-on gardening motion, water settling and dust particles floating",
            "animation_priority": "high",
            "motion_vector": "subtle_handheld_orbit",
            "animation_rationale": "Cenas de ação prática ganham retenção com movimento fluido de manuseio botânico."
        }

    # 4. Transformação / Prova (before_after / proof)
    if stype == "before_after" or story_role in ("proof", "result"):
        return {
            "should_animate": True,
            "animation_type": "transformation_reveal",
            "prompt_animacao": "Slow cinematic slide reveal, subtle glistening sunlight reflection across blooming petals, 4s fluid transition",
            "animation_priority": "high",
            "motion_vector": "horizontal_glide_reveal",
            "animation_rationale": "Revelação de transformação botânica exige dinamismo para destacar a vitalidade conquistada."
        }

    # 5. B-Roll Macro Dinâmico (somente se houver menção a água, gotas, vento ou movimento)
    if any(k in fala for k in ["água", "gota", "gotas", "orvalho", "vento", "escorrendo", "splashing", "flowing"]):
        return {
            "should_animate": True,
            "animation_type": "ambient_fluid_motion",
            "prompt_animacao": "High-speed macro capture of water droplets falling and shimmering on green leaf surface, shallow depth of field",
            "animation_priority": "medium",
            "motion_vector": "micro_dolly_slide",
            "animation_rationale": "Gotas e umidade em macro possuem apelo visual cinético de alta retenção."
        }

    # 6. B-Roll Macro Estático / Textura Pura (Não animar)
    return {
        "should_animate": False,
        "animation_type": "static_macro",
        "prompt_animacao": "",
        "animation_priority": "none",
        "motion_vector": "static",
        "animation_rationale": "Close-up estático de textura botânica preserva nitidez máxima sem necessidade de vídeo."
    }
