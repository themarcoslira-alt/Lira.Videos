# -*- coding: utf-8 -*-
"""PROVA POR ASSERT das guardas contra REENVIO (trava prompt_enviado) — tudo MOCK.
Tudo mock: sem Chrome, sem conta, sem credito, sem Google Flow."""
import json
import threading
import time

import pytest

import config
import services.api_v2 as v2
import services.playwright_flow as pw_flow
import services.scene_plan_service as scene_plan_svc
from app_web import app


@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    for mod in (config, v2, pw_flow, scene_plan_svc):
        monkeypatch.setattr(mod, "PROJETOS_DIR", tmp_path, raising=False)
    monkeypatch.setattr(pw_flow, "FLOW_QUEUE_BACKOFFS", [0, 0, 0])
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


@pytest.fixture
def client():
    return app.test_client()


def _criar_projeto(tmp_path, pid, ids):
    pdir = tmp_path / pid
    pdir.mkdir(parents=True, exist_ok=True)
    plan = {"projeto_id": pid, "cenas": [
        {"id": i, "visual_prompt": f"p{i}", "prompt_animacao": f"anim {i}",
         "tempo_inicio": 0.0, "tempo_fim": 5.0, "tipo": "video"} for i in ids]}
    (pdir / "lira_scene_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    return pdir


def _escrever_fila(pdir, itens):
    (pdir / "flow_queue.json").write_text(json.dumps(
        {"ativa": True, "pausada": False, "itens": itens, "erros": 0, "concluidos": 0,
         "cena_ativa": None}), encoding="utf-8")


def _ler_fila(pdir):
    return json.loads((pdir / "flow_queue.json").read_text(encoding="utf-8"))


def _item(fila, cid):
    for it in fila.get("itens", []):
        if int(it.get("scene_id") or it.get("id") or 0) == int(cid):
            return it
    raise AssertionError(f"item {cid} ausente: {fila.get('itens')}")


def _esperar(cond, timeout=25.0, intervalo=0.05):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(intervalo)
    return False


def _item_fila(cid, prompt_enviado=None):
    it = {"scene_id": cid, "id": cid, "status": "enfileirada", "modo": "animacao",
          "is_anim": True, "prompt": f"p{cid}", "max_tentativas": 3,
          "tentativa_atual": 0, "attempt_count": 0, "exec_id": None}
    if prompt_enviado is not None:
        it["prompt_enviado"] = prompt_enviado
    return it


class _WorkerFalso:
    """Substituto do singleton PlaywrightCDPWorker (sem Chrome)."""

    def __init__(self, baixar_ok=False):
        self.page = object()
        self._session_thread_ident = threading.get_ident()
        self.current_project_id = None
        self._baixar_ok = baixar_ok
        self.chamadas_aguardar = []
        self.cenas_geradas = []
        self.ordem = []
        self._pdir = None

    def _iniciar_sessao_thread(self):
        self._session_thread_ident = threading.get_ident()
        self.page = object()
        return True, "sessao fake"

    def _descartar_sessao_outra_thread(self):
        return False

    def _check_is_active(self):
        return self.page is not None

    def _ensure_project_open(self, projeto_id, timeout_s=5, conta_id=None):
        return True

    def _processar_cena_individual(self, projeto_id, cena, is_anim=False, **kw):
        """Envio NORMAL (simula o PROMPT_SENT_OK gravando a trava)."""
        cid = int(cena.get("id"))
        self.cenas_geradas.append(cid)
        self.ordem.append(cid)
        if self._pdir is not None:
            try:
                with pw_flow.transacao_fila(projeto_id, self._pdir) as _f:
                    for _it in (_f.get("itens") or []):
                        if int(_it.get("scene_id") or 0) == cid:
                            _it["prompt_enviado"] = True
                            break
            except Exception:
                pass
        return True, f"cena_{cid}.mp4"

    def _aguardar_e_baixar_video_existente(self, projeto_id, cena):
        """Rotina de espera/baixa (mockada)."""
        cid = int(cena.get("id"))
        self.chamadas_aguardar.append(cid)
        self.ordem.append(cid)
        if self._baixar_ok:
            return True, f"Cena {cid} gerada e baixada com sucesso: cena_{cid}.mp4"
        return False, "Timeout aguardando render do video ja enviado (sem reenvio)"


def _montar_fila(monkeypatch, tmp_path, client, pid, ids, com_trava, baixar_ok):
    """Cria a fila PELO ENDPOINT (como nos testes de robustez), grava a trava via
    transacao_fila e sobe o FlowQueueWorker DIRETO (sem /iniciar_fila)."""
    pdir = _criar_projeto(tmp_path, pid, ids)
    fake = _WorkerFalso(baixar_ok=baixar_ok)
    fake._pdir = tmp_path
    monkeypatch.setattr(pw_flow.FlowQueueWorker, "get_worker", lambda: fake)
    monkeypatch.setattr(pw_flow, "ensure_chrome_cdp", lambda *a, **k: (True, "cdp fake"))

    r = client.post(f"/api/v2/producao/{pid}/fila/adicionar",
                    json={"scene_ids": list(ids), "modo": "animacao"})
    assert r.status_code == 200, (r.status_code, r.get_json())
    fila0 = _ler_fila(pdir)
    assert len(fila0.get("itens") or []) == len(ids), fila0

    # Trava gravada pelo MECANISMO REAL (transacao_fila), não por escrita direta.
    with pw_flow.transacao_fila(pid, tmp_path) as fila:
        fila["ativa"] = True
        fila["pausada"] = False
        for it in (fila.get("itens") or []):
            cid = int(it.get("scene_id") or it.get("id") or 0)
            if cid in com_trava:
                it["prompt_enviado"] = True
                it["prompt_enviado_em"] = "2026-01-01T00:00:00"
                it["baseline_uuids"] = []

    w = pw_flow.FlowQueueWorker(pid, tmp_path)
    with pw_flow._queue_workers_lock:
        pw_flow._queue_workers[pid] = w      # teardown da fixture para o worker
    w.start_processing()
    return pdir, fake, w


def test_A_B_prompt_enviado_nao_reenvia_e_vira_aguardando_download(setup_env, client, monkeypatch):
    """Caso A (não reenvia + 1 chamada por cena) e B (falha => aguardando_download)."""
    pid, cids = "proj_guarda_ab", [1, 2]
    pdir, fake, _w = _montar_fila(monkeypatch, setup_env, client, pid, cids, {1, 2}, False)

    assert _esperar(lambda: len(fake.chamadas_aguardar) >= 1, timeout=15.0), \
        f"espera/baixa nao chamada (ordem={fake.ordem})"
    _esperar(lambda: len(fake.chamadas_aguardar) >= 2, timeout=10.0)
    fila = _ler_fila(pdir)

    assert fake.cenas_geradas == [], f"REENVIOU prompt: {fake.cenas_geradas}"
    assert fake.chamadas_aguardar == cids, fake.chamadas_aguardar
    for cid in cids:
        it = _item(fila, cid)
        assert it["status"] == "aguardando_download", it
        assert it.get("prompt_enviado") is True, it
    assert fila.get("erros", 0) == 0, fila.get("erros")
    assert fila.get("concluidos", 0) == 0, fila.get("concluidos")


def test_C_concluido_processa_a_proxima_cena_na_ordem(setup_env, client, monkeypatch):
    """Caso C: espera com SUCESSO conclui a cena 1 e o loop segue para a cena 2."""
    pid, cids = "proj_guarda_c", [1, 2]
    pdir, fake, _w = _montar_fila(monkeypatch, setup_env, client, pid, cids, {1, 2}, True)

    assert _esperar(lambda: len(fake.chamadas_aguardar) >= 2, timeout=15.0), fake.ordem
    _esperar(lambda: _item(_ler_fila(pdir), 2)["status"] == "concluida", timeout=10.0)
    fila = _ler_fila(pdir)
    assert fake.cenas_geradas == [], fake.cenas_geradas
    assert fake.ordem[:2] == cids, fake.ordem
    assert _item(fila, 1)["status"] == "concluida", _item(fila, 1)
    assert _item(fila, 1).get("prompt_enviado") is False, _item(fila, 1)
    assert _item(fila, 2)["status"] == "concluida", _item(fila, 2)
    assert fila.get("concluidos", 0) == 2, fila.get("concluidos")


def test_D_primeiro_envio_normal_e_grava_a_trava(setup_env, client, monkeypatch):
    """Caso D: sem prompt_enviado => envio normal ocorre e a trava é gravada."""
    pid, cids = "proj_guarda_d", [1]
    pdir, fake, _w = _montar_fila(monkeypatch, setup_env, client, pid, cids, set(), False)

    assert _esperar(lambda: fake.cenas_geradas == [1], timeout=15.0), fake.ordem
    assert fake.ordem[0] == 1, f"o 1o evento deveria ser o envio: {fake.ordem}"
    assert _esperar(lambda: bool(_item(_ler_fila(pdir), 1).get("prompt_enviado")), timeout=10.0), \
        "trava prompt_enviado nao gravada"
