"""
media_fetcher.py — Busca de mídia em Pexels, Pixabay e Unsplash
"""
import os
import json
import requests
from pathlib import Path
from typing import Optional
from config import PEXELS_API_KEY, PIXABAY_API_KEY, UNSPLASH_API_KEY, ASSETS_CACHE_DIR


def classify_media_quality(width: int, height: int) -> str:
    """
    Classifica qualidade baseada na resolução REAL do arquivo baixado.
    - "green": >= 1920x1080
    - "yellow": >= 1280x720
    - "red": < 1280x720 (sempre descartado)
    """
    if width >= 1920 and height >= 1080:
        return "green"
    elif width >= 1280 and height >= 720:
        return "yellow"
    else:
        return "red"


def _baixar_arquivo(url: str, destino: Path) -> bool:
    """Baixa arquivo de URL para destino. Retorna True se sucesso."""
    try:
        r = requests.get(url, timeout=30, stream=True)
        r.raise_for_status()
        with open(destino, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception:
        if destino.exists():
            destino.unlink()
        return False


def _obter_resolucao_arquivo(arquivo: Path) -> tuple:
    """Obtém resolução do arquivo de mídia baixado."""
    try:
        if arquivo.suffix.lower() in ('.mp4', '.webm', '.mov'):
            # Para vídeo, usa ffprobe
            import subprocess
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of",
                 "json", str(arquivo)],
                capture_output=True, text=True, timeout=10
            )
            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if streams:
                return (streams[0]["width"], streams[0]["height"])
        else:
            # Para imagem, usa PIL — garante fechamento com with
            from PIL import Image
            with Image.open(arquivo) as img:
                size = img.size
            return size
    except Exception:
        return (0, 0)
    return (0, 0)


def buscar_pexels(query: str, media_type: str = "video", per_page: int = 5) -> list:
    """
    Busca mídia no Pexels.
    Vídeo: usa src.large (resolução original, sempre green).
    Foto: usa src.large.
    """
    if not PEXELS_API_KEY:
        return []

    results = []

    if media_type == "video":
        url = "https://api.pexels.com/videos/search"
        params = {"query": query, "per_page": per_page}
    else:
        url = "https://api.pexels.com/v1/search"
        params = {"query": query, "per_page": per_page}

    headers = {"Authorization": PEXELS_API_KEY}

    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        if media_type == "video":
            for video in data.get("videos", []):
                video_files = video.get("video_files", [])
                # Pega arquivo com maior resolução
                best = None
                for vf in video_files:
                    if vf.get("width", 0) >= 1920 and vf.get("height", 0) >= 1080:
                        best = vf
                        break
                if not best:
                    # Usa o primeiro disponível
                    best = video_files[0] if video_files else None

                if best:
                    results.append({
                        "source": "pexels",
                        "media_type": "video",
                        "url": best.get("link", ""),
                        "width": best.get("width", 0),
                        "height": best.get("height", 0),
                        "duration": video.get("duration", 0),
                        "photographer": video.get("user", {}).get("name", ""),
                        "id": video.get("id", "")
                    })
        else:
            for photo in data.get("photos", []):
                src = photo.get("src", {})
                results.append({
                    "source": "pexels",
                    "media_type": "photo",
                    "url": src.get("large", ""),
                    "width": photo.get("width", 0),
                    "height": photo.get("height", 0),
                    "photographer": photo.get("photographer", ""),
                    "id": photo.get("id", "")
                })

    except Exception:
        pass

    return results


def buscar_pixabay(query: str, media_type: str = "video", per_page: int = 5) -> list:
    """
    Busca mídia no Pixabay.
    Vídeo: usa largeImageURL (~1280x853, yellow no plano gratuito).
    Foto: usa largeImageURL.
    NUNCA usa webformatURL (~640x427, thumbnail).
    """
    if not PIXABAY_API_KEY:
        return []

    results = []

    if media_type == "video":
        url = "https://pixabay.com/api/videos/"
    else:
        url = "https://pixabay.com/api/"

    params = {
        "key": PIXABAY_API_KEY,
        "q": query,
        "per_page": per_page,
        "safesearch": "true"
    }

    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        if media_type == "video":
            for hit in data.get("hits", []):
                videos = hit.get("videos", {})
                # Tenta pegar o large primeiro, depois medium
                large = videos.get("large", {})
                if large and large.get("url"):
                    results.append({
                        "source": "pixabay",
                        "media_type": "video",
                        "url": large["url"],
                        "width": large.get("width", 0),
                        "height": large.get("height", 0),
                        "duration": hit.get("duration", 0),
                        "photographer": hit.get("user", ""),
                        "id": hit.get("id", "")
                    })
                else:
                    medium = videos.get("medium", {})
                    if medium and medium.get("url"):
                        results.append({
                            "source": "pixabay",
                            "media_type": "video",
                            "url": medium["url"],
                            "width": medium.get("width", 0),
                            "height": medium.get("height", 0),
                            "duration": hit.get("duration", 0),
                            "photographer": hit.get("user", ""),
                            "id": hit.get("id", "")
                        })
        else:
            for hit in data.get("hits", []):
                results.append({
                    "source": "pixabay",
                    "media_type": "photo",
                    "url": hit.get("largeImageURL", ""),
                    "width": hit.get("imageWidth", 0),
                    "height": hit.get("imageHeight", 0),
                    "photographer": hit.get("user", ""),
                    "id": hit.get("id", "")
                })

    except Exception:
        pass

    return results


def buscar_unsplash(query: str, per_page: int = 5) -> list:
    """
    Busca fotos no Unsplash (apenas foto, sem vídeo).
    Usa urls.raw ou urls.full (resolução original).
    NUNCA urls.small/thumb.
    Fallback silencioso se a chave estiver vazia.
    """
    if not UNSPLASH_API_KEY:
        return []

    results = []
    url = "https://api.unsplash.com/search/photos"
    params = {
        "query": query,
        "per_page": per_page,
        "orientation": "landscape"
    }
    headers = {"Authorization": f"Client-ID {UNSPLASH_API_KEY}"}

    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        for photo in data.get("results", []):
            urls = photo.get("urls", {})
            results.append({
                "source": "unsplash",
                "media_type": "photo",
                "url": urls.get("raw", urls.get("full", "")),
                "width": photo.get("width", 0),
                "height": photo.get("height", 0),
                "photographer": photo.get("user", {}).get("name", ""),
                "id": photo.get("id", "")
            })

    except Exception:
        pass

    return results


def buscar_midias(query: str, media_type: str = "video") -> list:
    """
    Busca em todas as fontes disponíveis.
    Retorna lista de candidatos ordenados por score (relevância).
    """
    candidates = []

    # Busca em cada fonte
    pexels_results = buscar_pexels(query, media_type)
    pixabay_results = buscar_pixabay(query, media_type)

    candidates.extend(pexels_results)
    candidates.extend(pixabay_results)

    # Se for foto, busca também no Unsplash
    if media_type == "photo":
        unsplash_results = buscar_unsplash(query)
        candidates.extend(unsplash_results)

    # Atribui score baseado na fonte (só para ordenar)
    for c in candidates:
        if c["source"] == "pexels":
            c["score"] = 1.0
        elif c["source"] == "pixabay":
            c["score"] = 0.8
        elif c["source"] == "unsplash":
            c["score"] = 0.7
        else:
            c["score"] = 0.5

    # Ordena por score
    candidates.sort(key=lambda x: -x["score"])

    return candidates


def _tentar_deletar(arquivo: Path, tentativas: int = 2):
    """
    Tenta deletar arquivo com proteção contra PermissionError.
    Faz até N tentativas com pequeno delay entre elas.
    Se falhar, loga warning não-fatal e continua — nunca propaga exceção.
    """
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
            if arquivo.exists():
                try:
                    arquivo.unlink()
                except Exception:
                    pass
            return False
    return False


def baixar_e_classificar(candidato: dict, scene_id: int) -> Optional[dict]:
    """
    Baixa o candidato, classifica qualidade real, retorna resultado.
    Se qualidade for "red", deleta o arquivo do disco.
    """
    from services.event_logger import log_event

    cache_dir = ASSETS_CACHE_DIR / f"scene_{scene_id}"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Determina extensão
    url = candidato["url"]
    if not url:
        return None

    ext = ".mp4" if candidato["media_type"] == "video" else ".jpg"
    arquivo = cache_dir / f"{candidato['source']}_{candidato['id']}{ext}"

    # Baixa
    if not _baixar_arquivo(url, arquivo):
        return None

    # Obtém resolução real
    width, height = _obter_resolucao_arquivo(arquivo)
    quality = classify_media_quality(width, height)

    # Se red, deleta imediatamente com proteção contra PermissionError
    if quality == "red":
        _tentar_deletar(arquivo)
        return {
            "success": False,
            "quality": quality,
            "reason": f"Resolução {width}x{height} abaixo do mínimo (1280x720)"
        }

    return {
        "success": True,
        "quality": quality,
        "arquivo": str(arquivo),
        "width": width,
        "height": height,
        "source": candidato["source"],
        "media_type": candidato["media_type"],
        "photographer": candidato.get("photographer", ""),
        "id": candidato.get("id", "")
    }