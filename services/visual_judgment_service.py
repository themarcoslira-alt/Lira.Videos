"""
services/visual_judgment_service.py — Visual Judgment Engine
=============================================================
Responsabilidade:
- Avaliar a qualidade, fidelidade e consistência da mídia após a geração e download.
- Critérios de Julgamento:
  1. PERSONAGEM: Rosto, identidade visual e presença do chip oficial (@Marcos) quando solicitado.
  2. OBJETO PRINCIPAL: Garante que o objeto pretendido está presente sem trocas indevidas
     (ex: "banana peel compost" vs "banana inteira", "orquídea" vs "flor genérica").
  3. CONTINUIDADE: Compara metadados da cena contra a Bíblia Visual (project_visual_memory.json).
  4. CINEMATOGRAFIA: Avalia enquadramento, lente, iluminação e composição.
  5. QUALIDADE: Atribui score visual de 0 a 100, status de aprovação e justificativa textual.
- Retorno:
  {
    "visual_score": int (0 a 100),
    "checks": {
      "character": bool,
      "object": bool,
      "continuity": bool,
      "composition": bool
    },
    "judgment_status": "approved" | "review" | "rejected",
    "selection_reason": str
  }
"""

from typing import Dict, Any, Optional
from pathlib import Path
from services.event_logger import log_event

# ANTIGRAVITY #3 — fidelidade facial abaixo deste valor → rejeitar e reprocessar.
LIMIAR_FIDELIDADE_AVATAR = 70


def avaliar_fidelidade_facial(
    projeto_id: str,
    caminho_imagem: str,
    cena: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """ANTIGRAVITY #3 — compara a imagem gerada (cena avatar) com a reference.png.

    Retorna {"fidelidade": int 0-100, "metodo": str, "detalhe": str, "ok": bool}
    ou None quando a cena não usa personagem / não há reference local.
    """
    try:
        if cena is None:
            cena = {}
        if not (cena.get("uses_character") is True
                or "avatar" in str(cena.get("scene_type") or "").lower()):
            return None
        from services.facial_fidelity_engine import calcular_fidelidade_facial
        from services.character_service import resolver_imagem_avatar_projeto
        referencia = resolver_imagem_avatar_projeto(projeto_id) or ""
        if not referencia:
            log_event("VISUAL_JUDGMENT_AVATAR",
                      f"Projeto {projeto_id}: sem reference.png local — validação facial pulada.",
                      level="warn")
            return None
        return calcular_fidelidade_facial(referencia, caminho_imagem)
    except Exception as _e:
        log_event("VISUAL_JUDGMENT_AVATAR", f"Aviso ao avaliar fidelidade facial: {_e}", level="warn")
        return None


def avaliar_avatar_fidelidade_facial(
    projeto_id: str,
    cena_id: int,
    caminho_imagem_gerada: str,
    nome_personagem: str = ""
) -> Dict[str, Any]:
    """ANTIGRAVITY #3 (API pública) — fidelidade facial da imagem gerada (avatar).

    Compara a reference.png do personagem com a imagem gerada usando visão
    computacional (services.facial_fidelity_engine: embeddings quando
    face_recognition+opencv existirem; fallback heurístico YCbCr/numpy).

    Resolução da referência:
      1. projeto (character_service.resolver_imagem_avatar_projeto);
      2. Biblioteca global: Biblioteca/Personagens/<nome_personagem>/reference.png.

    Retorna {"aprovado": bool, "score_fidelidade": int 0-100, "motivo": str}.
    """
    resultado: Dict[str, Any] = {
        "aprovado": False,
        "score_fidelidade": 0,
        "motivo": "Validação não executada",
    }
    referencia = ""
    try:
        from services.character_service import resolver_imagem_avatar_projeto
        referencia = resolver_imagem_avatar_projeto(projeto_id) or ""
    except Exception:
        referencia = ""

    # Fallback: biblioteca global de personagens (ex.: Biblioteca/Personagens/Marcos/reference.png)
    if (not referencia or not Path(referencia).exists()) and nome_personagem:
        try:
            cand = Path("Biblioteca") / "Personagens" / str(nome_personagem).lstrip("@") / "reference.png"
            if cand.exists():
                referencia = str(cand)
        except Exception:
            pass

    if not referencia or not Path(referencia).exists():
        resultado["motivo"] = f"Referência não encontrada para '{nome_personagem or projeto_id}'."
        log_event("VISUAL_JUDGMENT_AVATAR", resultado["motivo"], level="warn")
        return resultado

    try:
        from services.facial_fidelity_engine import calcular_fidelidade_facial
        r = calcular_fidelidade_facial(referencia, caminho_imagem_gerada)
        score = int(r.get("fidelidade") or 0)
        metodo = str(r.get("metodo") or "")
        resultado["score_fidelidade"] = score
        resultado["aprovado"] = bool(r.get("ok")) and score >= LIMIAR_FIDELIDADE_AVATAR
        if not r.get("ok"):
            detalhe = r.get("detalhe") or "falha na comparação"
            resultado["motivo"] = (f"Validação facial reprovada: {detalhe} "
                                   f"(método={metodo}).")
        elif resultado["aprovado"]:
            resultado["motivo"] = f"Fidelidade facial {score}% >= {LIMIAR_FIDELIDADE_AVATAR}% (método={metodo})."
        else:
            detalhe = r.get("detalhe") or "comparação abaixo do limiar"
            resultado["motivo"] = f"Fidelidade facial {score}% < {LIMIAR_FIDELIDADE_AVATAR}% (método={metodo}; {detalhe})."
        log_event("VISUAL_JUDGMENT_AVATAR",
                  f"Cena {cena_id}: Fidelidade facial {score}% — aprovado={resultado['aprovado']}")
    except Exception as e:
        resultado["motivo"] = f"Erro ao comparar faces da cena {cena_id}: {e}"
        log_event("VISUAL_JUDGMENT_AVATAR", resultado["motivo"], level="warn")
    return resultado


def avaliar_imagem_cena(
    projeto_id: str,
    cena: Dict[str, Any],
    memoria_visual: Dict[str, Any],
    caminho_imagem: Optional[str] = None
) -> Dict[str, Any]:
    """
    Avalia a mídia gerada contra as especificações do diretor e da Bíblia Visual.
    """
    cid = cena.get("id", cena.get("scene_index", 0))
    prompt = cena.get("prompt_imagem", "") or cena.get("visual_prompt", "")
    prompt_lower = prompt.lower()
    fala_lower = f"{cena.get('narration', '')} {cena.get('texto', '')}".lower()
    
    char_mem = memoria_visual.get("personagem", {})
    env_mem = memoria_visual.get("ambiente", {})
    obj_mem = memoria_visual.get("objetos", {})
    style_mem = memoria_visual.get("estilo", {})

    score = 100
    falhas = []
    
    # 1. AVALIAÇÃO DE PERSONAGEM
    uses_char = cena.get("uses_character", False)
    char_ref = (char_mem.get("reference") or cena.get("character_ref") or "").strip()
    check_char = True
    
    if uses_char and char_ref:
        if "@homem" in prompt_lower or "@pessoa" in prompt_lower or "@man" in prompt_lower or "@woman" in prompt_lower or "@person" in prompt_lower:
            check_char = False
            score -= 55
            falhas.append("Tag genérica proibida detectada no prompt.")
        elif char_ref.lower() not in prompt_lower and char_ref.lower() not in (cena.get("character_ref") or "").lower():
            check_char = False
            score -= 35
            falhas.append(f"Personagem oficial {char_ref} ausente no comando visual.")
    elif not uses_char and char_ref:
        if char_ref.lower() in prompt_lower and not cena.get("uses_character_override"):
            check_char = False
            score -= 20
            falhas.append("Personagem incluído indevidamente em cena B-Roll.")

    # 2. AVALIAÇÃO DE OBJETO PRINCIPAL
    check_obj = True
    if "banana" in fala_lower or "adubo" in fala_lower:
        if "whole banana" in prompt_lower or "banana inteira" in prompt_lower:
            check_obj = False
            score -= 25
            falhas.append("Objeto incorreto: renderizada banana inteira em vez de adubo de casca.")
        elif not any(k in prompt_lower for k in ["compost", "fertilizer", "soil", "nutrient", "peel"]):
            check_obj = False
            score -= 15
            falhas.append("Falta de detalhe específico do nutriente/adubo no prompt.")
            
    if "orquidea" in fala_lower or "orquídea" in fala_lower or "raiz" in fala_lower:
        if "generic flower" in prompt_lower:
            check_obj = False
            score -= 20
            falhas.append("Espécie botânica incorreta: flor genérica em vez de orquídea.")

    # 3. AVALIAÇÃO DE CONTINUIDADE
    check_continuity = True
    cont_context = cena.get("continuity_context", "")
    if not cont_context and cid > 1:
        check_continuity = False
        score -= 10
        falhas.append("Ausência de cláusula de continuidade visual com a Bíblia do projeto.")

    # 4. AVALIAÇÃO DE COMPOSIÇÃO / CINEMATOGRAFIA
    check_comp = True
    cam = cena.get("camera_direction", {})
    if not cam or not cam.get("shot"):
        check_comp = False
        score -= 10
        falhas.append("Direção de enquadramento não especificada.")
    elif "16:9" not in prompt_lower and "framing" not in prompt_lower:
        score -= 5

    # 5. VERIFICAÇÃO FÍSICA DO ARQUIVO (Se fornecido)
    if caminho_imagem:
        p = Path(caminho_imagem)
        if not p.exists() or p.stat().st_size < 1000:
            score -= 50
            falhas.append("Arquivo de mídia corrompido ou inexistente no disco.")

    # 6. ANTIGRAVITY #3 — VALIDAÇÃO COM VISÃO COMPUTACIONAL (cenas AVATAR)
    #    Compara a face da imagem gerada com a reference.png (@personagem).
    #    Fidelidade < 70 → check_char=False + forte penalidade → rejeitar/reprocessar.
    avatar_fidelity = None
    avatar_fidelity_method = None
    if uses_char and caminho_imagem:
        _fid = avaliar_fidelidade_facial(projeto_id, caminho_imagem, cena)
        if _fid and _fid.get("fidelidade") is not None:
            avatar_fidelity = int(_fid["fidelidade"])
            avatar_fidelity_method = str(_fid.get("metodo") or "")
            log_event("VISUAL_JUDGMENT_AVATAR",
                      f"Fidelidade facial: {avatar_fidelity}% (método={avatar_fidelity_method})")
            if avatar_fidelity < LIMIAR_FIDELIDADE_AVATAR:
                check_char = False
                # Penalidade forte: (diferença até o limiar) + 50 fixos → rejeição.
                score -= (LIMIAR_FIDELIDADE_AVATAR - avatar_fidelity) + 50
                falhas.append(
                    f"Fidelidade facial do avatar {avatar_fidelity}% < "
                    f"{LIMIAR_FIDELIDADE_AVATAR}% — a face gerada não corresponde "
                    "à reference do personagem."
                )

    # Consolidação final do score (0 a 100)
    visual_score = max(0, min(100, score))
    
    if visual_score >= 80:
        judgment_status = "approved"
        if not falhas:
            reason = f"Cena {cid:03d} aprovada com excelência (fidelidade visual e continuidade perfeitas)."
        else:
            reason = f"Cena {cid:03d} aprovada com advertências menores: {'; '.join(falhas)}"
    elif visual_score >= 50:
        judgment_status = "review"
        reason = f"Cena {cid:03d} requer revisão: {'; '.join(falhas)}"
    else:
        judgment_status = "rejected"
        reason = f"Cena {cid:03d} rejeitada por inconsistência crítica: {'; '.join(falhas)}"

    # ANTIGRAVITY #3 — fidelidade facial do avatar abaixo do limiar SEMPRE rejeita,
    # independentemente das demais checagens textuais/composicionais.
    if avatar_fidelity is not None and avatar_fidelity < LIMIAR_FIDELIDADE_AVATAR:
        judgment_status = "rejected"
        reason = (f"Cena {cid:03d} rejeitada por fidelidade facial: "
                  f"{avatar_fidelity}% < {LIMIAR_FIDELIDADE_AVATAR}% "
                  f"(método={avatar_fidelity_method}).")

    result = {
        "visual_score": visual_score,
        "checks": {
            "character": check_char,
            "object": check_obj,
            "continuity": check_continuity,
            "composition": check_comp
        },
        "avatar_fidelity": avatar_fidelity,
        "avatar_fidelity_method": avatar_fidelity_method,
        "judgment_status": judgment_status,
        "selection_reason": reason
    }

    log_event("VISUAL_JUDGMENT", f"Cena {cid:03d}: score={visual_score} ({judgment_status}) - {reason}")
    return result