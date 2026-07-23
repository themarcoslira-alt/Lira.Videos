"""
video_encoder.py — Codificação de vídeo com h264_amf (AMD GPU)
Aceita lista de MP4s pré-processados do video_builder (sem audio proprio)
Concatena clipes e adiciona audio original como trilha
"""
import subprocess, json, shutil
from pathlib import Path
from config import VIDEO_ENCODER, VIDEO_ENCODER_OPTIONS, OUTPUT_DIR, FFMPEG_PATH, FFPROBE_PATH


def _validar_arquivo(arquivo: Path) -> bool:
    try:
        result = subprocess.run(
            [FFPROBE_PATH, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=pix_fmt,profile",
             "-of", "json", str(arquivo)],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return False
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        if not streams:
            return False
        stream = streams[0]
        if stream.get("pix_fmt") != "yuv420p":
            return False
        result2 = subprocess.run(
            [FFMPEG_PATH, "-v", "error", "-i", str(arquivo),
             "-frames:v", "1", "-f", "null", "-"],
            capture_output=True, text=True, timeout=15
        )
        return result2.returncode == 0
    except Exception:
        return False


def renderizar_video(arquivos_entrada: list, arquivo_audio: str,
                     nome_saida: str) -> dict:
    """
    Renderiza video final.
    Entrada: lista de MP4s pre-processados (sem audio proprio)
    Audio: arquivo_audio original (mp3/mp4) mapeado como trilha unica
    """
    from services.event_logger import log_event
    log_event("RENDER", f"Iniciando render: {len(arquivos_entrada)} clips, saida={nome_saida}", level="info")
    saida_tmp = OUTPUT_DIR / f"{nome_saida}.tmp.mp4"
    saida_final = OUTPUT_DIR / f"{nome_saida}.mp4"

    if saida_tmp.exists():
        saida_tmp.unlink()

    concat_file = OUTPUT_DIR / f"{nome_saida}_concat.txt"
    try:
        with open(concat_file, "w") as f:
            for arquivo in arquivos_entrada:
                f.write(f"file '{Path(arquivo).resolve()}'\n")

        comando = [
            FFMPEG_PATH, "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-i", arquivo_audio,
            "-c:v", VIDEO_ENCODER
        ] + VIDEO_ENCODER_OPTIONS + [
            "-c:a", "aac", "-b:a", "192k",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
            str(saida_tmp)
        ]

        result = subprocess.run(comando, capture_output=True, text=True, timeout=600)

        if result.returncode != 0:
            if saida_tmp.exists():
                saida_tmp.unlink()
            return {
                "success": False,
                "error": f"ffmpeg retornou codigo {result.returncode}",
                "stderr": result.stderr[-500:] if result.stderr else ""
            }

        if not _validar_arquivo(saida_tmp):
            saida_tmp.unlink()
            return {"success": False, "error": "Arquivo temporario falhou na validacao"}

        if saida_final.exists():
            saida_final.unlink()
        saida_tmp.rename(saida_final)

        return {
            "success": True,
            "arquivo": str(saida_final),
            "tamanho": saida_final.stat().st_size if saida_final.exists() else 0
        }

    except Exception as e:
        if saida_tmp.exists():
            saida_tmp.unlink()
        return {"success": False, "error": str(e)}
    finally:
        if concat_file.exists():
            concat_file.unlink()
