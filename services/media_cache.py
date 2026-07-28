"""
media_cache.py — Cache local de midia ja buscada/baixada, para reuso entre projetos.
Isolado por enquanto: NAO integrado na cascata de busca ainda.
"""
import json
import re
from config import ASSETS_CACHE_DIR

CACHE_INDEX_FILE = ASSETS_CACHE_DIR / "cache_index.json"


def _normalizar_query(query: str) -> str:
    q = query.lower().strip()
    return re.sub(r'\s+', ' ', q)


def _carregar_indice() -> dict:
    if CACHE_INDEX_FILE.exists():
        try:
            with open(CACHE_INDEX_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _salvar_indice(indice: dict) -> None:
    ASSETS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(indice, f, indent=2, ensure_ascii=False)


def buscar_no_cache(query: str):
    indice = _carregar_indice()
    q_norm = _normalizar_query(query)
    if q_norm in indice:
        return indice[q_norm]
    for chave, valor in indice.items():
        if q_norm in chave or chave in q_norm:
            return valor
    return None


def registrar_no_cache(query: str, arquivo: dict) -> None:
    indice = _carregar_indice()
    indice[_normalizar_query(query)] = arquivo
    _salvar_indice(indice)