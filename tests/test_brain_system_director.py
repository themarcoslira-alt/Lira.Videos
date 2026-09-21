"""
tests/test_brain_system_director.py — Suíte de Testes Automatizados do Brain System Director
============================================================================================
Testa:
 1. test_fase_0_ingestao_completa
 2. test_fase_1_analise_narrativa
 3. test_fase_2_decisoes_audiovisuais
 4. test_fase_3_enriquecimento_atomico
 5. test_fase_4_persistencia
 6. test_orquestar_producao_completa_end_to_end
 7. test_compatibilidade_schema_entrada_saida
 8. test_error_handling_deepseek_fallback
 9. test_caching_context_pack
10. test_endpoint_gerar_brain_completo
"""

import os
import sys
import json
import shutil
import pytest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app_web import app
from config import PROJETOS_DIR
from services.brain_system_director import BrainSystemDirector
import services.scene_plan_service as scene_plan_svc


TEST_PROJ = "test_brain_director_unit_project"


@pytest.fixture(autouse=True)
def cleanup_test_project():
    """Garante ambiente limpo antes e depois de cada teste."""
    pdir = PROJETOS_DIR / TEST_PROJ
    if pdir.exists():
        try:
            shutil.rmtree(pdir)
        except Exception:
            pass
    pdir.mkdir(parents=True, exist_ok=True)
    yield
    if pdir.exists():
        try:
            shutil.rmtree(pdir)
        except Exception:
            pass


def _criar_entrada_mock():
    return {
        "projeto_id": TEST_PROJ,
        "roteiro_transcricao": {
            "text": "Hello, I am Marcos. Today I will show you how to fertilize tomato plants with organic compost. Step one is preparing the soil. Finally, subscribe to the channel for more tips!",
            "duration": 18.0
        },
        "word_timestamps": [
            {"w": "Hello", "s": 0.0, "e": 0.5},
            {"w": "Marcos", "s": 0.9, "e": 1.4},
            {"w": "Step", "s": 7.5, "e": 8.0},
            {"w": "one", "s": 8.1, "e": 8.5},
            {"w": "subscribe", "s": 14.0, "e": 14.8}
        ],
        "cenas": [
            {"id": 1, "start": 0.0, "end": 4.5, "texto": "Hello, I am Marcos and today I will show you how to fertilize tomato plants."},
            {"id": 2, "start": 4.5, "end": 7.5, "texto": "Organic compost delivers essential nutrients straight to the root zone."},
            {"id": 3, "start": 7.5, "end": 13.0, "texto": "Step one is preparing the deep soil with organic matter."},
            {"id": 4, "start": 13.0, "end": 18.0, "texto": "Finally, subscribe to the channel for more tips!"}
        ],
        "audio_metadata": {
            "path": "",
            "duracao_total": 18.0,
            "silencio_table": [
                {"orig_start": 0.0, "orig_end": 18.0, "proc_start": 0.0, "proc_end": 18.0}
            ]
        },
        "personagem": "@Marcos",
        "estilo_visual": "photorealistic_cinematic"
    }


# =============================================================================
# TESTES UNITÁRIOS DAS FASES
# =============================================================================

def test_fase_0_ingestao_completa():
    """Valida a Fase 0: inicialização da Bíblia Visual e context pack."""
    director = BrainSystemDirector(TEST_PROJ)
    entrada = _criar_entrada_mock()

    res = director._fase_0_ingestao(entrada)

    assert "memoria_visual" in res
    assert "context_pack" in res
    mem = res["memoria_visual"]
    assert mem.get("personagem", {}).get("name") == "Marcos"
    assert mem.get("personagem", {}).get("reference") == "@Marcos"
    assert (PROJETOS_DIR / TEST_PROJ / "project_visual_memory.json").exists()


def test_fase_1_analise_narrativa():
    """Valida a Fase 1: classificação narrativa, detecção de cards e ritmo."""
    director = BrainSystemDirector(TEST_PROJ)
    entrada = _criar_entrada_mock()
    fase0 = director._fase_0_ingestao(entrada)

    cenas = director._fase_1_analise_narrativa(entrada, fase0["memoria_visual"])

    assert len(cenas) == 4
    assert cenas[0]["narrative_role"] == "HOOK"
    assert cenas[0]["uses_character"] is True
    # Cena 3 possui 'Step one'
    assert cenas[2]["is_story_card"] is True
    assert "step" in cenas[2]["story_card_type"]
    assert cenas[3]["narrative_role"] in ("CTA", "CLOSING")


def test_fase_2_decisoes_audiovisuais():
    """Valida a Fase 2: decisões de animação, transições e prompts de card."""
    director = BrainSystemDirector(TEST_PROJ)
    entrada = _criar_entrada_mock()
    fase0 = director._fase_0_ingestao(entrada)
    cenas = director._fase_1_analise_narrativa(entrada, fase0["memoria_visual"])

    decisoes = director._fase_2_decisoes_audiovisuais(entrada, cenas, options={})

    assert "decisoes" in decisoes
    assert len(decisoes["decisoes"]) == 4

    # Cena 1 (HOOK com avatar) deve animar
    cena1_dec = decisoes["decisoes"][0]
    assert cena1_dec["animacao"]["should_animate"] is True
    assert cena1_dec["transicao"]["tipo"] != "none"

    # Cena 3 (Story Card) deve ter prompt de card gerado
    cena3_dec = decisoes["decisoes"][2]
    assert len(cena3_dec["story_card_prompt"]) > 20
    assert "Kurzgesagt" in cena3_dec["story_card_prompt"] or "graphics card" in cena3_dec["story_card_prompt"].lower()


def test_fase_3_enriquecimento_atomico():
    """Valida a Fase 3: montagem de prompts, SFX, áudio e Smart FX."""
    director = BrainSystemDirector(TEST_PROJ)
    entrada = _criar_entrada_mock()
    fase0 = director._fase_0_ingestao(entrada)
    cenas = director._fase_1_analise_narrativa(entrada, fase0["memoria_visual"])
    decisoes = director._fase_2_decisoes_audiovisuais(entrada, cenas, options={})

    saida = director._fase_3_enriquecimento_atomico(
        entrada=entrada,
        decisoes=decisoes,
        memoria_visual=fase0["memoria_visual"],
        context_pack=fase0["context_pack"],
        options={"include_sfx": True, "smart_fx_modo": "sugestoes"}
    )

    assert "cenas" in saida
    assert len(saida["cenas"]) == 4

    c1 = saida["cenas"]["cena_1"]
    assert "prompt_imagem" in c1["prompts"]
    assert len(c1["prompts"]["prompt_imagem"]) > 10
    assert "prompt_animacao" in c1["prompts"]
    assert c1["audio"]["bgm"] is not None
    assert len(c1["sfx_ruidos"]) > 0
    assert "sugestoes_ia" in c1["smart_fx"]


def test_fase_4_persistencia():
    """Valida a Fase 4: gravação de lira_scene_plan.json e brain_director_output.json."""
    director = BrainSystemDirector(TEST_PROJ)
    entrada = _criar_entrada_mock()
    fase0 = director._fase_0_ingestao(entrada)
    cenas = director._fase_1_analise_narrativa(entrada, fase0["memoria_visual"])
    decisoes = director._fase_2_decisoes_audiovisuais(entrada, cenas, options={})
    saida = director._fase_3_enriquecimento_atomico(
        entrada=entrada,
        decisoes=decisoes,
        memoria_visual=fase0["memoria_visual"],
        context_pack=fase0["context_pack"],
        options={}
    )

    sucesso = director._fase_4_compilacao_persistencia(entrada, saida)
    assert sucesso is True

    plan_path = PROJETOS_DIR / TEST_PROJ / "lira_scene_plan.json"
    brain_out_path = PROJETOS_DIR / TEST_PROJ / "brain_director_output.json"

    assert plan_path.exists()
    assert brain_out_path.exists()

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert len(plan["cenas"]) == 4
    assert plan["cenas"][0]["narrative_role"] == "HOOK"


# =============================================================================
# TESTES INTEGRADOS E SCHEMA
# =============================================================================

def test_orquestar_producao_completa_end_to_end():
    """Executa o pipeline completo de ponta a ponta e valida o contrato consolidado."""
    director = BrainSystemDirector(TEST_PROJ)
    entrada = _criar_entrada_mock()

    saida = director.orquestar_producao_completa(entrada)

    assert saida["projeto_id"] == TEST_PROJ
    assert saida["diretor_versao"] == "3.0.0"
    assert saida["grade_producao"] == "A+"
    assert saida["metricas"]["total_cenas"] == 4
    assert saida["metricas"]["total_story_cards"] >= 1
    assert "cena_1" in saida["cenas"]
    assert "cena_4" in saida["cenas"]


def test_compatibilidade_schema_entrada_saida():
    """Valida se cada cena no payload de saída adere estritamente ao schema de entrega."""
    director = BrainSystemDirector(TEST_PROJ)
    entrada = _criar_entrada_mock()
    saida = director.orquestar_producao_completa(entrada)

    for k, cena in saida["cenas"].items():
        assert "scene_id" in cena
        assert "timing" in cena
        assert "start_s" in cena["timing"]
        assert "end_s" in cena["timing"]
        assert "prompts" in cena
        assert "prompt_imagem" in cena["prompts"]
        assert "transicao" in cena
        assert "animacao" in cena
        assert "auditoria_continuidade" in cena


def test_error_handling_deepseek_fallback():
    """Valida a degradação graciosa quando a IA/DeepSeek está fora do ar."""
    director = BrainSystemDirector(TEST_PROJ)
    entrada = _criar_entrada_mock()

    # Simula falha catastrófica no DeepSeek
    with patch("services.brain_system_director.analisar_contexto_global", side_effect=Exception("API Timeout")):
        with patch("services.brain_system_director.gerar_prompts_lote", side_effect=Exception("API Unavailable")):
            saida = director.orquestar_producao_completa(entrada)
            assert saida is not None
            assert len(saida["cenas"]) == 4
            # Prompts foram preenchidos via fallback determinístico
            assert len(saida["cenas"]["cena_1"]["prompts"]["prompt_imagem"]) > 20
            assert "Photorealistic" in saida["cenas"]["cena_1"]["prompts"]["prompt_imagem"]


def test_caching_context_pack():
    """Valida que o Context Pack é lido do cache sem refazer análise repetida."""
    director = BrainSystemDirector(TEST_PROJ)
    entrada = _criar_entrada_mock()

    # Primeira execução: preenche cache
    fase0_1 = director._fase_0_ingestao(entrada)
    assert director._context_pack_cache is not None

    # Segunda execução com chamada mockada para garantir que não chama a API
    with patch("services.brain_system_director.analisar_contexto_global") as mock_analisar:
        fase0_2 = director._fase_0_ingestao(entrada)
        mock_analisar.assert_not_called()
        assert fase0_2["context_pack"] == fase0_1["context_pack"]


def test_endpoint_gerar_brain_completo():
    """Valida o endpoint POST /api/v2/producao/<projeto_id>/gerar_brain_completo via Flask."""
    client = app.test_client()

    # Prepara projeto em disco com cenas.json
    pdir = PROJETOS_DIR / TEST_PROJ
    cenas_file = pdir / "cenas.json"
    cenas_file.write_text(json.dumps(_criar_entrada_mock()["cenas"], indent=2), encoding="utf-8")

    payload = {
        "include_transicoes": True,
        "include_story_cards": True,
        "include_audio_bgm": True,
        "include_efeitos_visuais": True,
        "include_animacoes": True,
        "include_sfx": True,
        "smart_fx_modo": "sugestoes"
    }

    res = client.post(
        f"/api/v2/producao/{TEST_PROJ}/gerar_brain_completo",
        data=json.dumps(payload),
        content_type="application/json"
    )

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["cenas_processadas"] == 4
    assert "brain_json_url" in data
    assert "efeitos_gerados" in data


# =============================================================================
# TESTES DE OVERRIDES MANUAIS (manual_overrides.json) E HASH
# =============================================================================

def test_manual_overrides_aplica_campos_e_flag():
    """Valida que _aplicar_manual_overrides preenche campos e seta manual_override=True."""
    director = BrainSystemDirector(TEST_PROJ)
    pdir = PROJETOS_DIR / TEST_PROJ

    ov_data = {
        "cenas": {
            "2": {
                "narrative_role": "AVATAR",
                "scene_type": "avatar_demonstracao",
                "uses_character": True,
                "character_ref": "@Marcos",
                "texto_contem": "essential nutrients",
                "prompt_imagem": "Realistic Marcos presenting organic nutrients in garden",
                "prompt_animacao": "Marcos gestures gently, natural daylight, 4s smooth ease"
            }
        }
    }
    (pdir / "manual_overrides.json").write_text(json.dumps(ov_data, indent=2), encoding="utf-8")

    cenas = [
        {"id": 1, "narrative_role": "HOOK", "texto": "Hello I am Marcos"},
        {"id": 2, "narrative_role": "BROLL", "texto": "Organic compost delivers essential nutrients straight to roots."},
        {"id": 3, "narrative_role": "BROLL", "texto": "Step one is preparing soil."}
    ]

    director._aplicar_manual_overrides(cenas)

    c2 = cenas[1]
    assert c2.get("manual_override") is True
    assert c2.get("narrative_role") == "AVATAR"
    assert c2.get("scene_type") == "avatar_demonstracao"
    assert c2.get("uses_character") is True
    assert c2.get("character_ref") == "@Marcos"
    assert c2.get("prompt_imagem") == "Realistic Marcos presenting organic nutrients in garden"
    assert c2.get("prompt_animacao") == "Marcos gestures gently, natural daylight, 4s smooth ease"


def test_calcular_hash_insumos_brain_com_e_sem_overrides():
    """Valida que calcular_hash_insumos_brain detecta mudanças em manual_overrides.json."""
    from services.api_v2 import calcular_hash_insumos_brain
    pdir = PROJETOS_DIR / TEST_PROJ

    (pdir / "cenas.json").write_text("[]", encoding="utf-8")
    (pdir / "roteiro.txt").write_text("Teste roteiro", encoding="utf-8")

    h1 = calcular_hash_insumos_brain(TEST_PROJ)

    # Cria manual_overrides.json
    ov_file = pdir / "manual_overrides.json"
    ov_file.write_text(json.dumps({"cenas": {"2": {"narrative_role": "AVATAR"}}}), encoding="utf-8")

    h2 = calcular_hash_insumos_brain(TEST_PROJ)
    assert h1 != h2, "Hash deve mudar após inclusão de manual_overrides.json"

    # Modifica manual_overrides.json
    ov_file.write_text(json.dumps({"cenas": {"2": {"narrative_role": "AVATAR", "v": 2}}}), encoding="utf-8")
    h3 = calcular_hash_insumos_brain(TEST_PROJ)
    assert h2 != h3, "Hash deve mudar após alteração de manual_overrides.json"

    # Sem alterações: hash estável
    h3_novamente = calcular_hash_insumos_brain(TEST_PROJ)
    assert h3 == h3_novamente, "Hash deve ser idêntico quando insumos não mudam"


def test_manual_overrides_arquivo_inexistente_ou_invalido():
    """Valida resiliência do _carregar_manual_overrides a arquivo ausente ou corrompido."""
    director = BrainSystemDirector(TEST_PROJ)
    pdir = PROJETOS_DIR / TEST_PROJ
    ov_file = pdir / "manual_overrides.json"

    # Inexistente
    if ov_file.exists():
        ov_file.unlink()
    assert director._carregar_manual_overrides() == {}

    # JSON quebrado
    ov_file.write_text("{ json invalido corrupto ...", encoding="utf-8")
    assert director._carregar_manual_overrides() == {}

    # JSON sem chave 'cenas'
    ov_file.write_text(json.dumps({"outro": 123}), encoding="utf-8")
    assert director._carregar_manual_overrides() == {}


def test_manual_overrides_texto_contem_divergente_aborta():
    """Valida que divergência em texto_contem aborta o override da cena com erro."""
    director = BrainSystemDirector(TEST_PROJ)
    pdir = PROJETOS_DIR / TEST_PROJ

    ov_data = {
        "cenas": {
            "2": {
                "narrative_role": "AVATAR",
                "texto_contem": "palavra_completamente_inexistente_no_texto",
                "uses_character": True
            }
        }
    }
    (pdir / "manual_overrides.json").write_text(json.dumps(ov_data), encoding="utf-8")

    cenas = [
        {"id": 2, "narrative_role": "BROLL", "texto": "Organic compost delivers essential nutrients."}
    ]

    director._aplicar_manual_overrides(cenas)

    # Cena 2 NÃO deve ter sido alterada
    assert cenas[0].get("manual_override") is not True
    assert cenas[0].get("narrative_role") == "BROLL"


def test_manual_overrides_backup_reclassificacao_e_idempotencia():
    """Valida movimentação para _backup_reclassificacao/ e que marcador impede segunda movimentação."""
    director = BrainSystemDirector(TEST_PROJ)
    pdir = PROJETOS_DIR / TEST_PROJ

    # Prepara pastas cenas/ e conteudo/ com mídias antigas da cena 2
    cenas_dir = pdir / "cenas"
    conteudo_dir = pdir / "conteudo"
    cenas_dir.mkdir(parents=True, exist_ok=True)
    conteudo_dir.mkdir(parents=True, exist_ok=True)

    img_old = cenas_dir / "02_broll_antigo.png"
    vid_old = conteudo_dir / "02_broll_antigo.mp4"
    img_old.write_bytes(b"fake_png_data")
    vid_old.write_bytes(b"fake_mp4_data")

    ov_data = {
        "cenas": {
            "2": {
                "narrative_role": "AVATAR",
                "texto_contem": "organic",
                "uses_character": True
            }
        }
    }
    (pdir / "manual_overrides.json").write_text(json.dumps(ov_data), encoding="utf-8")

    cenas = [
        {"id": 2, "narrative_role": "BROLL", "texto": "Organic compost for garden.", "arquivo_midia": str(img_old)}
    ]

    # Primeira execução: move mídias e cria marcador
    director._aplicar_manual_overrides(cenas)

    backup_dir = pdir / "_backup_reclassificacao"
    assert backup_dir.exists()
    assert (backup_dir / "cenas_02_broll_antigo.png").exists()
    assert (backup_dir / "conteudo_02_broll_antigo.mp4").exists()
    assert not img_old.exists()
    assert not vid_old.exists()

    marcador = backup_dir / ".marcador_backup_cena_002.json"
    assert marcador.exists()
    assert cenas[0]["status_reset"] is True
    assert cenas[0]["status"] == "PENDENTE"
    assert cenas[0]["arquivo_midia"] == ""

    # Cria nova mídia na pasta cenas/ para a cena 2
    img_nova = cenas_dir / "02_avatar_novo.png"
    img_nova.write_bytes(b"novo_avatar_png")

    # Segunda execução: o marcador deve impedir nova movimentação
    director._aplicar_manual_overrides(cenas)
    assert img_nova.exists(), "Nova mídia da cena 2 não deve ser movida na segunda execução"


def test_manual_overrides_cena_1_aceita_override():
    """Valida que a Cena 1 aceita reclassificação e configuração por manual_overrides."""
    director = BrainSystemDirector(TEST_PROJ)
    pdir = PROJETOS_DIR / TEST_PROJ

    cenas_dir = pdir / "cenas"
    cenas_dir.mkdir(parents=True, exist_ok=True)
    midia_c1 = cenas_dir / "01_[00-00-00-12].mp4"
    midia_c1.write_bytes(b"video_cena_1_antigo")

    ov_data = {
        "cenas": {
            "1": {
                "narrative_role": "BROLL",
                "uses_character": False,
                "texto_contem": "hello"
            }
        }
    }
    (pdir / "manual_overrides.json").write_text(json.dumps(ov_data), encoding="utf-8")

    cenas = [
        {"id": 1, "narrative_role": "HOOK", "texto": "Hello I am Marcos", "uses_character": True, "arquivo_midia": str(midia_c1)}
    ]

    director._aplicar_manual_overrides(cenas)

    # Cena 1 aceita override normalmente
    assert cenas[0]["narrative_role"] == "BROLL"
    assert cenas[0]["uses_character"] is False
    assert cenas[0].get("manual_override") is True


def test_avatar_sem_override_recebe_base_third_person():
    """Valida que qualquer avatar sem override recebe a base Third-person demonstration e avatar_style."""
    director = BrainSystemDirector(TEST_PROJ)

    entrada = {
        "projeto_id": TEST_PROJ,
        "cenas": [
            {
                "id": 2,
                "start": 4.5,
                "end": 8.0,
                "texto": "Look closely at these tomato leaves and stem.",
                "narrative_role": "AVATAR",
                "scene_type": "avatar_hands_on",
                "uses_character": True,
                "character_ref": "@Marcos",
                "prompt_imagem": "Photorealistic cinematic portrait shot of @Marcos in rustic garden, 35mm lens.",
                "prompt_animacao": "Smooth cinematic slow push-in forward, speaking naturally."
            }
        ]
    }

    fase0 = director._fase_0_ingestao(entrada)
    decisoes = {
        "decisoes": [
            {
                "cena": entrada["cenas"][0],
                "animacao": {"should_animate": True, "motion_vector": "slow_dolly_push"},
                "transicao": {"tipo": "none", "duracao_ms": 0},
                "story_card_prompt": ""
            }
        ]
    }

    saida = director._fase_3_enriquecimento_atomico(
        entrada=entrada,
        decisoes=decisoes,
        memoria_visual=fase0["memoria_visual"],
        context_pack=fase0["context_pack"],
        options={}
    )

    c2 = saida["cenas"]["cena_2"]
    p_img = c2["prompts"]["prompt_imagem"]
    p_anim = c2["prompts"]["prompt_animacao"]

    assert "documentary third-person demonstration shot" in p_img
    assert "portrait shot" not in p_img
    assert "hands in foreground" in p_img.lower()
    assert "Third-person demonstration" in p_anim
    assert "Hands in foreground" in p_anim
    assert "speaking naturally" not in p_anim
    assert c2["narrativa"]["avatar_style"] == "third_person_hands_on"


def test_avatar_com_override_sem_duplicacao():
    """Valida que prompts que já possuem third-person não sofrem concatenação duplicada."""
    director = BrainSystemDirector(TEST_PROJ)

    cena_com_base = {
        "id": 4,
        "start": 10.0,
        "end": 14.0,
        "texto": "Examine the flowers carefully.",
        "narrative_role": "AVATAR",
        "scene_type": "avatar_hands_on",
        "uses_character": True,
        "character_ref": "@Marcos",
        "manual_override": True,
        "prompt_imagem": "Photorealistic documentary third-person demonstration shot of @Marcos in rustic garden setting, hands in foreground examining flowers, 35mm lens.",
        "prompt_animacao": "Third-person demonstration. Hands in foreground gently inspecting flowers. No face-to-camera dialogue. Subtle handheld sway."
    }

    decisoes = {
        "decisoes": [
            {
                "cena": cena_com_base,
                "animacao": {"should_animate": True, "motion_vector": "subtle_handheld_orbit"},
                "transicao": {"tipo": "none", "duracao_ms": 0},
                "story_card_prompt": ""
            }
        ]
    }

    saida = director._fase_3_enriquecimento_atomico(
        entrada={"cenas": [cena_com_base]},
        decisoes=decisoes,
        memoria_visual={},
        context_pack={"world": "rustic garden", "lighting": "natural daylight", "negative_constraints": []},
        options={}
    )

    c4 = saida["cenas"]["cena_4"]
    p_img = c4["prompts"]["prompt_imagem"]
    p_anim = c4["prompts"]["prompt_animacao"]

    # Deve conter a expressão exatamente UMA vez (sem duplicações)
    assert p_img.lower().count("third-person") == 1
    assert p_anim.lower().count("third-person") == 1
    assert p_anim.lower().count("hands in foreground") == 1
    assert c4["narrativa"]["avatar_style"] == "third_person_hands_on"


def test_cena_1_hook_recebe_base_automaticamente():
    """Valida que a Cena 1 (HOOK com avatar) herda a base third-person automaticamente."""
    director = BrainSystemDirector(TEST_PROJ)
    entrada = _criar_entrada_mock()

    fase0 = director._fase_0_ingestao(entrada)
    cenas = director._fase_1_analise_narrativa(entrada, fase0["memoria_visual"])
    decisoes = director._fase_2_decisoes_audiovisuais(entrada, cenas, options={})
    saida = director._fase_3_enriquecimento_atomico(
        entrada=entrada,
        decisoes=decisoes,
        memoria_visual=fase0["memoria_visual"],
        context_pack=fase0["context_pack"],
        options={}
    )

    c1 = saida["cenas"]["cena_1"]
    p_img = c1["prompts"]["prompt_imagem"]
    p_anim = c1["prompts"]["prompt_animacao"]

    # Cena 1 é HOOK com Marcos e uses_character=True
    assert "third-person" in p_img.lower()
    assert "portrait shot" not in p_img
    assert "hands in foreground" in p_img.lower()
    assert "Third-person demonstration" in p_anim
    assert "Hands in foreground" in p_anim
    assert c1["narrativa"]["avatar_style"] == "third_person_hands_on"


def test_broll_puro_nao_recebe_base_avatar():
    """Valida que B-Roll puro não recebe a base do avatar nem avatar_style."""
    director = BrainSystemDirector(TEST_PROJ)

    cena_broll = {
        "id": 5,
        "start": 15.0,
        "end": 18.0,
        "texto": "The organic compost enriches the soil naturally.",
        "narrative_role": "BROLL",
        "scene_type": "broll_macro",
        "uses_character": False,
        "character_ref": "",
        "broll_query": "organic compost dark soil",
        "prompt_imagem": "Photorealistic cinematic macro b-roll shot of organic compost, shallow depth of field, no human presence.",
        "prompt_animacao": "Slow cinematic camera glide reveal, natural atmospheric movement."
    }

    decisoes = {
        "decisoes": [
            {
                "cena": cena_broll,
                "animacao": {"should_animate": True, "motion_vector": "slow_dolly_push"},
                "transicao": {"tipo": "none", "duracao_ms": 0},
                "story_card_prompt": ""
            }
        ]
    }

    saida = director._fase_3_enriquecimento_atomico(
        entrada={"cenas": [cena_broll]},
        decisoes=decisoes,
        memoria_visual={},
        context_pack={"world": "rustic garden", "lighting": "natural daylight", "negative_constraints": []},
        options={}
    )

    c5 = saida["cenas"]["cena_5"]
    p_img = c5["prompts"]["prompt_imagem"]
    p_anim = c5["prompts"]["prompt_animacao"]

    assert "third-person" not in p_img.lower()
    assert "Third-person demonstration" not in p_anim
    assert c5["narrativa"].get("avatar_style") != "third_person_hands_on"


# =============================================================================
# TESTES DE VALIDAÇÃO DE CONTRATOS E GALERIAS DECLARATIVAS
# =============================================================================

def test_analisar_contexto_global_chamada_com_assinatura_correta():
    """Valida que _fase_0_ingestao passa (cenas: list, referencias: list, estilo_preset: dict) para analisar_contexto_global."""
    director = BrainSystemDirector(TEST_PROJ)
    entrada = _criar_entrada_mock()

    mock_cp = {
        "main_theme": "Compostagem de Tomates",
        "world": "Horta Orgânica",
        "lighting": "Luz suave matinal",
        "negative_constraints": ["no bad quality"]
    }

    with patch("services.brain_system_director.analisar_contexto_global", return_value=mock_cp) as mock_analisar:
        res = director._fase_0_ingestao(entrada)
        mock_analisar.assert_called_once()
        args, kwargs = mock_analisar.call_args

        # Valida tipagem e chaves obrigatórias dos argumentos passados
        cenas_arg = kwargs.get("cenas") if "cenas" in kwargs else args[0]
        refs_arg = kwargs.get("referencias") if "referencias" in kwargs else args[1]
        preset_arg = kwargs.get("estilo_preset") if "estilo_preset" in kwargs else args[2]

        assert isinstance(cenas_arg, list), "Primeiro argumento DEVE ser lista de cenas"
        assert len(cenas_arg) == 4
        assert "narration" in cenas_arg[0] or "texto" in cenas_arg[0]
        assert isinstance(refs_arg, list), "Segundo argumento DEVE ser lista de referências"
        assert isinstance(preset_arg, dict), "Terceiro argumento DEVE ser dicionário de preset"
        assert "style_lock" in preset_arg

        # Valida que o context pack do mock foi adotado sem fallback
        assert res["context_pack"] == mock_cp


def test_gerar_prompts_lote_chamada_com_assinatura_e_retorno_dict():
    """Valida que _fase_3_enriquecimento_atomico passa total_cenas e processa retorno dict com chave 'scenes'."""
    director = BrainSystemDirector(TEST_PROJ)
    entrada = _criar_entrada_mock()

    fase0 = director._fase_0_ingestao(entrada)
    cenas = director._fase_1_analise_narrativa(entrada, fase0["memoria_visual"])
    decisoes = director._fase_2_decisoes_audiovisuais(entrada, cenas, options={})

    # Simula resposta canônica do DeepSeek (dicionário com chave 'scenes')
    deepseek_response = {
        "scenes": [
            {
                "id": 1,
                "prompt_imagem": "Documentary shot of Marcos in lush garden, hands in foreground examining plants.",
                "prompt_animacao": "Third-person demonstration. Hands in foreground gently checking leaves."
            },
            {
                "id": 2,
                "prompt_imagem": "Macro b-roll shot of organic compost granular soil particles.",
                "prompt_animacao": "Slow cinematic push-in toward rich organic matter."
            }
        ],
        "usage": {"prompt_tokens": 150, "completion_tokens": 80, "total_tokens": 230},
        "tempo_resposta_s": 1.2
    }

    with patch("services.brain_system_director.gerar_prompts_lote", return_value=deepseek_response) as mock_lote:
        saida = director._fase_3_enriquecimento_atomico(
            entrada=entrada,
            decisoes=decisoes,
            memoria_visual=fase0["memoria_visual"],
            context_pack=fase0["context_pack"],
            options={}
        )

        mock_lote.assert_called_once()
        args, kwargs = mock_lote.call_args

        # Valida passagem correta de total_cenas
        total_cenas_arg = kwargs.get("total_cenas") if "total_cenas" in kwargs else (args[2] if len(args) > 2 else None)
        assert total_cenas_arg == 4, f"total_cenas deve ser 4, recebido: {total_cenas_arg}"

        # Valida que o prompt gerado pela IA foi atribuído corretamente
        c2 = saida["cenas"]["cena_2"]
        assert "organic compost granular soil" in c2["prompts"]["prompt_imagem"]


def test_galerias_json_carregadas_com_sucesso():
    """Valida carregamento resiliente das galerias declarativas em config/galerias/."""
    director = BrainSystemDirector(TEST_PROJ)

    # 1. Carregamento direto
    smart_fx = director._carregar_galeria("smart_fx_rules.json")
    assert smart_fx is not None
    assert "regras" in smart_fx
    assert "HOOK" in smart_fx["regras"]
    assert "STORY_CARD" in smart_fx["regras"]

    sfx = director._carregar_galeria("sfx_ruidos.json")
    assert sfx is not None
    assert "efeitos" in sfx
    assert "swoosh_transicao" in sfx["efeitos"]

    bgm = director._carregar_galeria("audio_bgm.json")
    assert bgm is not None
    assert "presets" in bgm
    assert "cinematic_ambient_garden_v2" in bgm["presets"]

    # 2. Arquivo inexistente retorna None sem crash
    invalido = director._carregar_galeria("arquivo_que_nao_existe_xyz.json")
    assert invalido is None



