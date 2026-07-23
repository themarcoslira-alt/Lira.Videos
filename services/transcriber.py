"""
transcriber.py — Transcrição de áudio/vídeo com faster-whisper
Armazena:
  - roteiro_transcricao.txt  (formato [MM:SS] texto, compatibilidade)
  - roteiro_transcricao.json (segmentos estruturados com start/end/text)
"""
import os
from pathlib import Path
import json
from config import WHISPER_MODEL_SIZE, WHISPER_DEVICE, PROJETOS_DIR


def transcrever(project_name: str, arquivo_video: str) -> dict:
    """
    Transcreve o áudio de um vídeo usando faster-whisper.
    Salva:
      - roteiro_transcricao.txt com timestamps MM:SS (compatibilidade)
      - roteiro_transcricao.json com segmentos estruturados (start, end, text)
    Retorna dict com resultado.
    """
    from services.event_logger import log_event

    log_event("TRANSCRIBE", f"Iniciando transcricao: {arquivo_video}", level="info")

    from faster_whisper import WhisperModel

    project_dir = PROJETOS_DIR / project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    saida_txt = project_dir / "roteiro_transcricao.txt"
    saida_json = project_dir / "roteiro_transcricao.json"

    try:
        log_event("TRANSCRIBE", "Carregando modelo faster-whisper...", level="info")
        model = WhisperModel(WHISPER_MODEL_SIZE, device=WHISPER_DEVICE)
        segments, info = model.transcribe(arquivo_video, language="pt")

        linhas_txt = []
        full_text = []
        segmentos_json = []
        seg_count = 0
        for seg in segments:
            mins = int(seg.start // 60)
            secs = int(seg.start % 60)
            timestamp = f"{mins:02d}:{secs:02d}"
            texto = seg.text.strip()
            linhas_txt.append(f"[{timestamp}] {texto}")
            full_text.append(texto)
            segmentos_json.append({
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": texto,
                "timestamp": timestamp
            })
            seg_count += 1
            pct = int((seg.start / info.duration) * 100) if hasattr(info, 'duration') and info.duration else 0
            log_event("TRANSCRIBE",
                f"Segmento {seg_count} | [{timestamp}] | {pct}% do audio transcrito",
                level="info")

        # Salva TXT (compatibilidade)
        with open(saida_txt, "w", encoding="utf-8") as f:
            f.write("\n".join(linhas_txt))

        # Salva JSON estruturado (fonte de verdade temporal)
        transcricao_data = {
            "project": project_name,
            "duration": round(info.duration, 2) if hasattr(info, 'duration') and info.duration else 0,
            "language": info.language if hasattr(info, 'language') else "pt",
            "segments": segmentos_json,
            "segment_count": seg_count
        }
        with open(saida_json, "w", encoding="utf-8") as f:
            json.dump(transcricao_data, f, indent=2, ensure_ascii=False)

        texto_completo = " ".join(full_text)

        log_event("TRANSCRIBE",
                  f"Transcricao concluida: {seg_count} segmentos, idioma {info.language}",
                  level="info",
                  details={"segments": seg_count, "language": info.language, "duration": info.duration})

        return {
            "success": True,
            "project": project_name,
            "arquivo": str(saida_txt),
            "texto": texto_completo,
            "language": info.language,
            "duration": info.duration,
            "segments": seg_count
        }

    except Exception as e:
        log_event("TRANSCRIBE", f"Erro na transcricao: {str(e)}", level="error")
        return {
            "success": False,
            "project": project_name,
            "error": str(e)
        }