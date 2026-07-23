"""Testa buscar_midias_paralelo com ThreadPoolExecutor paralelo"""
import sys, json, time
sys.stdout.reconfigure(encoding="utf-8")
from services.media_fetcher import buscar_midias_paralelo

t0 = time.time()
result = buscar_midias_paralelo("plant", "photo", set())
tm = time.time() - t0

print(f"count={len(result)} time={tm:.1f}s")
for c in result:
    print(f"  {c['source']:7s} {c.get('width','?'):>5}x{c.get('height','?'):<5} score={c.get('score')} url={c.get('url','')[:60]}")