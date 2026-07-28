# -*- coding: utf-8 -*-
import subprocess, os, shutil, sys
sys.path.insert(0, "c:/ultracut3")
from config import FFMPEG_PATH, FFPROBE_PATH

print("FFMPEG:", FFMPEG_PATH)
print("FFPROBE:", FFPROBE_PATH)
print()

base = r"c:\ultracut3\projetos"

for nome in os.listdir(base):
    full_path = os.path.join(base, nome)
    if os.path.isdir(full_path) and nome.upper().startswith("YOU"):
        pasta = full_path
        print("Pasta:", pasta)
        for arq in os.listdir(pasta):
            full = os.path.join(pasta, arq)
            if os.path.isfile(full) and arq.upper().endswith(".MP3"):
                tam = os.path.getsize(full)
                print("MP3 original:", full, "(" + str(tam) + " bytes)")
                
                # Teste 1: ffprobe no caminho ORIGINAL (com apostrofo)
                print("\n--- Teste 1: ffprobe no caminho COM apostrofo ---")
                probe1 = subprocess.run(
                    [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", full],
                    capture_output=True, text=True, timeout=15
                )
                print("returncode:", probe1.returncode)
                print("stdout:", probe1.stdout.strip()[:100])
                if probe1.stderr:
                    print("stderr:", probe1.stderr[:300])
                
                # Teste 2: Copiar para output/ SEM apostrofo
                print("\n--- Teste 2: copia para output/ sem apostrofo ---")
                output_dir = r"c:\ultracut3\output"
                destino = os.path.join(output_dir, "test_audio_input.mp3")
                shutil.copy2(full, destino)
                print("Copiado:", destino, "(" + str(os.path.getsize(destino)) + " bytes)")
                
                probe2 = subprocess.run(
                    [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", destino],
                    capture_output=True, text=True, timeout=15
                )
                print("ffprobe returncode:", probe2.returncode)
                print("ffprobe stdout:", probe2.stdout.strip()[:100])
                if probe2.stderr:
                    print("ffprobe stderr:", probe2.stderr[:300])
                
                # Teste 3: ffmpeg atrim no audio sanitizado
                print("\n--- Teste 3: ffmpeg atrim (corte de silencio simulado) ---")
                out_file = os.path.join(output_dir, "test_silence_out.mp3")
                if os.path.exists(out_file):
                    os.remove(out_file)
                cmd = [
                    FFMPEG_PATH, "-y",
                    "-i", destino,
                    "-filter_complex",
                    "[0:a]atrim=start=0:end=10,asetpts=PTS-STARTPTS[a0];[a0]concat=n=1:v=0:a=1[out]",
                    "-map", "[out]", "-c:a", "aac", "-b:a", "192k",
                    "-ar", "44100",
                    out_file
                ]
                print("CMD:", " ".join(cmd))
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                print("returncode:", res.returncode)
                if res.stderr:
                    print("stderr (ultimas 500):", res.stderr[-500:])
                if os.path.exists(out_file):
                    print("Tamanho saida:", os.path.getsize(out_file), "bytes")
                else:
                    print("ARQUIVO SAIDA NAO EXISTE!")

print("\n=== DIAGNOSTICO CONCLUIDO ===")