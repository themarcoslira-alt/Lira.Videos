# -*- coding: utf-8 -*-
"""Validação pontual das mudanças da Tab 2 (não-colhido pela suíte test_*)."""
import json
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import app_web
import services.api_v2 as api_v2
import services.deepseek_prompt_service as dsvc
import services.scene_plan_service as scene_plan_svc

FALHAS = []
def ok(nome, det=""):
    print(f"  [OK]   {nome} {det}".rstrip())
def fail(nome, det=""):
    FALHAS.append(nome)
    print(f"  [FAIL] {nome} :: {det}")

app = app_web.app
app.config["TESTING"] = True
client = app.test_client()

# --- Bug #2: SCENE_PLAN_MISSING quando lira_scene_plan.json não existe ---
print("\n[1] Bug #2 — validação scene_plan em /api/v2/prompts/<id>/gerar")
fake = app_web.PROJETOS_DIR / "zzz_tab2_validation_tmp"
if fake.exists():
    shutil.rmtree(fake, ignore_errors=True)
fake.mkdir(parents=True, exist_ok=True)
try:
    r = client.post(f"/api/v2/prompts/{fake.name}/gerar",
                    data=json.dumps({"estilo_visual": "photorealistic_cinematic"}),
                    content_type="application/json")
    body = r.get_json(silent=True) or {}
    cond = (r.status_code == 400 and body.get("code") == "SCENE_PLAN_MISSING"
            and "Plano de cenas não encontrado" in (body.get("error") or ""))
    (ok if cond else fail)("SCENE_PLAN_MISSING com mensagem clara",
                           f"status={r.status_code} body={body}")

    # Com scene_plan.json existente, a validação NÃO bloqueia (segue o fluxo)
    plan_ok = {"projeto": fake.name, "cenas": []}
    (fake / "lira_scene_plan.json").write_text(json.dumps(plan_ok), encoding="utf-8")
    (fake / "meta.json").write_text(json.dumps({"estilo_visual": "photorealistic_cinematic"}), encoding="utf-8")
    r2 = client.post(f"/api/v2/prompts/{fake.name}/gerar",
                     data=json.dumps({"estilo_visual": "photorealistic_cinematic"}),
                     content_type="application/json")
    body2 = r2.get_json(silent=True) or {}
    cond2 = r2.status_code != 400 or body2.get("code") != "SCENE_PLAN_MISSING"
    (ok if cond2 else fail)("validação não bloqueia quando plano existe",
                            f"status={r2.status_code} body={body2}")
finally:
    shutil.rmtree(fake, ignore_errors=True)

# --- Part 3/5: status_holder custo + eeat_enabled no pipeline (API mockada) ---
print("\n[2] Part 3/5 — status_holder custo + eeat_enabled propagado")
proj = "zzz_tab2_pipeline_tmp"
pdir = app_web.PROJETOS_DIR / proj
if pdir.exists():
    shutil.rmtree(pdir, ignore_errors=True)
pdir.mkdir(parents=True, exist_ok=True)
try:
    cenas = [
        {"id": 1, "scene_index": 1, "tempo_inicio": 0.0, "tempo_fim": 4.0,
         "timestamp": "00:00 - 00:04", "narration": "Cena um de teste.",
         "texto": "Cena um de teste."},
        {"id": 2, "scene_index": 2, "tempo_inicio": 4.0, "tempo_fim": 8.0,
         "timestamp": "00:04 - 00:08", "narration": "Cena dois de teste.",
         "texto": "Cena dois de teste."},
    ]
    scene_plan_svc.salvar_scene_plan(proj, {"cenas": cenas})
    fake_context = {
        "theme": "Teste", "style_lock": "Photorealistic cinematic still, 16:9",
        "negative_lock": "no text",
    }
    cenas_res = [
        {"scene_index": 1, "timestamp": "00:00 - 00:04", "visual_role": "hook",
         "scene_type": "broll_macro",
         "prompt_imagem": "Expert botanist in garden, natural light, 8k, wide shot",
         "prompt_animacao": "Slow cinematic push-in toward the bush, 4s smooth ease",
         "references": [], "continuity_notes": "Warm"},
        {"scene_index": 2, "timestamp": "00:04 - 00:08", "visual_role": "explanation",
         "scene_type": "avatar_talking",
         "prompt_imagem": "Presenter explaining in studio, soft key light, medium shot",
         "prompt_animacao": "Subtle pan right on the presenter, warm light, 4s smooth ease",
         "references": [], "continuity_notes": "Studio"},
    ]

    def mock_api_call(messages, *args, **kwargs):
        # 1ª chamada = análise global; demais = lotes (ambos com custo)
        content = json.dumps(fake_context if "scenes" not in str(messages) else {"scenes": cenas_res})
        return {"content": content,
                "usage": {"prompt_tokens": 100, "completion_tokens": 50,
                          "total_tokens": 150, "custo_estimado_usd": 0.0001},
                "model": "deepseek-chat", "tempo_resposta_s": 0.3}

    status = {}
    with patch.object(dsvc, "_chamar_deepseek_api", side_effect=mock_api_call):
        resultado = dsvc.executar_pipeline_prompt_intelligence(
            projeto_id=proj,
            estilo_id="photorealistic_cinematic",
            api_key="fake_key",
            status_holder=status,
            eeat_enabled=True,
        )
    ok("pipeline executou com sucesso",
       f"success={resultado.get('success')} total_cenas={resultado.get('total_cenas')}")
    if "custo_estimado_usd" in status:
        ok("status_holder contém custo_estimado_usd", f"= {status['custo_estimado_usd']}")
    else:
        fail("status_holder sem custo_estimado_usd", f"keys={list(status.keys())}")
    if status.get("total_cenas") == 2:
        ok("status_holder total_cenas = 2")
    else:
        fail("status_holder total_cenas errado", f"= {status.get('total_cenas')}")
    if status.get("eeat_enabled") is True and resultado.get("eeat_enabled") is True:
        ok("eeat_enabled propagado até o status/resultado")
    else:
        fail("eeat_enabled não propagado",
             f"status={status.get('eeat_enabled')} resultado={resultado.get('eeat_enabled')}")
    if resultado.get("custo_estimado_usd", 0) > 0:
        ok("resultado final com custo_estimado_usd", f"= {resultado['custo_estimado_usd']}")
    else:
        fail("resultado final sem custo_estimado_usd")
finally:
    shutil.rmtree(pdir, ignore_errors=True)

# --- Part 5: E-E-A-T injetado no system_prompt do gerar_prompts_lote ---
print("\n[3] Part 5 — E-E-A-T no system prompt do gerar_prompts_lote")
import inspect
src = inspect.getsource(dsvc.gerar_prompts_lote)
if "eeat_enabled" in src and "ESTRUTURA E-E-A-T" in src:
    ok("gerar_prompts_lote contém param eeat_enabled + bloco E-E-A-T")
else:
    fail("gerar_prompts_lote sem E-E-A-T")
if "status_holder" in inspect.getsource(dsvc.executar_pipeline_prompt_intelligence):
    ok("executar_pipeline_prompt_intelligence aceita status_holder/eeat_enabled")
else:
    fail("pipeline sem status_holder")

# --- API: v2_gerar_prompts_ia aceita eeat_enabled ---
print("\n[4] API — v2_gerar_prompts_ia lê eeat_enabled")
src2 = inspect.getsource(api_v2.v2_gerar_prompts_ia)
if 'data.get("eeat_enabled", False)' in src2:
    ok("v2_gerar_prompts_ia lê eeat_enabled do body")
else:
    fail("v2_gerar_prompts_ia não lê eeat_enabled")

print("\n" + "=" * 60)
if FALHAS:
    print(f"RESULTADO: {len(FALHAS)} falha(s): {FALHAS}")
    sys.exit(1)
print("RESULTADO: TODAS AS VERIFICAÇÕES PASSARAM")
