"""
config.py — Configuração central do ULTRACUT3
"""
import os
from pathlib import Path

# Diretórios
BASE_DIR = Path("C:/ultracut3")
PROJETOS_DIR = BASE_DIR / "projetos"
BIBLIOTECA_DIR = BASE_DIR / "Biblioteca"
OUTPUT_DIR = BASE_DIR / "output"
ASSETS_CACHE_DIR = BASE_DIR / "assets_cache"
LOGS_DIR = BASE_DIR / "logs"

# Garantir que diretórios existem
for d in [PROJETOS_DIR, BIBLIOTECA_DIR, OUTPUT_DIR, ASSETS_CACHE_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Encoder de vídeo (AMD GPU)
VIDEO_ENCODER = "h264_amf"
VIDEO_ENCODER_OPTIONS = [
    "-pix_fmt", "yuv420p",
    "-profile:v", "main",
    "-movflags", "+faststart",
    "-c:a", "aac",
    "-b:a", "192k"
]

# APIs — carregadas de config_local.py (fora do Git)
PEXELS_API_KEY = ""
PIXABAY_API_KEY = ""
UNSPLASH_API_KEY = ""
ANTHROPIC_API_KEY = ""

try:
    import config_local as _local
    if hasattr(_local, 'PEXELS_API_KEY') and _local.PEXELS_API_KEY:
        PEXELS_API_KEY = _local.PEXELS_API_KEY
    if hasattr(_local, 'PIXABAY_API_KEY') and _local.PIXABAY_API_KEY:
        PIXABAY_API_KEY = _local.PIXABAY_API_KEY
    if hasattr(_local, 'UNSPLASH_API_KEY') and _local.UNSPLASH_API_KEY:
        UNSPLASH_API_KEY = _local.UNSPLASH_API_KEY
    if hasattr(_local, 'ANTHROPIC_API_KEY') and _local.ANTHROPIC_API_KEY:
        ANTHROPIC_API_KEY = _local.ANTHROPIC_API_KEY
except ImportError:
    pass  # config_local.py não existe — chaves ficam vazias

def recarregar_chaves():
    """Recarrega chaves do config_local.py em tempo real (sem restart)."""
    global PEXELS_API_KEY, PIXABAY_API_KEY, UNSPLASH_API_KEY, ANTHROPIC_API_KEY
    try:
        import config_local as _local
        import importlib
        importlib.reload(_local)
        if hasattr(_local, 'PEXELS_API_KEY'):
            PEXELS_API_KEY = _local.PEXELS_API_KEY
        if hasattr(_local, 'PIXABAY_API_KEY'):
            PIXABAY_API_KEY = _local.PIXABAY_API_KEY
        if hasattr(_local, 'UNSPLASH_API_KEY'):
            UNSPLASH_API_KEY = _local.UNSPLASH_API_KEY
        if hasattr(_local, 'ANTHROPIC_API_KEY'):
            ANTHROPIC_API_KEY = _local.ANTHROPIC_API_KEY
        return True
    except ImportError:
        # Cria config_local.py placeholder se não existir
        _criar_placeholder()
        return False
    except Exception:
        return False

def _criar_placeholder():
    """Cria config_local.py vazio se não existir."""
    template = '''"""
config_local.py — Chaves de API locais (NÃO COMMITAR no Git)
Preencha com suas chaves reais.
"""
PEXELS_API_KEY = ""
PIXABAY_API_KEY = ""
UNSPLASH_API_KEY = ""
ANTHROPIC_API_KEY = ""
'''
    with open(str(BASE_DIR / "config_local.py"), "w") as f:
        f.write(template)

# Limites
MAX_BIBLIOTECA_REUSE = 2
MAX_SEARCH_ATTEMPTS_PASSA1 = 6
MAX_SEARCH_ATTEMPTS_PASSA2 = 6
UNSPLASH_RATE_LIMIT = 45  # requisições/hora (margem segura sobre 50)

# Whisper
WHISPER_MODEL_SIZE = "base"  # tiny, base, small, medium, large-v3
WHISPER_DEVICE = "auto"       # auto, cpu, cuda

# Pipeline
PIPELINE_STEPS = [
    "transcrever",
    "gerar_cenas",
    "storyboard_broll",
    "buscar_midias",
    "renderizar"
]