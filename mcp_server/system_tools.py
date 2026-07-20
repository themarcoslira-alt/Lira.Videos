"""
system_tools.py — Ferramentas MCP para sistema
1 ferramenta: system_info
"""
import platform
import subprocess
import json
from pathlib import Path


def system_info() -> dict:
    """
    Retorna informações do sistema.
    Útil para diagnosticar configuração de encoder, GPU, etc.
    """
    info = {
        "os": platform.system(),
        "os_version": platform.version(),
        "python_version": platform.python_version(),
        "machine": platform.machine(),
        "processor": platform.processor()
    }

    # Verifica ffmpeg
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            first_line = result.stdout.split("\n")[0]
            info["ffmpeg"] = first_line
        else:
            info["ffmpeg"] = "não encontrado"
    except Exception:
        info["ffmpeg"] = "não encontrado"

    # Verifica encoders disponíveis
    try:
        result = subprocess.run(
            ["ffmpeg", "-encoders"],
            capture_output=True, text=True, timeout=5
        )
        encoders = result.stdout
        info["h264_amf_available"] = "h264_amf" in encoders
        info["h264_nvenc_available"] = "h264_nvenc" in encoders
        info["libx264_available"] = "libx264" in encoders
    except Exception:
        info["h264_amf_available"] = False

    # Verifica GPU AMD
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-init_hw_device", "amf"],
            capture_output=True, text=True, timeout=5
        )
        info["amf_supported"] = "amf" in result.stdout.lower() or "amf" in result.stderr.lower()
    except Exception:
        info["amf_supported"] = False

    return info