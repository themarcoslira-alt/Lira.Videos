"""Teste final completo - openai-whisper + numpy<2 + ffmpeg no PATH"""
import os
import sys
import traceback

log_path = r"C:\ultracut3\logs\test_final_completo.txt"
log = open(log_path, "w", encoding="utf-8")
log.write(f"Python: {sys.version}\n")
log.write(f"sys.executable: {sys.executable}\n")
log.flush()

# Adiciona ffmpeg ao PATH
ffmpeg_dir = r"C:\ultracut3\ffmpeg\ffmpeg-8.1.2-essentials_build\bin"
if os.path.isdir(ffmpeg_dir):
    os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ["PATH"]
    log.write(f"ffmpeg dir adicionado ao PATH: {ffmpeg_dir}\n")
else:
    log.write(f"ERRO: ffmpeg dir nao encontrado: {ffmpeg_dir}\n")
log.flush()

try:
    import subprocess
    r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
    log.write(f"ffmpeg OK (codigo {r.returncode})\n")
    log.flush()

    import whisper
    log.write("whisper import OK\n")
    log.flush()

    model = whisper.load_model("tiny")
    log.write("Modelo tiny carregado OK\n")
    log.flush()

    audio = r"C:\ultracut3\projetos\AAAA\AAAA.MP3"
    log.write(f"Transcrevendo {audio}...\n")
    log.flush()

    result = model.transcribe(audio, language="pt")
    log.write(f"Transcricao OK: {len(result['text'])} chars, {len(result.get('segments', []))} segmentos\n")
    log.flush()

    if result.get("segments"):
        s = result["segments"][0]
        log.write(f"Primeiro segmento: {s.get('start', 0):.1f}s - {s.get('end', 0):.1f}s: {s.get('text', '').strip()[:80]}\n")
        log.flush()

    if result.get("text"):
        log.write(f"Texto (inicio): {result['text'][:200]}...\n")
        log.flush()

    log.write("SUCESSO COMPLETO!\n")
except Exception as e:
    log.write(f"EXCECAO: {traceback.format_exc()}\n")
    log.flush()
except BaseException as e:
    log.write(f"BASE_EXC: {type(e).__name__}: {e}\n")
    log.flush()

log.write("FIM\n")
log.close()
print("TESTE CONCLUIDO", flush=True)