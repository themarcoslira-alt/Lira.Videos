"""Teste minimo - importa ctranslate2 diretamente"""
import sys, os, traceback
sys.path.insert(0, r"C:\ultracut3")
os.environ['PYTHONUNBUFFERED'] = '1'

log_path = r"C:\ultracut3\logs\test_ct2_result.txt"
with open(log_path, "w", encoding="utf-8") as log:
    log.write(f"Python: {sys.version}\n")
    log.flush()
    
    try:
        log.write("[CHECKPOINT] Antes de import ctranslate2\n")
        log.flush()
        import ctranslate2
        log.write(f"[CHECKPOINT] ctranslate2 import OK: {ctranslate2.__version__ if hasattr(ctranslate2, '__version__') else '?'}\n")
        log.flush()
        
        # Tenta criar modelo com cache local
        import huggingface_hub
        log.write(f"[CHECKPOINT] huggingface_hub OK\n")
        log.flush()
        
        # Tenta baixar modelo tiny
        model_path = "base"  # usa cache
        log.write(f"[CHECKPOINT] Antes de ctranslate2.models.Whisper({model_path})\n")
        log.flush()
        
        from ctranslate2.models import Whisper
        model = Whisper(model_path)
        log.write(f"[CHECKPOINT] Whisper model carregado\n")
        log.flush()
        
    except Exception as e:
        log.write(f"EXCECAO: {traceback.format_exc()}\n")
        log.flush()
    
    log.write("FIM\n")