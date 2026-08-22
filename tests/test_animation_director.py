"""
tests/test_animation_director.py — Animation Director AI Suite (FASE 6)
=======================================================================
Valida:
1. Apresentador falando (avatar_talking) recebe animação de fala e slow push-in.
2. Mãos trabalhando / ações físicas recebem animação fluida (kinetic_action).
3. Transformação / resultado botânico recebe animação de revelação (transformation_reveal).
4. Comparações estáticas (comparison) são marcadas como NÃO animar (should_animate = False).
5. Macro de textura estática não é animado desnecessariamente.
"""

import os
import sys
from pathlib import Path

# Adiciona raiz do ultracut3 ao path
ROOT_DIR = Path(r"C:\ultracut3")
sys.path.insert(0, str(ROOT_DIR))
sys.stdout.reconfigure(encoding="utf-8")

import services.animation_director_service as animation_director_svc


def test_animacao_cenas_diretor():
    """Valida as regras de decisão cinematográfica do Animation Director AI."""
    
    # 1. Apresentador Falando
    cena_avatar = {
        "id": 1,
        "scene_type": "avatar_talking",
        "story_role": "hook",
        "uses_character": True,
        "character_ref": "@Marcos",
        "narration": "Olá eu sou o Marcos"
    }
    dec_avatar = animation_director_svc.direcionar_animacao_cena(cena_avatar)
    assert dec_avatar["should_animate"] is True
    assert dec_avatar["animation_type"] == "presenter_speech"
    assert "push-in" in dec_avatar["prompt_animacao"].lower()
    assert dec_avatar["animation_priority"] in ("high", "medium")

    # 2. Ação Física / Mãos trabalhando
    cena_acao = {
        "id": 2,
        "scene_type": "broll_action",
        "story_role": "process",
        "uses_character": False,
        "narration": "Comece aplicando o adubo de banana ao redor da planta"
    }
    dec_acao = animation_director_svc.direcionar_animacao_cena(cena_acao)
    assert dec_acao["should_animate"] is True
    assert dec_acao["animation_type"] == "kinetic_action"
    assert dec_acao["animation_priority"] == "high"

    # 3. Transformação / Prova
    cena_prova = {
        "id": 3,
        "scene_type": "before_after",
        "story_role": "proof",
        "uses_character": False,
        "narration": "Vejam a transformação impressionante e as flores revigoradas"
    }
    dec_prova = animation_director_svc.direcionar_animacao_cena(cena_prova)
    assert dec_prova["should_animate"] is True
    assert dec_prova["animation_type"] == "transformation_reveal"

    # 4. Comparação Estática (NÃO animar)
    cena_comp = {
        "id": 4,
        "scene_type": "comparison",
        "story_role": "comparison",
        "uses_character": False,
        "narration": "Comparando o lado adubado com o lado sem adubo"
    }
    dec_comp = animation_director_svc.direcionar_animacao_cena(cena_comp)
    assert dec_comp["should_animate"] is False
    assert dec_comp["animation_type"] == "static_freeze"
    assert dec_comp["animation_priority"] == "none"

    # 5. Macro Estático de Textura (NÃO animar)
    cena_macro_estatica = {
        "id": 5,
        "scene_type": "broll_macro",
        "story_role": "discovery",
        "uses_character": False,
        "narration": "Observem a estrutura microscópica dos poros foliares"
    }
    dec_macro = animation_director_svc.direcionar_animacao_cena(cena_macro_estatica)
    assert dec_macro["should_animate"] is False
    assert dec_macro["animation_type"] == "static_macro"


if __name__ == "__main__":
    print("=" * 80)
    print("EXECUTANDO TESTES DA FASE 6 — ANIMATION DIRECTOR AI")
    print("=" * 80)
    test_animacao_cenas_diretor()
    print("✓ Teste 1: Decisão de animação para Avatar e Diálogo APROVADO")
    print("✓ Teste 2: Decisão de animação para Ação e Manuseio APROVADO")
    print("✓ Teste 3: Decisão de animação para Revelação/Transformação APROVADO")
    print("✓ Teste 4: Imobilização de Comparações e Macro Estático APROVADO")
    print("\nTODOS OS TESTES DO ANIMATION DIRECTOR AI PASSARAM COM SUCESSO!")
    print("=" * 80)
