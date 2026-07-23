"""
scene_builder.py — Divisão do roteiro em cenas
Gera cenas.json com:
  - id, texto, timestamps (MM:SS, compatibilidade)
  - start_time, end_time (segundos float)
  - duration (segundos)
  - transcript_lines (segmentos originais da transcrição)
  - previous_scene_id, next_scene_id
  - previous_context, next_context (texto das cenas adjacentes)
Fonte de verdade temporal: roteiro_transcricao.json (se existir).
Fallback: roteiro_transcricao.txt (projetos antigos).
"""
import json
import re
from pathlib import Path
from config import PROJETOS_DIR


def _ts_to_seconds(ts: str) -> float:
    """Converte MM:SS para segundos float."""
    partes = ts.split(":")
    return int(partes[0]) * 60 + int(partes[1])


def _carregar_transcricao(project_dir: Path) -> tuple:
    """
    Carrega a transcrição.
    Retorna (segmentos: list[dict], duracao_total: float).
    Cada segmento: {"start": float, "end": float, "text": str, "timestamp": str}
    Se falhar, retorna ([], 0).
    """
    json_path = project_dir / "roteiro_transcricao.json"
    txt_path = project_dir / "roteiro_transcricao.txt"

    # Tenta JSON primeiro (fonte de verdade)
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("segments", []), data.get("duration", 0)
        except Exception:
            pass

    # Fallback: TXT (projetos antigos)
    if txt_path.exists():
        from services.event_logger import log_event
        log_event("SCENES", "JSON de transcricao nao encontrado, usando TXT como fallback",
                  level="warn")
        segmentos = []
        try:
            with open(txt_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    match = re.match(r'\[(\d{2}:\d{2})\]\s*(.*)', line)
                    if match:
                        ts = match.group(1)
                        texto = match.group(2)
                        seg_start = _ts_to_seconds(ts)
                        # Sem end_time no TXT, estimamos ~5s por segmento
                        segmentos.append({
                            "start": seg_start,
                            "end": round(seg_start + 5.0, 2),
                            "text": texto,
                            "timestamp": ts
                        })
            duracao = segmentos[-1]["end"] if segmentos else 0
            return segmentos, duracao
        except Exception:
            pass

    return [], 0


def gerar_cenas(project_name: str) -> dict:
    """
    Lê a transcrição (JSON ou TXT) e divide em cenas.
    Salva cenas.json no diretório do projeto com metadados temporais.
    """
    from services.event_logger import log_event

    log_event("SCENES", f"Iniciando geracao de cenas para {project_name}", level="info")

    project_dir = PROJETOS_DIR / project_name
    roteiro_txt = project_dir / "roteiro_transcricao.txt"
    cenas_file = project_dir / "cenas.json"

    # Carrega segmentos da transcrição
    segmentos, duracao_total = _carregar_transcricao(project_dir)

    if not segmentos and not roteiro_txt.exists():
        log_event("SCENES", "Nenhum arquivo de transcricao encontrado", level="error")
        return {"success": False, "error": "Nenhum arquivo de transcricao encontrado"}

    # Se não temos segmentos estruturados, carrega TXT bruto
    if not segmentos:
        with open(roteiro_txt, "r", encoding="utf-8") as f:
            linhas = f.readlines()
        for linha in linhas:
            linha = linha.strip()
            match = re.match(r'\[(\d{2}:\d{2})\]\s*(.*)', linha)
            if match:
                ts = match.group(1)
                texto = match.group(2)
                seg_start = _ts_to_seconds(ts)
                segmentos.append({
                    "start": seg_start,
                    "end": round(seg_start + 5.0, 2),
                    "text": texto,
                    "timestamp": ts
                })
        duracao_total = segmentos[-1]["end"] if segmentos else 0

    # --- LÓGICA DE DIVISÃO EM CENAS ---
    cenas = []
    current_scene = {
        "id": 1, "texto": "", "timestamps": [], "topic": "",
        "segment_indices": []
    }
    scene_count = 1

    for idx, seg in enumerate(segmentos):
        texto = seg["text"]
        timestamp = seg["timestamp"]

        # Decide se abre nova cena (quando texto acumulado > 200 chars)
        if len(current_scene["texto"]) > 200:
            cenas.append(current_scene)
            scene_count += 1
            current_scene = {
                "id": scene_count,
                "texto": texto,
                "timestamps": [timestamp],
                "topic": "",
                "segment_indices": [idx]
            }
        else:
            if current_scene["texto"]:
                current_scene["texto"] += " " + texto
            else:
                current_scene["texto"] = texto
            current_scene["timestamps"].append(timestamp)
            current_scene["segment_indices"].append(idx)

    # Adiciona última cena
    if current_scene["texto"]:
        cenas.append(current_scene)

    # Se só tem uma cena, tenta dividir por pontuação
    if len(cenas) <= 1 and cenas:
        textos = re.split(r'[.!?]+', cenas[0]["texto"])
        textos = [t.strip() for t in textos if len(t.strip()) > 30]
        if len(textos) > 1:
            cenas = []
            # Distribui segmentos proporcionalmente
            total_segs = len(segmentos)
            segs_por_cena = max(1, total_segs // len(textos))
            for i, t in enumerate(textos):
                start_idx = i * segs_por_cena
                end_idx = min((i + 1) * segs_por_cena, total_segs)
                cenas.append({
                    "id": i + 1,
                    "texto": t,
                    "timestamps": [segmentos[s]["timestamp"] for s in range(start_idx, end_idx)],
                    "topic": "",
                    "segment_indices": list(range(start_idx, end_idx))
                })

    # --- ENRIQUECER CENAS COM METADADOS TEMPORAIS E CONTEXTO ---
    cenas_enriquecidas = []
    for i, cena in enumerate(cenas):
        # Coleta timestamps reais dos segmentos
        indices = cena.get("segment_indices", [])
        if indices and indices[0] < len(segmentos):
            seg_inicio = segmentos[indices[0]]
            seg_fim = segmentos[indices[-1]]
            start_time = seg_inicio["start"]
            end_time = seg_fim["end"]
            duration = round(end_time - start_time, 2)
        else:
            # Fallback: usa timestamps MM:SS
            ts_list = cena.get("timestamps", [])
            if ts_list:
                start_time = _ts_to_seconds(ts_list[0])
                end_time = _ts_to_seconds(ts_list[-1]) + 5.0
                duration = round(end_time - start_time, 2)
            else:
                start_time = 0.0
                end_time = 5.0
                duration = 5.0

        # Contexto anterior
        if i > 0:
            previous_context = cenas[i - 1].get("texto", "")
            previous_scene_id = cenas[i - 1].get("id", 0)
        else:
            previous_context = ""
            previous_scene_id = 0

        # Contexto posterior
        if i < len(cenas) - 1:
            next_context = cenas[i + 1].get("texto", "")
            next_scene_id = cenas[i + 1].get("id", 0)
        else:
            next_context = ""
            next_scene_id = 0

        # Monta transcript_lines (segmentos que compõem esta cena)
        transcript_lines = []
        for sidx in indices:
            if sidx < len(segmentos):
                transcript_lines.append(segmentos[sidx])
            else:
                # Fallback: usa timestamp do array
                ts_idx = len(transcript_lines)
                if ts_idx < len(cena.get("timestamps", [])):
                    ts = cena["timestamps"][ts_idx]
                    transcript_lines.append({
                        "start": _ts_to_seconds(ts),
                        "end": round(_ts_to_seconds(ts) + 5.0, 2),
                        "text": "",
                        "timestamp": ts
                    })

        cena_enriquecida = {
            "id": cena["id"],
            "texto": cena["texto"],
            "timestamps": cena["timestamps"],
            "topic": cena["topic"],
            "start_time": round(start_time, 2),
            "end_time": round(end_time, 2),
            "duration": duration,
            "previous_scene_id": previous_scene_id,
            "next_scene_id": next_scene_id,
            "previous_context": previous_context,
            "next_context": next_context,
            "transcript_lines": transcript_lines
        }
        cenas_enriquecidas.append(cena_enriquecida)

    # Salva cenas.json
    with open(cenas_file, "w", encoding="utf-8") as f:
        json.dump(cenas_enriquecidas, f, indent=2, ensure_ascii=False)

    return {
        "success": True,
        "project": project_name,
        "cenas_count": len(cenas_enriquecidas),
        "cenas": cenas_enriquecidas,
        "duration_total": duracao_total
    }