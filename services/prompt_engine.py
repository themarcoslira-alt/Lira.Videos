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
from services.character_service import obter_personagem_ativo, obter_identidade_projeto


def cena_tem_personagem(cena_ou_texto, nome_personagem=None) -> bool:
    from services.character_service import detectar_presenca_personagem_cena
    if isinstance(cena_ou_texto, dict):
        return detectar_presenca_personagem_cena(cena_ou_texto, str(nome_personagem or ""))
    return detectar_presenca_personagem_cena({"texto": str(cena_ou_texto or "")}, str(nome_personagem or ""))


def substituir_sujeito_por_referencia(texto: str, tag_ref: str, nome_personagem: str = "") -> str:
    """
    Substitui inteligentemente o sujeito principal da frase pela tag de referência (ex: @João, @Maria, @me).
    Regras:
    - 'A man walking through a garden' -> '@João walking through a garden'
    - 'A young woman examining rose plants' -> '@Maria examining rose plants'
    - 'A person standing outdoors' -> '@João standing outdoors'
    - 'An elderly man holding a leaf' -> '@João holding a leaf'
    - 'Um homem caminhando pelo jardim' -> '@João caminhando pelo jardim'
    - 'Uma mulher colhendo flores' -> '@Maria colhendo flores'
    - 'Uma pessoa observando o horizonte' -> '@João observando o horizonte'
    - 'João caminhando pelo jardim' -> '@João caminhando pelo jardim'
    - 'walking through a garden' -> '@João walking through a garden'
    """
    if not tag_ref:
        return texto

    t = texto.strip()
    if not t:
        return f"{tag_ref} in an authentic setting"

    # Se já começa com a tag de referência exata, retorna limpo
    if t.startswith(tag_ref):
        return t

    # 1. Se começa com o nome do personagem (ex: "João mostrando..." -> "@João mostrando...")
    if nome_personagem and re.match(rf'^{re.escape(nome_personagem)}\b', t, flags=re.IGNORECASE):
        return re.sub(rf'^{re.escape(nome_personagem)}\b\s*', f"{tag_ref} ", t, count=1, flags=re.IGNORECASE).strip()

    # 2. Padrões em inglês para sujeito principal
    pattern_en = r'^(an?\s+)?((young|old|elderly|tall|short|bearded|smiling|focused)\s+)?(man|woman|person|guy|girl|boy|male|female|presenter|gardener|farmer|worker|narrator)\s+'
    if re.match(pattern_en, t, flags=re.IGNORECASE):
        return re.sub(pattern_en, f"{tag_ref} ", t, count=1, flags=re.IGNORECASE).strip()

    # 3. Padrões em português para sujeito principal
    pattern_pt = r'^(um[a]?\s+)?((jovem|velho|idoso|alto|baixo|barbudo|sorridente)\s+)?(homem|mulher|pessoa|apresentador|jardineiro|agricultor|narrador)\s+'
    if re.match(pattern_pt, t, flags=re.IGNORECASE):
        return re.sub(pattern_pt, f"{tag_ref} ", t, count=1, flags=re.IGNORECASE).strip()

    # 4. Caso padrão: prefixa a tag no início da ação
    return f"{tag_ref} {t}"


def _gerar_descricao_visual_broll(
    texto_fala: str,
    nome_personagem: str = "",
    referencia_flow: str = "",
    tem_personagem: bool = False
) -> str:
    """
    Transforma a fala narrada em uma descrição visual cinematográfica rica (B-Roll Director).
    Substitui inteligentemente o sujeito principal pela tag de referência (ex: @João ou @me).
    """
    t = texto_fala.lower()
    limpo = re.sub(r'\[\d+:\d+\]', '', texto_fala).strip()

    # Cenas com Identidade (Personagem @Nome ou Avatar @me)
    tag_ref = (referencia_flow or (f"@{nome_personagem.strip()}" if nome_personagem else "")).strip()
    if tem_personagem and tag_ref:
        if any(w in t for w in ["walk", "show", "today", "i ", "my ", "explain", "i'll", "i'm", "look", "started", "noticed", "checked", "vou", "mostrar", "explicar"]):
            base_acao = "standing outdoors in a lush garden environment, engaging realistic posture, looking towards camera, natural daylight, 35mm lens, photorealistic details"
            return f"{tag_ref} {base_acao}"
        elif any(w in t for w in ["holding", "hand", "soil", "banana", "peel", "plant", "rose", "leaves", "segurando", "folhas", "raiz", "terra"]):
            base_acao = "carefully examining rose plants and organic soil in a rustic garden, natural morning sun, highly detailed textures, shallow depth of field"
            return f"{tag_ref} {base_acao}"
        else:
            acao = substituir_sujeito_por_referencia(limpo, tag_ref, nome_personagem)
            return f"{acao}, cinematic framing, natural lighting, realistic textures, photorealistic details"

    # Cenas puramente sem personagem (B-Roll puro e cinematográfico)
    if "root" in t or "raiz" in t or "underground" in t:
        return "Extreme close-up macro shot of a thick plant root covered with rich dark organic soil, resting on weathered rustic wood, soft natural sunlight, sharp details"
    elif "leaves" in t or "folhas" in t or "jagged" in t:
        return "Detailed top-down macro shot of fresh green leaves with morning dew droplets, vibrant natural green textures, earthy garden backdrop"
    elif "flower" in t or "flor" in t or "rose" in t:
        return "Beautiful close-up of a blooming rose flower in lush green foliage, golden hour sunlight glowing through petals, shallow depth of field"
    elif "banana" in t or "peel" in t or "fertilizer" in t:
        return "Close-up macro shot of natural organic banana peel compost resting by the roots in rich dark garden soil, soft morning daylight"
    else:
        return f"Cinematic visual depicting {limpo if limpo else 'scenic landscape'}, natural lighting, realistic textures, balanced composition, 16:9 framing"


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

    # Obtém dados da entidade de personagem configurada para a cena de forma dinâmica
    from services.character_service import obter_personagem_cena
    char_info = obter_personagem_cena(projeto_id, cena)
    if char_info:
        char_nome = char_info.get("nome", "")
        ref_flow = char_info.get("referencia_flow", f"@{char_nome}" if char_nome else "")
    else:
        char_nome = nome_personagem or ""
        ref_flow = f"@{char_nome}" if char_nome else ""

    tem_char = bool(char_info.get("uses_character", False)) if char_info else cena_tem_personagem(cena, char_nome)

    memoria = obter_memoria_visual(projeto_id)

    # 1. Gera descrição de cena cinematográfica inteligente (B-Roll) com @referencia_flow
    descricao_broll = _gerar_descricao_visual_broll(
        texto_fala=texto,
        nome_personagem=char_nome,
        referencia_flow=ref_flow,
        tem_personagem=tem_char
    )

    # 2. Monta o Prompt de Imagem Limpo e Cinematográfico para o Google Flow
    style_desc = memoria.get("style_lock", "Photorealistic cinematic still, natural lighting, 35mm lens, shallow depth of field, realistic textures, 16:9")
    continuity_desc = memoria.get("continuity_lock", "Same environment, same visual universe, same lighting style") if index > 0 else ""
    
    elementos = [descricao_broll, style_desc]
    if continuity_desc:
        elementos.append(continuity_desc)
    
    prompt_limpo = ". ".join(p.strip().rstrip(".") for p in elementos if p.strip()) + "."

    # 3. Prompt de Animação / Câmera (usado APENAS na Fase 2 de Animação de Vídeo)
    if dur < 3.0:
        prompt_anim = "Slow cinematic push-in (1.00 -> 1.05), natural smooth camera motion, 3s ease"
    elif dur > 6.0:
        prompt_anim = "Subtle cinematic slow panning and gentle zoom, steady camera, shallow depth of field, 6s ease"
    else:
        prompt_anim = "Cinematic slow forward motion (1.00 -> 1.04), subtle natural environmental motion"

    return {
        "prompt_imagem": prompt_limpo,
        "prompt_animacao": prompt_anim,
        "descricao_broll": descricao_broll,
        "tem_personagem": tem_char,
        "nome_personagem": char_nome if tem_char else "",
        "referencia_flow": ref_flow if tem_char else "",
    }


class PromptEngine:
    """Classe para compatibilidade legada com v1."""
    @staticmethod
    def gerar_prompt_cena(projeto_id: str, cena: Dict[str, Any], index: int = 0) -> str:
        res = construir_prompt_cena(projeto_id, cena, index, 1)
        return res["prompt_imagem"]
