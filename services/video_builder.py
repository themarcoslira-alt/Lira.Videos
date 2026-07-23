"""
video_builder.py — Montagem do vídeo final combinando cenas + B-roll
Pre-processa cada clipe: fotos viram video, videos tem audio removido e cortados
"""
import json, subprocess, os
from pathlib import Path
from config import PROJETOS_DIR, FFMPEG_PATH, FFPROBE_PATH


def _extrair_duracao_cena(project_name: str, cena_id: int) -> float:
    """Extrai duracao da cena a partir dos timestamps do cenas.json."""
    project_dir = PROJETOS_DIR / project_name
    cenas_file = project_dir / "cenas.json"
    if not cenas_file.exists():
        return 4.0
    with open(cenas_file, "r", encoding="utf-8") as f:
        cenas = json.load(f)
    cena = None
    for c in cenas:
        if c["id"] == cena_id:
            cena = c
            break
    if not cena or not cena.get("timestamps"):
        return 4.0
    timestamps = cena["timestamps"]
    def ts_to_seconds(ts: str) -> float:
        partes = ts.split(":")
        return int(partes[0]) * 60 + int(partes[1])
    inicio = ts_to_seconds(timestamps[0])
    fim = ts_to_seconds(timestamps[-1]) + 5
    duracao = fim - inicio
    return max(duracao, 3.0)


def _preprocessar_midia(arquivo_entrada: str, scene_id: int,
                         duracao: float, cache_dir: Path) -> str:
    """
    Pre-processa uma midia para video padrao.
    - Foto: converte para video com loop, duracao exata
    - Video: remove audio, corta/loop para duracao
    """
    entrada = Path(arquivo_entrada)
    saida = cache_dir / f"scene_{scene_id}_processed.mp4"
    if saida.exists():
        return str(saida)
    ext = entrada.suffix.lower()
    if ext in (".jpg", ".jpeg", ".png", ".webp"):
        cmd = [
            FFMPEG_PATH, "-y",
            "-loop", "1",
            "-i", str(entrada.resolve()),
            "-t", str(duracao),
            "-vf", "scale=1920:1080:force_original_aspect_ratio=1,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1:1",
            "-pix_fmt", "yuv420p",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            str(saida)
        ]
    else:
        probe = subprocess.run(
            [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(entrada.resolve())],
            capture_output=True, text=True, timeout=10
        )
        try:
            dur_video = float(probe.stdout.strip())
        except (ValueError, TypeError):
            dur_video = duracao
        if dur_video >= duracao:
            cmd = [
                FFMPEG_PATH, "-y",
                "-i", str(entrada.resolve()),
                "-an",
                "-t", str(duracao),
                "-vf", "scale=1920:1080:force_original_aspect_ratio=1,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1:1",
                "-pix_fmt", "yuv420p",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                str(saida)
            ]
        else:
            concat_txt = cache_dir / f"scene_{scene_id}_loop.txt"
            repeticoes = int(duracao / dur_video) + 1
            with open(concat_txt, "w") as f:
                for _ in range(repeticoes):
                    f.write(f"file '{entrada.resolve()}'\n")
            cmd = [
                FFMPEG_PATH, "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_txt),
                "-an",
                "-t", str(duracao),
                "-vf", "scale=1920:1080:force_original_aspect_ratio=1,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1:1",
                "-pix_fmt", "yuv420p",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                str(saida)
            ]
    from services.event_logger import log_event as _log
    _log("RENDER", f"Cena {scene_id}: executando FFmpeg para {entrada.name} (tamanho={entrada.stat().st_size//1024}KB, duracao={duracao:.1f}s)", level="info")
    _log("RENDER", f"Cena {scene_id}: saida={saida.name}", level="info")
    process = subprocess.Popen(
        cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace"
    )
    stderr_lines = []
    import re as _re2
    import time as _time
    inicio_proc = _time.time()
    timeout_proc = 300  # 5 minutos maximo por cena
    while True:
        if _time.time() - inicio_proc > timeout_proc:
            process.kill()
            raise RuntimeError(f"Timeout ({timeout_proc}s) ao processar cena {scene_id} - arquivo {entrada.name}")
        line = process.stderr.readline()
        if not line and process.poll() is not None:
            break
        if line:
            line = line.strip()
            stderr_lines.append(line)
            if "time=" in line:
                m = _re2.search(r"time=(\S+)", line)
                if m:
                    decorrido = int(_time.time() - inicio_proc)
                    _log("RENDER", f"Cena {scene_id}: processando... tempo={m.group(1)} | decorrido={decorrido}s", level="info")
    returncode = process.wait()
    decorrido_total = int(_time.time() - inicio_proc)
    if returncode != 0:
        _log("RENDER", f"Cena {scene_id}: ERRO no FFmpeg (codigo {returncode}) apos {decorrido_total}s", level="error")
        raise RuntimeError(f"Erro ao processar cena {scene_id}: {chr(10).join(stderr_lines[-5:])}")
    _log("RENDER", f"Cena {scene_id}: FFmpeg concluido em {decorrido_total}s — {saida.name}", level="info")
    return str(saida)


def construir_video(project_name: str) -> dict:
    from services.event_logger import log_event
    log_event("RENDER", f"construir_video chamado com project_name={project_name}", level="info")
    """
    Constrói o vídeo final combinando as mídias encontradas com o áudio original.
    1. Pre-processa cada midia (foto->video, video sem audio, cortado/loop)
    2. Prepara lista de arquivos processados para o video_encoder
    """
    project_dir = PROJETOS_DIR / project_name
    midias_file = project_dir / "midias_encontradas.json"
    cenas_file = project_dir / "cenas.json"

    if not midias_file.exists():
        return {"success": False, "error": "midias_encontradas.json nao encontrado"}
    if not cenas_file.exists():
        return {"success": False, "error": "cenas.json nao encontrado"}

    with open(midias_file, "r", encoding="utf-8") as f:
        midias = json.load(f)

    cache_dir = project_dir / "_processed"
    cache_dir.mkdir(parents=True, exist_ok=True)

    arquivos_video = []
    cenas_com_midia = 0
    cenas_sem_midia = 0

    for midia in midias:
        if midia.get("success") and midia.get("arquivo"):
            arquivo = midia["arquivo"]
            scene_id = midia.get("scene_id", 0)
            log_event("RENDER", f"Cena {scene_id}: arquivo={arquivo}, existe={Path(arquivo).exists()}", level="info")
            if Path(arquivo).exists():
                try:
                    from services.event_logger import log_event
                    log_event("RENDER", f"Cena {scene_id}: processando {Path(arquivo).name} ({Path(arquivo).suffix.upper()})", level="info")
                    duracao = _extrair_duracao_cena(project_name, scene_id)
                    log_event("RENDER", f"Cena {scene_id}: duracao={duracao:.1f}s — convertendo para MP4 1920x1080...", level="info")
                    arquivo_processado = _preprocessar_midia(arquivo, scene_id, duracao, cache_dir)
                    arquivos_video.append(arquivo_processado)
                    cenas_com_midia += 1
                    log_event("RENDER", f"Cena {scene_id}: OK — {cenas_com_midia}/{len(midias)} concluidas", level="info")
                except Exception as e:
                    from services.event_logger import log_event
                    log_event("RENDER", f"Cena {scene_id}: ERRO ao processar — {str(e)}", level="error")
                    cenas_sem_midia += 1
            else:
                cenas_sem_midia += 1
        else:
            cenas_sem_midia += 1

    audio_original = None
    # Tenta audio do projeto (nome do projeto + extensão)
    for nome in [f"{project_name}.mp3", f"{project_name}.mp4", f"{project_name}.wav",
                 "audio_original.mp3", "audio_original.mp4", "audio_original.wav"]:
        possivel = project_dir / nome
        if possivel.exists():
            audio_original = possivel
            break
    if not audio_original:
        for ext in [".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav"]:
            possivel = project_dir / f"input{ext}"
            if possivel.exists():
                audio_original = possivel
                break

    return {
        "success": True,
        "arquivos_video": arquivos_video,
        "arquivo_audio": str(audio_original) if audio_original and audio_original.exists() else None,
        "cenas_com_midia": cenas_com_midia,
        "cenas_sem_midia": cenas_sem_midia,
        "total_cenas": len(midias)
    }
