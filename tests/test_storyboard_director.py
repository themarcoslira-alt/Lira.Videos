"""
tests/test_storyboard_director.py — Automated Test Suite for Storyboard Director AI (FASE 4.1)
=============================================================================================
Valida:
1. Roteiro estruturado (começo, meio e fim) gera storyboard narrativo coerente.
2. Hook recebe prioridade máxima (retention_goal: 'very_high' e story_role: 'hook').
3. Cena com apresentador recebe story_role e narrative_purpose precisos.
4. Cenas B-roll recebem papéis narrativos (problem, process, proof) e conexões inter-cenas.
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
import services.storyboard_director_service as storyboard_director_svc


def test_storyboard_director_narrativa_completa():
    """Valida geração de storyboard narrativo estruturado de ponta a ponta."""
    proj = "test_storyboard_director_proj"
    pdir = ROOT_DIR / "projetos" / proj
    if pdir.exists():
        shutil.rmtree(pdir)

    # 1. Configura Identidade com @Marcos
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
        flow_char_id="flow-marcos-id-41"
    )

    # 2. Roteiro com 6 cenas cobrindo arco dramático
    srt_script = """1
00:00:00,000 --> 00:00:03,500
Olá pessoal, eu sou o Marcos e hoje eu vou revelar o maior erro no cultivo de orquídeas.

2
00:00:03,500 --> 00:00:07,000
Observem essas folhas amareladas e danificadas por falta de nutrientes.

3
00:00:07,000 --> 00:00:10,500
Aqui eu mostro na prática como aplicar o adubo orgânico de casca de banana fermentada.

4
00:00:10,500 --> 00:00:14,000
Comece aplicando a adubação ao redor da borda do vaso com cuidado.

5
00:00:14,000 --> 00:00:17,500
Olha a diferença e o resultado da planta florescendo com vitalidade.

6
00:00:17,500 --> 00:00:21,000
Se inscreva no canal para acompanhar mais tutoriais completos!
"""

    client = app.test_client()
    res = client.post(f"/api/v2/transcricao/{proj}/usar_srt", json={"srt_texto": srt_script})
    assert res.status_code == 200

    plan = scene_plan_svc.carregar_scene_plan(proj)
    assert plan is not None
    cenas = plan["cenas"]
    assert len(cenas) == 6

    # Teste 1: Hook recebe story_role='hook' e retention_goal='very_high'
    c1 = cenas[0]
    assert c1["story_role"] == "hook"
    assert c1["retention_goal"] == "very_high"
    assert "curiosity" in c1["narrative_purpose"].lower() or "magnetic" in c1["narrative_purpose"].lower()
    assert c1["previous_scene_connection"]
    assert c1["next_scene_connection"]

    # Teste 2: Cena de problema recebe story_role='problem' e retenção alta
    c2 = cenas[1]
    assert c2["story_role"] == "problem"
    assert c2["retention_goal"] in ("high", "very_high")
    assert "pain point" in c2["narrative_purpose"].lower() or "problem" in c2["narrative_purpose"].lower()

    # Teste 3: Demonstração com apresentador recebe story_role='demonstration'
    c3 = cenas[2]
    assert c3["story_role"] == "demonstration"
    assert c3["uses_character"] is True
    assert c3["character_ref"] == "@Marcos"

    # Teste 4: Processo prático recebe story_role='process'
    c4 = cenas[3]
    assert c4["story_role"] == "process"
    assert "step" in c4["narrative_purpose"].lower() or "execution" in c4["narrative_purpose"].lower()

    # Teste 5: Resultado e prova
    c5 = cenas[4]
    assert c5["story_role"] in ("proof", "result", "comparison")
    assert c5["retention_goal"] in ("high", "very_high")

    # Teste 6: CTA final
    c6 = cenas[5]
    assert c6["story_role"] == "cta"
    assert "action" in c6["narrative_purpose"].lower() or "subscription" in c6["narrative_purpose"].lower()

    # Teste de conexões narrativas inter-cenas
    for i, c in enumerate(cenas):
        assert c["previous_scene_connection"], f"Cena {i+1} sem previous_scene_connection"
        assert c["next_scene_connection"], f"Cena {i+1} sem next_scene_connection"


if __name__ == "__main__":
    print("=" * 80)
    print("EXECUTANDO TESTES DA FASE 4.1 — STORYBOARD DIRECTOR AI")
    print("=" * 80)
    test_storyboard_director_narrativa_completa()
    print("✓ Teste 1: Arco narrativo completo (Hook -> Problem -> Demo -> Process -> Result -> CTA) APROVADO")
    print("✓ Teste 2: Prioridade de retenção e propósito narrativo APROVADOS")
    print("✓ Teste 3: Conexões prévias e futuras inter-cenas APROVADAS")
    print("\nTODOS OS TESTES DO STORYBOARD DIRECTOR AI PASSARAM COM SUCESSO!")
    print("=" * 80)
