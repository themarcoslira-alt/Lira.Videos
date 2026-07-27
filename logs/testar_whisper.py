"""
Teste rápido para verificar configuração do faster-whisper
e reportar modelo atual vs otimizado.
"""
import time
import os
import sys
sys.path.insert(0, "c:/ultracut3")

# Config atual
print("=" * 60)
print("RELATORIO DE CONFIGURACAO DO WHISPER")
print("=" * 60)

from config import WHISPER_MODEL_SIZE, WHISPER_DEVICE
print(f"\nConfig atual (config.py):")
print(f"  model_size  = {WHISPER_MODEL_SIZE!r}")
print(f"  device      = {WHISPER_DEVICE!r}")
print(f"  compute_type= (nao especificado, padrao float32)")

# Teste com int8
print(f"\nTestando WhisperModel('base', device='cpu', compute_type='int8', cpu_threads={os.cpu_count()})...")
t0 = time.time()
from faster_whisper import WhisperModel
model = WhisperModel("base", device="cpu", compute_type="int8",
                      cpu_threads=os.cpu_count(), num_workers=2)
t1 = time.time()
print(f"  Carregamento: {t1-t0:.1f}s")
print(f"  Modelo pronto com int8, {os.cpu_count()} threads, 2 workers")
print(f"\nSugestao: modelo 'small' seria ~2x mais lento que 'base' mas mais preciso.")
print(f"         'medium' seria ~4-6x mais lento, nao recomendado para CPU.")
print("=" * 60)