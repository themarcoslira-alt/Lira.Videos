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


# ===========================================================================
# PLANO DE FONTES (DRY-RUN) — TAREFA 2
# ===========================================================================
# Antes de produzir, o operador precisa saber QUANTAS cenas sairão como VÍDEO e
# quantas como IMAGEM — e de QUAL FONTE (Flow vídeo / Flow imagem / banco de
# stock). `planejar_fontes` responde isso sem gerar nada: é uma função PURA
# (sem I/O, sem rede, sem Playwright) que recebe as cenas do plano e os saldos
# JÁ LIDOS pelo chamador.
#
# Orçamento = MESMA conta que a fila usa: soma, por conta que ainda tem crédito
# de vídeo, de `saldo_lido // custo_do_vídeo`. A constante de custo é a canônica
# de `services/flow_account_manager.py` (`CUSTOS_PADRAO["video"]`); o valor
# MEDIDO (`custos_atuais()`, que relê config + ledger) é resolvido pelo CHAMADOR
# e repassado em `custo_video=` — assim a função continua sem I/O.
#
# IMAGEM: o código confirma custo ZERO (`CUSTOS_PADRAO["imagem"] = 0`, e o
# `config/flow_custos.json` real traz {"video": 10, "imagem": 0}). Logo, imagem
# não consome orçamento (regra confirmada no código, não presumida).
FONTE_FLOW_VIDEO = "flow_video"
FONTE_FLOW_IMAGE = "flow_image"
FONTE_PEXELS = "pexels"          # banco de stock — ramo DESLIGADO por padrão
FONTE_FALLBACK_PADRAO = "flow_image"

# Pexels/Pixabay estão DESATIVADOS no produto (app_web.py devolve 410 e o
# pipeline_service ignora stock). O ramo `pexels` existe apenas PREPARADO: quem
# chama passa `stock_disponivel=True` explicitamente. Nunca reativar sozinho.
STOCK_DISPONIVEL_PADRAO = False


def custo_video_creditos() -> int:
    """Custo (créditos) de UMA geração de vídeo — constante canônica, SEM I/O.

    Lê `CUSTOS_PADRAO` do módulo de contas (mesmo valor usado pelo ledger); o
    custo MEDIDO exige leitura de disco e por isso é resolvido pelo chamador.
    """
    from services.flow_account_manager import CUSTOS_PADRAO
    try:
        return int(CUSTOS_PADRAO.get("video", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _saldo_int(valor) -> int:
    """`saldo_lido` -> int >= 0. None/não numérico/negativo vira 0.

    Conta sem saldo legível NÃO ganha crédito inventado (entra como 0 vídeos).
    """
    try:
        n = int(valor)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _secao_de(cena) -> str:
    """`secao_critica` normalizada ('' quando ausente/desconhecida)."""
    if not isinstance(cena, dict):
        return ""
    return str(cena.get("secao_critica") or "").strip().lower()


def _e_broll(cena) -> bool:
    """True quando o papel NARRATIVO da cena é BROLL (Fase 1 Lira Studio)."""
    if not isinstance(cena, dict):
        return False
    return str(cena.get("narrative_role") or "").strip().upper() == "BROLL"


def _id_cena(cena):
    """`id` inteiro da cena (None quando ausente/não numérico)."""
    if not isinstance(cena, dict):
        return None
    try:
        return int(cena.get("id"))
    except (TypeError, ValueError):
        return None



def orcamento_video(saldos, custo_video=None) -> dict:
    """Orçamento de vídeos + detalhe por conta (SEM I/O).

    Contas com `video_creditos_esgotados` verdadeiro NÃO entram no orçamento.
    Cada conta contribui com `saldo_lido // custo_video` vídeos. `custo_video`
    None → usa `custo_video_creditos()` (constante canônica do código).
    """
    if custo_video is None:
        custo_video = custo_video_creditos()
    try:
        custo = int(custo_video)
    except (TypeError, ValueError):
        custo = 0

    contas = []
    creditos_totais = 0
    creditos_video = 0
    for conta in (saldos or []):
        if not isinstance(conta, dict):
            continue
        saldo_raw = conta.get("saldo_lido")
        saldo = _saldo_int(saldo_raw)
        esgotada = bool(conta.get("video_creditos_esgotados"))
        creditos_totais += saldo
        videos = 0
        if not esgotada and custo > 0:
            videos = saldo // custo
            creditos_video += saldo
        contas.append({
            "id": conta.get("id"),
            "nome": conta.get("nome") or conta.get("email") or f"Conta {conta.get('id')}",
            "saldo_lido": saldo_raw,
            "saldo_lido_legivel": saldo_raw is not None and saldo > 0,
            "video_creditos_esgotados": esgotada,
            "entra_no_orcamento": (not esgotada) and custo > 0,
            "videos_possiveis": videos,
        })

    return {
        "orcamento": sum(c["videos_possiveis"] for c in contas),
        "custo_video": custo,
        "contas": contas,
        "creditos_totais_lidos": creditos_totais,
        "creditos_disponiveis_para_video": creditos_video,
        "contas_sem_saldo_lido": sum(1 for c in contas if not c["saldo_lido_legivel"]),
    }


def planejar_fontes(cenas, saldos, stock_disponivel=STOCK_DISPONIVEL_PADRAO,
                    custo_video=None) -> dict:
    """Plano de FONTES por cena (dry-run) — TAREFA 2. PURA: sem I/O e sem Playwright.

    - Orçamento = soma, por conta com crédito de vídeo, de
      `saldo_lido // custo_video` (constante canônica, nunca hardcoded).
    - Prioridade: REAPROVEITA `limitar_videos` (prioridade_video >
      secao_critica > retention_index) — a MESMA regra da fila, sem duplicar lógica.
    - Cenas dentro do orçamento → `fonte_planejada="flow_video"`; as demais →
      `"flow_image"` (imagem custa 0 crédito).
    - `stock_disponivel=True` (ramo PREPARADO e desligado por padrão): cenas BROLL
      com `broll_query`, FORA do orçamento, viram `"pexels"` com
      `fonte_fallback="flow_image"`.

    Retorna `{"resumo": {...}, "decisoes": [...]}` com as decisões na ordem
    CRONOLÓGICA de entrada. Nada é gravado e nenhuma cena é mutada.
    """
    lista = list(cenas or [])
    orc = orcamento_video(saldos, custo_video=custo_video)
    limite = orc["orcamento"]

    # `limitar_videos` devolve TODAS as cenas quando o limite é <= 0 (sem limite).
    # Para orçamento zerado a resposta correta é "nenhum vídeo" — tratado ANTES.
    selecionadas = limitar_videos(lista, limite) if limite > 0 else []
    # `limitar_videos` NÃO copia os dicts (devolve as mesmas referências), então a
    # identidade por `id()` marca exatamente as vencedoras sem reordenar nada.
    ids_selecionados = {id(c) for c in selecionadas}

    decisoes = []
    total_videos = 0
    total_imagens = 0
    total_stock = 0

    for cena in lista:
        secao = _secao_de(cena)
        if id(cena) in ids_selecionados:
            if secao:
                motivo = f"{secao} dentro do orçamento"
            elif isinstance(cena, dict) and bool(cena.get("prioridade_video")):
                motivo = "prioridade_video dentro do orçamento"
            else:
                motivo = "dentro do orçamento de vídeo"
            fonte = FONTE_FLOW_VIDEO
            total_videos += 1
        else:
            tem_query = bool(
                isinstance(cena, dict)
                and _e_broll(cena)
                and str(cena.get("broll_query") or "").strip()
            )
            if stock_disponivel and tem_query:
                fonte = FONTE_PEXELS
                motivo = "broll fora do orçamento com banco de stock disponível"
                total_stock += 1
            else:
                fonte = FONTE_FLOW_IMAGE
                motivo = "fora do orçamento de vídeo"
                total_imagens += 1

        decisoes.append({
            "id": _id_cena(cena),
            "fonte_planejada": fonte,
            "fonte_fallback": FONTE_FALLBACK_PADRAO,
            "motivo": motivo,
            "secao_critica": secao or None,
        })

    resumo = {
        "total_cenas": len(lista),
        "total_videos": total_videos,
        "total_imagens": total_imagens,
        "total_stock": total_stock,
        "orcamento_videos": limite,
        "custo_video": orc["custo_video"],
        "creditos_totais_lidos": orc["creditos_totais_lidos"],
        "creditos_disponiveis_para_video": orc["creditos_disponiveis_para_video"],
        "contas_sem_saldo_lido": orc["contas_sem_saldo_lido"],
        "saldo_por_conta": orc["contas"],
        "stock_disponivel": bool(stock_disponivel),
    }
    if orc["custo_video"] <= 0:
        resumo["aviso"] = (
            "custo de vídeo <= 0 (config/flow_custos.json): orçamento tratado como 0 "
            "para não superestimar a quantidade de vídeos"
        )

    return {"resumo": resumo, "decisoes": decisoes}

