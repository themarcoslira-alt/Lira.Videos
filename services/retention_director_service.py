"""
services/retention_director_service.py — Retention Director AI (FASE 7)
========================================================================
Responsabilidade:
- Pensar como um criador de conteúdo / diretor de retenção audiovisual de alta performance.
- Regras de Retenção:
  1. Hook Magnético: Garante retenção máxima nos primeiros segundos com promessa instigante.
  2. Quebra de Padrão (Anti-Monotonia):
     Impede sequências repetitivas (avatar -> avatar -> avatar).
     Estrutura a cadência: Pessoa -> Detalhe -> Ação -> Resultado.
  3. Modulação de Ritmo: Controla velocidade e tempos de corte para maximizar engajamento.
  4. Open Loops / Ganchos de Curiosidade: Injeta âncoras de curiosidade conectando as cenas.
- Retorno:
  * Lista de cenas otimizadas com retention_index (0-100), retention_cues e pattern_interrupt.
"""

from typing import List, Dict, Any, Optional
from services.event_logger import log_event


def otimizar_retencao_projeto(
    cenas: List[Dict[str, Any]],
    contexto_visual: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Analisa a cadência global do roteiro e aplica as leis de retenção audiovisual.
    """
    total = len(cenas)
    if total == 0:
        return {"project_retention_score": 100, "scenes": []}

    padrao_ideal = ["avatar_talking", "broll_macro", "broll_action", "hybrid", "before_after", "cta"]
    consecutive_avatars = 0
    pattern_interrupts_count = 0

    for i, c in enumerate(cenas):
        stype = c.get("scene_type", "broll_macro")
        story_role = c.get("story_role", "explanation")
        uses_char = c.get("uses_character", False)

        # 1. Verificação e quebra de monotonia de avatares consecutivos
        if uses_char and stype in ("avatar_talking", "avatar_action", "hybrid"):
            consecutive_avatars += 1
        else:
            consecutive_avatars = 0

        # Se houver mais de 2 avatares seguidos, interrompe com foco em detalhe/ação
        if consecutive_avatars > 2:
            if i < total - 1:
                c["scene_type"] = "broll_macro" if (i % 2 == 0) else "broll_action"
                c["uses_character"] = False
                c["character_ref"] = ""
                c["pattern_interrupt"] = True
                pattern_interrupts_count += 1
                consecutive_avatars = 0
                log_event("RETENTION_DIRECTOR", f"Cena {c.get('id', i+1)}: Quebra de padrão aplicada (Avatar excessivo -> B-Roll dinâmico)")

        # 2. Cálculo do retention_index individual (0 a 100)
        ret_index = 80
        cues = []

        if i == 0 or story_role == "hook":
            ret_index = 98
            cues.append("Magnetic visual hook: immediate visual question presented")
        elif story_role in ("proof", "result"):
            ret_index = 95
            cues.append("High-payoff visual reward: clear transformation reveal")
        elif story_role == "problem":
            ret_index = 90
            cues.append("Empathy / pain point: visual frustration highlighted")
        elif story_role in ("demonstration", "process"):
            ret_index = 85
            cues.append("Step-by-step kinetic clarity: active hands-on guidance")
        elif story_role == "cta":
            ret_index = 88
            cues.append("Direct engagement invitation: viewer action driven")
        else:
            ret_index = 82
            cues.append("Atmospheric pacing bridge: visual texture immersion")

        c["retention_index"] = ret_index
        c["retention_cues"] = cues
        if "pattern_interrupt" not in c:
            c["pattern_interrupt"] = False

    # Pontuação global do projeto
    avg_score = int(sum(c.get("retention_index", 85) for c in cenas) / total)
    pacing_grade = "A+" if avg_score >= 90 else ("A" if avg_score >= 80 else "B")

    log_event("RETENTION_DIRECTOR", f"Retenção global calculada: {avg_score}/100 (Grade: {pacing_grade}) com {pattern_interrupts_count} quebras de padrão.")

    return {
        "project_retention_score": avg_score,
        "pacing_grade": pacing_grade,
        "pattern_interrupts_applied": pattern_interrupts_count,
        "scenes": cenas
    }
