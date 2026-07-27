"""
media_fetcher.py — Busca de mídia em Pexels, Pixabay e Unsplash
Versão paralela: dispara 3 fontes ao mesmo tempo, retorna a melhor.
"""
import os, json, requests, time, concurrent.futures
from pathlib import Path
from typing import Optional
from config import PEXELS_API_KEY, PIXABAY_API_KEY, UNSPLASH_API_KEY, ASSETS_CACHE_DIR


def classify_media_quality(width: int, height: int) -> str:
    """GREEN >= 1280x720 (simplificado). RETIRED e RED não existem mais."""
    if width >= 1280 and height >= 720:
        return "green"
    return "red"


def _baixar_arquivo(url: str, destino: Path, source: str = "") -> bool:
    try:
        headers = {"User-Agent": "ultracut3/1.0"}
        # Pexels precisa de Authorization header ate para download do CDN
        if source == "pexels" and PEXELS_API_KEY:
            headers["Authorization"] = PEXELS_API_KEY
        # Timeout separado: (conexao, leitura) — 15s pra conectar, 120s pra baixar
        r = requests.get(url, timeout=(15, 120), stream=True, headers=headers, allow_redirects=True)
        if r.status_code != 200:
            from services.event_logger import log_event
            log_event("MEDIA_FETCH", f"Download HTTP {r.status_code}: {url[:60]}... (source={source})", level="error")
            return False
        with open(destino, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        # Validacao pos-download: arquivo existe e tem tamanho > 0
        if not destino.exists() or destino.stat().st_size == 0:
            _tentar_deletar(destino)
            return False
        # Validacao de tipo: tenta detectar se e imagem valida (para fotos)
        ext = destino.suffix.lower()
        try:
            if ext in ('.jpg', '.jpeg', '.png', '.webp', '.bmp'):
                from PIL import Image
                img = Image.open(destino)
                img.load()  # carrega pixels para forcar validacao completa
                img.close()
        except Exception as e:
            from services.event_logger import log_event
            log_event("MEDIA_FETCH", f"Arquivo baixado invalido: {destino.name} ({destino.stat().st_size} bytes) -> {str(e)[:60]}", level="error")
            _tentar_deletar(destino)
            return False
        return True
    except requests.exceptions.HTTPError as e:
        from services.event_logger import log_event
        log_event("MEDIA_FETCH", f"Download HTTP {e.response.status_code} {e.response.reason}: {url[:50]}... (auth={bool(headers.get('Authorization'))})", level="error")
        if destino.exists():
            _tentar_deletar(destino)
        return False
    except requests.exceptions.Timeout as e:
        from services.event_logger import log_event
        log_event("MEDIA_FETCH", f"Download TIMEOUT: {url[:50]}... ({type(e).__name__})", level="error")
        if destino.exists():
            _tentar_deletar(destino)
        return False
    except requests.exceptions.RequestException as e:
        from services.event_logger import log_event
        log_event("MEDIA_FETCH", f"Download REQUEST ERROR: {type(e).__name__}: {str(e)[:80]} (url: {url[:50]}...)", level="error")
        if destino.exists():
            _tentar_deletar(destino)
        return False
    except Exception as e:
        from services.event_logger import log_event
        log_event("MEDIA_FETCH", f"Download UNEXPECTED ERROR: {type(e).__name__}: {str(e)[:80]}", level="error")
        if destino.exists():
            _tentar_deletar(destino)
        return False


def _obter_resolucao_arquivo(arquivo: Path) -> tuple:
    try:
        if arquivo.suffix.lower() in ('.mp4', '.webm', '.mov'):
            import subprocess
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "json", str(arquivo)],
                capture_output=True, text=True, timeout=10
            )
            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if streams:
                return (streams[0]["width"], streams[0]["height"])
        else:
            from PIL import Image
            with Image.open(arquivo) as img:
                size = img.size
            return size
    except Exception as e:
        from services.event_logger import log_event
        log_event("MEDIA_FETCH", f"Erro ao ler resolucao do arquivo {arquivo.name}: {str(e)[:60]}",
                  level="error", details={"path": str(arquivo), "size": arquivo.stat().st_size if arquivo.exists() else 0})
        return (0, 0)
    return (0, 0)


def _tentar_deletar(arquivo: Path, tentativas: int = 2):
    """Proteção contra PermissionError no unlink."""
    import time
    for tentativa in range(tentativas):
        try:
            if arquivo.exists():
                arquivo.unlink()
            return True
        except PermissionError:
            if tentativa < tentativas - 1:
                time.sleep(0.1)
            else:
                try:
                    from services.event_logger import log_event
                    log_event("MEDIA_FETCH", f"Warning: nao foi possivel apagar arquivo orfao: {arquivo.name}",
                              level="warn", details={"path": str(arquivo)})
                except Exception:
                    pass
                return False
        except Exception:
            try:
                if arquivo.exists():
                    arquivo.unlink()
            except Exception:
                pass
            return False
    return False


def _fetch_pexels(query: str, media_type: str, timeout: float = 8.0) -> list:
    """Busca Pexels com timeout. Retorna lista de candidatos."""
    if not PEXELS_API_KEY:
        return []
    results = []
    url = "https://api.pexels.com/videos/search" if media_type == "video" else "https://api.pexels.com/v1/search"
    params = {"query": query, "per_page": 3}
    headers = {"Authorization": PEXELS_API_KEY}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if media_type == "video":
            for video in data.get("videos", []):
                vfs = video.get("video_files", [])
                best = None
                for vf in vfs:
                    if vf.get("width", 0) >= 1920 and vf.get("height", 0) >= 1080:
                        best = vf; break
                if not best:
                    best = vfs[0] if vfs else None
                if best:
                    results.append({"source":"pexels","media_type":"video","url":best.get("link",""),
                                    "width":best.get("width",0),"height":best.get("height",0),
                                    "id":video.get("id",""),"score":0.95})
        else:
            for photo in data.get("photos", []):
                src = photo.get("src", {})
                # Prioridade: large2x > large > original (original e muito pesado)
                url = src.get("large2x") or src.get("large") or src.get("original", "")
                results.append({"source":"pexels","media_type":"photo","url":url,
                                "width":photo.get("width",0),"height":photo.get("height",0),
                                "id":photo.get("id",""),"score":0.95})
    except Exception:
        pass
    return results


def _fetch_pixabay(query: str, media_type: str, timeout: float = 8.0) -> list:
    """Busca Pixabay com timeout. Retorna lista de candidatos."""
    if not PIXABAY_API_KEY:
        return []
    results = []
    url = "https://pixabay.com/api/videos/" if media_type == "video" else "https://pixabay.com/api/"
    params = {"key": PIXABAY_API_KEY, "q": query, "per_page": 3, "safesearch": "true"}
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if media_type == "video":
            for hit in data.get("hits", []):
                videos = hit.get("videos", {})
                large = videos.get("large", {})
                if large and large.get("url"):
                    w, h = large.get("width", 0), large.get("height", 0)
                    results.append({"source":"pixabay","media_type":"video","url":large["url"],
                                    "width":w,"height":h,"id":hit.get("id",""),"score":0.85})
                else:
                    medium = videos.get("medium", {})
                    if medium and medium.get("url"):
                        w, h = medium.get("width", 0), medium.get("height", 0)
                        results.append({"source":"pixabay","media_type":"video","url":medium["url"],
                                        "width":w,"height":h,"id":hit.get("id",""),"score":0.80})
        else:
            for hit in data.get("hits", []):
                results.append({"source":"pixabay","media_type":"photo","url":hit.get("largeImageURL",""),
                                "width":hit.get("imageWidth",0),"height":hit.get("imageHeight",0),
                                "id":hit.get("id",""),"score":0.85})
    except Exception:
        pass
    return results


def _fetch_unsplash(query: str, timeout: float = 8.0) -> list:
    """Busca Unsplash com timeout. Retorna lista de candidatos."""
    if not UNSPLASH_API_KEY:
        return []
    results = []
    url = "https://api.unsplash.com/search/photos"
    params = {"query": query, "per_page": 3, "orientation": "landscape"}
    headers = {"Authorization": f"Client-ID {UNSPLASH_API_KEY}"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        for photo in data.get("results", []):
            urls = photo.get("urls", {})
            results.append({"source":"unsplash","media_type":"photo",
                            "url":urls.get("raw", urls.get("full", "")),
                            "width":photo.get("width",0),"height":photo.get("height",0),
                            "id":photo.get("id",""),"score":0.75})
    except Exception:
        pass
    return results


def _buscar_uma_query(query: str, media_type: str, used_urls: set) -> list:
    """
    Dispara uma única query em todas as APIs em paralelo.
    Retorna lista de candidatos com metadados de origem.
    """
    candidatos = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futuros = {}
        futuros[executor.submit(_fetch_pexels, query, media_type)] = "pexels"
        futuros[executor.submit(_fetch_pixabay, query, media_type)] = "pixabay"
        if media_type == "photo":
            futuros[executor.submit(_fetch_unsplash, query)] = "unsplash"
        for future in concurrent.futures.as_completed(futuros, timeout=30):
            try:
                items = future.result()
                if items:
                    for item in items:
                        item["query_used"] = query
                    candidatos.extend(items)
            except Exception:
                continue
    return candidatos


def buscar_midias_paralelo(query: str, media_type: str = "video",
                           used_urls: set = None) -> Optional[dict]:
    """
    Busca mídia em PARALELO nas 3 fontes.
    Aceita string ou QueryPool. Se for string, cria pool com uma query.
    """
    from services.query_pool import QueryPool
    if isinstance(query, QueryPool):
        pool = query
    else:
        pool = QueryPool.from_single(scene_id=0, query=query, media_type=media_type)

    used_urls = used_urls or set()
    todos_candidatos = []

    from services.event_logger import log_event
    log_event("MEDIA_FETCH", f"QueryPool cena {pool.scene_id}: {pool.total_queries()} queries em {len(pool)} APIs",
              level="info")

    for q in pool:
        log_event("MEDIA_FETCH", f"QueryPool cena {pool.scene_id}: testando query=\"{q}\" em 3 APIs",
                  level="info")
        candidatos = _buscar_uma_query(q, pool.media_type, used_urls)
        if candidatos:
            log_event("MEDIA_FETCH", f"QueryPool cena {pool.scene_id}: query=\"{q}\" -> {len(candidatos)} candidatos",
                      level="info")
            todos_candidatos.extend(candidatos)
        else:
            log_event("MEDIA_FETCH", f"QueryPool cena {pool.scene_id}: query=\"{q}\" -> 0 candidatos",
                      level="info")

    # Ordena por score
    todos_candidatos.sort(key=lambda x: -x.get("score", 0))

    # Retorna o primeiro candidato válido que não está em used_urls
    for cand in todos_candidatos:
        if cand.get("score", 0) >= 0.75:
            url = cand.get("url", "")
            if url and url not in used_urls:
                used_urls.add(url)
                return cand

    return None


def baixar_e_classificar(candidato: dict, scene_id: int) -> Optional[dict]:
    """
    Baixa o candidato, classifica qualidade real, retorna resultado.
    Se qualidade for "red", deleta o arquivo do disco.
    """
    from services.event_logger import log_event

    cache_dir = ASSETS_CACHE_DIR / f"scene_{scene_id}"
    cache_dir.mkdir(parents=True, exist_ok=True)

    url = candidato.get("url", "")
    if not url:
        return None

    ext = ".mp4" if candidato.get("media_type") == "video" else ".jpg"
    arquivo = cache_dir / f"{candidato['source']}_{candidato['id']}{ext}"

    if not _baixar_arquivo(url, arquivo, candidato.get("source", "")):
        return None

    width, height = _obter_resolucao_arquivo(arquivo)
    quality = classify_media_quality(width, height)

    if quality == "red":
        _tentar_deletar(arquivo)
        return {
            "success": False,
            "quality": quality,
            "reason": f"Resolucao {width}x{height} abaixo do minimo (1280x720)"
        }

    return {
        "success": True,
        "quality": quality,
        "arquivo": str(arquivo),
        "width": width,
        "height": height,
        "source": candidato.get("source", ""),
        "media_type": candidato.get("media_type", "video"),
        "photographer": candidato.get("photographer", ""),
        "id": candidato.get("id", "")
    }