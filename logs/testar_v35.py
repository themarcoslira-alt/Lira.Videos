# -*- coding: utf-8 -*-
import sys, json, os, time, shutil
sys.path.insert(0, 'C:\\ultracut3')

from services.pipeline_service import PipelineService
from config import PROJETOS_DIR

# Remove projetos de teste anteriores
for pasta in ["teste_v35", "TesteAutoV3", "ValidacaoCrash2", "ValidacaoFinal", "TesteCrash2"]:
    caminho = PROJETOS_DIR / pasta
    if caminho.exists():
        shutil.rmtree(str(caminho))

p = PipelineService()
projeto = "teste_v35_final"
audio = "C:/ultracut3/video1/1.mp3"

print("=" * 60)
print("TESTE v3.5 - Fluxo completo")
print("=" * 60)

print("\n1. CRIAR PROJETO '%s' com audio" % projeto)
r = p.criar_projeto(projeto, audio)
print("   Resultado: %s" % json.dumps(r, ensure_ascii=False))
assert r.get("success")
assert p.project_name == projeto
print("   Projeto selecionado: %s [OK]" % p.project_name)

meta = p._carregar_meta()
print("   Meta tem audio: %s" % bool(meta.get('arquivo_audio', '')))

projetos = p.listar_projetos()
nomes = [pr.get("name") for pr in projetos]
assert projeto in nomes
print("   Na lista de projetos: [OK]")

print("\n2. PIPELINE COMPLETO")
progresso = []

def callback(step, status, msg):
    progresso.append({"step": step, "status": status, "msg": msg})
    print("   [etapa %d] [%s] %s" % (step, status, msg[:80]))

p.set_progress_callback(callback)
start = time.time()
result = p.executar_pipeline_completo(audio)
elapsed = time.time() - start

print("\n3. RESULTADO")
print("   Tempo: %.1fs" % elapsed)
print("   Success: %s" % result.get('success'))
if result.get("results"):
    for etapa, res in result["results"].items():
        s = "[OK]" if res.get("success") else "[FALHOU]"
        msg = res.get('error', res.get('message', 'OK'))
        print("   %s %s: %s" % (s, etapa, str(msg)[:80]))

print("\n4. LOGS (%d entradas)" % len(p.get_logs_projeto()))
for log in p.get_logs_projeto()[:5]:
    print("   [%s] [%s] [%s]" % (log.get('ts',''), log.get('etapa','?'), log.get('status','?')))

print("\n5. PROGRESSO (%d eventos)" % len(progresso))
for ev in progresso:
    print("   [%s] etapa %d: %s" % (ev["status"], ev["step"], ev["msg"][:80]))

print("\n6. VEREDICTO")
if result.get("success"):
    print("   [OK] PIPELINE COMPLETO em %.1fs" % elapsed)
else:
    print("   [AVISO] Pipeline com erros")
    for etapa, res in result.get("results", {}).items():
        if not res.get("success"):
            print("      [FALHOU] %s: %s" % (etapa, res.get('error', '')))