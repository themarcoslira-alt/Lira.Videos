"""
config.py — Configuração central do ULTRACUT3
"""
import os, shutil
from pathlib import Path

# Diretórios — usa o diretório do próprio arquivo para ser portável
BASE_DIR = Path(__file__).parent.resolve()
PROJETOS_DIR = BASE_DIR / "projetos"
BIBLIOTECA_DIR = BASE_DIR / "Biblioteca"
OUTPUT_DIR = BASE_DIR / "output"
ASSETS_CACHE_DIR = BASE_DIR / "assets_cache"
LOGS_DIR = BASE_DIR / "logs"

for d in [PROJETOS_DIR, BIBLIOTECA_DIR, OUTPUT_DIR, ASSETS_CACHE_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

def normalizar_caminho(caminho: str) -> str:
    """Normaliza caminhos legados C:\\ultracut3 para o BASE_DIR atual."""
    if not caminho:
        return ""
    c_str = str(caminho)
    base_win = str(BASE_DIR)
    base_slash = str(BASE_DIR).replace("\\", "/")
    return c_str.replace("C:\\ultracut3", base_win).replace("C:/ultracut3", base_slash)

# FFmpeg paths — auto-detect com fallback manual
FFMPEG_PATH = shutil.which("ffmpeg")
FFPROBE_PATH = shutil.which("ffprobe")
if not FFMPEG_PATH:
    for p in [
        str(BASE_DIR / "ffmpeg" / "ffmpeg-8.1.2-essentials_build" / "bin" / "ffmpeg.exe"),
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
        str(BASE_DIR / "ffmpeg" / "ffmpeg-8.1.2-essentials_build" / "bin" / "ffprobe.exe"),
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

# Fallback de encoder — usado APENAS quando h264_amf não está disponível
# ou falha ao inicializar no ambiente (ex: servidor sem GPU AMD / drivers antigos).
ENCODER_FALLBACK = "libx264"

_ENCODER_RESOLVIDO = None


def _testar_encoder(nome: str) -> bool:
    """Testa se um encoder ffmpeg realmente inicializa (encode minúsculo real)."""
    if not FFMPEG_PATH:
        return False
    import subprocess
    try:
        cmd = [
            FFMPEG_PATH, "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.2",
            "-frames:v", "1",
            "-c:v", nome,
            "-pix_fmt", "yuv420p",
            "-f", "null", "-",
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def resolver_encoder() -> str:
    """
    Retorna o encoder efetivo do ambiente:
      - VIDEO_ENCODER (h264_amf) se estiver disponível E inicializar;
      - ENCODER_FALLBACK (libx264) caso contrário.
    Resultado é cacheado por processo.
    """
    global _ENCODER_RESOLVIDO
    if _ENCODER_RESOLVIDO is None:
        _ENCODER_RESOLVIDO = VIDEO_ENCODER if _testar_encoder(VIDEO_ENCODER) else ENCODER_FALLBACK
    return _ENCODER_RESOLVIDO

# Fase 0 — Direção Visual / LLM Provider (aditivo)
# Provider: "local" (offline/determinístico) | "deepseek" | "anthropic" (futuro)
# Modelo SEMPRE configurável (env LLM_MODEL ou config_local) — nunca fixo no código.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "local").strip().lower()
LLM_MODEL = os.environ.get("LLM_MODEL", "").strip()
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1").strip()
LLM_API_KEY = ""
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "60"))
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "2"))

# Arquivos Fase 0
VISUAL_PROFILE_FILE = "visual_profile.json"
SCENE_PLAN_FILE = "scene_plan.json"

# APIs de Stock (Legadas / Deprecadas no Studio 2.0 - substituídas por IA / Google Flow)
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
    # Fase 0 — LLM (config_local pode definir provider/modelo/chave; env tem prioridade)
    if hasattr(_local, 'LLM_PROVIDER') and _local.LLM_PROVIDER and not os.environ.get("LLM_PROVIDER"):
        LLM_PROVIDER = _local.LLM_PROVIDER.strip().lower()
    if hasattr(_local, 'LLM_MODEL') and _local.LLM_MODEL and not os.environ.get("LLM_MODEL"):
        LLM_MODEL = _local.LLM_MODEL.strip()
    if hasattr(_local, 'LLM_BASE_URL') and _local.LLM_BASE_URL and not os.environ.get("LLM_BASE_URL"):
        LLM_BASE_URL = _local.LLM_BASE_URL.strip()
    if hasattr(_local, 'LLM_API_KEY') and _local.LLM_API_KEY:
        LLM_API_KEY = _local.LLM_API_KEY
except ImportError:
    pass

def recarregar_chaves():
    global PEXELS_API_KEY, PIXABAY_API_KEY, UNSPLASH_API_KEY, ANTHROPIC_API_KEY
    global LLM_PROVIDER, LLM_MODEL, LLM_BASE_URL, LLM_API_KEY
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
        if hasattr(_local, 'LLM_PROVIDER') and _local.LLM_PROVIDER:
            LLM_PROVIDER = _local.LLM_PROVIDER.strip().lower()
        if hasattr(_local, 'LLM_MODEL') and _local.LLM_MODEL:
            LLM_MODEL = _local.LLM_MODEL.strip()
        if hasattr(_local, 'LLM_BASE_URL') and _local.LLM_BASE_URL:
            LLM_BASE_URL = _local.LLM_BASE_URL.strip()
        if hasattr(_local, 'LLM_API_KEY') and _local.LLM_API_KEY:
            LLM_API_KEY = _local.LLM_API_KEY
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

WHISPER_MODEL_SIZE = "tiny"
WHISPER_MODEL = WHISPER_MODEL_SIZE  # alias para consistencia
WHISPER_DEVICE = "cpu"
WHISPER_COMPUTE_TYPE = "float32"  # float32 evita segfault do ctranslate2 em Python 3.14
WHISPER_CPU_THREADS = 2  # 2 threads evita segfault (None crasha)
WHISPER_NUM_WORKERS = 1
WHISPER_LANGUAGE = "en"

# Anthropic
ANTHROPIC_MODEL = "claude-sonnet-5"

PIPELINE_STEPS = [
    "transcrever",
    "gerar_cenas",
    "storyboard_broll",
    "buscar_midias",
    "renderizar"
]

# =============================================================================
# DISTRIBUIÇÃO NARRATIVA E DE MÍDIA (PADRÃO LIRA STUDIO)
# =============================================================================
AVATAR_QUOTA = 0.08           # 8% das cenas com apresentador (aprox. 7 a 10 cenas)
BROLL_QUOTA = 0.92            # 92% B-roll de cobertura visual
BROLL_VIDEO_RATIO = 0.60      # 60% dos B-rolls em vídeo (dinâmico/ação)
BROLL_IMAGE_RATIO = 0.40      # 40% dos B-rolls em imagem parada (macro/detalhe)

# =============================================================================
# CICLO NARRATIVO (LIRA STUDIO v0.3.5+)
# =============================================================================
# Substitui a quota aleatória por um ciclo Avatar → Imagem → Vídeo ao longo
# do vídeo (1→2→3→1→2→3...). Cada ciclo tem 3 cenas.
NARRATIVE_CYCLE_ENABLED = True
CYCLE_TIPOS = ["avatar_intro", "imagem_zoom", "video_acao"]
CYCLE_DURACAO_AVATAR = 7    # segundos
CYCLE_DURACAO_IMAGEM = 12   # segundos
CYCLE_DURACAO_VIDEO = 8     # segundos

# NOMENCLATURA (PADRÃO LIRA STUDIO v0.3.0+)
FILENAME_PATTERN = "{id:02d}_[{inicio_mmss}-{fim_mmss}].{ext}"
# Todas as mídias de cena (imagem e vídeo) passam a viver em cenas/ (unificado).
PASTAS_MIDIA = {
    "png": "cenas",
    "jpg": "cenas",
    "jpeg": "cenas",
    "webp": "cenas",
    "mp4": "cenas",
    "mov": "cenas",
    "mkv": "cenas",
    "metadata": "metadata",
}

# EFEITOS VÁLIDOS (zoom_in, fade, pan, none)
VALID_EFEITOS = ["zoom_in", "fade", "pan", "none"]