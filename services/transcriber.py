"""
transcriber.py — Transcrição de áudio/vídeo com faster-whisper
Armazena:
  - roteiro_transcricao.txt  (formato [MM:SS] texto, compatibilidade)
  - roteiro_transcricao.json (segmentos estruturados com start/end/text)
"""
import os
from pathlib import Path
import json
from config import WHISPER_MODEL_SIZE, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE, WHISPER_CPU_THREADS, WHISPER_NUM_WORKERS, PROJETOS_DIR


import threading
import time


# Cache do modelo Whisper (compartilhado entre chamadas)
_whisper_model = None
_whisper_model_lock = threading.Lock()

# Callback global opcional para progresso em tempo real
_callback_progresso = None


def set_progress_callback(fn):
    """Define callback fn(project_name, timestamp_str, pct) chamado a cada segmento."""
    global _callback_progresso
    _callback_progresso = fn


def _obter_modelo():
    """Retorna o modelo Whisper em cache (carrega apenas na primeira chamada)."""
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model
    with _whisper_model_lock:
        if _whisper_model is not None:
            return _whisper_model
        from faster_whisper import WhisperModel
        kwargs = {
            "model_size_or_path": WHISPER_MODEL_SIZE,
            "device": WHISPER_DEVICE,
            "compute_type": WHISPER_COMPUTE_TYPE,
            "num_workers": WHISPER_NUM_WORKERS,
        }
        if WHISPER_CPU_THREADS is not None:
            kwargs["cpu_threads"] = WHISPER_CPU_THREADS
        else:
            kwargs["cpu_threads"] = os.cpu_count()
        _whisper_model = WhisperModel(**kwargs)
        return _whisper_model


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

    project_dir = PROJETOS_DIR / project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    saida_txt = project_dir / "roteiro_transcricao.txt"
    saida_json = project_dir / "roteiro_transcricao.json"

    try:
        # Obtem/cria modelo em cache (thread-safe, primeira vez carrega, reutiliza depois)
        modelo_ja_existia = _whisper_model is not None
        model = _obter_modelo()
        if not modelo_ja_existia:
            log_event("TRANSCRIBE", "Modelo faster-whisper carregado em cache para reuso.", level="info")
        else:
            log_event("TRANSCRIBE", "Reutilizando modelo faster-whisper em cache (0s de carregamento).", level="info")

        log_event("TRANSCRIBE", "Iniciando transcricao do audio...", level="info")
        segments, info = model.transcribe(arquivo_video, language="pt")

        linhas_txt = []
        full_text = []
        segmentos_json = []
        seg_count = 0
        duracao_total = info.duration if hasattr(info, 'duration') and info.duration else 1

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
            pct = int((seg.start / duracao_total) * 100) if duracao_total > 0 else 0
            log_event("TRANSCRIBE",
                f"Segmento {seg_count} | [{timestamp}] | {pct}% do audio transcrito",
                level="info")

            # Callback de progresso para GUI (a cada segmento = ~1-2s)
            if _callback_progresso:
                try:
                    _callback_progresso(project_name, timestamp, pct)
                except Exception:
                    pass

        # Salva TXT (compatibilidade)
        with open(saida_txt, "w", encoding="utf-8") as f:
            f.write("\n".join(linhas_txt))

        # Salva JSON estruturado (fonte de verdade temporal)
        transcricao_data = {
            "project": project_name,
            "duration": round(duracao_total, 2),
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
                  details={"segments": seg_count, "language": info.language, "duration": duracao_total})

        # Marca transcricao como completa no meta.json do projeto
        meta_path = PROJETOS_DIR / project_name / "meta.json"
        try:
            if meta_path.exists():
                meta = json.loads(open(str(meta_path), "r", encoding="utf-8").read())
                meta["transcricao_completa"] = True
                with open(str(meta_path), "w", encoding="utf-8") as f:
                    json.dump(meta, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

        return {
            "success": True,
            "project": project_name,
            "arquivo": str(saida_txt),
            "texto": texto_completo,
            "language": info.language,
            "duration": duracao_total,
            "segments": seg_count
        }

    except Exception as e:
        log_event("TRANSCRIBE", f"Erro na transcricao: {str(e)}", level="error")
        return {
            "success": False,
            "project": project_name,
            "error": str(e)
        }