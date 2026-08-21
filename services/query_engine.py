"""
query_engine.py — QueryGenerator (Fase 0).

Transforma um visual_plan em queries adequadas por provider (Pexels, Pixabay,
Unsplash, image/video generation). Determinístico: mesma entrada → mesma saída.

Separado do VisualPlanner: o planner descreve a cena; o QueryGenerator traduz
essa descrição em consultas. Isso permite regras específicas por provider sem
alterar o planner.
"""

from typing import Optional

_STOPWORDS = {
    "the", "a", "an", "and", "of", "in", "on", "at", "to", "for", "with",
    "that", "this", "is", "are", "was", "were", "be", "been", "it", "its",
    "you", "your", "we", "our", "they", "their", "he", "she", "his", "her",
    "as", "by", "from", "not", "or", "but", "if", "then", "so", "about",
    "when", "how", "what", "why", "all", "one", "two", "some", "very", "just",
    "too", "also", "there", "here", "these", "those", "have", "has", "had",
    "do", "does", "did", "will", "would", "can", "could", "should", "more",
    "most", "other", "such", "only", "own", "same", "than",
    # palavras discursivas/frequentes (tokens não semânticos p/ queries)
    "near", "maybe", "right", "next", "way", "back", "first", "every", "like",
    "even", "still", "much", "many", "now", "good", "well", "know", "see", "go",
    "get", "make", "thing", "things", "something", "anything", "really", "because",
    "while", "since", "during", "before", "after", "again", "always", "never",
    "often", "today",
}

PROVIDER_PROFILES = {
    "pexels": {"max_primary": 4, "max_fallback": 3, "max_synonyms": 4},
    "pixabay": {"max_primary": 4, "max_fallback": 3, "max_synonyms": 4},
    "unsplash": {"max_primary": 4, "max_fallback": 3, "max_synonyms": 4},
    "image_generation": {"max_primary": 3, "max_fallback": 2, "max_synonyms": 3},
    "video_generation": {"max_primary": 3, "max_fallback": 2, "max_synonyms": 3},
}


class QueryGenerator:
    """Gera queries de busca/geração a partir de um visual_plan (determinístico)."""

    def __init__(self, language: str = "en"):
        self.language = language

    # ------------------------------------------------------- helpers

    @staticmethod
    def _limpar(valor) -> str:
        return str(valor or "").strip()

    @staticmethod
    def _dedup(lista) -> list:
        vistos = set()
        saida = []
        for item in lista:
            if item and item.lower() not in vistos:
                vistos.add(item.lower())
                saida.append(item)
        return saida

    def _tokens_significativos(self, *valores) -> list:
        tokens = []
        for valor in valores:
            for tok in str(valor or "").lower().replace(",", " ").split():
                tok = tok.strip(".'\"()[]-")
                if len(tok) >= 3 and tok not in _STOPWORDS and tok not in tokens:
                    tokens.append(tok)
        return tokens

    # ------------------------------------------------------- API

    def generate(self, visual_plan: Optional[dict], narration_text: str = "") -> dict:
        """Queries genéricas (aplicáveis a qualquer provider de stock)."""
        vp = visual_plan or {}
        subject = self._limpar(vp.get("subject", ""))
        action = self._limpar(vp.get("action", ""))
        environment = self._limpar(vp.get("environment", ""))
        vintent = self._limpar(vp.get("visual_intent", ""))
        mood = self._limpar(vp.get("mood", ""))
        shot = self._limpar(vp.get("shot", ""))

        primary = []
        if subject and action:
            primary.append(f"{subject} {action}")
        elif subject:
            primary.append(subject)
        if subject and environment:
            primary.append(f"{subject} {environment}")
        if vintent and subject:
            primary.append(f"{vintent} {subject}")
        if mood and subject:
            primary.append(f"{mood} {subject}")
        if not primary and narration_text:
            primary.append(self._limpar(narration_text)[:120])

        fallback = [subject, environment, action, vintent, shot]
        synonyms = self._tokens_significativos(subject, action, environment, vintent, mood)

        return {
            "primary_queries": self._dedup(primary)[:4],
            "fallback_queries": self._dedup([f for f in fallback if f])[:3],
            "synonyms": self._dedup(synonyms)[:4],
        }

    def generate_for_provider(self, visual_plan: Optional[dict], provider: str = "pexels",
                              narration_text: str = "") -> dict:
        """Queries ajustadas ao provider (regras específicas por fonte)."""
        base = self.generate(visual_plan, narration_text)
        perfil = PROVIDER_PROFILES.get(provider, PROVIDER_PROFILES["pexels"])

        resultado = {
            "primary_queries": base["primary_queries"][:perfil["max_primary"]],
            "fallback_queries": base["fallback_queries"][:perfil["max_fallback"]],
            "synonyms": base["synonyms"][:perfil["max_synonyms"]],
        }

        # Geração de imagem/vídeo: reforça atmosfera (mood) nas queries primárias.
        if provider in ("image_generation", "video_generation"):
            mood = self._limpar((visual_plan or {}).get("mood", ""))
            if mood:
                resultado["primary_queries"] = [
                    f"{q}, {mood} atmosphere" for q in resultado["primary_queries"]
                ][:perfil["max_primary"]]
        return resultado
