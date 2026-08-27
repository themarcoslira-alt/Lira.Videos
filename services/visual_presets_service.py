"""
visual_presets_service.py — Lira Studio
========================================
Fonte canônica de Presets de Direção Visual para geração de imagens e vídeos no Lira Studio.

Cada preset define:
  - id: Identificador único em snake_case
  - nome: Nome amigável de exibição no frontend
  - slug: Slug curto e sanitizado para nomeação oficial de arquivos (ex: Photorealistic_ci)
  - style_lock: Bloco canônico de fixação de estilo enviado aos modelos
  - instructions: Diretrizes fotográficas, de iluminação e renderização específicas
  - negative_defaults: Restrições visuais padrão do estilo
"""

from typing import Dict, List, Any, Optional
import re


NEGATIVE_LOCK_BASE = (
    "no text, no subtitles, no captions, no logos, no watermark, "
    "no inconsistent style, no duplicate character, no unnecessary random people, "
    "no split-screen, no deformed limbs, no bad anatomy, no blurry details"
)


PRESETS_DIRECAO_VISUAL: Dict[str, Dict[str, Any]] = {
    "photorealistic_cinematic": {
        "id": "photorealistic_cinematic",
        "nome": "Photorealistic Cinematic",
        "slug": "Photorealistic_ci",
        "style_lock": (
            "Photorealistic cinematic still, natural lighting, shallow depth of field, "
            "filmic color grading, realistic textures and materials, consistent lens and mood, "
            "cinematic composition, 16:9 aspect ratio."
        ),
        "instructions": (
            "Shot on 35mm / 50mm cinematic lens, organic skin and surface textures, subtle film grain, "
            "motivated directional lighting, dynamic range with rich deep shadows and soft highlights."
        ),
        "negative_defaults": NEGATIVE_LOCK_BASE,
        "is_default": True,
    },
    "documentary_natural": {
        "id": "documentary_natural",
        "nome": "Documentary Natural",
        "slug": "Documentary_nat",
        "style_lock": (
            "Documentary photography style, realistic ambient available lighting, authentic unposed moments, "
            "neutral color palette, true-to-life textures, sharp environmental context, 16:9 aspect ratio."
        ),
        "instructions": (
            "Photojournalistic style, natural eye-level framing, authentic handheld camera feel, "
            "unfiltered natural sunlight or practical indoor light, high textural clarity."
        ),
        "negative_defaults": NEGATIVE_LOCK_BASE + ", no artificial studio look, no oversaturated colors",
        "is_default": False,
    },
    "commercial_advertising": {
        "id": "commercial_advertising",
        "nome": "Commercial / Advertising",
        "slug": "Commercial_adv",
        "style_lock": (
            "High-end commercial advertisement photography, crisp studio lighting, pristine clean surfaces, "
            "vibrant polished color grading, elegant modern aesthetic, premium product commercial look, 16:9."
        ),
        "instructions": (
            "Studio three-point lighting, clean rim light, vibrant appealing colors, high contrast, "
            "flawless product and subject presentation, ultra-sharp detail."
        ),
        "negative_defaults": NEGATIVE_LOCK_BASE + ", no dirty textures, no dark grungy mood",
        "is_default": False,
    },
    "dark_cinematic": {
        "id": "dark_cinematic",
        "nome": "Dark Cinematic",
        "slug": "Dark_cinematic",
        "style_lock": (
            "Moody dark cinematic aesthetic, low-key lighting with high shadow contrast, dramatic chiaroscuro, "
            "atmospheric haze, deep desaturated tones with rich accent glows, suspenseful tone, 16:9."
        ),
        "instructions": (
            "Heavy shadows, silhouette and edge lighting, atmospheric fog or smoke, cool desaturated color palette "
            "with selective warm highlights, anamorphic lens flares."
        ),
        "negative_defaults": NEGATIVE_LOCK_BASE + ", no bright flat lighting, no happy pastel colors",
        "is_default": False,
    },
    "vintage_film": {
        "id": "vintage_film",
        "nome": "Vintage Film",
        "slug": "Vintage_film",
        "style_lock": (
            "Vintage 35mm film photograph, authentic analog grain, warm nostalgic color cast, "
            "soft halation around highlights, retro optical imperfections, timeless 1970s-1980s film stock, 16:9."
        ),
        "instructions": (
            "Kodachrome / Portra film simulation, warm golden hues, gentle chromatic aberration, "
            "rich organic grain texture, soft highlight roll-off."
        ),
        "negative_defaults": NEGATIVE_LOCK_BASE + ", no ultra-digital sharpness, no modern HDR look",
        "is_default": False,
    },
    "clean_editorial": {
        "id": "clean_editorial",
        "nome": "Clean Editorial",
        "slug": "Clean_editorial",
        "style_lock": (
            "High-fashion editorial magazine photography, soft diffused studio light, minimalist refined composition, "
            "sophisticated neutral color tones, artistic modern aesthetic, 16:9."
        ),
        "instructions": (
            "Large softbox lighting, soft neutral background, refined elegant poses and compositions, "
            "subtle pastel or monochrome nuances, pristine editorial framing."
        ),
        "negative_defaults": NEGATIVE_LOCK_BASE + ", no cluttered background, no chaotic composition",
        "is_default": False,
    },
    "hyperrealistic": {
        "id": "hyperrealistic",
        "nome": "Hyperrealistic",
        "slug": "Hyperrealistic",
        "style_lock": (
            "Hyperrealistic ultra-detailed capture, microscopic surface textures, pinpoint razor-sharp focus, "
            "perfect physical material rendering, crystal clear 8k resolution look, 16:9."
        ),
        "instructions": (
            "Macro precision, intricate pore and fiber details, perfect optical clarity, "
            "physically accurate light refraction and reflection."
        ),
        "negative_defaults": NEGATIVE_LOCK_BASE + ", no blur, no low resolution, no painterly artifacts",
        "is_default": False,
    },
    "blender_3d": {
        "id": "blender_3d",
        "nome": "Blender 3D",
        "slug": "Blender_3D",
        "style_lock": (
            "High-end Blender 3D render, physically based materials, cinematic global illumination, "
            "realistic volumetric lighting, detailed geometry, professional composition, 16:9."
        ),
        "instructions": (
            "Octane / Cycles render aesthetic, raytraced ambient occlusion, subsurface scattering on organic surfaces, "
            "clean 3D digital art modeling with tangible textures."
        ),
        "negative_defaults": NEGATIVE_LOCK_BASE + ", no 2D flat drawing, no low-poly artifacts",
        "is_default": False,
    },
    "illustration": {
        "id": "illustration",
        "nome": "Illustration",
        "slug": "Illustration",
        "style_lock": (
            "Expressive digital conceptual illustration, rich hand-painted brushwork textures, "
            "harmonious artistic color palette, creative stylized lighting and depth, 16:9."
        ),
        "instructions": (
            "Digital painting, painterly brush strokes, stylized focal emphasis, imaginative visual storytelling, "
            "balanced graphic composition."
        ),
        "negative_defaults": NEGATIVE_LOCK_BASE + ", no photorealistic skin, no raw camera photograph",
        "is_default": False,
    },
    "anime": {
        "id": "anime",
        "nome": "Anime",
        "slug": "Anime",
        "style_lock": (
            "High-budget modern cinematic anime visual, Makoto Shinkai / Kyoto Animation inspired, "
            "vibrant cel-shaded aesthetic, expressive character styling, atmospheric glowing skies, 16:9."
        ),
        "instructions": (
            "Clean line art with subtle colored line accents, dynamic lighting gradients, rich anime sky art, "
            "luminous lens flares and particle effects."
        ),
        "negative_defaults": NEGATIVE_LOCK_BASE + ", no western comic style, no live action photograph",
        "is_default": False,
    },
    "custom": {
        "id": "custom",
        "nome": "Custom (Personalizado)",
        "slug": "Custom",
        "style_lock": "Custom user-defined visual style, 16:9 aspect ratio.",
        "instructions": "Follow user provided visual directives strictly.",
        "negative_defaults": NEGATIVE_LOCK_BASE,
        "is_default": False,
    },
}

ESTILO_PADRAO_ID = "photorealistic_cinematic"


def listar_presets_estilos() -> List[Dict[str, Any]]:
    """Retorna lista de todos os presets visuais formatados para a UI e API."""
    return list(PRESETS_DIRECAO_VISUAL.values())


def obter_preset_por_id(estilo_id: Optional[str]) -> Dict[str, Any]:
    """Retorna a definição do preset pelo id, com fallback para o estilo padrão."""
    if not estilo_id:
        return PRESETS_DIRECAO_VISUAL[ESTILO_PADRAO_ID]
    eid = str(estilo_id).strip().lower()
    return PRESETS_DIRECAO_VISUAL.get(eid, PRESETS_DIRECAO_VISUAL[ESTILO_PADRAO_ID])


def sanitizar_slug_estilo(slug: str) -> str:
    """Sanitiza slug para garantir segurança estrita de filesystem no Windows."""
    clean = re.sub(r"[^\w\-]+", "_", str(slug).strip())
    return clean[:30] or "Visual"


def obter_slug_estilo(estilo_id: Optional[str]) -> str:
    """Retorna o slug curto sanitizado do estilo correspondente."""
    preset = obter_preset_por_id(estilo_id)
    return sanitizar_slug_estilo(preset.get("slug", "Photorealistic_ci"))
