"""
services/visual_memory_service.py — Visual Memory System
=========================================================
Gerencia a memória visual do universo do projeto em memory/visual_memory.json.
Mantém consistência de:
- Ambiente (environment / location)
- Iluminação (lighting / time)
- Estilo de Câmera (camera_style / lenses)
- Objetos principais (objects)
- Paleta de Cores (color_palette)
- Locks de Continuidade e Negativos
"""

import json
from pathlib import Path
from typing import Optional, Dict, Any

from config import PROJETOS_DIR
from services.event_logger import log_event


def _get_memory_dir(projeto_id: str) -> Path:
    d = PROJETOS_DIR / projeto_id / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def obter_memoria_visual(projeto_id: str) -> Dict[str, Any]:
    """Lê a memória visual do projeto ou retorna o estado padrão."""
    mem_file = _get_memory_dir(projeto_id) / "visual_memory.json"
    if mem_file.exists():
        try:
            return json.loads(mem_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return inicializar_memoria_visual(projeto_id)


def salvar_memoria_visual(projeto_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Salva os dados de memória visual no arquivo memory/visual_memory.json."""
    mem_file = _get_memory_dir(projeto_id) / "visual_memory.json"
    mem_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    log_event("VISUAL_MEMORY", f"Memória visual atualizada para o projeto '{projeto_id}'")
    return data


def inicializar_memoria_visual(projeto_id: str, estilo_visual: str = "photorealistic_cinematic",
                               transcricao_texto: str = "") -> Dict[str, Any]:
    """
    Inicializa a memória visual analisando o contexto da transcrição/roteiro
    e configurando os Locks do universo visual.
    """
    txt_lower = transcricao_texto.lower()

    # Detecção contextual do ambiente
    if any(w in txt_lower for w in ["dandelion", "dente de leão", "garden", "jardim", "lawn", "plant", "backyard", "flower"]):
        env = "backyard garden, lush green lawn, rural botanical setting"
        loc = "outdoors, natural sunlight garden"
        objs = "dandelion plant, green grass, roots, organic soil, wild yellow flowers, fluffy seed heads"
        palette = "earthy green, botanical amber, sunlight gold, deep organic brown"
    elif any(w in txt_lower for w in ["lab", "laboratório", "tech", "futuristic", "tecnologia", "circuit", "holograma"]):
        env = "advanced technology laboratory, sleek modern workspace"
        loc = "indoor high-tech facility"
        objs = "quantum computing consoles, glass screens, clean holographic displays"
        palette = "deep navy blue, neon cyan, polished silver, matte dark grey"
    else:
        env = "cinematic real-world environment"
        loc = "cinematic setting"
        objs = "key storytelling elements"
        palette = "rich cinematic tones, balanced contrast, realistic colors"

    memory_data = {
        "environment": env,
        "location": loc,
        "time": "daylight, golden morning sunlight",
        "lighting": "warm natural sunlight, subtle rim lighting, soft atmospheric diffusion",
        "camera_style": "cinematic 35mm lens, f/1.8 aperture, shallow depth of field, sharp textures, 16:9",
        "objects": objs,
        "color_palette": palette,
        "style_lock": "Photorealistic cinematic still, natural lighting, 35mm lens, shallow depth of field, realistic textures, 16:9",
        "continuity_lock": "Same environment, same visual universe, same lighting style",
        "negative_lock": "different person, different face, duplicate character, wrong clothes, text, logo, watermark, split screen, blurry, cartoonish, low quality, oversaturated, deformed",
    }

    return salvar_memoria_visual(projeto_id, memory_data)
