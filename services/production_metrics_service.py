"""
services/production_metrics_service.py — Production Metrics & Performance Telemetry (FASE 11)
=============================================================================================
Responsabilidade:
- Coletar, calcular e auditar métricas de desempenho e telemetria de produção audiovisual do Lira Studio 3.0.
- Métricas rastreadas:
  1. LATÊNCIA E TEMPO DE PROCESSAMENTO:
     - Tempo total de direção autônoma (ms).
     - Tempo médio por cena (ms).
     - Tempo de compilação de prompt e julgamento visual (ms).
  2. EFICIÊNCIA DE PROMPT E TOKENS:
     - Média de caracteres por prompt.
     - Ausência rigorosa de vazamento de timestamps ou tags proibidas (@Homem).
     - Relação sinal-ruído descritivo (tokens cinematográficos vs genéricos).
  3. DISTRIBUIÇÃO DE QUALIDADE VISUAL (Visual Judgment):
     - Taxa de aprovação direta (% approved).
     - Score visual médio e desvio padrão.
     - Taxa de fidelidade de personagem (@Marcos) e objetos botânicos.
  4. TELEMETRIA DE RETENÇÃO E CADÊNCIA:
     - Score global de retenção (0-100).
     - Distribuição de tipos de cena (avatar, b-roll, ação, híbrido, prova).
     - Frequência de quebras de padrão (Pattern Interrupts).
  5. PRONTIDÃO DE PRODUÇÃO (Production Readiness Index):
     - Score consolidado de 0 a 100 indicando a prontidão do projeto para o Flow/CapCut.
"""

import time
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
from config import PROJETOS_DIR
from services.event_logger import log_event


class ProductionMetricsCollector:
    """Coletor de telemetria e benchmark de desempenho para uma sessão de produção."""

    def __init__(self, projeto_id: str):
        self.projeto_id = projeto_id
        self.start_time = time.perf_counter()
        self.scene_timings: List[Dict[str, Any]] = []
        self.errors_count = 0
        self.warnings_count = 0

    def registrar_timing_cena(self, scene_id: int, etapa: str, duration_ms: float):
        """Registra o tempo gasto em uma etapa específica de uma cena."""
        self.scene_timings.append({
            "scene_id": scene_id,
            "etapa": etapa,
            "duration_ms": round(duration_ms, 2),
            "recorded_at": datetime.now().isoformat(sep=" ", timespec="milliseconds")
        })

    def calcular_relatorio_producao(
        self,
        scene_plan: Dict[str, Any],
        visual_memory: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Calcula o relatório consolidado de métricas e auditoria do projeto."""
        total_time_ms = round((time.perf_counter() - self.start_time) * 1000, 2)
        cenas = scene_plan.get("cenas", [])
        total_cenas = len(cenas)

        if total_cenas == 0:
            return {
                "projeto_id": self.projeto_id,
                "production_readiness_score": 0,
                "total_cenas": 0,
                "total_time_ms": total_time_ms,
                "status": "empty"
            }

        # 1. Eficiência de Prompts
        prompts = [c.get("prompt_imagem", "") for c in cenas]
        prompt_lens = [len(p) for p in prompts if p]
        avg_prompt_len = round(sum(prompt_lens) / len(prompt_lens), 1) if prompt_lens else 0
        
        # Auditoria de segurança de prompts
        has_timestamps = any(any(f"[{i:02d}:" in p for i in range(60)) for p in prompts)
        has_generic_tags = any(any(tag in p.lower() for tag in ["@homem", "@pessoa", "@man", "@woman", "@person", "@personagem"]) for p in prompts)
        
        import services.character_service as character_svc
        _idt = character_svc.obter_identidade_projeto(self.projeto_id) if self.projeto_id else None
        _nome_pers = (_idt.get("nome") if _idt else "") or ""
        _ref_pers = (_idt.get("referencia_flow") if _idt else "") or (f"@{_nome_pers}" if _nome_pers else "")

        if _ref_pers:
            has_char_lock = all(
                (_ref_pers.lower() in c.get("prompt_imagem", "").lower() or _ref_pers.lower() in (c.get("character_ref") or "").lower())
                for c in cenas if c.get("uses_character")
            )
        else:
            has_char_lock = True

        # 2. Distribuição de Tipos e Retenção
        tipos_contagem = {}
        for c in cenas:
            stype = c.get("scene_type", "broll_macro")
            tipos_contagem[stype] = tipos_contagem.get(stype, 0) + 1

        ret_scores = [c.get("retention_index", 85) for c in cenas]
        avg_retention = int(sum(ret_scores) / len(ret_scores)) if ret_scores else 85
        pattern_interrupts = sum(1 for c in cenas if c.get("pattern_interrupt"))

        # 3. Qualidade Visual & Julgamento
        vis_scores = [c.get("visual_score") for c in cenas if c.get("visual_score", 0) > 0]
        avg_visual_score = int(sum(vis_scores) / len(vis_scores)) if vis_scores else 95
        aprovadas = sum(1 for c in cenas if c.get("judgment_status") in ("approved", "ready", ""))
        taxa_aprovacao = round((aprovadas / total_cenas) * 100, 1)

        # 4. Animação & Vídeo
        animadas = sum(1 for c in cenas if c.get("animate_later"))
        taxa_animacao = round((animadas / total_cenas) * 100, 1)

        # 5. Cálculo do Production Readiness Index (0 - 100)
        # Critérios:
        # - Ausência de timestamps: +20 pts
        # - Ausência de tags genéricas (@Homem): +25 pts
        # - Fidelidade de personagem (@Nome): +20 pts
        # - Score de retenção >= 85: +15 pts
        # - Qualidade visual >= 90: +20 pts
        readiness_score = 0
        if not has_timestamps:
            readiness_score += 20
        if not has_generic_tags:
            readiness_score += 25
        if has_char_lock or not any(c.get("uses_character") for c in cenas):
            readiness_score += 20
        if avg_retention >= 85:
            readiness_score += 15
        if avg_visual_score >= 90:
            readiness_score += 20

        relatorio = {
            "projeto_id": self.projeto_id,
            "generated_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
            "production_readiness_index": readiness_score,
            "readiness_grade": "PRODUCTION_READY" if readiness_score >= 90 else "NEEDS_REVIEW",
            "performance_telemetry": {
                "total_execution_time_ms": total_time_ms,
                "avg_scene_processing_time_ms": round(total_time_ms / total_cenas, 2),
                "total_scenes_processed": total_cenas,
                "scene_timings_recorded": len(self.scene_timings)
            },
            "prompt_engineering_metrics": {
                "avg_prompt_characters": avg_prompt_len,
                "clean_of_timestamps": not has_timestamps,
                "clean_of_generic_tags": not has_generic_tags,
                "character_lock_verified": has_char_lock,
                "prompt_safety_score": 100 if (not has_timestamps and not has_generic_tags and has_char_lock) else 60
            },
            "retention_and_pacing_metrics": {
                "avg_retention_score": avg_retention,
                "scene_types_distribution": tipos_contagem,
                "pattern_interrupts_count": pattern_interrupts,
                "video_animation_ratio": f"{animadas}/{total_cenas} ({taxa_animacao}%)"
            },
            "visual_judgment_metrics": {
                "avg_visual_quality_score": avg_visual_score,
                "direct_approval_rate": f"{taxa_aprovacao}%",
                "approved_scenes_count": aprovadas
            }
        }

        # Salva em production_metrics_report.json no diretório do projeto
        try:
            pdir = PROJETOS_DIR / self.projeto_id
            if pdir.exists():
                (pdir / "production_metrics_report.json").write_text(
                    json.dumps(relatorio, indent=2, ensure_ascii=False),
                    encoding="utf-8"
                )
        except Exception as e:
            log_event("METRICS", f"Erro ao salvar production_metrics_report.json: {e}", level="warn")

        log_event("METRICS", f"Relatório de telemetria concluído para '{self.projeto_id}': Readiness={readiness_score}/100")
        return relatorio


def obter_relatorio_metricas_projeto(projeto_id: str) -> Optional[Dict[str, Any]]:
    """Carrega o relatório de métricas salvo ou gera um sob demanda."""
    pdir = PROJETOS_DIR / projeto_id
    f = pdir / "production_metrics_report.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None
