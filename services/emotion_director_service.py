"""
services/emotion_director_service.py — Emotion & Retention Director
===================================================================
Responsabilidade:
- Definir a intenção emocional e a energia de cada cena para máxima retenção de audiência:
  * Hook / Abertura: Alta energia, curiosidade, magnetismo.
  * Problema / Erro: Tensão dramática, atenção imediata, urgência.
  * Explicação / Conteúdo: Confiança, clareza, autoridade, energia média equilibrada.
  * Demonstração Prática: Foco sensorial, engajamento, precisão.
  * Revelação / Resultado: Satisfação, surpresa positiva, encantamento visual.
  * Chamada para Ação (CTA): Empoderamento, incentivo, conclusão calorosa.
"""

from typing import Dict, Any, Optional
from services.event_logger import log_event


def direcionar_emocao(
    cena: Dict[str, Any],
    scene_type: str,
    index: int = 0,
    total_cenas: int = 1,
    contexto_visual: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Computa a emoção, o nível de energia e a iluminação atmosférica para a cena.
    """
    fala = f"{cena.get('narration', '')} {cena.get('texto', '')} {cena.get('text', '')}".lower()

    # 1. Hook (Cena inicial ou introdução impactante)
    if index == 0 or any(k in fala for k in ["segredo", "nunca faça", "você sabia", "o maior erro", "descobri"]):
        emotion = "curiosity"
        energy = "high"
        role = "hook"
        lighting = "dramatic morning sunlight with directional contrast"

    # 2. Problema / Tensão
    elif any(k in fala for k in ["erro", "cuidado", "perigo", "estragar", "morrendo", "danificada", "errado"]):
        emotion = "urgency"
        energy = "high"
        role = "problem_reveal"
        lighting = "moody focused lighting with deep natural shadows"

    # 3. Explicação / Confiança
    elif scene_type in ("avatar_talking", "environment") or any(k in fala for k in ["porque", "funciona", "motivo", "estudo", "ciência", "entenda"]):
        emotion = "trust"
        energy = "medium"
        role = "explanation"
        lighting = "soft diffused natural light, balanced and clean"

    # 4. Demonstração Prática / Foco
    elif scene_type in ("avatar_action", "broll_action", "hybrid") or any(k in fala for k in ["aplico", "coloco", "como fazer", "passo", "misturo", "mostro"]):
        emotion = "focus"
        energy = "medium"
        role = "practical_demonstration"
        lighting = "crisp high-clarity daylight emphasizing textures"

    # 5. Resultado / Revelação de Sucesso
    elif scene_type == "before_after" or any(k in fala for k in ["resultado", "lindo", "florescendo", "transformação", "veja como", "olha só"]):
        emotion = "satisfaction"
        energy = "high"
        role = "proof_and_payoff"
        lighting = "golden hour radiant sunlight, warm glowing highlights"

    # 6. CTA / Encerramento
    elif scene_type == "cta" or index == total_cenas - 1:
        emotion = "empowerment"
        energy = "high"
        role = "call_to_action"
        lighting = "bright inviting daylight with warm friendly tones"

    # 7. Fallback neutro cinematográfico
    else:
        emotion = "discovery"
        energy = "medium"
        role = "narrative_progression"
        lighting = "natural atmospheric sunlight with soft bokeh"

    result = {
        "emotion": emotion,
        "energy": energy,
        "visual_role": role,
        "lighting_mood": lighting
    }

    log_event("EMOTION_DIRECTOR", f"Cena {cena.get('id', index+1)}: emotion='{emotion}', energy='{energy}', role='{role}'")
    return result
