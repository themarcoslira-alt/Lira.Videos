"""
app.py — Aplicação MCP server para ULTRACUT3
Registra e expõe as 9 ferramentas via protocolo MCP.
"""
from mcp_server.project_tools import criar_projeto, listar_projetos, deletar_projeto
from mcp_server.pipeline_tools import executar_pipeline, step_status, cancelar_pipeline
from mcp_server.log_tools import log_event, session_report
from mcp_server.system_tools import system_info


# Registro central de ferramentas
FERRAMENTAS = {
    "criar_projeto": {
        "fn": criar_projeto,
        "params": {"nome": {"type": "string", "required": True}},
        "description": "Cria um novo projeto de vídeo"
    },
    "listar_projetos": {
        "fn": listar_projetos,
        "params": {},
        "description": "Lista todos os projetos existentes"
    },
    "deletar_projeto": {
        "fn": deletar_projeto,
        "params": {"nome": {"type": "string", "required": True}},
        "description": "Deleta um projeto e todos seus arquivos"
    },
    "executar_pipeline": {
        "fn": executar_pipeline,
        "params": {
            "project_name": {"type": "string", "required": True},
            "arquivo_video": {"type": "string", "required": True}
        },
        "description": "Executa pipeline completo (5 etapas) para um projeto"
    },
    "step_status": {
        "fn": step_status,
        "params": {
            "project_name": {"type": "string", "required": True},
            "step": {"type": "string", "required": True}
        },
        "description": "Retorna status de uma etapa específica"
    },
    "cancelar_pipeline": {
        "fn": cancelar_pipeline,
        "params": {},
        "description": "Cancela a execução do pipeline em andamento"
    },
    "log_event": {
        "fn": log_event,
        "params": {
            "level": {"type": "string", "required": True},
            "category": {"type": "string", "required": True},
            "event": {"type": "string", "required": True},
            "message": {"type": "string", "required": True},
            "details": {"type": "object", "required": False}
        },
        "description": "Registra um evento no log"
    },
    "session_report": {
        "fn": session_report,
        "params": {
            "minutes": {"type": "integer", "required": False, "default": 120}
        },
        "description": "Gera relatório markdown da sessão atual"
    },
    "system_info": {
        "fn": system_info,
        "params": {},
        "description": "Retorna informações do sistema (OS, ffmpeg, GPU)"
    }
}


def executar_ferramenta(nome: str, params: dict = None) -> dict:
    """Executa uma ferramenta pelo nome."""
    if nome not in FERRAMENTAS:
        return {"error": f"Ferramenta '{nome}' não encontrada"}

    tool = FERRAMENTAS[nome]
    params = params or {}

    try:
        # Valida params obrigatórios
        for pname, pinfo in tool["params"].items():
            if pinfo.get("required") and pname not in params:
                # Usa valor default se existir
                if "default" in pinfo:
                    params[pname] = pinfo["default"]
                else:
                    return {"error": f"Parâmetro obrigatório '{pname}' não fornecido"}

        return tool["fn"](**params)
    except Exception as e:
        return {"error": str(e)}