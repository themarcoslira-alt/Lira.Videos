"""
_transcrever_subprocesso.py — Subprocesso isolado de transcricao (faster-whisper)
Executado via -c inline pelo transcriber.py (evita segfault do ctranslate2)
Recebe 3 args: arquivo_video, project_name, output_path
"""
import sys, json, os
from pathlib import Path

def main():
    if len(sys.argv) < 4:
        print(json.dumps({"success": False, "error": "Argumentos insuficientes"}))
        return 1

    arquivo_video = sys.argv[1]
    project_name = sys.argv[2]
    output_path = sys.argv[3]

    from faster_whisper import WhisperModel

    model = WhisperModel("tiny", device="cpu", compute_type="float32", cpu_threads=2, num_workers=1)

    print(f"[SUBPROCESSO] Iniciando transcricao: {arquivo_video}", flush=True)
    print(f"[SUBPROCESSO] Projeto: {project_name}", flush=True)

    segments, info = model.transcribe(arquivo_video, beam_size=5, language="en", vad_filter=False)

    total_duration = info.duration if info and info.duration else 0
    print(f"[SUBPROCESSO] Duracao total: {total_duration:.2f}s", flush=True)

    linhas_txt = []
    segmentos = []
    idx = 0

    for seg in segments:
        start = seg.start
        end = seg.end
        text = seg.text.strip()
        if not text:
            continue

        mins_s, secs_s = int(start // 60), int(start % 60)
        timestamp = f"{mins_s:02d}:{secs_s:02d}"

        linhas_txt.append(f"[{timestamp}] {text}")
        segmentos.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "text": text,
            "timestamp": timestamp
        })

        progress = (start / total_duration * 100) if total_duration > 0 else 0
        print(f"[TRANSCREVENDO] {progress:.1f}% do audio transcrito", flush=True)

        idx += 1

    # Salva TXT
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(linhas_txt))
        print(f"[SUBPROCESSO] TXT salvo: {output_path}", flush=True)
    except Exception as e:
        print(f"[SUBPROCESSO] Erro ao salvar TXT: {e}", flush=True)

    # Salva JSON
    try:
        project_dir = Path(output_path).parent
        json_path = project_dir / "roteiro_transcricao.json"
        json_data = {
            "segments": segmentos,
            "segment_count": len(segmentos),
            "duration": round(total_duration, 2),
            "language": info.language if info else "pt"
        }
        with open(str(json_path), "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        print(f"[SUBPROCESSO] JSON salvo: {json_path}", flush=True)
    except Exception as e:
        print(f"[SUBPROCESSO] Erro ao salvar JSON: {e}", flush=True)

    # Resultado final (JSON na ultima linha)
    resultado = {
        "success": True,
        "segments": len(segmentos),
        "texto": "\n".join(linhas_txt),
        "arquivo": output_path
    }
    print(json.dumps(resultado), flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())