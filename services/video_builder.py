"""
video_builder.py — Montagem do vídeo final combinando cenas + B-roll
Pre-processa cada clipe: fotos viram video com Ken Burns, videos tem audio
removido e cortados para duracao exata da cena.
Audio original e usado diretamente no render — sem etapa de corte de silencio.
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
    for c in cenas:
        if c["id"] == cena_id:
            start_time = c.get("start_time")
            end_time = c.get("end_time")
            if start_time is not None and end_time is not None:
                return max(1.5, end_time - start_time)
    return 4.0


def _gerar_comando_kenburns(foto_path: str, output_path: str, duracao: float,
                             indice_cena: int, width: int = 1920, height: int = 1080) -> list:
    """Gera comando FFmpeg com efeito Ken Burns para foto usando zoompan."""
    fps = 25
    total_frames = max(1, int(duracao * fps))
    zoom_in = (indice_cena % 2 == 0)
    w_par = 2 * int(width / 2)
    h_par = 2 * int(height / 2)

    if zoom_in:
        z_inicio, z_fim = "1.3", "1.0"
    else:
        z_inicio, z_fim = "1.0", "1.3"

    vf = (
        f"zoompan=z='{z_inicio}+({z_fim}-{z_inicio})*on/{total_frames}':"
        f"d={total_frames}:"
        f"s={w_par}x{h_par},"
        f"setsar=1"
    )

    from services.event_logger import log_event
    efeito = "zoom_in" if zoom_in else "zoom_out"
    log_event("RENDER", f"Cena {indice_cena}: Ken Burns {efeito} ({duracao:.1f}s, {total_frames} frames)", level="info")

    return [
        FFMPEG_PATH, '-y',
        '-loop', '1',
        '-i', str(Path(foto_path).resolve()),
        '-vf', vf,
        '-t', str(duracao),
        '-c:v', 'h264_amf',
        '-pix_fmt', 'yuv420p',
        '-r', str(fps),
        str(output_path)
    ]


def _preprocessar_midia(arquivo_entrada: str, scene_id: int,
                         duracao: float, cache_dir: Path,
                         project_name: str = "") -> str:
    """
    Pre-processa uma midia para video padrao.
    - Foto: converte com Ken Burns (zoom lento alternado)
    - Video: remove audio, corta/loop para duracao exata
    """
    from services.event_logger import log_event as _log
    import re as _re2
    import time as _time

    entrada = Path(arquivo_entrada)
    saida = cache_dir / f"scene_{scene_id}_processed.mp4"
    if saida.exists():
        _log("RENDER", f"Cena {scene_id}: cache hit — reutilizando {saida.name}", level="info")
        return str(saida)

    ext = entrada.suffix.lower()
    _log("RENDER", f"Cena {scene_id}: processando {entrada.name} ({ext.upper()}, {entrada.stat().st_size//1024}KB, {duracao:.1f}s)", level="info")

    if ext in (".jpg", ".jpeg", ".png", ".webp"):
        cmd = _gerar_comando_kenburns(str(entrada.resolve()), str(saida), duracao, scene_id)
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
                "-c:v", "h264_amf",
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
                "-c:v", "h264_amf",
                str(saida)
            ]

    process = subprocess.Popen(
        cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace"
    )
    stderr_lines = []
    inicio_proc = _time.time()
    timeout_proc = 300

    while True:
        if _time.time() - inicio_proc > timeout_proc:
            process.kill()
            raise RuntimeError(f"Timeout ({timeout_proc}s) ao processar cena {scene_id}")
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
                    _log("RENDER", f"Cena {scene_id}: time={m.group(1)} | decorrido={decorrido}s", level="info")

    returncode = process.wait()
    decorrido_total = int(_time.time() - inicio_proc)

    if returncode != 0:
        _log("RENDER", f"Cena {scene_id}: ERRO FFmpeg (codigo {returncode}) apos {decorrido_total}s", level="error")
        raise RuntimeError(f"Erro cena {scene_id}: {chr(10).join(stderr_lines[-5:])}")

    _log("RENDER", f"Cena {scene_id}: OK em {decorrido_total}s", level="info")
    return str(saida)


def _encontrar_audio_original(project_name: str) -> Path | None:
    """
    Localiza o audio original do projeto.
    Ordem de busca:
    1. meta.json -> arquivo_audio (caminho que o usuario selecionou na GUI)
    2. Qualquer arquivo de audio/video na pasta do projeto
    Nunca usa _no_silence.mp3 — o audio original e sempre a fonte de verdade.
    """
    from services.event_logger import log_event
    project_dir = PROJETOS_DIR / project_name
    meta_file = project_dir / "meta.json"

    # 1. Tenta ler o caminho salvo no meta.json (mais confiavel)
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            audio_path = meta.get("arquivo_audio", "")
            if audio_path and Path(audio_path).exists():
                log_event("RENDER", f"Audio original encontrado via meta.json: {Path(audio_path).name}", level="info")
                return Path(audio_path)
        except Exception:
            pass

    # 2. Fallback: qualquer arquivo de audio/video na pasta do projeto
    for ext in [".mp3", ".mp4", ".wav", ".aac", ".m4a", ".ogg", ".mov", ".mkv", ".avi"]:
        for candidato in sorted(project_dir.glob(f"*{ext}")):
            # Ignora arquivos gerados internamente
            if "_no_silence" in candidato.name or "_processed" in candidato.name:
                continue
            log_event("RENDER", f"Audio original encontrado por glob: {candidato.name}", level="info")
            return candidato

    return None


def construir_video(project_name: str) -> dict:
    """
    Constroi o video final combinando as midias encontradas com o audio original.
    Fluxo:
      1. Le midias_encontradas.json
      2. Pre-processa cada midia (foto->Ken Burns, video->sem audio cortado)
      3. Localiza audio original (via meta.json, sem _no_silence)
      4. Retorna lista de clips + caminho do audio para o video_encoder
    """
    from services.event_logger import log_event
    log_event("RENDER", f"construir_video: projeto={project_name}", level="info")

    if not FFMPEG_PATH or not FFPROBE_PATH:
        return {
            "success": False,
            "error": (
                "ffmpeg/ffprobe nao encontrado. "
                "Instale o ffmpeg e configure FFMPEG_PATH/FFPROBE_PATH em config.py"
            )
        }

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

    # Referência para pipeline (check de pause)
    _pipeline_ref = None
    try:
        from services.media_search import _pipeline_ref as _ms_pipeline
        _pipeline_ref = _ms_pipeline
    except Exception:
        pass

    # Carrega resume_index do pipeline se existir
    resume_idx = 0
    if _pipeline_ref:
        try:
            resume_idx = _pipeline_ref.get_resume_index(4)
            if resume_idx > 0:
                log_event("RENDER", f"Retomando render a partir da cena {resume_idx + 1}", level="info")
        except Exception:
            pass

    arquivos_video = []
    cenas_com_midia = 0
    cenas_sem_midia = 0
    ultimo_arquivo = None

    for midia_idx, midia in enumerate(midias):
        # Pula cenas já processadas se retomando de pause
        if resume_idx > 0 and midia_idx < resume_idx:
            if midia.get("success") and midia.get("arquivo") and Path(midia["arquivo"]).exists():
                # Verifica se o arquivo processado existe no cache
                scene_id = midia.get("scene_id", 0)
                cache_dir = project_dir / "_processed"
                arquivo_processado = cache_dir / f"scene_{scene_id}.mp4"
                if arquivo_processado.exists():
                    arquivos_video.append(str(arquivo_processado))
                    cenas_com_midia += 1
            continue

        # Checa pause antes de cada midia
        if _pipeline_ref:
            try:
                if _pipeline_ref._check_pause_before_item(4, midia_idx, len(midias)):
                    log_event("RENDER", f"Pipeline pausado/cancelado na cena {midia_idx + 1}/{len(midias)}", level="info")
                    break
            except Exception:
                pass
        if midia.get("success") and midia.get("arquivo"):
            arquivo = midia["arquivo"]
            scene_id = midia.get("scene_id", 0)

            if ultimo_arquivo and arquivo == ultimo_arquivo:
                log_event("RENDER", f"Cena {scene_id}: AVISO — mesma midia da cena anterior", level="warn")
            ultimo_arquivo = arquivo

            log_event("RENDER", f"Cena {scene_id}: arquivo={Path(arquivo).name}, existe={Path(arquivo).exists()}", level="info")

            if Path(arquivo).exists():
                try:
                    duracao = _extrair_duracao_cena(project_name, scene_id)
                    log_event("RENDER", f"Cena {scene_id}: duracao={duracao:.1f}s", level="info")
                    arquivo_processado = _preprocessar_midia(arquivo, scene_id, duracao, cache_dir, project_name)
                    arquivos_video.append(arquivo_processado)
                    cenas_com_midia += 1
                    log_event("RENDER", f"Cena {scene_id}: OK — {cenas_com_midia}/{len(midias)}", level="info")
                except Exception as e:
                    log_event("RENDER", f"Cena {scene_id}: ERRO — {str(e)}", level="error")
                    cenas_sem_midia += 1
            else:
                log_event("RENDER", f"Cena {scene_id}: arquivo nao encontrado em disco — pulando", level="warn")
                cenas_sem_midia += 1
        else:
            cenas_sem_midia += 1

    if cenas_com_midia == 0:
        return {
            "success": False,
            "error": f"Nenhuma midia processada (0/{len(midias)}). Verifique se ffmpeg esta instalado."
        }

    # Audio: SEMPRE o original, nunca _no_silence
    audio_original = _encontrar_audio_original(project_name)

    if audio_original:
        duracao_total = sum(
            _extrair_duracao_cena(project_name, m.get("scene_id", 0))
            for m in midias if m.get("success")
        )
        log_event("RENDER", f"construir_video: {cenas_com_midia} cenas, audio={audio_original.name}", level="info")
        log_event("RENDER", f"Duracao total clips: {duracao_total:.1f}s | Audio: {audio_original.name}", level="info")
    else:
        log_event("RENDER", "AVISO: audio original nao encontrado", level="warn")

    return {
        "success": True,
        "arquivos_video": arquivos_video,
        "arquivo_audio": str(audio_original) if audio_original else None,
        "cenas_com_midia": cenas_com_midia,
        "cenas_sem_midia": cenas_sem_midia,
        "total_cenas": len(midias)
    }
