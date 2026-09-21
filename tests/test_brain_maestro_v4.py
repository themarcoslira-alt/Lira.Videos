"""
tests/test_brain_maestro_v4.py — Suíte de Testes Automatizados do Brain Maestro v4.0
=====================================================================================
Testa:
  1. Instanciação e estrutura inicial de progresso
  2. Validação de integridade de áudio (Etapa 1)
  3. Checkpoints atômicos e retomada
  4. Mecanismo de cancelamento gracioso
  5. State Machine completa (Etapas 1 a 8) com mocks dos sub-sistemas
  6. Endpoints Flask na API v2 (/maestro/iniciar, /maestro/status, /maestro/cancelar)
  7. Prevenção de concorrência e conflitos (HTTP 409)
"""

import os
import sys
import json
import time
import shutil
import pytest
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app_web import app
from config import PROJETOS_DIR
from services.brain_maestro_v4 import BrainMaestroV4
from services.brain_system_director import BrainSystemDirector
import services.scene_plan_service as scene_plan_svc
from services.api_v2 import (
    _MAESTRO_STATUS_LOCK,
    _MAESTRO_INSTANCES,
    _MAESTRO_THREADS,
    _MAESTRO_STATUS
)

TEST_MAESTRO_PROJ = "test_maestro_v4_unit_project"


@pytest.fixture(autouse=True)
def setup_and_teardown_test_env():
    """Garante limpeza do projeto de teste e dos globals do Maestro antes e depois de cada teste."""
    pdir = PROJETOS_DIR / TEST_MAESTRO_PROJ
    if pdir.exists():
        try:
            shutil.rmtree(pdir)
        except Exception:
            pass
    pdir.mkdir(parents=True, exist_ok=True)

    with _MAESTRO_STATUS_LOCK:
        _MAESTRO_INSTANCES.pop(TEST_MAESTRO_PROJ, None)
        _MAESTRO_THREADS.pop(TEST_MAESTRO_PROJ, None)
        _MAESTRO_STATUS.pop(TEST_MAESTRO_PROJ, None)

    yield

    with _MAESTRO_STATUS_LOCK:
        _MAESTRO_INSTANCES.pop(TEST_MAESTRO_PROJ, None)
        _MAESTRO_THREADS.pop(TEST_MAESTRO_PROJ, None)
        _MAESTRO_STATUS.pop(TEST_MAESTRO_PROJ, None)

    if pdir.exists():
        try:
            shutil.rmtree(pdir)
        except Exception:
            pass


def _criar_audio_mock(duracao_bytes=5000) -> Path:
    pdir = PROJETOS_DIR / TEST_MAESTRO_PROJ
    audio_path = pdir / f"{TEST_MAESTRO_PROJ}.mp3"
    audio_path.write_bytes(b"\xFF\xFB\x90\x44" + (b"\x00" * duracao_bytes))
    return audio_path


# =============================================================================
# TESTES DE CLASSE E UNIDADE
# =============================================================================

def test_maestro_instanciacao_e_progresso():
    maestro = BrainMaestroV4(projeto_id=TEST_MAESTRO_PROJ)
    assert maestro.projeto_id == TEST_MAESTRO_PROJ
    assert maestro.estado_atual == "IDLE"
    assert maestro.job_id.startswith(f"maestro_{TEST_MAESTRO_PROJ}")

    progresso = maestro.get_progresso()
    assert progresso["progresso_global_pct"] == 0
    assert len(progresso["sub_sistemas"]) == 8
    assert "validacao_audio" in progresso["sub_sistemas"]
    assert "whisper" in progresso["sub_sistemas"]
    assert "scene_builder" in progresso["sub_sistemas"]
    assert "brain_v3_core" in progresso["sub_sistemas"]
    assert "story_cards_refinement" in progresso["sub_sistemas"]
    assert "producao_flow" in progresso["sub_sistemas"]
    assert "audio_design" in progresso["sub_sistemas"]
    assert "capcut_draft" in progresso["sub_sistemas"]


def test_etapa_1_validacao_audio():
    maestro = BrainMaestroV4(projeto_id=TEST_MAESTRO_PROJ)

    # 1. Arquivo inexistente -> FileNotFoundError
    with pytest.raises(FileNotFoundError):
        maestro._etapa_1_validar_audio(str(PROJETOS_DIR / TEST_MAESTRO_PROJ / "inexistente.mp3"))

    # 2. Arquivo corrompido / excessivamente pequeno -> ValueError
    tiny_audio = PROJETOS_DIR / TEST_MAESTRO_PROJ / "tiny.mp3"
    tiny_audio.write_bytes(b"123")
    with pytest.raises(ValueError):
        maestro._etapa_1_validar_audio(str(tiny_audio))

    # 3. Áudio mock válido
    valid_audio = _criar_audio_mock(duracao_bytes=80000)
    info = maestro._etapa_1_validar_audio(str(valid_audio))
    assert info["valido"] is True
    assert info["duracao_s"] >= 2.0
    assert "validacao_audio" in maestro.etapas_concluidas
    assert maestro.progresso["validacao_audio"]["status"] == "concluido"


def test_checkpoints_atomicos():
    maestro = BrainMaestroV4(projeto_id=TEST_MAESTRO_PROJ)
    maestro.etapa_atual = "brain_v3_core"
    maestro.etapas_concluidas = ["validacao_audio", "whisper", "scene_builder"]

    ok = maestro._salvar_checkpoint(status="EM_ANDAMENTO", custom_metric=42)
    assert ok is True
    assert maestro.checkpoint_path.exists()

    chk = maestro._carregar_checkpoint()
    assert chk is not None
    assert chk["status"] == "EM_ANDAMENTO"
    assert chk["etapa_atual"] == "brain_v3_core"
    assert chk["custom_metric"] == 42
    assert "scene_builder" in chk["etapas_concluidas"]


def test_mecanismo_cancelamento():
    maestro = BrainMaestroV4(projeto_id=TEST_MAESTRO_PROJ)
    assert maestro._verificar_interrupcao("validacao_audio") is False

    maestro.cancelar()
    assert maestro.stop_event.is_set() is True
    assert maestro._verificar_interrupcao("whisper") is True
    assert maestro.estado_atual == "CANCELADO"
    assert maestro.progresso["whisper"]["status"] == "cancelado"


def test_state_machine_execucao_mockada():
    audio_path = _criar_audio_mock(duracao_bytes=50000)
    maestro = BrainMaestroV4(projeto_id=TEST_MAESTRO_PROJ)

    pdir = PROJETOS_DIR / TEST_MAESTRO_PROJ
    (pdir / "roteiro_transcricao.json").write_text(json.dumps({"segments": [{"text": "Hello world", "start": 0.0, "end": 4.0}], "duration": 4.0}), encoding="utf-8")
    (pdir / "word_timestamps.json").write_text(json.dumps({"segments": [{"words": [{"w": "Hello", "s": 0.0, "e": 1.0}]}]}), encoding="utf-8")
    (pdir / "cenas.json").write_text(json.dumps([{"id": 1, "tempo_inicio": 0.0, "tempo_fim": 4.0, "texto": "Hello world"}]), encoding="utf-8")
    scene_plan_svc.salvar_scene_plan(TEST_MAESTRO_PROJ, {
        "projeto": TEST_MAESTRO_PROJ,
        "cenas": [{"id": 1, "tempo_inicio": 0.0, "tempo_fim": 4.0, "texto": "Hello world"}]
    })

    # Mocks para sub-sistemas externos
    with patch("services.brain_maestro_v4.transcrever", return_value={"success": True, "segments": 1}), \
         patch("services.brain_maestro_v4.gerar_cenas", return_value={"success": True, "cenas": 1}), \
         patch.object(BrainSystemDirector, "orquestar_producao_completa", return_value={"cenas": {"cena_1": {}}}), \
         patch("services.brain_maestro_v4.detect_story_cards", return_value=[]), \
         patch("services.brain_maestro_v4.mixar_voz_e_musica_ffmpeg", return_value=True), \
         patch("services.brain_maestro_v4.criar_draft_imagens", return_value={"success": True, "draft_dir": "c:/mock_draft"}):

        res = maestro.executar(
            audio_path=str(audio_path),
            options={"pular_flow": True, "pasta_capcut": str(pdir)}
        )

        assert res["sucesso"] is True
        assert res["status"] == "CONCLUIDO"
        assert len(res["etapas_concluidas"]) == 8
        assert maestro.estado_atual == "CONCLUIDO"
        assert maestro.checkpoint_path.exists()


# =============================================================================
# TESTES DOS NOVOS ENDPOINTS DA API v2
# =============================================================================

def test_endpoints_iniciar_status_cancelar():
    client = app.test_client()
    audio_path = _criar_audio_mock(duracao_bytes=60000)

    worker_iniciado = threading.Event()
    worker_segurar = threading.Event()

    def fake_worker(projeto_id, maestro, audio_path, avatar_metadata, options):
        worker_iniciado.set()
        worker_segurar.wait(timeout=5.0)

    # 1. Iniciar Maestro em background -> HTTP 202
    with patch("services.api_v2._maestro_worker", side_effect=fake_worker):
        resp = client.post(
            f"/api/v2/producao/{TEST_MAESTRO_PROJ}/maestro/iniciar",
            json={"arquivo_audio": str(audio_path), "avatar": "@Marcos", "pular_flow": True}
        )
        assert resp.status_code == 202
        dados = resp.get_json()
        assert dados["success"] is True
        assert "job_id" in dados
        assert dados["status"] == "iniciado"

        # Aguarda thread começar a rodar
        worker_iniciado.wait(timeout=2.0)

        # 2. Concorrência: Iniciar novamente enquanto ativo -> HTTP 409
        resp_duplicado = client.post(
            f"/api/v2/producao/{TEST_MAESTRO_PROJ}/maestro/iniciar",
            json={"arquivo_audio": str(audio_path)}
        )
        assert resp_duplicado.status_code == 409
        dados_dup = resp_duplicado.get_json()
        assert dados_dup["codigo_erro"] == "MAESTRO_ALREADY_RUNNING"

        # 3. Polling de status -> HTTP 200
        resp_status = client.get(f"/api/v2/producao/{TEST_MAESTRO_PROJ}/maestro/status")
        assert resp_status.status_code == 200
        dados_st = resp_status.get_json()
        assert dados_st["success"] is True
        assert "sub_sistemas" in dados_st

        # 4. Cancelar execução ativa -> HTTP 200
        resp_cancelar = client.post(f"/api/v2/producao/{TEST_MAESTRO_PROJ}/maestro/cancelar")
        assert resp_cancelar.status_code == 200
        dados_canc = resp_cancelar.get_json()
        assert dados_canc["success"] is True
        assert dados_canc["status"] == "CANCELADO"

        # Libera o worker para encerrar
        worker_segurar.set()

        # Aguarda encerramento da thread
        time.sleep(0.3)

        # 5. Cancelar novamente quando já parado -> HTTP 409
        resp_cancelar_dup = client.post(f"/api/v2/producao/{TEST_MAESTRO_PROJ}/maestro/cancelar")
        assert resp_cancelar_dup.status_code == 409
        assert resp_cancelar_dup.get_json()["codigo_erro"] == "NO_ACTIVE_MAESTRO"

