"""
services/story_rhythm_service.py — Story Rhythm & Visual Pacing Director
========================================================================
Responsabilidade:
- Controlar o ritmo narrativo visual do vídeo para evitar monotonia.
- Regras de Pacing:
  * max_avatar_consecutive = 2 (evitar mais de 2 cabeças falantes seguidas)
  * max_broll_consecutive = 3 (evitar sequências excessivamente longas de B-roll sem sujeito)
- Alternância cinematográfica ideal:
  Avatar (Hook) -> B-Roll (Problema) -> Detalhe Macro -> Híbrido (Demonstração) -> Avatar (Conclusão)
"""

from typing import List, Dict, Any, Optional
from services.event_logger import log_event


MAX_AVATAR_CONSECUTIVE = 2
MAX_BROLL_CONSECUTIVE = 3


def otimizar_ritmo_cenas(
    cenas: List[Dict[str, Any]],
    contexto_visual: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Percorre a lista de cenas e equilibra a cadência visual, ajustando a tipologia
    quando houver repetição excessiva de avatar ou b-roll consecutivo.
    """
    avatar_count = 0
    broll_count = 0

    tipos_avatar = ("avatar_talking", "avatar_action")
    tipos_broll = ("broll_action", "broll_macro", "environment", "comparison")

    for i, c in enumerate(cenas):
        stype = c.get("scene_type", "avatar_talking")
        uses_char = c.get("uses_character", False)

        if stype in tipos_avatar or (uses_char and stype != "hybrid"):
            avatar_count += 1
            broll_count = 0
        elif stype in tipos_broll or not uses_char:
            broll_count += 1
            avatar_count = 0
        else: # Hybrid
            avatar_count = 0
            broll_count = 0

        # Regra 1: Se atingiu mais que max_avatar_consecutive (ex: 3 avatares seguidos)
        if avatar_count > MAX_AVATAR_CONSECUTIVE:
            fala = f"{c.get('narration', '')} {c.get('texto', '')}".lower()
            # Transforma em cena Híbrida ou B-Roll com ação
            if any(k in fala for k in ["mostrar", "olha", "vejam", "aqui", "segurando", "solo", "planta", "passo"]):
                c["scene_type"] = "hybrid"
                c["visual_role"] = "practical_demonstration"
                c["uses_character"] = True
            else:
                c["scene_type"] = "broll_action"
                c["visual_role"] = "supporting_visual"
                c["uses_character"] = False
            avatar_count = 0
            log_event("STORY_RHYTHM", f"Cena {c.get('id', i+1)}: Ritmo ajustado de avatar contínuo para '{c['scene_type']}'.")

        # Regra 2: Se atingiu mais que max_broll_consecutive (ex: 4 b-rolls seguidos)
        if broll_count > MAX_BROLL_CONSECUTIVE:
            # Se o projeto tiver personagem configurado e a cena permitir, puxa um plano híbrido ou ambiente amplo
            char_nome = contexto_visual.get("main_character") if contexto_visual else ""
            if char_nome:
                c["scene_type"] = "hybrid"
                c["visual_role"] = "active_demonstration"
                c["uses_character"] = True
            else:
                c["scene_type"] = "environment"
                c["visual_role"] = "world_establishing"
            broll_count = 0
            log_event("STORY_RHYTHM", f"Cena {c.get('id', i+1)}: Ritmo ajustado de b-roll longo para '{c['scene_type']}'.")

    return cenas
