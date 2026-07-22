"""
media_search.py — Estratégia de busca em 2 passadas (sem reuso, sem biblioteca cega)
Usa o pool de até 15 candidatos por query, testa quality real após download.
"""
import json
from typing import Optional
from config import PROJETOS_DIR, MAX_SEARCH_ATTEMPTS_PASSA1, MAX_SEARCH_ATTEMPTS_PASSA2
from services.media_fetcher import buscar_midias, baixar_e_classificar
from services.library import find_relevant_entry, adicionar_media_biblioteca


def _testar_candidatos_por_query(candidatos: list, scene_id: int) -> Optional[dict]:
    """
    Testa múltiplos candidatos da MESMA query.
    Ordena por score, testa em ordem.
    Retorna o primeiro green encontrado, ou None se todos falharem.
    """
    # Ordena por score
    candidatos.sort(key=lambda x: -x.get("score", 0))

    for cand in candidatos:
        resultado = baixar_e_classificar(cand, scene_id)
        if resultado and resultado.get("success") and resultado["quality"] == "green":
            return resultado

    # Se não achou green, retorna None (tentativa gasta)
    return None


def _passada_busca(queries_cena: list, scene_id: int, max_attempts: int,
                   aceitar_yellow: bool = False) -> Optional[dict]:
    """
    Executa uma passada de busca.
    Tenta até max_attempts queries diferentes.
    Cada query pode ter múltiplos candidatos.
    """
    attempts = 0

    for query_item in queries_cena:
        if attempts >= max_attempts:
            break

        query = query_item["query"]
        media_type = query_item.get("media_type", "video")

        # Busca candidatos para esta query
        candidatos = buscar_midias(query, media_type)
        if not candidatos:
            attempts += 1
            continue

        # Testa candidatos desta query
        resultado = _testar_candidatos_por_query(candidatos, scene_id)
        if resultado:
            if resultado["quality"] == "green":
                return resultado
            elif aceitar_yellow and resultado["quality"] == "yellow":
                return resultado

        attempts += 1

    return None


def buscar_para_cena(scene_data: dict, queries_cena: list,
                     biblioteca_reuse_count: int = 0) -> dict:
    """
    Estratégia completa de busca para uma cena:
    1. 1ª passada (só GREEN) - até 6 tentativas, troca vídeo→foto na metade
    2. 2ª passada (GREEN ou YELLOW) - até 6 tentativas
    3. Fallback: biblioteca (máx 2 reusos por projeto)
    4. Se nada funcionar: needs_media = True
    """
    from services.event_logger import log_event
    scene_id = scene_data["id"]
    preference = scene_data.get("media_preference", "video")

    # 1ª passada: só GREEN
    # Separa queries em vídeo e foto
    queries_video = [q for q in queries_cena if q.get("media_type") == "video"]
    queries_photo = [q for q in queries_cena if q.get("media_type") == "photo"]

    if preference == "video":
        # 3 tentativas vídeo, depois 3 tentativas foto
        video_attempts = min(3, len(queries_video))
        photo_attempts = MAX_SEARCH_ATTEMPTS_PASSA1 - video_attempts
        first_half = queries_video[:video_attempts] + queries_photo[:photo_attempts]
    else:
        # 6 tentativas de foto direto
        first_half = queries_photo[:MAX_SEARCH_ATTEMPTS_PASSA1]

    resultado = _passada_busca(
        first_half, scene_id, MAX_SEARCH_ATTEMPTS_PASSA1, aceitar_yellow=False
    )
    if resultado:
        resultado["passada"] = 1
        resultado["scene_id"] = scene_id
        log_event("MEDIA_FETCH", f"Cena {scene_id}: GREEN na 1ª passada ({resultado['source']})", level="info")
        adicionar_media_biblioteca(resultado, scene_data)
        return resultado

    log_event("MEDIA_FETCH", f"Cena {scene_id}: iniciando 2ª passada (GREEN ou YELLOW)", level="info")

    # 2ª passada: GREEN ou YELLOW
    resto_queries = queries_video + queries_photo
    resultado = _passada_busca(
        resto_queries, scene_id, MAX_SEARCH_ATTEMPTS_PASSA2, aceitar_yellow=True
    )
    if resultado:
        resultado["passada"] = 2
        resultado["scene_id"] = scene_id
        # Adiciona à biblioteca
        adicionar_media_biblioteca(resultado, scene_data)
        return resultado

    # Fallback: biblioteca
    if biblioteca_reuse_count < 2:
        entry = find_relevant_entry(
            scene_data.get("keywords", []),
            scene_data.get("scene_type", "")
        )
        if entry:
            resultado = {
                "success": True,
                "quality": entry.get("quality", "yellow"),
                "arquivo": entry.get("arquivo", ""),
                "width": entry.get("width", 0),
                "height": entry.get("height", 0),
                "source": entry.get("source", "biblioteca"),
                "media_type": entry.get("media_type", "video"),
                "photographer": entry.get("photographer", ""),
                "id": entry.get("id", ""),
                "reused": True,
                "reuse_tier": entry.get("reuse_tier", 1),
                "passada": "biblioteca",
                "scene_id": scene_id
            }
            return resultado

    # Nada funcionou
    return {
        "success": False,
        "needs_media": True,
        "passada": "falhou",
        "scene_id": scene_id
    }


def buscar_midias_projeto(project_name: str) -> dict:
    """
    Executa busca completa para todas as cenas do projeto.
    """
    project_dir = PROJETOS_DIR / project_name
    storyboard_file = project_dir / "storyboard.json"
    queries_file = project_dir / "queries.json"
    resultado_file = project_dir / "midias_encontradas.json"

    if not storyboard_file.exists():
        return {"success": False, "error": "storyboard.json não encontrado"}
    if not queries_file.exists():
        return {"success": False, "error": "queries.json não encontrado"}

    with open(storyboard_file, "r", encoding="utf-8") as f:
        storyboard = json.load(f)
    with open(queries_file, "r", encoding="utf-8") as f:
        queries_data = json.load(f)

    # Mapeia queries por scene_id
    queries_map = {q["scene_id"]: q["candidates"] for q in queries_data}

    resultados = []
    biblioteca_reuse_count = 0
    needs_media_count = 0

    for scene in storyboard:
        scene_id = scene["id"]
        queries_cena = queries_map.get(scene_id, [])

        resultado = buscar_para_cena(
            scene, queries_cena, biblioteca_reuse_count
        )

        if resultado.get("reused"):
            biblioteca_reuse_count += 1

        if resultado.get("needs_media"):
            needs_media_count += 1

        resultados.append(resultado)

    with open(resultado_file, "w", encoding="utf-8") as f:
        json.dump(resultados, f, indent=2, ensure_ascii=False)

    green_count = sum(1 for r in resultados if r.get("quality") == "green")
    yellow_count = sum(1 for r in resultados if r.get("quality") == "yellow")
    reused_count = sum(1 for r in resultados if r.get("reused"))

    return {
        "success": True,
        "project": project_name,
        "total_scenes": len(resultados),
        "green": green_count,
        "yellow": yellow_count,
        "reused": reused_count,
        "needs_media": needs_media_count,
        "resultados": resultados
    }