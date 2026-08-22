"""
tests/test_fase43_validacao_real.py — End-to-End Validation Suite (FASE 4.3)
=============================================================================
Executa o teste completo de integração do fluxo real:
SRT
  ↓
Visual Director AI
  ↓
Visual Memory Engine
  ↓
Storyboard Director AI
  ↓
Scene Plan
  ↓
Prompt Builder AI
  ↓
Prompt History System
  ↓
Geração / Download Mídia
  ↓
Visual Judgment Engine
  ↓
Storyboard
"""

import os
import sys
import io
import json
import shutil
import numpy as np
from PIL import Image
from pathlib import Path

# Adiciona raiz do ultracut3 ao path
ROOT_DIR = Path(r"C:\ultracut3")
sys.path.insert(0, str(ROOT_DIR))
sys.stdout.reconfigure(encoding="utf-8")

from app_web import app
import services.character_service as character_svc
import services.scene_plan_service as scene_plan_svc
import services.visual_memory_engine as vme_svc
import services.prompt_history_service as prompt_history_svc


def test_fluxo_completo_fase43():
    """Valida o pipeline completo de ponta a ponta."""
    proj = "projeto_marcos_fase43_real"
    pdir = ROOT_DIR / "projetos" / proj
    if pdir.exists():
        shutil.rmtree(pdir)

    print("\n--- 1. CONFIGURANDO PERSONAGEM OFICIAL FLOW (@Marcos) ---")
    character_svc.salvar_identidade_projeto(
        projeto_id=proj,
        tipo="personagem",
        nome="Marcos",
        referencia_flow="@Marcos",
        visual_style="photorealistic_cinematic"
    )
    character_svc.atualizar_status_flow_personagem(
        projeto_id=proj,
        created=True,
        flow_char_name="@Marcos",
        flow_char_id="flow-marcos-fase43"
    )
    print("✓ Personagem @Marcos configurado e vinculado no Flow.")

    print("\n--- 2. INGESTÃO DE SRT REAL ---")
    srt_script = """1
00:00:00,000 --> 00:00:04,000
Olá pessoal, eu sou o Marcos e hoje eu vou revelar como recuperei minhas orquídeas usando adubo orgânico.

2
00:00:04,000 --> 00:00:08,000
Vejam a textura da casca de banana fermentada no solo úmido e como ela enriquece as raízes.

3
00:00:08,000 --> 00:00:12,000
Se inscreva no canal para não perder nenhuma dica de jardinagem sustentável!
"""

    client = app.test_client()
    res = client.post(f"/api/v2/transcricao/{proj}/usar_srt", json={"srt_texto": srt_script})
    assert res.status_code == 200
    print("✓ SRT ingerido e planejado com sucesso.")

    print("\n--- 3. VALIDAÇÃO DO VISUAL DIRECTOR & VISUAL MEMORY ---")
    p_ctx = pdir / "project_visual_context.json"
    p_mem = pdir / "project_visual_memory.json"
    assert p_ctx.exists(), "project_visual_context.json não foi criado"
    assert p_mem.exists(), "project_visual_memory.json não foi criado"
    
    mem_data = json.loads(p_mem.read_text(encoding="utf-8"))
    assert mem_data["personagem"]["name"] == "Marcos"
    assert mem_data["personagem"]["reference"] == "@Marcos"
    print("✓ Contexto Visual e Bíblia Visual persistidos com consistência.")

    print("\n--- 4. VALIDAÇÃO DO STORYBOARD DIRECTOR & SCENE PLAN ---")
    plan = scene_plan_svc.carregar_scene_plan(proj)
    assert plan is not None
    cenas = plan["cenas"]
    assert len(cenas) == 3

    # Cena 1: Hook com @Marcos
    assert cenas[0]["story_role"] == "hook"
    assert cenas[0]["retention_goal"] == "very_high"
    assert cenas[0]["uses_character"] is True
    assert cenas[0]["character_ref"] == "@Marcos"
    assert "@Marcos" in cenas[0]["prompt_imagem"]
    assert "@Homem" not in cenas[0]["prompt_imagem"]

    # Cena 2: B-Roll Macro
    assert cenas[1]["uses_character"] is False
    assert cenas[1]["character_ref"] == ""
    assert "@Marcos" not in cenas[1]["prompt_imagem"]
    assert "banana" in cenas[1]["prompt_imagem"].lower() or "compost" in cenas[1]["prompt_imagem"].lower()

    # Cena 3: CTA com @Marcos
    assert cenas[2]["story_role"] == "cta"
    assert cenas[2]["uses_character"] is True
    print("✓ Storyboard Director e Scene Plan 100% validados.")

    print("\n--- 5. VALIDAÇÃO DO PROMPT HISTORY SYSTEM ---")
    hist_dir = pdir / "prompt_history"
    assert hist_dir.exists()
    assert (hist_dir / "scene_001.txt").exists()
    assert (hist_dir / "scene_002.txt").exists()
    assert (hist_dir / "scene_003.txt").exists()
    print("✓ Arquivos de histórico scene_001.txt a scene_003.txt criados com sucesso.")

    print("\n--- 6. SIMULAÇÃO DE DOWNLOAD DE MÍDIA & VISUAL JUDGMENT ENGINE ---")
    # Cria bytes válidos de imagem > 1KB
    arr = np.random.randint(0, 255, (256, 256, 3), dtype=np.uint8)
    im = Image.fromarray(arr)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    valid_bytes = buf.getvalue()

    # Salva mídia estruturada para a Cena 001
    res_salva = scene_plan_svc.salvar_midia_cena_estruturada(
        projeto_id=proj,
        cid=1,
        ts_ini=0.0,
        ts_fim=4.0,
        prompt_texto=cenas[0]["prompt_imagem"],
        midia_bytes=valid_bytes,
        is_video=False,
        personagem_ref="@Marcos"
    )
    assert res_salva["success"] is True

    # Verifica arquivo físico salvo
    p_img = pdir / "cenas" / "001.png"
    assert p_img.exists()
    assert p_img.stat().st_size > 1000

    # Verifica julgamento visual na cena atualizada
    plan_atualizado = scene_plan_svc.carregar_scene_plan(proj)
    c1_atualizada = plan_atualizado["cenas"][0]
    assert c1_atualizada["visual_score"] >= 80
    assert c1_atualizada["judgment_status"] == "approved"
    assert c1_atualizada["image_status"] == "READY"
    assert c1_atualizada["status"] == "BAIXADA"
    print("✓ Imagem 001.png salva, validada e julgada como 'approved' (Score >= 80).")

    print("\n--- 7. VALIDAÇÃO DO STORYBOARD.JSON & PROMPT HISTORY RESULT ---")
    p_sb = pdir / "storyboard.json"
    assert p_sb.exists()
    sb_data = json.loads(p_sb.read_text(encoding="utf-8"))
    assert len(sb_data.get("cenas", [])) >= 1
    assert sb_data["cenas"][0]["arquivo"] == "001.png"

    # Verifica atualização no arquivo de histórico scene_001.txt
    txt_hist1 = (hist_dir / "scene_001.txt").read_text(encoding="utf-8")
    assert "001.png" in txt_hist1
    assert "judgment_status: approved" in txt_hist1
    print("✓ storyboard.json e bloco RESULT em scene_001.txt atualizados com sucesso.")


if __name__ == "__main__":
    print("=" * 80)
    print("EXECUTANDO FASE 4.3 — TESTE COMPLETO DE INTEGRAÇÃO REAL")
    print("=" * 80)
    test_fluxo_completo_fase43()
    print("\n" + "=" * 80)
    print("FASE 4.3 CONCLUÍDA COM 100% DE SUCESSO!")
    print("=" * 80)
