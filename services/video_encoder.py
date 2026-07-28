"""
video_encoder.py — Codificacao de video com h264_amf (AMD GPU)
Aceita lista de MP4s pre-processados do video_builder (sem audio proprio).
Concatena clipes e adiciona o audio ORIGINAL como trilha unica.
Sem logica de no_silence — o audio original e sempre a fonte de verdade.
"""
import subprocess, json, re as _re_module, os
from pathlib import Path
from config import VIDEO_ENCODER, VIDEO_ENCODER_OPTIONS, OUTPUT_DIR, FFMPEG_PATH, FFPROBE_PATH


def sanitizar_nome_arquivo(nome: str) -> str:
    """Remove caracteres problematicos para uso em caminhos de arquivo."""
    nome = nome.replace("'", "").replace('"', "")
    nome = _re_module.sub(r'[<>:"/\\|?*]', '_', nome)
    nome = _re_module.sub(r'\s+', ' ', nome).strip()
    return nome[:100]


def _validar_arquivo(arquivo: Path) -> bool:
    try:
        result = subprocess.run(
            [FFPROBE_PATH, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=pix_fmt",
             "-of", "json", str(arquivo)],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return False
        data = json.loads(result.stdout)
        if not data.get("streams"):
            return False
        result2 = subprocess.run(
            [FFMPEG_PATH, "-v", "error", "-i", str(arquivo),
             "-frames:v", "1", "-f", "null", "-"],
            capture_output=True, text=True, timeout=15
        )
        return result2.returncode == 0
    except Exception:
        return False


def _verificar_audio_valido(audio_path: str) -> bool:
    """Verifica se o arquivo de audio e valido via FFprobe."""
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
        return float(resultado.stdout.strip()) > 0
    except (ValueError, TypeError):
        return False


def _preparar_audio(arquivo_audio: str, safe_name: str) -> str:
    """
    Garante que o caminho do audio nao tem caracteres problematicos para o
    ffmpeg (apostrofos, etc). Se tiver, copia para OUTPUT_DIR com nome seguro.
    Nunca usa _no_silence — apenas o audio original.
    """
    from services.event_logger import log_event
    caracteres_problematicos = ["'", '"', '&', '!', '#']

    if any(c in arquivo_audio for c in caracteres_problematicos):
        destino = str(OUTPUT_DIR / f"{safe_name}_audio_render.mp3")
        if not Path(destino).exists() or Path(destino).stat().st_size == 0:
            import shutil
            shutil.copy2(arquivo_audio, destino)
            log_event("RENDER", f"Audio copiado (nome seguro): {Path(destino).name}", level="info")
        arquivo_audio = destino

    if not _verificar_audio_valido(arquivo_audio):
        raise RuntimeError(
            f"Audio invalido ou corrompido: {arquivo_audio}\n"
            f"Verifique se o arquivo de audio existe e nao esta corrompido."
        )

    return arquivo_audio


def renderizar_video(arquivos_entrada: list, arquivo_audio: str,
                     nome_saida: str) -> dict:
    """
    Renderiza o video final.
    Entrada: lista de MP4s pre-processados (sem audio proprio)
    Audio: arquivo original do projeto (mp3/mp4/wav) — direto, sem no_silence
    """
    from services.event_logger import log_event

    safe_name = sanitizar_nome_arquivo(nome_saida)
    if safe_name != nome_saida:
        log_event("RENDER", f"Nome sanitizado: '{nome_saida}' -> '{safe_name}'", level="info")

    # Prepara audio (sanitiza caminho se necessario, valida)
    try:
        arquivo_audio = _preparar_audio(arquivo_audio, safe_name)
    except RuntimeError as e:
        return {"success": False, "error": str(e)}

    log_event("RENDER", f"Audio para render: {Path(arquivo_audio).name} (valido=True)", level="info")
    log_event("RENDER", f"Iniciando render: {len(arquivos_entrada)} clips -> {safe_name}.mp4", level="info")

    saida_tmp = OUTPUT_DIR / f"{safe_name}.tmp.mp4"
    saida_final = OUTPUT_DIR / f"{safe_name}.mp4"

    if saida_tmp.exists():
        saida_tmp.unlink()

    concat_file = OUTPUT_DIR / f"{safe_name}_concat.txt"
    try:
        with open(concat_file, "w", encoding="utf-8") as f:
            for arquivo in arquivos_entrada:
                # Converte para barras pra frente — ffmpeg aceita no Windows
                # e evita problemas de escape com barras invertidas dentro
                # de aspas simples no formato concat.
                caminho = str(Path(arquivo).resolve()).replace("\\", "/")
                f.write(f"file '{caminho}'\n")

        log_event("RENDER", f"Concat criado: {len(arquivos_entrada)} clips", level="info")

        # Log das primeiras 3 linhas do concat para diagnostico
        try:
            linhas_concat = concat_file.read_text(encoding="utf-8").splitlines()
            for i, linha in enumerate(linhas_concat[:3]):
                log_event("RENDER", f"Concat linha {i+1}: {linha}", level="info")
            if len(linhas_concat) > 3:
                log_event("RENDER", f"Concat ... ({len(linhas_concat)} linhas total)", level="info")
        except Exception:
            pass

        # Concat final usa libx264 (software confiavel) — clips individuais
        # continuam com h264_amf (definido em video_builder.py)
        comando = [
            FFMPEG_PATH, "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-i", arquivo_audio,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-profile:v", "high",
            "-movflags", "+faststart",
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
        import time as _time
        _ultimo_pct = -1
        _ultimo_log_time = 0

        # Calcula duracao total dos clips para percentual de progresso
        duracao_total = 0.0
        try:
            from services.pipeline_service import calcular_duracao_total
            duracao_total = calcular_duracao_total(arquivos_entrada)
            log_event("RENDER", f"Duracao total estimada: {duracao_total:.1f}s", level="info")
        except Exception:
            pass

        while True:
            line = process.stderr.readline()
            if not line and process.poll() is not None:
                break
            if line:
                line = line.strip()
                stderr_lines.append(line)
                if "time=" in line:
                    m = _re.search(r"time=(\S+)", line)
                    fps_m = _re.search(r"fps=\s*(\S+)", line)
                    if m:
                        tempo_str = m.group(1)
                        fps = fps_m.group(1) if fps_m else "?"
                        try:
                            parts = tempo_str.split(":")
                            if len(parts) == 3:
                                h, mi, s = parts
                                tempo_seg = float(h)*3600 + float(mi)*60 + float(s)
                            else:
                                tempo_seg = 0
                            now = _time.time()
                            if duracao_total > 0:
                                pct = min(int((tempo_seg / duracao_total) * 100), 99)
                                if pct != _ultimo_pct and (pct % 5 == 0 or now - _ultimo_log_time > 10):
                                    _ultimo_pct = pct
                                    _ultimo_log_time = now
                                    log_event("RENDER", f"Renderizando... {pct}% (time={tempo_str}, fps={fps})", level="info")
                        except (ValueError, IndexError):
                            pass
                elif "error" in line.lower():
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

        tamanho = saida_final.stat().st_size if saida_final.exists() else 0
        log_event("RENDER", f"Video final gerado: {saida_final.name} ({tamanho//1024//1024}MB)", level="info")

        return {
            "success": True,
            "arquivo": str(saida_final),
            "tamanho": tamanho
        }

    except Exception as e:
        if saida_tmp.exists():
            saida_tmp.unlink()
        return {"success": False, "error": str(e)}
    finally:
        if concat_file.exists():
            concat_file.unlink()
