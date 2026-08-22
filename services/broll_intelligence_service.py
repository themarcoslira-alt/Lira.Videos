"""
services/broll_intelligence_service.py — B-Roll Intelligence Layer
===================================================================
Responsabilidade:
- Analisar a narrativa da cena e determinar a lista de elementos visuais de apoio (supporting_visuals).
- Exemplo:
  Fala: "Eu descobri que minhas plantas precisavam de mais água"
  Supporting Visuals:
  * "Close das folhas murchas com bordas ressecadas"
  * "Mão regando a base da planta com regador de bico fino"
  * "Solo escuro absorvendo gotas de água cristalinas"
  * "Planta revigorada com folhas verdes eretas"
"""

from typing import List, Dict, Any, Optional
from services.event_logger import log_event


def gerar_broll_inteligente(
    cena: Dict[str, Any],
    scene_type: str,
    contexto_visual: Optional[Dict[str, Any]] = None
) -> List[str]:
    """
    Analisa a cena e gera uma lista inteligente de elementos visuais de apoio (supporting_visuals).
    """
    fala = f"{cena.get('narration', '')} {cena.get('texto', '')} {cena.get('text', '')}".lower()
    suporte = []

    # 1. Detecção por termos de Jardinagem / Botânica
    if any(k in fala for k in ["adubo", "banana", "casca", "fertilizante", "nutriente", "compost"]):
        suporte.extend([
            "Macro close-up of organic banana peel nutrient compost",
            "Rich dark moist soil with crumbly fertile texture",
            "Hands gently applying organic fertilizer around plant roots"
        ])
    elif any(k in fala for k in ["raiz", "raízes", "root"]):
        suporte.extend([
            "Close-up of healthy white and golden plant root network",
            "Transparent planter pot showcasing root development",
            "Soil texture surrounding healthy vibrant roots"
        ])
    elif any(k in fala for k in ["rosa", "flor", "pétala", "orquídea", "rose", "flower"]):
        suporte.extend([
            "Macro shot of vivid rose petals with morning dew droplets",
            "Lush green foliage with subtle atmospheric bokeh",
            "Sunlight filtering through vibrant floral blossoms"
        ])
    elif any(k in fala for k in ["água", "regar", "umidade", "water"]):
        suporte.extend([
            "Water droplets splashing gently onto fresh green leaves",
            "Dark porous garden soil absorbing fresh clean water",
            "Watering tool pouring a fine stream at the base of the plant"
        ])
    elif any(k in fala for k in ["folha", "folhas", "poda", "cortar", "leaf"]):
        suporte.extend([
            "High-detail macro of intricate leaf vein patterns",
            "Gardening pruning shears making a clean precision cut",
            "Fresh vibrant green leaves catching morning sunlight"
        ])

    # 2. Detecção por termos de Tecnologia / Software
    elif any(k in fala for k in ["código", "software", "tela", "app", "interface", "code"]):
        suporte.extend([
            "Clean macro of code syntax on dark mode high-res display",
            "Hands typing on a minimalist mechanical keyboard",
            "Modern workstation ambiance with soft LED rim lighting"
        ])

    # 3. Detecção por termos de Culinária
    elif any(k in fala for k in ["ingrediente", "receita", "cozinha", "sabor", "cooking"]):
        suporte.extend([
            "Fresh colorful culinary ingredients arranged on rustic wood",
            "Knife precision slicing through fresh crisp produce",
            "Steaming pan with aromatic olive oil and herbs"
        ])

    # 4. Fallback contextual cinematográfico
    if not suporte:
        world = contexto_visual.get("world", "authentic botanical environment") if contexto_visual else "authentic environment"
        suporte.extend([
            f"Detailed environmental texture matching {world}",
            "Atmospheric natural lighting capturing depth of scene",
            "Subject interacting authentically with natural surrounding elements"
        ])

    log_event("BROLL_INTELLIGENCE", f"Cena {cena.get('id', 0)}: {len(suporte)} supporting_visuals gerados.")
    return suporte
