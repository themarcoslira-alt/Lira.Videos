"""
video_encoder.py — Codificação de vídeo com h264_amf (AMD GPU)
Proteção contra arquivo truncado: renderiza para .tmp.mp4, valida, depois renomeia.
"""
import subprocess
import json
from pathlib import Path
from config import VIDEO_ENCODER, VIDEO_ENCODER_OPTIONS, OUTPUT_DIR


def _validar_arquivo(arquivo: Path) -> bool:
    """Valida arquivo de vídeo com ffprobe."""
    try:
        # Testa se consegue ler o arquivo
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
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
        # Verifica pix_fmt=yuv420p
        if stream.get("pix_fmt") != "yuv420p":
            return False

        # Testa se consegue decodificar um frame
        result2 = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(arquivo),
             "-frames:v", "1", "-f", "null", "-"],
            capture_output=True, text=True, timeout=15
        )
        return result2.returncode == 0

    except Exception:
        return False


def renderizar_video(arquivos_entrada: list, arquivo_audio: str,
                     nome_saida: str) -> dict:
    """
    Renderiza vídeo final com h264_amf.
    Etapas:
    1. Renderiza para output/<nome>.tmp.mp4
    2. Valida com ffprobe
    3. Só após validação, renomeia para nome final
    """
    saida_tmp = OUTPUT_DIR / f"{nome_saida}.tmp.mp4"
    saida_final = OUTPUT_DIR / f"{nome_saida}.mp4"

    # Remove arquivos temporários anteriores
    if saida_tmp.exists():
        saida_tmp.unlink()

    # Concatena arquivos de entrada
    concat_file = OUTPUT_DIR / f"{nome_saida}_concat.txt"
    try:
        with open(concat_file, "w") as f:
            for arquivo in arquivos_entrada:
                f.write(f"file '{Path(arquivo).resolve()}'\n")

        comando = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-c:v", VIDEO_ENCODER
        ] + VIDEO_ENCODER_OPTIONS + [
            "-i", arquivo_audio,
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(saida_tmp)
        ]

        result = subprocess.run(
            comando,
            capture_output=True, text=True, timeout=600
        )

        if result.returncode != 0:
            if saida_tmp.exists():
                saida_tmp.unlink()
            return {
                "success": False,
                "error": f"ffmpeg retornou código {result.returncode}",
                "stderr": result.stderr[-500:] if result.stderr else ""
            }

        # Valida arquivo temporário
        if not _validar_arquivo(saida_tmp):
            saida_tmp.unlink()
            return {
                "success": False,
                "error": "Arquivo temporário falhou na validação"
            }

        # Renomeia para nome final (atômico)
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
        return {
            "success": False,
            "error": str(e)
        }
    finally:
        if concat_file.exists():
            concat_file.unlink()