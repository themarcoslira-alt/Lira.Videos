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


def cena_tem_personagem(texto: str, nome_personagem: Optional[str] = None) -> bool:
    """
    Analisa se a cena realmente possui a presença do personagem/narrador em tela,
    diferenciando de cenas descritivas de B-roll botânico, close-ups de objetos ou cenários.
    """
    if not texto:
        return False

    t = texto.lower()

    # 1. Menção explícita ao nome do personagem
    if nome_personagem and len(nome_personagem.strip()) > 1:
        np = nome_personagem.strip().lower()
        if np in t:
            return True

    # 2. Indicadores de narrador em tela / apresentação
    palavras_apresentador = [
        "today i'm going to", "i will show you", "i'm going to walk you through",
        "today i'll show you", "let me show you", "hoje eu vou", "vou te mostrar",
        "talking to camera", "falando para a câmera", "olhando para a câmera",
        "narrador", "narrator", "presenter", "apresentador", "holding the plant"
    ]
    if any(p in t for p in palavras_apresentador):
        return True

    # 3. Se contiver termos claramente botânicos / B-roll, NÃO é personagem
    palavras_broll = [
        "the root of", "a raiz", "the leaves are", "as folhas", "the flower",
        "yellow flower", "macro shot", "close-up", "underground", "soil",
        "milky sap", "sap inside the stem", "dandelion seeds", "fluffy seed head",
        "someone in some backyard is yanking", "dandelion isn't native",
        "botanists believe", "dent de lion", "lion's tooth", "roasted and ground",
        "dandelion root coffee", "herbalists", "edible", "vitamin a", "salads"
    ]
    if any(b in t for b in palavras_broll) and not (nome_personagem and nome_personagem.lower() in t):
        return False

    # 4. Ações humanas genéricas no roteiro
    if any(h in t for h in ["person", "homem", "mulher", "caminhou", "walking", "gesturing"]):
        return True

    return False


def _gerar_descricao_visual_broll(texto_fala: str, nome_personagem: str = "", tem_personagem: bool = False) -> str:
    """
    Transforma a fala narrada em uma descrição visual cinematográfica rica (B-Roll Director).
    """
    t = texto_fala.lower()

    # Cenas com personagem
    if tem_personagem and nome_personagem:
        if "walk you through" in t or "today" in t or "show you" in t or "explain" in t:
            return f"@{nome_personagem} standing in a lush green backyard garden holding a fresh dandelion plant, natural daylight, friendly expression, looking towards camera, rustic outdoor setting"
        elif "holding" in t or "hand" in t or "segurando" in t:
            return f"@{nome_personagem} carefully examining a flowering plant in both hands, outdoors in the morning sun, realistic textures, shallow depth of field"
        elif "walking" in t or "caminhou" in t or "backyard" in t:
            return f"@{nome_personagem} walking along a stone garden pathway surrounded by wild flowers and grass, soft morning sunlight"
        else:
            return f"@{nome_personagem} in a natural garden environment, engaging realistic posture, cinematic framing, photorealistic details"

    # Cenas de B-Roll Botânico / Natureza / Objetos
    if "root" in t or "raiz" in t or "underground" in t:
        return "Extreme close-up macro shot of a thick dandelion taproot covered with rich dark organic soil, resting on weathered rustic wood, soft natural sunlight, sharp details"
    elif "leaves" in t or "folhas" in t or "jagged" in t or "tooth" in t or "edible" in t:
        return "Detailed top-down macro shot of fresh jagged green dandelion leaves with morning dew droplets, vibrant natural green textures, earthy garden backdrop"
    elif "flower" in t or "flor" in t or "yellow" in t or "amarela" in t:
        return "Beautiful close-up of a bright yellow dandelion blossom fully opened in lush green grass, golden hour sunlight glowing through petals, shallow depth of field"
    elif "fluffy" in t or "seed" in t or "semente" in t or "wind" in t or "white" in t:
        return "Macro shot of a delicate white fluffy dandelion seed head in the soft breeze, individual floating seeds backlit by morning sun, ethereal atmosphere"
    elif "sap" in t or "stem" in t or "milky" in t or "leitoso" in t or "caule" in t:
        return "Macro close-up of a broken green dandelion stem showing a small drop of white milky sap, crisp focus, natural outdoor lighting"
    elif "coffee" in t or "cup" in t or "drink" in t or "roasted" in t or "tea" in t:
        return "Warm rustic table setting with a steaming ceramic cup of dark herbal dandelion root coffee next to dried roots and plants, morning window light"
    elif "salad" in t or "kitchen" in t or "dish" in t or "garlic" in t or "olive oil" in t:
        return "Artisanal wooden bowl with fresh wild dandelion greens, sliced lemons, and olive oil dressing on a country kitchen table, warm ambient lighting"
    elif "lawn" in t or "backyard" in t or "garden" in t or "yanking" in t or "ground" in t:
        return "Cinematic eye-level shot of a lush green lawn sprinkled with bright yellow wild dandelion flowers, rustic stone wall in the soft background, golden daylight"
    elif "monastery" in t or "medieval" in t or "centuries" in t or "history" in t:
        return "Historic stone monastery garden with neatly planted medicinal herbs and wild botanicals, atmospheric sunbeams breaking through morning mist"
    else:
        # Fallback descritivo cinematográfico baseado na fala
        limpo = re.sub(r'\[\d+:\d+\]', '', texto_fala).strip()
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
