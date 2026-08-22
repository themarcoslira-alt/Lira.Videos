"""
services/content_learning_engine.py — Content Learning Engine (FASE 8)
======================================================================
Responsabilidade:
- Memória central de aprendizado contínuo do Lira Studio.
- Rastreia e armazena:
  * Vídeos/projetos bem-sucedidos (score >= 85) e de baixo desempenho.
  * Hooks vencedores (fórmulas e estruturas que geraram retenção máxima).
  * Estilos visuais e configurações de câmera mais eficientes.
  * Padrões reprovados / negativos a serem evitados.
- Fornece recomendações automáticas para futuros roteiros e direções.
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
from config import PROJETOS_DIR, BASE_DIR
from services.event_logger import log_event

GLOBAL_LEARNING_FILE = BASE_DIR / "data" / "content_learning_memory.json"


def _get_learning_file() -> Path:
    GLOBAL_LEARNING_FILE.parent.mkdir(parents=True, exist_ok=True)
    return GLOBAL_LEARNING_FILE


def obter_memoria_aprendizado() -> Dict[str, Any]:
    """Recupera a base de aprendizado global ou inicializa com dados canônicos."""
    f = _get_learning_file()
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            log_event("CONTENT_LEARNING", f"Erro ao ler memória de aprendizado: {e}", level="warn")

    # Estado inicial de inteligência pré-treinada
    memoria_padrao = {
        "versao": "3.0",
        "atualizado_em": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "total_projetos_analisados": 0,
        "hooks_vencedores": [
            {
                "formula": "Mistake & Mystery Hook",
                "exemplo": "Hoje eu vou revelar o maior erro que cometi no meu cultivo...",
                "retention_score": 98,
                "categoria": "botanical_gardening"
            },
            {
                "formula": "Breakthrough Discovery Hook",
                "exemplo": "Descobri o adubo orgânico secreto que recupera plantas em 3 dias...",
                "retention_score": 96,
                "categoria": "sustainability"
            }
        ],
        "estilos_eficientes": [
            {
                "aesthetic": "photorealistic_cinematic",
                "lenses": ["35mm", "50mm", "85mm macro"],
                "lighting": "natural morning daylight with directional contrast",
                "avg_visual_score": 94
            }
        ],
        "padroes_reprovados": [
            "Sequência monótona de 3 ou mais avatares seguidos",
            "Uso de tags genéricas (@Homem, @Pessoa)",
            "Substituição de adubo/compost por frutos inteiros não processados",
            "Ausência de enquadramento ou lentes definidas"
        ],
        "historico_projetos": []
    }
    salvar_memoria_aprendizado(memoria_padrao)
    return memoria_padrao


def salvar_memoria_aprendizado(data: Dict[str, Any]) -> bool:
    """Salva os dados consolidados da memória de aprendizado."""
    try:
        f = _get_learning_file()
        data["atualizado_em"] = datetime.now().isoformat(sep=" ", timespec="seconds")
        f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception as e:
        log_event("CONTENT_LEARNING", f"Erro ao salvar aprendizado: {e}", level="error")
        return False


def registrar_aprendizado_projeto(
    projeto_id: str,
    scene_plan: Dict[str, Any],
    visual_context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Analisa o resultado de um projeto finalizado e incorpora seus aprendizados.
    """
    mem = obter_memoria_aprendizado()
    cenas = scene_plan.get("cenas", [])
    if not cenas:
        return mem

    # 1. Calcula score médio do projeto
    scores_visuais = [c.get("visual_score", 85) for c in cenas if c.get("visual_score", 0) > 0]
    avg_score = int(sum(scores_visuais) / len(scores_visuais)) if scores_visuais else 85

    # 2. Registra Hook se score foi alto
    c1 = cenas[0]
    if c1.get("story_role") == "hook" and avg_score >= 85:
        novo_hook = {
            "formula": "High-Retention Hook",
            "exemplo": c1.get("narration", "")[:120],
            "retention_score": c1.get("retention_index", 95),
            "projeto": projeto_id
        }
        # Evita duplicatas
        if not any(h.get("exemplo") == novo_hook["exemplo"] for h in mem["hooks_vencedores"]):
            mem["hooks_vencedores"].append(novo_hook)

    # 3. Adiciona histórico
    mem["total_projetos_analisados"] += 1
    mem["historico_projetos"].append({
        "projeto_id": projeto_id,
        "total_cenas": len(cenas),
        "avg_visual_score": avg_score,
        "data": datetime.now().isoformat(sep=" ", timespec="seconds")
    })

    salvar_memoria_aprendizado(mem)
    log_event("CONTENT_LEARNING", f"Projeto '{projeto_id}' indexado com sucesso na memória de aprendizado (Score Médio: {avg_score}).")
    return mem


def obter_recomendacoes_aprendidas(tema: str = "") -> Dict[str, Any]:
    """Retorna os melhores padrões aprendidos para orientar novas gerações."""
    mem = obter_memoria_aprendizado()
    return {
        "top_hooks": mem.get("hooks_vencedores", [])[:3],
        "top_styles": mem.get("estilos_eficientes", []),
        "anti_patterns": mem.get("padroes_reprovados", [])
    }
