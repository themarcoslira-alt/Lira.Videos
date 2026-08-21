"""
visual_profile.py — VisualProfile (Direção Visual global) — Fase 0.

Locks globais de direção visual, reutilizáveis entre projetos.
Cada cena planejada resolve uma CÓPIA destes locks (scene.locks) no momento
em que foi criada — se o perfil mudar depois, a cena preserva o que usou.
"""

import json
import os
from pathlib import Path
from typing import Optional

from config import VISUAL_PROFILE_FILE
from services.event_logger import log_event

LOCK_KEYS = ("style_lock", "character_lock", "world_lock", "composition_lock", "negative_lock")
RESOLVED_LOCK_KEYS = ("style", "character", "world", "composition", "negative")

DEFAULT_PROFILE = {
    "name": "Default — Photorealistic Cinematic",
    "style_lock": "Photorealistic cinematic, natural lighting, shallow depth of field, 35mm film grain, subtle teal-orange grade, high dynamic range.",
    "character_lock": "If people appear, keep the same character across all scenes: consistent face, build, clothing and age. Realistic proportions, no stylization.",
    "world_lock": "Consistent real-world environments. Lighting, weather and time of day must be coherent between consecutive scenes.",
    "composition_lock": "Rule of thirds, centered subject, 16:9 cinematic framing, negative space reserved for text overlays, stable horizon.",
    "negative_lock": "no text, no watermark, no warped faces, no oversaturation, no cartoon, no anime, no CGI look.",
}

PRESETS = {
    "photorealistic_cinematic": DEFAULT_PROFILE,
    "boneco_palito": {
        "name": "STICK FIGURE DOODLE",
        "style_lock": "Hand-drawn stick figure doodle, minimal line art, thin black strokes on plain white or lined paper, subtle sketch texture, flat 2D.",
        "character_lock": "One consistent stick figure character across all scenes: round head, simple line body, same proportions and posture vocabulary in every frame.",
        "world_lock": "Minimal props drawn with the same line style; consistent paper background and consistent stroke weight.",
        "composition_lock": "Subject centered, flat composition, generous margins for text, simple readable poses.",
        "negative_lock": "no photorealism, no complex shading, no 3D, no colors unless explicitly requested, no busy backgrounds.",
    },
    "cartoon": {
        "name": "CARTOON",
        "style_lock": "Colorful 2D cartoon, clean vector-like shapes, bold clean outlines, soft shading, vibrant palette, expressive and playful.",
        "character_lock": "Consistent cartoon character design in every scene: same face, same colors, same proportions, recognizable silhouette.",
        "world_lock": "Playful cartoon world with consistent background style and color language across all scenes.",
        "composition_lock": "Dynamic composition, exaggerated expressions, clear focal subject, storybook framing.",
        "negative_lock": "no photorealism, no horror, no realistic proportions, no text, no watermarks.",
    },
    "cinematografico_dramatico": {
        "name": "DRAMATIC CINEMATIC",
        "style_lock": "Dramatic cinematic look, high contrast, deep shadows, moody atmospheric lighting, anamorphic feel, filmic color grade, rich blacks.",
        "character_lock": "Consistent dramatic subject: same silhouette, wardrobe and presence in every scene; strong emotional performance.",
        "world_lock": "Dark and atmospheric environments; consistent tone, weather and mood continuity between scenes.",
        "composition_lock": "Strong leading lines, chiaroscuro lighting, cinematic 16:9 framing, deliberate negative space.",
        "negative_lock": "no flat lighting, no bright comedy palette, no oversaturation, no warped subjects, no text or watermarks.",
    },
}


class VisualProfile:
    """Direção visual global do projeto (locks reutilizáveis)."""

    def __init__(self, name="", style_lock="", character_lock="", world_lock="",
                 composition_lock="", negative_lock=""):
        self.name = name
        self.style_lock = style_lock
        self.character_lock = character_lock
        self.world_lock = world_lock
        self.composition_lock = composition_lock
        self.negative_lock = negative_lock

    @classmethod
    def from_dict(cls, data: dict) -> "VisualProfile":
        d = data or {}
        return cls(
            name=str(d.get("name", "")),
            style_lock=str(d.get("style_lock", "")),
            character_lock=str(d.get("character_lock", "")),
            world_lock=str(d.get("world_lock", "")),
            composition_lock=str(d.get("composition_lock", "")),
            negative_lock=str(d.get("negative_lock", "")),
        )

    @classmethod
    def default(cls) -> "VisualProfile":
        return cls.from_dict(DEFAULT_PROFILE)

    @classmethod
    def from_preset(cls, nome: str) -> "VisualProfile":
        nome = str(nome or "").strip()
        if nome not in PRESETS:
            raise ValueError(f"preset desconhecido: {nome!r} (disponíveis: {', '.join(PRESETS)})")
        return cls.from_dict(PRESETS[nome])

    def to_dict(self) -> dict:
        return {"name": self.name, **{k: getattr(self, k) for k in LOCK_KEYS}}

    def resolved_locks(self) -> dict:
        """Cópia dos locks com chaves curtas usadas em scene.locks."""
        return {
            "style": self.style_lock,
            "character": self.character_lock,
            "world": self.world_lock,
            "composition": self.composition_lock,
            "negative": self.negative_lock,
        }

    def validate(self) -> list:
        erros = []
        for k in LOCK_KEYS:
            if not isinstance(getattr(self, k), str):
                erros.append(f"{k} deve ser texto")
        return erros

    def is_valid(self) -> bool:
        return not self.validate()

    def __repr__(self):
        return f"VisualProfile(name={self.name!r})"


def caminho_perfil(project_dir) -> Path:
    return Path(project_dir) / VISUAL_PROFILE_FILE


def salvar_visual_profile(project_dir, profile: VisualProfile) -> Path:
    """Salva o perfil em <projeto>/visual_profile.json (escrita atômica)."""
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    destino = caminho_perfil(project_dir)
    tmp = project_dir / (VISUAL_PROFILE_FILE + ".tmp")
    tmp.write_text(json.dumps(profile.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp), str(destino))
    log_event("VISUAL_PROFILE", f"VisualProfile salvo: {profile.name} -> {destino}", level="info")
    return destino


def carregar_visual_profile(project_dir) -> Optional[VisualProfile]:
    """Carrega o perfil do projeto; None se ausente ou inválido."""
    destino = caminho_perfil(project_dir)
    if not destino.exists():
        return None
    try:
        data = json.loads(destino.read_text(encoding="utf-8"))
        profile = VisualProfile.from_dict(data)
        erros = profile.validate()
        if erros:
            log_event("VISUAL_PROFILE", f"perfil inválido em {destino}: {erros}", level="warn")
            return None
        return profile
    except Exception as e:
        log_event("VISUAL_PROFILE", f"erro ao carregar perfil {destino}: {e}", level="warn")
        return None
