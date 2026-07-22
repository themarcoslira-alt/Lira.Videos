"""
media_search.py — Busca paralela simplificada com anti-reuso.
Usa buscar_midias_paralelo (3 threads) e used_urls set.
"""
import json
from typing import Optional
from config import PROJETOS_DIR
from services.media_fetcher import buscar_midias_paralelo, baixar_e_classificar


def _carregar_used_urls(project_name: str) -> set:
    """Carrega conjunto de URLs já usadas do metadata do projeto."""
    meta_file = PROJETOS_DIR / project_name / "meta.json"
    if meta_file.exists():
        try:
            meta = json.loads(open(str(meta_file), "r", encoding="utf-8").read())
            return set(meta.get("used_urls", []))
        except Exception:
            pass
    return set()


def _salvar_used_urls(project_name: str, used_urls: set):
    """Salva conjunto de URLs já usadas no metadata do projeto."""
    meta_file = PROJETOS_DIR / project_name / "meta.json"
    try:
        meta = json.loads(open(str(meta_file), "r", encoding="utf-8").read())
        meta["used_urls"] = list(used_urls)
        with open(str(meta_file), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def buscar_para_cena(scene_data: dict, query: str, media_type: str,
                     used_urls: set) -> Optional[dict]:
    """
    Busca uma mídia para a cena usando busca paralela.
    1. Tenta video se preferencia for video
    2. Fallback para foto
    3. Usa so a primeira keyword se query com 3+ palavras nao funcionar
    """
    from services.event_logger import log_event
    scene_id = scene_data["id"]
    keywords = scene_data.get("keywords", [])
    log_event("MEDIA_FETCH",
              "Cena %d: iniciando busca | keywords=\"%s\" | preferencia=%s | usando query=\"%s\"" %
              (scene_id, keywords, media_type, query), level="info")

    tentativas = 0
    # Tenta com query completa primeiro, depois com keyword unica
    queries_tentar = [query]
    primeira_key = query.split()[0] if len(query.split()) > 1 else query
    if primeira_key != query:
        queries_tentar.append(primeira_key)

    for tq in queries_tentar:
        for tt in [media_type, "photo"]:
            tentativas += 1
            log_event("MEDIA_FETCH",
                      "Cena %d: tentativa %d — query=\"%s\" tipo=%s" %
                      (scene_id, tentativas, tq, tt), level="info")
            candidato = buscar_midias_paralelo(tq, tt, used_urls)
            if not candidato:
                log_event("MEDIA_FETCH",
                          "Cena %d: tentativa %d — nenhum candidato retornado das APIs" %
                          (scene_id, tentativas), level="info")
                continue

            log_event("MEDIA_FETCH",
                      "Cena %d: tentativa %d — candidato=%s score=%.2f resolucao=%dx%d" %
                      (scene_id, tentativas,
                       candidato.get("source", "?"),
                       candidato.get("score", 0),
                       candidato.get("width", 0),
                       candidato.get("height", 0)), level="info")

            resultado = baixar_e_classificar(candidato, scene_id)
            if resultado and resultado.get("success") and resultado.get("quality") == "green":
                log_event("MEDIA_FETCH",
                          "Cena %d: GREEN aceita! fonte=%s arquivo=%s" %
                          (scene_id, candidato.get("source", "?"),
                           resultado.get("arquivo", "")[-40:]), level="info")
                return resultado

            if resultado:
                log_event("MEDIA_FETCH",
                          "Cena %d: candidato rejeitado — quality=%s motivo=\"%s\"" %
                          (scene_id, resultado.get("quality", "?"),
                           resultado.get("reason", "desconhecido")), level="info")
            else:
                log_event("MEDIA_FETCH",
                          "Cena %d: candidato rejeitado — erro ao baixar/classificar" %
                          (scene_id), level="info")

    log_event("MEDIA_FETCH",
              "Cena %d: nenhum candidato aceito apos %d tentativas" %
              (scene_id, tentativas), level="info")
    return None


def buscar_midias_projeto(project_name: str) -> dict:
    """
    Executa busca paralela para todas as cenas do projeto.
    """
    from services.event_logger import log_event
    log_event("MEDIA_FETCH", f"Iniciando busca paralela para {project_name}", level="info")

    project_dir = PROJETOS_DIR / project_name
    storyboard_file = project_dir / "storyboard.json"
    resultado_file = project_dir / "midias_encontradas.json"

    if not storyboard_file.exists():
        return {"success": False, "error": "storyboard.json não encontrado"}

    with open(storyboard_file, "r", encoding="utf-8") as f:
        storyboard = json.load(f)

    # Carrega URLs já usadas (anti-reuso)
    used_urls = _carregar_used_urls(project_name)

    resultados = []
    needs_media_count = 0

    for scene in storyboard:
        scene_id = scene["id"]
        query = " ".join(scene.get("keywords", [f"scene_{scene_id}"]))
        media_type = scene.get("media_preference", "video")

        # Busca em paralelo (usa used_urls para evitar repetição)
        resultado = buscar_para_cena(scene, query, media_type, used_urls)
        if resultado:
            resultado["scene_id"] = scene_id
            resultado["passada"] = "paralela"
        else:
            needs_media_count += 1
            resultado = {
                "success": False,
                "needs_media": True,
                "passada": "falhou",
                "scene_id": scene_id
            }

        resultados.append(resultado)

    # Salva URLs usadas no metadata
    _salvar_used_urls(project_name, used_urls)

    with open(resultado_file, "w", encoding="utf-8") as f:
        json.dump(resultados, f, indent=2, ensure_ascii=False)

    green_count = sum(1 for r in resultados if r.get("quality") == "green")

    log_event("MEDIA_FETCH", f"Busca concluida: {len(resultados)} cenas, {green_count} green, {needs_media_count} pendentes",
              level="info")

    return {
        "success": True,
        "project": project_name,
        "total_scenes": len(resultados),
        "green": green_count,
        "yellow": 0,
        "reused": 0,
        "needs_media": needs_media_count,
        "resultados": resultados
    }
