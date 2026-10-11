# -*- coding: utf-8 -*-
"""
services/video_budget.py — Orçamento de vídeos por fila (funções PURAS: sem rede, sem I/O)
=========================================================================================
Motivação (setembro/2026): a fila em modo "animacao" enfileirava TODAS as cenas
elegíveis a vídeo em um único clique (206 itens) e consumia os créditos de todas
as contas de uma vez.

Referência de custo: Veo 3.1 Lite = 10 créditos por vídeo; conta free = 50
créditos/dia = 5 vídeos; com 5 contas = 25 vídeos/dia. Ou seja, uma fila sem
limite esgota o dia inteiro em um clique.

Este módulo apenas ESCOLHE quais cenas entram no clique. Ele NÃO altera cena,
plano, fila, nem créditos: o ledger de custos continua sendo o já existente em
`services/flow_account_manager.py`.

Prioridade de escolha (TAREFA 1):
  1. `prioridade_video` verdadeiro primeiro;
  2. `secao_critica` na ordem hook_30s > prender_2min > manter_5min > resto
     (ausente ou desconhecido = último);
  3. `retention_index` decrescente (ausente = 0);
  4. posição original na lista (garante ordenação ESTÁVEL e determinística).

Nomes de campo CONFIRMADOS no código antes do uso:
  - `prioridade_video` / `secao_critica` → `services/brain_system_director.py`
    (`_overrides_composicao_por_secao`, L1410-1461: hook_30s/prender_2min/
    manter_5min/resto).
  - `retention_index` → `services/scene_plan_service.py` (`_nova_cena`, L1357)
    e `services/retention_director_service.py` (L81).
  - `manual_override` → `services/scene_plan_service.py` L1428 /
    `services/brain_system_director.py` L334.
"""

# 0 = sem limite (padrão). Quem chama (a rota `/iniciar_fila`) passa o limite.
MAX_VIDEOS_POR_FILA_PADRAO = 0

# Ordem de criticidade da seção temporal do vídeo. Valores reais usados pelo
# brain_system_director (`_overrides_composicao_por_secao`).
_RANK_SECAO = {
    "hook_30s": 0,
    "prender_2min": 1,
    "manter_5min": 2,
    "resto": 3,
}
# Ausente/desconhecido fica DEPOIS de "resto" (é o último critério antes da posição).
_RANK_SECAO_AUSENTE = 4


def _retention_num(valor) -> float:
    """Converte `retention_index` em float. Ausente/não numérico vira 0.0."""
    if valor is None:
        return 0.0
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


def _chave_prioridade(indice: int, cena) -> tuple:
    """Chave de ordenação de uma cena: (prioridade_video, secao, -retention, posição).

    Menor chave = mais prioritário. Nunca muta `cena`.
    """
    if not isinstance(cena, dict):
        cena = {}
    prio_video = 0 if bool(cena.get("prioridade_video")) else 1
    secao = str(cena.get("secao_critica") or "").strip().lower()
    rank_secao = _RANK_SECAO.get(secao, _RANK_SECAO_AUSENTE)
    retention = _retention_num(cena.get("retention_index"))
    return (prio_video, rank_secao, -retention, indice)


def ordenar_por_prioridade(cenas):
    """Devolve uma NOVA lista ordenada por prioridade de retenção.

    Ordem: `prioridade_video` > `secao_critica` (hook_30s, prender_2min,
    manter_5min, resto, ausente) > `retention_index` decrescente > posição
    original. Não muta a lista nem os dicionários de entrada.
    """
    lista = list(cenas or [])
    pares = list(enumerate(lista))
    # `sorted` é estável e a posição original já é o último termo da chave.
    pares.sort(key=lambda par: _chave_prioridade(par[0], par[1]))
    return [cena for _, cena in pares]


def limitar_videos(cenas, limite=MAX_VIDEOS_POR_FILA_PADRAO):
    """Seleciona no máximo `limite` cenas por prioridade, mantendo a ordem cronológica.

    - `limite` None ou <= 0 → devolve TODAS as cenas (sem limite), na ordem original;
    - caso contrário → devolve as `limite` mais prioritárias (via
      `ordenar_por_prioridade`), REORDENADAS de volta para a ordem cronológica
      original da lista de entrada.

    Nunca muta a entrada (a lista e os dicionários permanecem intactos).
    """
    lista = list(cenas or [])

    if limite is None:
        return lista
    try:
        limite_int = int(limite)
    except (TypeError, ValueError):
        return lista
    if limite_int <= 0:
        return lista

    pares = list(enumerate(lista))
    pares.sort(key=lambda par: _chave_prioridade(par[0], par[1]))
    selecionados = pares[:limite_int]
    # Volta à ordem cronológica ORIGINAL (o índice original é a posição na entrada).
    selecionados.sort(key=lambda par: par[0])
    return [cena for _, cena in selecionados]
