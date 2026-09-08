"""
services/srt_tag_service.py — Tags de cena do SRT Melhorado (TESTE 007)
=======================================================================
O SRT MELHORADO anota cada bloco com uma TAG de tipo de cena
([AVATAR_TALKING], [AVATAR_ACTION_HANDS|PROCESS|APPLICATION],
[BROLL_STORY_START], [DIAGNOSTIC_PROCESS], [RESULTS_DAY_BY_DAY],
[CTA_LIKE], [CTA_SUBSCRIBE], [WHAT_YOU_WILL_LEARN] etc.).

Responsabilidades:
  - parsear SRT padrão preservando a TAG por bloco;
  - devolver dicas de cena (scene_type/avatar/lip-sync) para o Visual Director;
  - mapear a TAG para o campo novo `srt_tag` no scene_plan.
"""
import re
from typing import Dict, Any, List, Optional
from pathlib import Path

# ---------------------------------------------------------------------------
# Vocabulário canônico (23 tags do TESTE_007_MELHORADO.srt)
# ---------------------------------------------------------------------------

TAGS_AVATAR_TALKING = {
    "AVATAR_TALKING", "WHAT_YOU_WILL_LEARN", "SOLUTION_REVEAL", "HONEST_ANALYSIS",
    "IMPORTANT_DISCLAIMER", "KEY_LESSON", "OBSERVATION_PHILOSOPHY",
    "CHECKLIST_BASICS", "LESSON_SUMMARY", "QUICK_RECAP",
}

TAGS_AVATAR_ACTION = {
    "AVATAR_ACTION_HANDS", "AVATAR_ACTION_PROCESS", "AVATAR_ACTION_APPLICATION",
}

TAGS_BROLL = {
    "BEFORE_AFTER_COMPARISON", "BROLL_STORY_START", "DIAGNOSTIC_PROCESS",
    "AVOID_MISTAKES", "RESULTS_DAY_BY_DAY", "BEFORE_AFTER_RESULTS",
}

TAGS_CTA = {"CTA_LIKE", "CTA_SUBSCRIBE"}
TAGS_AVATAR_EXTRA = {"ENGAGEMENT", "CHANNEL_INFO"}
TAGS_NEUTRO = {"NUTRITION_ANALYSIS"}  # explicação com ou sem avatar (posição decide)

TODAS_TAGS = (TAGS_AVATAR_TALKING | TAGS_AVATAR_ACTION | TAGS_BROLL
              | TAGS_CTA | TAGS_AVATAR_EXTRA | TAGS_NEUTRO)

_REG_TAG = re.compile(r"^\[([A-Z][A-Z0-9_]*)\]\s*$")
_REG_CUE = re.compile(
    r"(?P<idx>\d+)\s*\n"
    r"\s*(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2}),(?P<ms>\d{3})\s*-->\s*"
    r"(?P<h2>\d{2}):(?P<m2>\d{2}):(?P<s2>\d{2}),(?P<ms2>\d{3})\s*\n"
    r"(?P<corpo>.*?)(?=\n\s*\n|\Z)",
    re.DOTALL,
)


def _para_segundos(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt_cues(texto_srt: str) -> List[Dict[str, Any]]:
    """Lê um SRT padrão e retorna cues [{index,start,end,text,srt_tag}]."""
    cues = []
    for m in _REG_CUE.finditer(texto_srt or ""):
        corpo = m.group("corpo").strip()
        linhas = [l.strip() for l in corpo.splitlines() if l.strip()]
        srt_tag = None
        if linhas and _REG_TAG.match(linhas[0]):
            srt_tag = _REG_TAG.match(linhas[0]).group(1)
            linhas = linhas[1:]
        cues.append({
            "index": int(m.group("idx")),
            "start": round(_para_segundos(m.group("h"), m.group("m"), m.group("s"), m.group("ms")), 3),
            "end": round(_para_segundos(m.group("h2"), m.group("m2"), m.group("s2"), m.group("ms2")), 3),
            "text": " ".join(linhas).strip(),
            "srt_tag": srt_tag,
        })
    return cues


def ler_srt_caminho(caminho) -> List[Dict[str, Any]]:
    p = Path(caminho)
    return parse_srt_cues(p.read_text(encoding="utf-8-sig"))

def categoria_tag(tag: Optional[str]) -> str:
    """Retorna: avatar | avatar_acao | broll | cta | neutro | '' (sem tag)."""
    if not tag:
        return ""
    if tag in TAGS_AVATAR_ACTION:
        return "avatar_acao"
    if tag in (TAGS_AVATAR_TALKING | TAGS_AVATAR_EXTRA):
        return "avatar"
    if tag in TAGS_CTA:
        return "cta"
    if tag in TAGS_BROLL:
        return "broll"
    if tag in TAGS_NEUTRO:
        return "neutro"
    return ""


def dica_cena_por_tag(tag: Optional[str]) -> Dict[str, Any]:
    """Dica declarativa (para o Visual Director) a partir da TAG."""
    categoria = categoria_tag(tag)
    base = {
        "srt_tag": tag,
        "categoria_tag": categoria,
        "lip_sync_needed": False,
        "hint_scene_type": None,
        "hint_uses_character": None,
        "hint_avatar_role": None,
        "hint_camera": None,
    }
    if categoria == "avatar":
        base.update({
            "lip_sync_needed": True,
            "hint_scene_type": "avatar_talking",
            "hint_uses_character": True,
            "hint_camera": "medium shot, direct to camera",
        })
    elif categoria == "avatar_acao":
        base.update({
            "lip_sync_needed": False,      # mãos/corpo — sem lip-sync
            "hint_scene_type": "avatar_action",
            "hint_uses_character": True,
            "hint_avatar_role": "AÇÃO",
            "hint_camera": "close-up on the hands/action, face out of frame",
        })
    elif categoria == "broll":
        base.update({
            "lip_sync_needed": False,
            "hint_scene_type": "broll_macro",   # conteúdo puro — nunca avatar
            "hint_uses_character": False,
            "hint_avatar_role": None,
        })
    elif categoria == "cta":
        base.update({
            "lip_sync_needed": True,
            "hint_scene_type": "avatar_talking",
            "hint_uses_character": True,
            "hint_avatar_role": "CTA",
            "hint_camera": "medium shot, direct to camera",
        })
    elif categoria == "neutro":
        base.update({"hint_scene_type": "broll_macro", "hint_uses_character": False})
    return base


def tag_mais_restritiva(cue_tags: List[Optional[str]]) -> Optional[str]:
    """Agrupa cues: escolhe a tag mais restritiva (broll > cta > acao > avatar)."""
    prioridade = {"broll": 3, "cta": 2, "avatar_acao": 1, "avatar": 1}
    melhor, melhor_prio = None, -1
    for t in cue_tags:
        prio = prioridade.get(categoria_tag(t), 0)
        if prio > melhor_prio:
            melhor, melhor_prio = t, prio
    return melhor
