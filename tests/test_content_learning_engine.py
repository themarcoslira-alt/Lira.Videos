"""
tests/test_content_learning_engine.py — Content Learning Engine Suite (FASE 8)
==============================================================================
Valida:
1. Inicialização e leitura da memória global de aprendizado.
2. Indexação de projetos concluídos e incorporação de hooks vencedores.
3. Fornecimento de recomendações de direção baseadas em aprendizado histórico.
4. Mapeamento de anti-padrões e estilos visuais de alta performance.
"""

import os
import sys
from pathlib import Path

# Adiciona raiz do ultracut3 ao path
ROOT_DIR = Path(r"C:\ultracut3")
sys.path.insert(0, str(ROOT_DIR))
sys.stdout.reconfigure(encoding="utf-8")

import services.content_learning_engine as learning_engine_svc


def test_memoria_aprendizado_e_recomendacoes():
    """Valida ciclo completo de leitura, indexação de projeto e recomendação."""
    
    # 1. Leitura inicial
    mem = learning_engine_svc.obter_memoria_aprendizado()
    assert mem["versao"] == "3.0"
    assert len(mem["hooks_vencedores"]) >= 2
    assert len(mem["padroes_reprovados"]) >= 3
    assert len(mem["estilos_eficientes"]) >= 1

    # 2. Simula indexação de um projeto com alto score
    fake_scene_plan = {
        "projeto": "projeto_teste_aprendizado",
        "cenas": [
            {
                "id": 1,
                "story_role": "hook",
                "narration": "O segredo definitivo que salvou meu jardim de orquídeas em 3 passos simples.",
                "visual_score": 96,
                "retention_index": 98
            },
            {
                "id": 2,
                "story_role": "process",
                "narration": "Misturando o adubo de banana",
                "visual_score": 92,
                "retention_index": 85
            }
        ]
    }

    mem_atualizada = learning_engine_svc.registrar_aprendizado_projeto(
        projeto_id="projeto_teste_aprendizado",
        scene_plan=fake_scene_plan
    )

    assert mem_atualizada["total_projetos_analisados"] >= 1
    assert any("projeto_teste_aprendizado" in h.get("projeto", "") for h in mem_atualizada["hooks_vencedores"])

    # 3. Recomendações
    recs = learning_engine_svc.obter_recomendacoes_aprendidas(tema="gardening")
    assert len(recs["top_hooks"]) >= 1
    assert len(recs["top_styles"]) >= 1
    assert len(recs["anti_patterns"]) >= 3


if __name__ == "__main__":
    print("=" * 80)
    print("EXECUTANDO TESTES DA FASE 8 — CONTENT LEARNING ENGINE")
    print("=" * 80)
    test_memoria_aprendizado_e_recomendacoes()
    print("✓ Teste 1: Leitura e integridade da base de aprendizado APROVADO")
    print("✓ Teste 2: Indexação e aprendizado de novo projeto APROVADO")
    print("✓ Teste 3: Extração de recomendações estratégicas APROVADO")
    print("\nTODOS OS TESTES DO CONTENT LEARNING ENGINE PASSARAM COM SUCESSO!")
    print("=" * 80)
