"""
scene_builder.py — Divisão do roteiro em cenas
"""
import json
import re
from pathlib import Path
from config import PROJETOS_DIR


def gerar_cenas(project_name: str) -> dict:
    """
    Lê o roteiro_transcricao.txt e divide em cenas baseadas em pausas/topics.
    Salva cenas.json no diretório do projeto.
    """
    from services.event_logger import log_event

    log_event("SCENES", f"Iniciando geracao de cenas para {project_name}", level="info")

    project_dir = PROJETOS_DIR / project_name
    roteiro_file = project_dir / "roteiro_transcricao.txt"
    cenas_file = project_dir / "cenas.json"

    if not roteiro_file.exists():
        log_event("SCENES", "roteiro_transcricao.txt nao encontrado", level="error")
        return {"success": False, "error": "roteiro_transcricao.txt não encontrado"}

    with open(roteiro_file, "r", encoding="utf-8") as f:
        linhas = f.readlines()

    cenas = []
    current_scene = {"id": 1, "texto": "", "timestamps": [], "topic": ""}
    scene_count = 1

    for linha in linhas:
        linha = linha.strip()
        if not linha:
            continue

        # Extrai timestamp e texto
        match = re.match(r'\[(\d{2}:\d{2})\]\s*(.*)', linha)
        if match:
            timestamp = match.group(1)
            texto = match.group(2)

            # Se o texto é longo o suficiente, é uma nova cena
            if len(current_scene["texto"]) > 200:
                cenas.append(current_scene)
                scene_count += 1
                current_scene = {
                    "id": scene_count,
                    "texto": texto,
                    "timestamps": [timestamp],
                    "topic": ""
                }
            else:
                if current_scene["texto"]:
                    current_scene["texto"] += " " + texto
                else:
                    current_scene["texto"] = texto
                current_scene["timestamps"].append(timestamp)

    # Adiciona última cena
    if current_scene["texto"]:
        cenas.append(current_scene)

    # Se só tem uma cena, tenta dividir por pontuação
    if len(cenas) <= 1 and cenas:
        textos = re.split(r'[.!?]+', cenas[0]["texto"])
        textos = [t.strip() for t in textos if len(t.strip()) > 30]
        if len(textos) > 1:
            cenas = []
            for i, t in enumerate(textos):
                cenas.append({
                    "id": i + 1,
                    "texto": t,
                    "timestamps": cenas[0]["timestamps"],
                    "topic": ""
                })

    with open(cenas_file, "w", encoding="utf-8") as f:
        json.dump(cenas, f, indent=2, ensure_ascii=False)

    return {
        "success": True,
        "project": project_name,
        "cenas_count": len(cenas),
        "cenas": cenas
    }