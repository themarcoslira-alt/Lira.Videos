"""
storyboard_builder.py — Storyboard Builder (BLOCO 4).

Nova etapa entre a transcrição (frases) e a geração de prompts:
identifica "beats visuais" (substantivos/ações) dentro de cada frase e, quando
houver múltiplos beats num intervalo curto, desdobra em sub-cenas usando os
word_timestamps.json (BLOCO 3). Se os word timestamps não estiverem disponíveis,
divide o intervalo proporcionalmente (FALLBACK documentado, não ideal).

Saída: storyboard.json por projeto (lista de cenas) + linhas de prompt com
"[MM:SS-MM:SS] [VIDEO|IMAGE] texto".

Compatibilidade: o `storyboard.json` legado (b-roll via Claude, com
keywords/search_queries) é um artefato DIFERENTE. Para não destruí-lo, se o
arquivo já existir com estrutura legada, o builder grava em `storyboard_beats.json`
e registra um aviso (nunca em silêncio). force=True sobrescreve.
"""

import json
import os
import random
import re
from pathlib import Path
from typing import Any, Dict, List

from config import PROJETOS_DIR, NARRATIVE_CYCLE_ENABLED, CYCLE_TIPOS, VALID_EFEITOS
from services.event_logger import log_event
from services.scene_schema import nova_cena_ciclo, aplicar_campos_ciclo

STORYBOARD_FILE = "storyboard.json"
STORYBOARD_BEATS_FILE = "storyboard_beats.json"
DEFAULT_PROPORCAO_VIDEO = 0.60  # 60% vídeo (dinâmico/ação), 40% imagem parada (macro/detalhe)

_STOP = {
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for", "with",
    "that", "this", "is", "are", "was", "were", "be", "been", "it", "its",
    "you", "your", "we", "our", "they", "their", "as", "by", "from", "not",
    "but", "if", "then", "so", "about", "when", "how", "what", "why", "all",
    "one", "two", "some", "very", "just", "too", "also", "there", "here",
    "these", "those", "have", "has", "had", "do", "does", "did", "will",
    "would", "can", "could", "should", "more", "most", "other", "such", "only",
    "own", "same", "than", "near", "maybe", "right", "next", "way", "back",
    "first", "every", "like", "even", "still", "much", "many", "now", "good",
    "well", "know", "see", "go", "get", "make", "thing", "things", "something",
    "anything", "really", "because", "while", "since", "during", "before",
    "after", "again", "always", "never", "often", "today",
}

# Separadores de beats visuais (enumeração). "and" puro NÃO separa, para não
# desdobrar fala comum; vírgula, ;, - e "even/then/e/ou" separam.
_BEAT_SPLIT = re.compile(r"\s*,\s*|\s*;\s*|\s+-\s+|\s+(?:even|then|e|ou)\s+", re.IGNORECASE)


def _fmt_ts(sec):
    sec = max(0.0, float(sec or 0))
    return f"{int(sec // 60):02d}:{int(sec % 60):02d}"


def _carregar_frases(project_dir):
    """Frases do roteiro (roteiro_transcricao.json) — fallback TXT."""
    project_dir = Path(project_dir)
    json_path = project_dir / "roteiro_transcricao.json"
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            frases = []
            for s in data.get("segments", []):
                texto = (s.get("text") or "").strip()
                if not texto:
                    continue
                frases.append({
                    "start": float(s.get("start", 0)),
                    "end": float(s.get("end", 0)),
                    "text": texto,
                })
            return frases
        except Exception as e:
            log_event("STORYBOARD", f"erro ao ler roteiro json: {e}", level="warn")
    txt_path = project_dir / "roteiro_transcricao.txt"
    if txt_path.exists():
        try:
            frases = []
            for line in txt_path.read_text(encoding="utf-8").splitlines():
                m = re.match(r"\[(\d{1,2}):(\d{2})\]\s*(.*)", line.strip())
                if m:
                    start = int(m.group(1)) * 60 + int(m.group(2))
                    frases.append({"start": start, "end": start + 5.0, "text": m.group(3).strip()})
            return frases
        except Exception:
            pass
    return []


def _carregar_palavras(project_dir):
    """Lista achatada de (palavra, start, end) do word_timestamps.json (BLOCO 3)."""
    path = Path(project_dir) / "word_timestamps.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        palavras = []
        for seg in data.get("segments", []):
            for w in seg.get("words", []):
                palavra = (w.get("w") or "").strip()
                if palavra:
                    palavras.append((palavra, float(w.get("s", 0)), float(w.get("e", 0))))
        return palavras
    except Exception as e:
        log_event("STORYBOARD", f"erro ao ler word_timestamps.json: {e}", level="warn")
        return []


def _extrair_beats(texto):
    """Divide uma frase em beats visuais (enumeração por vírgula/;/-/even/then)."""
    texto = (texto or "").strip()
    if not texto:
        return []
    partes = [p.strip(" .,;:!?()\"'") for p in _BEAT_SPLIT.split(texto) if p.strip()]
    beats = []
    for parte in partes:
        tokens = [t for t in re.split(r"\W+", parte.lower())
                  if t and len(t) >= 3 and t not in _STOP]
        if not tokens:
            continue
        beat = " ".join(tokens[:3])
        if beat not in beats:
            beats.append(beat)
    return beats


def _localizar_beat(palavras, beat, inicio, fim):
    """Procura a primeira palavra do beat dentro de [inicio, fim].
    Retorna (start, end) da palavra ou None."""
    tokens = set(beat.split())
    for palavra, ps, pe in palavras:
        if ps < inicio - 0.3 or pe > fim + 0.3:
            continue
        limpa = re.sub(r"\W", "", palavra.lower())
        if not limpa:
            continue
        for tok in tokens:
            if limpa == tok or limpa.startswith(tok) or tok.startswith(limpa):
                return (ps, pe)
    return None


def _detectar_legado(scenes_list):
    """True se a lista parece o storyboard legado (b-roll via Claude)."""
    if not isinstance(scenes_list, list) or not scenes_list:
        return False
    primeiro = scenes_list[0] if isinstance(scenes_list[0], dict) else {}
    return any(k in primeiro for k in ("keywords", "search_queries", "scene_type", "media_preference"))


def _atribuir_midia(scenes, proporcao_video):
    """Decide vídeo/imagem por posição temporal (HOOK/MEIO/FINAL) + <2s=imagem.
    Determinístico (seed fixa) e ajustado à proporção alvo configurável."""
    if not scenes:
        return scenes
    total = max(float(s["end_sec"]) for s in scenes) or 1.0
    hook_limite = min(0.20, 15.0 / total) if total > 0 else 0.20

    probs = []
    for s in scenes:
        pos = s["start_sec"] / total if total else 0
        if s["duration_sec"] < 2.0:
            p = 0.0  # cena curta -> imagem
        elif pos <= hook_limite:
            p = 0.87  # HOOK
        elif pos <= 0.80:
            p = 0.60  # MEIO
        else:
            p = 0.55  # FINAL
        probs.append(p)

    esperado = (sum(probs) / len(probs)) if probs else proporcao_video
    fator = (proporcao_video / esperado) if esperado > 0 else 1.0
    rng = random.Random(12345)
    for i, s in enumerate(scenes):
        p = max(0.0, min(1.0, probs[i] * fator))
        s["media_type"] = "video" if rng.random() < p else "photo"
    return scenes


def gerar_ciclos_narrativos(cenas_totais: int, blocos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Gera ciclos Avatar → Imagem → Vídeo ao longo do vídeo.

    Cada bloco (frase/segmento do roteiro) gera UM ciclo de 3 cenas:
      1. avatar_intro  (5-10s)   — apresentador fala sobre o tópico
      2. imagem_zoom   (10-15s)  — close macro com zoom progressivo
      3. video_acao    (5-10s)   — mãos demonstrando a ação

    Retorna lista de cenas com os campos do ciclo preenchidos
    (tipo_cena, efeito, posicao_ciclo, duracao_planejada, ciclo_numero).
    """
    ciclos: List[Dict[str, Any]] = []
    if not blocos:
        return ciclos

    for ciclo_num, bloco in enumerate(blocos, start=1):
        topico = str(bloco.get("topico") or bloco.get("text") or bloco.get("texto") or "")
        conteudo = str(bloco.get("conteudo") or bloco.get("text") or bloco.get("texto") or topico)
        acao = str(bloco.get("acao") or bloco.get("action") or conteudo)

        cena_avatar = nova_cena_ciclo(
            scene_id=len(ciclos) + 1, posicao_ciclo=1, ciclo_numero=ciclo_num,
            texto=f"Apresentador (@presenter) fala sobre: {topico}",
            efeito="none", duracao_planejada=7.0,
        )
        cena_avatar["prompt"] = cena_avatar["texto"]
        ciclos.append(cena_avatar)

        cena_imagem = nova_cena_ciclo(
            scene_id=len(ciclos) + 1, posicao_ciclo=2, ciclo_numero=ciclo_num,
            texto=f"Close macro de {conteudo}, detalhe, zoom progressivo",
            efeito="zoom_in", duracao_planejada=12.0,
        )
        cena_imagem["prompt"] = cena_imagem["texto"]
        ciclos.append(cena_imagem)

        cena_video = nova_cena_ciclo(
            scene_id=len(ciclos) + 1, posicao_ciclo=3, ciclo_numero=ciclo_num,
            texto=f"Mãos demonstrando: {acao}, movimento real, close ação",
            efeito="fade", duracao_planejada=8.0,
        )
        cena_video["prompt"] = cena_video["texto"]
        ciclos.append(cena_video)

    return ciclos


def _aplicar_ciclo_narrativo(scenes: List[Dict[str, Any]]) -> None:
    """Passada ADITIVA que preenche os campos do ciclo (tipo_cena/efeito/
    posicao_ciclo/ciclo_numero/duracao_planejada) nas scenes existentes.

    Não altera media_type já atribuído nem a contagem de cenas (compatível
    com os testes existentes). Cenas curtas (<2.5s) permanecem foto/macro.
    """
    if not scenes:
        return
    for i, s in enumerate(scenes):
        posicao = (i % 3) + 1
        # cena curta continua imagem/macro (regra 4.3 preservada)
        if float(s.get("duration_sec") or 0) < 2.5:
            posicao = 2
        aplicar_campos_ciclo(s, posicao, ciclo_numero=(i // 3) + 1)


def construir_storyboard(project_name, proporcao_video=None, force=False, base_dir=None):
    """Constrói o storyboard (beats visuais) e salva no projeto.

    Retorna {"success", "arquivo", "cenas_count", "video_count", "photo_count",
             "usou_word_timestamps", "scenes"}.
    """
    base = Path(base_dir) if base_dir else PROJETOS_DIR
    project_dir = base / project_name
    if not project_dir.exists():
        return {"success": False, "error": f"projeto não encontrado: {project_name}"}
    proporcao_video = float(proporcao_video) if proporcao_video is not None else DEFAULT_PROPORCAO_VIDEO

    frases = _carregar_frases(project_dir)
    if not frases:
        log_event("STORYBOARD", f"{project_name}: nenhuma frase de transcrição", level="warn")
        return {"success": False, "error": "transcrição não encontrada"}
    palavras = _carregar_palavras(project_dir)
    log_event("STORYBOARD",
              f"{project_name}: {len(frases)} frases, {len(palavras)} palavras "
              f"(word_timestamps={'sim' if palavras else 'NAO -> fallback proporcional'})",
              level="info")

    scenes = []
    scene_id = 1
    usar_palavras = bool(palavras)

    for frase in frases:
        texto = frase["text"]
        ini = float(frase["start"])
        fim = float(frase["end"])
        dur = max(0.5, fim - ini)
        beats = _extrair_beats(texto)

        # 4.3 — conteúdo é unidade visual única (ou intervalo curto): 1 cena
        if len(beats) <= 1 or dur <= 2.5:
            scenes.append({
                "scene_id": scene_id, "start": _fmt_ts(ini), "end": _fmt_ts(fim),
                "start_sec": round(ini, 2), "end_sec": round(fim, 2),
                "duration_sec": round(dur, 2), "text": texto, "media_type": None,
            })
            scene_id += 1
            continue

        # 4.2 — múltiplos beats: localiza palavra real (BLOCO 3) ou proporcional
        sub = []
        if usar_palavras:
            for b in beats:
                loc = _localizar_beat(palavras, b, ini, fim)
                if loc is None:
                    sub = []  # falha na localização -> fallback proporcional
                    break
                sub.append((b, loc[0], loc[1]))
        if not sub:
            passo = dur / len(beats)
            for i, b in enumerate(beats):
                s0 = ini + i * passo
                s1 = ini + (i + 1) * passo if i < len(beats) - 1 else fim
                sub.append((b, s0, s1))
        for i, (b, s0, s1) in enumerate(sub):
            if i < len(sub) - 1:
                s1 = sub[i + 1][1]  # fim = início do próximo beat
            scenes.append({
                "scene_id": scene_id, "start": _fmt_ts(s0), "end": _fmt_ts(s1),
                "start_sec": round(s0, 2), "end_sec": round(s1, 2),
                "duration_sec": round(max(0.5, s1 - s0), 2),
                "text": b, "media_type": None,
            })
            scene_id += 1

    scenes = _atribuir_midia(scenes, proporcao_video)

    # CICLO NARRATIVO v0.3.5+ — passada aditiva sobre as cenas existentes
    if NARRATIVE_CYCLE_ENABLED:
        _aplicar_ciclo_narrativo(scenes)
        log_event("STORYBOARD",
                  f"{project_name}: ciclo narrativo aplicado ({len(scenes)} cenas)",
                  level="info")

    # não destruir o storyboard legado (b-roll via Claude) em silêncio
    destino = Path(project_dir) / STORYBOARD_FILE
    if destino.exists() and not force:
        try:
            existente = json.loads(destino.read_text(encoding="utf-8"))
            if _detectar_legado(existente):
                destino = Path(project_dir) / STORYBOARD_BEATS_FILE
                log_event("STORYBOARD",
                          f"{project_name}: storyboard.json legado preservado — beats em {STORYBOARD_BEATS_FILE}",
                          level="warn")
        except Exception:
            pass

    tmp = Path(str(destino) + ".tmp")
    tmp.write_text(json.dumps(scenes, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp), str(destino))
    log_event("STORYBOARD", f"{project_name}: storyboard salvo em {destino.name} ({len(scenes)} cenas)",
              level="info")

    return {
        "success": True,
        "project": project_name,
        "arquivo": str(destino),
        "cenas_count": len(scenes),
        "video_count": sum(1 for s in scenes if s["media_type"] == "video"),
        "photo_count": sum(1 for s in scenes if s["media_type"] == "photo"),
        "usou_word_timestamps": usar_palavras,
        "scenes": scenes,
    }


def carregar_storyboard(project_name, base_dir=None):
    """Carrega o storyboard NOVO (formato de beats). [] se ausente/legado."""
    base = Path(base_dir) if base_dir else PROJETOS_DIR
    project_dir = base / project_name
    for nome in (STORYBOARD_FILE, STORYBOARD_BEATS_FILE):
        path = Path(project_dir) / nome
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list) and data and "media_type" in data[0]:
                    return data
            except Exception:
                pass
    return []


def linhas_para_prompt(project_name, base_dir=None):
    """Linhas '[MM:SS-MM:SS] [VIDEO|IMAGE] texto' a partir do storyboard novo.

    BLOCO 4.6 — integração com o gerador de prompts existente.
    """
    scenes = carregar_storyboard(project_name, base_dir=base_dir)
    linhas = []
    for s in scenes:
        tipo = "VIDEO" if s.get("media_type") == "video" else "IMAGE"
        linhas.append(f"[{s['start']}-{s['end']}] [{tipo}] {s.get('text', '')}".rstrip())
    return linhas


