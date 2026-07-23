# -*- coding: utf-8 -*-
import sys, json, os, time, shutil
sys.path.insert(0, 'C:\\ultracut3')

from services.pipeline_service import PipelineService
from config import PROJETOS_DIR

projeto = "teste_v36_com_apis"
audio = "C:/ultracut3/video1/1.mp3"

# Limpa projeto anterior se existir
shutil.rmtree(str(PROJETOS_DIR / projeto), ignore_errors=True)

print("=" * 60)
print("TESTE v3.6 COM APIs ATIVAS")
print("=" * 60)

# 1. Criar projeto
p = PipelineService()
r = p.criar_projeto(projeto, audio)
print("\n1. CRIAR PROJETO: %s" % json.dumps(r, ensure_ascii=False))

# 2. Pipeline com timestamps
progresso = []
inicio_etapas = {}

def callback(step, status, msg):
    now = time.time()
    progresso.append({"step": step, "status": status, "msg": msg, "ts": now})
    if status == "andamento":
        inicio_etapas[step] = now
    lbl = ["Transcricao", "Cenas", "Storyboard", "Midias", "Render"][step]
    if status == "andamento":
        print("   [INICIO] %s: %s" % (lbl, msg[:60]))
    elif status == "concluido":
        dur = now - inicio_etapas.get(step, now)
        print("   [FIM %.0fs] %s: %s" % (dur, lbl, msg[:80]))
    elif status == "erro":
        print("   [ERRO] %s: %s" % (lbl, msg[:80]))

p.set_progress_callback(callback)
start = time.time()
result = p.executar_pipeline_completo(audio)
elapsed = time.time() - start

# 3. Resultado
print("\n2. RESULTADO FINAL")
print("   Tempo total: %.1fs" % elapsed)
print("   Success: %s" % result.get('success'))

# 4. Logs
logs = p.get_logs_projeto()
print("\n3. LOGS DO PROJETO (%d entradas)" % len(logs))
for log in logs:
    print("   [%s] [%s] [%s]" % (log.get('ts',''), log.get('etapa','?'), log.get('status','?')))

# 5. Resultado da busca
resultado_file = PROJETOS_DIR / projeto / "midias_encontradas.json"
if resultado_file.exists():
    with open(str(resultado_file), "r", encoding="utf-8") as f:
        midias = json.load(f)
    green = sum(1 for m in midias if m.get("quality") == "green")
    needs = sum(1 for m in midias if m.get("needs_media"))
    total = len(midias)
    print("\n4. RESULTADO BUSCA DE MIDIAS")
    print("   Total cenas: %d" % total)
    print("   GREEN: %d (%.0f%%)" % (green, 100*green/total if total else 0))
    print("   Needs media: %d" % needs)
    
    # Verifica anti-reuso (URLs unicas)
    urls = [m.get("arquivo", "") for m in midias if m.get("success")]
    if urls:
        unicas = len(set(urls))
        print("   Arquivos baixados: %d" % len(urls))
        print("   Arquivos unicos: %d (anti-reuso: %s)" % (unicas, "OK" if unicas == len(urls) else "FALHOU"))
    
    print("\n   Detalhe por cena:")
    for m in midias:
        sid = m.get("scene_id", "?")
        if m.get("success"):
            print("     Cena %s: GREEN (%s)" % (sid, m.get("source", "?")))
        else:
            print("     Cena %s: %s" % (sid, "needs_media" if m.get("needs_media") else "falhou"))

# 6. Tempo da busca
tempos = {}
for ev in progresso:
    s = ev["step"]
    if s not in tempos:
        tempos[s] = {}
    tempos[s][ev["status"]] = ev["ts"]

print("\n5. TEMPO POR ETAPA (paralelo)")
for step_idx in sorted(tempos.keys()):
    t = tempos[step_idx]
    if "andamento" in t and "concluido" in t:
        delta = t["concluido"] - t["andamento"]
        nome = ["Transcricao", "Cenas", "Storyboard", "Midias", "Render"][step_idx]
        print("   %s: %.1fs" % (nome, delta))

# 7. Comparacao teorica
if 3 in tempos and "andamento" in tempos[3] and "concluido" in tempos[3]:
    t_paralelo = tempos[3]["concluido"] - tempos[3]["andamento"]
    t_sequencial_estimado = t_paralelo * 3  # 3 threads em paralelo vs 1 sequencial
    print("\n6. COMPARACAO VELOCIDADE")
    print("   Busca paralela (v3.6): %.1fs" % t_paralelo)
    print("   Busca sequencial estimada: %.1fs" % t_sequencial_estimado)
    print("   Ganho: %.1fx mais rapido" % (t_sequencial_estimado / t_paralelo if t_paralelo > 0 else 0))

# 8. Veredito
print("\n7. VEREDICTO")
if result.get("success"):
    print("   [OK] Pipeline completo em %.1fs" % elapsed)
else:
    print("   [AVISO] Pipeline com erros")