import json
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest

import config
import services.playwright_flow as pw_flow
import services.api_v2 as v2
import services.scene_plan_service as scene_plan_svc
from app_web import app


@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    """Configura PROJETOS_DIR em tmp_path, encurta intervalos e limpa workers."""
    monkeypatch.setattr(config, "PROJETOS_DIR", tmp_path)
    monkeypatch.setattr(v2, "PROJETOS_DIR", tmp_path)
    monkeypatch.setattr(pw_flow, "PROJETOS_DIR", tmp_path)
    monkeypatch.setattr(scene_plan_svc, "PROJETOS_DIR", tmp_path)

    # Torna os testes rápidos
    monkeypatch.setattr(pw_flow, "FLOW_QUEUE_BACKOFFS", [0, 0, 0])
    monkeypatch.setattr(pw_flow, "FLOW_QUEUE_INTERVALO_S", 0.05)

    # Limpa instâncias de workers anteriores
    with pw_flow._queue_workers_lock:
        for w in pw_flow._queue_workers.values():
            try:
                w.stop_processing()
            except Exception:
                pass
        pw_flow._queue_workers.clear()

    yield tmp_path

    # Cleanup ao término do teste
    with pw_flow._queue_workers_lock:
        for w in list(pw_flow._queue_workers.values()):
            try:
                w.stop_processing()
                w.join(timeout=1.0)
            except Exception:
                pass
        pw_flow._queue_workers.clear()


@pytest.fixture
def client():
    return app.test_client()


def criar_projeto_teste(tmp_path: Path, projeto_id: str, total_cenas: int = 3):
    pdir = tmp_path / projeto_id
    pdir.mkdir(parents=True, exist_ok=True)
    plan = {
        "projeto_id": projeto_id,
        "cenas": [
            {
                "id": i,
                "visual_prompt": f"Prompt visual da cena {i}",
                "status": "PENDENTE",
                "image_status": "PENDING"
            }
            for i in range(1, total_cenas + 1)
        ]
    }
    with open(pdir / "lira_scene_plan.json", "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)
    return pdir


# ============================================================================
# CASO 1: 50 POSTS CONCORRENTES EM /fila/adicionar COM WORKER ATIVO
# ============================================================================
def test_concorrencia_50_posts(client, setup_env, monkeypatch):
    """50 POSTs concorrentes em /fila/adicionar: esperar exatamente 51 itens únicos no arquivo."""
    pid = "proj_conc_50"
    pdir = criar_projeto_teste(setup_env, pid, total_cenas=60)

    def stub_sucesso(projeto_id, scene_id, prompt=""):
        time.sleep(0.02)
        return {"success": True, "caminho_imagem": f"projetos/{projeto_id}/cenas/{scene_id:03d}.png"}

    monkeypatch.setattr(pw_flow, "criar_imagem_cena", stub_sucesso)

    # Enfileira cena 1 para colocar o worker em execução ativa e salvamento
    r1 = client.post(f"/api/v2/producao/{pid}/fila/adicionar", json={"scene_ids": [1], "modo": "imagem"})
    assert r1.status_code == 200

    def disparar_post(scene_idx):
        c = app.test_client()
        res = c.post(f"/api/v2/producao/{pid}/fila/adicionar", json={"scene_ids": [scene_idx], "modo": "imagem"})
        return scene_idx, res.status_code, res.get_json()

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(disparar_post, i) for i in range(2, 52)]
        results = [f.result() for f in futures]

    for sid, status_code, data in results:
        assert status_code == 200, f"Falha na cena {sid}: {data}"
        assert data.get("success") is True

    # Aguarda o término ou timeout de 10s
    start_wait = time.time()
    while time.time() - start_wait < 10.0:
        s_data = client.get(f"/api/v2/producao/{pid}/fila_status").get_json()
        if s_data.get("concluidas") == 51:
            break
        time.sleep(0.1)

    with open(pdir / "flow_queue.json", "r", encoding="utf-8") as f:
        fq = json.load(f)

    itens = fq.get("itens", [])
    enqueued_ids = [int(it.get("scene_id") or it.get("id")) for it in itens]
    unique_ids = set(enqueued_ids)
    expected_ids = set(range(1, 52))

    assert len(itens) == 51, f"Esperado 51 itens, encontrado {len(itens)}"
    assert len(unique_ids) == 51, f"Esperado 51 itens únicos, encontrado {len(unique_ids)}"
    assert expected_ids - unique_ids == set(), f"Itens perdidos por Lost Update: {expected_ids - unique_ids}"


# ============================================================================
# CASO 2: FALHA PERMANENTE (EXATAMENTE 3 EXECUÇÕES)
# ============================================================================
def test_falha_permanente_3_tentativas(client, setup_env, monkeypatch):
    """Stub chamado exatamente 3 vezes; item final 'erro'; tentativa_atual == 3; plan com ERRO/ERROR."""
    pid = "proj_falha_3"
    pdir = criar_projeto_teste(setup_env, pid, total_cenas=1)
    call_count = 0

    def stub_falha(projeto_id, scene_id, prompt=""):
        nonlocal call_count
        call_count += 1
        return {"success": False, "erro": f"Erro persistente tentativa {call_count}"}

    monkeypatch.setattr(pw_flow, "criar_imagem_cena", stub_falha)

    res = client.post(f"/api/v2/producao/{pid}/fila/adicionar", json={"scene_ids": [1], "modo": "imagem"})
    assert res.status_code == 200

    start_wait = time.time()
    while time.time() - start_wait < 5.0:
        s_data = client.get(f"/api/v2/producao/{pid}/fila_status").get_json()
        if s_data.get("erros") == 1:
            break
        time.sleep(0.1)

    assert call_count == 3, f"Esperado exatamente 3 execuções (1 inicial + 2 retentativas), obtido {call_count}"

    with open(pdir / "flow_queue.json", "r", encoding="utf-8") as f:
        fq = json.load(f)
    item = fq["itens"][0]
    assert item["status"] == "erro"
    assert item["tentativa_atual"] == 3
    assert item["attempt_count"] == 3
    assert item["ultimo_erro"] == "Erro persistente tentativa 3"

    with open(pdir / "lira_scene_plan.json", "r", encoding="utf-8") as f:
        sp = json.load(f)
    cena_plan = sp["cenas"][0]
    assert cena_plan["status"] == "ERRO"
    assert cena_plan["image_status"] == "ERROR"


# ============================================================================
# CASO 3: PAUSA DURANTE CENA 1 E RETOMADA
# ============================================================================
def test_pausa_durante_cena_1_e_retomada(client, setup_env, monkeypatch):
    """Pausar durante a cena 1: cena 2 não inicia; 'pausada' continua True após conclusão da cena 1; retomar continua."""
    pid = "proj_pausa_retoma"
    pdir = criar_projeto_teste(setup_env, pid, total_cenas=3)

    def stub_lento(projeto_id, scene_id, prompt=""):
        time.sleep(0.2)
        return {"success": True, "caminho_imagem": f"img_{scene_id}.png"}

    monkeypatch.setattr(pw_flow, "criar_imagem_cena", stub_lento)

    client.post(f"/api/v2/producao/{pid}/fila/adicionar", json={"scene_ids": [1, 2, 3], "modo": "imagem"})
    time.sleep(0.05)  # Cena 1 iniciou

    # Pausa durante a cena 1
    r_pause = client.post(f"/api/v2/producao/{pid}/fila/pausar")
    assert r_pause.status_code == 200
    assert r_pause.get_json().get("pausada") is True

    # Aguarda a cena 1 terminar
    time.sleep(0.35)

    with open(pdir / "flow_queue.json", "r", encoding="utf-8") as f:
        fq = json.load(f)

    # Cena 1 concluiu, mas a fila deve continuar pausada e a cena 2 NÃO deve ter iniciado
    assert fq.get("pausada") is True
    assert fq["itens"][0]["status"] == "concluida"
    assert fq["itens"][1]["status"] == "enfileirada"
    assert fq.get("cena_ativa") is None

    # Retoma
    r_retoma = client.post(f"/api/v2/producao/{pid}/fila/retomar")
    assert r_retoma.status_code == 200

    # Aguarda todas as 3 concluírem
    start_wait = time.time()
    while time.time() - start_wait < 5.0:
        s_data = client.get(f"/api/v2/producao/{pid}/fila_status").get_json()
        if s_data.get("concluidas") == 3:
            break
        time.sleep(0.1)

    with open(pdir / "flow_queue.json", "r", encoding="utf-8") as f:
        fq_final = json.load(f)
    for it in fq_final["itens"]:
        assert it["status"] == "concluida"


# ============================================================================
# CASO 4: CANCELAMENTO DURANTE CENA 1 E REINÍCIO
# ============================================================================
def test_cancelamento_durante_cena_1_e_reinicio(client, setup_env, monkeypatch):
    """Cancelar durante a cena 1, depois adicionar 2 cenas: processa as 2 sem reiniciar o app."""
    pid = "proj_cancel_restart"
    pdir = criar_projeto_teste(setup_env, pid, total_cenas=5)

    def stub_lento(projeto_id, scene_id, prompt=""):
        time.sleep(0.2)
        return {"success": True, "caminho_imagem": f"img_{scene_id}.png"}

    monkeypatch.setattr(pw_flow, "criar_imagem_cena", stub_lento)

    client.post(f"/api/v2/producao/{pid}/fila/adicionar", json={"scene_ids": [1, 2, 3], "modo": "imagem"})
    time.sleep(0.05)

    # Cancela
    r_canc = client.post(f"/api/v2/producao/{pid}/fila/cancelar")
    assert r_canc.status_code == 200
    assert r_canc.get_json().get("fila_esvaziada") is True

    # Aguarda a cena 1 que estava em voo terminar
    time.sleep(0.3)

    # Adiciona cenas 4 e 5
    r_add = client.post(f"/api/v2/producao/{pid}/fila/adicionar", json={"scene_ids": [4, 5], "modo": "imagem"})
    assert r_add.status_code == 200
    assert r_add.get_json().get("enfileiradas") == 2

    # Aguarda conclusão
    start_wait = time.time()
    while time.time() - start_wait < 5.0:
        s_data = client.get(f"/api/v2/producao/{pid}/fila_status").get_json()
        if s_data.get("concluidas") == 2:
            break
        time.sleep(0.1)

    with open(pdir / "flow_queue.json", "r", encoding="utf-8") as f:
        fq = json.load(f)

    assert len(fq["itens"]) == 2
    assert fq["concluidos"] == 2
    assert [it["scene_id"] for it in fq["itens"]] == [4, 5]
    for it in fq["itens"]:
        assert it["status"] == "concluida"


# ============================================================================
# CASO 5: DOIS PROJETOS AO MESMO TEMPO (ISOLAMENTO E SERIALIZAÇÃO DE STUBS)
# ============================================================================
def test_dois_projetos_concorrentes_serializados(client, setup_env, monkeypatch):
    """Dois projetos (A e B) enfileirados ao mesmo tempo: filas isoladas e stubs nunca em paralelo."""
    pid_a = "proj_a"
    pid_b = "proj_b"
    pdir_a = criar_projeto_teste(setup_env, pid_a, total_cenas=3)
    pdir_b = criar_projeto_teste(setup_env, pid_b, total_cenas=3)

    active_calls = 0
    max_concurrent_calls = 0
    tracker_lock = threading.Lock()

    def stub_serializado(projeto_id, scene_id, prompt=""):
        nonlocal active_calls, max_concurrent_calls
        with tracker_lock:
            active_calls += 1
            if active_calls > max_concurrent_calls:
                max_concurrent_calls = active_calls
        time.sleep(0.08)
        with tracker_lock:
            active_calls -= 1
        return {"success": True, "caminho_imagem": f"img_{projeto_id}_{scene_id}.png"}

    monkeypatch.setattr(pw_flow, "criar_imagem_cena", stub_serializado)

    # Dispara os dois projetos simultaneamente
    r_a = client.post(f"/api/v2/producao/{pid_a}/fila/adicionar", json={"scene_ids": [1, 2, 3], "modo": "imagem"})
    r_b = client.post(f"/api/v2/producao/{pid_b}/fila/adicionar", json={"scene_ids": [1, 2, 3], "modo": "imagem"})
    assert r_a.status_code == 200
    assert r_b.status_code == 200

    # Aguarda término de ambos
    start_wait = time.time()
    while time.time() - start_wait < 8.0:
        s_a = client.get(f"/api/v2/producao/{pid_a}/fila_status").get_json()
        s_b = client.get(f"/api/v2/producao/{pid_b}/fila_status").get_json()
        if s_a.get("concluidas") == 3 and s_b.get("concluidas") == 3:
            break
        time.sleep(0.1)

    # Stubs nunca executam em paralelo devido ao _flow_generation_lock
    assert max_concurrent_calls == 1, f"Concorrência no stub detectada! max_concurrent_calls={max_concurrent_calls}"

    with open(pdir_a / "flow_queue.json", "r", encoding="utf-8") as f:
        fq_a = json.load(f)
    with open(pdir_b / "flow_queue.json", "r", encoding="utf-8") as f:
        fq_b = json.load(f)

    assert fq_a["projeto_id"] == pid_a
    assert fq_b["projeto_id"] == pid_b
    assert fq_a["concluidos"] == 3
    assert fq_b["concluidos"] == 3
    assert len(fq_a["itens"]) == 3
    assert len(fq_b["itens"]) == 3

    # Workers preservaram seus respectivos projeto_id
    w_a = pw_flow.FlowQueueWorker.get_queue_worker(pid_a)
    w_b = pw_flow.FlowQueueWorker.get_queue_worker(pid_b)
    assert w_a.projeto_id == pid_a
    assert w_b.projeto_id == pid_b


# ============================================================================
# CASO 6: READICIONAR UMA CENA EM 'ERRO' (RESETA SEM DUPLICAR)
# ============================================================================
def test_readicionar_cena_em_erro(client, setup_env, monkeypatch):
    """Readicionar uma cena em 'erro': reseta o item existente, não duplica, e o worker atualiza o item certo."""
    pid = "proj_readd_erro"
    pdir = criar_projeto_teste(setup_env, pid, total_cenas=1)

    falhar = True

    def stub_controlado(projeto_id, scene_id, prompt=""):
        if falhar:
            return {"success": False, "erro": "Erro temporario"}
        return {"success": True, "caminho_imagem": f"img_recuperada_{scene_id}.png"}

    monkeypatch.setattr(pw_flow, "criar_imagem_cena", stub_controlado)

    # 1. Enfileira cena 1 para falhar até virar 'erro'
    client.post(f"/api/v2/producao/{pid}/fila/adicionar", json={"scene_ids": [1], "modo": "imagem"})
    start_wait = time.time()
    while time.time() - start_wait < 5.0:
        s_data = client.get(f"/api/v2/producao/{pid}/fila_status").get_json()
        if s_data.get("erros") == 1:
            break
        time.sleep(0.1)

    with open(pdir / "flow_queue.json", "r", encoding="utf-8") as f:
        fq = json.load(f)
    assert len(fq["itens"]) == 1
    assert fq["itens"][0]["status"] == "erro"
    assert fq["itens"][0]["attempt_count"] == 3

    # 2. Agora muda stub para sucesso e readiciona a cena 1
    falhar = False
    r_readd = client.post(f"/api/v2/producao/{pid}/fila/adicionar", json={"scene_ids": [1], "modo": "imagem"})
    assert r_readd.status_code == 200
    assert r_readd.get_json().get("enfileiradas") == 1

    # Aguarda processamento
    start_wait = time.time()
    while time.time() - start_wait < 5.0:
        s_data = client.get(f"/api/v2/producao/{pid}/fila_status").get_json()
        if s_data.get("concluidas") == 1:
            break
        time.sleep(0.1)

    with open(pdir / "flow_queue.json", "r", encoding="utf-8") as f:
        fq_final = json.load(f)

    # Item NÃO pode ter sido duplicado no array de itens
    assert len(fq_final["itens"]) == 1
    item = fq_final["itens"][0]
    assert item["scene_id"] == 1
    assert item["status"] == "concluida"
    assert item["caminho_imagem"] == "img_recuperada_1.png"
    assert fq_final["concluidos"] == 1
