"""
services/visual_memory_engine.py — Project Visual Memory Engine (Bíblia Visual)
================================================================================
Responsabilidade:
- Construir e gerenciar a "Bíblia Visual" persistente do projeto em project_visual_memory.json.
- Guarda definições canônicas de:
  * PERSONAGEM: Nome, referência (@Marcos), aparência, vestimenta (clothing) e regras de identidade.
  * AMBIENTE: Localização, iluminação global, horário e clima.
  * OBJETOS: Objeto principal da narrativa e objetos recorrentes.
  * ESTILO: Estética cinematográfica, câmera, lentes e composição.
  * CONTINUIDADE: Regras imutáveis de consistência que nenhuma cena pode violar.
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional, List
from config import PROJETOS_DIR
from services.event_logger import log_event

VISUAL_MEMORY_FILE = "project_visual_memory.json"


def _memory_file_path(projeto_id: str) -> Path:
    pdir = PROJETOS_DIR / projeto_id
    pdir.mkdir(parents=True, exist_ok=True)
    return pdir / VISUAL_MEMORY_FILE


def obter_memoria_visual_projeto(projeto_id: str) -> Dict[str, Any]:
    """Recupera a Bíblia Visual do projeto ou gera um padrão se inexistente."""
    p = _memory_file_path(projeto_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            log_event("VISUAL_MEMORY_ENGINE", f"Erro ao ler {p}: {e}", level="warn")

    # Fallback inicial estruturado
    return {
        "projeto_id": projeto_id,
        "personagem": {
            "name": "",
            "reference": "",
            "appearance": "Authentic cinematic presenter",
            "clothing": "Casual comfortable attire",
            "identity_rules": ["Preserve facial structure and authentic styling"]
        },
        "ambiente": {
            "location": "Cinematic authentic environment",
            "lighting": "Natural daylight with soft shadows",
            "time_of_day": "morning daylight",
            "weather": "clear atmospheric lighting"
        },
        "objetos": {
            "main_object": "Key narrative subject",
            "recurring_objects": []
        },
        "estilo": {
            "aesthetic": "photorealistic_cinematic",
            "camera": "35mm prime lens, shallow depth of field",
            "composition": "rule of thirds, natural depth"
        },
        "continuidade": {
            "rules": [
                "Maintain uniform lighting and environment across all scenes",
                "Preserve exact wardrobe and character traits"
            ]
        }
    }


def salvar_memoria_visual_projeto(projeto_id: str, memory_data: Dict[str, Any]) -> bool:
    """Persiste a Bíblia Visual no arquivo project_visual_memory.json."""
    try:
        p = _memory_file_path(projeto_id)
        p.write_text(json.dumps(memory_data, indent=2, ensure_ascii=False), encoding="utf-8")
        log_event("VISUAL_MEMORY_ENGINE", f"{projeto_id}: {VISUAL_MEMORY_FILE} salvo com sucesso.")
        return True
    except Exception as e:
        log_event("VISUAL_MEMORY_ENGINE", f"{projeto_id}: erro ao salvar memória visual: {e}", level="warn")
        return False


def construir_memoria_visual_projeto(
    projeto_id: str,
    contexto_visual: Optional[Dict[str, Any]] = None,
    identidade: Optional[Dict[str, Any]] = None,
    roteiro_texto: str = ""
) -> Dict[str, Any]:
    """
    Constrói a Bíblia Visual do projeto integrando a identidade oficial,
    o contexto macro do Visual Director e o roteiro.
    """
    ctx = contexto_visual or {}
    ident = identidade or {}
    
    char_nome = ident.get("nome") or ctx.get("main_character") or ""
    char_ref = ident.get("referencia_flow") or (f"@{char_nome}" if char_nome else "")
    
    # 1. PERSONAGEM
    if char_nome:
        appearance = f"Authentic personable adult presenter ({char_nome}), friendly expression, athletic build, short dark hair"
        clothing = "Signature olive green gardening shirt with rolled-up sleeves, dark durable canvas work pants"
        identity_rules = [
            f"Always attach official flow character chip {char_ref}",
            "Preserve exact facial features, hair styling and skin tones across all avatar scenes",
            "Keep signature olive green work shirt consistent in every human appearance"
        ]
    else:
        appearance = "Documentary scenic focus without primary human presenter"
        clothing = "N/A"
        identity_rules = ["No human character injection in scenic/nature footage"]

    # 2. AMBIENTE
    world_desc = ctx.get("world", "Lush botanical rustic garden with green foliage, rich dark soil and natural daylight")
    ambiente = {
        "location": world_desc,
        "lighting": "Natural soft morning sunlight, balanced directional contrast, warm daylight",
        "time_of_day": "golden morning sunlight",
        "weather": "clear crisp atmospheric morning"
    }

    # 3. OBJETOS
    recurring = list(ctx.get("recurring_objects", []))
    main_obj = "Organic fermented banana peel compost fertilizer" if any("banana" in o.lower() or "adubo" in o.lower() for o in recurring) else (recurring[0] if recurring else "Botanical soil and plant ecosystem")
    
    # Adiciona objetos recorrentes padrão se não estiverem na lista
    objetos_detectados = set(recurring)
    for padrao in ["organic fertilizer compost", "rich dark garden soil", "healthy plant root network", "blooming orchid flowers"]:
        objetos_detectados.add(padrao)

    objetos = {
        "main_object": main_obj,
        "recurring_objects": sorted(list(objetos_detectados))
    }

    # 4. ESTILO
    estilo = {
        "aesthetic": ctx.get("visual_style", "photorealistic_cinematic"),
        "camera": "35mm and 50mm prime lenses, f/1.8 aperture, creamy background bokeh, 8k resolution, 16:9",
        "composition": "Rule of thirds, centered subject framing, organic texture depth, leading botanical lines"
    }

    # 5. CONTINUIDADE
    regras_continuidade = [
        f"Maintain {world_desc} as the singular cohesive location across the entire narrative",
        "Preserve exact lighting continuity (soft morning sunlight with organic color temperature)",
        "Never substitute organic compost with raw unpeeled whole fruits",
        "Maintain identical camera color grading, textures, and depth of field parameters"
    ]
    if char_nome:
        regras_continuidade.insert(0, f"Character {char_ref} must always wear the signature olive green gardening shirt in all avatar and hybrid scenes")

    memory_bible = {
        "projeto_id": projeto_id,
        "personagem": {
            "name": char_nome,
            "reference": char_ref,
            "appearance": appearance,
            "clothing": clothing,
            "identity_rules": identity_rules
        },
        "ambiente": ambiente,
        "objetos": objetos,
        "estilo": estilo,
        "continuidade": {
            "rules": regras_continuidade
        }
    }

    salvar_memoria_visual_projeto(projeto_id, memory_bible)
    print(f"[LOG] VISUAL_MEMORY_BIBLE_CREATED: Bíblia Visual do projeto '{projeto_id}' salva com sucesso em {VISUAL_MEMORY_FILE}", flush=True)
    log_event("VISUAL_MEMORY_ENGINE", f"Bíblia Visual criada para {projeto_id}: {len(regras_continuidade)} regras.")
    return memory_bible