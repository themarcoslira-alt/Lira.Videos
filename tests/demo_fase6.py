"""Demo BLOCO 6 — consistência de segmentação (3 execuções do mesmo áudio).

Extrai um clip de 60s do áudio real e roda o subprocesso de transcrição 3x,
reportando quantidade de segmentos e duração média por segmento.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

BASE = r"C:\ultracut3"
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from config import FFMPEG_PATH  # noqa: E402

AUDIO_REAL = (r"C:\ultracut3\projetos\Why His Lawn Is Greener - He Checks For This Every Week"
              r"\Why His Lawn Is Greener - He Checks For This Every Week.MP3")
PY = r"C:\ultracut3\.venv\Scripts\python.exe"
SUB = r"C:\ultracut3\_transcrever_subprocesso.py"


def main():
    tmp = tempfile.mkdtemp(prefix="_fase6_")
    clip = os.path.join(tmp, "clip.wav")
    print("extraindo clip de 60s...")
    subprocess.run(
        [FFMPEG_PATH, "-y", "-v", "error", "-ss", "0", "-t", "60",
         "-i", AUDIO_REAL, "-ar", "16000", "-ac", "1", clip],
        check=True,
    )

    resultados = []
    for i in range(1, 4):
        out_txt = os.path.join(tmp, f"run{i}", "roteiro_transcricao.txt")
        os.makedirs(os.path.dirname(out_txt), exist_ok=True)
        inicio = time.time()
        r = subprocess.run([PY, SUB, clip, f"run{i}", out_txt],
                           capture_output=True, text=True, timeout=300)
        dur = time.time() - inicio
        json_path = os.path.join(os.path.dirname(out_txt), "roteiro_transcricao.json")
        data = json.loads(open(json_path, encoding="utf-8").read())
        segs = data["segments"]
        n = len(segs)
        medias = [float(s["end"]) - float(s["start"]) for s in segs]
        avg = (sum(medias) / n) if n else 0.0
        resultados.append((n, avg))
        print(f"run {i}: {n} segmentos | duracao media {avg:.2f}s | tempo {dur:.1f}s")
        for s in segs[:3]:
            print("   ", f"[{s['timestamp']}]", s["text"][:70])
        if r.returncode != 0:
            print("   [erro] stdout:", (r.stdout or "")[-300:])

    counts = [x[0] for x in resultados]
    avgs = [round(x[1], 2) for x in resultados]
    print("\nconsistencia:")
    print("  quantidades:", counts)
    print("  duracao media:", avgs)
    print("  mesma quantidade em todas?", len(set(counts)) == 1)
    print("  mesma duracao media (1 casa)?", len(set(round(a, 1) for a in avgs)) == 1)

    shutil.rmtree(tmp, ignore_errors=True)
    print("cleanup OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
