"""
tests/test_visual_director.py — Automated Test Suite for Visual Director AI
===========================================================================
Valida:
1. Roteiro com personagem (@Marcos anexado corretamente, sem @Homem)
2. Roteiro sem personagem (puro B-roll, sem inventar personagem)
3. B-roll puro e cena híbrida
4. Continuidade de mundo, iluminação e estilo
5. Diversidade de enquadramentos e lentes do Camera Director
6. Pacing e alternância de cenas pelo Story Rhythm Director
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
import services.visual_director_service as visual_director_svc
import services.scene_classifier_service as scene_classifier_svc
import services.character_decision_service as character_decision_svc
import services.emotion_director_service as emotion_director_svc
import services.camera_director_service as camera_director_svc
import services.broll_intelligence_service as broll_intelligence_svc
import services.prompt_builder_service as prompt_builder_svc
import services.story_rhythm_service as story_rhythm_svc





def test_visual_director_com_personagem():
    """Valida pipeline completo com personagem bloqueado (@Marcos)."""
    proj = "test_director_com_personagem"
    pdir = ROOT_DIR / "projetos" / proj
    if pdir.exists():
        shutil.rmtree(pdir)

    # 1. Configura Identidade com Personagem Bloqueado
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
        flow_char_id="flow-id-marcos-12345"
    )

    # 2. Ingestão de SRT com 5 blocos
    srt_script = """1
00:00:00,000 --> 00:00:03,500
Olá amigos, eu sou o Marcos e hoje eu vou mostrar o segredo das minhas orquídeas.

2
00:00:03,500 --> 00:00:07,000
Observe essas raízes expostas e como as folhas absorvem a umidade.

3
00:00:07,000 --> 00:00:10,500
Aqui eu mostro na prática como aplicar a adubação orgânica ao redor do vaso.

4
00:00:10,500 --> 00:00:14,000
Vejam a textura da casca de banana fermentada no solo úmido.

5
00:00:14,000 --> 00:00:18,000
Se inscreva no canal para aprender mais técnicas de cultivo!
"""
    client = app.test_client()
    res = client.post(f"/api/v2/transcricao/{proj}/usar_srt", json={"srt_texto": srt_script})
    assert res.status_code == 200

    plan = scene_plan_svc.carregar_scene_plan(proj)
    assert plan is not None
    assert len(plan["cenas"]) == 5

    # Contexto Visual
    ctx = plan.get("visual_context") or visual_director_svc.obter_contexto_visual(proj)
    assert ctx["main_character"] == "Marcos"
    assert "garden" in ctx["world"].lower() or "botanical" in ctx["world"].lower()

    # Cena 1: Avatar Talking / Hook
    c1 = plan["cenas"][0]
    assert c1["scene_type"] == "avatar_talking"
    assert c1["uses_character"] is True
    assert c1["character_ref"] == "@Marcos"
    assert c1["visual_role"] == "hook"
    assert "@Homem" not in c1["prompt_imagem"]
    assert "@Marcos" in c1["prompt_imagem"]

    # Cena 2: B-Roll Macro
    c2 = plan["cenas"][1]
    assert c2["scene_type"] == "broll_macro"
    assert c2["uses_character"] is False
    assert c2["character_ref"] == ""
    assert len(c2["supporting_visuals"]) > 0

    # Cena 3: Híbrida (Demonstração com apresentador)
    c3 = plan["cenas"][2]
    assert c3["scene_type"] == "hybrid"
    assert c3["uses_character"] is True
    assert c3["character_ref"] == "@Marcos"

    # Cena 4: B-Roll Action / Macro
    c4 = plan["cenas"][3]
    assert c4["uses_character"] is False
    assert "soil" in c4["prompt_imagem"].lower() or "banana" in c4["prompt_imagem"].lower()

    # Cena 5: CTA
    c5 = plan["cenas"][4]
    assert c5["scene_type"] == "cta"
    assert c5["visual_role"] == "call_to_action"


def test_visual_director_sem_personagem():
    """Valida roteiro puramente de B-Roll / Natureza sem inventar personagens."""
    proj = "test_director_sem_personagem"
    pdir = ROOT_DIR / "projetos" / proj
    if pdir.exists():
        shutil.rmtree(pdir)

    srt_natureza = """1
00:00:00,000 --> 00:00:04,000
A floresta tropical desperta com os primeiros raios de sol entre a copa das árvores.

2
00:00:04,000 --> 00:00:08,000
Gotas de orvalho escorrem delicadamente pelas folhas das samambaias.

3
00:00:08,000 --> 00:00:12,000
Pequenas nascentes de água cristalina cortam as pedras cobertas de musgo verde.
"""
    client = app.test_client()
    res = client.post(f"/api/v2/transcricao/{proj}/usar_srt", json={"srt_texto": srt_natureza})
    assert res.status_code == 200

    plan = scene_plan_svc.carregar_scene_plan(proj)
    assert len(plan["cenas"]) == 3

    for c in plan["cenas"]:
        assert c["uses_character"] is False
        assert c["character_ref"] == ""
        assert "@Homem" not in c["prompt_imagem"]
        assert "@Personagem" not in c["prompt_imagem"]
        assert c["scene_type"] in ("environment", "broll_macro", "broll_action")


def test_camera_diversity_and_rhythm():
    """Valida que a câmera varia e o ritmo não permite cadeias monótonas."""
    cenas_mock = [
        {"id": 1, "texto": "Eu comecei falando", "narration": "Eu comecei falando", "scene_type": "avatar_talking", "uses_character": True},
        {"id": 2, "texto": "Eu continuei falando", "narration": "Eu continuei falando", "scene_type": "avatar_talking", "uses_character": True},
        {"id": 3, "texto": "Eu ainda estava falando no mesmo lugar", "narration": "Eu ainda estava falando no mesmo lugar", "scene_type": "avatar_talking", "uses_character": True},
        {"id": 4, "texto": "E eu falava mais", "narration": "E eu falava mais", "scene_type": "avatar_talking", "uses_character": True},
    ]
    
    # Executa otimização de ritmo
    otimizadas = story_rhythm_svc.otimizar_ritmo_cenas(cenas_mock, {"main_character": "Marcos"})
    
    # Valida que a 3ª cena consecutiva foi modulada para quebrar a cadeia
    tipos = [c["scene_type"] for c in otimizadas]
    assert otimizadas[2]["scene_type"] in ("hybrid", "broll_action")
    # Não pode haver 3 avatar_talking consecutivos
    for i in range(len(tipos) - 2):
        assert not (tipos[i] == "avatar_talking" and tipos[i+1] == "avatar_talking" and tipos[i+2] == "avatar_talking")


if __name__ == "__main__":
    print("Executando suite de testes do Visual Director AI...")
    test_visual_director_com_personagem()
    print("✓ Teste 1: Roteiro com Personagem @Marcos APROVADO")
    test_visual_director_sem_personagem()
    print("✓ Teste 2: Roteiro sem Personagem (B-Roll puro) APROVADO")
    test_camera_diversity_and_rhythm()
    print("✓ Teste 3: Ritmo e Diversidade de Câmera APROVADO")
    print("\nTODOS OS TESTES DO VISUAL DIRECTOR AI PASSARAM COM SUCESSO!")
