"""
config.py — Configuração central do ULTRACUT3
"""
import os, shutil
from pathlib import Path

# Diretórios
BASE_DIR = Path("C:/ultracut3")
PROJETOS_DIR = BASE_DIR / "projetos"
BIBLIOTECA_DIR = BASE_DIR / "Biblioteca"
OUTPUT_DIR = BASE_DIR / "output"
ASSETS_CACHE_DIR = BASE_DIR / "assets_cache"
LOGS_DIR = BASE_DIR / "logs"

for d in [PROJETOS_DIR, BIBLIOTECA_DIR, OUTPUT_DIR, ASSETS_CACHE_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# FFmpeg paths — auto-detect com fallback manual
FFMPEG_PATH = shutil.which("ffmpeg")
FFPROBE_PATH = shutil.which("ffprobe")
if not FFMPEG_PATH:
    for p in [
        r"C:\ultracut3\ffmpeg\ffmpeg-8.1.2-essentials_build\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\tools\ffmpeg\bin\ffmpeg.exe"
    ]:
        if Path(p).exists():
            FFMPEG_PATH = p
            break
if not FFPROBE_PATH:
    for p in [
        r"C:\ultracut3\ffmpeg\ffmpeg-8.1.2-essentials_build\bin\ffprobe.exe",
        r"C:\ffmpeg\bin\ffprobe.exe",
        r"C:\Program Files\ffmpeg\bin\ffprobe.exe",
        r"C:\tools\ffmpeg\bin\ffprobe.exe"
    ]:
        if Path(p).exists():
            FFPROBE_PATH = p
            break

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
    pass

def recarregar_chaves():
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
        _criar_placeholder()
        return False
    except Exception:
        return False

def _criar_placeholder():
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

MAX_BIBLIOTECA_REUSE = 2
MAX_SEARCH_ATTEMPTS_PASSA1 = 6
MAX_SEARCH_ATTEMPTS_PASSA2 = 6
UNSPLASH_RATE_LIMIT = 45

WHISPER_MODEL_SIZE = "base"
WHISPER_DEVICE = "auto"

PIPELINE_STEPS = [
    "transcrever",
    "gerar_cenas",
    "storyboard_broll",
    "buscar_midias",
    "renderizar"
]