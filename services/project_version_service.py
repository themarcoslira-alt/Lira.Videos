"""
services/project_version_service.py — Project Versioning Service (FASE 11)
==========================================================================
Responsabilidade:
- Manter o histórico imutável de versões de produção do vídeo em project_versions.json.
- Permite rastrear iterações (v1, v2, v3...), alterações de prompt e ajustes de direção.
- Regra Imutável: Nunca sobrescrever versões anteriores.
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
from config import PROJETOS_DIR
from services.event_logger import log_event
import services.scene_plan_service as scene_plan_svc


def _get_versions_file(projeto_id: str) -> Path:
    pdir = PROJETOS_DIR / projeto_id
    pdir.mkdir(parents=True, exist_ok=True)
    return pdir / "project_versions.json"


def obter_historico_versoes(projeto_id: str) -> Dict[str, Any]:
    """Retorna o histórico completo de versões do projeto."""
    vf = _get_versions_file(projeto_id)
    if vf.exists():
        try:
            return json.loads(vf.read_text(encoding="utf-8"))
        except Exception as e:
            log_event("PROJECT_VERSION", f"Erro ao ler project_versions.json de {projeto_id}: {e}", level="warn")

    # Inicializa v0 / baseline
    inicial = {
        "project_id": projeto_id,
        "current_version": "v1",
        "total_versions": 0,
        "versions": []
    }
    return inicial


def criar_nova_versao(
    projeto_id: str,
    changes: Optional[List[str]] = None,
    snapshot_customizado: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Cria uma nova versão imutável do projeto com snapshot do scene_plan e log de alterações.
    """
    hist = obter_historico_versoes(projeto_id)
    lista_versoes = hist.get("versions", [])
    
    num_proxima = len(lista_versoes) + 1
    tag_versao = f"v{num_proxima}"

    # Snapshot do scene plan atual
    snapshot = snapshot_customizado or scene_plan_svc.carregar_scene_plan(projeto_id) or {}
    mudancas = changes if changes is not None else [f"Initial visual direction for {tag_versao}"]

    nova_entrada = {
        "version": tag_versao,
        "created_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "changes": mudancas,
        "total_scenes": len(snapshot.get("cenas", [])),
        "snapshot": snapshot
    }

    lista_versoes.append(nova_entrada)
    hist["current_version"] = tag_versao
    hist["total_versions"] = len(lista_versoes)
    hist["versions"] = lista_versoes
    hist["updated_at"] = datetime.now().isoformat(sep=" ", timespec="seconds")

    vf = _get_versions_file(projeto_id)
    vf.write_text(json.dumps(hist, indent=2, ensure_ascii=False), encoding="utf-8")

    log_event("PROJECT_VERSION", f"Projeto {projeto_id}: Nova versão '{tag_versao}' registrada com {len(mudancas)} alterações.")
    return nova_entrada


def registrar_alteracao_cena_versao(
    projeto_id: str,
    scene_id: int,
    descricao_alteracao: str
) -> Dict[str, Any]:
    """Registra uma alteração específica e gera snapshot incremental."""
    hist = obter_historico_versoes(projeto_id)
    if not hist.get("versions"):
        return criar_nova_versao(projeto_id, changes=[descricao_alteracao])

    # Cria nova versão para a modificação
    return criar_nova_versao(
        projeto_id=projeto_id,
        changes=[f"Scene #{scene_id:03d}: {descricao_alteracao}"]
    )
