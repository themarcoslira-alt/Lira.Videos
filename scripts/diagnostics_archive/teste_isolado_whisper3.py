"""Teste com variaveis de ambiente que funcionou antes"""
import sys, os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OMP_WAIT_POLICY"] = "PASSIVE"
os.environ["OMP_NUM_THREADS"] = "2"

log = open(r"C:\ultracut3\logs\teste_env.txt", "w", encoding="utf-8")
log.write(f"Python: {sys.version}\n")
log.write(f"KMP_DUPLICATE_LIB_OK: {os.environ.get('KMP_DUPLICATE_LIB_OK', 'N/A')}\n")
log.write(f"OMP_NUM_THREADS: {os.environ.get('OMP_NUM_THREADS', 'N/A')}\n")
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