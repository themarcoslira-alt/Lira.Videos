"""
services/prompt_engine.py — Character & World Locks Prompt Engine
==================================================================
Motor determinístico de geração de prompts estruturados com:
- Character Lock (quando o personagem estiver na cena)
- World & Environment Lock (Memória Visual)
- Style Lock (fotografia, lentes, iluminação)
- Continuity Lock (continuidade cena a cena)
- Negative Lock (filtros anti-deformação e anti-inconsistência)
"""

import re
from typing import Dict, Any, Optional, List
from services.visual_memory_service import obter_memoria_visual
from services.character_service import obter_personagem_ativo


def cena_tem_personagem(texto: str, nome_personagem: Optional[str] = None) -> bool:
    """
    Analisa se a cena realmente possui a presença de um personagem ou narrador,
    diferenciando de cenas de B-roll botânico, paisagem ou objetos.
    """
    if not texto:
        return False

    t = texto.lower()

    # 1. Menção explícita ao nome do personagem
    if nome_personagem and len(nome_personagem.strip()) > 1:
        np = nome_personagem.strip().lower()
        if np in t:
            return True

    # 2. Palavras de ação humana / narrador na câmera
    palavras_humano = [
        "narrador", "narrator", "person", "homem", "mulher", "marcos",
        "talking to camera", "falando para a câmera", "olhando para a câmera",
        "holding", "segurando", "caminhando", "walking", "gesturing",
        "i'm going to walk you through", "today i'll show you", "vou te mostrar"
    ]
    if any(p in t for p in palavras_humano):
        return True

    # 3. Termos puramente botânicos/descritivos de B-roll indicam NÃO-personagem
    palavras_broll = [
        "the root of", "a raiz", "the leaves are", "as folhas", "the flower",
        "yellow flower", "macro shot", "close-up of", "underground", "soil",
        "lawn", "backyard lawn", "dandelion seeds", "sem ninguém"
    ]
    if any(b in t for b in palavras_broll) and not (nome_personagem and nome_personagem.lower() in t):
        return False

    return False


def construir_prompt_cena(projeto_id: str, cena: Dict[str, Any], index: int,
                          total_cenas: int, nome_personagem: str = "",
                          estilo_visual: str = "photorealistic_cinematic") -> Dict[str, str]:
    """
    Gera o prompt estruturado com blocos explícitos:
    REFERENCE CHARACTER / CHARACTER LOCK / SCENE / VISUAL STYLE / CONTINUITY / NEGATIVE.
    """
    cid = cena.get("id", index + 1)
    texto = cena.get("texto", "")
    ts_ini = cena.get("tempo_inicio", 0)
    dur = float(cena.get("duracao", 5.0))

    # Obtém dados do personagem e memória visual
    char_ativo = obter_personagem_ativo(projeto_id)
    char_nome = char_ativo.get("name") if char_ativo else (nome_personagem or "")
    tem_char = cena_tem_personagem(texto, char_nome)

    memoria = obter_memoria_visual(projeto_id)

    # 1. Monta o Prompt de Imagem Estruturado
    blocos = []

    # Timestamp de cabeçalho
    from services.scene_plan_service import _fmt_ts
    ts_label = f"[{_fmt_ts(ts_ini)}]"
    blocos.append(ts_label)

    # Bloco de Personagem (apenas se a cena tiver personagem)
    if tem_char and char_nome:
        blocos.append(f"REFERENCE CHARACTER:\n@{char_nome} reference image.")
        blocos.append("CHARACTER LOCK:\nSame person from reference image.\nSame face.\nSame clothes.\nSame identity.")

    # Bloco de Cena
    blocos.append(f"SCENE:\n{texto}")

    # Bloco de Estilo Visual
    style_desc = memoria.get("style_lock", "Photorealistic cinematic still,\nnatural lighting,\n35mm lens,\nshallow depth of field,\nrealistic textures,\n16:9.")
    blocos.append(f"VISUAL STYLE:\n{style_desc}")

    # Bloco de Continuidade
    continuity_desc = memoria.get("continuity_lock", "Same environment,\nsame visual universe,\nsame lighting style.")
    if index > 0:
        blocos.append(f"CONTINUITY:\n{continuity_desc}")

    # Bloco Negativo
    if tem_char:
        neg_desc = "different person,\ndifferent face,\nduplicate character,\nwrong clothes,\ntext,\nlogo,\nwatermark,\nsplit screen."
    else:
        neg_desc = "text,\nlogo,\nwatermark,\nsplit screen,\ncartoonish,\nlow quality,\ndeformed."
    blocos.append(f"NEGATIVE:\n{neg_desc}")

    prompt_estruturado = "\n\n".join(blocos)

    # 2. Prompt de Animação / Câmera (se for vídeo)
    if dur < 3.0:
        prompt_anim = "Slow cinematic push-in (1.00 -> 1.05), natural camera motion, 3s ease"
    elif dur > 6.0:
        prompt_anim = "Subtle cinematic pan and slow zoom, steady camera, shallow depth of field, 6s ease"
    else:
        prompt_anim = "Smooth Ken Burns motion (1.00 -> 1.04), subtle natural breeze and atmospheric motion"

    return {
        "prompt_imagem": prompt_estruturado,
        "prompt_animacao": prompt_anim,
        "tem_personagem": tem_char,
        "nome_personagem": char_nome if tem_char else "",
    }


class PromptEngine:
    """Classe para compatibilidade legada com v1."""
    @staticmethod
    def gerar_prompt_cena(projeto_id: str, cena: Dict[str, Any], index: int = 0) -> str:
        res = construir_prompt_cena(projeto_id, cena, index, 1)
        return res["prompt_imagem"]
