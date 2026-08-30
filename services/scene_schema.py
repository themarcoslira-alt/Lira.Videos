"""
services/scene_schema.py — Schema do Ciclo Narrativo (Lira Studio v0.3.5+)
=========================================================================
ADITIVO ao scene_plan_schema.py (Fase 0). Define os campos do CICLO
Avatar → Imagem → Vídeo por cena e a validação de integridade:

    tipo_cena:       enum["avatar_intro", "imagem_zoom", "video_acao"]
    efeito:          enum["zoom_in", "fade", "pan", "none"]
    posicao_ciclo:   int  (1=avatar, 2=imagem, 3=vídeo)
    duracao_planejada: float (segundos)
    ciclo_numero:    int  (qual ciclo o bloco pertence)

Garantias ao validar um plano:
  - tipo_cena nunca é None/vazio;
  - posicao_ciclo respeita a sequência 1→2→3→1→2→3... (módulo 3);
  - nunca há 2 avatar_intro consecutivos.
"""

from typing import Dict, List, Any

# Enums canônicos (espelham config.NARRATIVE_CYCLE_*)
CYCLE_TIPOS = ("avatar_intro", "imagem_zoom", "video_acao")
VALID_EFEITOS = ("zoom_in", "fade", "pan", "none")
VALID_POSICOES = (1, 2, 3)

# Durações planejadas padrão (segundos) — alinhadas a config
DEFAULT_DURACOES = {
    "avatar_intro": 7.0,
    "imagem_zoom": 12.0,
    "video_acao": 8.0,
}

# Campos do ciclo adicionados a cada cena
CYCLE_FIELDS = ("tipo_cena", "efeito", "posicao_ciclo",
                "duracao_planejada", "ciclo_numero")


def nova_cena_ciclo(
    scene_id: int,
    posicao_ciclo: int,
    ciclo_numero: int,
    texto: str = "",
    efeito: str = "",
    duracao_planejada: float = 0.0,
) -> Dict[str, Any]:
    """Cria um dict de cena com os campos do ciclo preenchidos.

    posicao_ciclo: 1=avatar_intro, 2=imagem_zoom, 3=video_acao.
    """
    posicao_ciclo = int(posicao_ciclo or 1)
    if posicao_ciclo not in VALID_POSICOES:
        posicao_ciclo = 1
    tipo_cena = CYCLE_TIPOS[posicao_ciclo - 1]

    if not efeito:
        efeito = {
            1: "none",
            2: "zoom_in",
            3: "fade",
        }.get(posicao_ciclo, "none")

    if not duracao_planejada or duracao_planejada <= 0:
        duracao_planejada = DEFAULT_DURACOES.get(tipo_cena, 8.0)

    return {
        "id": int(scene_id),
        "scene_index": int(scene_id),
        "texto": str(texto or ""),
        "tipo_cena": tipo_cena,
        "efeito": efeito,
        "posicao_ciclo": posicao_ciclo,
        "duracao_planejada": round(float(duracao_planejada), 2),
        "ciclo_numero": int(ciclo_numero),
        "media_type": "photo" if tipo_cena == "imagem_zoom" else "video",
    }


def aplicar_campos_ciclo(cena: Dict[str, Any], posicao_ciclo: int,
                         ciclo_numero: int) -> Dict[str, Any]:
    """Preenche (sem remover nada) os campos do ciclo numa cena existente."""
    if not isinstance(cena, dict):
        return cena
    base = nova_cena_ciclo(
        scene_id=int(cena.get("id") or cena.get("scene_index") or 0),
        posicao_ciclo=posicao_ciclo,
        ciclo_numero=ciclo_numero,
        texto=str(cena.get("texto") or cena.get("narration") or ""),
        efeito=str(cena.get("efeito") or ""),
        duracao_planejada=float(cena.get("duracao_planejada") or 0),
    )
    for k in CYCLE_FIELDS:
        if k not in cena or cena[k] in (None, "", 0):
            cena[k] = base[k]
    if not cena.get("media_type"):
        cena["media_type"] = base["media_type"]
    return cena


def _tipo_valido(tipo) -> bool:
    return bool(tipo) and str(tipo) in CYCLE_TIPOS


def _efeito_valido(efeito) -> bool:
    return bool(efeito) and str(efeito) in VALID_EFEITOS


def validar_ciclo(cenas: List[Dict[str, Any]]) -> List[str]:
    """Valida a sequência do ciclo. Retorna lista de erros (vazia = OK).

    Regras:
      1. tipo_cena != null (obrigatório).
      2. posicao_ciclo segue 1→2→3→1→2→3... (mod 3).
      3. Nunca 2 avatar_intro consecutivos.
    """
    erros: List[str] = []
    if not cenas:
        return erros

    anterior_tipo = None
    for i, cena in enumerate(cenas):
        cid = cena.get("id") or cena.get("scene_index") or i

        # 1. tipo_cena obrigatório e válido
        tipo = cena.get("tipo_cena")
        if not _tipo_valido(tipo):
            erros.append(f"cena {cid}: tipo_cena inválido ou nulo: {tipo!r}")

        # efeito válido (se presente)
        efeito = cena.get("efeito")
        if efeito is not None and not _efeito_valido(efeito):
            erros.append(f"cena {cid}: efeito inválido: {efeito!r} (permitidos: {VALID_EFEITOS})")

        # 2. posicao_ciclo na sequência correta
        pos = cena.get("posicao_ciclo")
        if pos not in VALID_POSICOES:
            erros.append(f"cena {cid}: posicao_ciclo inválida: {pos!r} (permitidas: {VALID_POSICOES})")
            continue
        esperada = (i % 3) + 1
        if int(pos) != esperada:
            erros.append(
                f"cena {cid}: posicao_ciclo {pos} quebra a sequência (esperada {esperada} na posição {i})"
            )

        # 3. nunca 2 avatar_intro consecutivos
        if tipo == "avatar_intro" and anterior_tipo == "avatar_intro":
            erros.append(f"cena {cid}: avatar_intro consecutivo detectado")
        anterior_tipo = tipo

    return erros


def eh_ciclo_valido(cenas: List[Dict[str, Any]]) -> bool:
    """True se o plano respeita o ciclo narrativo (sem erros)."""
    return not validar_ciclo(cenas)
