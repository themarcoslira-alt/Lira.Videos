"""
tests/test_image_variation_selector.py — Image Variation Selector AI Suite (FASE 5)
===================================================================================
Valida:
1. Comparação de múltiplas variações de imagem para a mesma cena.
2. Pontuação nos 5 critérios: Personagem, Objeto, Continuidade, Composição e Qualidade.
3. Escolha automática da melhor variação (winner) com justificativa técnica.
4. Identificação e penalização de variações com tags genéricas (@Homem) ou objetos incorretos.
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

import services.character_service as character_svc
import services.visual_memory_engine as vme_svc
import services.image_variation_selector_service as variation_selector_svc


def _gerar_imagem_png(caminho: Path, cor=(34, 139, 34)):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    arr = np.random.randint(0, 255, (256, 256, 3), dtype=np.uint8)
    im = Image.fromarray(arr)
    im.save(caminho, format="PNG")


def test_selecao_automatica_melhor_variacao():
    """Valida eleição da melhor variação entre 3 opções candidatas."""
    proj = "test_variation_selector_proj"
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
    ident = character_svc.obter_identidade_projeto(proj)
    ctx = {
        "world": "Lush botanical rustic garden with green foliage, rich dark soil and natural daylight",
        "visual_style": "photorealistic_cinematic",
        "recurring_objects": ["organic banana peel compost fertilizer", "orchid flowers", "dark soil"]
    }
    memoria_visual = vme_svc.construir_memoria_visual_projeto(proj, ctx, ident)

    # 2. Cria 3 arquivos físicos de imagens candidatas
    var1_path = pdir / "variacoes" / "cena_001_var1.png"
    var2_path = pdir / "variacoes" / "cena_001_var2.png"
    var3_path = pdir / "variacoes" / "cena_001_var3.png"
    _gerar_imagem_png(var1_path)
    _gerar_imagem_png(var2_path)
    _gerar_imagem_png(var3_path)

    cena = {
        "id": 1,
        "scene_index": 1,
        "narration": "Olá eu sou o Marcos mostrando o adubo orgânico",
        "texto": "Olá eu sou o Marcos mostrando o adubo orgânico",
        "uses_character": True,
        "character_ref": "@Marcos",
        "camera_direction": {"shot": "medium shot", "lens": "35mm"},
        "continuity_context": "Preserve exact character visual identity"
    }

    # Opções com diferentes níveis de qualidade/fidelidade
    candidatas = [
        {
            "index": 0,
            "image_path": str(var1_path),
            "prompt": "@Marcos presenting organic banana peel compost fertilizer in the rustic garden. 35mm medium shot. 16:9 framing."
        },
        {
            "index": 1,
            "image_path": str(var2_path),
            "prompt": "@Homem holding a banana. generic flower background."
        },
        {
            "index": 2,
            "image_path": str(var3_path),
            "prompt": "@Marcos holding a whole banana fruit in the market."
        }
    ]

    resultado = variation_selector_svc.comparar_e_selecionar_melhor_variacao(
        projeto_id=proj,
        cena=cena,
        variacoes=candidatas,
        memoria_visual=memoria_visual
    )

    # Variação 0 deve ser a vencedora indiscutível
    assert resultado["winner_index"] == 0
    assert resultado["winner_path"] == str(var1_path)
    assert resultado["winner_score"] >= 80
    assert len(resultado["ranked_variations"]) == 3

    # Ranking ordenado
    ranked = resultado["ranked_variations"]
    assert ranked[0]["variation_index"] == 0
    assert ranked[0]["total_score"] > ranked[1]["total_score"]
    assert ranked[0]["total_score"] > ranked[2]["total_score"]

    # Variação 1 (@Homem) deve ter penalidade severa em personagem
    var_homem = next(r for r in ranked if r["variation_index"] == 1)
    assert var_homem["judgment_status"] == "rejected"
    assert var_homem["breakdown"]["character"] == 5

    # Variação 2 (whole banana) deve ter penalidade em objeto
    var_banana = next(r for r in ranked if r["variation_index"] == 2)
    assert var_banana["breakdown"]["object"] == 5


if __name__ == "__main__":
    print("=" * 80)
    print("EXECUTANDO TESTES DA FASE 5 — IMAGE VARIATION SELECTOR AI")
    print("=" * 80)
    test_selecao_automatica_melhor_variacao()
    print("✓ Teste 1: Comparação de 3 variações e eleição do vencedor APROVADO")
    print("✓ Teste 2: Penalização precisa de tag genérica @Homem APROVADO")
    print("✓ Teste 3: Penalização de objeto incorreto (fruta inteira) APROVADO")
    print("\nTODOS OS TESTES DO IMAGE VARIATION SELECTOR AI PASSARAM COM SUCESSO!")
    print("=" * 80)
