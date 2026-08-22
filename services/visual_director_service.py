"""
services/visual_director_service.py — Visual Director AI
=========================================================
Responsabilidade:
- Ler a transcrição/SRT inteira antes de processar cenas individuais.
- Extrair o tema central, objetivo da narrativa, ambiente principal,
  personagem principal, objetos recorrentes, tom emocional e regras de continuidade.
- Persistir em project_visual_context.json dentro do diretório do projeto.
"""

import re
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from config import PROJETOS_DIR
from services.event_logger import log_event

VISUAL_CONTEXT_FILE = "project_visual_context.json"


def _context_path(projeto_id: str) -> Path:
    pdir = PROJETOS_DIR / projeto_id
    pdir.mkdir(parents=True, exist_ok=True)
    return pdir / VISUAL_CONTEXT_FILE


def salvar_contexto_visual(projeto_id: str, context: Dict[str, Any]) -> bool:
    """Persiste o contexto visual do projeto."""
    try:
        p = _context_path(projeto_id)
        p.write_text(json.dumps(context, indent=2, ensure_ascii=False), encoding="utf-8")
        log_event("VISUAL_DIRECTOR", f"{projeto_id}: project_visual_context.json salvo com sucesso.")
        return True
    except Exception as e:
        log_event("VISUAL_DIRECTOR", f"{projeto_id}: erro ao salvar contexto visual: {e}", level="warn")
        return False


def obter_contexto_visual(projeto_id: str) -> Dict[str, Any]:
    """Recupera o contexto visual persistido ou gera um padrão caso inexistente."""
    p = _context_path(projeto_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "theme": "General visual narrative",
        "main_character": "",
        "world": "Cinematic authentic environment",
        "tone": "engaging and informative",
        "visual_style": "photorealistic_cinematic",
        "recurring_objects": [],
        "continuity_rules": [
            "Maintain consistent natural lighting and color palette",
            "Preserve exact character visual identity across all avatar scenes"
        ]
    }


def extrair_tema_e_mundo(texto_completo: str) -> Dict[str, str]:
    """Identifica semanticamente o tema, o mundo e os objetos da narrativa."""
    t = texto_completo.lower()
    
    # 1. Detecção de Mundo / Ambiente
    if any(k in t for k in ["jardim", "planta", "rosa", "flor", "adubo", "solo", "raiz", "garden", "flower", "soil", "plant"]):
        world = "Lush botanical rustic garden with green foliage, rich dark soil and natural daylight"
        theme = "Gardening, botanical care and organic cultivation"
    elif any(k in t for k in ["tecnologia", "computador", "software", "código", "ia", "tech", "code", "ai", "computer"]):
        world = "Modern tech workspace with sleek workstation, ambient LED lighting and clean minimalist aesthetics"
        theme = "Technology, digital innovation and software development"
    elif any(k in t for k in ["cozinha", "receita", "comida", "culinária", "prato", "kitchen", "cooking", "recipe", "food"]):
        world = "Warm gourmet kitchen with rustic wooden countertops, fresh ingredients and soft lighting"
        theme = "Culinary arts, cooking and gourmet gastronomy"
    elif any(k in t for k in ["treino", "academia", "fitness", "saúde", "exercício", "gym", "workout", "fitness"]):
        world = "Modern dynamic fitness studio with atmospheric lighting and professional equipment"
        theme = "Health, fitness training and physical performance"
    elif any(k in t for k in ["negócio", "empresa", "vendas", "marketing", "finance", "business"]):
        world = "Contemporary executive office with glass walls, city backdrop and elegant corporate styling"
        theme = "Business strategy, professional growth and finance"
    else:
        world = "Cinematic authentic setting with natural environmental depth"
        theme = "Engaging documentary storytelling"

    # 2. Detecção de Objetos Recorrentes
    recurring = []
    mapa_objetos = [
        ("adubo", "organic compost fertilizer"),
        ("banana", "banana peel nutrients"),
        ("rosa", "blooming rose plant"),
        ("orquídea", "delicate orchid flowers"),
        ("raiz", "healthy root system"),
        ("solo", "rich dark garden soil"),
        ("tesoura", "gardening pruning shears"),
        ("vaso", "botanical planter pot"),
        ("água", "watering can and water droplets"),
        ("laptop", "sleek modern laptop"),
        ("smartphone", "smartphone interface"),
        ("café", "steaming cup of artisan coffee"),
        ("caderno", "leatherbound notebook and pen"),
    ]
    for termo_pt, termo_en in mapa_objetos:
        if termo_pt in t:
            recurring.append(termo_en)

    return {
        "theme": theme,
        "world": world,
        "recurring_objects": recurring
    }


def analisar_roteiro_completo(
    projeto_id: str,
    cenas_raw: List[Dict[str, Any]],
    nome_personagem_default: str = "",
    estilo_visual: str = "photorealistic_cinematic"
) -> Dict[str, Any]:
    """
    VISUAL DIRECTOR AI:
    Lê todo o roteiro / blocos SRT juntos para compor a visão artística global:
    - Tema central do vídeo
    - Objetivo da narrativa
    - Personagem principal
    - Ambiente principal (World)
    - Objetos recorrentes
    - Tom emocional
    - Estilo visual
    - Regras de continuidade
    """
    texto_completo = " ".join(
        str(c.get("texto") or c.get("text") or c.get("narration") or "")
        for c in cenas_raw
    ).strip()

    ext = extrair_tema_e_mundo(texto_completo)

    # Identifica tom emocional geral
    t_lower = texto_completo.lower()
    if any(k in t_lower for k in ["segredo", "erro", "cuidado", "atenção", "descobri", "perigo", "warning"]):
        tone = "intriguing, revealing and authoritative"
    elif any(k in t_lower for k in ["fácil", "rápido", "passo a passo", "aprenda", "simples"]):
        tone = "clear, encouraging, instructional and educational"
    elif any(k in t_lower for k in ["resultado", "impressionante", "incrível", "maravilhoso"]):
        tone = "inspiring, uplifting and visually rewarding"
    else:
        tone = "engaging, authentic and cinematic"

    context = {
        "theme": ext["theme"],
        "main_character": nome_personagem_default.strip(),
        "world": ext["world"],
        "tone": tone,
        "visual_style": estilo_visual or "photorealistic_cinematic",
        "recurring_objects": ext["recurring_objects"],
        "continuity_rules": [
            f"Maintain {ext['world']} as the cohesive backdrop",
            "Preserve realistic lighting continuity and natural color balance",
            "Lock character facial features and wardrobe across all avatar scenes"
            if nome_personagem_default else "Maintain strict prop and environmental continuity"
        ],
        "total_cenas_analisadas": len(cenas_raw),
        "script_word_count": len(texto_completo.split())
    }

    salvar_contexto_visual(projeto_id, context)
    print(f"[LOG] VISUAL_DIRECTOR_CONTEXT_CREATED: Tema='{context['theme']}' | Mundo='{context['world']}' | Personagem='{context['main_character']}'", flush=True)
    return context
