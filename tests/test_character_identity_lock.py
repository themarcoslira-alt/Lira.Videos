"""
tests/test_character_identity_lock.py — Character Identity Lock Suite (FASE 11.1)
================================================================================
Valida:
1. avatar_action mantém @Marcos (uses_character=True, character_ref="@Marcos").
2. hybrid mantém @Marcos (uses_character=True, character_ref="@Marcos").
3. broll (broll_macro, broll_action) continua sem personagem (uses_character=False, character_ref="").
4. Chip falhando no Flow mantém @Marcos no texto do prompt final (fallback textual seguro).
5. Flow e Scene Plan sincronizam perfeitamente a identidade do personagem bloqueado.
"""

import sys
import json
import shutil
from pathlib import Path

ROOT_DIR = Path(r"C:\ultracut3")
sys.path.insert(0, str(ROOT_DIR))
sys.stdout.reconfigure(encoding="utf-8")

import services.character_service as character_svc
import services.scene_plan_service as scene_plan_svc
from services.playwright_flow import PlaywrightCDPWorker


def test_character_identity_lock_completo():
    proj = "test_char_identity_lock_v11_proj"
    pdir = ROOT_DIR / "projetos" / proj
    if pdir.exists():
        shutil.rmtree(pdir)
    pdir.mkdir(parents=True, exist_ok=True)

    # 1. Configura Identidade Permanente Bloqueada (@Marcos)
    character_svc.salvar_identidade_projeto(
        projeto_id=proj,
        tipo="personagem",
        nome="Marcos",
        referencia_flow="@Marcos",
        visual_style="photorealistic_cinematic"
    )

    # 2. Testa obter_personagem_cena() para diferentes tipos de cenas
    # Caso 1: avatar_action
    cena_avatar_action = {
        "id": 5,
        "scene_type": "avatar_action",
        "narration": "banana peels. But before I show you exactly how I prepared this, there's something important",
        "prompt_imagem": "@Marcos actively working in the garden, handling botanical care with focused posture."
    }
    char_avatar_action = character_svc.obter_personagem_cena(proj, cena_avatar_action)
    assert char_avatar_action["uses_character"] is True, "avatar_action deve ter uses_character=True"
    assert char_avatar_action["character_ref"] == "@Marcos", "avatar_action deve ter character_ref='@Marcos'"

    # Caso 2: hybrid
    cena_hybrid = {
        "id": 4,
        "scene_type": "hybrid",
        "narration": "I made one simple change using just banana peels.",
        "prompt_imagem": "@Marcos presenting an organic botanical element towards the foreground."
    }
    char_hybrid = character_svc.obter_personagem_cena(proj, cena_hybrid)
    assert char_hybrid["uses_character"] is True, "hybrid deve ter uses_character=True"
    assert char_hybrid["character_ref"] == "@Marcos", "hybrid deve ter character_ref='@Marcos'"

    # Caso 3: broll_macro
    cena_broll_macro = {
        "id": 3,
        "scene_type": "broll_macro",
        "narration": "Barely any buds on the rose plant.",
        "prompt_imagem": "Macro close-up of healthy white and golden plant root network."
    }
    char_broll = character_svc.obter_personagem_cena(proj, cena_broll_macro)
    assert char_broll["uses_character"] is False, "broll_macro deve ter uses_character=False"
    assert char_broll["character_ref"] == "", "broll_macro deve ter character_ref vazio"

    # 3. Testa sincronizar_trava_identidade_cenas no scene_plan_service
    cenas_inconsistentes = [
        {
            "id": 1,
            "scene_type": "avatar_action",
            "uses_character": False, # Inconsistência intencional
            "character_ref": "",
            "prompt_imagem": "@Marcos actively working in the garden."
        },
        {
            "id": 2,
            "scene_type": "hybrid",
            "uses_character": False, # Inconsistência intencional
            "character_ref": "",
            "prompt_imagem": "Demonstrating soil mixture."
        },
        {
            "id": 3,
            "scene_type": "broll_macro",
            "uses_character": False,
            "character_ref": "",
            "prompt_imagem": "Macro shot of rose petals."
        }
    ]

    cenas_corrigidas = scene_plan_svc.sincronizar_trava_identidade_cenas(cenas_inconsistentes, projeto_id=proj)
    
    # Valida Cena 1 (avatar_action)
    assert cenas_corrigidas[0]["uses_character"] is True
    assert cenas_corrigidas[0]["character_ref"] == "@Marcos"

    # Valida Cena 2 (hybrid)
    assert cenas_corrigidas[1]["uses_character"] is True
    assert cenas_corrigidas[1]["character_ref"] == "@Marcos"

    # Valida Cena 3 (broll_macro)
    assert cenas_corrigidas[2]["uses_character"] is False
    assert cenas_corrigidas[2]["character_ref"] == ""

    # 4. Testa Segurança de Limpeza de Prompt no PlaywrightCDPWorker
    worker = PlaywrightCDPWorker()

    prompt_original = "@Marcos actively working in the garden, handling botanical care with focused posture."

    # Cenário A: Chip inserido com sucesso (strip_character_tag=True para não duplicar no editor Slate)
    prompt_chip_ok = worker._clean_prompt_text(prompt_original, ref_tag="@Marcos", strip_character_tag=True)
    assert not prompt_chip_ok.startswith("@Marcos")
    assert "actively working in the garden" in prompt_chip_ok

    # Cenário B: Chip FALHOU ou não inserido (strip_character_tag=False para manter fallback textual)
    prompt_chip_falhou = worker._clean_prompt_text(prompt_original, ref_tag="@Marcos", strip_character_tag=False)
    assert prompt_chip_falhou.startswith("@Marcos"), "Se o chip falhar, o prompt DEVE manter @Marcos no início!"
    assert "actively working in the garden" in prompt_chip_falhou


if __name__ == "__main__":
    print("=" * 80)
    print("EXECUTANDO TESTES DA FASE 11.1 — CHARACTER IDENTITY LOCK")
    print("=" * 80)
    test_character_identity_lock_completo()
    print("✓ Teste 1: avatar_action trava @Marcos (uses_character=True) APROVADO")
    print("✓ Teste 2: hybrid trava @Marcos (uses_character=True) APROVADO")
    print("✓ Teste 3: broll_macro permanece sem personagem (uses_character=False) APROVADO")
    print("✓ Teste 4: Sincronização automática do Scene Plan APROVADO")
    print("✓ Teste 5: Fallback textual do Playwright Flow preserva @Marcos se chip falhar APROVADO")
    print("\nTODOS OS TESTES DA FASE 11.1 PASSARAM COM 100% DE SUCESSO!")
    print("=" * 80)
