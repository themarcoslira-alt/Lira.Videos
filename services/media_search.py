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


def _format_timestamp(secs: float) -> str:
    """Formata segundos para MM:SS.mmm."""
    mins = int(secs // 60)
    segs = secs % 60
    return f"{mins:02d}:{segs:06.3f}"


def buscar_para_cena(scene_data: dict, query: str, media_type: str,
                     used_urls: set) -> Optional[dict]:
    """
    Busca midia para uma cena.
    Aceita query string (compatibilidade) ou QueryPool (novo).
    Se for string, cria um QueryPool com fallbacks a partir das keywords.
    """
    from services.query_pool import QueryPool
    scene_id = scene_data["id"]
    keywords = scene_data.get("keywords", [])
    texto_cena = scene_data.get("texto", "")[:80]
    start_time = scene_data.get("start_time")
    end_time = scene_data.get("end_time")
    ts_str = ""
    if start_time is not None and end_time is not None:
        ts_str = f"timestamp={_format_timestamp(start_time)}-{_format_timestamp(end_time)}"
    _log("Cena %d: iniciando busca | %s | texto=\"%s\" | preferencia=%s" %
         (scene_id, ts_str, texto_cena, media_type))

    # Cria pool: query principal + variações como fallback
    if isinstance(query, QueryPool):
        pool = query
    else:
        queries = [query]
        primeira_key = query.split()[0] if len(query.split()) > 1 else query
        if primeira_key != query:
            queries.append(primeira_key)
        pool = QueryPool(scene_id=scene_id, queries=queries, media_type=media_type)

    _log("Cena %d: pool com %d queries para testar em multiplas APIs" %
         (scene_id, pool.total_queries()))

    tentativas = 0
    tipos_tentar = ["photo"] if media_type == "video" else [media_type, "photo"]
    melhor_candidato = None
    melhor_score = 0.0

    for tq in pool:
        for tt in tipos_tentar:
            tentativas += 1
            _log("Cena %d: tentativa %d — query enviada a todas as APIs: \"%s\" (tipo=%s)" %
                 (scene_id, tentativas, tq, tt))
            pool_uma = QueryPool(scene_id=scene_id, queries=[tq], media_type=tt)
            candidato = buscar_midias_paralelo(pool_uma, tt, used_urls)
            if not candidato:
                _log("Cena %d: tentativa %d — nenhum resultado das APIs para query \"%s\"" %
                     (scene_id, tentativas, tq))
                continue

            score = candidato.get("score", 0)
            w = candidato.get("width", 0)
            h = candidato.get("height", 0)
            fonte = candidato.get("source", "?")
            _log("Cena %d: tentativa %d — %s retornou (score=%.2f, %dx%d) — baixando..." %
                 (scene_id, tentativas, fonte, score, w, h))

            resultado = baixar_e_classificar(candidato, scene_id)
            if resultado and resultado.get("success"):
                quality = resultado.get("quality", "?")
                w_real = resultado.get("width", w)
                h_real = resultado.get("height", h)
                if quality == "green":
                    _log("Cena %d: GREEN aceita! fonte=%s | id=%s_%s | score=%.2f | resolucao=%dx%d" %
                         (scene_id, fonte, fonte, candidato.get("id", "?"), score, w_real, h_real))
                    return resultado
                else:
                    motivo = resultado.get("reason", "desconhecido")
                    _log("Cena %d: candidato %s_%s rejeitado — motivo=%s (resolucao=%dx%d, quality=%s)" %
                         (scene_id, fonte, candidato.get("id", "?"), motivo, w_real, h_real, quality))
                    # Guarda como melhor candidato se tiver score maior
                    if score > melhor_score:
                        melhor_candidato = (fonte, candidato.get("id", "?"), score, w_real, h_real)
                        melhor_score = score
            else:
                _log("Cena %d: candidato %s_%s rejeitado — motivo=erro_download (falha ao baixar %s)" %
                     (scene_id, candidato.get("source", "?"), candidato.get("id", "?"),
                      candidato.get("url", "?")[:60]))

    if melhor_candidato:
        fonte, cid, score, w, h = melhor_candidato
        _log("Cena %d: PENDENTE — nenhum candidato atingiu GREEN | melhor: %s_%s (score=%.2f, resolucao=%dx%d)" %
             (scene_id, fonte, cid, score, w, h))
    else:
        _log("Cena %d: PENDENTE — nenhum resultado retornado por nenhuma fonte para as queries testadas" % scene_id)

    _log("Cena %d: todas as %d tentativas falharam" % (scene_id, tentativas))
    return None


def buscar_midias_projeto(project_name: str) -> dict:
    from services.event_logger import log_event
    from services.query_pool import QueryPool
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
    total_cenas = len(storyboard)

    for idx, scene in enumerate(storyboard):
        scene_id = scene["id"]
        pct_atual = int((idx / total_cenas) * 100)
        texto_cena = scene.get("texto", "")[:60]
        start_time = scene.get("start_time")
        end_time = scene.get("end_time")
        ts_str = ""
        if start_time is not None and end_time is not None:
            ts_str = f" | ts={_format_timestamp(start_time)}-{_format_timestamp(end_time)}"
        _log("Cena %d/%d (%d%%) — texto=\"%s\"%s" %
             (idx + 1, total_cenas, pct_atual, texto_cena, ts_str))

        # Verifica se storyboard tem search_queries (Claude batch) ou fallback para keywords
        search_queries = scene.get("search_queries", [])
        if not search_queries:
            # Fallback: usa keywords como antes
            query = " ".join(scene.get("keywords", [f"scene_{scene_id}"]))
            search_queries = [query]

        media_type = scene.get("media_preference", "video")

        # Cria QueryPool com as queries do planejamento (Claude ou local)
        pool = QueryPool(
            scene_id=scene_id,
            queries=search_queries,
            media_type=media_type,
            fallback_queries=scene.get("fallback_queries", [])
        )

        _log("Cena %d/%d: pool com %d queries (%s)" %
             (idx + 1, total_cenas, pool.total_queries(), search_queries))

        resultado = buscar_para_cena(scene, pool, media_type, used_urls)
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
        pct_depois = int(((idx + 1) / total_cenas) * 100)
        if resultado and resultado.get("quality") == "green":
            _log("Progresso: Cena %d/%d (%d%%) — GREEN obtida!" % (idx + 1, total_cenas, pct_depois))
        elif resultado and resultado.get("needs_media"):
            _log("Progresso: Cena %d/%d (%d%%) — sem resultado (needs_media)" % (idx + 1, total_cenas, pct_depois))
        elif resultado:
            _log("Progresso: Cena %d/%d (%d%%) — resultado parcial" % (idx + 1, total_cenas, pct_depois))
        else:
            _log("Progresso: Cena %d/%d (%d%%) — falhou" % (idx + 1, total_cenas, pct_depois))

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