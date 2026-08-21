"""
scene_plan_schema.py — Schema e validação do scene_plan.json (Fase 0).

Representação canônica de cada cena para a nova arquitetura de Direção Visual.
Arquivo ADITIVO: não substitui cenas.json / storyboard.json / midias_encontradas.json.

Status são INDEPENDENTES por estágio:
    planning / prompt / media / render  ∈  {pending, processing, ready, error}
"""

SCENE_PLAN_VERSION = 1

STATUS_VALUES = ("pending", "processing", "ready", "error")
STATUS_STAGES = ("planning", "prompt", "media", "render")

VISUAL_PLAN_FIELDS = (
    "visual_intent", "subject", "action", "environment",
    "shot", "camera", "lighting", "composition", "mood", "continuity",
)

LOCK_FIELDS = ("style", "character", "world", "composition", "negative")

MEDIA_PLAN_FIELDS = ("primary_queries", "fallback_queries", "synonyms")

REQUIRED_SCENE_KEYS = (
    "id", "temporal", "narration", "visual_plan",
    "locks", "media_plan", "selected_media", "prompt", "status",
)

REQUIRED_PROJECT_KEYS = ("id",)


def _eh_numero(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _eh_texto(v):
    return isinstance(v, str)


def _eh_lista_texto(v):
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def novo_status():
    """Status com todos os estágios em 'pending'."""
    return {stage: "pending" for stage in STATUS_STAGES}


def nova_scene(scene_id, start, end, texto, timestamp=""):
    """Cria uma cena canônica (skeleton) com os conceitos obrigatórios."""
    start = float(start or 0)
    end = float(end if end is not None else (start + 5.0))
    return {
        "id": scene_id,
        "temporal": {
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(max(0.0, end - start), 3),
        },
        "narration": {
            "text": str(texto or ""),
            "start": round(start, 3),
            "end": round(end, 3),
            "timestamp": str(timestamp or ""),
        },
        "visual_plan": {field: "" for field in VISUAL_PLAN_FIELDS},
        "locks": {field: "" for field in LOCK_FIELDS},
        "media_plan": {field: [] for field in MEDIA_PLAN_FIELDS},
        "selected_media": [],
        "prompt": {
            "engine": "PromptEngine",
            "version": 1,
            "text": "",
            "image_prompt": "",
            "animation_prompt": "",
            "negative_prompt": "",
            "generated_at": None,
        },
        "status": novo_status(),
    }


def nova_scene_plan(project_id, title="", visual_profile=None):
    """Cria um scene_plan vazio (com scenes list)."""
    return {
        "project": {
            "id": str(project_id),
            "title": str(title or project_id),
            "version": SCENE_PLAN_VERSION,
        },
        "visual_profile": dict(visual_profile or {}),
        "scenes": [],
    }


def validar_status(value):
    """Retorna lista de erros (vazia se ok)."""
    if value not in STATUS_VALUES:
        return [f"status inválido: {value!r} (permitidos: {', '.join(STATUS_VALUES)})"]
    return []


def validar_visual_plan(vp):
    if not isinstance(vp, dict):
        return ["visual_plan deve ser um objeto"]
    return [f"visual_plan.{f} deve ser texto" for f in VISUAL_PLAN_FIELDS if not _eh_texto(vp.get(f, ""))]


def validar_locks(locks):
    if not isinstance(locks, dict):
        return ["locks deve ser um objeto"]
    return [f"locks.{f} deve ser texto" for f in LOCK_FIELDS if not _eh_texto(locks.get(f, ""))]


def validar_media_plan(mp):
    if not isinstance(mp, dict):
        return ["media_plan deve ser um objeto"]
    erros = []
    for f in MEDIA_PLAN_FIELDS:
        if not _eh_lista_texto(mp.get(f, [])):
            erros.append(f"media_plan.{f} deve ser lista de texto")
    return erros


def validar_prompt(prompt):
    """Valida scene.prompt — aceita o formato antigo {text, generated_at}
    e o novo formato Fase 1 (engine/version/image_prompt/animation_prompt/negative_prompt)."""
    if not isinstance(prompt, dict):
        return ["prompt deve ser um objeto"]
    erros = []
    if not _eh_texto(prompt.get("text", "")):
        erros.append("text deve ser texto")
    for campo in ("engine", "image_prompt", "animation_prompt", "negative_prompt"):
        if campo in prompt and not _eh_texto(prompt[campo]):
            erros.append(f"{campo} deve ser texto")
    if "version" in prompt and not isinstance(prompt["version"], int):
        erros.append("version deve ser inteiro")
    ga = prompt.get("generated_at")
    if ga is not None and not isinstance(ga, str):
        erros.append("generated_at deve ser texto ou null")
    return erros


def validar_scene(scene):
    if not isinstance(scene, dict):
        return ["cena deve ser um objeto"]
    erros = []
    if not (_eh_texto(scene.get("id", "")) or isinstance(scene.get("id"), (int, float))):
        erros.append("scene.id obrigatório")
    temporal = scene.get("temporal", {})
    if not isinstance(temporal, dict) or not all(_eh_numero(temporal.get(k)) for k in ("start", "end", "duration")):
        erros.append("scene.temporal.start/end/duration devem ser numéricos")
    narration = scene.get("narration", {})
    if not isinstance(narration, dict) or not _eh_texto(narration.get("text", "")):
        erros.append("scene.narration.text obrigatório")
    erros += validar_visual_plan(scene.get("visual_plan", {}))
    erros += validar_locks(scene.get("locks", {}))
    erros += validar_media_plan(scene.get("media_plan", {}))
    if not isinstance(scene.get("selected_media", None), list):
        erros.append("scene.selected_media deve ser lista")
    prompt = scene.get("prompt", {})
    if not isinstance(prompt, dict) or not _eh_texto(prompt.get("text", "")):
        erros.append("scene.prompt.text obrigatório")
    else:
        erros += [f"scene.prompt.{e}" for e in validar_prompt(prompt)]
    status = scene.get("status", {})
    if not isinstance(status, dict):
        erros.append("scene.status deve ser objeto")
    else:
        for stage in STATUS_STAGES:
            if stage not in status:
                erros.append(f"scene.status.{stage} ausente")
            else:
                erros += [f"scene.status.{stage}: {e}" for e in validar_status(status[stage])]
    return erros


def validar_scene_plan(plan):
    if not isinstance(plan, dict):
        return ["scene_plan deve ser um objeto"]
    erros = []
    projeto = plan.get("project", {})
    if not isinstance(projeto, dict) or not _eh_texto(projeto.get("id", "")):
        erros.append("project.id obrigatório")
    if not isinstance(plan.get("visual_profile", None), dict):
        erros.append("visual_profile deve ser um objeto")
    scenes = plan.get("scenes")
    if not isinstance(scenes, list):
        erros.append("scenes deve ser uma lista")
    else:
        for i, sc in enumerate(scenes):
            erros += [f"scenes[{i}].{e}" for e in validar_scene(sc)]
    return erros


def eh_valida_scene(scene):
    return not validar_scene(scene)


def eh_valido_scene_plan(plan):
    return not validar_scene_plan(plan)
