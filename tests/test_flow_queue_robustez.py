# -*- coding: utf-8 -*-
"""
tests/test_flow_queue_robustez.py
=================================
Auditoria da fila assíncrona (Google Flow / Playwright CDP) — robustez.

Falhas cobertas (nenhuma delas era pega por testes com stub simples):
  1. AFINIDADE DE THREAD — o Playwright sync_api é thread-bound (greenlet): usar
     `page`/`browser` de outra thread levanta
     "Cannot switch to a different thread". Toda a automação passa a rodar numa
     ÚNICA thread dedicada (ator CDP) e sessões de outra thread são descartadas.
  2. EXCLUSÃO DO CAMINHO LEGADO — `_handle_run_queue` segura o mesmo
     `_flow_generation_lock`; `/iniciar_fila` responde 409 quando a fila
     assíncrona do projeto ainda tem item "enfileirada"/"gerando".
  3. exec_id + CURA DE ÓRFÃOS — item "gerando" sem execução viva (app reiniciado
     ou cancelado) volta para "enfileirada" (ou "erro", se esgotou tentativas);
     a Fase C localiza o item PELO exec_id (nunca por scene_id+status).
  4. CANCELAMENTO COERENTE — `/fila/cancelar` devolve a cena GERANDO do plan para
     PENDENTE; a conclusão tardia com sucesso regrava BAIXADA/READY; com falha,
     o plan não é tocado.
  5. POLLING SEM EFEITO COLATERAL — GET /fila_status não cria workers (peek).
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
    """Configura PROJETOS_DIR em tmp_path, encurta intervalos e limpa workers.

    Mesma base do tests/test_flow_queue_concorrencia.py.
    """
    monkeypatch.setattr(config, "PROJETOS_DIR", tmp_path)
    monkeypatch.setattr(v2, "PROJETOS_DIR", tmp_path)
    monkeypatch.setattr(pw_flow, "PROJETOS_DIR", tmp_path)
    monkeypatch.setattr(scene_plan_svc, "PROJETOS_DIR", tmp_path)

    # Torna os testes rápidos
    monkeypatch.setattr(pw_flow, "FLOW_QUEUE_BACKOFFS", [0, 0, 0])
    monkeypatch.setattr(pw_flow, "FLOW_QUEUE_INTERVALO_S", 0.05)

    with pw_flow._queue_workers_lock:
        for w in pw_flow._queue_workers.values():
            try:
                w.stop_processing()
            except Exception:
                pass
        pw_flow._queue_workers.clear()

    with pw_flow._exec_ids_lock:
        pw_flow._exec_ids_ativos.clear()

    yield tmp_path

    with pw_flow._queue_workers_lock:
        for w in list(pw_flow._queue_workers.values()):
            try:
                w.stop_processing()
                w.join(timeout=2.0)
            except Exception:
                pass
        pw_flow._queue_workers.clear()

    with pw_flow._exec_ids_lock:
        pw_flow._exec_ids_ativos.clear()


@pytest.fixture
def client():
    return app.test_client()


def criar_projeto_teste(tmp_path: Path, projeto_id: str, total_cenas: int = 3,
                        status_inicial: str = "PENDENTE"):
    pdir = tmp_path / projeto_id
    pdir.mkdir(parents=True, exist_ok=True)
    plan = {
        "projeto_id": projeto_id,
        "cenas": [
            {
                "id": i,
                "visual_prompt": f"Prompt visual da cena {i}",
                "status": status_inicial,
                "image_status": "PENDING"
            }
            for i in range(1, total_cenas + 1)
        ]
    }
    with open(pdir / "lira_scene_plan.json", "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)
    return pdir


def ler_json(pdir: Path, nome: str) -> dict:
    with open(pdir / nome, "r", encoding="utf-8") as f:
        return json.load(f)


def escrever_fila(pdir: Path, fila: dict):
    pdir.mkdir(parents=True, exist_ok=True)
    with open(pdir / "flow_queue.json", "w", encoding="utf-8") as f:
        json.dump(fila, f, indent=2, ensure_ascii=False)


def esperar(condicao, timeout=6.0, intervalo=0.02):
    """Aguarda a condição ficar verdadeira; devolve o último valor avaliado."""
    t0 = time.time()
    valor = None
    while time.time() - t0 < timeout:
        valor = condicao()
        if valor:
            return valor
        time.sleep(intervalo)
    return valor


def cena_do_plan(pdir: Path, cid: int) -> dict:
    plano = ler_json(pdir, "lira_scene_plan.json")
    for c in plano.get("cenas", []):
        if int(c.get("id", 0)) == int(cid):
            return c
    raise AssertionError(f"cena {cid} não encontrada no plan")


class _WorkerFalso:
    """Substituto do singleton PlaywrightCDPWorker COM afinidade de thread real.

    Qualquer uso de `page`/métodos numa thread diferente da que "criou" a sessão
    levanta RuntimeError("Cannot switch to a different thread") — exatamente o
    comportamento do Playwright sync_api (greenlet).
    """

    def __init__(self):
        self.page = None
        self.playwright = None
        self.browser = None
        self.context = None
        self.current_project_id = None
        self.current_flow_reference = None
        self._session_thread_ident = None
        self.identes_de_uso = []
        self.identes_de_sessao = []
        self.descartes = 0

    def _assert_thread(self):
        ident = threading.get_ident()
        if self._session_thread_ident is not None and self._session_thread_ident != ident:
            raise RuntimeError("Cannot switch to a different thread")

    def _iniciar_sessao_thread(self):
        self.identes_de_sessao.append(threading.get_ident())
        self._session_thread_ident = threading.get_ident()
        self.page = object()
        return True, "sessão fake"

    def _descartar_sessao_outra_thread(self):
        if self._session_thread_ident == threading.get_ident():
            return False
        self.descartes += 1
        self.page = None
        self._session_thread_ident = None
        return True

    def _check_is_active(self):
        self._assert_thread()
        return self.page is not None

    def _ensure_project_open(self, projeto_id, timeout_s=5, conta_id=None):
        self._assert_thread()
        return True

    def _processar_cena_individual(self, projeto_id, cena, is_anim=False):
        self._assert_thread()
        self.identes_de_uso.append(threading.get_ident())
        return True, f"cena_{int(cena.get('id'))}.png"


def _preparar_ator(monkeypatch, fake: _WorkerFalso):
    """Patcha o singleton do worker e o CDP (sem Chrome) para exercitar o ator real."""
    monkeypatch.setattr(pw_flow.FlowQueueWorker, "get_worker", lambda: fake)
    monkeypatch.setattr(pw_flow, "ensure_chrome_cdp", lambda *a, **k: (True, "cdp fake"))
    return fake


# ============================================================================
# CASO 1: AFINIDADE — TODA CHAMADA PLAYWRIGHT RODA NA THREAD DO ATOR
# ============================================================================
def test_01_afinidade_todas_as_chamadas_na_thread_do_ator(client, setup_env, monkeypatch):
    """2 projetos com workers reais (8 cenas) + 1 chamada direta de outra thread.

    Todas as execuções devem acontecer na MESMA thread (a do ator CDP) e NUNCA nas
    threads dos FlowQueueWorker — era lá que nascia o
    "Cannot switch to a different thread" (sessão de um worker usada por outro).
    """
    fake = _preparar_ator(monkeypatch, _WorkerFalso())

    pid_a, pid_b = "proj_afin_a", "proj_afin_b"
    criar_projeto_teste(setup_env, pid_a, total_cenas=4)
    criar_projeto_teste(setup_env, pid_b, total_cenas=4)

    r_a = client.post(f"/api/v2/producao/{pid_a}/fila/adicionar",
                      json={"scene_ids": [1, 2, 3, 4], "modo": "imagem"})
    r_b = client.post(f"/api/v2/producao/{pid_b}/fila/adicionar",
                      json={"scene_ids": [1, 2, 3, 4], "modo": "imagem"})
    assert r_a.status_code == 200 and r_b.status_code == 200

    w_a = pw_flow.FlowQueueWorker.get_queue_worker(pid_a)
    w_b = pw_flow.FlowQueueWorker.get_queue_worker(pid_b)

    # Chamada DIRETA de outra thread (fora dos workers) enquanto a fila roda.
    idents_diretos = []

    def _chamada_direta():
        idents_diretos.append(threading.get_ident())
        res = pw_flow.criar_imagem_cena(pid_a, 99, "prompt direto")
        assert res.get("success") is True, res
        assert "Cannot switch" not in str(res)

    t_direta = threading.Thread(target=_chamada_direta, daemon=True)
    t_direta.start()
    t_direta.join(timeout=20)

    def _ambos_concluidos():
        fq_a = pw_flow._ler_fila(pid_a, setup_env)
        fq_b = pw_flow._ler_fila(pid_b, setup_env)
        return fq_a.get("concluidos") == 4 and fq_b.get("concluidos") == 4

    assert esperar(_ambos_concluidos, timeout=20.0), "filas não concluíram"

    # 8 cenas da fila + 1 chamada direta = 9 execuções, TODAS na thread do ator.
    assert len(fake.identes_de_uso) >= 6
    idents_do_ator = set(fake.identes_de_uso)
    assert len(idents_do_ator) == 1, f"execuções espalhadas por threads: {idents_do_ator}"

    ident_ator = next(iter(idents_do_ator))
    assert ident_ator == pw_flow._get_cdp_actor().ident
    idents_dos_workers = {w_a.ident, w_b.ident} | set(idents_diretos)
    assert ident_ator not in idents_dos_workers, "chamada Playwright fora da thread do ator"
    # Nenhuma sessão ficou "presa" em thread de worker.
    assert set(fake.identes_de_sessao) == {ident_ator}


# ============================================================================
# CASO 2: SESSÃO ÓRFÃ (CRIADA POR THREAD MORTA) É DESCARTADA E RECRIADA
# ============================================================================
def test_02_sessao_orfa_de_thread_morta_e_descartada(setup_env, monkeypatch):
    """`page != None` com `_session_thread_ident` de uma thread JÁ MORTA.

    A guarda de afinidade descarta essa sessão e recria na thread do ator — sem a
    guarda, o primeiro uso levantaria "Cannot switch to a different thread".
    """
    fake = _preparar_ator(monkeypatch, _WorkerFalso())
    pid = "proj_sessao_orfa"
    criar_projeto_teste(setup_env, pid, total_cenas=1)

    ident_morto = []

    def _thread_que_morre():
        ident_morto.append(threading.get_ident())

    t = threading.Thread(target=_thread_que_morre, daemon=True)
    t.start()
    t.join(timeout=5)
    ident_da_thread_morta = ident_morto[0]

    # Sessão "viva" criada pela thread que já morreu.
    fake.page = object()
    fake._session_thread_ident = ident_da_thread_morta

    res = pw_flow.criar_imagem_cena(pid, 1, "prompt")

    assert res.get("success") is True, res
    assert fake.descartes == 1, "sessão de outra thread NÃO foi descartada"
    ident_ator = pw_flow._get_cdp_actor().ident
    assert fake._session_thread_ident == ident_ator
    assert fake.identes_de_sessao == [ident_ator]
    assert ident_ator != ident_da_thread_morta


# ============================================================================
# CASO 3: CAMINHO LEGADO EXCLUÍDO (LOCK GLOBAL + 409 EM /iniciar_fila)
# ============================================================================
def test_03_legado_bloqueia_o_ator_e_iniciar_fila_responde_409(client, setup_env, monkeypatch):
    """(a) thread segurando _flow_generation_lock bloqueia a tarefa do ator;
    (b) /iniciar_fila responde 409 quando a fila assíncrona tem item enfileirado."""
    fake = _preparar_ator(monkeypatch, _WorkerFalso())
    pid = "proj_legado"
    criar_projeto_teste(setup_env, pid, total_cenas=1)

    # ---------- (a) LOCK DO LEGADO x TAREFA DO ATOR ----------
    lock_em_uso = threading.Event()
    pode_liberar = threading.Event()

    def _segurar_lock_como_legado():
        # _handle_run_queue adquire o lock NO INÍCIO e libera no finally — aqui
        # simulamos exatamente essa janela.
        pw_flow._flow_generation_lock.acquire()
        try:
            lock_em_uso.set()
            pode_liberar.wait(10)
        finally:
            pw_flow._flow_generation_lock.release()

    t_legado = threading.Thread(target=_segurar_lock_como_legado, daemon=True)
    t_legado.start()
    assert lock_em_uso.wait(5)

    resultado = {}

    def _chamar_pela_fila():
        resultado["res"] = pw_flow.criar_imagem_cena(pid, 1, "prompt")

    t_chamador = threading.Thread(target=_chamar_pela_fila, daemon=True)
    t_chamador.start()
    time.sleep(0.4)
    assert t_chamador.is_alive(), "a tarefa do ator NÃO foi bloqueada pelo lock do legado"

    pode_liberar.set()
    t_chamador.join(timeout=20)
    assert not t_chamador.is_alive()
    assert resultado["res"].get("success") is True, resultado["res"]
    t_legado.join(timeout=5)
    # O lock fica livre (o caminho libera no finally).
    assert pw_flow._flow_generation_lock.acquire(timeout=1)
    pw_flow._flow_generation_lock.release()

    # ---------- (b) 409 COM ITEM "enfileirada" NA FILA ASSÍNCRONA ----------
    pid2 = "proj_legado_409"
    pdir2 = criar_projeto_teste(setup_env, pid2, total_cenas=1)
    escrever_fila(pdir2, {
        "projeto_id": pid2, "criado_em": "x", "atualizado_em": "x",
        "itens": [
            {"scene_id": 1, "id": 1, "status": "enfileirada", "attempt_count": 0, "max_tentativas": 3}
        ],
        "cena_ativa": None, "total": 1, "concluidos": 0, "erros": 0
    })

    chamadas_start = []

    def _start_worker_proibido(*a, **k):
        chamadas_start.append((a, k))
        raise AssertionError("start_worker NÃO podia ser chamado (caminho legado)")

    monkeypatch.setattr(pw_flow.FlowQueueWorker, "start_worker", staticmethod(_start_worker_proibido))

    resp = client.post(f"/api/v2/producao/{pid2}/iniciar_fila")
    assert resp.status_code == 409, resp.get_json()
    body = resp.get_json() or {}
    assert body.get("success") is False
    assert body.get("erro") == "Fila assíncrona em andamento neste projeto; aguarde ou cancele."
    # 'error' espelha o mesmo texto (campo lido pelo app.js).
    assert body.get("error") == body.get("erro")
    assert chamadas_start == [], "o caminho legado foi iniciado mesmo com a fila assíncrona ativa"
    # Nada foi tocado na fila (nem iniciado).
    assert ler_json(pdir2, "flow_queue.json")["itens"][0]["status"] == "enfileirada"


# ============================================================================
# CASO 4: FASE C POR exec_id (NUNCA POR scene_id) — MUTANTE MORRE AQUI
# ============================================================================
def test_04_fase_c_localiza_item_por_exec_id(client, setup_env, monkeypatch):
    """2 itens com scene_id=1: o 1º "erro" (exec_id antigo), o 2º "enfileirada".

    A execução deve atualizar SÓ o item da PRÓPRIA execução (o 2º): o 1º permanece
    "erro" idêntico. Uma Fase C por scene_id atualizaria o item errado.
    """
    pid = "proj_exec_id"
    pdir = criar_projeto_teste(setup_env, pid, total_cenas=1)

    item_erro = {
        "scene_id": 1, "id": 1, "posicao": 1, "modo": "imagem",
        "status": "erro", "exec_id": "exec_antigo_0001",
        "attempt_count": 3, "tentativa_atual": 3, "max_tentativas": 3,
        "error_msg": "Erro persistente", "ultimo_erro": "Erro persistente"
    }
    item_novo = {
        "scene_id": 1, "id": 1, "posicao": 2, "modo": "imagem",
        "status": "enfileirada", "exec_id": None,
        "attempt_count": 0, "tentativa_atual": 0, "max_tentativas": 3,
        "error_msg": "", "ultimo_erro": None, "prompt": "Prompt visual da cena 1"
    }
    escrever_fila(pdir, {
        "projeto_id": pid, "criado_em": "x", "atualizado_em": "x",
        "itens": [item_erro, item_novo],
        "cena_ativa": None, "total": 2, "concluidos": 0, "erros": 1,
        "ativa": True, "pausada": False
    })

    monkeypatch.setattr(pw_flow, "criar_imagem_cena",
                        lambda projeto_id, scene_id, prompt="": {"success": True, "caminho_imagem": "nova.png"})

    w = pw_flow.FlowQueueWorker.get_queue_worker(pid, setup_env)
    w.start_processing()

    def _concluiu():
        fq = ler_json(pdir, "flow_queue.json")
        return len(fq["itens"]) == 2 and fq["itens"][1].get("status") == "concluida"

    assert esperar(_concluiu, timeout=8.0), ler_json(pdir, "flow_queue.json")
    w.stop_processing()

    fq = ler_json(pdir, "flow_queue.json")
    i0, i1 = fq["itens"][0], fq["itens"][1]

    # 1º item: INTACTO (nenhum campo tocado — nem o exec_id antigo).
    assert i0["status"] == "erro"
    assert i0["exec_id"] == "exec_antigo_0001"
    assert i0["error_msg"] == "Erro persistente"
    assert i0["attempt_count"] == 3
    # 2º item: atualizado pela execução dona do exec_id.
    assert i1["status"] == "concluida"
    assert i1["caminho_imagem"] == "nova.png"
    assert i1["exec_id"] not in (None, "", "exec_antigo_0001")
    assert fq["concluidos"] == 1
    assert fq["erros"] == 1


# ============================================================================
# CASO 5: CONCLUSÃO TARDIA DE CENA CANCELADA E RE-ADICIONADA
# ============================================================================
def test_05_conclusao_tardia_nao_sobrescreve_a_nova_execucao(client, setup_env, monkeypatch):
    """1ª execução presa no stub → cancelar → re-adicionar (novo worker) → 2ª conclui.

    Ao liberar a 1ª, ela NÃO pode achar o item (exec_id antigo) nem sobrescrever o
    resultado da 2ª execução; concluidos permanece 1.

    NOTA (divergência documentada): o ator CDP é UMA thread — a 2ª chamada é
    enfileirada atrás da 1ª (nenhuma geração roda em paralelo, o que é exigido
    também pelo test_flow_queue_concorrencia). Por isso a 2ª chamada executa
    imediatamente APÓS a liberação da 1ª, e não "em paralelo rápido" a ela.
    """
    pid = "proj_tardia"
    pdir = criar_projeto_teste(setup_env, pid, total_cenas=1)

    bloqueado = threading.Event()
    liberar_1 = threading.Event()
    chamadas = {"n": 0}

    def stub(projeto_id, scene_id, prompt=""):
        chamadas["n"] += 1
        n = chamadas["n"]
        if n == 1:
            bloqueado.set()
            liberar_1.wait(10)
            return {"success": True, "caminho_imagem": "caminho_1a_execucao.png"}
        return {"success": True, "caminho_imagem": "caminho_2a_execucao.png"}

    monkeypatch.setattr(pw_flow, "criar_imagem_cena", stub)

    r_add = client.post(f"/api/v2/producao/{pid}/fila/adicionar",
                        json={"scene_ids": [1], "modo": "imagem"})
    assert r_add.status_code == 200
    assert bloqueado.wait(5), "a 1ª execução não chegou ao stub"
    exec_id_1 = ler_json(pdir, "flow_queue.json")["itens"][0]["exec_id"]

    # Cancela enquanto a 1ª execução está presa; o plan volta para PENDENTE.
    r_canc = client.post(f"/api/v2/producao/{pid}/fila/cancelar")
    assert r_canc.status_code == 200
    assert cena_do_plan(pdir, 1)["status"] == scene_plan_svc.STATUS_PENDENTE

    # Re-adiciona a MESMA cena: worker preservado (o cancelamento não encerra a thread).
    w_antigo = pw_flow.FlowQueueWorker.get_queue_worker(pid, setup_env)
    r_add2 = client.post(f"/api/v2/producao/{pid}/fila/adicionar",
                         json={"scene_ids": [1], "modo": "imagem"})
    assert r_add2.status_code == 200
    w_novo = pw_flow.FlowQueueWorker.get_queue_worker(pid, setup_env)
    assert w_novo is w_antigo, "o worker deve ser preservado após cancelar"

    def _nova_execucao_em_voo():
        fq = ler_json(pdir, "flow_queue.json")
        itens = fq.get("itens", [])
        return bool(itens) and itens[0].get("status") == "gerando" and itens[0].get("exec_id") != exec_id_1

    assert esperar(_nova_execucao_em_voo, timeout=8.0), ler_json(pdir, "flow_queue.json")
    fq = ler_json(pdir, "flow_queue.json")
    exec_id_2 = fq["itens"][0]["exec_id"]
    assert exec_id_2 and exec_id_2 != exec_id_1

    # A 2ª execução NÃO roda em paralelo com a 1ª (ator = uma thread por vez).
    time.sleep(0.2)
    assert chamadas["n"] == 1, "duas gerações rodaram em paralelo (o ator é uma thread)"

    # Libera a 1ª execução (conclusão TARDIA) — a 2ª roda em seguida.
    liberar_1.set()

    def _concluiu_2a():
        fq = ler_json(pdir, "flow_queue.json")
        itens = fq.get("itens", [])
        return bool(itens) and itens[0].get("status") == "concluida" and fq.get("concluidos") == 1

    assert esperar(_concluiu_2a, timeout=8.0), ler_json(pdir, "flow_queue.json")
    assert chamadas["n"] >= 2

    fq = ler_json(pdir, "flow_queue.json")
    assert len(fq["itens"]) == 1
    assert fq["itens"][0]["status"] == "concluida"
    assert fq["itens"][0]["caminho_imagem"] == "caminho_2a_execucao.png"
    assert fq["itens"][0]["exec_id"] == exec_id_2
    assert fq["concluidos"] == 1, "a conclusão tardia mexeu no contador"
    assert cena_do_plan(pdir, 1)["status"] == scene_plan_svc.STATUS_BAIXADA


# ============================================================================
# CASO 6: CURA DE ÓRFÃOS ("gerando" SEM EXECUÇÃO VIVA)
# ============================================================================
def test_06a_orfaos_volta_para_fila_e_tudo_e_processado(client, setup_env, monkeypatch):
    """(a) "gerando" sem execução viva + "enfileirada" → curados e processados."""
    pid = "proj_orfa_a"
    pdir = criar_projeto_teste(setup_env, pid, total_cenas=2)
    # A cena 1 ficou GERANDO no plan (app morreu durante a geração).
    plano = ler_json(pdir, "lira_scene_plan.json")
    plano["cenas"][0]["status"] = scene_plan_svc.STATUS_GERANDO
    plano["cenas"][0]["image_status"] = scene_plan_svc.IMAGE_STATUS_GENERATING
    with open(pdir / "lira_scene_plan.json", "w", encoding="utf-8") as f:
        json.dump(plano, f, indent=2)

    escrever_fila(pdir, {
        "projeto_id": pid, "criado_em": "x", "atualizado_em": "x",
        "itens": [
            {"scene_id": 1, "id": 1, "posicao": 1, "status": "gerando",
             "exec_id": "exec_de_um_app_que_morreu", "attempt_count": 1,
             "tentativa_atual": 1, "max_tentativas": 3, "prompt": "Prompt visual da cena 1"},
            {"scene_id": 2, "id": 2, "posicao": 2, "status": "enfileirada",
             "exec_id": None, "attempt_count": 0, "tentativa_atual": 0,
             "max_tentativas": 3, "prompt": "Prompt visual da cena 2"},
        ],
        "cena_ativa": {"scene_id": 1, "status": "gerando", "exec_id": "exec_de_um_app_que_morreu"},
        "total": 2, "concluidos": 0, "erros": 0, "ativa": True, "pausada": False
    })

    curados = pw_flow._curar_orfaos_fila(pid, setup_env)
    assert curados == [(1, "enfileirada")], curados
    fq = ler_json(pdir, "flow_queue.json")
    assert fq["itens"][0]["status"] == "enfileirada"
    assert fq["itens"][0]["exec_id"] is None
    assert fq["cena_ativa"] is None
    assert cena_do_plan(pdir, 1)["status"] == scene_plan_svc.STATUS_PENDENTE
    assert cena_do_plan(pdir, 1)["image_status"] == scene_plan_svc.IMAGE_STATUS_PENDING

    # Agora a fila processa as DUAS cenas normalmente.
    monkeypatch.setattr(pw_flow, "criar_imagem_cena",
                        lambda projeto_id, scene_id, prompt="": {"success": True, "caminho_imagem": f"ok_{scene_id}.png"})
    w = pw_flow.FlowQueueWorker.get_queue_worker(pid, setup_env)
    w.start_processing()
    assert esperar(lambda: ler_json(pdir, "flow_queue.json").get("concluidos") == 2, timeout=8.0), \
        ler_json(pdir, "flow_queue.json")
    w.stop_processing()
    w.join(timeout=2.0)

    fq = ler_json(pdir, "flow_queue.json")
    assert [it["status"] for it in fq["itens"]] == ["concluida", "concluida"]
    assert cena_do_plan(pdir, 1)["status"] == scene_plan_svc.STATUS_BAIXADA
    assert cena_do_plan(pdir, 2)["status"] == scene_plan_svc.STATUS_BAIXADA


def test_06b_orfao_sem_tentativas_vira_erro(client, setup_env, monkeypatch):
    """(b) órfão com attempt_count == max_tentativas → "erro" e plan ERRO/ERROR."""
    pid = "proj_orfa_b"
    pdir = criar_projeto_teste(setup_env, pid, total_cenas=1)
    plano = ler_json(pdir, "lira_scene_plan.json")
    plano["cenas"][0]["status"] = scene_plan_svc.STATUS_GERANDO
    plano["cenas"][0]["image_status"] = scene_plan_svc.IMAGE_STATUS_GENERATING
    with open(pdir / "lira_scene_plan.json", "w", encoding="utf-8") as f:
        json.dump(plano, f, indent=2)

    escrever_fila(pdir, {
        "projeto_id": pid, "criado_em": "x", "atualizado_em": "x",
        "itens": [
            {"scene_id": 1, "id": 1, "posicao": 1, "status": "gerando",
             "exec_id": "exec_morto", "attempt_count": 3, "tentativa_atual": 3,
             "max_tentativas": 3, "prompt": "Prompt visual da cena 1"},
        ],
        "cena_ativa": {"scene_id": 1, "status": "gerando", "exec_id": "exec_morto"},
        "total": 1, "concluidos": 0, "erros": 0, "ativa": True, "pausada": False
    })

    curados = pw_flow._curar_orfaos_fila(pid, setup_env)
    assert curados == [(1, "erro")], curados

    fq = ler_json(pdir, "flow_queue.json")
    assert fq["itens"][0]["status"] == "erro"
    assert fq["itens"][0]["error_msg"] == "Interrompida (app reiniciado ou cancelada) sem tentativas restantes"
    assert fq["itens"][0]["ultimo_erro"] == fq["itens"][0]["error_msg"]
    assert fq["erros"] == 1
    assert fq["cena_ativa"] is None

    cena = cena_do_plan(pdir, 1)
    assert cena["status"] == scene_plan_svc.STATUS_ERRO
    assert cena["image_status"] == scene_plan_svc.IMAGE_STATUS_ERROR


def test_06c_orfao_com_execucao_viva_nao_e_curado(client, setup_env, monkeypatch):
    """(c) _curar_orfaos_fila com stub bloqueado (execução VIVA) → item continua "gerando"."""
    pid = "proj_orfa_c"
    pdir = criar_projeto_teste(setup_env, pid, total_cenas=1)

    bloqueado = threading.Event()
    liberar = threading.Event()

    def stub(projeto_id, scene_id, prompt=""):
        bloqueado.set()
        liberar.wait(10)
        return {"success": True, "caminho_imagem": "x.png"}

    monkeypatch.setattr(pw_flow, "criar_imagem_cena", stub)

    r_add = client.post(f"/api/v2/producao/{pid}/fila/adicionar",
                        json={"scene_ids": [1], "modo": "imagem"})
    assert r_add.status_code == 200
    assert bloqueado.wait(5), "a execução não foi iniciada"

    fq = ler_json(pdir, "flow_queue.json")
    assert fq["itens"][0]["status"] == "gerando"
    exec_id_vivo = fq["itens"][0]["exec_id"]
    with pw_flow._exec_ids_lock:
        assert exec_id_vivo in pw_flow._exec_ids_ativos

    # Existe execução VIVA dona do item → a cura NÃO pode tocar em nada.
    curados = pw_flow._curar_orfaos_fila(pid, setup_env)
    assert curados == []
    fq2 = ler_json(pdir, "flow_queue.json")
    assert fq2["itens"][0]["status"] == "gerando"
    assert fq2["itens"][0]["exec_id"] == exec_id_vivo
    assert fq2["cena_ativa"] is not None

    liberar.set()


# ============================================================================
# CASO 7: CANCELAR × PLAN (PENDENTE -> CONCLUSÃO TARDIA -> BAIXADA/READY)
# ============================================================================
def test_07_cancelar_sincroniza_o_plan_e_a_conclusao_tardia_decide(client, setup_env, monkeypatch):
    """Cancelar durante a geração → plan PENDENTE; liberar com SUCESSO → BAIXADA/READY;
    repetir com FALHA → plan permanece PENDENTE."""
    pid = "proj_cancel_plan"
    pdir = criar_projeto_teste(setup_env, pid, total_cenas=1)

    estado = {"bloqueado": threading.Event(), "liberar": threading.Event(), "sucesso": True}
    chamadas = {"n": 0}

    def stub(projeto_id, scene_id, prompt=""):
        chamadas["n"] += 1
        estado["bloqueado"].set()
        estado["liberar"].wait(10)
        if estado["sucesso"]:
            return {"success": True, "caminho_imagem": f"cena_{chamadas['n']}.png"}
        return {"success": False, "erro": "falha simulada"}

    monkeypatch.setattr(pw_flow, "criar_imagem_cena", stub)

    # ---------- RODADA 1: sucesso tardio ----------
    r_add = client.post(f"/api/v2/producao/{pid}/fila/adicionar",
                        json={"scene_ids": [1], "modo": "imagem"})
    assert r_add.status_code == 200
    assert estado["bloqueado"].wait(5)
    assert cena_do_plan(pdir, 1)["status"] == scene_plan_svc.STATUS_GERANDO

    assert client.post(f"/api/v2/producao/{pid}/fila/cancelar").status_code == 200
    cena = cena_do_plan(pdir, 1)
    assert cena["status"] == scene_plan_svc.STATUS_PENDENTE
    assert cena["image_status"] == scene_plan_svc.IMAGE_STATUS_PENDING

    estado["liberar"].set()
    t0 = time.time()
    pronto = esperar(
        lambda: cena_do_plan(pdir, 1)["status"] == scene_plan_svc.STATUS_BAIXADA,
        timeout=6.0
    )
    assert pronto, (f"plan não virou BAIXADA após a conclusão tardia (cena={cena_do_plan(pdir, 1)})")
    assert time.time() - t0 < 6.0
    assert cena_do_plan(pdir, 1)["image_status"] == scene_plan_svc.IMAGE_STATUS_READY

    # ---------- RODADA 2: falha tardia → plan NÃO é tocado ----------
    estado["bloqueado"].clear()
    estado["liberar"].clear()
    estado["sucesso"] = False

    r_add2 = client.post(f"/api/v2/producao/{pid}/fila/adicionar",
                         json={"scene_ids": [1], "modo": "imagem"})
    assert r_add2.status_code == 200
    assert estado["bloqueado"].wait(5)
    assert client.post(f"/api/v2/producao/{pid}/fila/cancelar").status_code == 200
    assert cena_do_plan(pdir, 1)["status"] == scene_plan_svc.STATUS_PENDENTE

    estado["liberar"].set()
    time.sleep(0.5)
    cena = cena_do_plan(pdir, 1)
    assert cena["status"] == scene_plan_svc.STATUS_PENDENTE, "falha tardia mexeu no plan"
    assert cena["image_status"] == scene_plan_svc.IMAGE_STATUS_PENDING


# ============================================================================
# CASO 8: POLLING DE /fila_status NÃO CRIA WORKERS NEM THREADS
# ============================================================================
def test_08_fila_status_nao_cria_workers(client, setup_env, monkeypatch):
    """30 GETs de /fila_status num projeto sem worker: nada de worker/thread novo."""
    pid = "proj_poll_status"
    criar_projeto_teste(setup_env, pid, total_cenas=2)

    with pw_flow._queue_workers_lock:
        pw_flow._queue_workers.pop(pid, None)
        n_workers_antes = len(pw_flow._queue_workers)
    n_threads_antes = threading.active_count()

    for _ in range(30):
        resp = client.get(f"/api/v2/producao/{pid}/fila_status")
        assert resp.status_code == 200
        assert resp.get_json().get("success") is True

    with pw_flow._queue_workers_lock:
        n_workers_depois = len(pw_flow._queue_workers)
        assert pid not in pw_flow._queue_workers, "GET /fila_status criou worker"
    assert n_workers_depois == n_workers_antes
    assert threading.active_count() == n_threads_antes, "GET /fila_status criou threads"
