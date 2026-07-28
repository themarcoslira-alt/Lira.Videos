"""Script de teste isolado para WhisperModel - salva saida em arquivo"""
import sys, os, traceback
sys.path.insert(0, r"C:\ultracut3")
os.environ['PYTHONUNBUFFERED'] = '1'

log_path = r"C:\ultracut3\logs\test_whisper_result.txt"
with open(log_path, "w", encoding="utf-8") as log:
    log.write(f"Python: {sys.version}\n")
    log.flush()
    
    try:
        from faster_whisper import WhisperModel
        log.write("Import WhisperModel: OK\n")
        log.flush()
        
        kwargs = {
            "model_size_or_path": "base",
            "device": "cpu",
            "compute_type": "int8",
            "num_workers": 2,
            "cpu_threads": 16,
        }
        log.write(f"kwargs: {kwargs}\n")
        log.write("[CHECKPOINT] Antes de WhisperModel()\n")
        log.flush()
        
        model = WhisperModel(**kwargs)
        log.write("[CHECKPOINT] WhisperModel() OK\n")
        log.flush()
        
        # Testa transcricao
        arquivo = r"C:\ultracut3\projetos\AAAA\AAAA.MP3"
        log.write(f"[CHECKPOINT] Antes de transcribe({arquivo})\n")
        log.flush()
        
        segments, info = model.transcribe(arquivo, language="pt")
        log.write(f"[CHECKPOINT] transcribe() retornou: {info.language if info else '?'}\n")
        log.flush()
        
        for seg in segments:
            log.write(f"  seg: {seg.start:.1f}-{seg.end:.1f}: {seg.text[:50]}...\n")
            log.flush()
            break  # so primeiro segmento
        
        log.write("TRANSCRICAO COMPLETA COM SUCESSO!\n")
        log.flush()
        
    except Exception as e:
        log.write(f"EXCECAO: {traceback.format_exc()}\n")
        log.flush()
    except BaseException as e:
        log.write(f"BASE_EXCECAO: {type(e).__name__}: {e}\n")
        log.flush()
    
    log.write("FIM\n")