"""
scene_context.py — Contrato de dados Scene Planning Context
Agrega todas as informações do pipeline em uma estrutura unificada
para ser consumida futuramente pelo B-Roll Director e Claude batch.

Fontes de verdade:
  - roteiro_transcricao.json → segmentos, timestamps, texto completo
  - cenas.json v2 → cenas enriquecidas com contexto

Compatibilidade:
  - Projetos novos: leitura direta do JSON
  - Projetos antigos (só TXT): fallback para cenas.json + TXT
"""
import json
import re
from pathlib import Path
from typing import Optional
from config import PROJETOS_DIR


def _ts_to_seconds(ts: str) -> float:
    """Converte MM:SS para segundos float."""
    partes = ts.split(":")
    return int(partes[0]) * 60 + int(partes[1])


def _carregar_roteiro(project_dir: Path) -> dict:
    """
    Carrega o roteiro completo.
    Retorna: {"texto": str, "duration": float, "language": str, "segment_count": int}
    """
    json_path = project_dir / "roteiro_transcricao.json"
    txt_path = project_dir / "roteiro_transcricao.txt"

    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            segmentos = data.get("segments", [])
            texto = " ".join(s.get("text", "") for s in segmentos)
            return {
                "texto": texto,
                "duration": data.get("duration", 0),
                "language": data.get("language", "pt"),
                "segment_count": data.get("segment_count", 0)
            }
        except Exception:
            pass

    # Fallback TXT
    if txt_path.exists():
        with open(txt_path, "r", encoding="utf-8") as f:
            linhas = f.readlines()
        textos = []
        for linha in linhas:
            match = re.match(r'\[\d{2}:\d{2}\]\s*(.*)', linha.strip())
            if match:
                textos.append(match.group(1))
        return {
            "texto": " ".join(textos),
            "duration": 0,
            "language": "pt",
            "segment_count": len(textos)
        }

    return {"texto": "", "duration": 0, "language": "pt", "segment_count": 0}


def _carregar_cenas(project_dir: Path) -> list:
    """
    Carrega as cenas do projeto.
    Retorna lista de dics (formato cenas.json v2 ou fallback).
    """
    cenas_file = project_dir / "cenas.json"
    if not cenas_file.exists():
        return []

    try:
        with open(cenas_file, "r", encoding="utf-8") as f:
            cenas = json.load(f)
        return cenas if isinstance(cenas, list) else []
    except Exception:
        return []


def _normalizar_cena(cena: dict, cenas: list, idx: int, roteiro_texto: str) -> dict:
    """
    Normaliza uma cena para o contrato ScenePlanningContext.
    Garante que todos os campos existam, mesmo em projetos antigos.
    """
    # Timestamps
    ts_list = cena.get("timestamps", [])
    start_time = cena.get("start_time", 0.0)
    end_time = cena.get("end_time", 0.0)
    duration = cena.get("duration", 0.0)

    # Fallback se start_time/end_time não existem (projeto antigo)
    if not start_time and ts_list:
        start_time = _ts_to_seconds(ts_list[0])
        end_time = _ts_to_seconds(ts_list[-1]) + 5.0
        duration = round(end_time - start_time, 2)

    # Contexto
    prev_ctx = cena.get("previous_context", "")
    next_ctx = cena.get("next_context", "")
    prev_id = cena.get("previous_scene_id", cenas[idx - 1].get("id", 0) if idx > 0 else 0)
    next_id = cena.get("next_scene_id", cenas[idx + 1].get("id", 0) if idx < len(cenas) - 1 else 0)

    # Se contexto vazio mas cenas adjacentes existem, monta (projeto antigo)
    if not prev_ctx and idx > 0:
        prev_ctx = cenas[idx - 1].get("texto", cenas[idx - 1].get("text", ""))
    if not next_ctx and idx < len(cenas) - 1:
        next_ctx = cenas[idx + 1].get("texto", cenas[idx + 1].get("text", ""))

    # transcript_lines
    transcript_lines = cena.get("transcript_lines", [])
    if not transcript_lines:
        # Fallback: monta a partir dos timestamps
        for ts in ts_list:
            seg_start = _ts_to_seconds(ts)
            transcript_lines.append({
                "start": seg_start,
                "end": round(seg_start + 5.0, 2),
                "text": "",
                "timestamp": ts
            })

    return {
        "scene_id": cena.get("id", idx + 1),
        "texto": cena.get("texto", cena.get("text", "")),
        "start_time": round(start_time, 2),
        "end_time": round(end_time, 2),
        "duration": duration,
        "timestamps": ts_list,
        "topic": cena.get("topic", ""),
        "previous_scene_id": prev_id,
        "next_scene_id": next_id,
        "previous_context": prev_ctx,
        "next_context": next_ctx,
        "transcript_lines": transcript_lines,
        # Campos reservados para planejamento visual futuro (vindos do Claude)
        "narrative_role": "",
        "visual_intent": "",
        "subject": "",
        "action": "",
        "object": "",
        "environment": "",
        "shot_type": "",
        "energy": "",
        "emotion": "",
        "visual_priority": 0,
        # Estrutura para plano de busca futuro
        "search_strategies": []
    }


class ScenePlanningContext:
    """
    Contexto completo de planejamento visual para uma cena.
    Reúne todas as informações disponíveis no pipeline.

    Uso futuro (após integração com Claude):
        context.batch -> lista de ScenePlanningContext por cena
        context.full_script -> roteiro completo
        context.scenes -> todas as cenas
    """

    def __init__(self, project_name: str):
        self.project_name = project_name
        self.project_dir = PROJETOS_DIR / project_name

        # Carrega fontes de verdade (uma vez, em memória)
        self._roteiro = _carregar_roteiro(self.project_dir)
        self._cenas_raw = _carregar_cenas(self.project_dir)

        # Monta cenas normalizadas
        self._cenas = []
        for idx, cena in enumerate(self._cenas_raw):
            self._cenas.append(_normalizar_cena(cena, self._cenas_raw, idx, self._roteiro["texto"]))

    @property
    def full_script(self) -> str:
        """Roteiro completo."""
        return self._roteiro.get("texto", "")

    @property
    def duration_total(self) -> float:
        """Duração total do roteiro em segundos."""
        return self._roteiro.get("duration", 0)

    @property
    def language(self) -> str:
        """Idioma detectado."""
        return self._roteiro.get("language", "pt")

    @property
    def segment_count(self) -> int:
        """Total de segmentos na transcrição."""
        return self._roteiro.get("segment_count", 0)

    @property
    def cenas(self) -> list:
        """Lista de cenas normalizadas (ScenePlanningContext por cena)."""
        return self._cenas

    @property
    def cenas_count(self) -> int:
        """Total de cenas."""
        return len(self._cenas)

    def get_cena(self, scene_id: int) -> Optional[dict]:
        """Retorna uma cena específica pelo ID."""
        for c in self._cenas:
            if c["scene_id"] == scene_id:
                return c
        return None

    def build_batch(self) -> dict:
        """
        Monta o payload completo para processamento batch.
        Retorna um dict com:
          - full_script: roteiro completo
          - scenes: lista de cenas normalizadas
          - metadata: duração total, idioma, etc.
        """
        return {
            "full_script": self.full_script,
            "duration_total": self.duration_total,
            "language": self.language,
            "segment_count": self.segment_count,
            "scenes": self._cenas
        }

    def __repr__(self) -> str:
        return (f"ScenePlanningContext(project={self.project_name}, "
                f"cenas={self.cenas_count}, duracao={self.duration_total:.1f}s)")