"""
story_card_prompt_builder.py — Lira Studio Story Cards Prompt Intelligence
==========================================================================
Gera prompts visuais descritivos e estruturados para renderização de Story Cards
educacionais flat 2D (estilo Kurzgesagt / Vox Editorial / Minimalist Infographic)
via DeepSeek / Google Flow.
"""

from typing import Dict, Any, Optional

STEP_PALETTES = {
    "step_1": {
        "bg_name": "soft cream-yellow",
        "bg_hex": "#F0EDB8",
        "title_color_name": "black",
        "title_color_hex": "#000000",
        "accent": "#000000",
    },
    "step_2": {
        "bg_name": "soft cream-yellow",
        "bg_hex": "#F0EDB8",
        "title_color_name": "black",
        "title_color_hex": "#000000",
        "accent": "#000000",
    },
    "step_3": {
        "bg_name": "soft cream-yellow",
        "bg_hex": "#F0EDB8",
        "title_color_name": "black",
        "title_color_hex": "#000000",
        "accent": "#000000",
    },
    "step_4": {
        "bg_name": "soft cream-yellow",
        "bg_hex": "#F0EDB8",
        "title_color_name": "black",
        "title_color_hex": "#000000",
        "accent": "#000000",
    },
    "step_5": {
        "bg_name": "soft cream-yellow",
        "bg_hex": "#F0EDB8",
        "title_color_name": "black",
        "title_color_hex": "#000000",
        "accent": "#000000",
    },
}


def build_story_card_prompt(card_marker: Dict[str, Any], context_pack: Optional[Dict[str, Any]] = None) -> str:
    """
    Constrói um prompt visual descritivo e padronizado para renderizar um card gráfico.
    Garante contraste cromático, hierarquia visual e estilo gráfico limpo.
    """
    cfg = card_marker.get("story_card_config") or {}
    c_type = card_marker.get("story_card_type") or "step_1"
    style = card_marker.get("story_card_style") or "step_intro"

    title = cfg.get("title") or c_type.upper().replace("_", " ")
    subtitle = cfg.get("subtitle") or ""
    bg_color = cfg.get("background_color") or "#F0EDB8"
    title_color = cfg.get("title_color") or "#000000"
    icon_type = cfg.get("icon_type") or "infographic-element"

    if style == "step_intro":
        palette = STEP_PALETTES.get(c_type, STEP_PALETTES["step_1"])
        prompt = (
            f"Title card graphics card slide, 16:9 widescreen composition. "
            f"BACKGROUND: Solid flat smooth cream-yellow background ({palette['bg_hex']}), zero gradients, zero textures. "
            f"TYPOGRAPHY & HIERARCHY: Highly legible solid black ({palette['title_color_hex']}) modern sans-serif typography (Inter Bold / Poppins / Nunito Bold) centered in up to two lines. "
            f"Hierarchy: line 1 smaller step number '{title}', line 2 larger section title '{subtitle}'. "
            f"STYLE: Minimalist clean presentation title card, perfectly centered layout, maximum readability, "
            f"no icons, no decorative borders, no floating elements, no drop shadows, no photorealistic elements, no human faces, no hands, high digital clarity."
        )
    elif style in ("vox_editorial", "myth"):
        prompt = (
            f"Vox editorial style explainer infographic card, 16:9 aspect ratio, clean studio design. "
            f"BACKGROUND: Off-white cream background (#F8F9FA) with elegant red and navy editorial accents. "
            f"GRAPHICS: Clean diagonal badge labeled '{title}' in bold crimson red (#C5221F), "
            f"sub-headline text '{subtitle}' in sharp modern editorial grotesque font. "
            f"VISUAL: Stylized flat vector misconception bust icon with bold red line crossing out false assumption, "
            f"high-end journalism graphic, minimalist data card, clean margins, no clutter, no human person."
        )
    elif style == "data_viz":
        prompt = (
            f"Data visualization motion graphics card, 16:9 widescreen format, financial and scientific clarity. "
            f"BACKGROUND: Minimalist light neutral gray background (#F1F3F4) with soft technical grid lines. "
            f"HERO ELEMENT: Giant oversized stat callout '{title}' in vibrant Emerald Green (#137333) and deep navy typography, "
            f"with explanatory label '{subtitle}' underneath in slate gray monospace font. "
            f"VISUAL: Clean bar comparison metric or minimal percentage radial arc, Swiss graphic design aesthetic, "
            f"precise, modern, elegant data card, 8k digital render, zero photorealistic human elements."
        )
    elif style == "timeline":
        prompt = (
            f"Timeline progress graphic card, 16:9 aspect ratio, educational process overview. "
            f"BACKGROUND: Neutral clean aesthetic (#F6F8FA) with a horizontal progression track indicator. "
            f"CALLOUT: Highlighted milestone badge '{title}' in bold dark graphite (#1F2328), "
            f"supporting phase description '{subtitle}' in refined typography. "
            f"VISUAL: Minimalist milestone nodes, arrows indicating forward momentum, clean flat design, "
            f"modern vector explainer slide, high contrast, no human characters, no watermarks."
        )
    elif style == "alert_warning":
        prompt = (
            f"Urgent alert warning card in Vox and Bloomberg Quicktake style, 16:9 widescreen composition. "
            f"BACKGROUND: Warm amber pastel yellow background (#FEF7E0) with subtle cautionary geometric perimeter. "
            f"HERO CALLOUT: Striking bold header '{title}' in deep caution amber-brown (#B06000), "
            f"prominent warning description '{subtitle}' in crisp dark typography. "
            f"VISUAL: Elegant minimalist warning triangle icon with clean vector exclamation mark, "
            f"clear educational emphasis, authoritative graphic design, no human faces, no photographic distortion."
        )
    elif style in ("side_by_side", "comparison_card") or c_type in ("comparison_card", "labeled_comparison_diagram"):
        prompt = (
            f"Educational comparison diagram graphics card slide, slide gráfico minimalista de alta resolução, 16:9 widescreen composition. "
            f"BACKGROUND: Clean neutral studio background (#F8F9FA) with clear vertical division line for side-by-side comparison. "
            f"LAYOUT: Symmetric split frame with clear label space on both sides, divisão simétrica vertical. "
            f"HEADER: Bold comparison badge '{title}' in modern crisp typography, subtitle '{subtitle}'. "
            f"STYLE: Educational technical infographic comparison card, slide gráfico minimalista, neutral objective presentation, high visual clarity, no clutter, no human person."
        )
    else:
        prompt = (
            f"Educational visual story card, 16:9 aspect ratio. "
            f"BACKGROUND: Minimalist clean background ({bg_color}). "
            f"TEXT: Prominent header '{title}' ({title_color}) with descriptive note '{subtitle}'. "
            f"VISUAL: Minimalist flat 2D vector icon ({icon_type}), modern presentation graphic, "
            f"high contrast, clean layout, no human faces, no hands."
        )

    return prompt


# Alias de retrocompatibilidade
_build_card_prompt = build_story_card_prompt
