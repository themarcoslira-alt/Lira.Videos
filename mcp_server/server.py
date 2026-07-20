"""
server.py — Servidor MCP para ULTRACUT3
Ponto de entrada para conexão MCP, expõe as 9 ferramentas.
"""
import json
import sys
from mcp_server.app import FERRAMENTAS, executar_ferramenta


def handle_request(request: dict) -> dict:
    """
    Processa uma requisição MCP.
    Formato esperado: {"tool": "nome_da_ferramenta", "params": {...}}
    """
    tool_name = request.get("tool", "")
    params = request.get("params", {})

    if tool_name == "list_tools":
        # Retorna lista de ferramentas disponíveis
        tools_list = []
        for name, tool in FERRAMENTAS.items():
            tools_list.append({
                "name": name,
                "description": tool["description"],
                "parameters": tool["params"]
            })
        return {"tools": tools_list}

    result = executar_ferramenta(tool_name, params)
    return {"result": result}


def run_server_stdio():
    """
    Executa o servidor em modo stdio (para integração com Cline/VS Code).
    Lê requisições JSON de stdin, escreve respostas em stdout.
    """
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
            print(json.dumps(response, ensure_ascii=False), flush=True)
        except json.JSONDecodeError as e:
            error_response = {"error": f"JSON inválido: {str(e)}"}
            print(json.dumps(error_response, ensure_ascii=False), flush=True)
        except Exception as e:
            error_response = {"error": f"Erro interno: {str(e)}"}
            print(json.dumps(error_response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    run_server_stdio()