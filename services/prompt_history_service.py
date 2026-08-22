"""
services/prompt_history_service.py — Prompt & Decision History System
======================================================================
Responsabilidade:
- Gravar em formato legível (.txt) todas as decisões visuais e técnicas tomadas para cada cena.
- Pasta: projetos/<projeto_id>/prompt_history/scene_XXX.txt
- Registra:
  * CENA (001, 002...)
  * STORY ROLE (hook, problem, demonstration...)
  * SCENE TYPE (avatar_talking, broll_macro, hybrid...)
  * CHARACTER (@Marcos ou sem personagem)
  * EMOTION (curiosity, trust, urgency...)
  * CAMERA (enquadramento e lente)
  * CONTINUITY (regras e âncoras aplicadas)
  * VISUAL DECISION (por que a cena existe narrativamente)
  * PROMPT GENERATED (o prompt final completo)
  * RESULT (image_path, visual_score, judgment_status, selection_reason)
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional
from config import PROJETOS_DIR
from services.event_logger import log_event


def _get_history_dir(projeto_id: str) -> Path:
    d = PROJETOS_DIR / projeto_id / "prompt_history"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _history_file_path(projeto_id: str, cid: int) -> Path:
    return _get_history_dir(projeto_id) / f"scene_{cid:03d}.txt"


def formatar_texto_historico_cena(
    cena: Dict[str, Any],
    memoria_visual: Optional[Dict[str, Any]] = None,
    image_path: str = "",
    visual_score: Optional[int] = None,
    judgment_status: str = "",
    selection_reason: str = ""
) -> str:
    """
    Formata o conteúdo textual padronizado do histórico de decisão da cena.
    """
    cid = cena.get("id", cena.get("scene_index", 1))
    story_role = cena.get("story_role", "explanation")
    scene_type = cena.get("scene_type", "broll_macro")
    uses_char = cena.get("uses_character", False)
    char_ref = cena.get("character_ref", "") if uses_char else "N/A (B-Roll puro / sem apresentador)"
    emotion = cena.get("emotion", "curiosity")
    
    cam = cena.get("camera_direction", {})
    shot = cam.get("shot", "medium shot")
    lens = cam.get("lens", "35mm")
    camera_str = f"{lens} {shot}".strip() if lens else shot
    
    # Continuidade
    char_mem = (memoria_visual or {}).get("personagem", {})
    env_mem = (memoria_visual or {}).get("ambiente", {})
    clothing = char_mem.get("clothing", "Signature olive green gardening shirt")
    world = env_mem.get("location", "Rustic botanical garden environment")
    
    continuity_lines = []
    if uses_char:
        continuity_lines.append(f"{char_ref} wearing {clothing}")
    continuity_lines.append(world)
    if cena.get("continuity_context"):
        continuity_lines.append(cena["continuity_context"])
    continuity_str = "\n".join(continuity_lines)
    
    narrative_purpose = cena.get("narrative_purpose", "Create engagement in the narrative")
    prompt_gen = cena.get("prompt_imagem") or cena.get("visual_prompt", "")
    
    # Resultados (preserva se já passados ou do dict da cena)
    img_p = image_path or cena.get("arquivo_midia", "") or "Pendente de geração"
    v_score = visual_score if visual_score is not None else cena.get("visual_score", "Pendente")
    j_status = judgment_status or cena.get("judgment_status", "pending")
    s_reason = selection_reason or cena.get("selection_reason", "Aguardando geração da imagem")
    
    conteudo = f"""CENA:
{cid:03d}

STORY ROLE:
{story_role}

SCENE TYPE:
{scene_type}

CHARACTER:
{char_ref}

EMOTION:
{emotion}

CAMERA:
{camera_str}

CONTINUITY:
{continuity_str}

VISUAL DECISION:
Why this scene exists:
{narrative_purpose}

PROMPT GENERATED:
{prompt_gen}

RESULT:
image_path: {img_p}
visual_score: {v_score}
judgment_status: {j_status}
selection_reason: {s_reason}
"""
    return conteudo.strip() + "\n"


def registrar_historico_prompt_cena(
    projeto_id: str,
    cena: Dict[str, Any],
    memoria_visual: Optional[Dict[str, Any]] = None
) -> str:
    """
    Grava ou atualiza o arquivo scene_XXX.txt na pasta prompt_history do projeto.
    Retorna o caminho relativo do arquivo.
    """
    cid = int(cena.get("id", cena.get("scene_index", 1)))
    p_file = _history_file_path(projeto_id, cid)
    
    texto = formatar_texto_historico_cena(cena, memoria_visual)
    p_file.write_text(texto, encoding="utf-8")
    
    rel_path = f"prompt_history/scene_{cid:03d}.txt"
    log_event("PROMPT_HISTORY", f"{projeto_id}: Histórico gravado em {rel_path}")
    return rel_path


def atualizar_historico_resultado_cena(
    projeto_id: str,
    cid: int,
    image_path: str = "",
    visual_score: int = 100,
    judgment_status: str = "approved",
    selection_reason: str = ""
) -> bool:
    """
    Atualiza o bloco RESULT do arquivo scene_XXX.txt após a mídia ser baixada e avaliada.
    """
    p_file = _history_file_path(projeto_id, cid)
    if not p_file.exists():
        return False
        
    try:
        linhas = p_file.read_text(encoding="utf-8").splitlines()
        novas_linhas = []
        in_result = False
        
        for l in linhas:
            if l.strip() == "RESULT:":
                in_result = True
                novas_linhas.append(l)
                novas_linhas.append(f"image_path: {image_path}")
                novas_linhas.append(f"visual_score: {visual_score}")
                novas_linhas.append(f"judgment_status: {judgment_status}")
                novas_linhas.append(f"selection_reason: {selection_reason}")
                break
            if not in_result:
                novas_linhas.append(l)
                
        p_file.write_text("\n".join(novas_linhas) + "\n", encoding="utf-8")
        log_event("PROMPT_HISTORY", f"{projeto_id}: Resultado atualizado em scene_{cid:03d}.txt")
        return True
    except Exception as e:
        log_event("PROMPT_HISTORY", f"{projeto_id}: Erro ao atualizar resultado em scene_{cid:03d}.txt: {e}", level="warn")
        return False
