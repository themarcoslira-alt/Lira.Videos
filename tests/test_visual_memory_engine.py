"""
tests/test_visual_memory_engine.py — Automated Test Suite for FASE 4.0
======================================================================
Valida:
1. Personagem @Marcos permanece consistente com a Bíblia Visual.
2. Ambiente (World) permanece consistente entre cenas.
3. Objeto principal (adubo orgânico) não é substituído indevidamente.
4. Score visual e checks são gerados com precisão.
5. Imagens ou cenas inconsistentes são identificadas com score reduzido.
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
import services.visual_memory_engine as vme_svc
import services.continuity_checker_service as continuity_checker_svc
import services.visual_judgment_service as visual_judgment_svc


def test_1_personagem_marcos_consistente():
    """Valida que @Marcos e suas regras de vestimenta são persistidos na Bíblia Visual."""
    proj = "projeto_marcos"
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
        flow_char_id="baf62874-6ec5-4cf3-bec0-1ae1e83286fc"
    )

    ident = character_svc.obter_identidade_projeto(proj)
    ctx = {
        "world": "Lush botanical rustic garden with green foliage, rich dark soil and natural daylight",
        "visual_style": "photorealistic_cinematic",
        "recurring_objects": ["organic banana peel compost fertilizer", "orchid flowers", "dark soil"]
    }

    bible = vme_svc.construir_memoria_visual_projeto(proj, ctx, ident)
    assert bible["personagem"]["name"] == "Marcos"
    assert bible["personagem"]["reference"] == "@Marcos"
    assert "olive green" in bible["personagem"]["clothing"].lower()
    assert len(bible["continuidade"]["rules"]) >= 4

    # Verifica arquivo salvo no disco
    p_mem = pdir / "project_visual_memory.json"
    assert p_mem.exists()


def test_2_ambiente_consistente():
    """Valida verificação de ambiente coerente vs divergente pelo Continuity Checker."""
    proj = "projeto_marcos"
    bible = vme_svc.obter_memoria_visual_projeto(proj)

    # Cena no jardim correto
    cena_boa = {
        "id": 1,
        "texto": "Aqui no meu jardim eu cuido das orquídeas",
        "narration": "Aqui no meu jardim eu cuido das orquídeas",
        "scene_type": "avatar_talking",
        "uses_character": True,
        "character_ref": "@Marcos",
        "camera_direction": {"shot": "medium shot"}
    }
    res_boa = continuity_checker_svc.verificar_continuidade_cena(cena_boa, bible)
    assert res_boa["environment_consistent"] is True
    assert res_boa["continuity_score"] == 100

    # Cena em local divergente (praia)
    cena_divergente = {
        "id": 2,
        "texto": "Estou na praia observando o mar aberto",
        "narration": "Estou na praia observando o mar aberto",
        "scene_type": "environment",
        "uses_character": False,
        "character_ref": "",
        "camera_direction": {"shot": "wide shot"}
    }
    res_div = continuity_checker_svc.verificar_continuidade_cena(cena_divergente, bible)
    assert res_div["environment_consistent"] is False
    assert res_div["continuity_score"] < 100
    assert any("praia" in w.lower() for w in res_div["warnings"])


def test_3_objeto_principal_preservado():
    """Valida que o objeto principal (adubo de banana) é respeitado e violações são pontuadas."""
    proj = "projeto_marcos"
    bible = vme_svc.obter_memoria_visual_projeto(proj)

    # Cena com menção incorreta a fruto inteiro
    cena_objeto_errado = {
        "id": 3,
        "texto": "Vou usar essa banana inteira com casca e polpa",
        "narration": "Vou usar essa banana inteira com casca e polpa",
        "scene_type": "broll_action",
        "uses_character": False,
        "character_ref": "",
        "camera_direction": {"shot": "close-up"}
    }
    res_obj = continuity_checker_svc.verificar_continuidade_cena(cena_objeto_errado, bible)
    assert res_obj["object_consistent"] is False
    assert any("objeto" in w.lower() for w in res_obj["warnings"])


def test_4_score_visual_criado():
    """Valida geração do visual_score e checks aprovados pelo Visual Judgment Engine."""
    proj = "projeto_marcos"
    bible = vme_svc.obter_memoria_visual_projeto(proj)

    cena_correta = {
        "id": 1,
        "scene_index": 1,
        "texto": "Olá eu sou o Marcos mostrando o adubo orgânico",
        "narration": "Olá eu sou o Marcos mostrando o adubo orgânico",
        "prompt_imagem": "@Marcos presenting organic banana peel compost fertilizer in the rustic garden. medium shot, 35mm lens, centered subject. natural morning daylight, shallow depth of field, 16:9 framing. Preserve exact character visual identity.",
        "uses_character": True,
        "character_ref": "@Marcos",
        "camera_direction": {"shot": "medium shot", "lens": "35mm"},
        "continuity_context": "Preserve exact character visual identity"
    }

    judgment = visual_judgment_svc.avaliar_imagem_cena(proj, cena_correta, bible)
    assert judgment["visual_score"] >= 80
    assert judgment["checks"]["character"] is True
    assert judgment["checks"]["object"] is True
    assert judgment["checks"]["continuity"] is True
    assert judgment["checks"]["composition"] is True
    assert judgment["judgment_status"] == "approved"
    assert "@Marcos" in judgment["selection_reason"] or "aprovada" in judgment["selection_reason"].lower()


def test_5_imagem_ruim_identificada():
    """Valida que uma cena com tag genérica @Homem e objeto errado é rejeitada com baixo score."""
    proj = "projeto_marcos"
    bible = vme_svc.obter_memoria_visual_projeto(proj)

    cena_inconsistente = {
        "id": 2,
        "scene_index": 2,
        "texto": "Homem segurando banana inteira",
        "narration": "Homem segurando banana inteira",
        "prompt_imagem": "@Homem holding a whole banana. generic flower backdrop.",
        "uses_character": True,
        "character_ref": "@Marcos",
        "camera_direction": {},
        "continuity_context": ""
    }

    judgment_bad = visual_judgment_svc.avaliar_imagem_cena(proj, cena_inconsistente, bible)
    assert judgment_bad["visual_score"] < 50
    assert judgment_bad["checks"]["character"] is False
    assert judgment_bad["checks"]["object"] is False
    assert judgment_bad["judgment_status"] == "rejected"


if __name__ == "__main__":
    print("=" * 80)
    print("EXECUTANDO TESTES DA FASE 4.0 — VISUAL MEMORY & JUDGMENT ENGINE")
    print("=" * 80)
    test_1_personagem_marcos_consistente()
    print("✓ Teste 1: Consistência de @Marcos na Bíblia Visual APROVADO")
    test_2_ambiente_consistente()
    print("✓ Teste 2: Consistência e Detecção de Ambiente APROVADO")
    test_3_objeto_principal_preservado()
    print("✓ Teste 3: Preservação de Objeto Principal APROVADO")
    test_4_score_visual_criado()
    print("✓ Teste 4: Cálculo de Visual Score e Checks APROVADO")
    test_5_imagem_ruim_identificada()
    print("✓ Teste 5: Identificação e Rejeição de Inconsistências APROVADO")
    print("\n" + "=" * 80)
    print("TODOS OS TESTES DA FASE 4.0 PASSARAM COM SUCESSO!")
    print("=" * 80)
