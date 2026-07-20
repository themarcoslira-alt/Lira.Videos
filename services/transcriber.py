"""
transcriber.py — Transcrição de áudio/vídeo com faster-whisper
"""
import os
from pathlib import Path
import json
from config import WHISPER_MODEL_SIZE, WHISPER_DEVICE, PROJETOS_DIR


def transcrever(project_name: str, arquivo_video: str) -> dict:
    """
    Transcreve o áudio de um vídeo usando faster-whisper.
    Salva roteiro_transcricao.txt com timestamps MM:SS.
    Retorna dict com resultado.
    """
    from faster_whisper import WhisperModel

    project_dir = PROJETOS_DIR / project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    saida = project_dir / "roteiro_transcricao.txt"

    try:
        model = WhisperModel(WHISPER_MODEL_SIZE, device=WHISPER_DEVICE)
        segments, info = model.transcribe(arquivo_video, language="pt")

        linhas = []
        full_text = []
        for seg in segments:
            mins = int(seg.start // 60)
            secs = int(seg.start % 60)
            timestamp = f"{mins:02d}:{secs:02d}"
            linhas.append(f"[{timestamp}] {seg.text.strip()}")
            full_text.append(seg.text.strip())

        with open(saida, "w", encoding="utf-8") as f:
            f.write("\n".join(linhas))

        texto_completo = " ".join(full_text)

        return {
            "success": True,
            "project": project_name,
            "arquivo": str(saida),
            "texto": texto_completo,
            "language": info.language,
            "duration": info.duration,
            "segments": len(linhas)
        }

    except Exception as e:
        return {
            "success": False,
            "project": project_name,
            "error": str(e)
        }