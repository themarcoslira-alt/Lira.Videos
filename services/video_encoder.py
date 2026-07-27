"""
video_encoder.py — Codificação de vídeo com h264_amf (AMD GPU)
Aceita lista de MP4s pré-processados do video_builder (sem audio proprio)
Concatena clipes e adiciona audio original como trilha
"""
import subprocess, json, re as _re_module, os
from pathlib import Path
from config import VIDEO_ENCODER, VIDEO_ENCODER_OPTIONS, OUTPUT_DIR, FFMPEG_PATH, FFPROBE_PATH


def sanitizar_nome_arquivo(nome: str) -> str:
    """
    Sanitiza um nome para uso seguro em caminhos de arquivo.
    Remove ou substitui caracteres problematicos no Windows e no FFmpeg.
    O nome original e preservado para exibicao na GUI.
    """
    nome = nome.replace("'", "").replace('"', "")
    nome = _re_module.sub(r'[<>:"/\\|?*]', '_', nome)
    nome = _re_module.sub(r'\s+', ' ', nome).strip()
    return nome[:100]


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
        pix_fmt = stream.get("pix_fmt", "")
        if pix_fmt not in ("yuv420p", "yuvj420p", "yuv420p10le", "nv12"):
            from services.event_logger import log_event
            log_event("RENDER", f"Validacao: pix_fmt={pix_fmt} nao reconhecido", level="warn")
        result2 = subprocess.run(
            [FFMPEG_PATH, "-v", "error", "-i", str(arquivo),
             "-frames:v", "1", "-f", "null", "-"],
            capture_output=True, text=True, timeout=15
        )
        return result2.returncode == 0
    except Exception:
        return False


def _verificar_audio_valido(audio_path: str) -> bool:
    """Verifica se o arquivo de audio e valido usando FFprobe."""
    if not audio_path or not Path(audio_path).exists():
        return False
    if Path(audio_path).stat().st_size == 0:
        return False
    cmd = [
        FFPROBE_PATH, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path
    ]
    resultado = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if resultado.returncode != 0:
        return False
    try:
        duracao = float(resultado.stdout.strip())
        return duracao > 0
    except (ValueError, TypeError):
        return False


def _selecionar_audio_para_render(audio_no_silence: str, audio_original: str,
                                   safe_name: str) -> str:
    """
    Seleciona o melhor audio disponivel para o render final.
    Prioridade: no_silence valido > original valido > erro.
    Garante que o caminho final nao tem apostrofo.
    """
    from services.event_logger import log_event
    caracteres_problematicos = ["'", '"', '&', '!', '#']

    def _sanitizar_caminho(src: str, sufixo: str) -> str:
        """Copia para OUTPUT_DIR se o caminho tiver caracteres problematicos."""
        if any(c in src for c in caracteres_problematicos):
            destino = str(OUTPUT_DIR / f"{safe_name}_{sufixo}.mp3")
            if not Path(destino).exists() or Path(destino).stat().st_size == 0:
                if Path(destino).exists():
                    Path(destino).unlink()
                import shutil
                shutil.copy2(src, destino)
                log_event("RENDER", f"Audio copiado: {Path(destino).name}", level="info")
            return destino
        return src

    def _remover_se_corrompido(caminho: str):
        """Remove arquivo de audio se estiver corrompido (0 bytes ou ffprobe falha)."""
        if caminho and Path(caminho).exists():
            if not _verificar_audio_valido(caminho):
                Path(caminho).unlink()
                log_event("RENDER", f"Audio corrompido removido: {Path(caminho).name}", level="warn")

    # Tentativa 1: no_silence
    if audio_no_silence and Path(audio_no_silence).exists():
        caminho_seguro = _sanitizar_caminho(audio_no_silence, "no_silence")
        # Loga diagnostico antes de remover
        if caminho_seguro and Path(caminho_seguro).exists():
            tamanho = Path(caminho_seguro).stat().st_size
            log_event("RENDER", f"Verificando audio no_silence: {Path(caminho_seguro).name} ({tamanho} bytes)", level="info")
        _remover_se_corrompido(caminho_seguro)
        # Re-copia se foi deletado
        if not Path(caminho_seguro).exists() and audio_no_silence != caminho_seguro:
            import shutil
            shutil.copy2(audio_no_silence, caminho_seguro)
        if _verificar_audio_valido(caminho_seguro):
            log_event("RENDER", f"Usando audio sem silencio: {Path(caminho_seguro).name}", level="info")
            return caminho_seguro
        else:
            log_event("RENDER", f"_no_silence.mp3 invalido — tentando audio original", level="warn")

    # Tentativa 2: original
    if audio_original and Path(audio_original).exists():
        caminho_seguro = _sanitizar_caminho(audio_original, "audio_render")
        if _verificar_audio_valido(caminho_seguro):
            log_event("RENDER", f"Usando audio original: {Path(caminho_seguro).name}", level="info")
            return caminho_seguro
        else:
            # Loga o caminho SEGURO (sanitizado) que falhou, nao o original
            # O original pode ter apostrofos que fazem ffprobe falhar mesmo se o audio e valido
            log_event("RENDER", f"Audio original tambem invalido: {caminho_seguro}", level="error")

    raise RuntimeError("Nenhum audio valido encontrado para o render final")


def renderizar_video(arquivos_entrada: list, arquivo_audio: str,
                     nome_saida: str) -> dict:
    """
    Renderiza video final.
    Entrada: lista de MP4s pre-processados (sem audio proprio)
    Audio: arquivo_audio original (mp3/mp4) mapeado como trilha unica
    """
    from services.event_logger import log_event
    # Sanitiza nome para uso em arquivos (remove apostrofos, etc.)
    safe_name = sanitizar_nome_arquivo(nome_saida)
    if safe_name != nome_saida:
        log_event("RENDER", f"Nome sanitizado: '{nome_saida}' -> '{safe_name}'", level="info")

    # Seleciona audio com fallback cascata e validacao FFprobe
    # Tenta _no_silence.mp3 primeiro, depois audio original
    project_dir = None
    audio_original_path = arquivo_audio
    audio_no_silence_path = None

    # Procura _no_silence.mp3 relativo ao projeto
    from config import PROJETOS_DIR
    for p in PROJETOS_DIR.iterdir():
        if p.is_dir():
            cand = p / f"{safe_name}_no_silence.mp3"
            if cand.exists():
                audio_no_silence_path = str(cand)
                break

    if not audio_no_silence_path:
        # Tenta inferir pelo nome
        cand = Path(arquivo_audio).parent / f"{safe_name}_no_silence.mp3"
        if cand.exists():
            audio_no_silence_path = str(cand)

    try:
        arquivo_audio = _selecionar_audio_para_render(
            audio_no_silence=audio_no_silence_path,
            audio_original=audio_original_path,
            safe_name=safe_name
        )
    except RuntimeError as e:
        return {"success": False, "error": str(e)}

    log_event("RENDER", f"Audio selecionado: {arquivo_audio} (valido={_verificar_audio_valido(arquivo_audio)})", level="info")
    log_event("RENDER", f"Iniciando render: {len(arquivos_entrada)} clips, saida={safe_name}", level="info")
    log_event("RENDER", f"Concatenando {len(arquivos_entrada)} clipes e adicionando audio...", level="info")

    # Calcula duracao total para percentual
    from services.pipeline_service import calcular_duracao_total
    duracao_total = calcular_duracao_total(arquivos_entrada)
    log_event("RENDER", f"Duracao total estimada: {duracao_total:.1f}s", level="info")

    saida_tmp = OUTPUT_DIR / f"{safe_name}.tmp.mp4"
    saida_final = OUTPUT_DIR / f"{safe_name}.mp4"

    if saida_tmp.exists():
        saida_tmp.unlink()

    concat_file = OUTPUT_DIR / f"{safe_name}_concat.txt"
    try:
        with open(concat_file, "w") as f:
            for arquivo in arquivos_entrada:
                caminho_escape = str(Path(arquivo).resolve()).replace("\\", "/").replace("'", "'\\''")
                f.write(f"file '{caminho_escape}'\n")
        log_event("RENDER", f"Concat criado: {concat_file} ({len(arquivos_entrada)} clips)", level="info")

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

        process = subprocess.Popen(
            comando, stderr=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace"
        )
        stderr_lines = []
        import re as _re
        _ultimo_pct = -1
        _ultimo_log_time = 0
        while True:
            line = process.stderr.readline()
            if not line and process.poll() is not None:
                break
            if line:
                line = line.strip()
                stderr_lines.append(line)
                if "frame=" in line or "time=" in line or "fps=" in line:
                    m = _re.search(r"time=(\S+)", line)
                    fps_m = _re.search(r"fps=\s*(\S+)", line)
                    if m:
                        tempo_str = m.group(1)
                        fps = fps_m.group(1) if fps_m else "?"
                        try:
                            parts = tempo_str.split(":")
                            if len(parts) == 3:
                                h, m, s = parts
                                tempo_seg = float(h) * 3600 + float(m) * 60 + float(s)
                            elif len(parts) == 2:
                                m, s = parts
                                tempo_seg = float(m) * 60 + float(s)
                            else:
                                tempo_seg = 0
                            if duracao_total > 0:
                                pct = min(int((tempo_seg / duracao_total) * 100), 99)
                                import time as _time
                                now = _time.time()
                                if pct != _ultimo_pct and (pct % 5 == 0 or now - _ultimo_log_time > 10):
                                    _ultimo_pct = pct
                                    _ultimo_log_time = now
                                    log_event("RENDER", f"Renderizando... {pct}% (tempo={tempo_str}, fps={fps})", level="info")
                        except (ValueError, IndexError):
                            pass
                elif "Error" in line or "error" in line.lower():
                    log_event("RENDER", f"FFmpeg: {line[:120]}", level="error")

        returncode = process.wait()
        if returncode != 0:
            if saida_tmp.exists():
                saida_tmp.unlink()
            return {
                "success": False,
                "error": f"ffmpeg retornou codigo {returncode}",
                "stderr": "\n".join(stderr_lines[-20:])
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