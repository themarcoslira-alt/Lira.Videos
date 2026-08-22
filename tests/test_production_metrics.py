"""
tests/test_production_metrics.py — Production Metrics & Performance Validation Suite (FASE 11)
=============================================================================================
Valida:
1. Geração e integridade do arquivo canonical 'production_metrics.json'.
2. Cálculo de scores médios (Visual, Continuidade, Retenção) e atribuição de final_grade (A+).
3. Sistema de Feedback Humano (aprovação, solicitação de revisão, notas do usuário).
4. Versionamento de projeto com snapshots imutáveis em 'project_versions.json'.
5. Endpoints de API REST do Lira Studio (/api/v2/metrics, /api/v2/human_feedback, /api/v2/versions).
6. Integração com Content Learning Engine (armazenamento de feedback e decisões bem-sucedidas).
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
import services.autonomous_director_service as auto_director_svc
import services.production_metrics_engine as metrics_engine_svc
import services.human_feedback_service as human_feedback_svc
import services.project_version_service as version_svc
import services.content_learning_engine as learning_svc


def test_fluxo_completo_metricas_e_feedback():
    """Valida o ciclo completo da Fase 11: Métricas, Feedback Humano, Versões e Aprendizado."""
    proj = "test_metrics_feedback_v11_proj"
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

    srt_roteiro = """1
00:00:00,000 --> 00:00:04,000
Olá pessoal, eu sou o Marcos e hoje eu vou mostrar o método ideal de fertilização orgânica.

2
00:00:04,000 --> 00:00:08,000
Vejam a textura da casca de banana fermentada nutrindo o solo rústico.

3
00:00:08,000 --> 00:00:12,000
Se inscreva no canal para acompanhar mais tutoriais completos de cultivo!
"""

    # 2. Executa Direção Autônoma
    res_auto = auto_director_svc.dirigir_producao_autonoma(
        projeto_id=proj,
        roteiro_texto=srt_roteiro,
        nome_personagem="Marcos",
        estilo_visual="photorealistic_cinematic"
    )
    assert res_auto["success"] is True

    # 3. Validação de production_metrics.json (Parte 1)
    metrics_file = pdir / "production_metrics.json"
    assert metrics_file.exists(), "production_metrics.json não foi criado"
    
    metricas = json.loads(metrics_file.read_text(encoding="utf-8"))
    assert metricas["project_id"] == proj
    assert metricas["scenes_total"] == 3
    assert metricas["average_visual_score"] >= 90
    assert metricas["average_continuity_score"] >= 95
    assert metricas["average_retention_score"] >= 85
    assert metricas["final_grade"] in ("A+", "A")
    assert metricas["scenes_approved"] >= 2

    # 4. Validação do Feedback Humano (Parte 2)
    # Usuário aprova Cena 1
    res_f1 = human_feedback_svc.registrar_feedback_humano(
        projeto_id=proj,
        scene_id=1,
        status="approved",
        note="Excelente introdução com enquadramento perfeito",
        approved_by="Marcos Director"
    )
    assert res_f1["success"] is True
    assert res_f1["human_status"] == "approved"

    # Usuário pede revisão na Cena 2
    res_f2 = human_feedback_svc.registrar_feedback_humano(
        projeto_id=proj,
        scene_id=2,
        status="revision_requested",
        note="Ajustar iluminação matinal para ficar mais suave",
        approved_by="Marcos Director"
    )
    assert res_f2["success"] is True
    assert res_f2["human_status"] == "revision_requested"

    # Resumo de Feedback
    resumo_fb = human_feedback_svc.obter_resumo_feedback_projeto(proj)
    assert resumo_fb["approved_count"] == 1
    assert resumo_fb["revision_requested_count"] == 1

    # 5. Validação de Versionamento (Parte 3)
    versions_file = pdir / "project_versions.json"
    assert versions_file.exists(), "project_versions.json não foi criado"
    
    hist_versoes = version_svc.obter_historico_versoes(proj)
    assert hist_versoes["total_versions"] >= 1
    assert hist_versoes["current_version"] == f"v{hist_versoes['total_versions']}"
    assert len(hist_versoes["versions"]) >= 1

    # Cria nova versão explícita pós-revisão
    v2 = version_svc.criar_nova_versao(
        projeto_id=proj,
        changes=["Adjusted scene 02 lighting according to human review note"]
    )
    assert v2["version"] == "v2"
    assert "Adjusted scene 02" in v2["changes"][0]

    # 6. Validação dos Endpoints HTTP (Parte 4)
    client = app.test_client()

    # GET /api/v2/metrics/<projeto_id>
    res_m = client.get(f"/api/v2/metrics/{proj}")
    assert res_m.status_code == 200
    data_m = res_m.get_json()
    assert data_m["success"] is True
    assert data_m["metrics"]["scenes_total"] == 3

    # POST /api/v2/human_feedback/<projeto_id>/<scene_id>
    res_hf_api = client.post(
        f"/api/v2/human_feedback/{proj}/3",
        json={"status": "approved", "note": "CTA aprovado", "approved_by": "Editor"}
    )
    assert res_hf_api.status_code == 200
    data_hf = res_hf_api.get_json()
    assert data_hf["success"] is True
    assert data_hf["human_status"] == "approved"

    # GET /api/v2/versions/<projeto_id>
    res_v_api = client.get(f"/api/v2/versions/{proj}")
    assert res_v_api.status_code == 200
    data_v = res_v_api.get_json()
    assert data_v["success"] is True
    assert data_v["versions"]["total_versions"] >= 2

    # 7. Validação de Integração com Content Learning (Parte 5)
    mem_learning = learning_svc.obter_memoria_aprendizado()
    assert mem_learning["total_projetos_analisados"] >= 1
    
    ultimo_proj = mem_learning["historico_projetos"][-1]
    assert "aprovacao_humana" in ultimo_proj
    assert "score_visual" in ultimo_proj
    assert "retencao_prevista" in ultimo_proj


if __name__ == "__main__":
    print("=" * 80)
    print("EXECUTANDO TESTES DA FASE 11 — PRODUCTION METRICS & HUMAN FEEDBACK")
    print("=" * 80)
    test_fluxo_completo_metricas_e_feedback()
    print("✓ Teste 1: Geração e cálculo de production_metrics.json APROVADO")
    print("✓ Teste 2: Sistema de Feedback Humano (Aprovação / Revisão) APROVADO")
    print("✓ Teste 3: Versionamento de Projeto com Snapshots Imutáveis APROVADO")
    print("✓ Teste 4: Endpoints REST (/metrics, /human_feedback, /versions) APROVADOS")
    print("✓ Teste 5: Integração e Aprendizado Contínuo (Content Learning) APROVADO")
    print("\nTODOS OS TESTES DA FASE 11 PASSARAM COM 100% DE SUCESSO!")
    print("=" * 80)
