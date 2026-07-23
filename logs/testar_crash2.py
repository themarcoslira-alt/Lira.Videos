"""
Script de validacao dos 2 crashes corrigidos.
"""
import sys, json, os
sys.path.insert(0, 'C:\\ultracut3')

from services.pipeline_service import PipelineService
from services.event_logger import log_event, ler_eventos

def step(label, result):
    print(f"  [{label}] {json.dumps(result, ensure_ascii=False)[:200]}")
    return result.get("success", False)

print("="*60)
print("VALIDACAO v3.4 - CRASH 1: criar_projeto")
print("="*60)
s = PipelineService()

# CRASH 1 - criar projeto
step("criar_projeto", s.criar_projeto("ValidacaoCrash2"))
step("criar_projeto (repetido)", s.criar_projeto("ValidacaoCrash2"))
step("listar_projetos", {"count": len(s.listar_projetos())})

print()
print("="*60)
print("VALIDACAO v3.4 - Pipeline 5 etapas")
print("="*60)

# Cria roteiro falso
p = 'C:/ultracut3/projetos/ValidacaoCrash2'
os.makedirs(p, exist_ok=True)
with open(f'{p}/roteiro_transcricao.txt', 'w', encoding='utf-8') as f:
    f.write('[00:00] Welcome to this video about artificial intelligence\n')
    f.write('[00:05] Today we will explore how neural networks work\n')
    f.write('[00:10] This technology is transforming many industries\n')
    f.write('[00:15] In conclusion AI will shape our future\n')

from services.scene_builder import gerar_cenas
from services.broll_director import gerar_storyboard
from services.query_generator import gerar_queries
from services.media_search import buscar_midias_projeto

step("gerar_cenas", gerar_cenas('ValidacaoCrash2'))
step("gerar_storyboard", gerar_storyboard('ValidacaoCrash2', usar_claude=False))
step("gerar_queries", gerar_queries('ValidacaoCrash2'))

print()
print("="*60)
print("BUSCANDO MIDIAS (vai tentar APIs, pode levar tempo)")
print("="*60)
r = buscar_midias_projeto('ValidacaoCrash2')
print(f"Resultado: success={r.get('success')} total={r.get('total_scenes')} green={r.get('green')} yellow={r.get('yellow')} needs={r.get('needs_media')}")
if r.get('resultados'):
    for res in r['resultados']:
        print(f"  Cena {res.get('scene_id','?')}: ok={res.get('success')} quality={res.get('quality','?')} passada={res.get('passada','?')} source={res.get('source','?')} needs={res.get('needs_media','')}")

print()
print("="*60)
print("LOG FINAL")
print("="*60)
for e in ler_eventos(linhas=100):
    print(f"  [{e.get('ts','')}] [{e.get('category','?')}] [{e.get('level','?')}] {e.get('message','')}")