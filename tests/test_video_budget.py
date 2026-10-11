# -*- coding: utf-8 -*-
"""
tests/test_video_budget.py — Orçamento de vídeos por fila (services/video_budget.py)
====================================================================================
Contexto: a fila em modo "animacao" enfileirava TODAS as cenas elegíveis a vídeo
de uma vez (206 itens em setembro/2026), esgotando os créditos de todas as contas
(Veo 3.1 Lite = 10 créditos; conta free 50/dia = 5 vídeos; 5 contas = 25/dia).

Este teste valida as DUAS funções puras do módulo (sem rede, sem I/O, sem mutação
da entrada) e, no fim, que a rota `/iniciar_fila` (services/api_v2.py) está
cabeada no orçamento (checagem estrutural — sem chamar o Flow).

Campos usados (`prioridade_video`, `secao_critica`, `retention_index`): nomes
CONFIRMADOS no código antes do uso (brain_system_director.py L1410-1461,
scene_plan_service.py L1357, retention_director_service.py L81).
"""
import copy
import unittest
from pathlib import Path

from services.video_budget import (
    MAX_VIDEOS_POR_FILA_PADRAO,
    limitar_videos,
    ordenar_por_prioridade,
)

API_SRC = Path("services/api_v2.py").read_text(encoding="utf-8")


def _cena(cid, **extra):
    """Cena mínima do lira_scene_plan (só os campos usados pelo orçamento)."""
    base = {"id": cid}
    base.update(extra)
    return base


def _cenas_39():
    """39 cenas elegíveis, na ordem cronológica 1..39.

    Distribuição: 20 "resto" (1-20), 10 "manter_5min" (21-30),
    4 "prender_2min" (31-34) e 5 "hook_30s" (35-39).

    As cenas de HOOK são as ÚLTIMAS cronologicamente — de propósito: assim o
    teste prova que a seleção é por PRIORIDADE, não por posição.
    `retention_index` decrescente (100 - id) nas de "resto" torna a seleção
    determinística (entram as 6 de maior índice: ids 1..6).
    """
    cenas = []
    for cid in range(1, 21):        # resto (20)
        cenas.append(_cena(cid, secao_critica="resto",
                           retention_index=100 - cid))
    for cid in range(21, 31):       # manter_5min (10)
        cenas.append(_cena(cid, secao_critica="manter_5min",
                           retention_index=50))
    for cid in range(31, 35):       # prender_2min (4)
        cenas.append(_cena(cid, secao_critica="prender_2min",
                           retention_index=70))
    for cid in range(35, 40):       # hook_30s (5)
        cenas.append(_cena(cid, secao_critica="hook_30s",
                           retention_index=90))
    return cenas


class TestOrdenarPorPrioridade(unittest.TestCase):

    def test_01_prioridade_video_vem_primeiro(self):
        """`prioridade_video` verdadeiro precede qualquer outra cena."""
        cenas = [
            _cena(1, secao_critica="hook_30s", retention_index=99),
            _cena(2, prioridade_video=True, secao_critica="resto"),
            _cena(3, prioridade_video=False, secao_critica="hook_30s",
                  retention_index=99),
        ]
        ordem = [c["id"] for c in ordenar_por_prioridade(cenas)]
        self.assertEqual(ordem[0], 2, "a cena com prioridade_video deve ser a 1ª")
        self.assertEqual(ordem[1:], [1, 3])

    def test_02_ordem_da_secao_critica(self):
        """hook_30s > prender_2min > manter_5min > resto > ausente."""
        cenas = [
            _cena(1),                                   # ausente (último)
            _cena(2, secao_critica="resto"),
            _cena(3, secao_critica="manter_5min"),
            _cena(4, secao_critica="prender_2min"),
            _cena(5, secao_critica="hook_30s"),
        ]
        ordem = [c["id"] for c in ordenar_por_prioridade(cenas)]
        self.assertEqual(ordem, [5, 4, 3, 2, 1])

    def test_03_retention_index_decrescente(self):
        """Mesma seção → maior `retention_index` primeiro; ausente = 0."""
        cenas = [
            _cena(1, secao_critica="resto", retention_index=10),
            _cena(2, secao_critica="resto", retention_index=80),
            _cena(3, secao_critica="resto"),            # ausente = 0
            _cena(4, secao_critica="resto", retention_index=50),
        ]
        ordem = [c["id"] for c in ordenar_por_prioridade(cenas)]
        self.assertEqual(ordem, [2, 4, 1, 3])

    def test_04_ordenacao_estavel_empate_total(self):
        """Empate completo mantém a posição original (ordenação estável)."""
        cenas = [_cena(cid) for cid in (7, 3, 9, 1)]
        ordem = [c["id"] for c in ordenar_por_prioridade(cenas)]
        self.assertEqual(ordem, [7, 3, 9, 1])

    def test_05_campos_ausentes_e_tipos_estranhos_nao_quebram(self):
        """Cenas vazias / sem os campos / tipos inesperados não levantam exceção."""
        cenas = [
            _cena(1),
            {},
            {"id": 3, "retention_index": "abc"},
            {"id": 5, "prioridade_video": True},
            {"id": 6, "retention_index": None},
            {"id": 7, "secao_critica": None},
        ]
        ordenadas = ordenar_por_prioridade(cenas)
        self.assertEqual(len(ordenadas), 6)
        # a única cena com prioridade_video (id 5) vem na frente, mesmo estando no meio
        self.assertEqual(ordenadas[0].get("id"), 5)
        # as demais (sem nenhum campo útil) mantêm a ordem original entre si
        self.assertEqual([c.get("id") for c in ordenadas[1:]], [1, None, 3, 6, 7])
        # valor truthy "estranho" também conta como prioridade_video verdadeira
        ord2 = ordenar_por_prioridade([
            _cena(1, secao_critica="hook_30s", retention_index=99),
            _cena(2, prioridade_video="sim", secao_critica="resto"),
        ])
        self.assertEqual([c["id"] for c in ord2], [2, 1])

    def test_06_nao_muta_a_entrada(self):
        cenas = _cenas_39()
        antes = copy.deepcopy(cenas)
        ordenar_por_prioridade(cenas)
        self.assertEqual(cenas, antes, "a lista de entrada não pode ser alterada")


class TestLimitarVideos(unittest.TestCase):

    def test_01_39_cenas_com_limite_25_devolve_25(self):
        """(i) 39 elegíveis + limite 25 → 25 selecionadas, incluindo TODOS os hooks."""
        cenas = _cenas_39()
        selecionadas = limitar_videos(cenas, 25)
        self.assertEqual(len(selecionadas), 25)
        ids = [c["id"] for c in selecionadas]
        hooks = {35, 36, 37, 38, 39}
        self.assertTrue(hooks.issubset(set(ids)),
                        "todas as cenas de hook_30s devem entrar na seleção")
        # resto (1..20): só as 6 de maior retention_index (ids 1..6)
        self.assertEqual(set(ids) & set(range(1, 21)), set(range(1, 7)))

    def test_02_hook_30s_antes_de_resto_na_prioridade(self):
        """(i) Na ORDEM DE PRIORIDADE, todos os hook_30s vêm antes de qualquer resto."""
        ordem = [c["id"] for c in ordenar_por_prioridade(_cenas_39())]
        pos_hooks = [ordem.index(cid) for cid in (35, 36, 37, 38, 39)]
        pos_resto = [ordem.index(cid) for cid in range(1, 21)]
        self.assertLess(max(pos_hooks), min(pos_resto),
                        "hook_30s deve ter prioridade sobre 'resto'")

    def test_03_limite_zero_devolve_todas(self):
        """(ii) limite 0 = sem limite."""
        cenas = _cenas_39()
        self.assertEqual(len(limitar_videos(cenas, 0)), 39)

    def test_04_limite_none_devolve_todas(self):
        """(ii) limite None = sem limite."""
        cenas = _cenas_39()
        self.assertEqual(len(limitar_videos(cenas, None)), 39)

    def test_05_limite_negativo_devolve_todas(self):
        cenas = _cenas_39()
        self.assertEqual(len(limitar_videos(cenas, -7)), 39)

    def test_06_limite_maior_que_o_total_devolve_todas(self):
        cenas = _cenas_39()
        self.assertEqual(len(limitar_videos(cenas, 999)), 39)

    def test_07_prioridade_video_entra_antes_de_qualquer_outra(self):
        """(iii) Com limite 1, a cena com prioridade_video é a escolhida (mesmo sendo a última cronologicamente)."""
        cenas = [
            _cena(1, secao_critica="hook_30s", retention_index=99),
            _cena(2, secao_critica="prender_2min", retention_index=99),
            _cena(3, prioridade_video=True, secao_critica="resto"),
        ]
        selecionadas = limitar_videos(cenas, 1)
        self.assertEqual([c["id"] for c in selecionadas], [3])

    def test_08_saida_em_ordem_cronologica(self):
        """(v) A saída volta à ordem cronológica original da entrada."""
        cenas = _cenas_39()
        ids = [c["id"] for c in limitar_videos(cenas, 25)]
        self.assertEqual(ids, sorted(ids), "a seleção deve sair em ordem crescente")
        self.assertEqual(
            ids,
            [1, 2, 3, 4, 5, 6]
            + list(range(21, 31))
            + list(range(31, 35))
            + list(range(35, 40)),
        )

    def test_09_nao_muta_a_entrada(self):
        """(vi) A lista (e os dicionários) de entrada permanecem intactos."""
        cenas = _cenas_39()
        antes = copy.deepcopy(cenas)
        limitar_videos(cenas, 25)
        limitar_videos(cenas, 3)
        self.assertEqual(cenas, antes)
        self.assertEqual(len(cenas), 39)

    def test_10_lista_vazia_e_campos_ausentes(self):
        """(iv) Campos ausentes / lista vazia não quebram."""
        self.assertEqual(limitar_videos([], 5), [])
        cenas = [_cena(1), {}, {"id": 3, "retention_index": "x"}]
        self.assertEqual(len(limitar_videos(cenas, 2)), 2)
        self.assertEqual(len(limitar_videos(cenas, None)), 3)

    def test_11_limite_aceita_string_numerica(self):
        """`max_videos` pode chegar do JSON como string."""
        self.assertEqual(len(limitar_videos(_cenas_39(), "25")), 25)
        self.assertEqual(len(limitar_videos(_cenas_39(), "0")), 39)
        self.assertEqual(len(limitar_videos(_cenas_39(), "abc")), 39)

    def test_12_retorno_e_lista_nova_e_padrao_sem_limite(self):
        """Nunca devolve a MESMA lista de entrada; padrão do módulo é 0 (sem limite)."""
        cenas = _cenas_39()
        self.assertIsNot(limitar_videos(cenas, 25), cenas)
        self.assertIsNot(limitar_videos(cenas, 0), cenas)
        self.assertEqual(MAX_VIDEOS_POR_FILA_PADRAO, 0)


class TestIntegracaoIniciarFila(unittest.TestCase):
    """Checagem ESTRUTURAL: a rota usa o orçamento e devolve os 3 campos. Sem Flow."""

    def _trecho(self):
        ini = API_SRC.index("def v2_producao_iniciar_fila(")
        fim = API_SRC.index("def v2_producao_reclassificar_animacoes(")
        return API_SRC[ini:fim]

    def test_01_rota_aplica_o_orcamento(self):
        trecho = self._trecho()
        self.assertIn("from services.video_budget import limitar_videos", trecho)
        self.assertIn('data.get("max_videos")', trecho)
        self.assertIn("_objs_selecionados = limitar_videos(", trecho)
        self.assertIn('cenas_pendentes = [int(c["id"]) for c in _objs_selecionados]', trecho)

    def test_02_resposta_traz_os_tres_numeros(self):
        trecho = self._trecho()
        self.assertIn('"videos_elegiveis": videos_elegiveis', trecho)
        self.assertIn('"videos_enfileirados": videos_enfileirados', trecho)
        self.assertIn('"limite": limite_videos', trecho)

    def test_03_log_video_budget(self):
        trecho = self._trecho()
        self.assertIn('"VIDEO_BUDGET"', trecho)
        self.assertIn("videos_elegiveis={videos_elegiveis}", trecho)

    def test_04_orcamento_so_no_modo_animacao(self):
        """O bloco do orçamento está guardado por `e_animacao` (modo imagem intacto)."""
        trecho = self._trecho()
        self.assertIn("if e_animacao and limite_videos is not None and cenas_pendentes:", trecho)


if __name__ == "__main__":
    unittest.main(verbosity=2)
