"""
tests/test_lira_studio_3_ui.py — Lira Studio 3.0 Directorial Dashboard Suite (FASE 9)
=====================================================================================
Valida:
1. Endpoint /api/v2/diretor3/<projeto_id> retorna o resumo executivo do Diretor 3.0.
2. Integração dos dados de Memória Visual, Personagem, Retenção e Aprendizado.
3. Presença dos elementos visuais da aba DIRETOR 3.0 no frontend HTML/JS.
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


def test_endpoint_diretor3_ui():
    """Valida o endpoint da API do Diretor 3.0."""
    proj = "test_ui_diretor3_proj"
    pdir = ROOT_DIR / "projetos" / proj
    if pdir.exists():
        shutil.rmtree(pdir)

    # 1. Configura Identidade
    character_svc.salvar_identidade_projeto(
        projeto_id=proj,
        tipo="personagem",
        nome="Marcos",
        referencia_flow="@Marcos",
        visual_style="photorealistic_cinematic"
    )

    srt_script = """1
00:00:00,000 --> 00:00:04,000
Olá eu sou o Marcos mostrando as técnicas de adubação.
"""

    client = app.test_client()
    res_srt = client.post(f"/api/v2/transcricao/{proj}/usar_srt", json={"srt_texto": srt_script})
    assert res_srt.status_code == 200

    # 2. Testa endpoint /api/v2/diretor3/<projeto_id>
    res_d3 = client.get(f"/api/v2/diretor3/{proj}")
    assert res_d3.status_code == 200
    data = res_d3.get_json()

    assert data["success"] is True
    assert data["projeto_id"] == proj
    assert "visual_memory" in data
    assert "visual_context" in data
    assert "summary" in data

    s = data["summary"]
    assert s["total_cenas"] == 1
    assert s["avatar_cenas"] == 1
    assert s["project_retention_score"] >= 90
    assert s["character_ref"] == "@Marcos"


def test_elementos_interface_html():
    """Valida a presença do botão e do painel DIRETOR 3.0 no index.html."""
    html_path = ROOT_DIR / "static" / "index.html"
    assert html_path.exists()
    content = html_path.read_text(encoding="utf-8")

    assert 'data-s2-tab="diretor3"' in content
    assert 'id="s2-tab-diretor3"' in content
    assert 'id="d3-retention-score"' in content
    assert 'id="d3-bible-world"' in content
    assert 'id="d3-cenas-timeline"' in content


if __name__ == "__main__":
    print("=" * 80)
    print("EXECUTANDO TESTES DA FASE 9 — LIRA STUDIO 3.0 UI")
    print("=" * 80)
    test_endpoint_diretor3_ui()
    print("✓ Teste 1: Endpoint /api/v2/diretor3/<projeto_id> APROVADO")
    test_elementos_interface_html()
    print("✓ Teste 2: Elementos e painéis do Diretor 3.0 no index.html APROVADOS")
    print("\nTODOS OS TESTES DA FASE 9 PASSARAM COM SUCESSO!")
    print("=" * 80)
