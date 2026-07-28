import sys, traceback
log = open(r"C:\ultracut3\logs\test_openai310_result.txt", "w", encoding="utf-8")
log.write(f"Python: {sys.version}\n")
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
    if result['text']:
        log.write(f"Texto: {result['text'][:200]}...\n")
    log.write("SUCESSO!\n")
except Exception as e:
    log.write(f"EXCECAO: {traceback.format_exc()}\n")
log.write("FIM\n")
log.close()