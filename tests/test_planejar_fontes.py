# -*- coding: utf-8 -*-
"""
tests/test_planejar_fontes.py — TAREFA 1/2/3 (plano de fontes dry-run)
=====================================================================
Problema: o projeto não mostrava, ANTES de produzir, quantas cenas seriam vídeo
e quantas imagem, nem de qual FONTE; o orçamento de vídeo só reagia durante a
fila (e já gastando crédito).

Cobertura:
1. `video_budget.planejar_fontes` — função PURA que decide a fonte por cena com o
   orçamento somado de TODAS as contas que ainda têm crédito de vídeo.
2. `_nova_cena` — os 3 campos novos (`fonte_planejada`/`fonte_fallback`/
   `fonte_real`) e a whitelist de `atualizar_cena` (senão seriam descartados).
3. Plano ANTIGO (sem os campos) continua carregando — leitura via `.get`.
4. Endpoint dry-run `GET /api/v2/producao/<id>/planejar_fontes` — devolve resumo +
   decisões com os 80 primeiros caracteres do texto narrado e NÃO grava nada.
"""
import json
from pathlib import Path

import pytest

import config
import services.character_service as character_svc
import services.scene_plan_service as scene_plan_svc
import services.video_budget as vb
import services.api_v2 as v2
from app_web import app


def _cena(cid, **kwargs):
    """Cena mínima com os campos que o planejador lê."""
    base = {
        "id": cid,
        "texto": f"texto da cena {cid}",
        "secao_critica": None,
        "retention_index": 50,
        "prioridade_video": False,
        "narrative_role": "AVATAR",
        "broll_query": None,
    }
    base.update(kwargs)
    return base


class TestOrcamento:
    """Orçamento = soma, por conta com crédito de vídeo, de saldo // custo."""

    def test_varios_saldos_somados_geram_o_numero_esperado(self):
        saldos = [
            {"id": 1, "nome": "a", "video_creditos_esgotados": False, "saldo_lido": 50},
            {"id": 2, "nome": "b", "video_creditos_esgotados": False, "saldo_lido": 30},
            {"id": 3, "nome": "c", "video_creditos_esgotados": True, "saldo_lido": 90},
        ]
        orc = vb.orcamento_video(saldos, custo_video=10)
        assert orc["orcamento"] == 8          # 5 (conta 1) + 3 (conta 2)
        assert orc["creditos_totais_lidos"] == 170
        assert orc["creditos_disponiveis_para_video"] == 80   # conta 3 fica de fora
        assert [c["videos_possiveis"] for c in orc["contas"]] == [5, 3, 0]

    def test_custo_vem_da_constante_canonica_do_codigo(self):
        """Sem `custo_video` explícito usa CUSTOS_PADRAO["video"] (nunca hardcode)."""
        from services.flow_account_manager import CUSTOS_PADRAO
        assert vb.custo_video_creditos() == int(CUSTOS_PADRAO["video"])
        saldos = [{"id": 1, "video_creditos_esgotados": False, "saldo_lido": 50}]
        _custo = int(CUSTOS_PADRAO["video"])
        esperado = 50 // _custo if _custo > 0 else 0
        assert vb.orcamento_video(saldos)["orcamento"] == esperado

    def test_conta_sem_saldo_lido_nao_inventa_credito(self):
        saldos = [{"id": 1, "video_creditos_esgotados": False, "saldo_lido": None},
                  {"id": 2, "video_creditos_esgotados": False, "saldo_lido": "50"}]
        orc = vb.orcamento_video(saldos, custo_video=10)
        assert orc["orcamento"] == 5
        assert orc["contas_sem_saldo_lido"] == 1

    def test_custo_zero_nao_estoura_e_nao_superestima(self):
        saldos = [{"id": 1, "video_creditos_esgotados": False, "saldo_lido": 50}]
        orc = vb.orcamento_video(saldos, custo_video=0)
        assert orc["orcamento"] == 0          # nunca ZeroDivisionError
        res = vb.planejar_fontes([_cena(1)], saldos, custo_video=0)
        assert res["resumo"]["total_videos"] == 0
        assert "aviso" in res["resumo"]


class TestHookEEsgotadas:
    """(a) o hook entra no orçamento primeiro; (b) esgotadas ⇒ zero vídeos."""

    def test_varios_saldos_geram_o_numero_esperado_com_o_hook_primeiro(self):
        # O hook está no MEIO da lista: se a escolha fosse posicional ele não
        # entraria — tem que entrar por PRIORIDADE.
        cenas = [
            _cena(1, secao_critica="resto", retention_index=10),
            _cena(2, secao_critica="resto", retention_index=20),
            _cena(3, secao_critica="hook_30s", retention_index=1),
            _cena(4, secao_critica="resto", retention_index=30),
        ]
        saldos = [{"id": 1, "video_creditos_esgotados": False, "saldo_lido": 20}]
        res = vb.planejar_fontes(cenas, saldos, custo_video=10)   # orçamento = 2

        por_id = {d["id"]: d for d in res["decisoes"]}
        assert res["resumo"]["orcamento_videos"] == 2
        assert res["resumo"]["total_videos"] == 2
        assert res["resumo"]["total_imagens"] == 2

        assert por_id[3]["fonte_planejada"] == "flow_video"
        assert por_id[3]["motivo"] == "hook_30s dentro do orçamento"
        # 2ª vaga vai para a MAIOR retention_index entre as "resto" (id 4, 30)
        assert por_id[4]["fonte_planejada"] == "flow_video"
        assert por_id[1]["fonte_planejada"] == "flow_image"
        assert por_id[1]["motivo"] == "fora do orçamento de vídeo"
        # as decisões preservam a ordem CRONOLÓGICA de entrada
        assert [d["id"] for d in res["decisoes"]] == [1, 2, 3, 4]

    def test_todas_as_contas_esgotadas_geram_zero_videos(self):
        cenas = [_cena(i, secao_critica="hook_30s") for i in range(1, 6)]
        saldos = [
            {"id": 1, "video_creditos_esgotados": True, "saldo_lido": 500},
            {"id": 2, "video_creditos_esgotados": True, "saldo_lido": 500},
        ]
        res = vb.planejar_fontes(cenas, saldos, custo_video=10)
        # Regressão do `limitar_videos(limite<=0)` = "sem limite": orçamento 0
        # PRECISA devolver nenhum vídeo, e não todas as cenas.
        assert res["resumo"]["orcamento_videos"] == 0
        assert res["resumo"]["total_videos"] == 0
        assert res["resumo"]["total_imagens"] == 5
        assert {d["fonte_planejada"] for d in res["decisoes"]} == {"flow_image"}

    def test_lista_vazia_de_saldos_tambem_gera_zero_videos(self):
        res = vb.planejar_fontes([_cena(1), _cena(2)], [], custo_video=10)
        assert res["resumo"]["total_videos"] == 0
        assert res["resumo"]["total_imagens"] == 2


class TestRamoStock:
    """(c) stock_disponivel=False NUNCA gera pexels (ramo preparado e desligado)."""

    def test_stock_desligado_nunca_gera_pexels(self):
        cenas = [
            _cena(1, narrative_role="BROLL", broll_query="tomato plant close up"),
            _cena(2, narrative_role="BROLL", broll_query="watering soil"),
        ]
        saldos = [{"id": 1, "video_creditos_esgotados": False, "saldo_lido": 0}]
        res = vb.planejar_fontes(cenas, saldos, custo_video=10)   # stock_disponivel padrão

        assert res["resumo"]["stock_disponivel"] is False
        assert res["resumo"]["total_stock"] == 0
        assert {d["fonte_planejada"] for d in res["decisoes"]} == {"flow_image"}
        assert all(d["fonte_fallback"] == "flow_image" for d in res["decisoes"])

    def test_stock_liga_apenas_quando_pedido_explicitamente(self):
        cenas = [
            _cena(1, narrative_role="BROLL", broll_query="tomato plant close up"),
            _cena(2, narrative_role="BROLL", broll_query=""),      # sem query → imagem
            _cena(3, narrative_role="AVATAR", broll_query="algo"),  # não é broll
        ]
        saldos = [{"id": 1, "video_creditos_esgotados": False, "saldo_lido": 0}]
        res = vb.planejar_fontes(cenas, saldos, stock_disponivel=True, custo_video=10)

        por_id = {d["id"]: d for d in res["decisoes"]}
        assert por_id[1]["fonte_planejada"] == vb.FONTE_PEXELS
        assert por_id[1]["fonte_fallback"] == "flow_image"
        assert por_id[2]["fonte_planejada"] == "flow_image"
        assert por_id[3]["fonte_planejada"] == "flow_image"
        assert res["resumo"]["total_stock"] == 1



# ---------------------------------------------------------------------------
# Plano no disco + endpoint dry-run
# ---------------------------------------------------------------------------
def _criar_projeto(tmp_path, monkeypatch, nome, cenas, extra=None):
    """Cria projetos/<nome>/lira_scene_plan.json num PROJETOS_DIR temporário."""
    monkeypatch.setattr(scene_plan_svc, "PROJETOS_DIR", tmp_path)
    monkeypatch.setattr(character_svc, "PROJETOS_DIR", tmp_path)
    monkeypatch.setattr(v2, "PROJETOS_DIR", tmp_path)
    pdir = tmp_path / nome
    pdir.mkdir(parents=True, exist_ok=True)
    plano = {"projeto": nome, "cenas": cenas}
    if extra:
        plano.update(extra)
    (pdir / "lira_scene_plan.json").write_text(
        json.dumps(plano, ensure_ascii=False), encoding="utf-8")
    return pdir


class TestPlanoAntigo:
    """(d) plano antigo, SEM os campos novos, carrega sem erro (leitura via .get)."""

    def test_plano_sem_os_campos_novos_carrega_e_le_com_get(self, tmp_path, monkeypatch):
        pdir = _criar_projeto(
            tmp_path, monkeypatch, "proj_antigo",
            [{"id": 1, "texto": "cena antiga"}, {"id": 2, "narration": "outra cena"}],
        )
        plan = scene_plan_svc.carregar_scene_plan("proj_antigo")
        assert plan is not None
        assert len(plan["cenas"]) == 2

        primeira = plan["cenas"][0]
        # Nada é inventado: a leitura nova usa `.get`, que devolve None.
        assert primeira.get("fonte_planejada") is None
        assert primeira.get("fonte_fallback") is None
        assert primeira.get("fonte_real") is None
        # O arquivo em disco NÃO ganhou os campos novos (carregar não grava).
        raw = (pdir / "lira_scene_plan.json").read_text(encoding="utf-8")
        assert "fonte_planejada" not in raw
        assert "fonte_fallback" not in raw

        # E o planejador roda sobre o plano antigo sem erro.
        saldos = [{"id": 1, "video_creditos_esgotados": False, "saldo_lido": 10}]
        res = vb.planejar_fontes(plan["cenas"], saldos, custo_video=10)
        assert res["resumo"]["total_cenas"] == 2
        assert res["resumo"]["total_videos"] == 1
        assert res["resumo"]["total_imagens"] == 1


class TestCamposDaCena:
    """TAREFA 1 — os 3 campos novos em `_nova_cena` + whitelist de atualizar_cena."""

    def test_nova_cena_traz_os_campos_do_plano_de_fontes(self):
        c = scene_plan_svc._nova_cena(1, 0.0, 5.0, "texto qualquer")
        assert c["fonte_planejada"] is None
        assert c["fonte_fallback"] == "flow_image"
        assert c["fonte_real"] is None

    def test_campos_novos_persistem_via_atualizar_cena(self, tmp_path, monkeypatch):
        """Sem os campos na whitelist, `atualizar_cena` os descartaria em SILÊNCIO."""
        _criar_projeto(tmp_path, monkeypatch, "proj_whitelist", [{"id": 1, "texto": "x"}])
        r = scene_plan_svc.atualizar_cena("proj_whitelist", 1, {
            "fonte_planejada": "flow_video",
            "fonte_fallback": "flow_image",
            "fonte_real": "flow",
        })
        assert r.get("success") is True
        cena = scene_plan_svc.carregar_scene_plan("proj_whitelist")["cenas"][0]
        assert cena["fonte_planejada"] == "flow_video"
        assert cena["fonte_fallback"] == "flow_image"
        assert cena["fonte_real"] == "flow"



class _FlowMgrFake:
    """Substituto do FlowAccountManager: saldos e custo SEM tocar config nem browser."""

    def __init__(self, saldos, custo=10):
        self._saldos = saldos
        self._custo = custo

    def saldos_detalhados(self):
        return self._saldos

    def custos_atuais(self):
        return {"video": self._custo, "imagem": 0}


class TestEndpointDryRun:
    """TAREFA 3 — GET /api/v2/producao/<id>/planejar_fontes (não grava nada)."""

    def test_endpoint_devolve_resumo_decisoes_e_nao_grava(self, tmp_path, monkeypatch):
        cenas = [
            _cena(1, secao_critica="hook_30s", texto="Hook do video " + "x" * 120),
            _cena(2), _cena(3), _cena(4),
        ]
        pdir = _criar_projeto(tmp_path, monkeypatch, "proj_dry", cenas)
        arquivo = pdir / "lira_scene_plan.json"
        antes = arquivo.read_bytes()
        n_cenas = len(scene_plan_svc.carregar_scene_plan("proj_dry")["cenas"])
        assert n_cenas == 4

        monkeypatch.setattr(v2, "flow_mgr", _FlowMgrFake(
            [{"id": 1, "nome": "conta", "video_creditos_esgotados": False,
              "saldo_lido": 30}], custo=10))

        resp = app.test_client().get("/api/v2/producao/proj_dry/planejar_fontes")
        assert resp.status_code == 200
        dados = resp.get_json()

        assert dados["success"] is True
        assert dados["dry_run"] is True
        assert dados["resumo"]["total_cenas"] == 4
        assert dados["resumo"]["orcamento_videos"] == 3      # 30 créditos // 10
        assert dados["resumo"]["total_videos"] == 3
        assert dados["resumo"]["total_imagens"] == 1
        assert dados["resumo"]["custo_video"] == 10
        assert dados["resumo"]["creditos_totais_lidos"] == 30
        assert dados["resumo"]["total_stock"] == 0
        assert dados["resumo"]["saldo_por_conta"][0]["nome"] == "conta"

        assert len(dados["decisoes"]) == 4
        assert [d["id"] for d in dados["decisoes"]] == [1, 2, 3, 4]
        assert dados["decisoes"][0]["fonte_planejada"] == "flow_video"
        assert dados["decisoes"][0]["motivo"] == "hook_30s dentro do orçamento"
        # 80 primeiros caracteres do texto narrado
        assert dados["decisoes"][0]["texto"] == ("Hook do video " + "x" * 120)[:80]
        assert len(dados["decisoes"][0]["texto"]) == 80

        # NADA foi gravado no plano (dry-run de verdade).
        assert arquivo.read_bytes() == antes

    def test_endpoint_sem_plano_devolve_404(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scene_plan_svc, "PROJETOS_DIR", tmp_path)
        monkeypatch.setattr(character_svc, "PROJETOS_DIR", tmp_path)
        resp = app.test_client().get("/api/v2/producao/nao_existe/planejar_fontes")
        assert resp.status_code == 404
        assert resp.get_json()["success"] is False

