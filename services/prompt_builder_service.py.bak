"""
services/prompt_builder_service.py — Visual Prompt Builder AI
=============================================================
Responsabilidade:
- Sintetizar todas as decisões da camada de Inteligência Visual (Visual Director) em prompts cinematográficos de alta fidelidade para o Google Flow.
- Eliminar prompts genéricos ("Cinematic visual depicting...").
- Estruturar o prompt combinando:
  * Sujeito / Personagem (chip nativo @Nome quando uses_character=True, nunca @Homem)
  * Ação física ou detalhe concreto
  * Cenário / Mundo (World)
  * Direção de Câmera (Enquadramento, Lente, Composição)
  * Iluminação e Tom Emocional
  * Âncoras de Continuidade
- Regras Estritas:
  * Nunca incluir timestamps no prompt de texto.
  * Gerar texto em inglês limpo, expressivo e cinematicamente denso.
  * prompt_imagem (100% estático para Nano Banana 2) vs prompt_animacao (movimento para Veo 3.1).
"""

import re
from typing import Dict, Any, Optional, List
from services.event_logger import log_event


def _limpar_texto(texto: str) -> str:
    """Remove timestamps e formatações de legendas."""
    t = re.sub(r'\[\d+:\d+\]', '', texto)
    t = re.sub(r'\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}', '', t)
    return " ".join(t.split()).strip()


def construir_prompt_diretor(
    projeto_id: str,
    cena: Dict[str, Any],
    contexto_visual: Optional[Dict[str, Any]] = None,
    index: int = 0,
    total_cenas: int = 1
) -> Dict[str, str]:
    """
    Constrói os prompts oficiais (prompt_imagem e prompt_animacao) integrando todos os sinais do Visual Director AI.
    """
    fala = _limpar_texto(cena.get("narration") or cena.get("texto") or cena.get("text") or "")
    fala_lower = fala.lower()
    
    stype = cena.get("scene_type", "broll_macro")
    uses_char = bool(cena.get("uses_character", False))
    char_ref = cena.get("character_ref", "").strip()
    emotion = cena.get("emotion", "curiosity")
    camera = cena.get("camera_direction") or {}
    shot = camera.get("shot", "medium shot")
    lens = camera.get("lens", "35mm")
    movement = camera.get("movement", "slow push-in")
    composition = camera.get("composition", "natural framing")
    lighting = cena.get("lighting_mood", "natural morning daylight")
    supporting = cena.get("supporting_visuals") or []
    continuity = cena.get("continuity_context", "")
    duracao = float(cena.get("duracao", 5.0))

    # FASE 3.3 / 11 — Garante que char_ref nunca seja vazio nem genérico (@Homem/@Pessoa/@Personagem).
    # Quando uses_character, o sujeito é SEMPRE o personagem bloqueado (@Nome real).
    if uses_char:
        if not char_ref or char_ref.lower() in ["@homem", "@mulher", "@pessoa", "@man", "@woman",
                                                "@person", "@personagem", "personagem"]:
            nome_real = str(cena.get("nome_personagem") or "").strip() or str(cena.get("nome") or "").strip()
            if not nome_real and contexto_visual:
                nome_real = contexto_visual.get("main_character", "")
            char_ref = f"@{nome_real}" if nome_real and not nome_real.startswith("@") else (nome_real or "@Marcos")
        cena["character_ref"] = char_ref

    elementos_prompt = []

    # 1. Sujeito e Ação Principal
    if uses_char and char_ref:
        if stype == "avatar_talking":
            acao_core = f"{char_ref} looking towards the camera with a {emotion} and engaging expression, speaking naturally"
        elif stype == "avatar_action":
            acao_core = f"{char_ref} actively working in the garden, handling botanical care with focused posture"
        elif stype == "hybrid":
            acao_core = f"{char_ref} presenting an organic botanical element towards the foreground with an instructive posture"
        elif stype == "cta":
            acao_core = f"{char_ref} with a welcoming and inspiring presence, smiling towards the viewer"
        else:
            acao_core = f"{char_ref} authentically engaged in the setting"
    else:
        # B-Roll / Detalhe / Macro / Environment
        if stype == "broll_macro":
            if supporting:
                acao_core = supporting[0]
            else:
                acao_core = "Macro close-up shot of rich dark organic garden soil and vibrant green botanical roots"
        elif stype == "broll_action":
            if supporting:
                acao_core = supporting[min(1, len(supporting)-1)]
            else:
                acao_core = "First-person close-up perspective of hands gently applying organic fertilizer around plant roots"
        elif stype == "environment":
            world = contexto_visual.get("world", "Lush rustic garden") if contexto_visual else "Lush rustic garden"
            acao_core = f"Wide scenic establishing shot of {world}, blooming foliage and atmospheric morning mist"
        elif stype == "before_after":
            acao_core = "Clear side-by-side visual demonstration of healthy flourishing plant leaves compared to dull unfertilized soil"
        else:
            acao_core = supporting[0] if supporting else "Cinematic detailed botanical composition"

    elementos_prompt.append(acao_core)

    # 2. Especificação Cinematográfica de Câmera e Lente
    elementos_prompt.append(f"{shot}, shot on {lens} lens, {composition}")

    # 3. Iluminação e Atmosfera
    elementos_prompt.append(f"{lighting}, shallow depth of field, photorealistic textures, 8k resolution, 16:9")

    # 4. Continuidade
    if continuity:
        elementos_prompt.append(continuity.rstrip("."))

    # Monta prompt de imagem final
    prompt_imagem_final = ". ".join(p.strip().rstrip(".") for p in elementos_prompt if p.strip()) + "."

    # 5. Prompt de Animação / Câmera (Veo 3.1 - Fase 2)
    if duracao < 3.0:
        prompt_animacao_final = f"Smooth cinematic {movement}, 3s subtle ease, natural micro environmental movement"
    elif duracao > 6.0:
        prompt_animacao_final = f"Subtle {movement} with gentle parallax drift, steady cinematic motion, 6s ease"
    else:
        prompt_animacao_final = f"Cinematic {movement}, steady camera tracking, lifelike subtle ambient breeze"

    log_event("PROMPT_BUILDER", f"Cena {cena.get('id', index+1)}: Prompt de diretor construído ({len(prompt_imagem_final)} chars).")
    return {
        "prompt_imagem": prompt_imagem_final,
        "prompt_animacao": prompt_animacao_final
    }
