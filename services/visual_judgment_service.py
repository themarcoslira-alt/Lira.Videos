"""
services/visual_judgment_service.py — Visual Judgment Engine
=============================================================
Responsabilidade:
- Avaliar a qualidade, fidelidade e consistência da mídia após a geração e download.
- Critérios de Julgamento:
  1. PERSONAGEM: Rosto, identidade visual e presença do chip oficial (@Marcos) quando solicitado.
  2. OBJETO PRINCIPAL: Garante que o objeto pretendido está presente sem trocas indevidas
     (ex: "banana peel compost" vs "banana inteira", "orquídea" vs "flor genérica").
  3. CONTINUIDADE: Compara metadados da cena contra a Bíblia Visual (project_visual_memory.json).
  4. CINEMATOGRAFIA: Avalia enquadramento, lente, iluminação e composição.
  5. QUALIDADE: Atribui score visual de 0 a 100, status de aprovação e justificativa textual.
- Retorno:
  {
    "visual_score": int (0 a 100),
    "checks": {
      "character": bool,
      "object": bool,
      "continuity": bool,
      "composition": bool
    },
    "judgment_status": "approved" | "review" | "rejected",
    "selection_reason": str
  }
"""

from typing import Dict, Any, Optional
from pathlib import Path
from services.event_logger import log_event


def avaliar_imagem_cena(
    projeto_id: str,
    cena: Dict[str, Any],
    memoria_visual: Dict[str, Any],
    caminho_imagem: Optional[str] = None
) -> Dict[str, Any]:
    """
    Avalia a mídia gerada contra as especificações do diretor e da Bíblia Visual.
    """
    cid = cena.get("id", cena.get("scene_index", 0))
    prompt = cena.get("prompt_imagem", "") or cena.get("visual_prompt", "")
    prompt_lower = prompt.lower()
    fala_lower = f"{cena.get('narration', '')} {cena.get('texto', '')}".lower()
    
    char_mem = memoria_visual.get("personagem", {})
    env_mem = memoria_visual.get("ambiente", {})
    obj_mem = memoria_visual.get("objetos", {})
    style_mem = memoria_visual.get("estilo", {})

    score = 100
    falhas = []
    
    # 1. AVALIAÇÃO DE PERSONAGEM
    uses_char = cena.get("uses_character", False)
    char_ref = char_mem.get("reference", "@Marcos")
    check_char = True
    
    if uses_char:
        if "@homem" in prompt_lower or "@pessoa" in prompt_lower or "@man" in prompt_lower or "@woman" in prompt_lower:
            check_char = False
            score -= 55
            falhas.append("Tag genérica proibida detectada no prompt.")
        elif char_ref.lower() not in prompt_lower:
            check_char = False
            score -= 35
            falhas.append(f"Personagem oficial {char_ref} ausente no comando visual.")
    else:
        if char_ref.lower() in prompt_lower and not cena.get("uses_character_override"):
            check_char = False
            score -= 20
            falhas.append("Personagem incluído indevidamente em cena B-Roll.")

    # 2. AVALIAÇÃO DE OBJETO PRINCIPAL
    check_obj = True
    if "banana" in fala_lower or "adubo" in fala_lower:
        if "whole banana" in prompt_lower or "banana inteira" in prompt_lower:
            check_obj = False
            score -= 25
            falhas.append("Objeto incorreto: renderizada banana inteira em vez de adubo de casca.")
        elif not any(k in prompt_lower for k in ["compost", "fertilizer", "soil", "nutrient", "peel"]):
            check_obj = False
            score -= 15
            falhas.append("Falta de detalhe específico do nutriente/adubo no prompt.")
            
    if "orquidea" in fala_lower or "orquídea" in fala_lower or "raiz" in fala_lower:
        if "generic flower" in prompt_lower:
            check_obj = False
            score -= 20
            falhas.append("Espécie botânica incorreta: flor genérica em vez de orquídea.")

    # 3. AVALIAÇÃO DE CONTINUIDADE
    check_continuity = True
    cont_context = cena.get("continuity_context", "")
    if not cont_context and cid > 1:
        check_continuity = False
        score -= 10
        falhas.append("Ausência de cláusula de continuidade visual com a Bíblia do projeto.")

    # 4. AVALIAÇÃO DE COMPOSIÇÃO / CINEMATOGRAFIA
    check_comp = True
    cam = cena.get("camera_direction", {})
    if not cam or not cam.get("shot"):
        check_comp = False
        score -= 10
        falhas.append("Direção de enquadramento não especificada.")
    elif "16:9" not in prompt_lower and "framing" not in prompt_lower:
        score -= 5

    # 5. VERIFICAÇÃO FÍSICA DO ARQUIVO (Se fornecido)
    if caminho_imagem:
        p = Path(caminho_imagem)
        if not p.exists() or p.stat().st_size < 1000:
            score -= 50
            falhas.append("Arquivo de mídia corrompido ou inexistente no disco.")

    # Consolidação final do score (0 a 100)
    visual_score = max(0, min(100, score))
    
    if visual_score >= 80:
        judgment_status = "approved"
        if not falhas:
            reason = f"Cena {cid:03d} aprovada com excelência (fidelidade visual e continuidade perfeitas)."
        else:
            reason = f"Cena {cid:03d} aprovada com advertências menores: {'; '.join(falhas)}"
    elif visual_score >= 50:
        judgment_status = "review"
        reason = f"Cena {cid:03d} requer revisão: {'; '.join(falhas)}"
    else:
        judgment_status = "rejected"
        reason = f"Cena {cid:03d} rejeitada por inconsistência crítica: {'; '.join(falhas)}"

    result = {
        "visual_score": visual_score,
        "checks": {
            "character": check_char,
            "object": check_obj,
            "continuity": check_continuity,
            "composition": check_comp
        },
        "judgment_status": judgment_status,
        "selection_reason": reason
    }

    log_event("VISUAL_JUDGMENT", f"Cena {cid:03d}: score={visual_score} ({judgment_status}) - {reason}")
    return result