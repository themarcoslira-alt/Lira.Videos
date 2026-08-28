"""Teste isolado com float32 - que funcionou antes"""
import sys, os
log = open(r"C:\ultracut3\logs\teste_float32.txt", "w", encoding="utf-8")
log.write(f"Python: {sys.version}\n")
log.flush()

try:
    from faster_whisper import WhisperModel
    log.write("Import OK\n")
    log.flush()
    log.write("Carregando base com float32...\n")
    log.flush()
    model = WhisperModel("base", device="cpu", compute_type="float32", cpu_threads=2)
    log.write("MODELO CARREGADO COM SUCESSO!\n")
    log.flush()
    print("OK", flush=True)
except Exception as e:
    import traceback
    log.write(f"EXC: {traceback.format_exc()}\n")
    log.flush()
log.write("FIM\n")
log.close()