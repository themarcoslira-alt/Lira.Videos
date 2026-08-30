"""
services/avatar_decision_service.py — Avatar vs B-roll Decision (Lira Studio Fase 1)
====================================================================================
Decide, para uma cena já classificada narrativamente, se ela deve ser produzida
com AVATAR (Google Flow/Elton Flow, apresenta @Marcos) ou com B-ROLL (Pexels,
gratuito).

Regra de negócio:
  HOOK    -> avatar (sempre)
  AVATAR  -> avatar (sempre)
  BROLL   -> b-roll (sempre)
  CTA     -> avatar (sempre)
  CLOSING -> avatar (sempre)

Reutiliza services/character_service.py para validar/obter o personagem do
projeto (identidade.json / characters/<Nome>/reference.png) — NÃO duplica lógica.
"""

from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from config import PROJETOS_DIR

# ---------------------------------------------------------------------------
# Falas por papel (usadas no "prompt" quando a cena não é fornecida)
# ---------------------------------------------------------------------------

_ROLE_AVATAR_PROMPTS = {
    "HOOK": "Apresentador abrindo o vídeo, saudando o público e capturando a "
            "atenção olhando direto para a câmera",
    "AVATAR": "Apresentador em ponto de transição estratégica, falando diretamente "
              "com o público olhando para a câmera",
    "TRANSITION": "Apresentador reengajando o público e introduzindo o próximo bloco "
                  "narrativo olhando para a câmera",
    "CTA": "Apresentador convidando o público a curtir, comentar e se inscrever, "
           "olhando direto para a câmera",
    "CLOSING": "Apresentador se despedindo de forma calorosa, agradecendo e "
               "indicando o próximo vídeo",
}

_COST_TABLE = {
    "avatar": 0.15,   # custo médio de geração de avatar no Google Flow (créditos)
    "broll": 0.0,     # Pexels é gratuito
}


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _normalizar(personagem_id) -> str:
    return str(personagem_id or "").strip().lstrip("@").strip()


def _nome_candidato(p: dict) -> str:
    for k in ("nome", "name", "referencia_flow", "arquivo_flow"):
        v = p.get(k) or ""
        if isinstance(v, str) and v.strip():
            return v.strip().lstrip("@").strip()
    return ""


def _personagens_do_projeto(projeto_id: str) -> list:
    """Reúne entidades do personagem do projeto (identidade + personas locais)."""
    import services.character_service as character_svc
    entidades: list = []
    if projeto_id:
        idt = character_svc.obter_identidade_projeto(projeto_id)
        if idt:
            entidades.extend([idt] + (idt.get("personagens") or []))
        for p in character_svc.listar_personagens(projeto_id):
            entidades.append(p)
    return entidades


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def decide_avatar_or_broll(
    narrative_role: str,
    scene: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """Retorna (tipo, valor) onde tipo é 'avatar'|'broll'.

    - avatar: valor = prompt de geração do avatar (Fase 2 usa @personagem)
    - broll : valor = query Pexels (ou query genérica se a cena não for dada)
    """
    role = (narrative_role or "").upper()
    if role == "BROLL":
        query = _query_broll(scene)
        return ("broll", query)
    prompt = _prompt_avatar(role, scene)
    return ("avatar", prompt)


def _prompt_avatar(role: str, scene: Optional[Dict[str, Any]]) -> str:
    base = _ROLE_AVATAR_PROMPTS.get(role, _ROLE_AVATAR_PROMPTS["AVATAR"])
    if scene:
        texto = str(scene.get("texto") or scene.get("narration") or scene.get("text") or "").strip()
        if texto:
            limpo = " ".join(texto.split())
            return f'{base} — "{limpo}"'
    return base


def _query_broll(scene: Optional[Dict[str, Any]]) -> str:
    if not scene:
        return "garden nature macro"
    from services.enhanced_scene_classifier import get_broll_query
    texto = str(scene.get("texto") or scene.get("narration") or scene.get("text") or "")
    st = str(scene.get("scene_type") or "")
    return get_broll_query(texto, st) or "garden nature macro"


def validate_personagem_exists(
    personagem_id: str,
    projeto_id: str = "",
) -> bool:
    """Verifica se o personagem foi criado no Flow / projeto.

    True se a referência aparecer em identidade.json, na pasta
    characters/<Nome>/ (com conteúdo) ou na biblioteca global.
    """
    nome = _normalizar(personagem_id)
    if not nome:
        return False

    if projeto_id:
        for p in _personagens_do_projeto(projeto_id):
            if _nome_candidato(p).lower() == nome.lower():
                return True
        char_dir = PROJETOS_DIR / projeto_id / "characters" / nome
        if char_dir.exists() and any(char_dir.iterdir()):
            return True

    import services.character_service as character_svc
    for p in character_svc.listar_biblioteca_personagens():
        if _nome_candidato(p).lower() == nome.lower():
            return True
    return False


def get_personagem_reference(
    personagem_id: str,
    projeto_id: str = "",
) -> str:
    """Retorna o caminho do reference.png do personagem (ou "" se não existir)."""
    nome = _normalizar(personagem_id)
    if not nome:
        return ""

    if projeto_id:
        for p in _personagens_do_projeto(projeto_id):
            if _nome_candidato(p).lower() != nome.lower():
                continue
            abs_img = p.get("imagem_abs") or p.get("reference_image_abs") or ""
            if abs_img and Path(abs_img).exists():
                return str(Path(abs_img))
            rel = p.get("imagem") or p.get("reference_image") or ""
            if rel:
                cand = PROJETOS_DIR / projeto_id / rel
                if cand.exists():
                    return str(cand)
            break
        cand = PROJETOS_DIR / projeto_id / "characters" / nome / "reference.png"
        if cand.exists():
            return str(cand)

    import services.character_service as character_svc
    for p in character_svc.listar_biblioteca_personagens():
        if _nome_candidato(p).lower() != nome.lower():
            continue
        for k in ("reference_image_abs", "imagem_abs", "reference_image", "imagem"):
            v = p.get(k) or ""
            if not v:
                continue
            vp = Path(v)
            if vp.is_absolute() and vp.exists():
                return str(vp)
            if projeto_id:
                relp = PROJETOS_DIR / projeto_id / v
                if relp.exists():
                    return str(relp)
        break
    return ""


def estimate_cost(
    narrative_role: str,
    personagem_id: str = "",
    projeto_id: str = "",
) -> Dict[str, Any]:
    """Estimativa de custo de produção da cena.

    - avatar (HOOK/AVATAR/CTA/CLOSING): custo de créditos no Google Flow.
      Se o personagem não existir, o custo estimado sobe (precisa criar).
    - broll (BROLL): gratuito (Pexels).
    """
    role = (narrative_role or "").upper()
    if role == "BROLL":
        return {
            "type": "broll",
            "cost_estimate": _COST_TABLE["broll"],
            "currency": "credits",
            "source": "pexels",
            "requires_character_creation": False,
            "label": "Gratuito (Pexels)",
        }
    existe = validate_personagem_exists(personagem_id, projeto_id) if personagem_id else True
    return {
        "type": "avatar",
        "cost_estimate": _COST_TABLE["avatar"] if existe else 0.45,
        "currency": "credits",
        "source": "google_flow",
        "requires_character_creation": not existe,
        "label": "Google Flow (avatar)",
    }