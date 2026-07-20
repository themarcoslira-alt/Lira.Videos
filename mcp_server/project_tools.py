"""
project_tools.py — Ferramentas MCP para gerenciamento de projetos
3 ferramentas: criar_projeto, listar_projetos, deletar_projeto
"""
from services.pipeline_service import PipelineService


_pipeline = PipelineService()


def criar_projeto(nome: str) -> dict:
    """Cria um novo projeto."""
    return _pipeline.criar_projeto(nome)


def listar_projetos() -> list:
    """Lista todos os projetos existentes."""
    return _pipeline.listar_projetos()


def deletar_projeto(nome: str) -> dict:
    """Deleta um projeto e todos seus arquivos."""
    import shutil
    from config import PROJETOS_DIR

    project_dir = PROJETOS_DIR / nome
    if not project_dir.exists():
        return {"success": False, "error": f"Projeto '{nome}' não encontrado"}

    try:
        shutil.rmtree(project_dir)
        return {"success": True, "project": nome}
    except Exception as e:
        return {"success": False, "error": str(e)}