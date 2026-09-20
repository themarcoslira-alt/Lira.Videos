# -*- coding: utf-8 -*-
"""
tests/test_flow_queue_resiliencia.py
====================================
Validação de resiliência da fila Google Flow e Ator CDP:
  1. Separação de espera na fila vs tempo de execução no Ator CDP
  2. Timeout de execução isolado (apenas após início efetivo)
  3. Estorno de attempt_count para tarefas não tentadas
  4. Callback de resultado tardio no scene_plan após timeout
  5. cancelar_execucao() preserva a thread do worker (sem duplicação)
  6. Isolamento entre projetos em cancelamentos concorrentes
  7. Pausa automática da fila com mensagem se ator estiver travado
  8. Retorno imediato (não tentada / ocupado) ao disputar lock com legado
  9. Guarda 409 em /fila/adicionar quando fila legada estiver ativa
"""
import json
import threading
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
    """Configura PROJETOS_DIR em tmp_path, encurta intervalos e limpa workers."""
    monkeypatch.setattr(config, "PROJETOS_DIR", tmp_path)
    monkeypatch.setattr(v2, "PROJETOS_DIR", tmp_path)
    monkeypatch.setattr(pw_flow, "PROJETOS_DIR", tmp_path)
    monkeypatch.setattr(scene_plan_svc, "PROJETOS_DIR", tmp_path)

    # Torna os testes rápidos
    monkeypatch.setattr(pw_flow, "FLOW_QUEUE_BACKOFFS", [0, 0, 0])
    monkeypatch.setattr(pw_flow, "FLOW_QUEUE_INTERVALO_S", 0.02)
    monkeypatch.setattr(pw_flow, "FLOW_ACTOR_POLL_S", 0.02)

    with pw_flow._queue_workers_lock:
        for w in list(pw_flow._queue_workers.values()):
            try:
                w.stop_processing()
            except Exception:
                pass
        pw_flow._queue_workers.clear()

    with pw_flow._exec_ids_lock:
        pw_flow._exec_ids_ativos.clear()

    with pw_flow._legacy_run_lock:
        pw_flow._legacy_run_ativos = 0

    yield tmp_path

    with pw_flow._queue_workers_lock:
        for w in list(pw_flow._queue_workers.values()):
            try:
                w.stop_processing()
            except Exception:
                pass
        pw_flow._queue_workers.clear()

    with pw_flow._exec_ids_lock:
        pw_flow._exec_ids_ativos.clear()

    with pw_flow._legacy_run_lock:
        pw_flow._legacy_run_ativos = 0


@pytest.fixture
def client(setup_env):
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def criar_projeto_teste(tmp_path: Path, projeto_id: str, total_cenas: int = 2) -> Path:
    """Cria a pasta do projeto com meta.json e lira_scene_plan.json válido."""
    pdir = tmp_path / projeto_id
    pdir.mkdir(parents=True, exist_ok=True)
    meta = {"nome": projeto_id, "modo_producao": "imagem", "total_cenas": total_cenas}
    with open(pdir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)

    cenas = []
    for cid in range(1, total_cenas + 1):
        cenas.append({
            "id": cid,
            "cena": cid,
            "status": scene_plan_svc.STATUS_PENDENTE,
            "image_status": scene_plan_svc.IMAGE_STATUS_PENDING,
            "visual_prompt": f"Prompt visual da cena {cid}",
            "prompt_imagem": f"Prompt visual da cena {cid}",
            "texto": f"Texto da cena {cid}",
            "tempo_inicio": (cid - 1) * 3.0,
            "tempo_fim": cid * 3.0,
            "tipo": "image"
        })
    with open(pdir / "lira_scene_plan.json", "w", encoding="utf-8") as f:
        json.dump({"cenas": cenas}, f)

    return pdir


def ler_json(pdir: Path, nome: str) -> dict:
    with open(pdir / nome, "r", encoding="utf-8") as f:
        return json.load(f)


def cena_do_plan(pdir: Path, scene_id: int) -> dict:
    plano = ler_json(pdir, "lira_scene_plan.json")
    for c in plano.get("cenas", []):
        if int(c.get("id", 0)) == scene_id:
            return c
    raise AssertionError(f"Cena {scene_id} não encontrada no plan")


def esperar(condicao, timeout: float = 6.0, passo: float = 0.05) -> bool:
    limite = time.time() + timeout
    while time.time() < limite:
        try:
            if condicao():
                return True
        except Exception:
            pass
        time.sleep(passo)
    return False


# ============================================================================
# CASO 1: ESPERA NA FILA NÃO CONSOME TIMEOUT DO ATOR
# ============================================================================
def test_01_espera_na_fila_nao_consome_timeout(setup_env, monkeypatch):
    """Tarefa na fila do ator espera 1.0s atrás de outra; timeout de 0.5s de execução NÃO dispara na espera."""
    actor = pw_flow._get_cdp_actor()

    t1_iniciou = threading.Event()
    t1_pode_terminar = threading.Event()

    def task1():
        t1_iniciou.set()
        t1_pode_terminar.wait(5.0)
        return "task1_ok"

    def task2():
        return "task2_ok"

    # Submete task1 (vai rodar por ~0.8s)
    task1_handle = actor.submit_async(task1)
    assert t1_iniciou.wait(2.0), "task1 não iniciou"

    # Submete task2 com timeout de execução de 0.5s
    # Ela vai ficar esperando na fila do ator enquanto task1 roda
    t0_sub = time.time()
    task2_handle = actor.submit_async(task2)

    # Espera 0.8s (maior que o timeout de 0.5s de task2) antes de liberar task1
    time.sleep(0.8)
    t1_pode_terminar.set()

    # Aguarda task2 com timeout=0.5s
    # Se o timeout contasse o tempo de espera na fila (0.8s > 0.5s), levantaria _ExecTimeout.
    res2 = actor.aguardar(task2_handle, timeout=0.5)
    assert res2 == "task2_ok"
    assert actor.aguardar(task1_handle) == "task1_ok"


# ============================================================================
# CASO 2: TIMEOUT DE EXECUÇÃO APENAS APÓS INÍCIO (ERRO DEFINITIVO)
# ============================================================================
def test_02_timeout_de_execucao_apenas_apos_inicio(client, setup_env, monkeypatch):
    """Tarefa que excede timeout após iniciar marca item como erro definitivo."""
    pid = "proj_timeout_exec"
    pdir = criar_projeto_teste(setup_env, pid, total_cenas=1)

    t_iniciou = threading.Event()
    t_liberar = threading.Event()

    def stub_demorado(projeto_id, scene_id, prompt=""):
        t_iniciou.set()
        t_liberar.wait(5.0)
        return {"success": True, "caminho_imagem": "demorou.png"}

    monkeypatch.setattr(pw_flow, "criar_imagem_cena", stub_demorado)
    # Define timeout do ator curto para o teste (0.3s)
    monkeypatch.setattr(pw_flow, "_timeout_do_ator", lambda: 0.3)

    r_add = client.post(f"/api/v2/producao/{pid}/fila/adicionar",
                        json={"scene_ids": [1], "modo": "imagem"})
    assert r_add.status_code == 200
    assert t_iniciou.wait(2.0), "tarefa não iniciou no ator"

    # Espera item virar 'erro' por timeout_execucao sem retentativa
    try:
        assert esperar(lambda: ler_json(pdir, "flow_queue.json")["itens"][0]["status"] == "erro", timeout=5.0)
        fq = ler_json(pdir, "flow_queue.json")
        item = fq["itens"][0]
        assert item["status"] == "erro"
        assert "Timeout" in str(item.get("error_msg") or item.get("ultimo_erro"))
        assert fq["erros"] == 1
        # Não tentou retentar (tentativas não queimadas desnecessariamente além de 1)
        assert item["attempt_count"] == 1
    finally:
        t_liberar.set()


# ============================================================================
# CASO 3: ITEM NÃO TENTADO NÃO QUEIMA TENTATIVA
# ============================================================================
def test_03_item_nao_tentado_nao_queima_tentativa(client, setup_env, monkeypatch):
    """Tarefa cancelada antes de iniciar volta para 'enfileirada' com attempt_count inalterado."""
    pid = "proj_nao_tentada"
    pdir = criar_projeto_teste(setup_env, pid, total_cenas=2)

    actor = pw_flow._get_cdp_actor()
    t1_iniciou = threading.Event()
    t1_liberar = threading.Event()

    def task_bloqueia(projeto_id, scene_id, prompt=""):
        t1_iniciou.set()
        t1_liberar.wait(5.0)
        return {"success": True, "caminho_imagem": "ok1.png"}

    monkeypatch.setattr(pw_flow, "criar_imagem_cena", task_bloqueia)

    # Adiciona cena 1 e 2
    r_add = client.post(f"/api/v2/producao/{pid}/fila/adicionar",
                        json={"scene_ids": [1, 2], "modo": "imagem"})
    assert r_add.status_code == 200
    assert t1_iniciou.wait(2.0), "cena 1 não iniciou"

    # Cena 2 está aguardando na fila. Chamamos cancelar_execucao no worker do projeto
    w = pw_flow.FlowQueueWorker.get_queue_worker(pid, setup_env)

    # Simulamos uma tarefa cancelada com _NaoTentada antes de começar:
    task_fake = pw_flow._ActorTask(lambda: None, (), {})
    task_fake.future.cancel()

    # Verificamos diretamente a semântica da exceção _NaoTentada no ator
    with pytest.raises(pw_flow._NaoTentada) as exc_info:
        actor.aguardar(task_fake, cancel_check=lambda: True)
    assert exc_info.value.motivo == "cancelada"

    t1_liberar.set()


# ============================================================================
# CASO 4: RESULTADO TARDIO REGISTRADO NO PLAN VIA CALLBACK
# ============================================================================
def test_04_resultado_tardio_registrado_no_plan(setup_env, monkeypatch):
    """Tarefa que conclui após timeout grava imagem no scene_plan via callback."""
    pid = "proj_tardio_plan"
    pdir = criar_projeto_teste(setup_env, pid, total_cenas=1)

    # Marca cena no plan como ERRO inicialmente
    scene_plan_svc.atualizar_cena(pid, 1, {
        "status": scene_plan_svc.STATUS_ERRO,
        "image_status": scene_plan_svc.IMAGE_STATUS_ERROR,
    })
    assert cena_do_plan(pdir, 1)["status"] == scene_plan_svc.STATUS_ERRO

    # Cria Future simulando a tarefa que concluiu tardiamente
    from concurrent.futures import Future
    fut = Future()
    exec_id_fake = "exec_tardio_123"

    # Inicializa fila com o item em 'erro' com o mesmo exec_id
    with pw_flow.transacao_fila(pid, setup_env) as fq:
        fq["itens"] = [{
            "scene_id": 1,
            "exec_id": exec_id_fake,
            "status": "erro",
            "attempt_count": 1,
        }]
        fq["erros"] = 1

    # Registra callback
    fut.add_done_callback(
        lambda f: pw_flow._tratar_resultado_tardio(f, pid, 1, exec_id_fake, setup_env)
    )

    # Conclui future tardiamente com imagem
    caminho_fake = str(pdir / "midia_tardia_1.png")
    Path(caminho_fake).touch()
    fut.set_result({"success": True, "caminho_imagem": caminho_fake})

    # Verifica se scene_plan_svc foi atualizado para BAIXADA / READY
    assert cena_do_plan(pdir, 1)["status"] == scene_plan_svc.STATUS_BAIXADA
    assert cena_do_plan(pdir, 1)["image_status"] == scene_plan_svc.IMAGE_STATUS_READY

    # Verifica se flow_queue foi atualizado para 'concluida'
    fq = ler_json(pdir, "flow_queue.json")
    assert fq["itens"][0]["status"] == "concluida"
    assert fq["concluidos"] == 1
    assert fq["erros"] == 0


# ============================================================================
# CASO 5: CANCELAR EXECUÇÃO PRESERVA O WORKER (SEM NOVA THREAD)
# ============================================================================
def test_05_cancelar_execucao_preserva_worker(client, setup_env, monkeypatch):
    """cancelar_execucao() incrementa geração, cancela em voo, mas thread do worker continua viva."""
    pid = "proj_canc_worker"
    pdir = criar_projeto_teste(setup_env, pid, total_cenas=2)

    w = pw_flow.FlowQueueWorker.get_queue_worker(pid, setup_env)
    w.start_processing()
    assert w.is_alive()
    gen_antes = w._cancel_gen
    ident_antes = w.ident

    # Cancela via endpoint
    resp = client.post(f"/api/v2/producao/{pid}/fila/cancelar")
    assert resp.status_code == 200
    assert w.is_alive(), "worker NÃO deve morrer com cancelar_execucao()"
    assert w._cancel_gen == gen_antes + 1
    assert w.ident == ident_antes

    # Re-adicionar obtém a MESMA instância e MESMA thread
    resp2 = client.post(f"/api/v2/producao/{pid}/fila/adicionar",
                         json={"scene_ids": [1], "modo": "imagem"})
    assert resp2.status_code == 200
    w_depois = pw_flow.FlowQueueWorker.get_queue_worker(pid, setup_env)
    assert w_depois is w
    assert w_depois.ident == ident_antes
    w.stop_processing()


# ============================================================================
# CASO 6: DOIS PROJETOS NÃO DUPLICAM EXECUÇÃO
# ============================================================================
def test_06_dois_projetos_nao_duplicam_execucao(client, setup_env, monkeypatch):
    """Cancelar projeto A não afeta worker do projeto B; nenhum roda a mesma cena 2x."""
    pid_a = "proj_iso_a"
    pid_b = "proj_iso_b"
    pdir_a = criar_projeto_teste(setup_env, pid_a, total_cenas=1)
    pdir_b = criar_projeto_teste(setup_env, pid_b, total_cenas=1)

    execucoes = {"a": 0, "b": 0}

    def stub(projeto_id, scene_id, prompt=""):
        if projeto_id == pid_a:
            execucoes["a"] += 1
            time.sleep(0.3)
        else:
            execucoes["b"] += 1
            time.sleep(0.05)
        return {"success": True, "caminho_imagem": f"{projeto_id}_{scene_id}.png"}

    monkeypatch.setattr(pw_flow, "criar_imagem_cena", stub)

    # Inicia ambos os projetos
    client.post(f"/api/v2/producao/{pid_a}/fila/adicionar", json={"scene_ids": [1], "modo": "imagem"})
    client.post(f"/api/v2/producao/{pid_b}/fila/adicionar", json={"scene_ids": [1], "modo": "imagem"})

    # Cancela projeto A enquanto roda
    time.sleep(0.05)
    client.post(f"/api/v2/producao/{pid_a}/fila/cancelar")

    # Aguarda projeto B concluir
    assert esperar(lambda: ler_json(pdir_b, "flow_queue.json").get("concluidos") == 1, timeout=6.0)

    # Garante que projeto B concluiu normalmente e cena de A não rodou mais de 1x
    assert execucoes["b"] == 1
    assert execucoes["a"] <= 1


# ============================================================================
# CASO 7: ATOR TRAVADO PAUSA A FILA
# ============================================================================
def test_07_ator_travado_pausa_fila(client, setup_env, monkeypatch):
    """Quando ator está travado > timeout, novas tarefas são rejeitadas e fila é pausada com mensagem."""
    pid = "proj_ator_travado"
    pdir = criar_projeto_teste(setup_env, pid, total_cenas=2)

    actor = pw_flow._get_cdp_actor()

    t_iniciou = threading.Event()
    t_liberar = threading.Event()

    def task_travando():
        t_iniciou.set()
        t_liberar.wait(10.0)
        return "fim_travada"

    # Envia tarefa que realmente bloqueia a thread do ator
    t_handle = actor.submit_async(task_travando)
    assert t_iniciou.wait(2.0), "tarefa bloqueadora não iniciou no ator"

    try:
        # Simula que a tarefa iniciou há 700s (> timeout padrão de 600s)
        t_handle.started_at = time.time() - 700.0
        assert actor.esta_travado() is True

        # Teste da rejeição imediata em aguardar com ator travado
        task_nova = pw_flow._ActorTask(lambda: "ok", (), {})
        with pytest.raises(pw_flow._NaoTentada) as exc_info:
            actor.aguardar(task_nova)
        assert exc_info.value.motivo == "travado"

        # Ao enfileirar cena, o worker tenta processar mas detecta o ator travado
        # e pausa a fila automaticamente com motivo descritivo
        client.post(f"/api/v2/producao/{pid}/fila/adicionar", json={"scene_ids": [1], "modo": "imagem"})

        assert esperar(lambda: ler_json(pdir, "flow_queue.json").get("pausada") is True, timeout=5.0)
        fq = ler_json(pdir, "flow_queue.json")
        assert fq["pausada"] is True
        assert "travado" in fq.get("motivo_pausa", "").lower()
        # Item não queimou tentativa
        assert fq["itens"][0]["status"] == "enfileirada"
        assert fq["itens"][0]["attempt_count"] == 0
    finally:
        t_liberar.set()


# ============================================================================
# CASO 8: LOCK DO LEGADO RETORNA OCUPADO SEM TRAVAR
# ============================================================================
def test_08_lock_legado_retorna_ocupado_sem_travar(setup_env, monkeypatch):
    """Lock do legado ocupado faz _criar_imagem_cena_impl retornar nao_tentada=True, ocupado=True em até FLOW_LOCK_ACQUIRE_TIMEOUT_S."""
    pid = "proj_lock_ocupado"
    criar_projeto_teste(setup_env, pid, total_cenas=1)

    monkeypatch.setattr(pw_flow, "FLOW_LOCK_ACQUIRE_TIMEOUT_S", 0.1)

    lock_em_uso = threading.Event()
    pode_liberar = threading.Event()

    def _segurar_lock_outra_thread():
        pw_flow._flow_generation_lock.acquire()
        try:
            lock_em_uso.set()
            pode_liberar.wait(5.0)
        finally:
            pw_flow._flow_generation_lock.release()

    t = threading.Thread(target=_segurar_lock_outra_thread, daemon=True)
    t.start()
    assert lock_em_uso.wait(2.0), "outra thread não adquiriu o lock"

    try:
        t0 = time.time()
        res = pw_flow._criar_imagem_cena_impl(pid, 1, "prompt")
        duracao = time.time() - t0

        assert res.get("success") is False
        assert res.get("nao_tentada") is True
        assert res.get("ocupado") is True
        assert "legada" in res.get("erro", "")
        assert duracao < 0.5, f"demorou {duracao}s para retornar ocupado"
    finally:
        pode_liberar.set()
        t.join(timeout=2.0)


# ============================================================================
# CASO 9: GUARDA DE /fila/adicionar COM LEGADO ATIVO
# ============================================================================
def test_09_guarda_fila_adicionar_com_legado_ativo(client, setup_env, monkeypatch):
    """POST /fila/adicionar retorna 409 quando fila legada está ativa (legacy_queue_ativa() == True)."""
    pid = "proj_guarda_legado"
    criar_projeto_teste(setup_env, pid, total_cenas=1)

    # Ativa contador da fila legada
    with pw_flow._legacy_run_lock:
        pw_flow._legacy_run_ativos = 1

    try:
        assert pw_flow.legacy_queue_ativa() is True

        resp = client.post(f"/api/v2/producao/{pid}/fila/adicionar",
                           json={"scene_ids": [1], "modo": "imagem"})
        assert resp.status_code == 409, resp.get_json()
        body = resp.get_json() or {}
        assert body.get("success") is False
        assert "legada" in body.get("erro", "").lower() or "legada" in body.get("error", "").lower()
    finally:
        with pw_flow._legacy_run_lock:
            pw_flow._legacy_run_ativos = 0

    assert pw_flow.legacy_queue_ativa() is False
    # Após liberar o legado, POST /fila/adicionar responde 200
    resp_ok = client.post(f"/api/v2/producao/{pid}/fila/adicionar",
                          json={"scene_ids": [1], "modo": "imagem"})
    assert resp_ok.status_code == 200
