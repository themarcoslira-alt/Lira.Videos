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
    from services.event_logger import log_event

    log_event("TRANSCRIBE", f"Iniciando transcricao: {arquivo_video}", level="info")

    from faster_whisper import WhisperModel

    project_dir = PROJETOS_DIR / project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    saida = project_dir / "roteiro_transcricao.txt"

    try:
        log_event("TRANSCRIBE", "Carregando modelo faster-whisper...", level="info")
        model = WhisperModel(WHISPER_MODEL_SIZE, device=WHISPER_DEVICE)
        segments, info = model.transcribe(arquivo_video, language="pt")

        linhas = []
        full_text = []
        seg_count = 0
        for seg in segments:
            mins = int(seg.start // 60)
            secs = int(seg.start % 60)
            timestamp = f"{mins:02d}:{secs:02d}"
            linhas.append(f"[{timestamp}] {seg.text.strip()}")
            full_text.append(seg.text.strip())
            seg_count += 1
            if seg_count % 10 == 0:
                log_event("TRANSCRIBE", f"Transcrevendo... segmento {seg_count} em [{timestamp}]",
                          level="info", details={"progress": seg_count, "time": seg.start})

        with open(saida, "w", encoding="utf-8") as f:
            f.write("\n".join(linhas))

        texto_completo = " ".join(full_text)

        log_event("TRANSCRIBE",
                  f"Transcricao concluida: {len(linhas)} segmentos, idioma {info.language}",
                  level="info",
                  details={"segments": len(linhas), "language": info.language, "duration": info.duration})

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
        log_event("TRANSCRIBE", f"Erro na transcricao: {str(e)}", level="error")
        return {
            "success": False,
            "project": project_name,
            "error": str(e)
        }
