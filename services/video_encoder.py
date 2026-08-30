"""
video_encoder.py — Codificacao de video com h264_amf (AMD GPU)
Aceita lista de MP4s pre-processados do video_builder (sem audio proprio).
Concatena clipes e adiciona o audio ORIGINAL como trilha unica.
Sem logica de no_silence — o audio original e sempre a fonte de verdade.
"""
import subprocess, json, re as _re_module, os
from pathlib import Path
from config import VIDEO_ENCODER, VIDEO_ENCODER_OPTIONS, OUTPUT_DIR, FFMPEG_PATH, FFPROBE_PATH, resolver_encoder, ENCODER_FALLBACK


def _opcoes_encoder(encoder: str) -> list:
    """Opções ffmpeg de vídeo apropriadas para o encoder escolhido."""
    if encoder == "h264_amf":
        return [
            "-c:v", "h264_amf",
            "-pix_fmt", "yuv420p",
            "-profile:v", "main",
            "-quality", "balanced",
            "-movflags", "+faststart",
        ]
    return [
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
        "-movflags", "+faststart",
    ]


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


def _obter_duracao_clipe(arquivo: str) -> float:
    """Obtem duracao de um clipe via ffprobe."""
    try:
        result = subprocess.run(
            [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(arquivo)],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return max(0.1, float(result.stdout.strip()))
    except Exception:
        pass
    return 4.0


def _montar_filtro_xfade(arquivos: list, media_types: list) -> tuple:
    """
    Monta filter_complex para concatenar clipes com transicoes.
    Regras:
      - Foto -> Foto: corte seco (concat, sem transicao)
      - Foto -> Video / Video -> Foto / Video -> Video: crossfade 0.3s (xfade)
    Retorna (filter_complex_str, label_final, perda_total_segundos).
    """
    from services.event_logger import log_event

    n = len(arquivos)
    tipos = media_types if media_types and len(media_types) == n else ["video"] * n

    filtros = []
    for i in range(n):
        filtros.append(
            f"[{i}:v]setpts=PTS-STARTPTS,fps=25,format=yuv420p[v{i}]"
        )

    duracoes = [_obter_duracao_clipe(a) for a in arquivos]
    log_event("RENDER", f"Duracoes clipes: {[round(d,2) for d in duracoes]}", level="info")

    label_atual = "[v0]"
    dur_total = duracoes[0]
    perda = 0.0

    for i in range(1, n):
        prev_t = tipos[i - 1]
        curr_t = tipos[i]
        if prev_t == "photo" and curr_t == "photo":
            # Corte seco — sem transicao
            out = f"[c{i}]"
            filtros.append(f"{label_atual}[v{i}]concat=n=2:v=1:a=0{out}")
            dur_total += duracoes[i]
            log_event("RENDER", f"Transicao cena {i}: foto->foto corte seco", level="info")
        else:
            # Crossfade de 0.3s (margem de 0.05s p/ garantir offset+duration <= duracao real)
            out = f"[x{i}]"
            offset = max(0.0, dur_total - 0.35)
            filtros.append(
                f"{label_atual}[v{i}]xfade=transition=fade:duration=0.3:offset={offset:.3f}{out}"
            )
            dur_total = dur_total + duracoes[i] - 0.3
            perda += 0.3
            log_event("RENDER", f"Transicao cena {i}: {prev_t}->{curr_t} crossfade 0.3s (offset={offset:.3f})", level="info")

        label_atual = out

    filter_complex = ";".join(filtros)

    # Compensa a perda de tempo dos crossfades esticando o ultimo clipe
    # (mantem o video alinhado com o audio original como fonte de verdade)
    if perda > 0.05:
        filter_complex += f";{label_atual}tpad=stop_mode=clone:stop_duration={perda:.3f}[vfinal]"
        label_atual = "[vfinal]"
        log_event("RENDER", f"Compensacao de {perda:.2f}s adicionada ao ultimo clipe (tpad)", level="info")

    log_event("RENDER", f"filter_complex: {filter_complex[:200]}...", level="info")
    return filter_complex, label_atual, perda


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


def _rodar_render_final(comando: list, saida_tmp: Path, saida_final: Path,
                        arquivos_entrada: list) -> dict:
    """Executa o comando final de render e retorna o dict de resultado."""
    from services.event_logger import log_event
    import re as _re
    import time as _time

    process = subprocess.Popen(
        comando, stderr=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace"
    )

    stderr_lines = []
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
                            tempo_seg = float(h) * 3600 + float(mi) * 60 + float(s)
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


def renderizar_video(arquivos_entrada: list, arquivo_audio: str,
                     nome_saida: str, media_types: list = None) -> dict:
    """
    Renderiza o video final.
    Entrada: lista de MP4s pre-processados (sem audio proprio)
    Audio: arquivo original do projeto (mp3/mp4/wav) — direto, sem no_silence
    media_types: lista de "photo"/"video" por clipe (para transicoes xfade).
                 Se None/vazio, usa concat classico (retrocompatibilidade).
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

    encoder_ativo = resolver_encoder()
    log_event("RENDER", f"Encoder efetivo para o render final: {encoder_ativo}", level="info")

    concat_file = OUTPUT_DIR / f"{safe_name}_concat.txt"
    try:
        usa_xfade = (
            media_types
            and len(media_types) == len(arquivos_entrada)
            and len(arquivos_entrada) > 1
        )

        if usa_xfade:
            # Transicoes xfade via filter_complex — cada clipe e um input separado
            filtro_xfade, label_final, perda = _montar_filtro_xfade(arquivos_entrada, media_types)
            comando = [
                FFMPEG_PATH, "-y"
            ]
            for arquivo in arquivos_entrada:
                comando += ["-i", str(Path(arquivo).resolve())]
            comando += [
                "-i", arquivo_audio,
                "-filter_complex", filtro_xfade,
                "-map", label_final,
                "-map", f"{len(arquivos_entrada)}:a:0",
                *_opcoes_encoder(encoder_ativo),
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                str(saida_tmp)
            ]
            log_event("RENDER", f"Render com xfade: {len(arquivos_entrada)} inputs, perda={perda:.2f}s", level="info")
        else:
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
                *_opcoes_encoder(encoder_ativo),
                "-c:a", "aac", "-b:a", "192k",
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-shortest",
                str(saida_tmp)
            ]

        resultado = _rodar_render_final(comando, saida_tmp, saida_final, arquivos_entrada)

        # Fallback de encoder (ITEM 7): se o render falhou com o encoder primário
        # e há um fallback diferente disponível, tenta uma vez com o fallback.
        if not resultado.get("success") and encoder_ativo != ENCODER_FALLBACK:
            log_event("RENDER",
                      f"Render falhou com {encoder_ativo}; tentando fallback {ENCODER_FALLBACK}...",
                      level="warn")
            encoder_ativo = ENCODER_FALLBACK
            if usa_xfade:
                comando = [FFMPEG_PATH, "-y"]
                for arquivo in arquivos_entrada:
                    comando += ["-i", str(Path(arquivo).resolve())]
                comando += [
                    "-i", arquivo_audio,
                    "-filter_complex", filtro_xfade,
                    "-map", label_final,
                    "-map", f"{len(arquivos_entrada)}:a:0",
                    *_opcoes_encoder(encoder_ativo),
                    "-c:a", "aac", "-b:a", "192k",
                    "-shortest",
                    str(saida_tmp)
                ]
            else:
                comando = [
                    FFMPEG_PATH, "-y",
                    "-f", "concat", "-safe", "0",
                    "-i", str(concat_file),
                    "-i", arquivo_audio,
                    *_opcoes_encoder(encoder_ativo),
                    "-c:a", "aac", "-b:a", "192k",
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-shortest",
                    str(saida_tmp)
                ]
            resultado = _rodar_render_final(comando, saida_tmp, saida_final, arquivos_entrada)

        return resultado

    except Exception as e:
        if saida_tmp.exists():
            saida_tmp.unlink()
        return {"success": False, "error": str(e)}
    finally:
        if concat_file.exists():
            concat_file.unlink()


# ===========================================================================
# ANTIGRAVITY — Blindagem de codec de vídeo (compatibilidade CapCut)
# ---------------------------------------------------------------------------
# O render final local já usa H.264 (h264_amf/libx264). O risco real são os
# CLIPS de vídeo de ORIGEM (ex.: baixados do Pexels) que podem chegar em
# H.265/HEVC/AV1 — se referenciados direto no draft do CapCut, versões antigas
# (ex.: CapCut Samsung N9.10) não decodificam. Estas funções garantem H.264/MP4.
# ===========================================================================


def detectar_codec_video(caminho) -> str:
    """Retorna o codec de vídeo (codec_name) via ffprobe, ou '' em erro."""
    from services.event_logger import log_event
    try:
        r = subprocess.run(
            [FFPROBE_PATH, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name",
             "-of", "default=noprint_wrappers=1:nokey=1", str(caminho)],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            return (r.stdout or "").strip().lower()
    except Exception as e:
        log_event("RENDER", f"ffprobe codec falhou p/ {Path(caminho).name}: {e}", level="warn")
    return ""


def detectar_container_video(caminho) -> str:
    """Retorna o container (format_name) via ffprobe, ou '' em erro."""
    try:
        r = subprocess.run(
            [FFPROBE_PATH, "-v", "error",
             "-show_entries", "format=format_name",
             "-of", "default=noprint_wrappers=1:nokey=1", str(caminho)],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            return (r.stdout or "").strip().lower()
    except Exception:
        pass
    return ""


def converter_para_h264(caminho_origem, destino) -> dict:
    """Converte um vídeo para H.264/MP4 compatível (yuv420p, profile main).

    Remove o áudio do clipe ('-an') — no CapCut a trilha de áudio é a narração
    do projeto (os clipes de vídeo já entram mutados com volume=0).
    Usa libx264 (software, disponível em qualquer máquina).
    Retorna {"success": bool, "arquivo": str, "stderr": str}.
    """
    from services.event_logger import log_event
    try:
        cmd = [
            FFMPEG_PATH, "-y", "-v", "error",
            "-i", str(Path(caminho_origem).resolve()),
            "-an",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-profile:v", "main",
            "-movflags", "+faststart",
            str(Path(destino).resolve()),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode == 0 and Path(destino).exists() and Path(destino).stat().st_size > 0:
            log_event("RENDER", f"Vídeo convertido para H.264: {Path(destino).name}", level="info")
            return {"success": True, "arquivo": str(destino), "stderr": ""}
        return {"success": False, "arquivo": "", "stderr": (r.stderr or "")[-500:]}
    except Exception as e:
        return {"success": False, "arquivo": "", "stderr": str(e)}


def garantir_video_h264_compat(caminho, destino_dir=None) -> str:
    """Garante que um vídeo é H.264/MP4 (CapCut old friendly).

    - Se codec == h264 E container MP4 -> retorna o próprio caminho (sem custo);
    - Se não for possível detectar -> retorna o próprio caminho (não arrisca);
    - Caso contrário -> converte para H.264/MP4 em `destino_dir` e retorna o novo.
    """
    from services.event_logger import log_event
    caminho = str(caminho)
    if not Path(caminho).exists():
        return caminho
    codec = detectar_codec_video(caminho)
    container = detectar_container_video(caminho)
    if codec == "h264" and "mp4" in container:
        return caminho
    if codec == "":
        return caminho  # não detectou — mantém original
    base_dir = destino_dir or Path(caminho).parent
    Path(base_dir).mkdir(parents=True, exist_ok=True)
    destino = Path(base_dir) / f"{Path(caminho).stem}_h264.mp4"
    log_event("RENDER",
              f"Codec '{codec}' / container '{container}' — convertendo para H.264 "
              f"(compat CapCut): {Path(caminho).name}",
              level="info")
    r = converter_para_h264(caminho, destino)
    if r["success"]:
        return destino
    log_event("RENDER", f"Conversão H.264 falhou — mantendo original: {r.get('stderr', '')}", level="warn")
    return caminho
