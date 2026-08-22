"""
services/image_variation_selector_service.py — Image Variation Selector AI (FASE 5)
===================================================================================
Responsabilidade:
- Avaliar e comparar múltiplas variações de imagem geradas pelo Google Flow para uma mesma cena.
- Critérios de Comparação:
  1. PERSONAGEM (character_fidelity): Fidelidade facial e vestimenta de @Marcos.
  2. OBJETO (object_accuracy): Presença do adubo/prop correto vs substituições incorretas.
  3. CONTINUIDADE (continuity_alignment): Coerência com a Bíblia Visual do projeto.
  4. CINEMATOGRAFIA (cinematography_composition): Lente, profundidade de campo e enquadramento.
  5. QUALIDADE TÉCNICA (technical_quality): Nitidez, resolução e ausência de deformações.
- Retorno:
  * Escolha automática da melhor variação (winner_index, winner_path, score e justificativa técnica).
"""

from typing import List, Dict, Any, Optional
from pathlib import Path
from services.event_logger import log_event
import services.visual_judgment_service as visual_judgment_svc


def comparar_e_selecionar_melhor_variacao(
    projeto_id: str,
    cena: Dict[str, Any],
    variacoes: List[Dict[str, Any]],
    memoria_visual: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Compara múltiplas opções de imagens candidatas e elege automaticamente a melhor.
    Cada item em 'variacoes' pode conter:
      {"index": int, "image_path": str, "prompt": str, "image_bytes": bytes?}
    """
    if not variacoes:
        return {
            "winner_index": 0,
            "winner_path": "",
            "winner_score": 0,
            "ranked_variations": [],
            "selection_rationale": "Nenhuma variação fornecida para avaliação."
        }

    mem_proj = memoria_visual or {}
    avaliadas = []

    for var in variacoes:
        v_idx = var.get("index", len(avaliadas))
        v_path = var.get("image_path", "")
        v_prompt = var.get("prompt", cena.get("prompt_imagem", ""))

        cena_candidata = dict(cena)
        cena_candidata["prompt_imagem"] = v_prompt

        # Avaliação via Visual Judgment Engine
        judgment = visual_judgment_svc.avaliar_imagem_cena(
            projeto_id=projeto_id,
            cena=cena_candidata,
            memoria_visual=mem_proj,
            caminho_imagem=v_path
        )

        v_score = judgment["visual_score"]
        checks = judgment["checks"]
        
        # Breakdown pontuado dos 5 pilares (Total 100):
        # 1. Personagem: até 25 pts
        score_char = 25 if checks.get("character") else 5
        # 2. Objeto: até 25 pts
        score_obj = 25 if checks.get("object") else 5
        # 3. Continuidade: até 20 pts
        score_cont = 20 if checks.get("continuity") else 5
        # 4. Cinematografia / Composição: até 15 pts
        score_comp = 15 if checks.get("composition") else 5
        # 5. Qualidade Técnica (bônus de arquivo existente e íntegro): até 15 pts
        score_qual = 15 if v_path and Path(v_path).exists() and Path(v_path).stat().st_size > 1000 else 10

        total_var_score = min(100, score_char + score_obj + score_cont + score_comp + score_qual)

        # Ajusta pelo score do visual judgment
        final_score = int((total_var_score * 0.5) + (v_score * 0.5))

        avaliadas.append({
            "variation_index": v_idx,
            "image_path": v_path,
            "judgment_status": judgment["judgment_status"],
            "total_score": final_score,
            "breakdown": {
                "character": score_char,
                "object": score_obj,
                "continuity": score_cont,
                "composition": score_comp,
                "technical_quality": score_qual
            },
            "selection_reason": judgment["selection_reason"]
        })

    # Ordena da maior pontuação para a menor
    avaliadas.sort(key=lambda x: x["total_score"], reverse=True)
    vencedora = avaliadas[0]

    rationale = (
        f"Variação #{vencedora['variation_index']+1} selecionada com Score {vencedora['total_score']}/100. "
        f"Justificativa: {vencedora['selection_reason']}"
    )

    log_event("VARIATION_SELECTOR", f"Cena {cena.get('id', 1)}: Variação #{vencedora['variation_index']+1} eleita ({vencedora['total_score']} pts)")

    return {
        "winner_index": vencedora["variation_index"],
        "winner_path": vencedora["image_path"],
        "winner_score": vencedora["total_score"],
        "ranked_variations": avaliadas,
        "selection_rationale": rationale
    }
