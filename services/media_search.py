"""
media_search.py — Busca paralela simplificada com anti-reuso.
Usa buscar_midias_paralelo (3 threads) e used_urls set.
Aceita um callback opcional para progresso detalhado em tempo real.
Busca é IDEMPOTENTE: cenas já resolvidas com mídia válida são puladas.
"""
import json
from typing import Optional, Callable
from pathlib import Path
from config import PROJETOS_DIR, ASSETS_CACHE_DIR
from services.media_fetcher import buscar_midias_paralelo, baixar_e_classificar


_callback_progresso: Optional[Callable] = None
_pipeline_ref = None  # referência para PipelineService (pause/retomada)


def set_callback(fn: Callable):
    """Define callback para progresso em tempo real (chamado pela pipeline_service)."""
    global _callback_progresso
    _callback_progresso = fn


def set_pipeline_ref(pipeline):
    """Define referência para PipelineService (para check de pause)."""
    global _pipeline_ref
    _pipeline_ref = pipeline


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


def _validar_midia_existente(project_name: str, scene_id: int,
                              resultado_anterior: dict = None) -> Optional[dict]:
    """
    Verifica se a cena ja tem midia valida em disco.
    Retorna o dict do resultado se valido, None se precisar buscar de novo.
    """
    # Se nao tem resultado anterior, precisa buscar
    if not resultado_anterior:
        return None
    if not resultado_anterior.get("success"):
        return None
    if resultado_anterior.get("needs_media"):
        return None

    qualidade = resultado_anterior.get("quality", "")
    if qualidade not in ("green", "yellow"):
        return None

    # Verifica arquivo principal
    arquivo = resultado_anterior.get("arquivo", "")
    if not arquivo or not Path(arquivo).exists():
        return None
    if Path(arquivo).stat().st_size == 0:
        return None

    # Tudo valido: pode pular
    return resultado_anterior


def _gerar_queries_frescas(project_name: str, scene: dict) -> list:
    """
    Gera queries FRESCAS para uma cena que precisa buscar de novo,
    usando broll_director para reanalisar o texto.
    """
    scene_id = scene["id"]
    texto = scene.get("texto", "")
    keywords = scene.get("keywords", [])

    from services.event_logger import log_event
    log_event("MEDIA_FETCH", f"Cena {scene_id}: gerando queries frescas (texto=\"{texto[:60]}...\")", level="info")

    # Gera queries localmente (mesma logica do broll_director._gerar_local)
    from services.broll_director import _extract_keywords_local
    from services.broll_director import SCENE_TYPE_KEYWORDS

    keywords_local = _extract_keywords_local(texto)
    scene_type = "explicacao"
    texto_lower = texto.lower()
    for stype, skeywords in SCENE_TYPE_KEYWORDS.items():
        if any(kw in texto_lower for kw in skeywords):
            scene_type = stype
            break

    # Gera search_queries do mesmo jeito que _gerar_queries_locais
    # (logica replicada do broll_director)
    search_queries = []
    if keywords_local:
        for kw in keywords_local[:3]:
            search_queries.append(f"{kw} {scene_type}")
        if len(keywords_local) >= 2:
            search_queries.append(f"{keywords_local[0]} {keywords_local[1]}")

    if not search_queries and keywords:
        search_queries = [" ".join(keywords[:3])]

    if not search_queries:
        search_queries = [f"scene_{scene_id}"]

    log_event("MEDIA_FETCH", f"Cena {scene_id}: {len(search_queries)} queries frescas geradas: {search_queries}", level="info")
    return search_queries


def buscar_para_cena(scene_data: dict, query, media_type: str,
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
    tipos_tentar = ["video", "photo"] if media_type == "video" else ["photo"]
    melhor_candidato = None
    melhor_score = 0.0

    for tq in pool:
        for tt in tipos_tentar:
            tentativas += 1
            tipo_label = "VIDEO" if tt == "video" else "PHOTO"
            _log("Cena %d: tentativa %d — %s \"%s\"" % (scene_id, tentativas, tipo_label, tq))
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
    pipeline = _pipeline_ref
    log_event("MEDIA_FETCH", "Iniciando busca paralela para %s" % project_name, level="info")

    project_dir = PROJETOS_DIR / project_name
    storyboard_file = project_dir / "storyboard.json"
    resultado_file = project_dir / "midias_encontradas.json"

    if not storyboard_file.exists():
        return {"success": False, "error": "storyboard.json nao encontrado"}

    with open(storyboard_file, "r", encoding="utf-8") as f:
        storyboard = json.load(f)

    # Carrega resultados anteriores para idempotencia
    resultados_anteriores = {}
    if resultado_file.exists():
        try:
            dados_antigos = json.loads(open(str(resultado_file), "r", encoding="utf-8").read())
            for r in dados_antigos:
                sid = r.get("scene_id")
                if sid is not None:
                    resultados_anteriores[sid] = r
            log_event("MEDIA_FETCH", "Carregados %d resultados anteriores de midias_encontradas.json" %
                      len(resultados_anteriores), level="info")
        except Exception as e:
            log_event("MEDIA_FETCH", "Erro ao carregar resultados anteriores: %s" % str(e), level="warn")

    used_urls = _carregar_used_urls(project_name)

    resultados = []
    needs_media_count = 0
    total_cenas = len(storyboard)
    puladas = 0

    # Carrega resume_index do pipeline se existir
    resume_idx = 0
    if pipeline:
        try:
            resume_idx = pipeline.get_resume_index(3)
            if resume_idx > 0:
                log_event("MEDIA_FETCH", f"Retomando busca a partir da cena {resume_idx + 1}/{total_cenas}", level="info")
        except Exception:
            pass

    for idx, scene in enumerate(storyboard):
        scene_id = scene["id"]
        # Pula cenas já processadas se retomando de pause
        if resume_idx > 0 and idx < resume_idx:
            resultado_anterior = resultados_anteriores.get(scene_id)
            if resultado_anterior:
                resultados.append(resultado_anterior)
            else:
                resultados.append({"success": False, "needs_media": True, "scene_id": scene_id})
            continue

        # Checa pause antes de cada cena
        if pipeline:
            try:
                if pipeline._check_pause_before_item(3, idx, total_cenas):
                    log_event("MEDIA_FETCH", f"Pipeline pausado/cancelado na cena {idx + 1}/{total_cenas}", level="info")
                    # Salva resultados parciais e sai
                    break
            except Exception:
                pass

        pct_atual = int((idx / total_cenas) * 100)
        texto_cena = scene.get("texto", "")[:60]
        start_time = scene.get("start_time")
        end_time = scene.get("end_time")
        ts_str = ""
        if start_time is not None and end_time is not None:
            ts_str = f" | ts={_format_timestamp(start_time)}-{_format_timestamp(end_time)}"

        # --- IDEMPOTENCIA: verifica se ja tem midia valida ---
        resultado_anterior = resultados_anteriores.get(scene_id)
        midia_valida = _validar_midia_existente(project_name, scene_id, resultado_anterior)

        if midia_valida:
            _log("Cena %d/%d (%d%%) — JA RESOLVIDA (pulando busca) | texto=\"%s\"%s | qualidade=%s" %
                 (idx + 1, total_cenas, pct_atual, texto_cena, ts_str, midia_valida.get("quality", "?")))
            resultados.append(midia_valida)
            puladas += 1
            continue

        _log("Cena %d/%d (%d%%) — buscando midia | texto=\"%s\"%s" %
             (idx + 1, total_cenas, pct_atual, texto_cena, ts_str))

        # Verifica se storyboard tem search_queries (Claude batch) ou fallback para keywords
        search_queries = scene.get("search_queries", [])
        if not search_queries:
            # Fallback: usa keywords como antes
            query = " ".join(scene.get("keywords", [f"scene_{scene_id}"]))
            search_queries = [query]

        media_type = scene.get("media_preference", "video")

        # Para cenas que PRECISAM buscar de novo: gera queries FRESCAS
        # (nao recicla queries que ja falharam antes)
        if resultado_anterior and not resultado_anterior.get("success"):
            _log("Cena %d/%d: cena pendente — gerando queries frescas" % (idx + 1, total_cenas))
            queries_frescas = _gerar_queries_frescas(project_name, scene)
            if queries_frescas:
                search_queries = queries_frescas

        # Cria QueryPool com as queries do planejamento (Claude ou local)
        pool = QueryPool(
            scene_id=scene_id,
            queries=search_queries,
            media_type=media_type,
            fallback_queries=scene.get("fallback_queries", [])
        )

        _log("Cena %d/%d: pool com %d queries (%s)" %
             (idx + 1, total_cenas, pool.total_queries(), search_queries[:2]))

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
    _log("Busca concluida: %d cenas, %d puladas (ja resolvidas), %d green, %d pendentes" %
         (len(resultados), puladas, green_count, needs_media_count))

    return {
        "success": True,
        "project": project_name,
        "total_scenes": len(resultados),
        "green": green_count,
        "yellow": 0,
        "reused": 0,
        "needs_media": needs_media_count,
        "puladas": puladas,
        "resultados": resultados
    }


# ---------------------------------------------------------------------------
# MODO LOCAL TIMESTAMP — mídia pré-gerada externamente com nome padronizado
# (Tarefa 4 — ULTRACUT3 WEB v1.0)
#
# Padrão de nome aceito:  ^\d+_\[(\d{2})-(\d{2})\]_.*\.(jpg|jpeg|png|mp4|mov)$
# Exemplo:                0007_[02-30]_dandelion_field.jpg  -> timestamp = 2*60+30 = 150s
# ---------------------------------------------------------------------------

import os as _os
import re as _re

TIMESTAMP_FILE_REGEX = _re.compile(
    r"^\d+_\[(\d{2})-(\d{2})\]_.*\.(jpg|jpeg|png|mp4|mov)$",
    _re.IGNORECASE,
)

TIMESTAMP_MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".mp4", ".mov"}


def detectar_modo_local_timestamp(caminho_pasta: str) -> tuple:
    """
    Detecta se uma pasta contém mídia pré-gerada com nome padronizado por timestamp.

    Retorna:
        (True, lista_ordenada) se >= 70% dos arquivos de imagem/vídeo casarem com o
        padrão ^\d+_\[(\d{2})-(\d{2})\]_.*\.(jpg|jpeg|png|mp4|mov)$.
        lista_ordenada = [(caminho_arquivo, timestamp_em_segundos), ...] ordenada
        por timestamp.
        (False, []) caso contrário.
    """
    try:
        pasta = Path(caminho_pasta)
        if not pasta.exists() or not pasta.is_dir():
            _log(f"detectar_modo_local_timestamp: pasta não encontrada: {caminho_pasta}", level="warn")
            return False, []
    except Exception as e:
        _log(f"detectar_modo_local_timestamp: erro ao acessar pasta: {e}", level="warn")
        return False, []

    arquivos_media = []
    for item in sorted(pasta.iterdir()):
        if not item.is_file():
            continue
        if item.suffix.lower() not in TIMESTAMP_MEDIA_EXTENSIONS:
            continue
        arquivos_media.append(item)

    if not arquivos_media:
        _log("detectar_modo_local_timestamp: nenhum arquivo de mídia na pasta", level="info")
        return False, []

    casados = []
    for arq in arquivos_media:
        m = TIMESTAMP_FILE_REGEX.match(arq.name)
        if m:
            minutos = int(m.group(1))
            segundos = int(m.group(2))
            ts = minutos * 60 + segundos
            casados.append((str(arq), ts))

    pct = (len(casados) / len(arquivos_media)) * 100
    _log(
        "detectar_modo_local_timestamp: %d/%d arquivos casam com padrão (%.0f%%) — %s"
        % (len(casados), len(arquivos_media), pct,
           "MODO LOCAL TIMESTAMP" if pct >= 70 else "usando busca via API"),
        level="info",
    )

    if pct < 70:
        return False, []

    # Ordena por timestamp (e por nome como desempate)
    casados.sort(key=lambda x: (x[1], x[0]))
    return True, casados


def _cena_start_seconds(cena: dict) -> float:
    """Extrai o tempo de início (segundos) de uma cena, com fallback para timestamps MM:SS."""
    start = cena.get("start_time")
    if isinstance(start, (int, float)) and start > 0:
        return float(start)
    ts_list = cena.get("timestamps") or []
    if ts_list:
        try:
            ts = str(ts_list[0]).strip()
            partes = ts.split(":")
            return int(partes[0]) * 60 + int(partes[1])
        except Exception:
            pass
    return 0.0


def casar_midia_por_timestamp(cenas: list, arquivos_com_timestamp: list,
                              tolerancia_segundos: int = 3) -> dict:
    """
    Casa cada cena com o arquivo de timestamp mais próximo dentro da tolerância.

    - Cada arquivo só pode ser usado UMA vez (removido dos candidatos após o match).
    - Se nenhum arquivo estiver dentro da tolerância, registra evento de aviso
      na fila existente e a cena fica sem mídia (não interrompe o pipeline).

    Retorna: mapeamento {scene_id: caminho_arquivo}.
    """
    candidatos = [list(item) for item in arquivos_com_timestamp]  # (path, ts) mutáveis
    mapeamento = {}

    for cena in cenas:
        scene_id = cena.get("id", cena.get("scene_id", 0))
        inicio = _cena_start_seconds(cena)

        melhor = None
        melhor_dist = None
        melhor_idx = None
        for idx, (path, ts) in enumerate(candidatos):
            dist = abs(ts - inicio)
            if dist <= tolerancia_segundos:
                if melhor_dist is None or dist < melhor_dist:
                    melhor_dist = dist
                    melhor = path
                    melhor_idx = idx

        if melhor is not None:
            mapeamento[scene_id] = melhor
            candidatos.pop(melhor_idx)
        else:
            mm, ss = int(inicio // 60), int(inicio % 60)
            _log(
                "aviso: cena em %02d:%02d sem imagem correspondente encontrada"
                % (mm, ss),
                level="warn",
            )

    return mapeamento