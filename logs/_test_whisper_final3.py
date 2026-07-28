"""Teste final - openai-whisper com numpy<2 no .venv310"""
import sys, os, traceback

log_path = r"C:\ultracut3\logs\test_whisper_final3.txt"
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
        
        audio = r"C:\ultracut3\projetos\AAAA\AAAA.MP3"
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