"""
video_builder.py — Montagem do vídeo final combinando cenas + B-roll
Pre-processa cada clipe: fotos viram video com Ken Burns, videos tem audio
removido e cortados para duracao exata da cena.
Audio original e usado diretamente no render — sem etapa de corte de silencio.
"""
import json, subprocess, os
from pathlib import Path
from config import PROJETOS_DIR, FFMPEG_PATH, FFPROBE_PATH, resolver_encoder, ENCODER_FALLBACK


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


def _inferir_media_type(midia: dict, arquivo: str) -> str:
    """
    Determina o tipo de midia de um clipe (photo ou video).
    Prioridade:
    1. Campo media_type no midias_encontradas.json (fonte de verdade do fetcher)
    2. Extensao do arquivo baixado (.jpg/.png/.webp = photo, resto = video)
    """
    mt = midia.get("media_type", "")
    if mt in ("photo", "video"):
        return mt
    ext = Path(arquivo).suffix.lower()
    return "photo" if ext in (".jpg", ".jpeg", ".png", ".webp") else "video"


def _gerar_comando_kenburns(foto_path: str, output_path: str, duracao: float,
                             indice_cena: int, width: int = 1920, height: int = 1080,
                             preset: str = None) -> list:
    """
    Gera comando FFmpeg com efeito Ken Burns cinematográfico para foto usando zoompan.
    Presets suportados:
      - 'zoom_in': Zoom suave aproximando do centro (1.0 -> 1.07)
      - 'zoom_out': Zoom suave recuando para o centro (1.07 -> 1.0)
      - 'zoom_in_slow': Zoom sutil e ultra lento (1.0 -> 1.04)
      - 'zoom_out_slow': Zoom out sutil e ultra lento (1.04 -> 1.0)
      - 'pan_right': Pan horizontal suave da esquerda para a direita (com zoom 1.07)
      - 'pan_left': Pan horizontal suave da direita para a esquerda (com zoom 1.07)
      - 'pan_up': Pan vertical suave de baixo para cima (com zoom 1.07)
      - 'pan_down': Pan vertical suave de cima para baixo (com zoom 1.07)
    Se preset for None, alterna automaticamente pelos modos ciclicamente com base no índice da cena.
    """
    fps = 25
    total_frames = max(1, int(duracao * fps))
    w_par = 2 * int(width / 2)
    h_par = 2 * int(height / 2)

    presets_ciclo = ["zoom_in", "pan_right", "zoom_out", "pan_left"]
    modo_efeito = preset if preset and preset != "estatico" else presets_ciclo[(indice_cena - 1) % len(presets_ciclo)]

    if modo_efeito in ("zoom_in_slow", "zoom_out_slow"):
        zoom_max = min(1.05, 1.0 + (duracao / 4.0) * 0.04)
        zoom_max = max(1.03, zoom_max)
    else:
        zoom_max = min(1.08, 1.0 + (duracao / 4.0) * 0.08)
        zoom_max = max(1.04, zoom_max)
    zoom_speed = round((zoom_max - 1.0) / (duracao * fps), 6)

    if modo_efeito in ("zoom_in", "zoom_in_slow"):
        expr_z = f"min(zoom+{zoom_speed},{zoom_max:.4f})"
        expr_x = "iw/2-(iw/zoom/2)"
        expr_y = "ih/2-(ih/zoom/2)"
    elif modo_efeito in ("zoom_out", "zoom_out_slow"):
        expr_z = f"if(eq(on,1),{zoom_max:.4f},max(zoom-{zoom_speed},1.0))"
        expr_x = "iw/2-(iw/zoom/2)"
        expr_y = "ih/2-(ih/zoom/2)"
    elif modo_efeito == "pan_right":
        expr_z = f"{zoom_max:.4f}"
        expr_x = f"(iw-iw/zoom)*(on/{total_frames})"
        expr_y = "ih/2-(ih/zoom/2)"
    elif modo_efeito == "pan_left":
        expr_z = f"{zoom_max:.4f}"
        expr_x = f"(iw-iw/zoom)*(1-on/{total_frames})"
        expr_y = "ih/2-(ih/zoom/2)"
    elif modo_efeito == "pan_up":
        expr_z = f"{zoom_max:.4f}"
        expr_x = "iw/2-(iw/zoom/2)"
        expr_y = f"(ih-ih/zoom)*(1-on/{total_frames})"
    elif modo_efeito == "pan_down":
        expr_z = f"{zoom_max:.4f}"
        expr_x = "iw/2-(iw/zoom/2)"
        expr_y = f"(ih-ih/zoom)*(on/{total_frames})"
    else:
        expr_z = f"min(zoom+{zoom_speed},{zoom_max:.4f})"
        expr_x = "iw/2-(iw/zoom/2)"
        expr_y = "ih/2-(ih/zoom/2)"

    vf = (
        f"zoompan=z='{expr_z}':"
        f"x='{expr_x}':y='{expr_y}':"
        f"d={total_frames}:s={w_par}x{h_par}:fps={fps},"
        f"setsar=1"
    )

    from services.event_logger import log_event
    log_event("RENDER", f"Cena {indice_cena}: Ken Burns [{modo_efeito}] ({duracao:.1f}s, {total_frames} frames)", level="info")

    return [
        FFMPEG_PATH, '-y',
        '-loop', '1',
        '-i', str(Path(foto_path).resolve()),
        '-vf', vf,
        '-t', str(duracao),
        '-c:v', resolver_encoder(),
        '-pix_fmt', 'yuv420p',
        '-profile:v', 'main',
        '-r', str(fps),
        str(output_path)
    ]


def converter_imagem_para_broll_mp4(
    foto_path: str,
    saida_mp4: str,
    duracao: float,
    indice_cena: int,
    preset: str = None
) -> bool:
    """Converte uma imagem estática (PNG/JPG) em vídeo MP4 B-Roll com Ken Burns."""
    from services.event_logger import log_event
    foto = Path(foto_path)
    if not foto.exists():
        log_event("BROLL_MOTION", f"Imagem não encontrada: {foto_path}", level="error")
        return False
    saida = Path(saida_mp4)
    saida.parent.mkdir(parents=True, exist_ok=True)

    cmd = _gerar_comando_kenburns(str(foto.resolve()), str(saida.resolve()), duracao, indice_cena, preset=preset)
    returncode, stderr_lines = _rodar_ffmpeg_preprocess(cmd, indice_cena)
    enc_ativo = resolver_encoder()
    if returncode != 0 and enc_ativo != ENCODER_FALLBACK:
        cmd_fb = _substituir_encoder(cmd, ENCODER_FALLBACK)
        returncode, stderr_lines = _rodar_ffmpeg_preprocess(cmd_fb, indice_cena)

    if returncode == 0 and saida.exists() and saida.stat().st_size > 1000:
        log_event("BROLL_MOTION", f"Cena {indice_cena:03d}: clipe B-roll gerado com sucesso ({saida.name})", level="info")
        return True
    else:
        log_event("BROLL_MOTION", f"Cena {indice_cena:03d}: erro ao gerar clipe B-roll", level="error")
        return False


def converter_todas_imagens_projeto_para_broll_mp4(projeto_id: str, callback_progresso=None) -> dict:
    """
    Varre todas as cenas do projeto no lira_scene_plan.json.
    Para cada cena que seja imagem (.png/.jpg), renderiza um clipe MP4 com movimento
    (Ken Burns alternado) e atualiza o arquivo_midia da cena para apontar para o .mp4.
    """
    from services.event_logger import log_event
    import services.scene_plan_service as scene_plan_svc
    plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    if not plan or not plan.get("cenas"):
        return {"success": False, "error": "Plano de cenas não encontrado"}

    cenas = plan.get("cenas", [])
    total = len(cenas)
    convertidas = 0
    erros = 0
    pdir = PROJETOS_DIR / projeto_id

    log_event("BROLL_MOTION", f"Iniciando conversão de {total} imagens para clipes MP4 B-Roll no projeto '{projeto_id}'...", level="info")

    for idx, c in enumerate(cenas, 1):
        cid = int(c.get("id", idx))
        dur = max(1.5, float(c.get("duracao", 4.0)))

        # Localiza arquivo atual da cena
        arq_atual = scene_plan_svc.resolver_arquivo_cena(projeto_id, cid, float(c.get("tempo_inicio", 0)))
        if not arq_atual or not arq_atual.exists():
            continue

        suf = arq_atual.suffix.lower()
        if suf in (".mp4", ".mov", ".webm"):
            # Já é vídeo, não precisa converter
            convertidas += 1
            if callback_progresso:
                callback_progresso(idx, total, f"Cena {cid:03d} já é vídeo")
            continue

        # REDESIGN F1: preset de movimento por cena (motion_preset, campo opcional).
        # Se ausente -> fallback sequencial clássico (rotação zoom_in/pan_right/
        # zoom_out/pan_left pelo índice). 'estatico' mantém a imagem como está.
        motion_preset = str(c.get("motion_preset") or "").strip().lower()
        if motion_preset == "estatico":
            log_event("BROLL_MOTION",
                      f"Cena {cid:03d}: motion_preset='estatico' — mantém imagem estática (sem MP4).",
                      level="info")
            if callback_progresso:
                callback_progresso(idx, total, f"Cena {cid:03d} estática — mantém foto")
            continue
        preset_mp4 = motion_preset if motion_preset in ("zoom_in", "zoom_out", "pan_right", "pan_left") else None

        # Nome de destino MP4: mesmo padrão na pasta cenas/
        nome_stem = arq_atual.stem
        saida_mp4 = pdir / "cenas" / f"{nome_stem}.mp4"

        if saida_mp4.exists() and saida_mp4.stat().st_size > 1000:
            # Cache hit (limitação conhecida: mudar motion_preset depois da 1ª
            # conversão mantém o MP4 anterior — regenerar apagando o arquivo).
            ok = True
        else:
            ok = converter_imagem_para_broll_mp4(str(arq_atual), str(saida_mp4), dur, cid, preset=preset_mp4)

        if ok:
            convertidas += 1
            # Atualiza scene_plan para referenciar o clipe MP4
            scene_plan_svc.atualizar_cena(projeto_id, cid, {
                "arquivo_midia": str(saida_mp4.resolve()),
                "download_path": str(saida_mp4.resolve()),
                "filename": saida_mp4.name,
                "tipo": scene_plan_svc.TIPO_VIDEO,
                "media_intent": "video",
                "video_status": scene_plan_svc.VIDEO_STATUS_READY,
                "status": scene_plan_svc.STATUS_BAIXADA,
            })
        else:
            erros += 1

        if callback_progresso:
            callback_progresso(idx, total, f"Convertendo Cena {cid:03d} ({idx}/{total})")

    # Sincroniza mídias encontradas e galeria
    scene_plan_svc.sincronizar_midias_encontradas(projeto_id)
    log_event("BROLL_MOTION", f"Conversão concluída: {convertidas}/{total} cenas agora possuem vídeo MP4.", level="info")

    return {
        "success": True,
        "total": total,
        "convertidas": convertidas,
        "erros": erros,
        "mensagem": f"{convertidas} de {total} cenas convertidas para vídeo B-Roll com movimento."
    }



def _substituir_encoder(comando: list, novo_encoder: str) -> list:
    """Troca '-c:v <enc>' por '-c:v <novo_encoder>' dentro de um comando ffmpeg."""
    novo = []
    i = 0
    while i < len(comando):
        if comando[i] == "-c:v" and i + 1 < len(comando):
            novo += ["-c:v", novo_encoder]
            i += 2
        else:
            novo.append(comando[i])
            i += 1
    return novo


def _rodar_ffmpeg_preprocess(cmd: list, scene_id: int) -> tuple:
    """
    Executa um comando ffmpeg de pré-processamento acompanhando o progresso.
    Retorna (returncode, stderr_lines). Não lança exceção em erro de ffmpeg.
    """
    import re as _re2
    import time as _time
    from services.event_logger import log_event as _log

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
            return 124, stderr_lines + [f"Timeout ({timeout_proc}s) ao processar cena {scene_id}"]
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
    return process.wait(), stderr_lines


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
                "-profile:v", "main",
                "-c:v", resolver_encoder(),
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
                "-profile:v", "main",
                "-c:v", resolver_encoder(),
                str(saida)
            ]

    enc_ativo = resolver_encoder()
    returncode, stderr_lines = _rodar_ffmpeg_preprocess(cmd, scene_id)
    if returncode != 0 and enc_ativo != ENCODER_FALLBACK:
        _log("RENDER",
             f"Cena {scene_id}: encoder {enc_ativo} falhou (código {returncode}) — "
             f"tentando fallback {ENCODER_FALLBACK}...", level="warn")
        cmd_fb = _substituir_encoder(cmd, ENCODER_FALLBACK)
        returncode, stderr_lines = _rodar_ffmpeg_preprocess(cmd_fb, scene_id)

    if returncode != 0:
        _log("RENDER", f"Cena {scene_id}: ERRO FFmpeg (código {returncode}) após {len(stderr_lines)} linhas de stderr", level="error")
        raise RuntimeError(f"Erro cena {scene_id}: {chr(10).join(stderr_lines[-5:])}")

    _log("RENDER", f"Cena {scene_id}: OK", level="info")
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

    # (media_search.py removido — fluxo stock substituído pelo Google Flow/Playwright)

    arquivos_video = []
    media_types = []
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
                    media_types.append(_inferir_media_type(midia, midia["arquivo"]))
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
                    media_types.append(_inferir_media_type(midia, arquivo))
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
        "media_types": media_types,
        "arquivo_audio": str(audio_original) if audio_original else None,
        "cenas_com_midia": cenas_com_midia,
        "cenas_sem_midia": cenas_sem_midia,
        "total_cenas": len(midias)
    }
