"""
video_builder.py — Montagem do vídeo final combinando cenas + B-roll
"""
import json
from pathlib import Path
from config import PROJETOS_DIR


def construir_video(project_name: str) -> dict:
    """
    Constrói o vídeo final combinando as mídias encontradas com o áudio original.
    Prepara a lista de arquivos para o video_encoder.
    """
    project_dir = PROJETOS_DIR / project_name
    midias_file = project_dir / "midias_encontradas.json"
    cenas_file = project_dir / "cenas.json"

    if not midias_file.exists():
        return {"success": False, "error": "midias_encontradas.json não encontrado"}
    if not cenas_file.exists():
        return {"success": False, "error": "cenas.json não encontrado"}

    with open(midias_file, "r", encoding="utf-8") as f:
        midias = json.load(f)
    with open(cenas_file, "r", encoding="utf-8") as f:
        cenas = json.load(f)

    arquivos_video = []
    cenas_com_midia = 0
    cenas_sem_midia = 0

    for midia in midias:
        if midia.get("success") and midia.get("arquivo"):
            arquivo = midia["arquivo"]
            if Path(arquivo).exists():
                arquivos_video.append(arquivo)
                cenas_com_midia += 1
            else:
                cenas_sem_midia += 1
        else:
            cenas_sem_midia += 1

    # Procura áudio original
    audio_original = project_dir / "audio_original.mp4"
    if not audio_original.exists():
        # Tenta achar o vídeo de entrada
        for ext in [".mp4", ".avi", ".mov", ".mkv"]:
            possivel = project_dir / f"input{ext}"
            if possivel.exists():
                audio_original = possivel
                break

    return {
        "success": True,
        "arquivos_video": arquivos_video,
        "arquivo_audio": str(audio_original) if audio_original.exists() else None,
        "cenas_com_midia": cenas_com_midia,
        "cenas_sem_midia": cenas_sem_midia,
        "total_cenas": len(midias)
    }