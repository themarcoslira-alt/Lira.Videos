"""
tests/test_retention_director.py — Retention Director AI Suite (FASE 7)
======================================================================
Valida:
1. Hook inicial recebe prioridade e score de retenção máximo (>= 95).
2. Prevenção de monotonia (Anti-Monotonia): quebra de sequências excessivas de avatar.
3. Cadência rítmica (Pessoa -> Detalhe -> Ação -> Resultado) preservada.
4. Pontuação global de retenção do projeto (project_retention_score) e cues de retenção.
"""

import os
import sys
from pathlib import Path

# Adiciona raiz do ultracut3 ao path
ROOT_DIR = Path(r"C:\ultracut3")
sys.path.insert(0, str(ROOT_DIR))
sys.stdout.reconfigure(encoding="utf-8")

import services.retention_director_service as retention_director_svc


def test_retencao_e_quebra_de_monotonia():
    """Valida quebra de sequências monótonas de avatar e cálculo de retenção."""
    
    # 1. Roteiro com 4 avatares seguidos (monotonia intencional para testar quebra)
    cenas_monotonas = [
        {"id": 1, "scene_type": "avatar_talking", "story_role": "hook", "uses_character": True, "character_ref": "@Marcos"},
        {"id": 2, "scene_type": "avatar_talking", "story_role": "problem", "uses_character": True, "character_ref": "@Marcos"},
        {"id": 3, "scene_type": "avatar_talking", "story_role": "explanation", "uses_character": True, "character_ref": "@Marcos"},
        {"id": 4, "scene_type": "avatar_talking", "story_role": "process", "uses_character": True, "character_ref": "@Marcos"},
        {"id": 5, "scene_type": "cta", "story_role": "cta", "uses_character": True, "character_ref": "@Marcos"},
    ]

    res = retention_director_svc.otimizar_retencao_projeto(cenas_monotonas)
    assert res["project_retention_score"] >= 80
    assert res["pacing_grade"] in ("A+", "A")
    assert res["pattern_interrupts_applied"] >= 1

    cenas_otimizadas = res["scenes"]
    
    # Cena 1 deve ser Hook com retenção >= 95
    assert cenas_otimizadas[0]["retention_index"] >= 95
    assert len(cenas_otimizadas[0]["retention_cues"]) >= 1

    # Cena 3 ou 4 deve ter sofrido quebra de padrão (pattern_interrupt = True) e virado B-Roll
    cenas_interrompidas = [c for c in cenas_otimizadas if c.get("pattern_interrupt") is True]
    assert len(cenas_interrompidas) >= 1
    assert cenas_interrompidas[0]["scene_type"] in ("broll_macro", "broll_action")
    assert cenas_interrompidas[0]["uses_character"] is False


def test_cadencia_narrativa_ideal():
    """Valida retenção em sequência dinâmica (Pessoa -> Detalhe -> Ação -> Prova -> CTA)."""
    cenas_dinamicas = [
        {"id": 1, "scene_type": "avatar_talking", "story_role": "hook", "uses_character": True, "character_ref": "@Marcos"},
        {"id": 2, "scene_type": "broll_macro", "story_role": "problem", "uses_character": False, "character_ref": ""},
        {"id": 3, "scene_type": "broll_action", "story_role": "process", "uses_character": False, "character_ref": ""},
        {"id": 4, "scene_type": "before_after", "story_role": "proof", "uses_character": False, "character_ref": ""},
        {"id": 5, "scene_type": "cta", "story_role": "cta", "uses_character": True, "character_ref": "@Marcos"},
    ]

    res = retention_director_svc.otimizar_retencao_projeto(cenas_dinamicas)
    assert res["project_retention_score"] >= 88
    assert res["pacing_grade"] in ("A+", "A")
    assert res["pattern_interrupts_applied"] == 0  # Já estava ideal, sem necessidade de interrupção


if __name__ == "__main__":
    print("=" * 80)
    print("EXECUTANDO TESTES DA FASE 7 — RETENTION DIRECTOR AI")
    print("=" * 80)
    test_retencao_e_quebra_de_monotonia()
    print("✓ Teste 1: Quebra de padrão anti-monotonia (Pattern Interrupt) APROVADO")
    test_cadencia_narrativa_ideal()
    print("✓ Teste 2: Cadência ideal e cálculo do score global de retenção APROVADO")
    print("\nTODOS OS TESTES DO RETENTION DIRECTOR AI PASSARAM COM SUCESSO!")
    print("=" * 80)
