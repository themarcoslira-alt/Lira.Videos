"""
services/character_decision_service.py — Character Decision System
===================================================================
Responsabilidade:
- Decidir formalmente e sem ambiguidades a vinculação do personagem da cena.
- REGRA ABSOLUTA: Nunca usar @Homem, @Pessoa, @Man ou termos genéricos.
- Ordem de Prioridade Única:
  1. Flow Character ID (ex: ID nativo da biblioteca do Flow)
  2. Referência @Nome (ex: @Marcos)
  3. reference.png (imagem isolada em characters/<Nome>/reference.png)
  4. Upload comum (arquivo original da imagem)
- Se existir personagem bloqueado e a cena requerer sujeito humano:
  Retorna a entidade oficial resolvida.
- Se for B-roll ou não houver personagem cadastrado:
  Retorna uses_character=False e nunca inventa referências.
"""

from typing import Dict, Any, Optional, Tuple
from services.character_service import obter_identidade_projeto, obter_personagem_cena
from services.event_logger import log_event


# ---------------------------------------------------------------------------
# FASE 3.3 — Detecção de apresentador por narrativa
# Prioriza personagem bloqueado quando a fala indica:
#   - primeira pessoa contando experiência
#   - criador do teste / autor do experimento
#   - pessoa explicando um processo
#   - demonstração prática
# ---------------------------------------------------------------------------

_PRONOME_1P = (
    "eu ", " eu", "meu ", "minha ", "meus ", "minhas ", "comigo ", "eu mesmo",
    "eu mesmo ", "eu fiz ", "fiz ", "criei ", "testei ", "experimentei ",
    "descobri ", "aprendi ", "cuidei ", "plantei ", "preparei ", "coloquei ",
    "apliquei ", "usei ", "vi ",
    " i ", " i'm ", " i am ", " my ", " me ", " myself ", " i'll ", " i will ",
    " i've ", " i have ", " i made ", " i created ", " i tested ", " i experimented ",
    " i discovered ", " i learned ", " i planted ", " i prepared ", " i applied ",
    " i used ", " i saw ", " i tried ", " i decided ", " i noticed ", " i observed ",
    " i yanked ", " i pulled ", " i walk you through ", " i show you ", " right now ",
    " while you're watching ", " while you are watching "
)

_PALAVRAS_EXPERIENCIA = (
    "experiência", "experiencia", "experiment", "teste", "testei", "criador",
    "criei", "eu fiz", "fiz um teste", "meu experimento", "descobri",
    "aprendi na prática", "na prática", "fui eu", "eu mesmo",
    "my experiment", "my test", "i discovered", "i learned", "first hand", "in practice",
    "my channel", "on this channel", "i test", "i'm testing", "i tried", "my results",
    "my finding", "i found", "i walked through"
)

_PALAVRAS_PROCESSO_DEMO = (
    "mostro", "demonstro", "como fazer", "passo a passo", "vou mostrar",
    "vou demonstrar", "aplico", "aplicar", "coloco", "segurando",
    "estou mostrando", "na prática", "demonstração", "demonstracao",
    "veja comigo", "vou te mostrar", "deixa eu mostrar", "eu aplico",
    "walk you through", "i'll walk you through", "i will walk you through", "show you",
    "show you exactly", "let me show you", "i'm going to show", "i will show",
    "step by step", "how to make", "how to prepare", "holding", "watch this",
    "look at this", "here is how", "here's how", "i apply", "i demonstrate"
)

# Termos que indicam b-roll PURO (objeto/sem sujeito humano) — NUNCA forçam personagem
_TERMOS_BROLL_PURO = (
    "close na rosa", "close nas rosas", "apenas a planta", "detalhe da casca",
    "macro shot of", "close-up of the", "texture of", "soil texture",
    "no people", "no person", "close no solo", "rosas", "orquídea",
    "as folhas", "as raízes", "a textura", "as raízes expostas",
    "casca de banana", "banana fermentada", "adubo no solo", "o enxofre no solo"
)


def _narrativa_indica_apresentador(cena: Dict[str, Any]) -> Tuple[bool, str]:
    """Retorna (indica_apresentador, 'avatar_talking'|'').

    Regra Estratégica:
    Apenas ativa o avatar físico em pontos estratégicos de conexão:
      - Abertura / Hook inicial (Cena 1 ou saudações)
      - Transições narrativas explícitas
      - Fechamento / CTA final
    Falas em primeira pessoa no corpo do vídeo ("eu fiz", "eu apliquei")
    são tratadas como voiceover sobre B-roll de cobertura visual.
    """
    texto = f"{cena.get('narration', '')} {cena.get('texto', '')} {cena.get('text', '')}".lower()
    if not texto.strip():
        return False, ""

    # B-roll puro explícito (sem sujeito humano) nunca força personagem
    if any(t in texto for t in _TERMOS_BROLL_PURO):
        return False, ""

    cid = int(cena.get("id") or cena.get("scene_index") or 0)
    
    # 1. Abertura / Hook
    if cid <= 1 or any(k in texto for k in [
        "olá", "eu sou", "meu nome é", "bem-vindos", "bem vindo",
        "hello", "hi", "welcome", "welcome back", "in this video"
    ]):
        return True, "avatar_talking"

    # 2. CTA e Encerramento
    if any(k in texto for k in [
        "inscreva-se", "curta o vídeo", "deixe seu like", "se inscreva", "compartilhe",
        "subscribe", "até a próxima", "valeu", "tchau", "see you next time", "thanks for watching"
    ]):
        return True, "avatar_talking"

    # 3. Transição narrativa explícita
    from services.enhanced_scene_classifier import _FRASES_TRANSICAO
    if any(t in texto for t in _FRASES_TRANSICAO):
        return True, "avatar_talking"

    return False, ""


def decidir_personagem_cena(
    projeto_id: str,
    cena: Dict[str, Any],
    scene_type: str = "",
    contexto_visual: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Decide a referência oficial do personagem para a cena seguindo a regra estrita de prioridade.
    FASE 3.3: além da tipologia, a NARRATIVA em primeira pessoa/experiência/demonstração também
    prioriza personagem bloqueado (@Nome), nunca @Homem/@Pessoa e nunca b-roll forçado.
    """
    ident = obter_identidade_projeto(projeto_id)
    if not ident:
        return {
            "uses_character": False,
            "character_ref": "",
            "flow_character_id": "",
            "nome": "",
            "tipo": "sem_personagem",
            "origem": "none"
        }

    nome_pers = ident.get("nome", "").strip()
    flow_id = ident.get("flow_character_id", "").strip()
    ref_flow = ident.get("referencia_flow", f"@{nome_pers}" if nome_pers else "").strip()
    tipo_pers = ident.get("tipo", "personagem")
    img_abs = ident.get("imagem_abs", "")
    arq_flow = ident.get("arquivo_flow", "reference.png")

    # FASE 3.3 — se há personagem bloqueado e a narrativa indica apresentador, prioriza.
    if nome_pers:
        indica_apresentador, tipo_sugerido = _narrativa_indica_apresentador(cena)
        if indica_apresentador:
            if ref_flow.lower() in ["@homem", "@mulher", "@pessoa", "@man", "@woman", "@person"]:
                ref_flow = f"@{nome_pers}"
            return {
                "uses_character": True,
                "character_ref": ref_flow or f"@{nome_pers}",
                "flow_character_id": flow_id,
                "nome": nome_pers,
                "tipo": tipo_pers,
                "imagem_abs": img_abs,
                "arquivo_flow": arq_flow,
                "origem": "narrative_first_person",
                "scene_type_override": tipo_sugerido or "avatar_talking",
            }

    # Verifica se a tipologia da cena pede personagem
    tipos_com_personagem = ("avatar_talking", "avatar_action", "hybrid")
    pediu_char = bool(cena.get("uses_character")) or (scene_type in tipos_com_personagem)

    # Se a cena for expressamente B-Roll puro, macro ou environment sem apresentador:
    tipos_sem_personagem = ("broll_macro", "broll_action", "environment", "before_after", "comparison")
    if scene_type in tipos_sem_personagem and not cena.get("uses_character_override"):
        return {
            "uses_character": False,
            "character_ref": "",
            "flow_character_id": flow_id,
            "nome": nome_pers,
            "tipo": tipo_pers,
            "imagem_abs": img_abs,
            "origem": "broll_skip"
        }

    if not pediu_char:
        return {
            "uses_character": False,
            "character_ref": "",
            "flow_character_id": flow_id,
            "nome": nome_pers,
            "tipo": tipo_pers,
            "imagem_abs": img_abs,
            "origem": "scene_decision_no_character"
        }

    # Garante que a tag de referência nunca seja genérica (@Homem, @Pessoa, etc.)
    if ref_flow.lower() in ["@homem", "@mulher", "@pessoa", "@man", "@woman", "@person"]:
        ref_flow = f"@{nome_pers}" if nome_pers else "@Personagem"

    # Prioridade 1: Flow Character ID vinculado
    origem_resolucao = "flow_character_id" if flow_id else "referencia_flow"

    decision = {
        "uses_character": True,
        "character_ref": ref_flow,
        "flow_character_id": flow_id,
        "nome": nome_pers,
        "tipo": tipo_pers,
        "imagem_abs": img_abs,
        "arquivo_flow": arq_flow,
        "origem": origem_resolucao
    }

    log_event("CHARACTER_DECISION", f"Cena {cena.get('id', 0)}: uses_character=True, ref='{ref_flow}', flow_id='{flow_id}'")
    return decision
