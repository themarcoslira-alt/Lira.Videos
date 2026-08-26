"""
services/brand_profile_service.py — Brand Profile (Lira Studio Fase 1)
=======================================================================
Gerencia o perfil GLOBAL do canal (reutilizável por todos os projetos):
  - persistência em projetos/brand_profile.json
  - cada projeto pode herdar o global ou sobrescrever campos via
    update_profile("campo", valor) / update_profile("nested.campo", valor)
  - expõe getters de apresentador, música e mix de produção
"""

import json
import os
from copy import deepcopy
from typing import Dict, Any, Optional
from pathlib import Path

from config import PROJETOS_DIR

BRAND_PROFILE_FILE = "brand_profile.json"

DEFAULT_BRAND_PROFILE: Dict[str, Any] = {
    "channel_name": "Lira Jardinagem",
    "channel_description": "Dicas profissionais de jardinagem",
    "presenter_name": "Marcos",
    "presenter_reference_path": "Biblioteca/Personagens/marcos/reference.png",
    "presenter_video_poses": {
        "talking": "Biblioteca/Personagens/marcos/poses_talking.mp4",
        "action": "Biblioteca/Personagens/marcos/poses_action.mp4",
        "thinking": "Biblioteca/Personagens/marcos/poses_thinking.mp4",
    },
    "broll_source": "pexels",
    "broll_fallback": "stock_videos",
    "music_default": "background_garden_upbeat.mp3",
    "music_path": "Biblioteca/Musicas/",
    "caption_style": {
        "font": "Arial",
        "size": 24,
        "color": "#FFFF00",
        "background": "black",
        "position": "bottom",
    },
    "avatar_config": {
        "duration_min_seconds": 3,
        "duration_max_seconds": 15,
        "fps": 30,
        "resolution": "1920x1080",
    },
    "video_mix_ratio": {
        "avatar_percentage": 0.7,
        "broll_percentage": 0.3,
    },
    "quality_settings": {
        "bitrate": "8000k",
        "codec": "h264",
    },
    "export_format": "mp4",
    "capcut_integration": True,
}

# Campos obrigatórios verificados no load (compatibilidade retroativa)
_CAMPOS_OBRIGATORIOS = (
    "channel_name", "channel_description", "presenter_name",
    "presenter_reference_path", "presenter_video_poses", "broll_source",
    "broll_fallback", "music_default", "music_path", "caption_style",
    "avatar_config", "video_mix_ratio", "quality_settings",
    "export_format", "capcut_integration",
)


def _caminho(base_dir=None) -> Path:
    base = Path(base_dir) if base_dir else PROJETOS_DIR
    return base / BRAND_PROFILE_FILE


def default_profile(channel_name: str = "Lira Jardinagem") -> Dict[str, Any]:
    """Perfil padrão (cópia profunda — nunca muta a constante)."""
    p = deepcopy(DEFAULT_BRAND_PROFILE)
    if channel_name:
        p["channel_name"] = channel_name
    return p


def _complete_faltantes(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Preenche campos obrigatórios ausentes com o default (sem perder extras)."""
    default = default_profile(profile.get("channel_name"))
    for k in _CAMPOS_OBRIGATORIOS:
        if k not in profile:
            profile[k] = deepcopy(default[k])
    return profile


def save_profile(profile: Optional[Dict[str, Any]] = None, base_dir=None) -> bool:
    """Persiste o perfil em <base_dir>/brand_profile.json (atômico)."""
    data = profile if profile is not None else default_profile()
    path = _caminho(base_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(path))
        return True
    except Exception:
        return False


def load_brand_profile(base_dir=None) -> Dict[str, Any]:
    """Carrega o perfil global (criando o default se o arquivo não existir)."""
    path = _caminho(base_dir)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return _complete_faltantes(data)
        except Exception:
            pass
    return create_default_profile(base_dir=base_dir)


def create_default_profile(channel_name: str = "Lira Jardinagem", base_dir=None) -> Dict[str, Any]:
    """Cria o perfil padrão se não existir; caso exista, apenas retorna."""
    path = _caminho(base_dir)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return _complete_faltantes(data)
        except Exception:
            pass
    profile = default_profile(channel_name)
    save_profile(profile, base_dir=base_dir)
    return profile


def _set_nested(d: Dict[str, Any], path: str, value) -> None:
    keys = path.split(".")
    cur = d
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


def update_profile(field: str, value, base_dir=None) -> bool:
    """Atualiza um campo específico (suporta caminho aninhado 'a.b.c')."""
    profile = load_brand_profile(base_dir=base_dir)
    _set_nested(profile, field, value)
    return save_profile(profile, base_dir=base_dir)


def get_presenter_reference(presenter_name: str = "", base_dir=None) -> str:
    """Retorna o caminho do reference.png do apresentador (global do canal)."""
    profile = load_brand_profile(base_dir=base_dir)
    return profile.get("presenter_reference_path") or ""


def get_music_path(base_dir=None) -> str:
    """Retorna o caminho completo da música padrão do canal."""
    profile = load_brand_profile(base_dir=base_dir)
    nome = profile.get("music_default") or ""
    base_mus = profile.get("music_path") or ""
    return os.path.join(base_mus, nome).replace("\\", "/") if nome else base_mus