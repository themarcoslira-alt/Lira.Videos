"""
media_search.py — Busca paralela simplificada com anti-reuso.
Usa buscar_midias_paralelo (3 threads) e used_urls set.
Aceita um callback opcional para progresso detalhado em tempo real.
"""
import json
from typing import Optional, Callable
from config import PROJETOS_DIR
from services.media_fetcher import buscar_midias_paralelo, baixar_e_classificar


_callback_progresso: Optional[Callable] = None


def set_callback(fn: Callable):
    """Define callback para progresso em tempo real (chamado pela pipeline_service)."""
    global _callback_progresso
    _callback_progresso = fn


def _log(msg: str, level: str = "info"):
    """Loga evento e chama callback se existir."""
    from services.event_logger import log_event
    log_event("MEDIA_FETCH", msg, level=level)
    if _callback_progresso:
        try:
            _callback_progresso(3, "andamento", msg)
        except Exception:
            pass


def _carregar_used_urls(project_name: str) -> set:
    meta_file = PROJETOS_DIR / project_name / "meta.json"
    if meta_file.exists():
        try:
            meta = json.loads(open(str(meta_file), "r", encoding="utf-8").read())
            return set(meta.get("used_urls", []))
        except Exception:
            pass
    return set()


def _salvar_used_urls(project_name: str, used_urls: set):
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
    scene_id = scene_data["id"]
    keywords = scene_data.get("keywords", [])
    _log("Cena %d: iniciando | keywords=\"%s\" | preferencia=%s" %
         (scene_id, keywords, media_type))

    queries_tentar = [query]
    primeira_key = query.split()[0] if len(query.split()) > 1 else query
    if primeira_key != query:
        queries_tentar.append(primeira_key)

    tentativas = 0
    for tq in queries_tentar:
        for tt in [media_type, "photo"]:
            tentativas += 1
            _log("Cena %d: tentativa %d/%d — query=\"%s\" tipo=%s disparando 3 APIs em paralelo..." %
                 (scene_id, tentativas, len(queries_tentar) * 2, tq, tt))
            candidato = buscar_midias_paralelo(tq, tt, used_urls)
            if not candidato:
                _log("Cena %d: tentativa %d — nenhum resultado das APIs" % (scene_id, tentativas))
                continue

            _log("Cena %d: tentativa %d — retornou %s (score=%.2f, %dx%d) — baixando..." %
                 (scene_id, tentativas,
                  candidato.get("source", "?"),
                  candidato.get("score", 0),
                  candidato.get("width", 0),
                  candidato.get("height", 0)))

            resultado = baixar_e_classificar(candidato, scene_id)
            if resultado and resultado.get("success") and resultado.get("quality") == "green":
                _log("Cena %d: GREEN aceita! fonte=%s" % (scene_id, candidato.get("source", "?")))
                return resultado

            if resultado:
                _log("Cena %d: rejeitada — quality=%s motivo=\"%s\"" %
                     (scene_id, resultado.get("quality", "?"),
                      resultado.get("reason", "desconhecido")))
            else:
                _log("Cena %d: rejeitada — erro ao baixar" % scene_id)

    _log("Cena %d: todas as %d tentativas falharam" % (scene_id, tentativas))
    return None


def buscar_midias_projeto(project_name: str) -> dict:
    from services.event_logger import log_event
    log_event("MEDIA_FETCH", "Iniciando busca paralela para %s" % project_name, level="info")

    project_dir = PROJETOS_DIR / project_name
    storyboard_file = project_dir / "storyboard.json"
    resultado_file = project_dir / "midias_encontradas.json"

    if not storyboard_file.exists():
        return {"success": False, "error": "storyboard.json nao encontrado"}

    with open(storyboard_file, "r", encoding="utf-8") as f:
        storyboard = json.load(f)

    used_urls = _carregar_used_urls(project_name)

    resultados = []
    needs_media_count = 0

    for scene in storyboard:
        scene_id = scene["id"]
        query = " ".join(scene.get("keywords", [f"scene_{scene_id}"]))
        media_type = scene.get("media_preference", "video")

        _log("--- Buscando midia para Cena %d/%d ---" % (scene_id, len(storyboard)))
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

    _salvar_used_urls(project_name, used_urls)

    with open(resultado_file, "w", encoding="utf-8") as f:
        json.dump(resultados, f, indent=2, ensure_ascii=False)

    green_count = sum(1 for r in resultados if r.get("quality") == "green")
    _log("Busca concluida: %d cenas, %d green, %d pendentes" %
         (len(resultados), green_count, needs_media_count))

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