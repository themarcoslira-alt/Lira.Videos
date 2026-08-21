"""
scene_media_type.py — Lira Studio
==================================
Módulo compartilhado para determinação do tipo de mídia ("video" | "photo") por cena,
com base na sobreposição de tempo com os beats do storyboard.
"""

import json
from pathlib import Path
from config import PROJETOS_DIR


def obter_tipo_media_por_cena(projeto: str) -> dict[int, str]:
    """Mapeia cena (cenas.json id) -> 'video'|'photo' usando o Storyboard Builder.

    O Storyboard Builder marca CADA cena (beats) com `media_type`. Como os beats
    são sub-divisões das frases, o mapeamento é por TEMPO: cada cena recebe o tipo
    DOMINANTE dos beats dentro do seu intervalo [start_time, end_time). Se o
    storyboard (beats) não existir, ele é construído sob demanda para projetos manuais.
    """
    project_dir = PROJETOS_DIR / projeto
    cenas_file = project_dir / "cenas.json"
    if not cenas_file.exists():
        return {}
    try:
        cenas = json.loads(cenas_file.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not cenas or not isinstance(cenas, list):
        return {}

    from services.storyboard_builder import carregar_storyboard, construir_storyboard

    beats = carregar_storyboard(projeto)

    modo = "automatico"
    meta_file = project_dir / "meta.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            modo = meta.get("modo_execucao", "automatico")
        except Exception:
            pass

    if not beats and modo == "manual":
        try:
            construir_storyboard(projeto)
        except Exception:
            pass
        beats = carregar_storyboard(projeto)

    if beats:
        mapeamento = {}
        for c in cenas:
            cid = c.get("id")
            if cid is None:
                continue
            try:
                ini = float(c.get("start_time") or c.get("start") or 0)
            except (TypeError, ValueError):
                ini = 0.0
            try:
                fim = float(c.get("end_time") or c.get("end") or ini + 5.0)
            except (TypeError, ValueError):
                fim = ini + 5.0
            votos = []
            for b in beats:
                try:
                    bs = float(b.get("start_sec") or 0)
                except (TypeError, ValueError):
                    continue
                if bs >= ini - 0.5 and bs < fim + 0.5:
                    votos.append(b.get("media_type"))
            if votos:
                v = sum(1 for x in votos if x == "video")
                p = len(votos) - v
                mapeamento[int(cid)] = "video" if v >= p else "photo"
            else:
                mapeamento[int(cid)] = "video"
        return mapeamento

    # Fallback: storyboard legado (b-roll via Claude) com media_preference por cena
    mapeamento = {}
    try:
        sb = json.loads((project_dir / "storyboard.json").read_text(encoding="utf-8"))
        for s in sb:
            if isinstance(s, dict) and s.get("id") is not None:
                mapeamento[int(s["id"])] = ("video" if s.get("media_preference") == "video"
                                            else "photo")
    except Exception:
        pass
    return mapeamento
