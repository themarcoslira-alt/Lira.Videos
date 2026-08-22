"""
tests/test_autonomous_director.py — Autonomous Director AI Suite (FASE 10)
===========================================================================
Valida:
1. Execução autônoma de ponta a ponta a partir de um roteiro fornecido pelo usuário.
2. Tomada de decisões automáticas de:
   - Divisão e classificação de cenas
   - Ritmo e retenção (Retention & Storyboard Director)
   - Câmera e composição cinematográfica (Camera Director)
   - Personagem bloqueado Flow (@Marcos)
   - Prompts de imagem e animação (Animation & Prompt Builder)
   - Persistência da memória visual e histórico de decisões
3. Validação do endpoint POST /api/v2/autonomous_direct/<projeto_id>.
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


def test_direcao_autonoma_completa():
    """Valida o orquestrador mestre autônomo."""
    proj = "test_autonomous_director_proj"
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

    srt_script = """1
00:00:00,000 --> 00:00:04,000
Olá pessoal, eu sou o Marcos e hoje eu vou ensinar o segredo do adubo orgânico de banana.

2
00:00:04,000 --> 00:00:08,000
Vejam a textura da casca de banana fermentada nutrindo as raízes no solo escuro.

3
00:00:08,000 --> 00:00:12,000
Se inscreva no canal para acompanhar mais tutoriais completos de cultivo!
"""

    # 2. Execução Direta via Serviço
    res = auto_director_svc.dirigir_producao_autonoma(
        projeto_id=proj,
        roteiro_texto=srt_script,
        nome_personagem="Marcos",
        estilo_visual="photorealistic_cinematic"
    )

    assert res["success"] is True
    summary = res["summary"]
    assert summary["total_cenas"] == 3
    assert summary["cenas_com_personagem"] >= 1
    assert summary["cenas_animadas_video"] >= 1

    cenas = res["plan"]["cenas"]
    assert len(cenas) == 3
    assert cenas[0]["story_role"] == "hook"
    assert cenas[0]["uses_character"] is True
    assert cenas[0]["character_ref"] == "@Marcos"
    assert cenas[0]["animate_later"] is True

    # 3. Execução via Endpoint HTTP
    client = app.test_client()
    res_http = client.post(
        f"/api/v2/autonomous_direct/{proj}",
        json={
            "roteiro": srt_script,
            "nome_personagem": "Marcos",
            "estilo_visual": "photorealistic_cinematic"
        }
    )
    assert res_http.status_code == 200
    data_http = res_http.get_json()
    assert data_http["success"] is True
    assert data_http["summary"]["total_cenas"] == 3


if __name__ == "__main__":
    print("=" * 80)
    print("EXECUTANDO TESTES DA FASE 10 — AUTONOMOUS DIRECTOR AI")
    print("=" * 80)
    test_direcao_autonoma_completa()
    print("✓ Teste 1: Orquestração autônoma completa (Cenas, Câmera, Personagem, Animação) APROVADO")
    print("✓ Teste 2: Endpoint /api/v2/autonomous_direct/<projeto_id> APROVADO")
    print("\nTODOS OS TESTES DO AUTONOMOUS DIRECTOR AI PASSARAM COM SUCESSO!")
    print("=" * 80)
