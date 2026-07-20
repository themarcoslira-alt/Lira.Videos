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

# APIs
PEXELS_API_KEY = ""  # Preencher com chave real
PIXABAY_API_KEY = ""  # Preencher com chave real
UNSPLASH_API_KEY = ""  # Opcional: fallback de fotos
OPENAI_API_KEY = ""    # Para Claude batch via API compatível

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