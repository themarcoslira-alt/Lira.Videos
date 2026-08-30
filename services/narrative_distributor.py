"""
services/narrative_distributor.py — Balanço GLOBAL avatar/b-roll (Lira Studio v0.2.0)
====================================================================================
Substitui a decisão 100% por-cena por uma passada de equilíbrio no plano inteiro:

  1. Âncoras duras (HOOK 1ª cena, CTA, CLOSING, transições explícitas) são mantidas.
  2. AVATAR excedente é rebaixado para BROLL (cobertura visual), gerando broll_query.
  3. Nunca permite 2 avatares consecutivos sem B-roll entre eles.
  4. Respeita video_mix_ratio.avatar_percentage do brand_profile (fallback 22%).
  5. Carimba narrativa_versao no plano -> migração idempotente de planos stale
     (ex.: projetos gerados quando o default do classificador ainda era AVATAR).

ADITIVO: nunca remove campos existentes; apenas ajusta narrative_role,
avatar_required e broll_query. Não toca em prompts, status ou arquivos.
"""

from typing import Dict, List, Any

# Versão da regra de distribuição. Ao subir, planos antigos são rebalanceados
# automaticamente na primeira leitura (auto-healing em carregar_scene_plan).
NARRATIVA_VERSAO = 3

# Quotas padrão (fallback se brand_profile.json não existir). Avatar = pontos
# estratégicos (âncora + reengajamento), nunca a maioria do vídeo (8% ~ 7 a 10 cenas).
DEFAULT_QUOTAS = {
    "avatar": 0.08,   # 8% das cenas com apresentador (7 a 10 cenas)
    "broll":  0.92,   # 92% B-roll de cobertura visual
    "hook":   0.02,
    "cta":    0.04,
    "closing": 0.02,
}

_ROLES_AVATAR = {"HOOK", "AVATAR", "TRANSITION", "CTA", "CLOSING"}
_ROLES_DURAS = {"HOOK", "CTA", "CLOSING", "TRANSITION"}  # nunca rebaixadas


def _broll_query_fallback(cena: Dict[str, Any]) -> str:
    """Gera uma query Pexels mínima quando a cena é rebaixada para BROLL."""
    from services.enhanced_scene_classifier import get_broll_query
    texto = str(cena.get("texto") or cena.get("narration") or cena.get("text") or "")
    st = str(cena.get("scene_type") or "")
    return get_broll_query(texto, st) or "garden nature macro"


def _quota_avatar(projeto: str) -> float:
    """Avatar alvo (%) — brand_profile pode ajustar dentro do teto padrão.

    O perfil global antigo trazia avatar_percentage=0.7 (design da Fase 1 que
    causou a saturação). O padrão oficial é 8% (DEFAULT_QUOTAS, 7 a 10 cenas).
    """
    try:
        from services.brand_profile_service import load_brand_profile
        p = load_brand_profile() or {}
        mix = p.get("video_mix_ratio") or {}
        perfil = float(mix.get("avatar_percentage", DEFAULT_QUOTAS["avatar"]))
    except Exception:
        perfil = DEFAULT_QUOTAS["avatar"]
    return max(0.05, min(perfil, DEFAULT_QUOTAS["avatar"]))


def rebalancear_narrativa(cenas: List[Dict[str, Any]], projeto: str = "") -> int:
    """Reequilibra narrative_role/avatar_required de um plano. Retorna nº de mudanças.

    Estratégia (Lira Studio v0.2.0):
      1. Âncoras duras: cena 1 vira HOOK; se não há CLOSING nas últimas 3, a última
         vira CLOSING; HOOKs fora da abertura são rebaixados.
      2. Todo AVATAR não-duro é rebaixado para BROLL (reset).
      3. Repõe avatar estrategicamente até a quota (22%), escolhendo cenas que
         estavam mais próximas das posições-alvo espalhadas no vídeo, com
         espaçamento mínimo de 2 índices (garante >=1 BROLL entre avatares).
      4. Todo BROLL garante broll_query.
    """
    if not cenas:
        return 0
    n = len(cenas)
    max_avatar = max(2, round(n * _quota_avatar(projeto)))
    mudancas = 0

    def _role(i: int) -> str:
        return str(cenas[i].get("narrative_role") or "BROLL").upper()

    def _rebaixar(i: int) -> None:
        cenas[i]["narrative_role"] = "BROLL"
        cenas[i]["avatar_required"] = False
        cenas[i]["uses_character"] = False
        cenas[i]["character_ref"] = ""
        cenas[i]["scene_type"] = "broll_macro"
        cenas[i]["broll_query"] = _broll_query_fallback(cenas[i])

    def _promover(i: int) -> None:
        cenas[i]["narrative_role"] = "AVATAR"
        cenas[i]["avatar_required"] = True
        cenas[i]["scene_type"] = "avatar_talking"
        cenas[i].pop("broll_query", None)

    # ---- Passo 1: normaliza papéis (HOOK no início, CLOSING no fim) ----
    if _role(0) not in _ROLES_DURAS:
        cenas[0]["narrative_role"], mudancas = "HOOK", mudancas + 1
    if not any(_role(i) == "CLOSING" for i in range(max(0, n - 3), n)):
        cenas[-1]["narrative_role"], mudancas = "CLOSING", mudancas + 1

    # ---- Passo 2: rebaixa HOOKs extras (apenas abertura pode ter HOOK) ----
    for i in range(1, n):
        if _role(i) == "HOOK":
            _rebaixar(i)
            mudancas += 1

    hard_idx = [i for i in range(n) if _role(i) in _ROLES_DURAS]

    # ---- Passo 3: reset de avatares + reposição estratégica com espaçamento ----
    candidatos = [i for i in range(n) if _role(i) == "AVATAR" and i not in hard_idx]
    for i in candidatos:
        _rebaixar(i)
        mudancas += 1

    ocupados = list(hard_idx)              # posições que NÃO podem virar BROLL
    orcamento = max(0, max_avatar - len(ocupados))
    if orcamento and candidatos:
        passos = orcamento + 1
        alvo_por_cand = {
            i: min(abs(i - int(((k + 1) / passos) * n)) for k in range(orcamento))
            for i in candidatos
        }
        promovidos = set()
        for i in sorted(candidatos, key=lambda x: (alvo_por_cand[x], x)):
            if len(promovidos) >= orcamento:
                break
            if all(abs(i - o) >= 2 for o in ocupados):
                _promover(i)
                mudancas += 1
                ocupados.append(i)
                promovidos.add(i)

    # ---- Passo 4: garante broll_query em todo BROLL ----
    for i in range(n):
        if _role(i) == "BROLL":
            if not cenas[i].get("broll_query"):
                cenas[i]["broll_query"] = _broll_query_fallback(cenas[i])
            cenas[i]["avatar_required"] = False
            cenas[i]["uses_character"] = False
            cenas[i]["character_ref"] = ""
            if str(cenas[i].get("scene_type", "")).startswith("avatar_"):
                cenas[i]["scene_type"] = "broll_macro"
        else:
            cenas[i]["avatar_required"] = _role(i) in _ROLES_AVATAR
    return mudancas


def aplicar_reclassificacao_narrativa(plan: Dict[str, Any], projeto: str = "") -> bool:
    """Reclassifica e rebalanceia o plano se narrativa_versao estiver desatualizada.

    Retorna True se houve mudança (e o plano foi atualizado em memória).
    Nunca lança exceção: quem chama (carregar_scene_plan) é caminho de leitura.
    """
    if not plan or not plan.get("cenas"):
        return False
    if plan.get("narrativa_versao") == NARRATIVA_VERSAO:
        return False
    from services.scene_plan_service import aplicar_classificacao_narrativa_cena
    cenas = plan["cenas"]
    for idx, cena in enumerate(cenas):
        aplicar_classificacao_narrativa_cena(cena, index=idx)
    mudancas = rebalancear_narrativa(cenas, projeto=projeto)
    plan["narrativa_versao"] = NARRATIVA_VERSAO
    return True
