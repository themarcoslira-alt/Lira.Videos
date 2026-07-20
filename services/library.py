"""
library.py — Biblioteca de Mídia local
Organização por categoria, busca em 2 níveis de relevância.
"""
import json
import shutil
from pathlib import Path
from typing import Optional
from config import BIBLIOTECA_DIR


def _carregar_biblioteca() -> dict:
    """Carrega o índice da biblioteca do disco."""
    index_file = BIBLIOTECA_DIR / "index.json"
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"entries": [], "categories": {}}


def _salvar_biblioteca(data: dict):
    """Salva o índice da biblioteca no disco."""
    index_file = BIBLIOTECA_DIR / "index.json"
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def adicionar_media_biblioteca(media_info: dict, scene_data: dict):
    """
    Adiciona mídia encontrada à biblioteca.
    Copia o arquivo para a pasta Biblioteca organizada por categoria.
    """
    if not media_info.get("success"):
        return

    arquivo_origem = media_info.get("arquivo")
    if not arquivo_origem or not Path(arquivo_origem).exists():
        return

    scene_type = scene_data.get("scene_type", "geral")
    scene_keywords = scene_data.get("keywords", [])

    # Cria diretório por categoria
    cat_dir = BIBLIOTECA_DIR / scene_type
    cat_dir.mkdir(parents=True, exist_ok=True)

    # Copia arquivo
    ext = Path(arquivo_origem).suffix
    nome_destino = f"{media_info['source']}_{media_info['id']}{ext}"
    destino = cat_dir / nome_destino

    try:
        shutil.copy2(arquivo_origem, destino)
    except Exception:
        return

    # Adiciona ao índice
    data = _carregar_biblioteca()

    entry = {
        "id": media_info.get("id", ""),
        "arquivo": str(destino),
        "source": media_info.get("source", ""),
        "media_type": media_info.get("media_type", "video"),
        "quality": media_info.get("quality", "yellow"),
        "width": media_info.get("width", 0),
        "height": media_info.get("height", 0),
        "photographer": media_info.get("photographer", ""),
        "scene_type": scene_type,
        "keywords": scene_keywords,
        "tags": scene_keywords
    }

    # Evita duplicatas no índice
    for existing in data["entries"]:
        if existing.get("id") == entry["id"] and existing.get("source") == entry["source"]:
            return  # Já existe

    data["entries"].append(entry)

    # Organiza por categoria
    if scene_type not in data["categories"]:
        data["categories"][scene_type] = []
    data["categories"][scene_type].append(entry["id"])

    _salvar_biblioteca(data)


def find_relevant_entry(keywords: list, scene_category: str) -> Optional[dict]:
    """
    Busca entrada relevante na biblioteca em 2 níveis:
    Nível 1 (preferencial): categoria bate E pelo menos 1 keyword em comum.
    Nível 2 (fallback): palavra exata e específica em comum (mesmo sem bater categoria).
    Retorna None se não achar nada relevante.
    """
    data = _carregar_biblioteca()
    entries = data.get("entries", [])

    if not entries:
        return None

    keywords_set = set(k.lower() for k in keywords if k)

    # Nível 1: categoria bate + keyword em comum
    nivel1_candidates = []
    for entry in entries:
        entry_category = entry.get("scene_type", "")
        entry_keywords = set(k.lower() for k in entry.get("keywords", []) if k)
        entry_tags = set(k.lower() for k in entry.get("tags", []) if k)

        if entry_category == scene_category:
            common = keywords_set & (entry_keywords | entry_tags)
            if common:
                nivel1_candidates.append((entry, len(common)))

    if nivel1_candidates:
        # Ordena por mais matches
        nivel1_candidates.sort(key=lambda x: -x[1])
        best = nivel1_candidates[0][0]
        best["reuse_tier"] = 1
        return best

    # Nível 2: palavra exata e específica (não genérica)
    generic_words = {
        "video", "photo", "image", "scene", "stock", "b-roll", "background",
        "vídeo", "foto", "imagem", "cena", "fundo"
    }

    nivel2_candidates = []
    for entry in entries:
        entry_keywords = set(k.lower() for k in entry.get("keywords", []) if k)
        entry_tags = set(k.lower() for k in entry.get("tags", []) if k)

        common = keywords_set & (entry_keywords | entry_tags)
        # Filtra palavras genéricas
        specific = common - generic_words
        if specific:
            nivel2_candidates.append((entry, len(specific)))

    if nivel2_candidates:
        nivel2_candidates.sort(key=lambda x: -x[1])
        best = nivel2_candidates[0][0]
        best["reuse_tier"] = 2
        return best

    return None


def listar_biblioteca() -> dict:
    """Lista todas as entradas da biblioteca."""
    data = _carregar_biblioteca()
    return {
        "total": len(data.get("entries", [])),
        "categories": list(data.get("categories", {}).keys()),
        "entries": data.get("entries", [])
    }


def remover_media(media_id: str, source: str) -> bool:
    """Remove mídia da biblioteca."""
    data = _carregar_biblioteca()

    new_entries = []
    removed = False
    for entry in data.get("entries", []):
        if entry.get("id") == media_id and entry.get("source") == source:
            # Remove arquivo físico
            arquivo = Path(entry.get("arquivo", ""))
            if arquivo.exists():
                arquivo.unlink()
            removed = True
        else:
            new_entries.append(entry)

    if removed:
        data["entries"] = new_entries
        # Reconstroi categorias
        data["categories"] = {}
        for entry in new_entries:
            cat = entry.get("scene_type", "geral")
            if cat not in data["categories"]:
                data["categories"][cat] = []
            data["categories"][cat].append(entry["id"])
        _salvar_biblioteca(data)

    return removed