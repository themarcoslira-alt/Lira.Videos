"""
pipeline_tools.py — Ferramentas MCP para execução do pipeline
3 ferramentas: executar_pipeline, step_status, cancelar_pipeline
"""
from services.pipeline_service import PipelineService


_pipeline = PipelineService()


def executar_pipeline(project_name: str, arquivo_video: str) -> dict:
    """Executa pipeline completo para um projeto."""
    _pipeline.project_name = project_name
    return _pipeline.executar_pipeline_completo(arquivo_video)


def step_status(project_name: str, step: str) -> dict:
    """Retorna o status de uma etapa específica."""
    _pipeline.project_name = project_name
    result = _pipeline.get_step_status(step)
    if result:
        return result
    return {"error": f"Etapa '{step}' não encontrada"}


def cancelar_pipeline() -> dict:
    """Cancela a execução do pipeline em andamento."""
    _pipeline.cancelar()
    return {"success": True, "message": "Pipeline cancelado"}