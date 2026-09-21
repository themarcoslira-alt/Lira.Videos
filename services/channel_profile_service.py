"""
services/channel_profile_service.py — Channel Intelligence Profile
=======================================================================
Perfil EDITORIAL GLOBAL do canal (camada de conteúdo), complementar ao
Brand Profile TÉCNICO (`brand_profile_service.py` → apresentador/música/
legenda/mix de produção).

Diferença de escopo:
  * brand_profile.json  -> COMO o vídeo é produzido (técnico).
  * channel_profile.json-> COMO o conteúdo é escrito/pensado (editorial):
        nicho, público-alvo, estilo de escrita, estrutura de hook,
        fluxo do roteiro, ritmo de narração, técnicas de retenção,
        contagem de palavras e padrões de monetização.

Estado: SOMENTE PERSISTÊNCIA + GETTERS (nenhum consumo no pipeline ainda —
decisão explícita do usuário). Nada aqui altera o comportamento existente.

Persistência: projetos/channel_profile.json (atômico), com os mesmos padrões
de `brand_profile_service` (defaults, completude retroativa e update aninhado).
"""

import json
import os
from copy import deepcopy
from typing import Dict, Any, Optional, List
from pathlib import Path

from config import PROJETOS_DIR

CHANNEL_PROFILE_FILE = "channel_profile.json"

DEFAULT_CHANNEL_PROFILE: Dict[str, Any] = {
    "channel_name": "Lira Jardinagem",
    "niche": {
        "category": "Jardinagem/paisagismo prático e cuidados com o lar (home & garden care)",
        "focus": "soluções \"mito vs. verdade\" para problemas comuns de plantas e gramado",
        "subniches": [
            "cuidado de gramado (soil science aplicada)",
            "cuidado de ervas/plantas mediterrâneas",
        ],
        "topic_formats": [
            "mito vs. ciência real",
            "erro que todo mundo comete",
        ],
        "title_patterns": [
            "promessa de transformação + número ou intensificador",
            "exemplos: \"100x Stronger\", \"Faster Than Any Fertilizer\"",
        ],
    },
    "audience": {
        "profile": "adultos donos de casa própria, 30-60 anos",
        "situation": ("já tentam cuidar do jardim/gramado mas sentem frustração por "
                      "\"fazer tudo certo\" e não ver resultado"),
        "investment_level": "médio (compram fertilizante e produtos de jardinagem)",
        "emotional_state": {
            "antes": "frustração + busca de validação (\"por que não está funcionando comigo?\")",
            "depois": "alívio/esperança quando a causa raiz é revelada",
        },
    },
    "writing_style": {
        "tone": "conversacional, direto, \"professor confiável de bairro\"",
        "vocabulary": ("acessível, zero jargão sem explicação — quando usa termo técnico, "
                       "explica na sequência"),
        "technical_terms_examples": ["cation exchange", "thigmomorphogenesis"],
        "sentence_structure": "frases curtas intercaladas com explicações mais longas",
        "person": "primeira pessoa forte",
        "social_proof": "storytelling pessoal como prova social",
    },
    "hook": {
        "window_seconds": 30,
        "patterns": [
            "dor/frustração relacionável e específica (\"Você já tentou X e não funcionou\")",
            "confissão pessoal chocante (\"Eu matei sete plantas de alecrim\")",
            "números concretos logo no início",
            "promessa de revelar a causa raiz escondida (\"o que ninguém te conta\")",
        ],
        "open_loop": "promessa não resolvida até o meio do vídeo",
        "subscribe_cta_after_hook": True,
        "subscribe_cta_position": "logo após o hook, antes do conteúdo principal",
    },
    # Fluxo canônico do roteiro (8 blocos, na ordem)
    "script_flow": [
        {"step": 1, "id": "hook", "desc": "Hook de dor/confissão pessoal"},
        {"step": 2, "id": "cta_inscricao", "desc": "CTA de inscrição rápido"},
        {"step": 3, "id": "mitos", "numbered": True,
         "desc": "Desconstrução de mitos (\"primeiro mito... segundo mito...\")"},
        {"step": 4, "id": "explicacao_cientifica",
         "desc": "Explicação científica simplificada do \"porquê real\""},
        {"step": 5, "id": "passos", "numbered": True, "cue": "Step 1, Step 2...",
         "desc": "Passo a passo prático numerado"},
        {"step": 6, "id": "case_real",
         "desc": "Case real/prova pessoal (\"testei isso na minha propriedade...\")"},
        {"step": 7, "id": "erros_comuns", "numbered": True,
         "desc": "Erros comuns a evitar (numerados)"},
        {"step": 8, "id": "recapitulacao_cta",
         "cta_cascade": ["like", "comente", "compartilhe", "inscreva-se"],
         "desc": "Recapitulação + CTA final em cascata"},
    ],
    "narration_rhythm": {
        "sentence_length": "frases curtas e médias predominam",
        "cadence": ("quase de \"batida\" — várias frases de 3-6 palavras em sequência "
                    "para dar ênfase"),
        "emphasis_example": "Grass needs nitrogen, potassium, phosphorus.",
        "rhetorical_pauses": "pausas retóricas marcadas por perguntas diretas ao espectador",
        "structural_repetition": "repetição estrutural (listas numeradas de mitos/erros/passos)",
    },
    "retention_techniques": [
        {"id": "open_loop", "desc": "Loop aberto no hook (promessa não resolvida até o meio)"},
        {"id": "mito_counter_intuitivo",
         "desc": "Correção de mito (contra-intuitivo = gancho de atenção)"},
        {"id": "callback_final", "desc": "Callback ao problema inicial no fechamento"},
        {"id": "prova_social_pessoal", "desc": "Prova social pessoal (case study com números reais)"},
        {"id": "pergunta_retorica",
         "desc": "Perguntas retóricas diretas (\"Wait, that sounds good, right? Wrong.\")"},
        {"id": "cta_comentario_especifico",
         "desc": "CTA de comentário com pergunta específica (zip code, tipo de planta) "
                 "para gerar engajamento mensurável"},
    ],
    "word_count": {
        "examples": {"gypsum": 6384, "rosemary": 4591},
        "average": 5487,
        "narration_minutes_range": [18, 22],
        "note": "~5.487 palavras ≈ 18-22 minutos de narração em ritmo natural",
    },
    "content_patterns": {
        "format": "mito vs. ciência real / erro que todo mundo comete",
        "ending_pattern": "guia prático replicável",
        "monetization": {
            "soft_sell": "venda soft de produtos (links na descrição)",
            "lead_magnet": "ebook",
        },
    },
}

# Campos obrigatórios verificados no load (compatibilidade retroativa)
_CAMPOS_OBRIGATORIOS = (
    "channel_name", "niche", "audience", "writing_style", "hook", "script_flow",
    "narration_rhythm", "retention_techniques", "word_count", "content_patterns",
)


def _caminho(base_dir=None) -> Path:
    base = Path(base_dir) if base_dir else PROJETOS_DIR
    return base / CHANNEL_PROFILE_FILE


def default_channel_profile(channel_name: str = "Lira Jardinagem") -> Dict[str, Any]:
    """Perfil editorial padrão (cópia profunda — nunca muta a constante)."""
    p = deepcopy(DEFAULT_CHANNEL_PROFILE)
    if channel_name:
        p["channel_name"] = channel_name
    return p


def _complete_faltantes(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Preenche campos obrigatórios ausentes com o default (sem perder extras)."""
    default = default_channel_profile(profile.get("channel_name"))
    for k in _CAMPOS_OBRIGATORIOS:
        if k not in profile:
            profile[k] = deepcopy(default[k])
    return profile


def save_channel_profile(profile: Optional[Dict[str, Any]] = None, base_dir=None) -> bool:
    """Persiste o perfil em <base_dir>/channel_profile.json (atômico)."""
    data = profile if profile is not None else default_channel_profile()
    path = _caminho(base_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(path))
        return True
    except Exception:
        return False


def load_channel_profile(base_dir=None) -> Dict[str, Any]:
    """Carrega o perfil editorial (criando o default se o arquivo não existir)."""
    path = _caminho(base_dir)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return _complete_faltantes(data)
        except Exception:
            pass
    return create_default_channel_profile(base_dir=base_dir)


def create_default_channel_profile(channel_name: str = "Lira Jardinagem",
                                   base_dir=None) -> Dict[str, Any]:
    """Cria o perfil padrão se não existir; caso exista, apenas retorna."""
    path = _caminho(base_dir)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return _complete_faltantes(data)
        except Exception:
            pass
    profile = default_channel_profile(channel_name)
    save_channel_profile(profile, base_dir=base_dir)
    return profile


def _set_nested(d: Dict[str, Any], path: str, value) -> None:
    keys = path.split(".")
    cur = d
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


def update_channel_profile(field: str, value, base_dir=None) -> bool:
    """Atualiza um campo específico (suporta caminho aninhado 'a.b.c')."""
    profile = load_channel_profile(base_dir=base_dir)
    _set_nested(profile, field, value)
    return save_channel_profile(profile, base_dir=base_dir)


# ---------------------------------------------------------------------------
# GETTERS (somente leitura — NENHUM consumo no pipeline ainda)
# ---------------------------------------------------------------------------
def get_niche(base_dir=None) -> Dict[str, Any]:
    """Nicho/categoria, subnichos e formatos de tópico."""
    return load_channel_profile(base_dir=base_dir).get("niche") or {}


def get_audience(base_dir=None) -> Dict[str, Any]:
    """Público-alvo (perfil, situação, investimento, estado emocional)."""
    return load_channel_profile(base_dir=base_dir).get("audience") or {}


def get_writing_style(base_dir=None) -> Dict[str, Any]:
    """Tom, vocabulário, estrutura de frase, pessoa e prova social."""
    return load_channel_profile(base_dir=base_dir).get("writing_style") or {}


def get_hook_structure(base_dir=None) -> Dict[str, Any]:
    """Estrutura do hook (janela em segundos, padrões, loop aberto, CTA)."""
    return load_channel_profile(base_dir=base_dir).get("hook") or {}


def get_script_flow(base_dir=None) -> List[Dict[str, Any]]:
    """Fluxo canônico do roteiro (lista ordenada dos 8 blocos)."""
    return load_channel_profile(base_dir=base_dir).get("script_flow") or []


def get_flow_step(step: int, base_dir=None) -> Dict[str, Any]:
    """Um bloco do fluxo pelo número do passo (1..8); {} se não existir."""
    for item in get_script_flow(base_dir=base_dir):
        if int(item.get("step", 0)) == int(step):
            return item
    return {}


def get_narration_rhythm(base_dir=None) -> Dict[str, Any]:
    """Ritmo de narração (tamanho de frase, cadência, pausas, repetição)."""
    return load_channel_profile(base_dir=base_dir).get("narration_rhythm") or {}


def get_retention_techniques(base_dir=None) -> List[Dict[str, Any]]:
    """Técnicas de retenção declaradas (id + descrição)."""
    return load_channel_profile(base_dir=base_dir).get("retention_techniques") or []


def get_word_count_target(base_dir=None) -> int:
    """Contagem MÉDIA de palavras alvo por vídeo (0 se ausente)."""
    return int((load_channel_profile(base_dir=base_dir).get("word_count") or {})
               .get("average") or 0)


def get_word_count_examples(base_dir=None) -> Dict[str, int]:
    """Exemplos reais de contagem por vídeo ({nome: palavras})."""
    return ((load_channel_profile(base_dir=base_dir).get("word_count") or {})
            .get("examples") or {})


def get_narration_minutes_range(base_dir=None) -> List[int]:
    """Faixa de duração esperada da narração em minutos (ex.: [18, 22])."""
    return ((load_channel_profile(base_dir=base_dir).get("word_count") or {})
            .get("narration_minutes_range") or [])


def get_content_patterns(base_dir=None) -> Dict[str, Any]:
    """Formato do conteúdo, padrão de fechamento e monetização."""
    return load_channel_profile(base_dir=base_dir).get("content_patterns") or {}


def get_editorial_profile(base_dir=None) -> Dict[str, Any]:
    """Bloco editorial completo (nicho/estilo/hook/fluxo/ritmo/retenção)."""
    p = load_channel_profile(base_dir=base_dir)
    return {k: p.get(k) for k in _CAMPOS_OBRIGATORIOS if k != "channel_name"}


