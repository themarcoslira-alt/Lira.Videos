"""
visual_planner.py — VisualPlanner (Fase 0).

Fluxo: narração → scene → visual_plan.
O LLM decide O QUE mostrar e COMO (sujeito, ação, ambiente, enquadramento,
câmera, iluminação, composição, mood, continuidade, intenção visual).
O CÓDIGO decide timestamps, arquivos, estado, armazenamento, render.

Contrato: cada cena recebe (narration + contexto da cena + cena anterior +
visual profile/locks) e produz um visual_plan estruturado e validável.

O planner NÃO gera queries (isso é do QueryGenerator) e NUNCA escreve em
scene_plan.json — retorna resultado para a camada de aplicação persistir.
"""

import re
from typing import Optional

from services.event_logger import log_event
from services.llm_client import LLMClient, LLMError, LLMSchemaError
from services.scene_plan_schema import VISUAL_PLAN_FIELDS, validar_visual_plan
from services.visual_profile import VisualProfile

_STOPWORDS = {
    "the", "a", "an", "and", "of", "in", "on", "at", "to", "for", "with",
    "that", "this", "is", "are", "was", "were", "be", "been", "it", "its",
    "you", "your", "we", "our", "they", "their", "he", "she", "his", "her",
    "as", "by", "from", "not", "or", "but", "if", "then", "so", "about",
    "when", "how", "what", "why", "all", "one", "two", "some", "very", "just",
    "too", "also", "there", "here", "these", "those", "have", "has", "had",
    "do", "does", "did", "will", "would", "can", "could", "should", "more",
    "most", "other", "such", "only", "own", "same", "than",
    # palavras discursivas/frequentes (para fallback local determinístico)
    "near", "maybe", "right", "next", "way", "back", "first", "every", "like",
    "even", "still", "much", "many", "now", "good", "well", "know", "see", "go",
    "get", "make", "thing", "things", "something", "anything", "really", "because",
    "while", "since", "during", "before", "after", "again", "always", "never",
    "often", "today",
}

DEFAULT_VISUAL_PLAN = {
    "visual_intent": "contextual",
    "subject": "",
    "action": "",
    "environment": "",
    "shot": "medium",
    "camera": "static",
    "lighting": "natural light",
    "composition": "rule of thirds",
    "mood": "neutral",
    "continuity": "",
}


def _normalizar_visual_plan(dados) -> dict:
    """Garante que todas as 10 chaves existam com tipo texto."""
    plano = dict(DEFAULT_VISUAL_PLAN)
    if isinstance(dados, dict):
        for campo in VISUAL_PLAN_FIELDS:
            valor = dados.get(campo)
            if isinstance(valor, str):
                plano[campo] = valor.strip()
            elif isinstance(valor, (int, float)) and not isinstance(valor, bool):
                plano[campo] = str(valor)
    return plano


class VisualPlanner:
    """Diretor visual. provider=None → planejamento local determinístico (offline)."""

    def __init__(self, provider=None, model=None, max_retries=None, timeout=None, retry_delay=0.5):
        self.provider = provider
        self.model = model
        self.max_retries = max_retries
        self.timeout = timeout
        self.retry_delay = retry_delay

    # ------------------------------------------------------------------ API

    def planejar_cena(self, scene: dict, visual_profile: Optional[VisualProfile] = None,
                      previous_scene: Optional[dict] = None, provider=None) -> dict:
        """Planeja UMA cena.

        Retorna:
          {"success": bool, "mode": "local"|"llm",
           "visual_plan": {...10 campos...},
           "locks": {...5 locks resolvidos...},
           "error": str}
        """
        profile = visual_profile or VisualProfile.default()
        locks = profile.resolved_locks()
        provider_efetivo = provider or self.provider

        if provider_efetivo is None:
            plano = self._planejar_local(scene, previous_scene)
            return {"success": True, "mode": "local", "visual_plan": plano, "locks": locks, "error": ""}

        system, user = self._construir_prompt(scene, previous_scene, profile)
        try:
            client = LLMClient(provider_efetivo, model=self.model,
                               max_retries=self.max_retries, timeout=self.timeout,
                               retry_delay=self.retry_delay)
            dados = client.complete_json(system, user, validator=validar_visual_plan,
                                         temperature=0.2, max_tokens=2000)
            plano = _normalizar_visual_plan(dados)
            return {"success": True, "mode": "llm", "visual_plan": plano, "locks": locks, "error": ""}
        except LLMSchemaError as e:
            log_event("VISUAL_PLAN", f"cena {scene.get('id')}: resposta LLM inválida — {e}", level="error")
            return {"success": False, "mode": "llm", "visual_plan": _normalizar_visual_plan({}), "locks": locks, "error": str(e)}
        except LLMError as e:
            log_event("VISUAL_PLAN", f"cena {scene.get('id')}: erro LLM — {e}", level="error")
            return {"success": False, "mode": "llm", "visual_plan": _normalizar_visual_plan({}), "locks": locks, "error": str(e)}

    def planejar_todas(self, scenes: list, visual_profile: Optional[VisualProfile] = None,
                       provider=None, on_progress=None) -> list:
        """Planeja todas as cenas. Retorna lista de dicts {scene, resultado}."""
        resultados = []
        total = len(scenes)
        for i, scene in enumerate(scenes, 1):
            previous = scenes[i - 2] if i >= 2 else None
            r = self.planejar_cena(scene, visual_profile, previous, provider)
            resultados.append({"scene": scene, "resultado": r})
            if on_progress:
                on_progress(i, total, r)
        return resultados

    # ------------------------------------------------- planejamento local

    def _planejar_local(self, scene: dict, previous_scene: Optional[dict] = None) -> dict:
        """Fallback determinístico offline (sem rede). Mesma entrada → mesma saída."""
        narration = scene.get("narration") or {}
        texto = str(narration.get("text", "") or scene.get("texto", ""))
        tokens = [t for t in re.split(r"[^a-zA-Z]+", texto.lower()) if len(t) >= 4 and t not in _STOPWORDS]
        subject = " ".join(tokens[:3]) or "main subject"
        action = " ".join(tokens[3:5]) or "scene action"
        continuity = "consistent with previous scenes" if previous_scene else "opening scene"
        return {
            "visual_intent": "contextual",
            "subject": subject,
            "action": action,
            "environment": "background environment",
            "shot": "medium",
            "camera": "static",
            "lighting": "natural light",
            "composition": "rule of thirds",
            "mood": "neutral",
            "continuity": continuity,
        }

    # ------------------------------------------------------------------ prompt

    def _construir_prompt(self, scene: dict, previous_scene: Optional[dict],
                          profile: VisualProfile):
        temporal = scene.get("temporal") or {}
        narration = scene.get("narration") or {}
        prev_text = ""
        if previous_scene:
            prev_text = (previous_scene.get("narration") or {}).get("text", "") or previous_scene.get("texto", "")
        scene_plan_atual = scene.get("visual_plan") or {}
        vp_suggestion = scene_plan_atual.get("visual_intent", "") if isinstance(scene_plan_atual, dict) else ""

        system = (
            "You are the VISUAL DIRECTOR of a narrated video.\n"
            "You decide ONLY what to show and how: subject, action, environment, shot, camera, "
            "lighting, composition, mood, continuity and visual intent.\n"
            "You NEVER decide timestamps, durations, files, downloads, APIs, encoding or pipeline operations.\n"
            "You MUST respect the MASTER STYLE LOCKS below. Never violate them.\n\n"
            f"STYLE LOCK: {profile.style_lock}\n"
            f"CHARACTER LOCK: {profile.character_lock}\n"
            f"WORLD LOCK: {profile.world_lock}\n"
            f"COMPOSITION LOCK: {profile.composition_lock}\n"
            f"NEGATIVE LOCK: {profile.negative_lock}\n\n"
            "Respond with STRICT JSON ONLY for the single scene, with exactly these string fields:\n"
            "visual_intent, subject, action, environment, shot, camera, lighting, composition, mood, continuity\n"
            "No markdown, no comments, no text outside the JSON object."
        )
        user = (
            f"Scene: {scene.get('id')}\n"
            f"Time: start={temporal.get('start')}s end={temporal.get('end')}s "
            f"duration={temporal.get('duration')}s\n"
            f"Narration: {narration.get('text', '')}\n"
            f"Previous scene narration: {prev_text or '(none)'}\n"
            f"Visual intent suggestion: {vp_suggestion or '(none)'}"
        )
        return system, user
