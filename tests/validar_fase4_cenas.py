# -*- coding: utf-8 -*-
"""FASE 4 — Validar sincronização automática e integridade (não-colhido pela suíte).

Rodar: python tests/validar_fase4_cenas.py
"""
import json
import random
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

PROJ = "Boy"
PDIR = BASE / "projetos" / PROJ
PLAN = PDIR / "lira_scene_plan.json"
MIDIAS = PDIR / "midias_encontradas.json"
CENAS_DIR = PDIR / "cenas"

import services.scene_plan_service as svc

RESULTADOS = []

def registrar(nome, ok, detalhe=""):
    RESULTADOS.append((nome, ok, detalhe))
    print(f"  [{'OK' if ok else 'FALHA'}] {nome} {detalhe}".rstrip())

def em_cenas(p) -> bool:
    try:
        return "cenas" in Path(p).parts
    except Exception:
        return False

# Backup do estado real (restaurado no fim)
_bak_plan = PLAN.read_text(encoding="utf-8") if PLAN.exists() else None
_bak_midias = MIDIAS.read_text(encoding="utf-8") if MIDIAS.exists() else None

print("=" * 70)
print("TESTE 1 — INTEGRIDADE (92 cenas: arquivo_midia existe em cenas/?)")
print("=" * 70)
try:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    cenas = plan.get("cenas", [])
    registrar("plano carregado", len(cenas) == 92, f"({len(cenas)} cenas)")
    n_cenas_ok = n_legado_tem_cenas = n_legado_sem_cenas = n_inexistente = 0
    discrep = []
    for c in cenas:
        cid = int(c.get("id") or c.get("scene_index") or 0)
        arq = str(c.get("arquivo_midia") or "")
        if not arq:
            n_inexistente += 1
            discrep.append((cid, "sem arquivo_midia"))
            continue
        existe = Path(arq).exists()
        if em_cenas(arq):
            if existe:
                n_cenas_ok += 1
            else:
                n_inexistente += 1
                discrep.append((cid, f"aponta p/ cenas/ mas não existe: {arq}"))
            continue
        r = svc.resolver_arquivo_cena(PROJ, cid)
        if not existe:
            n_inexistente += 1
            discrep.append((cid, f"arquivo_midia não existe: {arq}"))
        elif r and em_cenas(r):
            n_legado_tem_cenas += 1
        else:
            n_legado_sem_cenas += 1
            discrep.append((cid, f"legado sem equivalente em cenas/: {arq}"))
    print(f"  -> em cenas/ (registrado+existe): {n_cenas_ok}")
    print(f"  -> legado, MAS com equivalente em cenas/: {n_legado_tem_cenas}")
    print(f"  -> legado SEM equivalente em cenas/: {n_legado_sem_cenas}")
    print(f"  -> inexistente/sem registro: {n_inexistente}")
    if discrep:
        print("  -> Discrepâncias (primeiras 10):")
        for cid, msg in discrep[:10]:
            print(f"       cena {cid}: {msg}")
    registrar("TESTE 1 integridade",
              (n_cenas_ok + n_legado_tem_cenas) == len(cenas) and n_inexistente == 0,
              f"(em cenas/: {n_cenas_ok}, legado c/ cenas/: {n_legado_tem_cenas})")
except Exception as e:
    registrar("TESTE 1 integridade", False, f"exceção: {e}")

print()
print("=" * 70)
print("TESTE 2 — SINCRONIZAÇÃO (novo vídeo cenas/01_novo.mp4)")
print("=" * 70)
teste2_novo = CENAS_DIR / "01_novo.mp4"
try:
    fonte = next((PDIR / "videos").glob("*.mp4"), None) if (PDIR / "videos").exists() else None
    if not fonte:
        fonte = next(CENAS_DIR.glob("*.mp4"), None)
    assert fonte, "sem .mp4 de origem para o teste"
    shutil.copy2(fonte, teste2_novo)
    print(f"  -> criado: {teste2_novo.name} ({teste2_novo.stat().st_size} bytes)")

    sinc = svc.sincronizar_midias_encontradas(PROJ, force=True)
    print(f"  -> sincronizar_midias_encontradas() -> {sinc} cenas")

    plan2 = json.loads(PLAN.read_text(encoding="utf-8"))
    c1 = next(c for c in plan2["cenas"] if int(c.get("id", 0)) == 1)
    midia_c1 = str(c1.get("arquivo_midia") or "")
    adotou = (midia_c1 == str(teste2_novo))
    print(f"  -> cena 1 arquivo_midia agora: {midia_c1}")
    registrar("TESTE 2 sync adotou cenas/01_novo.mp4", adotou,
              "" if adotou else
              "(plano manteve a mídia já registrada — o resolvedor prioriza o arquivo_midia "
              "existente em disco; um arquivo NOVO em cenas/ só é adotado se a cena não "
              "tiver mídia válida registrada. NÃO bloqueia a limpeza.)")
finally:
    if teste2_novo.exists():
        teste2_novo.unlink()

print()
print("=" * 70)
print("TESTE 3 — RESOLUÇÃO (5 cenas aleatórias -> caminho em cenas/?)")
print("=" * 70)
try:
    random.seed(42)
    amostra = sorted(random.sample(range(1, 93), 5))
    ok_res = 0
    for cid in amostra:
        r = svc.resolver_arquivo_cena(PROJ, cid)
        caminho = str(r) if r else "None"
        ok = bool(r and r.exists())
        em_c = em_cenas(r) if r else False
        print(f"  cena {cid:3d} -> {caminho}  [existe={ok}, em_cenas={em_c}]")
        if ok:
            ok_res += 1
    registrar("TESTE 3 resolução (5 cenas resolvem arquivo existente)", ok_res == 5,
              f"({ok_res}/5 resolvem; caminhos podem ser legados quando cenas/ "
              f"não tem o arquivo e o plano aponta p/ legado)")
except Exception as e:
    registrar("TESTE 3 resolução", False, f"exceção: {e}")

print()
print("=" * 70)
print("TESTE 4 — COMPATIBILIDADE LEGADA (mídia só em imagens/ e videos/)")
print("=" * 70)
proj_tmp = "zzz_fase4_legado_tmp"
pdir_tmp = BASE / "projetos" / proj_tmp
try:
    shutil.rmtree(pdir_tmp, ignore_errors=True)
    (pdir_tmp / "imagens").mkdir(parents=True)
    (pdir_tmp / "videos").mkdir(parents=True)
    png_legado = pdir_tmp / "imagens" / "003_teste.png"
    mp4_legado = pdir_tmp / "videos" / "004_teste.mp4"
    png_legado.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 900)
    mp4_legado.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"y" * 900)
    plan_legado = {
        "projeto": proj_tmp,
        "cenas": [
            {"id": 3, "scene_index": 3, "tempo_inicio": 8.0, "tempo_fim": 12.0,
             "texto": "cena legado imagem"},
            {"id": 4, "scene_index": 4, "tempo_inicio": 12.0, "tempo_fim": 16.0,
             "texto": "cena legado video",
             "video_status": "READY", "tipo": "video"},
        ],
    }
    svc.salvar_scene_plan(proj_tmp, plan_legado)

    r_img = svc.resolver_arquivo_cena(proj_tmp, 3)
    print(f"  imagem legada -> {r_img}")
    ok_img = bool(r_img and r_img.exists() and not em_cenas(r_img)
                  and "imagens" in r_img.parts)
    registrar("TESTE 4a legado imagens/ (fallback encontra)", ok_img)

    r_vid = svc.resolver_arquivo_cena(proj_tmp, 4)
    print(f"  vídeo legado  -> {r_vid}")
    ok_vid = bool(r_vid and r_vid.exists() and not em_cenas(r_vid)
                  and "videos" in r_vid.parts and r_vid.suffix == ".mp4")
    registrar("TESTE 4b legado videos/ (fallback encontra)", ok_vid)

    cenas_tmp = pdir_tmp / "cenas"
    cenas_tmp.mkdir(exist_ok=True)
    (cenas_tmp / "3.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"z" * 900)
    r_prior = svc.resolver_arquivo_cena(proj_tmp, 3)
    ok_prior = bool(r_prior and em_cenas(r_prior))
    print(f"  prioridade cenas/ -> {r_prior}")
    registrar("TESTE 4c cenas/ tem prioridade sobre legado", ok_prior)
except Exception as e:
    registrar("TESTE 4 legado", False, f"exceção: {e}")
finally:
    shutil.rmtree(pdir_tmp, ignore_errors=True)

# Restaura estado real do Boy
if _bak_plan is not None:
    PLAN.write_text(_bak_plan, encoding="utf-8")
if _bak_midias is not None:
    MIDIAS.write_text(_bak_midias, encoding="utf-8")

print()
print("=" * 70)
falhas = [(n, d) for n, ok, d in RESULTADOS if not ok]
for nome, ok, det in RESULTADOS:
    print(f"  [{'PASS' if ok else 'FAIL'}] {nome}")
print("=" * 70)
if falhas:
    print(f"RESULTADO: {len(falhas)} falha(s): {[n for n, _ in falhas]}")
    sys.exit(1)
print("RESULTADO: TODOS OS TESTES PASSARAM — limpeza de imagens/ + videos/ APROVADA")

