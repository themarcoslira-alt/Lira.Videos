"""
tests/test_prompt_history.py — Automated Test Suite for Prompt History System (FASE 4.2)
========================================================================================
Valida:
1. Arquivos scene_XXX.txt são criados automaticamente na pasta prompt_history.
2. Cada arquivo contém todas as seções canônicas de decisão:
   - CENA
   - STORY ROLE
   - SCENE TYPE
   - CHARACTER
   - EMOTION
   - CAMERA
   - CONTINUITY
   - VISUAL DECISION
   - PROMPT GENERATED
   - RESULT
3. O bloco RESULT é atualizado quando a imagem é gravada e julgada pelo Visual Judgment Engine.
"""

import os
import sys
import json
import shutil
from pathlib import Path

# Adiciona raiz do ultracut3 ao path
ROOT_DIR = Path(r"C:\ultracut3")
sys.path.insert(0, str(ROOT_DIR))
sys.stdout.reconfigure(encoding="utf-8")

from app_web import app
import services.character_service as character_svc
import services.scene_plan_service as scene_plan_svc
import services.prompt_history_service as prompt_history_svc


def test_prompt_history_criacao_e_estrutura():
    """Valida criação automática e estrutura de scene_XXX.txt."""
    proj = "test_prompt_history_proj"
    pdir = ROOT_DIR / "projetos" / proj
    if pdir.exists():
        shutil.rmtree(pdir)

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
        flow_char_id="flow-marcos-42"
    )

    srt_script = """1
00:00:00,000 --> 00:00:04,000
Olá pessoal, eu sou o Marcos e hoje eu vou mostrar meu jardim de orquídeas.

2
00:00:04,000 --> 00:00:08,000
Vejam a textura da casca de banana fermentada no solo úmido.
"""

    client = app.test_client()
    res = client.post(f"/api/v2/transcricao/{proj}/usar_srt", json={"srt_texto": srt_script})
    assert res.status_code == 200

    plan = scene_plan_svc.carregar_scene_plan(proj)
    cenas = plan["cenas"]
    assert len(cenas) == 2

    # Verifica se os arquivos de histórico foram criados no disco
    hist_dir = pdir / "prompt_history"
    assert hist_dir.exists()
    
    file_s1 = hist_dir / "scene_001.txt"
    file_s2 = hist_dir / "scene_002.txt"
    assert file_s1.exists()
    assert file_s2.exists()

    # Valida conteúdo de scene_001.txt
    txt_1 = file_s1.read_text(encoding="utf-8")
    assert "CENA:" in txt_1
    assert "001" in txt_1
    assert "STORY ROLE:" in txt_1
    assert "SCENE TYPE:" in txt_1
    assert "CHARACTER:" in txt_1
    assert "@Marcos" in txt_1
    assert "EMOTION:" in txt_1
    assert "CAMERA:" in txt_1
    assert "CONTINUITY:" in txt_1
    assert "VISUAL DECISION:" in txt_1
    assert "Why this scene exists:" in txt_1
    assert "PROMPT GENERATED:" in txt_1
    assert "RESULT:" in txt_1

    # Valida metadados na cena do scene_plan
    assert cenas[0]["decision_logged"] is True
    assert cenas[0]["prompt_history_path"] == "prompt_history/scene_001.txt"


def test_prompt_history_atualizacao_resultado():
    """Valida atualização do bloco RESULT após download e julgamento da mídia."""
    import io
    from PIL import Image

    proj = "test_prompt_history_proj"
    pdir = ROOT_DIR / "projetos" / proj
    hist_dir = pdir / "prompt_history"
    file_s1 = hist_dir / "scene_001.txt"
    assert file_s1.exists()

    # Gera imagem PNG válida de teste (> 1024 bytes)
    import numpy as np
    arr = np.random.randint(0, 255, (256, 256, 3), dtype=np.uint8)
    im = Image.fromarray(arr)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    valid_png_bytes = buf.getvalue()

    res_salva = scene_plan_svc.salvar_midia_cena_estruturada(
        projeto_id=proj,
        cid=1,
        ts_ini=0.0,
        ts_fim=4.0,
        prompt_texto="@Marcos speaking in the rustic garden. 35mm lens.",
        midia_bytes=valid_png_bytes,
        is_video=False,
        personagem_ref="@Marcos"
    )
    assert res_salva["success"] is True

    # Lê novamente o arquivo scene_001.txt
    txt_updated = file_s1.read_text(encoding="utf-8")
    assert "image_path:" in txt_updated
    assert "001.png" in txt_updated
    assert "visual_score: 95" in txt_updated or "visual_score: 100" in txt_updated or "visual_score:" in txt_updated
    assert "judgment_status: approved" in txt_updated


if __name__ == "__main__":
    print("=" * 80)
    print("EXECUTANDO TESTES DA FASE 4.2 — PROMPT HISTORY SYSTEM")
    print("=" * 80)
    test_prompt_history_criacao_e_estrutura()
    print("✓ Teste 1: Criação de scene_XXX.txt e todas as seções canônicas APROVADO")
    test_prompt_history_atualizacao_resultado()
    print("✓ Teste 2: Atualização do bloco RESULT pós-julgamento visual APROVADO")
    print("\nTODOS OS TESTES DO PROMPT HISTORY SYSTEM PASSARAM COM SUCESSO!")
    print("=" * 80)
