"""Teste final - openai-whisper com numpy<2 no .venv310"""
# --- raiz ATUAL do repositorio (antes: C:\ultracut3 hardcoded) ---
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent
import sys, os, traceback

log_path = str(ROOT_DIR / "logs" / "test_whisper_final3.txt")
with open(log_path, "w", encoding="utf-8") as log:
    log.write(f"Python: {sys.version}\n")
    log.write(f"sys.executable: {sys.executable}\n")
    log.flush()
    
    try:
        import whisper
        log.write("Import OK\n")
        log.flush()
        
        log.write("Carregando modelo tiny...\n")
        log.flush()
        model = whisper.load_model("tiny")
        log.write("Modelo tiny carregado OK\n")
        log.flush()
        
        audio = str(ROOT_DIR / "projetos" / "AAAA" / "AAAA.MP3")
        log.write(f"Transcrevendo {audio}...\n")
        log.flush()
        
        result = model.transcribe(audio, language="pt")
        log.write(f"Transcricao OK: {len(result['text'])} chars, {len(result.get('segments',[]))} segmentos\n")
        log.flush()
        
        if result['text']:
            log.write(f"Texto (inicio): {result['text'][:200]}...\n")
            log.flush()
        
        log.write("SUCESSO COMPLETO!\n")
        
    except Exception as e:
        log.write(f"EXCECAO: {traceback.format_exc()}\n")
        log.flush()
    
    log.write("FIM\n")