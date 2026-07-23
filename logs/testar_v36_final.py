# -*- coding: utf-8 -*-
import sys, json, os, time, shutil
sys.path.insert(0, 'C:\\ultracut3')

from services.pipeline_service import PipelineService
from config import PROJETOS_DIR

projeto = "teste_v36_final_com_foto"
audio = "C:/ultracut3/video1/1.mp3"

shutil.rmtree(str(PROJETOS_DIR / projeto), ignore_errors=True)

print("=" * 60)
print("TESTE FINAL v3.6 - Com fallback video->photo")
print("=" * 60)

p = PipelineService()
r = p.criar_projeto(projeto, audio)
print("\n1. CRIAR PROJETO: %s" % json.dumps(r, ensure_ascii=False))

progresso = []
inicio_etapas = {}

def callback(step, status, msg):
    now = time.time()
    progresso.append({"step": step, "status": status, "msg": msg, "ts": now})
    if status == "andamento":
        inicio_etapas[step] = now
    lbl = ["Transcricao", "Cenas", "Storyboard", "Midias", "Render"][step]
    if status == "andamento":
        print("   [INICIO] %s: %s" % (lbl, msg[:80]))
    elif status == "concluido":
        dur = now - inicio_etapas.get(step, now)
        print("   [FIM %.0fs] %s: %s" % (dur, lbl, msg[:80]))
    elif status == "erro":
        print("   [ERRO] %s: %s" % (lbl, msg[:80]))

p.set_progress_callback(callback)
start = time.time()
result = p.executar_pipeline_completo(audio)
elapsed = time.time() - start

print("\n2. RESULTADO FINAL")
print("   Tempo total: %.1fs" % elapsed)
print("   Success: %s" % result.get('success'))

logs = p.get_logs_projeto()
print("\n3. LOGS (%d entradas)" % len(logs))
for log in logs:
    print("   [%s] [%s] [%s]" % (log.get('ts',''), log.get('etapa','?'), log.get('status','?')))

resultado_file = PROJETOS_DIR / projeto / "midias_encontradas.json"
if resultado_file.exists():
    with open(str(resultado_file), "r", encoding="utf-8") as f:
        midias = json.load(f)
    green = sum(1 for m in midias if m.get("quality") == "green")
    needs = sum(1 for m in midias if m.get("needs_media"))
    total = len(midias)
    print("\n4. RESULTADO MIDIAS")
    print("   Total: %d | GREEN: %d | Needs: %d" % (total, green, needs))
    
    urls = [m.get("arquivo", "") for m in midias if m.get("success")]
    if urls:
        print("   Arquivos baixados: %d" % len(urls))
        print("   Arquivos unicos: %d" % len(set(urls)))
    
    print("\n   Detalhe:")
    for m in midias:
        sid = m.get("scene_id", "?")
        if m.get("success"):
            print("     Cena %s: GREEN (%s)" % (sid, m.get("source", "?")))
        else:
            print("     Cena %s: %s" % (sid, "needs_media" if m.get("needs_media") else "falhou"))

    # Verifica se render rodou
    output_file = PROJETOS_DIR / projeto.parent / "output" / "%s.mp4" % projeto
    render_result = result.get("results", {}).get("renderizar", {})
    if render_result.get("success"):
        print("\n5. RENDER: OK - %s" % render_result.get("arquivo", ""))
    else:
        print("\n5. RENDER: %s" % render_result.get("error", "nao executou"))

# Tempos
tempos = {}
for ev in progresso:
    s = ev["step"]
    if s not in tempos:
        tempos[s] = {}
    tempos[s][ev["status"]] = ev["ts"]

print("\n6. TEMPOS:")
for step_idx in sorted(tempos.keys()):
    t = tempos[step_idx]
    if "andamento" in t and "concluido" in t:
        delta = t["concluido"] - t["andamento"]
        nome = ["Transcricao", "Cenas", "Storyboard", "Midias", "Render"][step_idx]
        print("   %s: %.1fs" % (nome, delta))

if result.get("success"):
    print("\n7. VEREDICTO: [OK] Pipeline completo em %.1fs" % elapsed)
else:
    print("\n7. VEREDICTO: [AVISO] Pipeline com erros")