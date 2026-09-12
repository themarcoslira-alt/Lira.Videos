# --- raiz ATUAL do repositorio (antes: C:\ultracut3 hardcoded) ---
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent
import sys, traceback
sys.stdout = open(str(ROOT_DIR / "logs" / "whisper_final_result.txt"), "w", encoding="utf-8")
sys.stderr = sys.stdout

print(f"Python: {sys.version}")

try:
    import whisper
    print("Import OK")
    
    print("Carregando modelo tiny...", flush=True)
    model = whisper.load_model("tiny")
    print("Modelo tiny carregado OK", flush=True)
    
    audio = str(ROOT_DIR / "projetos" / "AAAA" / "AAAA.MP3")
    print(f"Transcrevendo {audio}...", flush=True)
    result = model.transcribe(audio, language="pt")
    print(f"Transcricao OK: {len(result['text'])} chars, {len(result.get('segments',[]))} segmentos", flush=True)
    if result['text']:
        print(f"Texto: {result['text'][:200]}...")
    print("SUCESSO!", flush=True)
except Exception as e:
    print(f"EXCECAO: {traceback.format_exc()}", flush=True)
print("FIM", flush=True)