"""
services/prompt_engine.py — Script & B-Roll Intelligence Engine
================================================================
Transforma transcrições brutas (SRT) em cenas visuais cinematográficas:
- Análise semântica de contexto e B-roll
- Separação rigorosa de prompt_imagem (100% estático) vs prompt_animacao (movimento de câmera)
- Injeção precisa de @NomePersonagem quando houver personagem
- Memória visual e consistência de ambiente
"""

import re
from typing import Dict, Any, Optional, List
from services.visual_memory_service import obter_memoria_visual
from services.character_service import obter_personagem_ativo


def cena_tem_personagem(cena_ou_texto: Any, nome_personagem: Optional[str] = None) -> bool:
    """
    Decisão explícita de presença de personagem:
    Toda cena que tiver o campo nome_personagem preenchido (ou personagem ativo no projeto)
    recebe automaticamente a presença e consistência do personagem.
    """
    if isinstance(cena_ou_texto, dict):
        char_cena = str(cena_ou_texto.get("nome_personagem") or "").strip()
        if char_cena:
            return True
        texto = cena_ou_texto.get("texto", "")
    else:
        texto = str(cena_ou_texto or "")

    if nome_personagem and len(str(nome_personagem).strip()) > 0:
        return True

    if not texto:
        return False

    t = texto.lower()
    if any(h in t for h in ["presenter", "apresentador", "narrator", "narrador", "talking to camera"]):
        return True

    return False


def _gerar_descricao_visual_broll(texto_fala: str, nome_personagem: str = "", tem_personagem: bool = False) -> str:
    """
    Transforma a fala narrada em uma descrição visual cinematográfica rica (B-Roll Director).
    Quando tem_personagem=True, garante a referência @NomePersonagem no prompt.
    """
    t = texto_fala.lower()
    limpo = re.sub(r'\[\d+:\d+\]', '', texto_fala).strip()

    # Cenas com Personagem (Presença Explícita)
    if tem_personagem and nome_personagem:
        char_tag = f"@{nome_personagem.strip()}"
        if any(w in t for w in ["walk", "show", "today", "i ", "my ", "explain", "i'll", "i'm", "look", "started", "noticed", "checked", "vou", "mostrar"]):
            return f"{char_tag} standing outdoors in a lush garden environment, engaging realistic posture, looking towards camera, natural daylight, 35mm lens, photorealistic details"
        elif any(w in t for w in ["holding", "hand", "soil", "banana", "peel", "plant", "rose", "leaves", "segurando", "folhas", "raiz"]):
            return f"{char_tag} carefully examining rose plants and organic soil in a rustic garden, natural morning sun, highly detailed textures, shallow depth of field"
        else:
            return f"{char_tag} in an authentic garden setting, {limpo}, cinematic framing, natural lighting, realistic textures, photorealistic details"

    # Cenas puramente sem personagem (caso não haja personagem cadastrado)
    if "root" in t or "raiz" in t or "underground" in t:
        return "Extreme close-up macro shot of a thick plant root covered with rich dark organic soil, resting on weathered rustic wood, soft natural sunlight, sharp details"
    elif "leaves" in t or "folhas" in t or "jagged" in t:
        return "Detailed top-down macro shot of fresh green leaves with morning dew droplets, vibrant natural green textures, earthy garden backdrop"
    elif "flower" in t or "flor" in t or "rose" in t:
        return "Beautiful close-up of a blooming rose flower in lush green foliage, golden hour sunlight glowing through petals, shallow depth of field"
    elif "banana" in t or "peel" in t or "fertilizer" in t:
        return "Close-up macro shot of natural organic banana peel compost resting by the roots in rich dark garden soil, soft morning daylight"
    else:
        return f"Cinematic visual depicting: {limpo}, natural lighting, realistic textures, balanced composition, 16:9 framing"


def construir_prompt_cena(projeto_id: str, cena: Dict[str, Any], index: int,
                          total_cenas: int, nome_personagem: str = "",
                          estilo_visual: str = "photorealistic_cinematic") -> Dict[str, str]:
    """
    Gera o prompt estruturado com blocos explícitos:
    - prompt_imagem: 100% descritivo visual e estático (sem comandos de movimento).
    - prompt_animacao: comandos de câmera e movimento (usado apenas na Fase 2).
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

    # 1. Gera descrição de cena cinematográfica inteligente (B-Roll)
    descricao_broll = _gerar_descricao_visual_broll(texto, char_nome, tem_char)

    # 2. Monta o Prompt de Imagem Estruturado (100% ESTÁTICO)
    blocos = []

    # Timestamp de cabeçalho
    from services.scene_plan_service import _fmt_ts
    ts_label = f"[{_fmt_ts(ts_ini)}]"
    blocos.append(ts_label)

    # Bloco de Personagem (apenas se a cena tiver personagem)
    if tem_char and char_nome:
        blocos.append(f"REFERENCE CHARACTER:\n@{char_nome} reference image.")
        blocos.append(f"CHARACTER LOCK:\nSame person from @{char_nome} reference image.\nSame face.\nSame clothes.\nSame identity.")

    # Bloco de Cena (Inteligência B-roll)
    blocos.append(f"SCENE:\n{descricao_broll}")

    # Bloco de Estilo Visual (Estático)
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

    # 3. Prompt de Animação / Câmera (usado APENAS na Fase 2 de Animação de Vídeo)
    if dur < 3.0:
        prompt_anim = "Slow cinematic push-in (1.00 -> 1.05), natural smooth camera motion, 3s ease"
    elif dur > 6.0:
        prompt_anim = "Subtle cinematic slow panning and gentle zoom, steady camera, shallow depth of field, 6s ease"
    else:
        prompt_anim = "Cinematic slow forward motion (1.00 -> 1.04), subtle natural environmental motion"

    return {
        "prompt_imagem": prompt_estruturado,
        "prompt_animacao": prompt_anim,
        "descricao_broll": descricao_broll,
        "tem_personagem": tem_char,
        "nome_personagem": char_nome if tem_char else "",
    }


class PromptEngine:
    """Classe para compatibilidade legada com v1."""
    @staticmethod
    def gerar_prompt_cena(projeto_id: str, cena: Dict[str, Any], index: int = 0) -> str:
        res = construir_prompt_cena(projeto_id, cena, index, 1)
        return res["prompt_imagem"]
