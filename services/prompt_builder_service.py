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
from pathlib import Path
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

    # DETECÇÃO DE PERSONAGEM POR CENA (detectar_personagens_cena):
    # liga o prompt final ao personagem REAL citado na cena e expõe a imagem
    # de referência (reference.png) para o Playwright anexar no Google Flow.
    # Lira Studio v0.2.0 (Frente 1): se narrative_role for BROLL ou avatar_required for False,
    # a cena é cobertura visual pura e não deve ter personagem injetado por léxico.
    is_broll = (cena.get("narrative_role") == "BROLL") or (cena.get("avatar_required") is False)
    if is_broll:
        uses_char = False
        char_ref = ""
        if stype in ["avatar_talking", "avatar_action", "hybrid"]:
            stype = "broll_macro"

    personagem_detectado = None
    detec = None
    if not is_broll:
        try:
            from services.character_service import detectar_personagens_cena as _detectar_cena
            from services.character_service import resolver_imagem_avatar_projeto as _avatar_proj
            detec = _detectar_cena(projeto_id, cena)
        except Exception:
            detec = None

    if detec and detec.get("total_detectados", 0) > 0:
        personagens_det = detec.get("personagens", [])
        # Prioriza a referência mais concreta: flow_id > @nome > reference.png > upload > nenhuma
        prioridade_ref = {"flow_id": 0, "@nome": 1, "reference.png": 2, "upload": 3, "nenhuma": 4}
        personagem_det = sorted(
            personagens_det,
            key=lambda p: prioridade_ref.get((p.get("referencia") or {}).get("tipo", "nenhuma"), 9),
        )[0]
        ref_det = personagem_det.get("referencia") or {}
        nome_det = personagem_det.get("nome", "")
        tag_det = ""
        img_det = ""

        if ref_det.get("tipo") == "@nome":
            tag_det = ref_det.get("valor") or (f"@{nome_det}" if nome_det else "")
        elif ref_det.get("tipo") == "flow_id":
            tag_det = ref_det.get("valor") or (f"@{nome_det}" if nome_det else "")
        else:
            tag_det = f"@{nome_det}" if nome_det else ""

        if ref_det.get("tipo") in ("reference.png", "upload"):
            img_det = ref_det.get("valor", "") or ""
        elif ref_det.get("tipo") in ("@nome", "flow_id"):
            # Entidade traz imagem_abs/reference_image_abs (ex: characters/<Nome>/reference.png)
            ent_det = ref_det.get("entidade") or {}
            img_det = (ent_det.get("imagem_abs") or ent_det.get("reference_image_abs") or "").strip()
            if img_det and not Path(img_det).exists():
                img_det = ""
        if not img_det:
            img_det = _avatar_proj(projeto_id)

        if tag_det:
            cena["uses_character"] = True
            cena["character_ref"] = tag_det
            char_ref = tag_det
            uses_char = True
        cena["personagem_detectado"] = {
            "nome": nome_det,
            "origem": personagem_det.get("origem", ""),
            "referencia_tipo": ref_det.get("tipo", ""),
            "referencia_valor": ref_det.get("valor", ""),
        }
        cena["personagem_ref"] = tag_det
        cena["personagem_ref_imagem"] = img_det
        log_event(
            "PROMPT_BUILDER",
            f"Cena {cena.get('id', index+1)}: personagem(ns) detectado(s) "
            f"{[p.get('nome') for p in personagens_det]} -> ref '{tag_det}' img '{img_det}'",
        )

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

    # Garante que char_ref nunca seja vazio nem genérico (@Homem/@Pessoa/@Personagem).
    # Quando uses_character, o sujeito é SEMPRE o personagem do projeto (@Nome real).
    if uses_char:
        if not char_ref or char_ref.lower() in ["@homem", "@mulher", "@pessoa", "@man", "@woman",
                                                "@person", "@personagem", "personagem"]:
            nome_real = str(cena.get("nome_personagem") or "").strip() or str(cena.get("nome") or "").strip()
            if not nome_real and contexto_visual:
                nome_real = contexto_visual.get("main_character", "")
            char_ref = f"@{nome_real}" if nome_real and not nome_real.startswith("@") else (f"@{nome_real}" if nome_real else "")
        cena["character_ref"] = char_ref

    world = (contexto_visual.get("world") or "authentic cinematic environment") if contexto_visual else "authentic cinematic environment"
    elementos_prompt = []

    # 1. Sujeito e Ação Principal
    if uses_char or stype in ["avatar_talking", "avatar_action", "cta", "hybrid"]:
        sujeito = char_ref if char_ref else ("@" + str(cena.get("nome_personagem") or "Personagem"))
        if stype == "avatar_talking":
            acao_core = f"{sujeito} looking towards the camera with a {emotion} and engaging expression, speaking naturally"
        elif stype == "avatar_action":
            acao_core = f"{sujeito} actively engaged in practical focused action, authentic posture"
        elif stype == "hybrid":
            acao_core = f"{sujeito} presenting visual element towards the foreground with an instructive posture"
        elif stype == "cta":
            acao_core = f"{sujeito} with a welcoming and inspiring presence, smiling towards the viewer"
        else:
            acao_core = f"{sujeito} authentically engaged in the setting"

        # Adiciona especificações cinematográficas completas do avatar
        elementos_prompt.append(acao_core)
        cam_specs = [p for p in [shot or "close-up", f"shot on {lens or '50mm'} lens", composition or "rule of thirds, expressive facial details"] if p]
        elementos_prompt.append(", ".join(cam_specs))
        elementos_prompt.append(f"{lighting or 'soft diffused natural light, balanced and clean'}, shallow depth of field, photorealistic textures, 16:9")
        elementos_prompt.append("Preserve exact character visual identity, facial features and signature wardrobe")
        elementos_prompt.append(f"Seamless continuity in {world}")
        elementos_prompt.append("Cohesive color palette, natural photorealistic textures, 16:9 framing")
    else:
        # B-Roll / Detalhe / Macro / Environment
        if stype == "broll_macro":
            acao_core = supporting[0] if supporting else f"Detailed close-up macro texture in {world}"
        elif stype == "broll_action":
            acao_core = supporting[min(1, len(supporting)-1)] if supporting else f"First-person focused practical action in {world}"
        elif stype == "environment":
            acao_core = f"Wide scenic establishing shot of {world}, natural lighting and atmospheric depth"
        elif stype == "before_after":
            acao_core = supporting[0] if supporting else f"Side-by-side visual demonstration in {world}"
        else:
            acao_core = supporting[0] if supporting else f"Cinematic detailed composition in {world}"

        elementos_prompt.append(acao_core)
        if shot or lens or composition:
            cam_specs = [p for p in [shot, f"shot on {lens} lens" if lens else "", composition] if p]
            elementos_prompt.append(", ".join(cam_specs))
        elementos_prompt.append(f"{lighting}, shallow depth of field, photorealistic textures, 16:9")
        if continuity and continuity not in acao_core:
            elementos_prompt.append(continuity.rstrip("."))

    # Monta prompt de imagem final sem repetições
    partes_finais = []
    for p in elementos_prompt:
        p_clean = p.strip().rstrip(".")
        if p_clean and p_clean not in partes_finais:
            partes_finais.append(p_clean)

    prompt_imagem_final = ". ".join(partes_finais) + "."

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
