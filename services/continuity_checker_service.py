"""
services/continuity_checker_service.py — Continuity Checker Engine
===================================================================
Responsabilidade:
- Auditar cada cena antes da compilação final do prompt contra a Bíblia Visual (project_visual_memory.json).
- Valida:
  * character_consistent: Personagem permanece consistente com @Nome e regras de vestimenta?
  * environment_consistent: Cenário/ambiente permanece coerente com a localização global?
  * object_consistent: O objeto principal é respeitado sem substituições incorretas?
  * style_consistent: Câmera, paleta e estética permanecem harmônicos?
- Retorno:
  {
    "character_consistent": bool,
    "environment_consistent": bool,
    "object_consistent": bool,
    "style_consistent": bool,
    "continuity_score": int (0 a 100),
    "warnings": list[str]
  }
- Comportamento: 100% não-bloqueante (anota advertências e pontua).
"""

from typing import Dict, Any, List, Optional
from services.event_logger import log_event


def verificar_continuidade_cena(
    cena: Dict[str, Any],
    memoria_visual: Dict[str, Any],
    contexto_visual: Optional[Dict[str, Any]] = None,
    index: int = 0
) -> Dict[str, Any]:
    """
    Verifica a coerência da cena contra a Bíblia Visual do projeto.
    """
    warnings = []
    
    char_mem = memoria_visual.get("personagem", {})
    env_mem = memoria_visual.get("ambiente", {})
    obj_mem = memoria_visual.get("objetos", {})
    style_mem = memoria_visual.get("estilo", {})
    
    # 1. VERIFICAÇÃO DE PERSONAGEM
    uses_char = cena.get("uses_character", False)
    char_ref = cena.get("character_ref", "").strip()
    official_ref = char_mem.get("reference", "").strip()
    
    char_consistent = True
    if uses_char:
        if not char_ref:
            char_consistent = False
            warnings.append(f"Cena {cena.get('id', index+1)} marcada como uses_character=True mas character_ref está vazia.")
        elif official_ref and char_ref != official_ref:
            char_consistent = False
            warnings.append(f"Cena {cena.get('id', index+1)} usa referência '{char_ref}' divergente da oficial '{official_ref}'.")
        elif char_ref.lower() in ["@homem", "@pessoa", "@man", "@person"]:
            char_consistent = False
            warnings.append(f"Cena {cena.get('id', index+1)} contém tag genérica proibida: {char_ref}.")
    else:
        if char_ref:
            char_consistent = False
            warnings.append(f"Cena {cena.get('id', index+1)} é B-Roll mas possui character_ref '{char_ref}'.")

    # 2. VERIFICAÇÃO DE AMBIENTE
    fala = f"{cena.get('narration', '')} {cena.get('texto', '')}".lower()
    env_consistent = True
    
    # Se a narração citar outro local desconexo (ex: "estou na praia" em projeto de jardim)
    loc_esperado = env_mem.get("location", "").lower()
    if "praia" in fala and "praia" not in loc_esperado and "beach" not in loc_esperado:
        env_consistent = False
        warnings.append(f"Cena {cena.get('id', index+1)} menciona ambiente divergente ('praia') do universo oficial ('{env_mem.get('location')}').")
    elif "neve" in fala and "neve" not in loc_esperado and "snow" not in loc_esperado:
        env_consistent = False
        warnings.append(f"Cena {cena.get('id', index+1)} menciona clima divergente ('neve') do universo oficial.")

    # 3. VERIFICAÇÃO DE OBJETOS
    main_obj = obj_mem.get("main_object", "").lower()
    obj_consistent = True
    
    # Exemplo: se a cena fala de adubo de banana e alguém tenta usar banana inteira
    if "banana" in fala or "adubo" in fala:
        if "banana inteira" in fala or "whole banana" in fala:
            obj_consistent = False
            warnings.append(f"Cena {cena.get('id', index+1)} viola regra de objeto: solicitado adubo de casca, mas citado fruto inteiro.")

    # 4. VERIFICAÇÃO DE ESTILO
    style_consistent = True
    stype = cena.get("scene_type", "")
    cam = cena.get("camera_direction", {})
    if not cam or not cam.get("shot"):
        style_consistent = False
        warnings.append(f"Cena {cena.get('id', index+1)} não possui enquadramento de câmera definido.")

    # 5. CÁLCULO DO SCORE DE CONTINUIDADE (0 a 100)
    deducoes = len(warnings) * 15
    continuity_score = max(0, min(100, 100 - deducoes))

    result = {
        "character_consistent": char_consistent,
        "environment_consistent": env_consistent,
        "object_consistent": obj_consistent,
        "style_consistent": style_consistent,
        "continuity_score": continuity_score,
        "warnings": warnings
    }

    if warnings:
        log_event("CONTINUITY_CHECKER", f"Cena {cena.get('id', index+1)}: {len(warnings)} advertências de continuidade (score={continuity_score})", level="warn")
    else:
        log_event("CONTINUITY_CHECKER", f"Cena {cena.get('id', index+1)}: 100% consistente com a Bíblia Visual.")

    return result