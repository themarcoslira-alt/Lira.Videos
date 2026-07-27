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

    # 5. Construir filtro FFmpeg com sintaxe start=X:end=Y
    partes = []
    for i, (inicio, fim) in enumerate(mesclados):
        partes.append(
            f"[0:a]atrim=start={inicio:.3f}:end={fim:.3f},"
            f"asetpts=PTS-STARTPTS[a{i}]"
        )
    concat_in = ''.join(f'[a{i}]' for i in range(len(mesclados)))
    partes.append(
        f"{concat_in}concat=n={len(mesclados)}:v=0:a=1[out]"
    )
    filter_complex = ';'.join(partes)

    # Sanitiza o path de entrada antes de passar ao FFmpeg (remove apostrofos)
    from services.video_encoder import sanitizar_nome_arquivo
    audio_path_seguro = audio_path
    if "'" in audio_path or '"' in audio_path:
        # Copia para um caminho seguro temporario
        safe_tag = sanitizar_nome_arquivo(Path(audio_path).stem)
        audio_path_seguro = str(Path(output_path).parent / f"{safe_tag}_input_safe.mp3")
        if not Path(audio_path_seguro).exists():
            import shutil
            shutil.copy2(audio_path, audio_path_seguro)
            log_event("SILENCIO", f"Audio copiado para path seguro: {audio_path_seguro}", level="info")

    # 6. Executar FFmpeg com -ar 44100 para compatibilidade
    cmd = [
        FFMPEG_PATH, '-y', '-i', audio_path_seguro,
        '-filter_complex', filter_complex,
        '-map', '[out]',
        '-c:a', 'aac', '-b:a', '192k',
        '-ar', '44100',
        output_path
    ]
    log_event("SILENCIO", f"Processando {len(mesclados)} segmentos...", level="info")
    resultado = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    # Validacao pos-processamento
    if resultado.returncode != 0:
        log_event("SILENCIO",
                  f"FFmpeg falhou (codigo {resultado.returncode}): {resultado.stderr[-300:]} — usando audio original",
                  level="error")
        return audio_path, []

    if not Path(output_path).exists() or Path(output_path).stat().st_size == 0:
        log_event("SILENCIO",
                  f"Arquivo gerado vazio ou ausente — usando audio original",
                  level="error")
        return audio_path, []

    # Verifica duracao do arquivo gerado
    from config import FFPROBE_PATH
    probe = subprocess.run(
        [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", output_path],
        capture_output=True, text=True, timeout=15
    )
    if probe.returncode != 0:
        log_event("SILENCIO",
                  f"Arquivo no_silence gerado corrompido (ffprobe falhou) — usando audio original",
                  level="error")
        return audio_path, []
    try:
        duracao_gerada = float(probe.stdout.strip())
        if duracao_gerada <= 0:
            log_event("SILENCIO",
                      f"Arquivo no_silence gerado com duracao zero — usando audio original",
                      level="error")
            return audio_path, []
    except (ValueError, TypeError):
        log_event("SILENCIO",
                  f"Arquivo no_silence gerado com duracao invalida — usando audio original",
                  level="error")
        return audio_path, []

    removido = sum(
        mesclados[i][0] - mesclados[i-1][1]
        for i in range(1, len(mesclados))
        if mesclados[i][0] - mesclados[i-1][1] > silence_threshold
    )
    log_event("SILENCIO",
              f"Concluido: {len(mesclados)} segmentos mantidos | ~{removido:.1f}s removidos | saida: {Path(output_path).name} ({Path(output_path).stat().st_size} bytes, {duracao_gerada:.1f}s)",
              level="info")
    return output_path, mapeamento


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