# -*- coding: utf-8 -*-
"""Teste isolado do filtro silenceremove do FFmpeg"""
import subprocess, os, sys
sys.path.insert(0, "c:/ultracut3")
from config import FFMPEG_PATH, FFPROBE_PATH

audio_in = r"c:\ultracut3\output\test_audio_input.mp3"
out_mp3 = r"c:\ultracut3\output\test_silenceremove_final.mp3"

print("=" * 60)
print("TESTE: silenceremove (built-in FFmpeg)")
print("=" * 60)
print("Audio:", audio_in)
print("Tamanho:", os.path.getsize(audio_in), "bytes")
print()

# Teste: silenceremove nativo
if os.path.exists(out_mp3):
    os.remove(out_mp3)

af = "silenceremove=stop_periods=-1:stop_duration=0.5:stop_threshold=-50dB"
cmd = [FFMPEG_PATH, "-y", "-i", audio_in, "-af", af,
       "-c:a", "aac", "-b:a", "192k", "-ar", "44100", out_mp3]

print("Comando: ffmpeg -y -i <input> -af " + af + " ...")
r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
print("returncode:", r.returncode)
print("stderr (ultimas 500 chars):", r.stderr[-500:] if r.stderr else "(vazio)")
print()

if os.path.exists(out_mp3):
    sz = os.path.getsize(out_mp3)
    print(">>> ARQUIVO GERADO:", sz, "bytes")
    # Valida com ffprobe
    probe = subprocess.run([FFPROBE_PATH, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", out_mp3],
        capture_output=True, text=True, timeout=15)
    print(">>> ffprobe: rc=" + str(probe.returncode), "duracao=" + probe.stdout.strip()[:10])
    if sz > 100000 and probe.returncode == 0:
        print(">>> CRITERIOS ATENDIDOS!")
    else:
        print(">>> CRITERIOS NAO ATENDIDOS")
else:
    print(">>> ARQUIVO NAO GERADO")

print()
print("=" * 60)
print("TESTE CONCLUIDO")
print("=" * 60)