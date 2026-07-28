"""Teste do faster-whisper no .venv310 - escreve tudo em arquivo"""
import sys, os, json, traceback

log_path = r"C:\ultracut3\logs\test_venv310_result.txt"
log = open(log_path, "w", encoding="utf-8")
log.write(f"Python: {sys.version}\n")
log.write(f"sys.path: {sys.path}\n")
log.flush()

try:
    log.write("[CHECKPOINT] Importando faster_whisper...\n")
    log.flush()
    from faster_whisper import WhisperModel
    log.write("[CHECKPOINT] Import OK\n")
    log.flush()
    
    kwargs = {
        "model_size_or_path": "tiny",
        "device": "cpu",
        "compute_type": "int8",
        "num_workers": 1,
        "cpu_threads": 4,
    }
    log.write(f"[CHECKPOINT] kwargs: {kwargs}\n")
    log.write("[CHECKPOINT] Antes de WhisperModel()\n")
    log.flush()
    
    model = WhisperModel(**kwargs)
    log.write("[CHECKPOINT] Depois de WhisperModel()\n")
    log.flush()
    
    # Testa transcricao
    audio = r"C:\ultracut3\projetos\AAAA\AAAA.MP3"
    log.write(f"[CHECKPOINT] Antes de transcribe({audio})\n")
    log.flush()
    
    segments, info = model.transcribe(audio, language="pt")
    log.write(f"[CHECKPOINT] transcribe() retornou\n")
    log.write(f"  language: {info.language if info else '?'}\n")
    log.write(f"  duration: {info.duration if info else '?'}\n")
    log.flush()
    
    for i, seg in enumerate(segments):
        if i >= 3:
            break
        log.write(f"  seg[{i}]: {seg.start:.1f}-{seg.end:.1f}: {seg.text.strip()[:80]}\n")
        log.flush()
    
    log.write("SUCESSO!\n")
    
except Exception as e:
    log.write(f"EXCECAO: {traceback.format_exc()}\n")
    log.flush()
except BaseException as e:
    log.write(f"BASE_EXC: {type(e).__name__}: {e}\n")
    log.flush()

log.write("FIM\n")
log.close()