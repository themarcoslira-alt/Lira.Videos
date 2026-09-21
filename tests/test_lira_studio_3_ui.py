"""
tests/test_lira_studio_3_ui.py — Lira Studio 3.0 Directorial Dashboard Suite
=============================================================================
Valida:
1. Endpoint /api/v2/diretor3/<projeto_id> retorna o resumo executivo.
2. Presença das 5 abas principais e ausência das abas removidas 6 (diretor3) e 7 (brain-director).
3. Presença do indicador e botão de recálculo do Brain na Montagem.
4. Idempotência do Brain System Director (a segunda chamada não re-executa).
5. Endpoint de status do Brain (/api/v2/producao/<id>/brain_status).
6. Falha do Brain sem bloquear o pipeline.
"""

import os
import sys
import json
import shutil
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.stdout.reconfigure(encoding="utf-8")

from app_web import app
import services.character_service as character_svc
import services.scene_plan_service as scene_plan_svc
from services.api_v2 import (
    executar_brain_system_director,
    disparar_brain_background,
    obter_status_brain,
    _BRAIN_STATUS,
    _BRAIN_STATUS_LOCK
)


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
    """Valida a presença das 4 abas canônicas e a ausência das abas descontinuadas/consolidadas."""
    html_path = ROOT_DIR / "static" / "index.html"
    assert html_path.exists()
    content = html_path.read_text(encoding="utf-8")

    # 1. As 4 abas canônicas devem estar presentes na navegação
    assert 'data-s2-tab="studio"' in content, "Aba 1 (studio) deve existir"
    assert 'data-s2-tab="brain"' in content, "Aba 2 (brain) deve existir"
    assert 'data-s2-tab="producao"' in content, "Aba 3 (producao) deve existir"
    assert 'data-s2-tab="montagem"' in content, "Aba 4 (montagem) deve existir"

    # 2. As abas descontinuadas/consolidadas devem estar AUSENTES da navbar e do DOM
    for tab_removida in ("prompts", "flow", "exportacao", "diretor3", "brain-director"):
        assert f'data-s2-tab="{tab_removida}"' not in content, f"Aba {tab_removida} não deve existir na navbar"
        assert f'id="s2-tab-{tab_removida}"' not in content, f"Painel s2-tab-{tab_removida} não deve existir no DOM"

    # 3. Indicador de status do Brain e botão de recálculo devem estar presentes na Montagem
    assert 'id="s2-brain-status-badge"' in content, "Badge de status do Brain deve estar presente na Montagem"
    assert 'id="btn-recalcular-brain"' in content, "Botão Recalcular Brain deve estar presente na Montagem"


def test_endpoint_brain_status():
    """Valida o endpoint GET /api/v2/producao/<id>/brain_status."""
    proj = "test_ui_diretor3_proj"
    client = app.test_client()

    res = client.get(f"/api/v2/producao/{proj}/brain_status")
    assert res.status_code == 200
    data = res.get_json()

    assert data["success"] is True
    assert data["projeto_id"] == proj
    assert data["status"] in ("pendente", "rodando", "ok", "falhou")
    assert "total_cenas" in data
    assert "total_transicoes" in data
    assert "total_story_cards" in data
    assert "total_sfx" in data


def test_idempotencia_brain():
    """Valida que a segunda chamada com os mesmos insumos pula a re-execução."""
    proj = "test_ui_diretor3_proj"
    pdir = ROOT_DIR / "projetos" / proj

    # Execução 1
    res1 = executar_brain_system_director(proj, force=False)
    assert res1["success"] is True
    assert (pdir / "brain_director_output.json").exists()

    # Execução 2 com mesmos insumos
    res2 = executar_brain_system_director(proj, force=False)
    assert res2["success"] is True
    assert res2.get("pulou") is True, "A segunda execução deve ter pulado (idempotente)"
    assert res2.get("motivo") == "idempotente"
    assert res2.get("hash") == res1.get("hash")


def test_falha_brain_nao_bloqueia_pipeline():
    """Valida que uma falha na execução do Brain não quebra o pipeline e registra status 'falhou'."""
    proj_invalido = "projeto_que_nao_existe_xyz_999"

    # Execução em background não levanta exceção e registra falhou
    disparar_brain_background(proj_invalido)
    time.sleep(0.5)

    st = obter_status_brain(proj_invalido)
    assert st["status"] == "falhou", f"Esperado status 'falhou', obtido: {st['status']}"
    assert "Erro no Brain" in st["mensagem"] or "não encontrado" in st["mensagem"]


if __name__ == "__main__":
    print("=" * 80)
    print("EXECUTANDO TESTES ATUALIZADOS — LIRA STUDIO 3.0 UI & BRAIN INTEGRATION")
    print("=" * 80)
    test_endpoint_diretor3_ui()
    print("✓ Teste 1: Endpoint /api/v2/diretor3/<projeto_id> APROVADO")
    test_elementos_interface_html()
    print("✓ Teste 2: Elementos e 5 abas principais no index.html APROVADOS")
    test_endpoint_brain_status()
    print("✓ Teste 3: Endpoint /api/v2/producao/<id>/brain_status APROVADO")
    test_idempotencia_brain()
    print("✓ Teste 4: Idempotência do Brain Director APROVADO")
    test_falha_brain_nao_bloqueia_pipeline()
    print("✓ Teste 5: Resiliência a falhas do Brain Director APROVADO")
    print("\nTODOS OS TESTES PASSARAM COM SUCESSO!")
    print("=" * 80)
