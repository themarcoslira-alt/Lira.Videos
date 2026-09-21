r"""
story_card_detector_service.py — Lira Studio Story Cards Intelligence
=====================================================================
Detecta padrões narrativos e pontos de transição pedagógica em roteiros e
carimba marcadores estruturados de Story Cards em cada cena.

Padrões suportados:
  1. STEP: "Step (one..five|\d+)" ou "Passo (\d+)" -> Style: step_intro
  2. MYTH: "(first..fifth|\d+)\s+myth" ou "Myth (\d+)" -> Style: vox_editorial / myth
  3. DATA_VIZ: "$XX", "XX%", "XX ton/tons/pounds/lbs/kg" -> Style: data_viz
  4. TIMELINE: "week \d+", "day \d+", "\d+ weeks/days/months" -> Style: timeline
  5. ALERT_WARNING: "mistake (one..five|\d+)", "alerta", "warning" -> Style: alert_warning
"""

import re
from typing import Dict, List, Any, Optional

NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "primeiro": 1, "segundo": 2, "terceiro": 3, "quarto": 4, "quinto": 5,
    "um": 1, "dois": 2, "tres": 3, "três": 3, "quatro": 4, "cinco": 5,
}

# Regex patterns
RE_STEP = re.compile(r"\b(?:step|passo)\s+(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\b", re.IGNORECASE)
RE_MYTH = re.compile(r"\b(?:(first|second|third|fourth|fifth|\d+)\s+(?:myth|mito)|(?:myth|mito)\s+(one|two|three|four|five|\d+))\b", re.IGNORECASE)
RE_MISTAKE = re.compile(r"\b(?:mistake|erro)\s+(one|two|three|four|five|\d+)\b", re.IGNORECASE)
RE_ALERT = re.compile(r"\b(?:warning|caution|alerta|cuidado|danger|perigo)\b", re.IGNORECASE)
RE_DATA = re.compile(r"(\$\s*\d+(?:\s*(?:to|-)\s*\$?\s*\d+)?|\b\d+(?:[.,]\d+)?\s*\%|\b\d+(?:[.,]\d+)?\s*(?:tons?|toneladas?|pounds?|lbs?|kg|quilogramas?|ppm)\b)", re.IGNORECASE)
RE_TIMELINE = re.compile(r"(\b(?:week|day|month|semana|dia|mês)\s*\d+\b|\b\d+\s*(?:to|-)\s*\d+\s*(?:weeks?|days?|months?|semanas?|dias?|meses?)\b|\b\d+\s*(?:weeks?|days?|months?|semanas?|dias?|meses?)\b)", re.IGNORECASE)

COLOR_PALETTES = {
    "step_1": {"bg": "#E6F1FB", "title_color": "#185FA5", "icon": "beaker"},
    "step_2": {"bg": "#EAF3DE", "title_color": "#2B6E3F", "icon": "spreader"},
    "step_3": {"bg": "#E1F5EE", "title_color": "#1D6F60", "icon": "droplet"},
    "step_4": {"bg": "#EEEDFE", "title_color": "#533AB7", "icon": "clock"},
    "step_5": {"bg": "#FAECE7", "title_color": "#9E3A20", "icon": "sparkles"},
    "myth": {"bg": "#FDF2F4", "title_color": "#C5221F", "icon": "alert-circle"},
    "vox_editorial": {"bg": "#F8F9FA", "title_color": "#1A73E8", "icon": "bookmark"},
    "data_viz": {"bg": "#F1F3F4", "title_color": "#137333", "icon": "trending-up"},
    "timeline": {"bg": "#F6F8FA", "title_color": "#1F2328", "icon": "calendar"},
    "alert_warning": {"bg": "#FEF7E0", "title_color": "#B06000", "icon": "alert-triangle"},
}


def _parse_num(val: str) -> int:
    if not val:
        return 1
    v = val.lower().strip()
    if v in NUM_WORDS:
        return NUM_WORDS[v]
    try:
        return int(v)
    except (ValueError, TypeError):
        return 1


def _extract_subtitle(text: str, match_end: int, max_words: int = 6) -> str:
    """Extrai uma frase/subtítulo curta logo após o match."""
    remainder = text[match_end:].strip(" :,-.")
    if not remainder:
        return ""
    # Pega até a primeira pontuação forte ou max_words
    parts = re.split(r"[.!?\n;]", remainder)
    first_chunk = parts[0].strip()
    words = first_chunk.split()
    if len(words) > max_words:
        return " ".join(words[:max_words])
    return first_chunk


def detect_story_cards(scene_plan_or_scenes: Any) -> List[Dict[str, Any]]:
    """
    Analisa as cenas e retorna uma lista de marcadores StoryCardMarker.
    
    Aceita tanto um dict de scene_plan (com chave 'cenas') quanto uma lista de cenas.
    Retorna lista de dicionários com:
      - scene_id
      - is_story_card (bool)
      - story_card_type (str)
      - story_card_style (str)
      - narration_fragment (str)
      - timecode_start (float)
      - timecode_end (float)
      - confidence (float)
      - story_card_config (dict)
    """
    if isinstance(scene_plan_or_scenes, dict):
        cenas = scene_plan_or_scenes.get("cenas", [])
    elif isinstance(scene_plan_or_scenes, list):
        cenas = scene_plan_or_scenes
    else:
        cenas = []

    markers: List[Dict[str, Any]] = []

    for c in cenas:
        cid = int(c.get("id") or c.get("scene_index") or 0)
        texto = str(c.get("narration") or c.get("texto") or c.get("texto_transcricao") or "").strip()
        t_start = float(c.get("tempo_inicio") or c.get("start") or 0.0)
        t_end = float(c.get("tempo_fim") or c.get("end") or (t_start + float(c.get("duracao", 5.0))))

        if not texto:
            continue

        marker = None

        # 1. Padrão STEP
        m_step = RE_STEP.search(texto)
        if m_step:
            num = _parse_num(m_step.group(1))
            c_type = f"step_{num}"
            style = "step_intro"
            palette = COLOR_PALETTES.get(c_type, COLOR_PALETTES["step_1"])
            subtitle = _extract_subtitle(texto, m_step.end()) or f"Passo {num}"
            marker = {
                "scene_id": cid,
                "is_story_card": True,
                "story_card_type": c_type,
                "story_card_style": style,
                "narration_fragment": m_step.group(0),
                "timecode_start": t_start,
                "timecode_end": t_end,
                "confidence": 0.98,
                "story_card_config": {
                    "title": f"STEP {num}",
                    "subtitle": subtitle,
                    "background_color": palette["bg"],
                    "title_color": palette["title_color"],
                    "icon_type": palette["icon"],
                    "duration_ms": 3000,
                    "animation_in": "fade_in",
                    "animation_out": "fade_out",
                },
                "story_card_sync_timecode": 0.0,
                "story_card_position": "before"
            }

        # 2. Padrão MYTH
        if not marker:
            m_myth = RE_MYTH.search(texto)
            if m_myth:
                n_str = m_myth.group(1) or m_myth.group(2)
                num = _parse_num(n_str)
                c_type = f"myth_{num}"
                style = "vox_editorial"
                palette = COLOR_PALETTES["myth"]
                subtitle = _extract_subtitle(texto, m_myth.end()) or f"Mito comum #{num}"
                marker = {
                    "scene_id": cid,
                    "is_story_card": True,
                    "story_card_type": c_type,
                    "story_card_style": style,
                    "narration_fragment": m_myth.group(0),
                    "timecode_start": t_start,
                    "timecode_end": t_end,
                    "confidence": 0.95,
                    "story_card_config": {
                        "title": f"MYTH {num}",
                        "subtitle": subtitle,
                        "background_color": palette["bg"],
                        "title_color": palette["title_color"],
                        "icon_type": palette["icon"],
                        "duration_ms": 3000,
                        "animation_in": "fade_in",
                        "animation_out": "fade_out",
                    },
                    "story_card_sync_timecode": 0.0,
                    "story_card_position": "before"
                }

        # 3. Padrão MISTAKE / ALERT
        if not marker:
            m_mis = RE_MISTAKE.search(texto)
            if m_mis:
                num = _parse_num(m_mis.group(1))
                c_type = f"alert_{num}"
                style = "alert_warning"
                palette = COLOR_PALETTES["alert_warning"]
                subtitle = _extract_subtitle(texto, m_mis.end()) or f"Erro crítico #{num}"
                marker = {
                    "scene_id": cid,
                    "is_story_card": True,
                    "story_card_type": c_type,
                    "story_card_style": style,
                    "narration_fragment": m_mis.group(0),
                    "timecode_start": t_start,
                    "timecode_end": t_end,
                    "confidence": 0.92,
                    "story_card_config": {
                        "title": f"MISTAKE {num}",
                        "subtitle": subtitle,
                        "background_color": palette["bg"],
                        "title_color": palette["title_color"],
                        "icon_type": palette["icon"],
                        "duration_ms": 3000,
                        "animation_in": "fade_in",
                        "animation_out": "fade_out",
                    },
                    "story_card_sync_timecode": 0.0,
                    "story_card_position": "before"
                }
            else:
                m_alert = RE_ALERT.search(texto)
                if m_alert:
                    style = "alert_warning"
                    palette = COLOR_PALETTES["alert_warning"]
                    subtitle = _extract_subtitle(texto, m_alert.end()) or "Aviso importante"
                    frag = m_alert.group(0).upper()
                    marker = {
                        "scene_id": cid,
                        "is_story_card": True,
                        "story_card_type": "alert_warning",
                        "story_card_style": style,
                        "narration_fragment": m_alert.group(0),
                        "timecode_start": t_start,
                        "timecode_end": t_end,
                        "confidence": 0.90,
                        "story_card_config": {
                            "title": frag,
                            "subtitle": subtitle,
                            "background_color": palette["bg"],
                            "title_color": palette["title_color"],
                            "icon_type": palette["icon"],
                            "duration_ms": 3000,
                            "animation_in": "fade_in",
                            "animation_out": "fade_out",
                        },
                        "story_card_sync_timecode": 0.0,
                        "story_card_position": "before"
                    }

        # 4. Padrão DATA_VIZ
        if not marker:
            m_data = RE_DATA.search(texto)
            if m_data:
                fragment = m_data.group(0).strip()
                style = "data_viz"
                palette = COLOR_PALETTES["data_viz"]
                marker = {
                    "scene_id": cid,
                    "is_story_card": True,
                    "story_card_type": "data_viz",
                    "story_card_style": style,
                    "narration_fragment": fragment,
                    "timecode_start": t_start,
                    "timecode_end": t_end,
                    "confidence": 0.85,
                    "story_card_config": {
                        "title": fragment.upper(),
                        "subtitle": _extract_subtitle(texto, m_data.end(), max_words=5),
                        "background_color": palette["bg"],
                        "title_color": palette["title_color"],
                        "icon_type": palette["icon"],
                        "duration_ms": 2500,
                        "animation_in": "fade_in",
                        "animation_out": "fade_out",
                    },
                    "story_card_sync_timecode": 0.0,
                    "story_card_position": "before"
                }

        # 5. Padrão TIMELINE
        if not marker:
            m_time = RE_TIMELINE.search(texto)
            if m_time:
                fragment = m_time.group(0).strip()
                style = "timeline"
                palette = COLOR_PALETTES["timeline"]
                marker = {
                    "scene_id": cid,
                    "is_story_card": True,
                    "story_card_type": "timeline",
                    "story_card_style": style,
                    "narration_fragment": fragment,
                    "timecode_start": t_start,
                    "timecode_end": t_end,
                    "confidence": 0.82,
                    "story_card_config": {
                        "title": fragment.upper(),
                        "subtitle": "Progressão estimada",
                        "background_color": palette["bg"],
                        "title_color": palette["title_color"],
                        "icon_type": palette["icon"],
                        "duration_ms": 2500,
                        "animation_in": "fade_in",
                        "animation_out": "fade_out",
                    },
                    "story_card_sync_timecode": 0.0,
                    "story_card_position": "before"
                }

        if marker:
            markers.append(marker)

    return markers


def apply_story_cards_to_plan(scene_plan: dict) -> dict:
    """
    Aplica marcadores de Story Cards em todas as cenas do scene_plan.
    Gera também o prompt de imagem específico via story_card_prompt_builder.
    """
    try:
        from services.story_card_prompt_builder import build_story_card_prompt
    except ImportError:
        from story_card_prompt_builder import build_story_card_prompt

    markers = detect_story_cards(scene_plan)
    markers_by_id = {m["scene_id"]: m for m in markers}

    cenas = scene_plan.get("cenas", [])
    for c in cenas:
        cid = int(c.get("id") or c.get("scene_index") or 0)
        marker = markers_by_id.get(cid)
        if marker:
            c["is_story_card"] = True
            c["story_card_type"] = marker["story_card_type"]
            c["story_card_style"] = marker["story_card_style"]
            c["story_card_config"] = marker["story_card_config"]
            c["story_card_sync_timecode"] = marker["story_card_sync_timecode"]
            c["story_card_position"] = marker["story_card_position"]
            c["story_card_prompt_imagem"] = build_story_card_prompt(marker)
        else:
            if "is_story_card" not in c:
                c["is_story_card"] = False
                c["story_card_type"] = ""
                c["story_card_style"] = ""
                c["story_card_config"] = None
                c["story_card_prompt_imagem"] = ""
                c["story_card_sync_timecode"] = 0.0
                c["story_card_position"] = "before"

    return scene_plan
