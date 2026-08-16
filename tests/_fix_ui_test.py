"""Teste REAL do caminho da UI (correção do Bloco 6 no pai).

Cria projeto novo (como a interface), dispara a transcrição pelo MESMO endpoint
que o botão "Transcrever com WhisperX" usa (POST /api/upload_audio/<id>), aguarda
pelo polling de status e exibe o conteúdo EXATO do textarea do card 2
(GET /api/transcricao). Repetido 2x com projetos novos.
Grava o log em _fix_ui_utf8.txt (UTF-8) para leitura estável.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, r"C:\ultracut3")

from config import FFMPEG_PATH, PROJETOS_DIR  # noqa: E402
import app_web  # noqa: E402

OUT = r"C:\ultracut3\_fix_ui_utf8.txt"


def log(*a):
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(" ".join(str(x) for x in a) + "\n")


client = app_web.app.test_client()
AUDIO_REAL = (r"C:\ultracut3\projetos\Why His Lawn Is Greener - He Checks For This Every Week"
              r"\Why His Lawn Is Greener - He Checks For This Every Week.MP3")


def aguardar_transcricao(nome, timeout=240):
    inicio = time.time()
    while time.time() - inicio < timeout:
        st = client.get(f"/api/status/{nome}").get_json()
        if st.get("transcricao_completa"):
            return round(time.time() - inicio)
        time.sleep(2)
    return None


def main():
    try:
        tmp = tempfile.mkdtemp(prefix="_fix_ui_")
        clip = os.path.join(tmp, "clip_60s.wav")
        log("extraindo clip de 60s do audio real...")
        subprocess.run(
            [FFMPEG_PATH, "-y", "-v", "error", "-ss", "0", "-t", "60",
             "-i", AUDIO_REAL, "-ar", "16000", "-ac", "1", clip],
            check=True,
        )

        with open(clip, "rb") as fh:
            bytes_clip = fh.read()

        for run in (1, 2):
            nome = f"_fix_ui_run{run}"
            client.post(f"/api/deletar_projeto/{nome}")

            # 1) criar projeto novo (modo manual, como a interface)
            r = client.post("/api/criar_projeto", data={
                "nome": nome, "modo": "manual",
                "audio": (io.BytesIO(bytes_clip), "clip_60s.wav"),
            }, content_type="multipart/form-data")
            log(f"[run {run}] criar_projeto:", r.status_code, r.get_json())

            # 2) botão "Transcrever com WhisperX" -> endpoint real
            r = client.post(f"/api/upload_audio/{nome}", data={
                "audio": (io.BytesIO(bytes_clip), "clip_60s.wav"),
            }, content_type="multipart/form-data")
            log(f"[run {run}] upload_audio (dispara transcricao):", r.status_code, r.get_json())

            # 3) aguarda conclusão (polling como o frontend)
            seg = aguardar_transcricao(nome)
            log(f"[run {run}] transcricao concluida em {seg}s" if seg else f"[run {run}] TIMEOUT")
            if not seg:
                continue

            # 4) texto EXATO que o textarea do card 2 exibe (GET /api/transcricao)
            tr = client.get(f"/api/transcricao/{nome}").get_json()
            linhas = (tr.get("texto") or "").splitlines()
            log(f"[run {run}] CARD 2 TEXTAREA — {len(linhas)} linhas:")
            for l in linhas:
                log("   ", l)

            # 5) estatísticas + alinhamento do word_timestamps
            proj = PROJETOS_DIR / nome
            rt = json.loads((proj / "roteiro_transcricao.json").read_text(encoding="utf-8"))
            segs = rt["segments"]
            medias = [float(s["end"]) - float(s["start"]) for s in segs]
            log(f"[run {run}] roteiro: {len(segs)} segmentos | duração média "
                f"{(sum(medias) / len(medias) if medias else 0):.2f}s | fonte={rt.get('fonte')}")
            wt_path = proj / "word_timestamps.json"
            if wt_path.exists():
                wt = json.loads(wt_path.read_text(encoding="utf-8"))
                wt_segs = wt.get("segments", [])
                com_words = sum(1 for s in wt_segs if s.get("words"))
                log(f"[run {run}] word_timestamps: {len(wt_segs)} segmentos | com words: {com_words} | "
                    f"alinhado={len(wt_segs) == len(segs)}")

            client.post(f"/api/deletar_projeto/{nome}")
            log(f"[run {run}] cleanup OK")

        shutil.rmtree(tmp, ignore_errors=True)
        log("FIM")
    except Exception as e:
        import traceback
        log("ERRO:", repr(e))
        log(traceback.format_exc())
    return 0


if __name__ == "__main__":
    sys.exit(main())
