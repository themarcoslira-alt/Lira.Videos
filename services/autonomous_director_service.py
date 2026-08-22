"""
services/autonomous_director_service.py — Autonomous Director AI (FASE 10)
===========================================================================
Responsabilidade:
- Orquestrador Mestre Autônomo do Lira Studio 3.0.
- O usuário fornece apenas o roteiro / transcrição (ou áudio).
- O Autonomous Director toma TODAS as decisões automaticamente:
  * Divisão e classificação de cenas (Scene Classifier)
  * Ritmo narrativo e retenção (Retention & Storyboard Director)
  * Direção de enquadramento, lente e luz (Camera Director)
  * Vinculação estrita de personagem Flow (Character Decision @Marcos)
  * Definição de quais cenas viram vídeo (Animation Director)
  * Prompts cinematográficos limpos (Prompt Builder)
  * Bíblia visual e continuidade (Visual Memory Engine)
  * Histórico de decisões (Prompt History System)
  * Indexação no aprendizado contínuo (Content Learning Engine)
"""

import re
import json
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
from config import PROJETOS_DIR
from services.event_logger import log_event
import services.scene_plan_service as scene_plan_svc
import services.content_learning_engine as learning_svc
import services.character_service as character_svc


def dirigir_producao_autonoma(
    projeto_id: str,
    roteiro_texto: str,
    nome_personagem: str = "",
    estilo_visual: str = "photorealistic_cinematic"
) -> Dict[str, Any]:
    """
    Executa a direção audiovisual autônoma completa do projeto a partir do roteiro bruto.
    """
    print(f"\n[AUTONOMOUS_DIRECTOR] Iniciando direção autônoma completa para '{projeto_id}'...", flush=True)
    log_event("AUTONOMOUS_DIRECTOR", f"Iniciando direção autônoma do projeto '{projeto_id}'")

    pdir = PROJETOS_DIR / projeto_id
    scene_plan_svc.garantir_estrutura_pastas(projeto_id)

    # 1. Configura Identidade se especificado
    if nome_personagem:
        ident_atual = character_svc.obter_identidade_projeto(projeto_id) or {}
        if not ident_atual.get("nome"):
            character_svc.salvar_identidade_projeto(
                projeto_id=projeto_id,
                tipo="personagem",
                nome=nome_personagem,
                referencia_flow=f"@{nome_personagem}",
                visual_style=estilo_visual
            )

    # 2. Parsing do SRT / Roteiro
    texto_limpo = roteiro_texto.strip()
    segmentos = []

    # Parsing por timestamps [MM:SS]
    for m in re.finditer(r"\[?\s*(\d{1,2}):(\d{2})\s*\]?\s+(.+)", texto_limpo):
        start = int(m.group(1)) * 60 + int(m.group(2))
        end = start + 5.0
        segmentos.append({
            "start": float(start),
            "end": float(end),
            "text": m.group(3).strip(),
            "timestamp": f"{int(m.group(1)):02d}:{m.group(2)}",
        })

    # Parsing padrão por blocos SRT ou linhas de texto
    if not segmentos:
        linhas = [l.strip() for l in texto_limpo.splitlines() if l.strip()]
        tempo_atual = 0.0
        for linha in linhas:
            if re.match(r"^\d+$", linha) or "-->" in linha:
                continue
            segmentos.append({
                "start": round(tempo_atual, 2),
                "end": round(tempo_atual + 4.0, 2),
                "text": linha,
                "timestamp": f"{int(tempo_atual//60):02d}:{int(tempo_atual%60):02d}",
            })
            tempo_atual += 4.0

    if not segmentos:
        return {"success": False, "error": "Nenhum segmento ou fala identificado no roteiro."}

    # Salva cenas.json
    cenas_raw = []
    for idx, seg in enumerate(segmentos, 1):
        cenas_raw.append({
            "id": idx,
            "start_time": seg["start"],
            "end_time": seg["end"],
            "texto": seg["text"],
            "timestamps": [seg.get("timestamp", f"{int(seg['start']//60):02d}:{int(seg['start']%60):02d}")],
        })
    (pdir / "cenas.json").write_text(json.dumps(cenas_raw, indent=2, ensure_ascii=False), encoding="utf-8")

    # 3. Executa a geração do Scene Plan com o pipeline do Diretor
    res_plan = scene_plan_svc.gerar_scene_plan(projeto=projeto_id, force=True)
    if not res_plan.get("success"):
        return {"success": False, "error": res_plan.get("error", "Erro ao gerar scene plan")}

    plan = res_plan.get("plan", {})
    cenas = plan.get("cenas", [])

    # 4. Registra na memória de aprendizado contínuo
    learning_svc.registrar_aprendizado_projeto(
        projeto_id=projeto_id,
        scene_plan=plan,
        visual_context=plan.get("visual_context")
    )

    # 5. Coleta e consolida métricas de produção (FASE 11)
    import services.production_metrics_service as metrics_svc
    import services.visual_memory_engine as vme_svc
    mem_visual = vme_svc.obter_memoria_visual_projeto(projeto_id)
    collector = metrics_svc.ProductionMetricsCollector(projeto_id)
    relatorio_metricas = collector.calcular_relatorio_producao(plan, mem_visual)

    # 6. Sumário das decisões autônomas
    total = len(cenas)
    animadas = sum(1 for c in cenas if c.get("animate_later"))
    personagens = sum(1 for c in cenas if c.get("uses_character"))
    brolls = total - personagens

    sumario = {
        "projeto_id": projeto_id,
        "total_cenas": total,
        "cenas_com_personagem": personagens,
        "cenas_broll": brolls,
        "cenas_animadas_video": animadas,
        "estilo_visual": estilo_visual,
        "production_readiness_index": relatorio_metricas.get("production_readiness_index", 100),
        "readiness_grade": relatorio_metricas.get("readiness_grade", "PRODUCTION_READY"),
        "status": "ready_for_production",
        "concluido_em": datetime.now().isoformat(sep=" ", timespec="seconds")
    }

    print(f"[AUTONOMOUS_DIRECTOR] Direção concluída com sucesso: {total} cenas estruturadas ({personagens} avatar, {brolls} b-roll, {animadas} animadas). Readiness: {sumario['production_readiness_index']}/100", flush=True)
    log_event("AUTONOMOUS_DIRECTOR", f"Direção concluída: {sumario}")

    return {
        "success": True,
        "summary": sumario,
        "metrics": relatorio_metricas,
        "plan": plan
    }
