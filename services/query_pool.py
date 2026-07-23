"""
query_pool.py — Pool compartilhado de queries por cena
As queries pertencem à CENA, não à API.
Todas as queries do pool são testadas em todas as APIs disponíveis.
"""
from typing import Optional


class QueryPool:
    """
    Pool de queries para uma cena.
    Suporta múltiplas queries, todas compartilhadas entre todas as APIs.

    Uso:
        pool = QueryPool(scene_id=7, queries=["ant carrying leaf", "leafcutter ant"])
        for q in pool:
            # testar q em Pexels, Pixabay, Unsplash
    """

    def __init__(self, scene_id: int, queries: list,
                 media_type: str = "video",
                 fallback_queries: list = None):
        """
        Args:
            scene_id: ID da cena
            queries: Lista de queries primárias (pelo menos 1)
            media_type: "video" ou "photo"
            fallback_queries: Lista opcional de queries de fallback
        """
        self.scene_id = scene_id
        # Deduplica mantendo ordem
        seen = set()
        self._queries = []
        for q in (queries or []):
            if q not in seen:
                seen.add(q)
                self._queries.append(q)
        self.media_type = media_type
        self._fallback = []
        for fq in (fallback_queries or []):
            if fq not in seen:
                seen.add(fq)
                self._fallback.append(fq)

    @property
    def queries(self) -> list:
        """Todas as queries disponíveis (primárias + fallback)."""
        todas = list(self._queries)
        for fq in self._fallback:
            if fq not in todas:
                todas.append(fq)
        return todas

    @property
    def primary_queries(self) -> list:
        """Apenas queries primárias."""
        return list(self._queries)

    @property
    def fallback_queries(self) -> list:
        """Apenas queries de fallback."""
        return list(self._fallback)

    def add_query(self, query: str, primary: bool = True):
        """Adiciona uma query ao pool."""
        if primary:
            if query not in self._queries:
                self._queries.append(query)
        else:
            if query not in self._fallback:
                self._fallback.append(query)

    def total_queries(self) -> int:
        """Total de queries no pool."""
        return len(self._queries) + len(self._fallback)

    def __len__(self) -> int:
        return self.total_queries()

    def __iter__(self):
        return iter(self.queries)

    def __repr__(self) -> str:
        return (f"QueryPool(cena={self.scene_id}, "
                f"queries={len(self._queries)}, "
                f"fallback={len(self._fallback)}, "
                f"media={self.media_type})")

    @staticmethod
    def from_single(scene_id: int, query: str, media_type: str = "video") -> "QueryPool":
        """Cria um pool com uma única query (compatibilidade retroativa)."""
        return QueryPool(scene_id=scene_id, queries=[query], media_type=media_type)