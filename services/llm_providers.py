"""
llm_providers.py — Providers LLM configuráveis (Fase 0).

- DeepSeekProvider: modelo configurável (LLM_MODEL) — nunca fixa um nome de
  modelo no código.
- LocalProvider: determinístico/offline (testes e demo sem rede).
- criar_provider(): fábrica por nome ("deepseek" | "local").
"""

import json
import re

import requests

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_PROVIDER, LLM_TIMEOUT
from services.event_logger import log_event
from services.llm_client import LLMError

_STOP = {
    "the", "a", "an", "and", "of", "in", "on", "at", "to", "for", "with",
    "that", "this", "is", "are", "was", "were", "be", "been", "it", "its",
    "you", "your", "we", "our", "they", "their", "he", "she", "his", "her",
    "as", "by", "from", "not", "or", "but", "if", "then", "so", "about",
    "when", "how", "what", "why", "all", "one", "two", "some", "very", "just",
    "too", "also", "there", "here", "these", "those", "have", "has", "had",
    "do", "does", "did", "will", "would", "can", "could", "should", "more",
    "most", "other", "such", "only", "own", "same", "than",
    # palavras discursivas/frequentes (para provider local determinístico)
    "near", "maybe", "right", "next", "way", "back", "first", "every", "like",
    "even", "still", "much", "many", "now", "good", "well", "know", "see", "go",
    "get", "make", "thing", "things", "something", "anything", "really", "because",
    "while", "since", "during", "before", "after", "again", "always", "never",
    "often", "today",
}


class LLMProvider:
    name = "base"

    def __init__(self, model=None):
        self.model = model or LLM_MODEL or None

    def generate(self, messages: list, model=None, temperature: float = 0.2,
                 max_tokens: int = 2000) -> str:
        raise NotImplementedError


class DeepSeekProvider(LLMProvider):
    """Provider DeepSeek (API OpenAI-compatible) com modelo configurável."""

    name = "deepseek"

    def __init__(self, api_key=None, model=None, base_url=None, timeout=None):
        super().__init__(model=model)
        self.api_key = api_key or LLM_API_KEY or ""
        self.base_url = (base_url or LLM_BASE_URL).rstrip("/")
        self.timeout = timeout or LLM_TIMEOUT

    def generate(self, messages, model=None, temperature=0.2, max_tokens=2000) -> str:
        modelo = model or self.model
        if not self.api_key:
            raise LLMError("DeepSeek: api_key não configurada (defina LLM_API_KEY)")
        if not modelo:
            raise LLMError("DeepSeek: modelo não configurado — defina LLM_MODEL (nenhum modelo é fixo no código)")
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": modelo,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            if resp.status_code != 200:
                raise LLMError(f"DeepSeek HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            return content
        except LLMError:
            raise
        except Exception as e:
            raise LLMError(f"DeepSeek erro: {e}") from e


class LocalProvider(LLMProvider):
    """Provider determinístico offline — resposta JSON válida para o VisualPlanner."""

    name = "local"

    def __init__(self, model="local-deterministic"):
        super().__init__(model=model)

    def generate(self, messages, model=None, temperature=0.2, max_tokens=2000) -> str:
        texto = ""
        for m in reversed(messages or []):
            if isinstance(m, dict) and m.get("role") == "user":
                texto = str(m.get("content", ""))
                break
        tokens = [t for t in re.split(r"[^a-zA-Z]+", texto.lower()) if len(t) >= 4 and t not in _STOP]
        subject = " ".join(tokens[:3]) or "main subject"
        action = " ".join(tokens[3:5]) or "scene action"
        plano = {
            "visual_intent": "contextual",
            "subject": subject,
            "action": action,
            "environment": "background environment",
            "shot": "medium",
            "camera": "static",
            "lighting": "natural light",
            "composition": "rule of thirds",
            "mood": "neutral",
            "continuity": "consistent with previous scenes",
        }
        log_event("LLM", "LocalProvider gerou plano determinístico (offline)", level="info")
        return json.dumps(plano, ensure_ascii=False)


PROVIDERS = {
    "deepseek": DeepSeekProvider,
    "local": LocalProvider,
}


def criar_provider(nome=None, **opts) -> LLMProvider:
    """Fábrica de provider por nome; default = config.LLM_PROVIDER."""
    nome = (nome or LLM_PROVIDER or "local").strip().lower()
    if nome not in PROVIDERS:
        raise ValueError(f"provider desconhecido: {nome!r} (disponíveis: {', '.join(PROVIDERS)})")
    return PROVIDERS[nome](**opts)
