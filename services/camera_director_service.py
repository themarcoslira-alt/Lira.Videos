"""
services/camera_director_service.py — Camera Director AI
=========================================================
Responsabilidade:
- Definir a direção de fotografia e cinematografia para cada cena:
  * Enquadramento (Shot): close-up, medium shot, wide shot, macro, POV, over-the-shoulder, top-down, documentary handheld, low-angle heroic.
  * Lente (Lens): 24mm (grande angular), 35mm (cinematográfica ambiental), 50mm (olho humano natural), 85mm (retrato expressivo com bokeh), 100mm (macro de altíssima precisão).
  * Movimento (Movement): slow push-in, static tripod, subtle handheld drift, tracking pan, tilt up, gentle orbit.
  * Composição (Composition): rule of thirds, centered subject, depth of field bokeh, Dutch angle, foreground framing.
- Regra de Ouro: Nunca repetir o mesmo enquadramento e lente em cenas consecutivas sem motivação dramática.
"""

from typing import Dict, Any, Optional
from services.event_logger import log_event


MAPA_SHOT_POR_TIPO = {
    "avatar_talking": [
        {"shot": "medium shot", "lens": "35mm", "movement": "slow push-in", "composition": "centered subject with soft natural depth of field"},
        {"shot": "close-up", "lens": "50mm", "movement": "subtle handheld drift", "composition": "rule of thirds, expressive facial details"},
        {"shot": "medium close-up", "lens": "85mm", "movement": "static tripod with gentle atmospheric breeze", "composition": "shallow depth of field, creamy background blur"}
    ],
    "avatar_action": [
        {"shot": "medium wide shot", "lens": "35mm", "movement": "tracking pan following the movement", "composition": "subject in action within authentic environment"},
        {"shot": "low angle medium shot", "lens": "24mm", "movement": "slow upward tilt", "composition": "heroic perspective with expansive garden backdrop"},
        {"shot": "over-the-shoulder shot", "lens": "50mm", "movement": "subtle steadycam drift", "composition": "focus on hands and working area"}
    ],
    "broll_macro": [
        {"shot": "extreme close-up macro", "lens": "100mm macro", "movement": "slow cinematic push-in (micro dolly)", "composition": "ultra-fine focus on biological texture, morning dew droplets"},
        {"shot": "top-down macro shot", "lens": "50mm macro", "movement": "subtle vertical tilt", "composition": "geometric flat lay with rich organic surface detail"},
        {"shot": "close-up macro", "lens": "85mm macro", "movement": "gentle horizontal slide", "composition": "sharp foreground subject against atmospheric soft bokeh"}
    ],
    "broll_action": [
        {"shot": "POV close-up shot", "lens": "35mm", "movement": "smooth handheld following the hands", "composition": "first-person viewpoint of hands executing precise technique"},
        {"shot": "tight close-up", "lens": "50mm", "movement": "slow panning across the tool and material", "composition": "crisp focus on point of contact, shallow depth of field"},
        {"shot": "medium close-up", "lens": "85mm", "movement": "static tripod", "composition": "high-clarity capture of natural motion"}
    ],
    "environment": [
        {"shot": "wide establishing shot", "lens": "24mm cinematic wide", "movement": "slow panoramic pan", "composition": "expansive architectural depth and natural landscape balance"},
        {"shot": "cinematic wide shot", "lens": "35mm", "movement": "slow forward dolly", "composition": "leading lines through the botanical environment"}
    ],
    "hybrid": [
        {"shot": "medium shot with foreground focus", "lens": "35mm", "movement": "slow push-in towards the presented object", "composition": "character engaged in background while presenting object sharply in foreground"},
        {"shot": "over-the-shoulder medium shot", "lens": "50mm", "movement": "subtle handheld drift", "composition": "balanced connection between presenter and demonstrated technique"}
    ],
    "before_after": [
        {"shot": "split composition / aligned medium shot", "lens": "50mm", "movement": "static tripod for perfect before/after alignment", "composition": "centered subject with distinct visual clarity"},
        {"shot": "close-up reveal", "lens": "35mm", "movement": "slow push-in", "composition": "vivid demonstration of transformation"}
    ],
    "comparison": [
        {"shot": "two-element balanced medium shot", "lens": "35mm", "movement": "static tripod", "composition": "side-by-side contrast with neutral objective lighting"},
        {"shot": "panning medium shot", "lens": "50mm", "movement": "slow pan from first subject to second subject", "composition": "clear comparative framing"}
    ],
    "cta": [
        {"shot": "warm medium close-up", "lens": "50mm", "movement": "slow friendly push-in", "composition": "direct eye contact, welcoming and empowering atmosphere"},
        {"shot": "medium shot", "lens": "35mm", "movement": "static tripod with warm backlight", "composition": "open relaxed posture, golden hour glow"}
    ]
}


def direcionar_camera(
    cena: Dict[str, Any],
    scene_type: str,
    index: int = 0,
    camera_anterior: Optional[Dict[str, Any]] = None
) -> Dict[str, str]:
    """
    Computa a direção de câmera para a cena garantindo diversidade e precisão visual.
    """
    opcoes = MAPA_SHOT_POR_TIPO.get(scene_type, MAPA_SHOT_POR_TIPO["broll_macro"])
    
    # Seleciona uma opção variada em relação à cena anterior
    escolha = opcoes[index % len(opcoes)]
    
    if camera_anterior and escolha.get("shot") == camera_anterior.get("shot") and len(opcoes) > 1:
        escolha = opcoes[(index + 1) % len(opcoes)]

    camera_direction = {
        "shot": escolha["shot"],
        "lens": escolha["lens"],
        "movement": escolha["movement"],
        "composition": escolha["composition"],
        "aspect_ratio": "16:9 cinematic"
    }

    log_event("CAMERA_DIRECTOR", f"Cena {cena.get('id', index+1)}: shot='{camera_direction['shot']}', lens='{camera_direction['lens']}', movement='{camera_direction['movement']}'")
    return camera_direction
