import json
import time
from pathlib import Path
import pytest

import config
import services.playwright_flow as pw_flow
import services.api_v2 as v2
import services.scene_plan_service as scene_plan_svc
from app_web import app


@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    """Configura PROJETOS_DIR em tmp_path e limpa workers."""
    monkeypatch.setattr(config, "PROJETOS_DIR", tmp_path)
    monkeypatch.setattr(v2, "PROJETOS_DIR", tmp_path)
    monkeypatch.setattr(pw_flow, "PROJETOS_DIR", tmp_path)
    monkeypatch.setattr(scene_plan_svc, "PROJETOS_DIR", tmp_path)

    monkeypatch.setattr(pw_flow, "FLOW_QUEUE_BACKOFFS", [0, 0, 0])
    monkeypatch.setattr(pw_flow, "FLOW_QUEUE_INTERVALO_S", 0.05)

    with pw_flow._queue_workers_lock:
        for w in pw_flow._queue_workers.values():
            try:
                w.stop_processing()
            except Exception:
                pass
        pw_flow._queue_workers.clear()

    yield tmp_path

    with pw_flow._queue_workers_lock:
        for w in list(pw_flow._queue_workers.values()):
            try:
                w.stop_processing()
            except Exception:
                pass
        pw_flow._queue_workers.clear()


def _criar_projeto_teste(tmp_path, pid="proj_refazer_test"):
    pdir = tmp_path / pid
    cenas_dir = pdir / "cenas"
    cenas_dir.mkdir(parents=True, exist_ok=True)

    # Cria mídias de teste para cena 1 e cena 2
    f1 = cenas_dir / "01_[00-00-05].png"
    f1.write_bytes(b"PNG_DATA_SCENE_1_OLD" * 50)
    f2 = cenas_dir / "02_[00-05-10].png"
    f2.write_bytes(b"PNG_DATA_SCENE_2_STAYS" * 50)

    plano = {
        "versao": "2.0",
        "cenas": [
            {
                "id": 1,
                "scene_index": 1,
                "tempo_inicio": 0.0,
                "tempo_fim": 5.0,
                "texto": "Texto cena 1",
                "visual_prompt": "Cinematic shot of healthy tomato plants",
                "arquivo_midia": str(f1),
                "status": "BAIXADA",
                "image_status": "READY"
            },
            {
                "id": 2,
                "scene_index": 2,
                "tempo_inicio": 5.0,
                "tempo_fim": 10.0,
                "texto": "Texto cena 2",
                "visual_prompt": "Close up on tomato flower",
                "arquivo_midia": str(f2),
                "status": "BAIXADA",
                "image_status": "READY"
            }
        ]
    }
    (pdir / "lira_scene_plan.json").write_text(json.dumps(plano, indent=2), encoding="utf-8")

    fila = {
        "projeto_id": pid,
        "ativa": False,
        "pausada": False,
        "total": 2,
        "itens": [
            {
                "scene_id": 1,
                "id": 1,
                "posicao": 1,
                "modo": "imagem",
                "status": "concluida",
                "prompt": "Cinematic shot of healthy tomato plants"
            },
            {
                "scene_id": 2,
                "id": 2,
                "posicao": 2,
                "modo": "imagem",
                "status": "concluida",
                "prompt": "Close up on tomato flower"
            }
        ]
    }
    (pdir / "flow_queue.json").write_text(json.dumps(fila, indent=2), encoding="utf-8")
    return pid, pdir


def test_arquivar_midias_antigas_cena(setup_env):
    pid, pdir = _criar_projeto_teste(setup_env, "proj_unit_refazer")
    f1 = pdir / "cenas" / "01_[00-00-05].png"
    f2 = pdir / "cenas" / "02_[00-05-10].png"

    assert f1.exists()
    assert f2.exists()

    res = scene_plan_svc.arquivar_midias_antigas_cena(pid, 1)
    assert res["success"] is True
    assert len(res["arquivados"]) > 0

    # f1 deve ter sido movido de cenas/
    assert not f1.exists()
    # f2 da cena 2 DEVE ser preservado intacto
    assert f2.exists()

    # Verifica se o arquivo arquivado existe em _backup_refazer/
    backup_dir = pdir / "_backup_refazer"
    assert backup_dir.exists()
    backups = list(backup_dir.iterdir())
    assert len(backups) == 1
    assert "01_[00-00-05].png" in backups[0].name

    # Verifica status atualizado no lira_scene_plan.json
    plan_atual = scene_plan_svc.carregar_scene_plan(pid)
    c1 = plan_atual["cenas"][0]
    assert c1["arquivo_midia"] == ""
    assert c1["status"] == "GERANDO"
    assert c1["image_status"] == "GENERATING"


def test_endpoint_refazer(setup_env):
    pid, pdir = _criar_projeto_teste(setup_env, "proj_api_refazer")
    client = app.test_client()

    # Dispara endpoint /refazer para a cena 1
    res = client.post(f"/api/v2/producao/{pid}/cenas/1/refazer", json={"modo": "imagem"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["status"] == "GERANDO"
    assert data["cena_id"] == 1
    assert len(data["arquivos_arquivados"]) > 0

    # Verifica flow_queue.json: cena 1 deve estar "enfileirada" e fila ativa
    with open(pdir / "flow_queue.json", encoding="utf-8") as f:
        fila = json.load(f)

    assert fila["ativa"] is True
    c1_queue = [it for it in fila["itens"] if it["scene_id"] == 1][0]
    assert c1_queue["status"] == "enfileirada"
    assert c1_queue["tentativa_atual"] == 0

    # Verifica lira_scene_plan.json
    plan = scene_plan_svc.carregar_scene_plan(pid)
    assert plan["cenas"][0]["status"] == "GERANDO"
    assert plan["cenas"][0]["arquivo_midia"] == ""
