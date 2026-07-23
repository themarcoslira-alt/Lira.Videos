"""
Validacao Final — Pipeline B-Roll (Fase 5)
Executa testes controlados, mede metricas e diagnostica gargalos.
"""
import sys, json, time
from pathlib import Path
from services.scene_context import ScenePlanningContext
from services.broll_director import gerar_storyboard
from services.query_pool import QueryPool

# ============================================================
# METRICAS GLOBAIS
# ============================================================
metricas = {
    "pipeline": {},
    "claude": {},
    "apis": {},
    "cobertura": {},
    "qualidade": {},
    "performance": {},
    "falhas": []
}

def registrar(secao, chave, valor):
    if secao not in metricas:
        metricas[secao] = {}
    metricas[secao][chave] = valor
    print(f"  [METRICA] {secao}.{chave} = {valor}")

def log(msg):
    print(f"[VALIDACAO] {msg}")

log("=== VALIDACAO FINAL — FASE 5 ===")
log("Projeto: TESTE\n")

# ============================================================
# 1. ESTADO DO PROJETO
# ============================================================
log("--- 1. Verificando estado do projeto ---")
proj_dir = Path("c:/ultracut3/projetos/TESTE")
files = [f.name for f in proj_dir.iterdir() if f.is_file()]
log(f"Arquivos: {files}")

has_json = any("roteiro_transcricao.json" in f for f in files)
has_txt = any("roteiro_transcricao.txt" in f for f in files)
has_cenas = any("cenas.json" in f for f in files)
has_storyboard = any("storyboard.json" in f for f in files)
has_midias = any("midias_encontradas.json" in f for f in files)
has_meta = any("meta.json" in f for f in files)

registrar("pipeline", "roteiro_json", has_json)
registrar("pipeline", "roteiro_txt", has_txt)
registrar("pipeline", "cenas_json", has_cenas)
registrar("pipeline", "storyboard_json", has_storyboard)
registrar("pipeline", "midias_json", has_midias)

# ============================================================
# 2. SCENE PLANNING CONTEXT
# ============================================================
log("\n--- 2. Testando ScenePlanningContext ---")
t0 = time.time()
ctx = ScenePlanningContext("TESTE")
t1 = time.time()

registrar("performance", "scene_context_load_ms", round((t1-t0)*1000, 1))
registrar("pipeline", "cenas_count", ctx.cenas_count)
registrar("pipeline", "segment_count", ctx.segment_count)
registrar("pipeline", "duration_total", ctx.duration_total)
registrar("pipeline", "full_script_len", len(ctx.full_script))

log(f"Cenas: {ctx.cenas_count}, Segmentos: {ctx.segment_count}, Duração: {ctx.duration_total}s")

# Cena 0
c0 = ctx.get_cena(1)
if c0:
    campos_esperados = ["scene_id", "texto", "start_time", "end_time", "duration",
                       "previous_context", "next_context", "transcript_lines",
                       "narrative_role", "visual_intent", "search_strategies"]
    campos_presentes = [c for c in campos_esperados if c in c0]
    campos_ausentes = [c for c in campos_esperados if c not in c0]
    registrar("pipeline", "campos_planejamento_presentes", len(campos_presentes))
    registrar("pipeline", "campos_planejamento_ausentes", len(campos_ausentes))
    log(f"Campos planejamento: {len(campos_presentes)}/{len(campos_esperados)}")

# Batch
batch = ctx.build_batch()
registrar("pipeline", "batch_scenes", len(batch["scenes"]))
registrar("pipeline", "batch_full_script_len", len(batch["full_script"]))
log(f"Batch: {len(batch['scenes'])} cenas, script={len(batch['full_script'])} chars")

# ============================================================
# 3. STORYBOARD (local, sem Claude)
# ============================================================
log("\n--- 3. Testando Storyboard (local) ---")
t0 = time.time()
sb = gerar_storyboard("TESTE", usar_claude=False)
t1 = time.time()

registrar("performance", "storyboard_local_ms", round((t1-t0)*1000, 1))
registrar("cobertura", "camada", sb.get("camada"))
registrar("cobertura", "cenas_storyboard", sb.get("cenas_count"))

if sb.get("storyboard"):
    s0 = sb["storyboard"][0]
    log(f"Storyboard cena 0 keys: {list(s0.keys())}")
    registrar("cobertura", "storyboard_tem_search_queries", "search_queries" in s0)
    if "search_queries" not in s0:
        # Storyboard local antigo nao tem search_queries, usa keywords
        registrar("cobertura", "storyboard_usa_keywords_fallback", True)
        log("  -> Storyboard usa keywords como fallback (sem search_queries)")

# ============================================================
# 4. QUERY POOL
# ============================================================
log("\n--- 4. Testando QueryPool ---")
pool = QueryPool(scene_id=1, queries=["dandelion plant", "dandelion flower"],
                 media_type="photo", fallback_queries=["wildflower yellow"])
registrar("cobertura", "query_pool_queries", pool.total_queries())
registrar("cobertura", "query_pool_primary", len(pool.primary_queries))
registrar("cobertura", "query_pool_fallback", len(pool.fallback_queries))

# Teste com single
pool_single = QueryPool.from_single(scene_id=2, query="garden plant", media_type="video")
registrar("cobertura", "query_pool_from_single", pool_single.total_queries())

# Verificacao de duplicacao
pool_dedup = QueryPool(scene_id=3, queries=["a", "a", "b", "b", "c"])
registrar("cobertura", "query_pool_dedup", pool_dedup.total_queries())
log(f"Pool dedup: {pool_dedup.total_queries()} queries (3 esperado)")

# ============================================================
# 5. MEDIA SEARCH (simulado - verifica estrutura apenas)
# ============================================================
log("\n--- 5. Verificando integracao media_search ---")
if has_midias:
    try:
        midias = json.loads(open(proj_dir / "midias_encontradas.json", encoding="utf-8").read())
        total = len(midias)
        green = sum(1 for m in midias if m.get("quality") == "green")
        needs = sum(1 for m in midias if m.get("needs_media"))
        registrar("qualidade", "midias_total", total)
        registrar("qualidade", "midias_green", green)
        registrar("qualidade", "midias_needs_media", needs)
        if total > 0:
            registrar("cobertura", "scene_coverage_rate", round(green/total, 2))
            registrar("qualidade", "green_rate", round(green/total*100, 1))
        log(f"Midias: {total} total, {green} green, {needs} needs_media")
        if green > 0:
            m0 = next((m for m in midias if m.get("quality") == "green"), midias[0])
            log(f"  Melhor: {m0.get('source')} {m0.get('width')}x{m0.get('height')} {m0.get('quality')}")
    except Exception as e:
        log(f"  ERRO lendo midias: {e}")
else:
    log("  Sem midias_encontradas.json (busca nao executada ainda)")

# ============================================================
# 6. STORYBOARD ENRIQUECIMENTO
# ============================================================
log("\n--- 6. Verificando enriquecimento do storyboard ---")
if has_storyboard:
    try:
        storyboard = json.loads(open(proj_dir / "storyboard.json", encoding="utf-8").read())
        sb_ids = [s.get("id") for s in storyboard]
        sb_keys = list(storyboard[0].keys()) if storyboard else []
        registrar("cobertura", "storyboard_ids", sb_ids)
        registrar("cobertura", "storyboard_campos", len(sb_keys))
        log(f"Storyboard: {len(storyboard)} cenas, ids={sb_ids}")
        log(f"  Campos: {sb_keys}")
        if "search_queries" in sb_keys:
            total_sq = sum(len(s.get("search_queries", [])) for s in storyboard)
            registrar("cobertura", "total_search_queries", total_sq)
            log(f"  Total search_queries: {total_sq}")
    except Exception as e:
        log(f"  ERRO lendo storyboard: {e}")

# ============================================================
# 7. VERIFICACAO DO CENAS.JSON v2
# ============================================================
log("\n--- 7. Verificando cenas.json v2 ---")
if has_cenas:
    try:
        cenas = json.loads(open(proj_dir / "cenas.json", encoding="utf-8").read())
        c0 = cenas[0] if cenas else {}
        campos_v2 = list(c0.keys())
        tem_v2 = all(k in c0 for k in ["start_time", "end_time", "duration", 
                                         "previous_context", "next_context",
                                         "transcript_lines"])
        registrar("pipeline", "cenas_v2_completo", tem_v2)
        registrar("pipeline", "cenas_v2_campos", len(campos_v2))
        log(f"cenas.json v2 completo: {tem_v2}")
        log(f"Campos: {campos_v2}")
        if tem_v2:
            log(f"  start_time={c0['start_time']}s end={c0['end_time']}s dur={c0['duration']}s")
            log(f"  prev_ctx={len(c0['previous_context'])} chars next_ctx={len(c0['next_context'])} chars")
            log(f"  transcript_lines={len(c0['transcript_lines'])} segmentos")
    except Exception as e:
        log(f"  ERRO lendo cenas: {e}")

# ============================================================
# 8. RESUMO
# ============================================================
log("\n" + "="*60)
log("RESUMO DA VALIDACAO")
log("="*60)

for secao, dados in metricas.items():
    if isinstance(dados, dict) and dados:
        log(f"\n{secao.upper()}:")
        for k, v in dados.items():
            log(f"  {k}: {v}")
    elif isinstance(dados, list) and dados:
        log(f"\n{secao.upper()}:")
        for item in dados:
            log(f"  - {item}")

log("\n" + "="*60)
log("RELATORIO DE OTIMIZACAO")
log("="*60)

# Analise
cobertura = metricas.get("cobertura", {})
qualidade = metricas.get("qualidade", {})
performance = metricas.get("performance", {})

total_cenas = metricas.get("pipeline", {}).get("cenas_count", 0)
batch_scenes = metricas.get("pipeline", {}).get("batch_scenes", 0)

recomendacoes = []

if not has_json:
    recomendacoes.append("1. [ROTEIRO JSON] Projeto TESTE nao tem roteiro_transcricao.json (fallback TXT). "
                         "Apos nova transcricao, o JSON sera gerado automaticamente com timestamps precisos.")

if batch_scenes != total_cenas:
    recomendacoes.append(f"2. [INCONSISTENCIA] batch_scenes ({batch_scenes}) != cenas_count ({total_cenas})")

if not cobertura.get("storyboard_tem_search_queries", False):
    recomendacoes.append("3. [SEARCH QUERIES] Storyboard local (sem Claude) nao gera search_queries. "
                         "Isso e esperado pelo design, mas limita a quantidade de queries testadas.")

if cobertura.get("query_pool_from_single", 0) > 0:
    pass  # from_single OK

# Avaliacao final
if recomendacoes:
    log("\nRecomendacoes de melhoria (ate 3):")
    for r in recomendacoes[:3]:
        log(f"  {r}")
else:
    log("\nNenhuma otimizacao necessaria com base nos dados atuais.")

log(f"\nPipeline v{ctx.cenas_count} cenas: {'COMPATIVEL' if has_cenas else 'INCOMPATIVEL'}")
log(f"Storyboard: {sb.get('cenas_count', '?')} cenas (camada: {sb.get('camada', '?')})")
log(f"Mídias: {qualidade.get('midias_green', '?')}/{qualidade.get('midias_total', '?')} green")
log(f"Cobertura: {cobertura.get('scene_coverage_rate', '?')}")
log(f"Queries por pool: {cobertura.get('query_pool_queries', '?')}")
log(f"Tempo storyboard: {performance.get('storyboard_local_ms', '?')}ms")
log(f"Tempo SceneContext: {performance.get('scene_context_load_ms', '?')}ms")