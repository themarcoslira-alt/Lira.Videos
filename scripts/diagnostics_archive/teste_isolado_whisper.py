"""Script de teste minimo para isolar crash do faster-whisper / ctranslate2"""
import sys, os

log_path = r"C:\ultracut3\logs\teste_isolado_resultado.txt"
log = open(log_path, "w", encoding="utf-8")
log.write(f"Python: {sys.version}\n")
log.write(f"sys.executable: {sys.executable}\n")
log.flush()

try:
    log.write("[CHECKPOINT] Importando faster_whisper...\n")
    log.flush()
    from faster_whisper import WhisperModel
    log.write("[CHECKPOINT] Import OK\n")
    log.flush()

    log.write("[CHECKPOINT] Carregando modelo base com compute_type='int8'...\n")
    log.flush()
    model = WhisperModel("base", device="cpu", compute_type="int8")
    log.write("[CHECKPOINT] Modelo carregado com sucesso!\n")
    log.flush()

    print("Modelo carregado com sucesso", flush=True)
except Exception as e:
    import traceback
    tb = traceback.format_exc()
    log.write(f"EXCECAO: {tb}\n")
    log.flush()
    print(f"EXCECAO: {type(e).__name__}: {e}", flush=True)

log.write("FIM\n")
log.close()