"""
services/retention_analyzer_service.py — Retention Analyzer (Estratégia de Retenção)
=====================================================================================
Analisa o plano de cenas sob a ótica de retenção audiovisual (estudo YouTube):

  - Avatar NÃO é performance contínua: é pattern interrupt estratégico.
  - Padrão alvo: HOOK → VALUE → [B-ROLL] → CHECKPOINT (~90-150s) → ... →
    CONCLUSÃO (>=80%) → CTA (>=90%).
  - Teto anti-saturação: ~6-9 cenas com avatar por vídeo (8-10% das cenas),
    nunca avatares consecutivos no corpo do vídeo, e B-ROLL puro máximo 150s.

Responsabilidades:
  - `RetentionAnalyzer.analyze_scene_plan(cenas)` — auditoria e score.
  - `validate_retention_pattern(cenas)` — check-list e problemas.
  - `aplicar_estrategia_retencao(cenas, ...)` — preenche os campos novos
    (avatar_role, should_have_avatar, pattern_interrupt, ...) e alinha
    uses_character/avatar_required com os papéis escolhidos.
"""
import collections
from typing import Dict, List, Any, Optional

from services.scene_classifier_service import classify_avatar_role, GAP_MAX_AVATAR_SEG


ROLES_OBRIGATORIOS = ("HOOK", "CTA")
ROLES_FALAM = ("HOOK", "VALUE", "CHECKPOINT", "REFORÇO", "CONCLUSÃO", "CTA")


def _ts(cena: Dict[str, Any]) -> float:
    """Timestamp de início da cena (segundos)."""
    v = cena.get("tempo_inicio")
    if v is None:
        v = cena.get("start")
    if v is None:
        v = cena.get("start_time")
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _dur(cena: Dict[str, Any]) -> float:
    v = cena.get("duracao")
    if v is None:
        ts = _ts(cena)
        te = cena.get("tempo_fim")
        if te is None:
            te = cena.get("end")
        if te is None:
            te = cena.get("end_time")
        try:
            v = float(te or ts + 5.0) - ts
        except (TypeError, ValueError):
            v = 5.0
    try:
        return max(0.5, float(v or 5.0))
    except (TypeError, ValueError):
        return 5.0


def aplicar_estrategia_retencao(
    cenas: List[Dict[str, Any]],
    video_duration: Optional[float] = None,
    character_ref: str = "",
) -> List[Dict[str, Any]]:
    """Classifica e aplica papéis de retenção no plano (passada aditiva).

    - Preenche os 6 campos novos de retenção por cena.
    - Teto de avatares: ~6-9 cenas (8-10%) — nunca satura.
    - Alinha uses_character/avatar_required/character_ref aos papéis.
    """
    if not cenas:
        return cenas

    # Duração total (maior tempo_fim ou soma quando não houver timestamps)
    if not video_duration:
        fim_ts = [(_ts(c) + _dur(c)) for c in cenas]
        video_duration = max(fim_ts) if fim_ts else (sum(_dur(c) for c in cenas))

    # Ordena por tempo real para o passe de gap (não altera a lista original)
    idx_ordenado = sorted(range(len(cenas)), key=lambda i: _ts(cenas[i]))

    n = len(cenas)
    max_avatar = max(3, min(9, round(n * 0.09)))

    # 1º passe — candidatos por regra de retenção
    estado = {"ultimo_avatar_timestamp": -9999.0, "avatar_count": 0, "max_avatar": max_avatar}
    for i in idx_ordenado:
        c = cenas[i]
        inicio = _ts(c)
        texto = str(c.get("texto") or c.get("narration") or c.get("text") or "")
        dec = classify_avatar_role(
            scene_id=c.get("scene_index") if c.get("scene_index") is not None else c.get("id", i + 1),
            timestamp_start=inicio,
            timestamp_end=inicio + _dur(c),
            video_duration_total=video_duration,
            roteiro_texto=texto,
            cenas_anteriores=estado,
        )
        c["avatar_role"] = dec["avatar_role"]
        c["is_pattern_interrupt"] = bool(dec["is_pattern_interrupt"])
        c["expected_viewer_drop"] = bool(dec["expected_viewer_drop"])
        c["retencao_impact"] = dec["retencao_impact"] or "low"
        c["should_have_avatar"] = bool(dec["should_have_avatar"])
        if "motivo_retencao" not in c:
            c["motivo_retencao"] = dec.get("motivo", "")
        if c["should_have_avatar"]:
            estado["avatar_count"] += 1
            estado["ultimo_avatar_timestamp"] = inicio

    # 1.5ª passe — NORMALIZAÇÃO GLOBAL (evita saturação e papéis duplicados):
    #   * HOOK: só a 1ª cena
    #   * VALUE: só a 1ª cena candidata (início)
    #   * CHECKPOINT/REFORÇO/AÇÃO: apenas com espaçamento mínimo (~90-120s)
    #   * CONCLUSÃO e CTA: apenas a ÚLTIMA ocorrência de cada papel
    #   * teto final = max_avatar (6-9)
    def _normalizar_papeis():
        # índices (em ordem de tempo) dos candidatos por papel
        idx_hook = [i for i in idx_ordenado if cenas[i].get("avatar_role") == "HOOK"]
        idx_value = [i for i in idx_ordenado if cenas[i].get("avatar_role") == "VALUE"]
        idx_check = [i for i in idx_ordenado if cenas[i].get("avatar_role") == "CHECKPOINT"]
        idx_ref = [i for i in idx_ordenado if cenas[i].get("avatar_role") in ("REFORÇO", "AÇÃO")]
        idx_concl = [i for i in idx_ordenado if cenas[i].get("avatar_role") == "CONCLUSÃO"]
        idx_cta = [i for i in idx_ordenado if cenas[i].get("avatar_role") == "CTA"]

        # Obrigatórios: HOOK (1º), CTA (último), CONCLUSÃO (última) e VALUE (1ª, se existir)
        mantidos = []
        if idx_hook:
            mantidos.append(idx_hook[0])
        if idx_value:
            mantidos.append(idx_value[0])
        if idx_concl:
            mantidos.append(idx_concl[-1])
        if idx_cta:
            mantidos.append(idx_cta[-1])

        # Espaçamento mínimo entre avatares do corpo
        MIN_GAP = 90.0

        def _distancia_ok(i: int) -> bool:
            ts_i = _ts(cenas[i])
            return all(abs(ts_i - _ts(cenas[j])) >= MIN_GAP for j in mantidos)

        for i in idx_check:
            if len(mantidos) >= max_avatar:
                break
            if _distancia_ok(i):
                mantidos.append(i)

        for i in idx_ref:
            if len(mantidos) >= max_avatar:
                break
            if _distancia_ok(i):
                mantidos.append(i)

        # Rebaixa candidatos que não foram mantidos
        for i, c in enumerate(cenas):
            if c.get("avatar_role") and i not in mantidos:
                c["avatar_role"] = None
                c["should_have_avatar"] = False
                c["is_pattern_interrupt"] = False
                c["expected_viewer_drop"] = False
                c["retencao_impact"] = "low"
                c["motivo_retencao"] = "rebaixado pela normalização de retenção"

        # Garante que o HOOK (cena 1) e o CTA final sejam mantidos mesmo sem candidatura
        if cenas and not cenas[0].get("avatar_role"):
            cenas[0]["avatar_role"] = "HOOK"
            cenas[0]["should_have_avatar"] = True
            cenas[0]["retencao_impact"] = "critical"
            cenas[0]["motivo_retencao"] = "abertura obrigatória (HOOK)"
        if len(cenas) > 1 and not cenas[-1].get("avatar_role"):
            cenas[-1]["avatar_role"] = "CTA"
            cenas[-1]["should_have_avatar"] = True
            cenas[-1]["retencao_impact"] = "critical"
            cenas[-1]["motivo_retencao"] = "encerramento obrigatório (CTA)"

    _normalizar_papeis()

    # 2º passe — calcula o gap efetivo de cada cena até o avatar anterior
    ultimo = -9999.0
    for i in idx_ordenado:
        c = cenas[i]
        inicio = _ts(c)
        c["timestamp_desde_ultimo_avatar"] = int(round(inicio - ultimo)) if ultimo > -999 else 0
        if c.get("should_have_avatar"):
            ultimo = inicio

    # 3º passe — alinha flags de personagem com os papéis
    for i, c in enumerate(cenas):
        role = c.get("avatar_role")
        deve_avatar = bool(
            c.get("should_have_avatar")
            or (role in ROLES_OBRIGATORIOS and i in (0, len(cenas) - 1))
        )
        if deve_avatar:
            c["uses_character"] = True
            c["avatar_required"] = True
            if character_ref and not str(c.get("character_ref") or "").strip():
                c["character_ref"] = character_ref
            st = str(c.get("scene_type") or "")
            if not st.startswith(("avatar_", "hybrid", "cta")):
                c["scene_type"] = "avatar_action" if role == "AÇÃO" else "avatar_talking"
        else:
            c["uses_character"] = False
            c["avatar_required"] = False
            c["character_ref"] = ""
            if role is None and str(c.get("scene_type") or "").startswith("avatar_"):
                c["scene_type"] = "broll_macro"

    return cenas


class RetentionAnalyzer:
    """Auditoria de retenção do scene plan."""

    @staticmethod
    def _fmt(seg: float) -> str:
        m, s = divmod(int(seg or 0), 60)
        return f"{m}:{s:02d}"

    @classmethod
    def analyze_scene_plan(cls, scene_plan: Dict[str, Any]) -> Dict[str, Any]:
        cenas = (scene_plan or {}).get("cenas", []) or []
        return cls._analisar(cenas)

    @classmethod
    def analyze_cenas(cls, cenas: List[Dict[str, Any]]) -> Dict[str, Any]:
        return cls._analisar(cenas)

    @classmethod
    def _analisar(cls, cenas: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not cenas:
            return {"error": "sem cenas", "score_retencao": 0, "problemas": ["plano vazio"]}

        total = len(cenas)
        duracao_total = 0.0
        avatar_total = 0.0
        checkpoints = []
        schedule = []
        ultimo_avatar_end = 0.0
        broll_gap = 0.0
        maior_gap_broll = 0.0

        for i, c in enumerate(cenas):
            d = _dur(c)
            inicio = _ts(c)
            duracao_total += d
            role = c.get("avatar_role")
            tem_avatar = bool(
                c.get("should_have_avatar")
                or c.get("uses_character") is True
                or (role and role in ROLES_FALAM)
            )
            if tem_avatar:
                avatar_total += d
                gap = inicio - ultimo_avatar_end
                checkpoints.append({
                    "scene_id": c.get("scene_index") if c.get("scene_index") is not None else c.get("id"),
                    "timestamp": cls._fmt(inicio),
                    "role": role or "AVATAR",
                    "duracao_s": round(d, 1),
                    "intervalo_desde_anterior_s": int(round(gap)) if i > 0 else 0,
                })
                schedule.append({
                    "timestamp": f"{cls._fmt(inicio)}-{cls._fmt(inicio + d)}",
                    "role": role or "AVATAR",
                    "interval_from_last": int(round(gap)) if i > 0 else 0,
                })
                ultimo_avatar_end = inicio + d
                broll_gap = 0.0
            else:
                broll_gap += d
                if broll_gap > maior_gap_broll:
                    maior_gap_broll = broll_gap

        avatar_count = len(checkpoints)
        avatar_percent_cenas = round(avatar_count / total * 100, 1) if total else 0.0
        avatar_percent_tempo = round(avatar_total / duracao_total * 100, 1) if duracao_total else 0.0

        validacoes, problemas, score = cls.validate_retention_pattern(cenas)

        return {
            "total_cenas": total,
            "total_duration": round(duracao_total, 1),
            "avatar_scenes": avatar_count,
            "avatar_scenes_percent": avatar_percent_cenas,
            "avatar_total_duration": round(avatar_total, 1),
            "avatar_duration_percent": avatar_percent_tempo,
            "broll_percent": round(100.0 - avatar_percent_tempo, 1),
            "maior_gap_broll_s": int(round(maior_gap_broll)),
            "avatar_checkpoints": checkpoints,
            "pattern_interrupts_schedule": schedule,
            "roles": dict(collections.Counter(str(c.get("avatar_role")) for c in cenas)),
            "validacoes": validacoes,
            "problemas": problemas,
            "score_retencao": score,
        }
    @staticmethod
    def validate_retention_pattern(scene_plan_or_cenas) -> tuple:
        """Check-list de retenção. Retorna (validacoes, problemas, score 0-75)."""
        cenas = scene_plan_or_cenas.get("cenas") if isinstance(scene_plan_or_cenas, dict) else scene_plan_or_cenas
        cenas = list(cenas or [])
        problemas: List[str] = []
        validacoes: Dict[str, bool] = {}
        if not cenas:
            return {"vazio": False}, ["plano vazio"], 0

        n = len(cenas)
        roles = [str(c.get("avatar_role") or "") for c in cenas]
        avatar_idx = [i for i, r in enumerate(roles) if r]
        tem_hook = bool(avatar_idx and avatar_idx[0] == 0)

        # Última cena com papel explícito de avatar (CTA/Conclusão)
        tem_cta_fim = bool(roles and roles[-1] in ("CTA", "CONCLUSÃO")) or bool(
            avatar_idx and avatar_idx[-1] >= n - 1
        )

        validacoes["avatar_na_abertura"] = tem_hook
        if not tem_hook:
            problemas.append("Cena 1 sem papel HOOK/avatar")

        value_idx = [i for i, r in enumerate(roles) if r == "VALUE"]
        validacoes["value_early"] = bool(value_idx) and value_idx[0] <= min(5, max(1, n // 6))
        if not validacoes["value_early"]:
            problemas.append("Sem VALUE nas primeiras cenas (promessa de valor)")

        ult_roles = [r for r in roles if r in ("CTA", "CONCLUSÃO")]
        validacoes["cta_at_end"] = tem_cta_fim
        validacoes["conclusao_no_fim"] = bool(ult_roles) and roles.index(ult_roles[-1]) >= n * 0.7
        if not validacoes["cta_at_end"]:
            problemas.append("Última cena sem papel CTA")
        if not validacoes["conclusao_no_fim"]:
            problemas.append("Sem CONCLUSÃO/CTA nos 20% finais")

        pct = (len(avatar_idx) / n) * 100 if n else 0
        validacoes["avatar_scenes_optimal"] = 5 <= len(avatar_idx) <= 10 and pct <= 15
        if not validacoes["avatar_scenes_optimal"]:
            problemas.append(f"Avatar cenas={len(avatar_idx)} ({pct:.0f}%) fora do alvo 6-9 / <=10%")

        # B-ROLL puro consecutivo máximo (usa duração real)
        gap_atual = 0.0
        maior_gap = 0.0
        for c in cenas:
            d = _dur(c)
            if c.get("uses_character") is True or c.get("should_have_avatar"):
                gap_atual = 0.0
            else:
                gap_atual += d
                maior_gap = max(maior_gap, gap_atual)
        validacoes["broll_max_consecutivo"] = maior_gap <= GAP_MAX_AVATAR_SEG
        if not validacoes["broll_max_consecutivo"]:
            problemas.append(f"B-ROLL puro de {maior_gap:.0f}s sem avatar (máx {GAP_MAX_AVATAR_SEG:.0f}s)")

        consec_meio = any(
            avatar_idx[i] + 1 == avatar_idx[i + 1] and avatar_idx[i + 1] < n - 1
            for i in range(len(avatar_idx) - 1)
        )
        validacoes["sem_avatar_consecutivo_no_meio"] = not consec_meio
        if consec_meio:
            problemas.append("Avatares consecutivos no corpo do vídeo")

        validacoes["checkpoints_meio"] = any(r == "CHECKPOINT" for r in roles)
        if not validacoes["checkpoints_meio"]:
            problemas.append("Sem CHECKPOINT de re-engajamento no meio")

        score = sum(1 for v in validacoes.values() if v) / max(1, len(validacoes))
        score = int(40 + score * 35)  # base 40 + bônus -> até 75
        score = min(75, score)

        return validacoes, problemas, score
