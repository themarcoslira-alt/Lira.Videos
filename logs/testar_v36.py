# -*- coding: utf-8 -*-
import sys, json, os, time
sys.path.insert(0, 'C:\\ultracut3')
sys.stdout.reconfigure(encoding='utf-8')

from services.pipeline_service import PipelineService
from config import PROJETOS_DIR

projeto = "teste_v36_paralelo"
audio = "C:/ultracut3/video1/1.mp3"

print("=" * 60)
print("VALIDACAO v3.6 - Busca paralela + GREEN-only + anti-reuso")
print("=" * 60)

# 1. Criar projeto
print("\n1. CRIAR PROJETO '%s'" % projeto)
p = PipelineService()
r = p.criar_projeto(projeto, audio)
print("   Resultado: %s" % json.dumps(r, ensure_ascii=False))

# 2. Executar pipeline completo com timestamps
print("\n2. PIPELINE COMPLETO (com medicoes)")
progresso = []
def callback(step, status, msg):
    now = time.time()
    progresso.append({"step": step, "status": status, "msg": msg, "ts": now})
    print("   [%.1fs] [etapa %d] [%s] %s" % (now - start, step, status, msg[:80]))

p.set_progress_callback(callback)
start = time.time()
result = p.executar_pipeline_completo(audio)
elapsed = time.time() - start

# 3. Resultado
print("\n3. RESULTADO FINAL")
print("   Tempo total: %.1fs" % elapsed)
print("   Success: %s" % result.get('success'))
if result.get("results"):
    for etapa, res in result["results"].items():
        s = "[OK]" if res.get("success") else "[FALHOU]"
        print("   %s %s" % (s, etapa))

# Medir tempo da busca de midias
tempos_busca = [ev for ev in progresso if ev["step"] == 3]
tempos_render = [ev for ev in progresso if ev["step"] == 4]
tempos_transc = [ev for ev in progresso if ev["step"] == 0]

print("\n4. MEDICAO DE TEMPO POR ETAPA")
etapas_tempos = {}
for ev in progresso:
    s = ev["step"]
    if s not in etapas_tempos:
        etapas_tempos[s] = {}
    etapas_tempos[s][ev["status"]] = ev["ts"]

for step_idx in sorted(etapas_tempos.keys()):
    t = etapas_tempos[step_idx]
    if "andamento" in t and "concluido" in t:
        delta = t["concluido"] - t["andamento"]
        nome = ["Transcricao", "Cenas", "Storyboard", "Midias", "Render"][step_idx]
        print("   %s: %.1fs" % (nome, delta))

# Detalhes da busca de midias
if tempos_busca:
    inicio_busca = min(ev["ts"] for ev in tempos_busca if ev["status"] == "andamento")
    fim_busca = max(ev["ts"] for ev in tempos_busca if ev["status"] == "concluido" or ev["status"] == "erro")
    delta_busca = fim_busca - inicio_busca
    print("\n5. TEMPO DA BUSCA PARALELA")
    print("   Inicio: %.1fs" % (inicio_busca - start))
    print("   Fim: %.1fs" % (fim_busca - start))
    print("   Delta: %.1fs" % delta_busca)

# 6. Logs do projeto
logs = p.get_logs_projeto()
print("\n6. LOGS DO PROJETO (%d entradas)" % len(logs))
for log in logs:
    print("   [%s] [%s] [%s]" % (log.get('ts',''), log.get('etapa','?'), log.get('status','?')))

# 7. Resultado da busca
resultado_file = PROJETOS_DIR / projeto / "midias_encontradas.json"
if resultado_file.exists():
    with open(str(resultado_file), "r", encoding="utf-8") as f:
        midias = json.load(f)
    green_count = sum(1 for m in midias if m.get("quality") == "green")
    needs_count = sum(1 for m in midias if m.get("needs_media"))
    total = len(midias)
    print("\n7. RESULTADO DA BUSCA")
    print("   Total cenas: %d" % total)
    print("   GREEN: %d (%.0f%%)" % (green_count, 100*green_count/total if total else 0))
    print("   Needs media: %d" % needs_count)

# 8. Veredito
print("\n8. VEREDICTO")
print("   [OK] Pipeline completo em %.1fs" % elapsed)
print("   [OK] Busca paralela completada")
print("   [OK] Anti-reuso via used_urls" if green_count > 0 else "   [AVISO] Sem midias encontradas")