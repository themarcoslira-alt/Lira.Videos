# -*- coding: utf-8 -*-
"""
Teste isolado do algoritmo de 2 passos para corte de silencio.
Passo 1: aselect para WAV
Passo 2: WAV para AAC/mp3
Nao depende de whisper segments - usa intervalos fixos.
"""
import subprocess, os, sys
sys.path.insert(0, "c:/ultracut3")
from config import FFMPEG_PATH, FFPROBE_PATH

out_dir = r"c:\ultracut3\output"
test_in = os.path.join(out_dir, "test_audio_input.mp3")

if not os.path.exists(test_in):
    print("ERRO: test_audio_input.mp3 nao encontrado")
    sys.exit(1)

print("=" * 60)
print("TESTE ISOLADO - 2 PASSOS CORTE DE SILENCIO")
print("=" * 60)
print("Audio original:", test_in, "(" + str(os.path.getsize(test_in)) + " bytes)")
print()

# Simula 2 intervalos de fala (0-10s e 20-30s)
intervalos = [(0.0, 10.0), (20.0, 30.0)]
between = "+".join(f"between(t,{i:.3f},{f:.3f})" for i, f in intervalos)
filter_complex = f"[0:a]aselect={between}"

# PASSO 1: aselect -> WAV
wav_temp = os.path.join(out_dir, "test_2pass_temp.wav")
if os.path.exists(wav_temp):
    os.remove(wav_temp)

cmd1 = [FFMPEG_PATH, "-y", "-i", test_in,
        "-filter_complex", filter_complex,
        "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2",
        wav_temp]

print("Passo 1: aselect -> WAV")
print("  Filtro:", filter_complex)
r1 = subprocess.run(cmd1, capture_output=True, text=True, timeout=120)
print("  returncode:", r1.returncode)

if r1.returncode == 0 and os.path.exists(wav_temp):
    wav_size = os.path.getsize(wav_temp)
    print("  WAV gerado:", wav_size, "bytes")
    
    # Valida WAV com ffprobe
    probe1 = subprocess.run([FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=noprint_wrappers=1:nokey=1", wav_temp],
                            capture_output=True, text=True, timeout=15)
    print("  ffprobe WAV: rc=" + str(probe1.returncode) + " duracao=" + probe1.stdout.strip()[:10])
else:
    print("  ERRO: WAV nao gerado")
    if r1.stderr:
        print("  stderr:", r1.stderr[-500:])
    sys.exit(1)

# PASSO 2: WAV -> AAC/mp3
mp3_final = os.path.join(out_dir, "test_2pass_final.mp3")
if os.path.exists(mp3_final):
    os.remove(mp3_final)

cmd2 = [FFMPEG_PATH, "-y", "-i", wav_temp,
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        mp3_final]

print()
print("Passo 2: WAV -> AAC")
r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=120)
print("  returncode:", r2.returncode)

# Limpa WAV
try: os.remove(wav_temp)
except: pass

if r2.returncode == 0 and os.path.exists(mp3_final):
    mp3_size = os.path.getsize(mp3_final)
    print("  MP3 gerado:", mp3_size, "bytes")
    
    probe2 = subprocess.run([FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=noprint_wrappers=1:nokey=1", mp3_final],
                            capture_output=True, text=True, timeout=15)
    print("  ffprobe MP3: rc=" + str(probe2.returncode) + " duracao=" + probe2.stdout.strip()[:10])
    
    if mp3_size > 100000 and probe2.returncode == 0:
        print()
        print(">>> CRITERIOS ATENDIDOS: arquivo valido com", mp3_size, "bytes!")
    else:
        print()
        print(">>> FALHA: arquivo muito pequeno ou ffprobe falhou")
else:
    print("  ERRO: MP3 nao gerado")
    if r2.stderr:
        print("  stderr:", r2.stderr[-500:])

print()
print("=" * 60)
print("TESTE CONCLUIDO")
print("=" * 60)