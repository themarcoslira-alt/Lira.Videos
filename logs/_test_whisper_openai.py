"""Teste openai-whisper - salva saida completa em arquivo"""
import sys, os, json, traceback
sys.path.insert(0, r"C:\ultracut3")
os.environ['PYTHONUNBUFFERED'] = '1'

log_path = r"C:\ultracut3\logs\test_openai_result.txt"
with open(log_path, "w", encoding="utf-8") as log:
    log.write(f"Python: {sys.version}\n")
    log.flush()
    
    try:
        import whisper
        log.write("whisper import OK\n")
        log.flush()
        
        log.write("[CHECKPOINT] Carregando modelo tiny...\n")
        log.flush()
        model = whisper.load_model("tiny")
        log.write("[CHECKPOINT] Modelo tiny carregado\n")
        log.flush()
        
        arquivo = r"C:\ultracut3\projetos\AAAA\AAAA.MP3"
        log.write(f"[CHECKPOINT] Transcrevendo {arquivo}...\n")
        log.flush()
        
        result = model.transcribe(arquivo, language="pt")
        log.write(f"[CHECKPOINT] Transcricao concluida!\n")
        log.flush()
        log.write(f"  Texto: {len(result['text'])} chars\n")
        log.write(f"  Segmentos: {len(result.get('segments', []))}\n")
        log.flush()
        
        # Exibe primeiros 200 chars
        if result['text']:
            log.write(f"  Texto (inicio): {result['text'][:200]}...\n")
        
        log.write("TRANSCRICAO COMPLETA COM SUCESSO!\n")
        log.flush()
        
    except Exception as e:
        log.write(f"EXCECAO: {traceback.format_exc()}\n")
        log.flush()
    
    log.write("FIM\n")