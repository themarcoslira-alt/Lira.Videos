"""
scene_store.py — Persistência central do scene_plan.json (Fase 0).

Único módulo autorizado a escrever scene_plan.json.
Responsabilidades: carregar, salvar, atualizar cena/status/visual_plan/locks/
prompt/media_plan/selected_media, preservar dados existentes, escrita segura.

Arquivo ADITIVO — coexiste com cenas.json / storyboard.json / midias_encontradas.json.
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from config import PROJETOS_DIR, SCENE_PLAN_FILE
from services.event_logger import log_event
from services.scene_plan_schema import (
    STATUS_STAGES,
    STATUS_VALUES,
    nova_scene,
    nova_scene_plan,
    validar_scene,
    validar_scene_plan,
)


class SceneStore:
    """Fachada de persistência do scene_plan de um projeto."""

    def __init__(self, project_name: str, base_dir=None):
        self.project_name = str(project_name)
        self.base_dir = Path(base_dir) if base_dir is not None else PROJETOS_DIR
        self.project_dir = self.base_dir / self.project_name
        self.plan_file = self.project_dir / SCENE_PLAN_FILE
        self._lock = threading.RLock()

    # ---------------- leitura ----------------

    def path(self) -> Path:
        return self.plan_file

    def exists(self) -> bool:
        return self.plan_file.exists()

    def load(self) -> Optional[dict]:
        if not self.plan_file.exists():
            return None
        try:
            with self.plan_file.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log_event("SCENE_PLAN", f"scene_plan.json corrompido ({self.project_name}): {e}", level="warn")
            return None

    def get_scenes(self) -> list:
        plan = self.load()
        return plan.get("scenes", []) if plan else []

    def get_scene(self, scene_id) -> Optional[dict]:
        sid = str(scene_id)
        for sc in self.get_scenes():
            if str(sc.get("id")) == sid:
                return sc
        return None

    # ---------------- escrita segura ----------------

    def _atomic_write(self, plan: dict):
        self.project_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.project_dir / (SCENE_PLAN_FILE + ".tmp")
        tmp.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(self.plan_file))

    def save(self, plan: dict, validate: bool = True) -> bool:
        with self._lock:
            if validate:
                erros = validar_scene_plan(plan)
                if erros:
                    log_event("SCENE_PLAN", f"scene_plan inválido — não salvo: {erros[:3]}", level="error")
                    return False
            self._atomic_write(plan)
            log_event("SCENE_PLAN", f"scene_plan.json salvo ({self.project_name}, {len(plan.get('scenes', []))} cenas)", level="info")
            return True

    def _mutar(self, transformar: Callable[[dict], None]) -> bool:
        """Carrega, aplica transformação, valida e salva (rollback = não gravar)."""
        with self._lock:
            plan = self.load() or nova_scene_plan(self.project_name, self.project_name)
            try:
                transformar(plan)
            except Exception as e:
                log_event("SCENE_PLAN", f"erro ao atualizar scene_plan: {e}", level="error")
                return False
            erros = validar_scene_plan(plan)
            if erros:
                log_event("SCENE_PLAN", f"scene_plan inválido após atualização: {erros[:3]}", level="error")
                return False
            return self.save(plan, validate=False)

    @staticmethod
    def _localizar(plan: dict, scene_id) -> Optional[dict]:
        sid = str(scene_id)
        for sc in plan.get("scenes", []):
            if str(sc.get("id")) == sid:
                return sc
        return None

    # ---------------- updates ----------------

    def upsert_scene(self, scene: dict) -> bool:
        """Adiciona ou substitui uma cena preservando as demais."""
        erros_cena = validar_scene(scene)
        if erros_cena:
            log_event("SCENE_PLAN", f"upsert_scene: cena inválida — {erros_cena[:3]}", level="error")
            return False

        def fn(plan):
            sid = str(scene.get("id"))
            for i, sc in enumerate(plan.get("scenes", [])):
                if str(sc.get("id")) == sid:
                    plan["scenes"][i] = scene
                    return
            plan["scenes"].append(scene)

        return self._mutar(fn)

    def update_scene(self, scene_id, **fields) -> bool:
        allowed = {"id", "temporal", "narration", "visual_plan", "locks",
                   "media_plan", "selected_media", "prompt", "status"}
        if not fields or not set(fields) <= allowed:
            return False

        def fn(plan):
            sc = self._localizar(plan, scene_id)
            if sc is None:
                raise KeyError(f"cena {scene_id} não encontrada")
            for k, v in fields.items():
                sc[k] = v

        return self._mutar(fn)

    def update_visual_plan(self, scene_id, visual_plan: dict) -> bool:
        return self.update_scene(scene_id, visual_plan=visual_plan)

    def update_locks(self, scene_id, locks: dict) -> bool:
        return self.update_scene(scene_id, locks=locks)

    def update_prompt(self, scene_id, text: str, generated_at=None) -> bool:
        ts = generated_at or datetime.now().isoformat(sep=" ", timespec="seconds")
        return self.update_scene(scene_id, prompt={"text": str(text or ""), "generated_at": ts})

    def update_media_plan(self, scene_id, media_plan: dict) -> bool:
        return self.update_scene(scene_id, media_plan=media_plan)

    def set_prompt(self, scene_id, resultado: dict) -> bool:
        """Persiste o resultado estruturado do PromptEngine em scene.prompt.

        PromptEngine NÃO escreve em disco: o SceneStore é o único autorizado.
        """
        from services.prompt_engine import PROMPT_ENGINE_NAME, PROMPT_ENGINE_VERSION
        meta = resultado.get("metadata") or {}
        versao = meta.get("version") or PROMPT_ENGINE_VERSION
        engine = meta.get("engine") or PROMPT_ENGINE_NAME
        image = resultado.get("image_prompt", "") or ""
        prompt = {
            "engine": engine,
            "version": versao,
            "text": image,
            "image_prompt": image,
            "animation_prompt": resultado.get("animation_prompt", "") or "",
            "negative_prompt": resultado.get("negative_prompt", "") or "",
            "generated_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        }
        return self.update_scene(scene_id, prompt=prompt)

    def set_selected_media(self, scene_id, media_items: list) -> bool:
        if not isinstance(media_items, list):
            return False
        return self.update_scene(scene_id, selected_media=media_items)

    def update_status(self, scene_id, stage: str, value: str) -> bool:
        """Atualiza UM estágio de status (planning/prompt/media/render) de forma independente."""
        if stage not in STATUS_STAGES or value not in STATUS_VALUES:
            return False

        def fn(plan):
            sc = self._localizar(plan, scene_id)
            if sc is None:
                raise KeyError(f"cena {scene_id} não encontrada")
            sc.setdefault("status", {})[stage] = value

        return self._mutar(fn)

    def set_project_info(self, project_id=None, title=None) -> bool:
        def fn(plan):
            if project_id:
                plan["project"]["id"] = str(project_id)
            if title:
                plan["project"]["title"] = str(title)

        return self._mutar(fn)

    def set_visual_profile(self, profile_dict: dict) -> bool:
        if not isinstance(profile_dict, dict):
            return False
        return self._mutar(lambda plan: plan.update(visual_profile=profile_dict))

    # ---------------- construção / compatibilidade ----------------

    @staticmethod
    def criar_scenes_de_cenas(cenas: list) -> list:
        """Cria scenes (skeleton canônico) a partir de itens de cenas.json."""
        scenes = []
        for c in cenas:
            sid = c.get("id", len(scenes) + 1)
            start = c.get("start_time", 0) or 0
            end = c.get("end_time")
            if end is None:
                end = start + 5.0
            ts = c.get("timestamps") or []
            sc = nova_scene(f"scene_{int(sid):03d}", start, end,
                            c.get("texto", ""), ts[0] if ts else "")
            sc["narration"]["previous_context"] = c.get("previous_context", "")
            sc["narration"]["next_context"] = c.get("next_context", "")
            scenes.append(sc)
        return scenes

    def build_from_legacy(self, cenas: list, storyboard: list = None,
                          midias: list = None) -> dict:
        """Monta um scene_plan a partir dos artefatos legados (sem destruí-los)."""
        scenes = self.criar_scenes_de_cenas(cenas or [])
        sb = {}
        for item in storyboard or []:
            sb[str(item.get("id", item.get("scene_id", 0)))] = item
        md = {}
        for m in midias or []:
            md[str(m.get("scene_id", 0))] = m

        for sc in scenes:
            num = str(int(str(sc["id"]).split("_")[-1]))
            item = sb.get(num, {})
            if item:
                sc["visual_plan"] = {
                    "visual_intent": item.get("visual_intent", ""),
                    "subject": item.get("subject", ""),
                    "action": item.get("action", ""),
                    "environment": item.get("environment", ""),
                    "shot": item.get("shot_type", ""),
                    "camera": "",
                    "lighting": "",
                    "composition": "",
                    "mood": item.get("emotion") or item.get("energy", ""),
                    "continuity": "",
                }
                q = item.get("search_queries") or item.get("primary_queries") or []
                fq = item.get("fallback_queries") or []
                syn = item.get("synonyms") or []
                sc["media_plan"] = {
                    "primary_queries": list(q),
                    "fallback_queries": list(fq),
                    "synonyms": list(syn),
                }
                sc["status"]["planning"] = "ready"
            m = md.get(num, {})
            if m and m.get("success") and m.get("arquivo"):
                sc["selected_media"] = [{
                    "arquivo": m.get("arquivo", ""),
                    "media_type": m.get("media_type", ""),
                    "quality": m.get("quality", ""),
                    "origem": m.get("origem_midia", "api"),
                }]
                sc["status"]["media"] = "ready"

        plan = nova_scene_plan(self.project_name, self.project_name)
        plan["scenes"] = scenes
        return plan


