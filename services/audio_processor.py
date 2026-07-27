"""
audio_processor.py — Processamento de áudio: corte de silêncio e sincronização.
Usa timestamps dos segmentos Whisper para remover gaps > 0.5s entre falas.
Gera um novo arquivo de áudio sem silêncio e um mapeamento de timestamps.
"""
import subprocess
from pathlib import Path
from config import FFMPEG_PATH


def cortar_silencio(segmentos: list, audio_path: str, output_path: str,
                    silence_threshold: float = 0.5) -> tuple:
    """
    Remove silencios do audio original usando timestamps do Whisper.

    Returns:
        (caminho_audio_processado, mapeamento_timestamps)
        mapeamento = lista de dicts:
        {'orig_start': float, 'orig_end': float,
         'proc_start': float, 'proc_end': float}
    """
    from services.event_logger import log_event

    if not segmentos or not audio_path or not Path(audio_path).exists():
        log_event("SILENCIO", "Segmentos ou audio ausentes — pulando corte de silencio", level="warn")
        return audio_path, []

    # 1. Coletar intervalos de fala com margem de 50ms
    intervalos = []
    for seg in segmentos:
        inicio = max(0.0, float(seg.get('start', 0)) - 0.05)
        fim = float(seg.get('end', 0)) + 0.05
        if fim > inicio:
            intervalos.append([inicio, fim])

    if not intervalos:
        log_event("SILENCIO", "Nenhum intervalo de fala encontrado", level="warn")
        return audio_path, []

    # 2. Mesclar intervalos com gap < silence_threshold
    intervalos.sort(key=lambda x: x[0])
    mesclados = [intervalos[0][:]]
    for inicio, fim in intervalos[1:]:
        if inicio - mesclados[-1][1] < silence_threshold:
            mesclados[-1][1] = max(fim, mesclados[-1][1])
        else:
            mesclados.append([inicio, fim])

    log_event("SILENCIO", f"{len(mesclados)} segmentos de fala detectados", level="info")

    # 3. CASO ESPECIAL: apenas 1 segmento = sem silencio significativo
    if len(mesclados) == 1:
        orig_dur = mesclados[0][1] - mesclados[0][0]
        log_event("SILENCIO",
                  f"Apenas 1 segmento ({orig_dur:.1f}s) — sem silencio significativo, usando audio original",
                  level="info")
        return audio_path, []  # mapeamento vazio = usa timestamps originais

    # 4. Calcular mapeamento de timestamps
    mapeamento = []
    cursor = 0.0
    for orig_inicio, orig_fim in mesclados:
        duracao = orig_fim - orig_inicio
        mapeamento.append({
            'orig_start': orig_inicio,
            'orig_end': orig_fim,
            'proc_start': cursor,
            'proc_end': cursor + duracao
        })
        cursor += duracao

    # 5. Abordagem em 2 passos: aselect para WAV intermediario, depois encoding AAC
    # (aselect direto para AAC causa erros de timestamp com Qavg: nan)
    from services.video_encoder import sanitizar_nome_arquivo
    from config import OUTPUT_DIR
    safe_tag = sanitizar_nome_arquivo(Path(audio_path).stem)

    # Input: copia para pasta temporaria sem apostrofo
    audio_path_seguro = audio_path
    if "'" in audio_path or '"' in audio_path:
        audio_path_seguro = str(OUTPUT_DIR / f"{safe_tag}_silence_input.mp3")
        if not Path(audio_path_seguro).exists():
            import shutil
            shutil.copy2(audio_path, audio_path_seguro)
            log_event("SILENCIO", f"Audio copiado para path seguro: {audio_path_seguro}", level="info")

    # Output: usa OUTPUT_DIR (nunca tem apostrofo)
    output_sguro = str(OUTPUT_DIR / f"{safe_tag}_no_silence.mp3")
    temp_wav = str(OUTPUT_DIR / f"{safe_tag}_no_silence_temp.wav")

    # Passo 1: aselect para WAV (sem problemas de timestamp)
    between_clauses = '+'.join(
        f"between(t,{inicio:.3f},{fim:.3f})" for inicio, fim in mesclados
    )
    cmd1 = [
        FFMPEG_PATH, '-y', '-i', audio_path_seguro,
        '-filter_complex', f"[0:a]aselect={between_clauses}",
        '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '2',
        temp_wav
    ]
    log_event("SILENCIO", f"Passo 1: aselect para WAV ({len(mesclados)} intervalos)...", level="info")
    r1 = subprocess.run(cmd1, capture_output=True, text=True, timeout=300)

    if r1.returncode != 0 or not Path(temp_wav).exists() or Path(temp_wav).stat().st_size == 0:
        log_event("SILENCIO",
                  f"Passo 1 falhou (codigo {r1.returncode}): {r1.stderr[-200:]} — usando audio original",
                  level="error")
        return audio_path, []

    # Passo 2: codificar WAV para AAC
    cmd2 = [
        FFMPEG_PATH, '-y', '-i', temp_wav,
        '-c:a', 'aac', '-b:a', '192k', '-ar', '44100',
        output_sguro
    ]
    log_event("SILENCIO", f"Passo 2: codificando WAV para AAC...", level="info")
    r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=300)

    # Limpa WAV temporario
    try: Path(temp_wav).unlink()
    except: pass

    if r2.returncode != 0 or not Path(output_sguro).exists() or Path(output_sguro).stat().st_size == 0:
        log_event("SILENCIO",
                  f"Passo 2 falhou (codigo {r2.returncode}) — usando audio original",
                  level="error")
        return audio_path, []

    # Validacao final com ffprobe
    from config import FFPROBE_PATH
    probe = subprocess.run(
        [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", output_sguro],
        capture_output=True, text=True, timeout=15
    )
    if probe.returncode != 0:
        log_event("SILENCIO", "Arquivo no_silence corrompido — usando audio original", level="error")
        return audio_path, []
    try:
        duracao_gerada = float(probe.stdout.strip())
        if duracao_gerada <= 0:
            log_event("SILENCIO", f"Duracao zero ({duracao_gerada:.1f}s) — usando audio original", level="error")
            return audio_path, []
    except (ValueError, TypeError):
        log_event("SILENCIO", "Duracao invalida — usando audio original", level="error")
        return audio_path, []

    removido = sum(
        mesclados[i][0] - mesclados[i-1][1]
        for i in range(1, len(mesclados))
        if mesclados[i][0] - mesclados[i-1][1] > silence_threshold
    )
    log_event("SILENCIO",
              f"Concluido: {len(mesclados)} segmentos mantidos | ~{removido:.1f}s removidos | saida: {Path(output_sguro).name} ({Path(output_sguro).stat().st_size} bytes, {duracao_gerada:.1f}s)",
              level="info")
    return output_sguro, mapeamento


def converter_timestamp(ts_original: float, mapeamento: list) -> float:
    """
    Converte timestamp do audio original para timestamp no audio processado.
    """
    if not mapeamento:
        return ts_original
    for m in mapeamento:
        if m['orig_start'] <= ts_original <= m['orig_end']:
            offset = ts_original - m['orig_start']
            return m['proc_start'] + offset
    # Timestamp fora dos intervalos de fala — retornar mais proximo
    for m in mapeamento:
        if ts_original < m['orig_start']:
            return m['proc_start']
    if mapeamento:
        return mapeamento[-1]['proc_end']
    return ts_original