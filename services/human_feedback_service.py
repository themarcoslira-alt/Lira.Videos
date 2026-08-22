"""
services/human_feedback_service.py — Human Feedback System (FASE 11)
====================================================================
Responsabilidade:
- Permitir aprovação e intervenção humana nas decisões do Lira Studio 3.0.
- Estados de Cena:
  * pending
  * approved
  * revision_requested
- Cada cena armazena:
  {
    "human_status": str,
    "human_note": str,
    "approved_by": str
  }
"""

from typing import Dict, Any, Optional
from datetime import datetime
from services.event_logger import log_event
import services.scene_plan_service as scene_plan_svc
import services.production_metrics_engine as metrics_engine_svc


STATUS_FEEDBACK_VALIDOS = {"pending", "approved", "revision_requested"}


def registrar_feedback_humano(
    projeto_id: str,
    scene_id: int,
    status: str,
    note: str = "",
    approved_by: str = "User"
) -> Dict[str, Any]:
    """
    Registra a decisão/revisão humana de uma cena específica no scene plan.
    """
    st = status.strip().lower()
    if st not in STATUS_FEEDBACK_VALIDOS:
        return {
            "success": False,
            "error": f"Status inválido: '{status}'. Use 'approved', 'revision_requested' ou 'pending'."
        }

    plan = scene_plan_svc.carregar_scene_plan(projeto_id)
    if not plan:
        return {"success": False, "error": f"Scene plan do projeto '{projeto_id}' não encontrado."}

    cena_alvo = None
    for c in plan.get("cenas", []):
        if int(c.get("id", -1)) == int(scene_id):
            cena_alvo = c
            break

    if not cena_alvo:
        return {"success": False, "error": f"Cena #{scene_id} não encontrada no projeto '{projeto_id}'."}

    # Atualiza dados de feedback
    cena_alvo["human_status"] = st
    cena_alvo["human_note"] = note.strip()
    cena_alvo["approved_by"] = approved_by.strip() or "User"
    cena_alvo["human_feedback_at"] = datetime.now().isoformat(sep=" ", timespec="seconds")
    cena_alvo["manual_intervention"] = True

    # Se aprovado e o status de julgamento era pendente, sincroniza
    if st == "approved":
        cena_alvo["judgment_status"] = "approved"
    elif st == "revision_requested":
        cena_alvo["judgment_status"] = "revision_requested"

    # Salva scene plan
    scene_plan_svc.salvar_scene_plan(projeto_id, plan)

    # Recalcula métricas de produção automaticamente
    metricas = metrics_engine_svc.calcular_e_salvar_metricas(projeto_id)

    log_event("HUMAN_FEEDBACK", f"Projeto {projeto_id} | Cena #{scene_id}: Feedback registrado -> '{st}' por '{approved_by}'")

    return {
        "success": True,
        "scene_id": scene_id,
        "human_status": st,
        "human_note": note,
        "approved_by": approved_by,
        "updated_metrics": metricas
    }


def obter_resumo_feedback_projeto(projeto_id: str) -> Dict[str, Any]:
    """Retorna o consolidado de aprovações humanas do projeto."""
    plan = scene_plan_svc.carregar_scene_plan(projeto_id) or {"cenas": []}
    cenas = plan.get("cenas", [])
    
    total = len(cenas)
    approved = sum(1 for c in cenas if c.get("human_status") == "approved")
    revision = sum(1 for c in cenas if c.get("human_status") == "revision_requested")
    pending = total - approved - revision

    return {
        "total_cenas": total,
        "approved_count": approved,
        "revision_requested_count": revision,
        "pending_count": pending,
        "approval_percentage": round((approved / total) * 100, 1) if total > 0 else 0.0
    }
