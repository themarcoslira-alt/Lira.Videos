"""
prompt_engine.py — PromptEngine (Fase 1).

Transforma a direção visual estruturada em prompts determinísticos e coerentes
para geração visual:

    scene.visual_plan + scene.locks + visual_profile + contexto
        → image_prompt, animation_prompt, negative_prompt

Princípios:
- 100% determinístico: mesma entrada → mesma saída. NUNCA chama o LLM.
- Hierarquia: STYLE → WORLD → CHARACTER → SUBJECT → ACTION → ENVIRONMENT
  → COMPOSITION → SHOT → CAMERA → LIGHTING → MOOD → CONTINUITY → NEGATIVE.
- Locks globais têm prioridade sobre descrições conflitantes da cena.
- Sem duplicações desnecessárias.
- Não inventa personagem quando a cena não tem.
- Movimento controlado/subtil (conteúdo educacional), salvo se o visual plan
  pedir explicitamente mais energia.

NÃO escreve em scene_plan.json — retorna resultado estruturado; o SceneStore
persiste após validação.
"""

import re
from typing import Optional

from services.event_logger import log_event
from services.visual_profile import VisualProfile

PROMPT_ENGINE_NAME = "PromptEngine"
PROMPT_ENGINE_VERSION = 1

# Marcadores de estilo usados para remover contradições vindas da descrição da cena.
_MARCAS_NAO_FOTOREALISTAS = (
    "2d", "cartoon", "anime", "illustration", "hand-drawn",
    "watercolor", "doodle", "stick figure", "flat",
)
_MARCAS_FOTOREALISTAS = (
    "photorealistic", "photographic", "photoreal", "realistic",
    "cinematic", "3d render", "3d", "photography", "film",
)

PESSOA_TOKENS = (
    "person", "people", "man", "woman", "boy", "girl", "child", "kid", "kids",
    "gardener", "farmer", "worker", "scientist", "researcher", "hands", "hand",
    "character", "figure", "men", "women", "couple", "baby", "neighbor",
    "someone", "everyone", "portrait", "his", "her", "she", "he",
)

_STOP_FALLBACK = {
    "the", "a", "an", "and", "of", "in", "on", "at", "to", "for", "with",
    "that", "this", "is", "are", "was", "were", "be", "been", "it", "its",
    "you", "your", "we", "our", "they", "their", "as", "by", "from", "not",
    "or", "but", "if", "then", "so", "about", "when", "how", "what", "why",
    "all", "one", "two", "some", "very", "just", "too", "also", "there",
    "here", "these", "those", "have", "has", "had", "do", "does", "did",
    "will", "would", "can", "could", "should", "more", "most", "other",
    "such", "only", "own", "same", "than", "near", "maybe", "right", "next",
    "way", "back", "first", "every", "like", "even", "still", "much", "many",
    "now", "good", "well", "know", "see", "go", "get", "make", "thing",
    "things", "something", "anything", "really", "because", "while", "since",
    "during", "before", "after", "again", "always", "never", "often", "today",
}


def _texto(v) -> str:
    return str(v or "").strip()


def _capitalizar(texto: str) -> str:
    texto = _texto(texto)
    if not texto:
        return ""
    return texto[0].upper() + texto[1:]


def _frase(texto: str) -> str:
    """Normaliza um trecho derivado da cena como frase capitalizada."""
    texto = _texto(texto)
    if not texto:
        return ""
    if not texto.endswith((".", "!", "?")):
        texto += "."
    return _capitalizar(texto)


def _adicionar(partes: list, vistos: set, texto: str):
    """Adiciona um parágrafo sem duplicar conteúdo já presente."""
    texto = _texto(texto)
    if not texto:
        return
    norm = texto.lower()
    if norm in vistos:
        return
    if norm in " ".join(partes).lower():
        return
    vistos.add(norm)
    partes.append(texto)


def _sanitizar_contradicoes(texto: str, style: str) -> str:
    """Remove da descrição da cena palavras que contradizem o style_lock."""
    texto = _texto(texto)
    if not texto:
        return texto
    s = style.lower()
    if any(m in s for m in _MARCAS_FOTOREALISTAS):
        remover = _MARCAS_NAO_FOTOREALISTAS
    elif any(m in s for m in _MARCAS_NAO_FOTOREALISTAS):
        remover = _MARCAS_FOTOREALISTAS
    else:
        return texto
    resultado = texto
    for palavra in remover:
        resultado = re.sub(rf"\b{re.escape(palavra)}\b", "", resultado, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", resultado).strip(" ,;")


def _tem_pessoa(vp: dict) -> bool:
    texto = " ".join([
        _texto(vp.get("subject")),
        _texto(vp.get("action")),
        _texto(vp.get("environment")),
        _texto(vp.get("continuity")),
    ]).lower()
    for token in PESSOA_TOKENS:
        if re.search(rf"\b{re.escape(token)}\b", texto):
            return True
    return False


def _sujeito_fallback(scene: dict) -> str:
    narr = scene.get("narration") or {}
    texto = _texto(narr.get("text"))
    if not texto:
        return "main subject"
    primeira = re.split(r"[.!?]", texto)[0].strip()
    palavras = [p.strip(" ,;:'\"").lower() for p in primeira.split()]
    significativas = [p for p in palavras if p and p not in _STOP_FALLBACK]
    base = significativas or palavras
    return " ".join(base[:8]) or "main subject"


def _resolver_locks(scene: dict, visual_profile) -> tuple:
    """Une locks da cena (cópia resolvida) com locks do perfil (fallback)."""
    locks_cena = scene.get("locks") or {}
    if isinstance(visual_profile, VisualProfile):
        locks_perfil = visual_profile.resolved_locks()
        nome = visual_profile.name
    elif isinstance(visual_profile, dict):
        locks_perfil = {
            "style": visual_profile.get("style_lock", ""),
            "character": visual_profile.get("character_lock", ""),
            "world": visual_profile.get("world_lock", ""),
            "composition": visual_profile.get("composition_lock", ""),
            "negative": visual_profile.get("negative_lock", ""),
        }
        nome = visual_profile.get("name", "")
    else:
        locks_perfil = {}
        nome = ""
    resolvidos = {}
    for curto in ("style", "character", "world", "composition", "negative"):
        resolvidos[curto] = _texto(locks_cena.get(curto)) or _texto(locks_perfil.get(curto))
    return resolvidos, _texto(nome)


def _continuidade(scene: dict, previous_scene: Optional[dict]) -> str:
    """Nota curta de continuidade (sem copiar o prompt anterior inteiro)."""
    vp = scene.get("visual_plan") or {}
    if _texto(vp.get("continuity")):
        return _texto(vp["continuity"])
    if previous_scene:
        pvp = previous_scene.get("visual_plan") or {}
        notas = []
        p_suj = _texto(pvp.get("subject"))
        p_amb = _texto(pvp.get("environment"))
        if p_suj and p_suj.lower() != "main subject":
            notas.append(f"same subject ({p_suj})")
        if p_amb and p_amb.lower() != "background environment":
            notas.append(f"same environment ({p_amb})")
        if notas:
            return ", ".join(notas) + "."
    return ""


def _construir_negative_prompt(negative: str) -> str:
    if _texto(negative):
        return f"Avoid: {negative}"
    return "Avoid: unnecessary text, watermarks, logos, distorted anatomy, duplicate objects, visual clutter"


def _animacao(scene: dict, subject: str) -> str:
    """Instrução de animação — movimento controlado/subtil (diretor visual)."""
    vp = scene.get("visual_plan") or {}
    temporal = scene.get("temporal") or {}
    duracao = temporal.get("duration", 4.0)
    mood = _texto(vp.get("mood")).lower()
    intent = _texto(vp.get("visual_intent")).lower()
    shot = _texto(vp.get("shot")).lower()
    camera = _texto(vp.get("camera")).lower()

    alto = (
        any(w in mood for w in ("dynamic", "dramatic", "energetic", "fast", "chaotic", "epic"))
        or any(w in intent for w in ("action", "aerial", "fly", "chase"))
        or "action" in shot
    )
    intensidade = (
        "Moderate, controlled movement with clear intent"
        if alto
        else "Subtle, controlled, natural movement (instructional pacing)"
    )

    if "static" in camera or not camera:
        cam = "Camera remains static; no camera movement"
    elif "handheld" in camera:
        cam = "Very gentle handheld drift"
    elif any(p in camera for p in ("push", "pull", "pan", "dolly", "truck", "crane")):
        cam = f"Slow camera {camera}"
    else:
        cam = "Slow, subtle camera push-in"

    if "closeup" in shot or "close-up" in shot or "macro" in shot:
        movimento = (
            f"Subtle movement within the frame: {subject} shifts slightly; "
            "the background stays soft and static."
        )
    elif "wide" in shot:
        movimento = (
            f"Broad scene with gentle ambient movement (e.g., swaying vegetation); "
            f"{subject} remains clearly readable."
        )
    else:
        movimento = (
            f"Gentle natural movement centered on {subject}; "
            "the surrounding environment remains largely static."
        )

    return " ".join([
        movimento,
        "The original composition, framing and visual style are maintained throughout.",
        f"{cam}.",
        f"{intensidade}.",
        f"The move unfolds over the full {duracao}s of the scene; no sudden jumps.",
    ])


# ---------------------------------------------------------------- validações

def validar_image_prompt(prompt, style=None) -> list:
    erros = []
    if not isinstance(prompt, str) or not prompt.strip():
        erros.append("image_prompt deve ser texto não vazio")
    else:
        if len(prompt.strip()) < 20:
            erros.append("image_prompt muito curto para ser uma instrução visual")
        if style and _texto(style) and _texto(style) not in prompt:
            erros.append("image_prompt deve conter o style_lock")
    return erros


def validar_animation_prompt(prompt) -> list:
    erros = []
    if not isinstance(prompt, str) or not prompt.strip():
        erros.append("animation_prompt deve ser texto não vazio")
    else:
        if not any(w in prompt.lower() for w in ("move", "camera", "static", "movement", "pan", "push", "drift")):
            erros.append("animation_prompt deve conter instruções de movimento")
    return erros


def validar_negative_prompt(prompt) -> list:
    erros = []
    if not isinstance(prompt, str) or not prompt.strip():
        erros.append("negative_prompt deve ser texto não vazio")
    elif "avoid" not in prompt.lower():
        erros.append("negative_prompt deve declarar exclusões (Avoid)")
    return erros


def validar_prompt_result(resultado) -> list:
    erros = []
    if resultado.get("success") is not True:
        erros.append("success deve ser True")
    erros += validar_image_prompt(resultado.get("image_prompt"))
    erros += validar_animation_prompt(resultado.get("animation_prompt"))
    erros += validar_negative_prompt(resultado.get("negative_prompt"))
    return erros


class PromptEngine:
    """Engine determinístico de composição de prompts visuais."""

    NAME = PROMPT_ENGINE_NAME
    VERSION = PROMPT_ENGINE_VERSION

    def generate(self, scene, visual_profile=None, previous_scene=None) -> dict:
        """
        Gera image_prompt / animation_prompt / negative_prompt para uma cena.

        Retorno (contrato estruturado e validável):
          {"success": bool,
           "image_prompt": str, "animation_prompt": str, "negative_prompt": str,
           "metadata": {"profile": str, "scene_id": str, "engine": str, "version": int},
           "errors": []}
        """
        resultado = {
            "success": False,
            "image_prompt": "",
            "animation_prompt": "",
            "negative_prompt": "",
            "metadata": {"profile": "", "scene_id": "", "engine": self.NAME, "version": self.VERSION},
            "errors": [],
        }
        if not isinstance(scene, dict):
            resultado["errors"].append("scene deve ser um objeto")
            return resultado
        resultado["metadata"]["scene_id"] = _texto(scene.get("id"))

        resolvidos, nome_perfil = _resolver_locks(scene, visual_profile)
        resultado["metadata"]["profile"] = nome_perfil

        if not resolvidos["style"]:
            resultado["errors"].append("style_lock é obrigatório (locks globais)")
            return resultado

        try:
            image = self._construir_image_prompt(scene, resolvidos, previous_scene)
            animation = self._animacao_prompt(scene, resolvidos, previous_scene)
            negative = _construir_negative_prompt(resolvidos["negative"])
        except Exception as e:  # noqa: BLE001
            log_event("PROMPT", f"PromptEngine erro ao gerar: {e}", level="error")
            resultado["errors"].append(str(e))
            return resultado

        resultado.update({
            "image_prompt": image,
            "animation_prompt": animation,
            "negative_prompt": negative,
        })
        erros = (
            validar_image_prompt(image)
            + validar_animation_prompt(animation)
            + validar_negative_prompt(negative)
        )
        if erros:
            resultado["errors"] = erros
            return resultado
        resultado["success"] = True
        return resultado

    def generate_many(self, scenes, visual_profile=None, on_progress=None) -> list:
        """Gera para todas as cenas, passando a cena anterior como contexto."""
        resultados = []
        total = len(scenes)
        for i, scene in enumerate(scenes, 1):
            previous = scenes[i - 2] if i >= 2 else None
            r = self.generate(scene, visual_profile, previous)
            resultados.append({"scene": scene, "resultado": r})
            if on_progress:
                on_progress(i, total, r)
        return resultados

    # ------------------------------------------------- construção do image_prompt

    def _construir_image_prompt(self, scene, resolvidos, previous_scene) -> str:
        vp = scene.get("visual_plan") or {}
        partes = []
        vistos = set()

        style = resolvidos["style"]
        world = resolvidos["world"]
        char = resolvidos["character"]
        comp_lock = resolvidos["composition"]

        # 1. STYLE (obrigatório)
        _adicionar(partes, vistos, style)
        # 2. WORLD
        _adicionar(partes, vistos, world)
        # 3. CHARACTER (apenas se a cena tem pessoa)
        if _tem_pessoa(vp) and char:
            _adicionar(partes, vistos, f"Character: {char}")

        subject = _sanitizar_contradicoes(_texto(vp.get("subject")), style)
        if not subject:
            subject = _sanitizar_contradicoes(_sujeito_fallback(scene), style)
        action = _sanitizar_contradicoes(_texto(vp.get("action")), style)
        environment = _sanitizar_contradicoes(_texto(vp.get("environment")), style)
        visual_intent = _sanitizar_contradicoes(_texto(vp.get("visual_intent")), style)
        shot = _sanitizar_contradicoes(_texto(vp.get("shot")), style)
        camera = _sanitizar_contradicoes(_texto(vp.get("camera")), style)
        lighting = _sanitizar_contradicoes(_texto(vp.get("lighting")), style)
        mood = _sanitizar_contradicoes(_texto(vp.get("mood")), style)
        composition = _sanitizar_contradicoes(_texto(vp.get("composition")), style)
        continuity = _texto(vp.get("continuity")) or _continuidade(scene, previous_scene)

        # 4-5. SUBJECT + ACTION
        if visual_intent:
            artigo = "an" if visual_intent[:1].lower() in "aeiou" else "a"
            frase_sujeito = f"{artigo} {visual_intent} view of {subject}"
        else:
            frase_sujeito = f"A view of {subject}"
        if action and action.lower() not in subject.lower():
            frase_sujeito += f", {action}"
        _adicionar(partes, vistos, _frase(frase_sujeito))

        # 6. ENVIRONMENT
        if environment:
            _adicionar(partes, vistos, _frase(environment))

        # 7-9. COMPOSITION (lock global) + SHOT + CAMERA + composition da cena
        comp_bloco = []
        if comp_lock:
            comp_bloco.append(comp_lock)
        cena_comp = []
        if shot:
            cena_comp.append(f"{shot} framing")
        if camera:
            cena_comp.append(f"{camera} camera")
        if composition and composition.lower() not in comp_lock.lower():
            cena_comp.append(f"composition: {composition}")
        if cena_comp:
            comp_bloco.append(", ".join(cena_comp))
        if comp_bloco:
            _adicionar(partes, vistos, _frase(" ".join(comp_bloco)))

        # 10-11. LIGHTING + MOOD
        luz = []
        if lighting:
            if "lighting" in lighting.lower() or lighting.lower().endswith("light"):
                luz.append(lighting)
            else:
                luz.append(f"{lighting} lighting")
        if mood:
            luz.append(f"{mood} mood")
        if luz:
            _adicionar(partes, vistos, _frase(", ".join(luz)))

        # 12. CONTINUITY
        if continuity:
            _adicionar(partes, vistos, _frase(f"Continuity: {continuity}"))

        # 13. NEGATIVE
        _adicionar(partes, vistos, _construir_negative_prompt(resolvidos["negative"]))

        return "\n".join(partes)

    def _animacao_prompt(self, scene, resolvidos, previous_scene) -> str:
        vp = scene.get("visual_plan") or {}
        subject = _texto(vp.get("subject")) or _sujeito_fallback(scene)
        return _animacao(scene, subject)

