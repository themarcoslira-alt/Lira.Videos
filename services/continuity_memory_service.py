"""
services/continuity_memory_service.py — Continuity Memory Layer
================================================================
Responsabilidade:
- Assegurar a consistência visual inquebrável em todo o projeto:
  * Personagem: Rosto, idade, corte de cabelo, roupa, tom de pele e postura.
  * Mundo: Local, horário do dia, iluminação global, atmosfera e clima.
  * Estilo: Câmera, textura, paleta de cores e estética cinematográfica.
- Fornecer blocos de ancoragem de continuidade para cada cena.
"""

from typing import Dict, Any, Optional
from services.character_service import obter_identidade_projeto
from services.event_logger import log_event


def gerar_contexto_continuidade_cena(
    projeto_id: str,
    cena: Dict[str, Any],
    scene_type: str,
    uses_character: bool = False,
    character_ref: str = "",
    index: int = 0,
    total_cenas: int = 1,
    contexto_visual: Optional[Dict[str, Any]] = None
) -> str:
    """
    Constrói a instrução de continuidade visual para a cena.
    """
    contexto = contexto_visual or {}
    world = contexto.get("world", "Lush botanical garden setting with natural lighting")
    char_nome = contexto.get("main_character", "")

    ancoras = []

    # 1. Ancoragem de Personagem
    if uses_character and character_ref:
        ancoras.append("Preserve exact character visual identity, facial features and signature wardrobe")

    # 2. Ancoragem de Mundo / Cenário
    if scene_type in ("avatar_talking", "avatar_action", "hybrid", "environment"):
        ancoras.append(f"Seamless continuity in {world.lower()}")
    elif scene_type in ("broll_macro", "broll_action"):
        ancoras.append(f"Matching background textures and natural daylight consistent with the main garden setting")

    # 3. Ancoragem de Iluminação e Atmosfera
    if index > 0:
        ancoras.append("Cohesive color palette, natural photorealistic textures, 16:9 framing")

    contexto_str = ". ".join(ancoras)
    if contexto_str and not contexto_str.endswith("."):
        contexto_str += "."

    log_event("CONTINUITY_MEMORY", f"Cena {cena.get('id', index+1)}: continuidade gerada ({len(ancoras)} âncoras).")
    return contexto_str
