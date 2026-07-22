"""
event_logger.py — Logger central do ULTRACUT3
Escreve eventos estruturados em logs/events.jsonl
Pode ser importado por qualquer módulo do pipeline ou GUI.
"""
import json
from datetime import datetime
from pathlib import Path
from config import LOGS_DIR


EVENTS_FILE = LOGS_DIR / "events.jsonl"


def log_event(category: str, message: str, level: str = "info",
              details: dict = None) -> dict:
    """
    Registra um evento no arquivo de log events.jsonl.
    levels: info, warn, error
    categories: PIPELINE, TRANSCRIBE, SCENES, STORYBOARD, MEDIA_FETCH,
                RENDER, UI, SYSTEM
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    entry = {
        "ts": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "level": level.upper(),
        "category": category.upper(),
        "message": message,
        "details": details or {}
    }

    try:
        with open(EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


def ler_eventos(linhas: int = 200, categoria: str = None) -> list:
    """
    Lê as últimas N linhas do arquivo de log.
    Se categoria for fornecida, filtra por ela.
    Retorna lista de dicts.
    """
    if not EVENTS_FILE.exists():
        return []

    eventos = []
    try:
        with open(EVENTS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        evt = json.loads(line)
                        if categoria and categoria != "TODOS":
                            if evt.get("category") != categoria:
                                continue
                        eventos.append(evt)
                    except (json.JSONDecodeError, ValueError):
                        continue
    except Exception:
        return []

    # Pega as últimas N linhas
    return eventos[-linhas:]


def listar_categorias() -> list:
    """Lista todas as categorias presentes no log."""
    categorias = set()
    if EVENTS_FILE.exists():
        try:
            with open(EVENTS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            evt = json.loads(line)
                            cat = evt.get("category")
                            if cat:
                                categorias.add(cat)
                        except Exception:
                            continue
        except Exception:
            pass
    return sorted(categorias)