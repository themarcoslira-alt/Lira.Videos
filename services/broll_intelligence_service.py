"""
services/broll_intelligence_service.py — B-Roll Intelligence Layer
===================================================================
Responsabilidade:
- Analisar a narrativa da cena e determinar a lista de elementos visuais de apoio
  (supporting_visuals) ANCORADOS no que a narração realmente descreve.

Regras de ancoragem (corrigem o viés de "objeto solto" — o teclado mecânico em
vídeo de jardim vinha da palavra "app" casando dentro de "applied"):

  1. AÇÃO FÍSICA na narração (apply/pour/dig/cut/spray/plant/test/mix/add/work…)
     -> "Gardener's hands <ação> <objeto>, POV close-up, garden environment"
  2. NARRAÇÃO CONCEITUAL / REFLEXIVA / EXPLICATIVA
     (why/because/when/if/the reason/what happens…)
     -> macro botânico PURO, sem presença humana (solo, raízes, folhas, tomateiro)
  3. RESULTADO VISUAL citado (flowers/fruit/tomatoes/roots/leaves growing)
     -> macro botânico DO RESULTADO específico
  4. Sem ação, sem conceito e sem resultado -> macro botânico base ancorado no
     `world` do contexto visual.

Correspondência SEMPRE por palavra inteira (\\bpalavra\\b), nunca substring cega:
  "app"  NÃO casa com "applied", "happens", "approach" nem "application";
  "flor" NÃO casa com "flower" (o inglês tem chaves próprias: flower/flowers);
  "rose" NÃO casa com "nitrogen", "glucose" ou "purpose".

Exemplos (fala -> 1º supporting_visual):
  "I applied gypsum to the soil"
    -> "Gardener's hands applying gypsum to the soil, POV close-up, garden environment"
  "And here's why that happens. Nitrogen."
    -> macro de solo/raízes, sem pessoas
  "the flowers were almost none"
    -> macro do tomateiro sem flores (resultado visual)
"""

import re
import unicodedata
from typing import List, Dict, Any, Optional, Tuple

from services.event_logger import log_event


# ---------------------------------------------------------------------------
# Normalização + casamento por PALAVRA INTEIRA (\b), nunca substring
# ---------------------------------------------------------------------------

def _normalizar(texto: str) -> str:
    """Minúsculas e sem acentos — permite casar chaves PT e EN com o mesmo regex."""
    base = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in base if not unicodedata.combining(c))


def _compilar(palavras) -> re.Pattern:
    """Regex de palavra inteira para um conjunto de termos (normalizados)."""
    termos = sorted({_normalizar(p).strip() for p in palavras if p and str(p).strip()},
                    key=len, reverse=True)
    if not termos:
        return re.compile(r"(?!x)x")          # nunca casa
    return re.compile(r"\b(?:" + "|".join(re.escape(t) for t in termos) + r")\b")


def _casa(padrao: re.Pattern, texto_normalizado: str) -> bool:
    """True se o padrão (palavra inteira) aparece no texto já normalizado."""
    return bool(texto_normalizado and padrao.search(texto_normalizado))


# ---------------------------------------------------------------------------
# TABELAS DE GATILHOS (a ORDEM é a precedência)
# ---------------------------------------------------------------------------

# AÇÃO FÍSICA: (padrão de palavra inteira, gerúndio EN usado no prompt).
# Cada flexão real é declarada: \b não casa "apply" dentro de "applied".
ACOES_FISICAS: Tuple[Tuple[re.Pattern, str], ...] = (
    (_compilar(("apply", "applies", "applied", "applying", "reapply", "reapplied", "reapplying",
                "fertilize", "fertilizes", "fertilized", "fertilizing", "fertilise", "fertilises",
                "fertilised", "fertilising", "adubar", "aduba", "adubou", "adubando",
                "aplicar", "aplica", "aplicou", "aplicando")), "applying"),
    (_compilar(("pour", "pours", "poured", "pouring", "despejar", "despeja", "despejou",
                "despejando")), "pouring"),
    (_compilar(("dig", "digs", "dug", "digging", "cavar", "cava", "cavou", "cavando")), "digging"),
    (_compilar(("cut", "cuts", "cutting", "prune", "prunes", "pruned", "pruning", "cortar",
                "corta", "cortou", "cortando", "podar", "poda", "podou", "podando")), "cutting"),
    (_compilar(("spray", "sprays", "sprayed", "spraying", "borrifar", "borrifa", "borrifou",
                "borrifando", "pulverizar", "pulveriza", "pulverizou", "pulverizando")), "spraying"),
    # "plant" é ambíguo (verbo x substantivo): só conta como ação nas formas
    # verbais (planting/planted/transplant...) ou em "plant(s) the/your/...".
    (re.compile(r"\b(?:planting|planted|transplant|transplants|transplanted|transplanting|"
                r"replant|replants|replanted|replanting|"
                r"plants?\s+(?:the|a|an|your|my|our|their|some|these|those|it|them|tomato|"
                r"tomatoes|seedlings?)|"
                r"plantar|plantou|plantando)\b"), "planting"),
    (_compilar(("test", "tests", "tested", "testing", "testar", "testa", "testou", "testando")), "testing"),
    (_compilar(("mix", "mixes", "mixed", "mixing", "stir", "stirs", "stirred", "stirring",
                "misturar", "mistura", "misturou", "misturando")), "mixing"),
    (_compilar(("add", "adds", "added", "adding", "adicionar", "adiciona", "adicionou",
                "adicionando")), "adding"),
    (_compilar(("work", "works", "worked", "working", "trabalhar", "trabalha", "trabalhou",
                "trabalhando")), "working"),
    (_compilar(("spread", "spreads", "spreading", "espalhar", "espalha", "espalhou",
                "espalhando")), "spreading"),
    (_compilar(("sprinkle", "sprinkles", "sprinkled", "sprinkling", "polvilhar", "polvilha",
                "polvilhando")), "sprinkling"),
    (_compilar(("watering", "watered", "waters", "regar", "rega", "regou", "regando", "molhar",
                "molha", "molhou", "molhando")), "watering"),
)

# MATERIAL aplicado (rótulo EN; ordem = prioridade do mais específico)
MATERIAIS: Tuple[Tuple[re.Pattern, str], ...] = (
    (_compilar(("gypsum", "gesso")), "gypsum"),
    (_compilar(("compost", "composto", "humus")), "compost"),
    (_compilar(("fertilizer", "fertilizers", "fertiliser", "fertilize", "fertilized",
                "fertilizing", "adubo", "fertilizante")), "organic fertilizer"),
    (_compilar(("mulch", "cobertura morta")), "mulch"),
    (_compilar(("nutrient", "nutrients", "nutriente", "nutrientes")), "nutrients"),
    (_compilar(("water", "agua")), "water"),
    (_compilar(("seed", "seeds", "semente", "sementes")), "seeds"),
    (_compilar(("seedling", "seedlings", "muda", "mudas")), "seedlings"),
    (_compilar(("soil", "solo", "substrato")), "soil"),
)

# ALVO da ação (onde a ação acontece)
ALVOS: Tuple[Tuple[re.Pattern, str], ...] = (
    (_compilar(("soil", "solo", "substrato")), "soil"),
    (_compilar(("raised bed", "raised beds", "canteiro", "canteiros")), "raised bed"),
    (_compilar(("root", "roots", "raiz", "raizes", "root zone")), "root zone"),
    (_compilar(("plant base", "base of the plant", "stem base", "base da planta")), "plant base"),
)

# CONCEITUAL / REFLEXIVO / EXPLICATIVO
CONCEITUAIS: re.Pattern = _compilar((
    "why", "because", "when", "if", "reason", "reasons", "happens", "happened", "happening",
    "explain", "explains", "explained", "explanation", "myth", "myths", "truth", "actually",
    "science", "means", "mean", "understand", "realize", "realise", "notice", "noticed",
    "remember", "think", "thought", "believe", "probably", "maybe",
    "porque", "quando", "motivo", "razao", "explica", "explicar", "acontece", "aconteceu",
    "mito", "verdade", "entender", "percebi", "lembro", "talvez",
))

# RESULTADO VISUAL: (padrão, rótulo, macros específicos do resultado)
RESULTADOS: Tuple[Tuple[re.Pattern, str, Tuple[str, ...]], ...] = (
    (_compilar(("tomato", "tomatoes", "tomate", "tomates", "tomateiro", "tomateiros")), "tomatoes",
     ("Macro of tomato plants with sparse blossoms and no fruit set, natural daylight, shallow depth of field",
      "Extreme close-up macro of green tomato trusses at different ripening stages, natural daylight",
      "Macro of a tomato stem with healthy foliage in sharp focus, soft garden bokeh")),
    (_compilar(("fruit", "fruits", "fruiting", "fruto", "frutos", "frutificar")), "fruit",
     ("Extreme close-up macro of fruit setting on the plant, water droplets, natural daylight",
      "Macro of a productive flower truss turning into young fruit, shallow depth of field",
      "Macro of healthy leaves and stems supporting the fruiting plant, soft garden bokeh")),
    (_compilar(("flower", "flowers", "flowering", "blossom", "blossoms", "bloom", "blooms",
                "rose", "roses", "flor", "flores", "petala", "petalas", "orquidea", "orquideas")), "flowers",
     ("Macro of a tomato plant with almost no flowers, bare stems in sharp focus, natural daylight",
      "Extreme close-up of an empty flower truss with buds that never set fruit, natural daylight",
      "Macro of lush green foliage without blossoms, soft garden bokeh")),
    (_compilar(("root", "roots", "rooted", "raiz", "raizes", "root system")), "roots",
     ("Extreme close-up macro of a healthy white root network inside dark moist soil, natural daylight",
      "Macro of soil particles clinging to fine roots, shallow depth of field",
      "Macro of the root zone at the base of the plant in dark rich soil, natural daylight")),
    (_compilar(("leaf", "leaves", "foliage", "folha", "folhas")), "leaves",
     ("Extreme close-up macro of leaf surface and veins with morning dew, natural daylight",
      "Macro of green foliage layers with soft atmospheric bokeh",
      "Macro of leaves catching natural sunlight, photorealistic textures")),
    (_compilar(("grow", "grows", "growing", "grew", "growth", "crescer", "cresce", "cresceu",
                "crescendo", "crescimento")), "growth",
     ("Macro of new growth emerging from dark soil, natural daylight, shallow depth of field",
      "Extreme close-up of an emerging shoot with its first leaves, shallow depth of field",
      "Macro of steadily growing foliage in a consistent garden setting, natural daylight")),
)

# DOMÍNIO do projeto (o jardim é o padrão; tech/culinária têm chaves próprias,
# agora TODAS com limite de palavra — "app" não casa "applied").
JARDIM: re.Pattern = _compilar((
    "garden", "gardening", "plant", "plants", "planting", "soil", "compost", "gypsum",
    "fertilizer", "fertiliser", "mulch", "seed", "seeds", "seedling", "root", "roots",
    "leaf", "leaves", "foliage", "flower", "flowers", "blossom", "tomato", "tomatoes",
    "vegetable", "harvest", "pruning", "watering", "lawn", "grass", "shrub", "botanical",
    "jardim", "jardinagem", "planta", "plantas", "solo", "adubo", "fertilizante", "semente",
    "sementes", "muda", "mudas", "raiz", "raizes", "folha", "folhas", "flor", "flores",
    "tomate", "tomates", "tomateiro", "horta", "grama", "gramado",
))
TECNOLOGIA: re.Pattern = _compilar((
    "code", "codes", "coding", "software", "app", "apps", "developer", "programming",
    "interface", "dashboard", "server", "api", "database", "algorithm", "deploy",
    "screen", "monitor", "keyboard", "workstation", "laptop", "notebook computer",
    "tela", "codigo", "programacao", "computador", "teclado",
))
CULINARIA: re.Pattern = _compilar((
    "recipe", "recipes", "ingredient", "ingredients", "cooking", "cook", "kitchen", "oven",
    "pan", "flavor", "taste", "baking", "receita", "cozinha", "ingrediente", "sabor", "forno",
    "frigideira", "cozinhar",
))

# Presença humana que NÃO pode existir no macro botânico puro
PRESENCA_HUMANA: re.Pattern = _compilar((
    "hands", "hand", "person", "people", "gardener", "presenter", "man", "woman", "human",
    "body", "arms", "fingers", "face", "maos", "mao", "pessoa", "apresentador", "jardineiro",
))


# ---------------------------------------------------------------------------
# Helpers de decisão
# ---------------------------------------------------------------------------

def _primeiro_match(texto: str, tabela) -> str:
    """Rótulo do primeiro padrão que casa (a ordem da tabela é a precedência)."""
    for padrao, rotulo in tabela:
        if _casa(padrao, texto):
            return rotulo
    return ""


def _acao_detectada(texto: str) -> str:
    """Gerúndio EN da primeira família de AÇÃO FÍSICA encontrada ('' se nenhuma)."""
    for padrao, gerundio in ACOES_FISICAS:
        if _casa(padrao, texto):
            return gerundio
    return ""


def _resultado_detectado(texto: str):
    """(rótulo, macros) do primeiro RESULTADO VISUAL citado, ou ('', ())."""
    for padrao, rotulo, macros in RESULTADOS:
        if _casa(padrao, texto):
            return rotulo, macros
    return "", ()


def _dominio(fala: str, contexto_visual: Optional[Dict[str, Any]]) -> str:
    """Domínio do conteúdo: 'garden' (padrão), 'tech' ou 'culinary'."""
    pistas = fala or ""
    if contexto_visual:
        pistas += " " + _normalizar(" ".join(
            str(contexto_visual.get(k) or "") for k in ("world", "theme", "visual_style")))
    if _casa(JARDIM, pistas):
        return "garden"
    if _casa(TECNOLOGIA, pistas):
        return "tech"
    if _casa(CULINARIA, pistas):
        return "culinary"
    return "garden"


def _sem_presenca_humana(itens: List[str]) -> List[str]:
    """Mantém apenas itens sem presença humana (macro botânico puro)."""
    return [i for i in itens if not _casa(PRESENCA_HUMANA, _normalizar(i))]


def _base_botanica(world: str) -> List[str]:
    """Macro botânico base (3 itens), sem presença humana."""
    return [
        "Extreme close-up macro of rich dark garden soil texture, natural daylight, "
        "shallow depth of field, photorealistic",
        "Macro of living plant roots and fine soil particles, natural daylight, shallow depth of field",
        f"Detailed macro of green foliage with soft natural bokeh in the {world}",
    ]


def _hands(gerundio: str, fala: str, world: str) -> List[str]:
    """POV de mãos executando a ação física citada (objeto extraído da narração)."""
    material = _primeiro_match(fala, MATERIAIS)
    alvo = _primeiro_match(fala, ALVOS)
    if material and material != "soil":
        nucleo = f"{gerundio} {material}" + (f" to the {alvo}" if alvo else " into the garden soil")
    elif alvo:
        nucleo = f"{gerundio} the {alvo}"
    else:
        nucleo = f"{gerundio} around the plant base"
    return [
        f"Gardener's hands {nucleo}, POV close-up, garden environment",
        "Close-up of the treated soil surface with fine organic texture, natural daylight",
        f"Same hands and tools continuing the task in the {world}, consistent lighting and colour palette",
    ]


def _garden_contextual(fala: str, contexto_visual: Optional[Dict[str, Any]]) -> List[str]:
    """Regras 1 -> 2 -> 3 -> 4: ancora o visual no que a narração descreve."""
    world = ((contexto_visual or {}).get("world")
             or "lush botanical garden with rich dark soil and natural daylight")
    acao = _acao_detectada(fala)
    if acao:
        return _hands(acao, fala, world)                     # 1. ação física -> mãos/POV
    if _casa(CONCEITUAIS, fala):
        return _sem_presenca_humana(_base_botanica(world))   # 2. conceitual -> macro puro
    _rotulo, macros = _resultado_detectado(fala)
    if macros:
        itens = [macros[0]] + _base_botanica(world)[1:]      # 3. resultado -> macro do resultado
        return _sem_presenca_humana(itens)
    return _sem_presenca_humana(_base_botanica(world))       # 4. base botânica


def gerar_broll_inteligente(
    cena: Dict[str, Any],
    scene_type: str,
    contexto_visual: Optional[Dict[str, Any]] = None
) -> List[str]:
    """
    Devolve 3 supporting_visuals ANCORADOS na narração da cena:
      ação física -> POV de mãos; conceitual/reflexivo -> macro botânico puro;
      resultado visual -> macro do resultado citado; senão -> macro botânico base.
    Correspondência por palavra inteira (\\b), nunca substring: "app" não casa
    "applied/happens/approach" (era daí que vinha o teclado mecânico no jardim).
    """
    fala = _normalizar(" ".join(str(cena.get(k) or "")
                                for k in ("narration", "texto", "text")))
    dominio = _dominio(fala, contexto_visual)

    if dominio == "tech":
        suporte = [
            "Hands typing on a minimalist mechanical keyboard, POV close-up, soft LED rim lighting",
            "Clean macro of code syntax on a high-resolution display, shallow depth of field",
            "Modern workstation ambiance with soft LED rim lighting, photorealistic, 16:9",
        ]
    elif dominio == "culinary":
        suporte = [
            "Hands preparing fresh ingredients on a wooden board, POV close-up, warm kitchen light",
            "Knife precision slicing through fresh crisp produce, shallow depth of field",
            "Steaming pan with aromatic herbs in a warm kitchen, photorealistic, 16:9",
        ]
    else:
        suporte = _garden_contextual(fala, contexto_visual)

    log_event("BROLL_INTELLIGENCE",
              f"Cena {cena.get('id', 0)}: dominio={dominio} "
              f"{len(suporte)} supporting_visuals ancorados na narração.")
    return suporte



