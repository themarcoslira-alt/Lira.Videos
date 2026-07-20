"""
query_generator.py — Geração de queries de busca de mídia
"""
import json
from config import PROJETOS_DIR


def _generate_15_candidates(storyboard_item: dict) -> list:
    """
    Gera até 15 candidatos de query baseado na análise do storyboard.
    Retorna lista de dicts com: query, media_type, score, source
    score = 1.0 (melhor) a 0.1 (pior) — só para ordenar, não para aceitar/rejeitar
    """
    keywords = storyboard_item.get("keywords", [])
    scene_type = storyboard_item.get("scene_type", "explicacao")
    preference = storyboard_item.get("media_preference", "video")
    texto = storyboard_item.get("texto", "")

    candidates = []
    score = 1.0

    # Preferência principal
    candidates.append({
        "query": " ".join(keywords[:3]),
        "media_type": preference,
        "score": score,
        "source": "preference"
    })
    score -= 0.1

    # Variações combinando keywords
    if len(keywords) >= 2:
        for i in range(min(3, len(keywords) - 1)):
            candidates.append({
                "query": f"{keywords[i]} {keywords[i+1]}",
                "media_type": preference,
                "score": score,
                "source": "combo"
            })
            score -= 0.05

    # Se preferência é video, adiciona variações de foto
    if preference == "video":
        candidates.append({
            "query": " ".join(keywords[:2]),
            "media_type": "photo",
            "score": score,
            "source": "photo_fallback"
        })
        score -= 0.05

        if len(keywords) >= 2:
            candidates.append({
                "query": f"{keywords[0]} {keywords[1]}",
                "media_type": "photo",
                "score": score,
                "source": "photo_combo"
            })
            score -= 0.05

    # Variações por scene_type
    scene_variations = {
        "introducao": ["startup", "beginning", "opening"],
        "explicacao": ["explanation", "concept", "information"],
        "exemplo": ["example", "demonstration", "sample"],
        "demonstracao": ["demonstration", "showing", "display"],
        "comparacao": ["comparison", "versus", "contrast"],
        "conclusao": ["conclusion", "summary", "ending"]
    }

    if scene_type in scene_variations:
        for var in scene_variations[scene_type][:3]:
            candidates.append({
                "query": var,
                "media_type": preference,
                "score": score,
                "source": f"scene_type_{scene_type}"
            })
            score -= 0.05

    # Adiciona keyword única como fallback
    for kw in keywords[:3]:
        candidates.append({
            "query": kw,
            "media_type": "video",
            "score": score,
            "source": "single_keyword"
        })
        score -= 0.05

    # Limita a 15 candidatos
    return candidates[:15]


def gerar_queries(project_name: str) -> dict:
    """
    Gera queries para todas as cenas do storyboard.
    """
    project_dir = PROJETOS_DIR / project_name
    storyboard_file = project_dir / "storyboard.json"
    queries_file = project_dir / "queries.json"

    if not storyboard_file.exists():
        return {"success": False, "error": "storyboard.json não encontrado"}

    with open(storyboard_file, "r", encoding="utf-8") as f:
        storyboard = json.load(f)

    all_queries = []
    for item in storyboard:
        candidates = _generate_15_candidates(item)
        all_queries.append({
            "scene_id": item["id"],
            "texto": item.get("texto", ""),
            "keywords": item.get("keywords", []),
            "candidates": candidates
        })

    with open(queries_file, "w", encoding="utf-8") as f:
        json.dump(all_queries, f, indent=2, ensure_ascii=False)

    total_candidates = sum(len(q["candidates"]) for q in all_queries)
    return {
        "success": True,
        "project": project_name,
        "cenas": len(all_queries),
        "total_candidates": total_candidates,
        "queries": all_queries
    }