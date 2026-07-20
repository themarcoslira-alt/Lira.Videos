"""
log_tools.py — Ferramentas MCP para logging e relatórios
2 ferramentas: log_event, session_report
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from config import LOGS_DIR


EVENTS_FILE = LOGS_DIR / "events.jsonl"


def _ensure_logs():
    """Garante que o arquivo de log existe."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    if not EVENTS_FILE.exists():
        EVENTS_FILE.touch()


def log_event(level: str, category: str, event: str, message: str,
              details: dict = None) -> dict:
    """
    Registra um evento no arquivo de log events.jsonl.
    levels: INFO, WARN, ERROR
    categories: UI_CLICK, UI_ERROR, PIPELINE_STEP, PIPELINE_ERROR,
                RENDER_PROGRESS, MEDIA_FETCH, SYSTEM
    """
    _ensure_logs()

    entry = {
        "ts": datetime.now().isoformat(),
        "level": level.upper(),
        "category": category.upper(),
        "event": event,
        "message": message,
        "details": details or {}
    }

    try:
        with open(EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


def session_report(minutes: int = 120) -> dict:
    """
    Gera relatório markdown da sessão atual.
    Resumo: erros, última execução de pipeline, cliques de UI com erro.
    """
    _ensure_logs()

    cutoff = datetime.now() - timedelta(minutes=minutes)
    eventos = []

    try:
        with open(EVENTS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        evt = json.loads(line)
                        ts = datetime.fromisoformat(evt["ts"])
                        if ts >= cutoff:
                            eventos.append(evt)
                    except (json.JSONDecodeError, ValueError):
                        continue
    except Exception:
        pass

    if not eventos:
        return {
            "success": True,
            "report": "Nenhum evento encontrado no período.",
            "event_count": 0
        }

    # Estatísticas
    error_count = sum(1 for e in eventos if e["level"] == "ERROR")
    warn_count = sum(1 for e in eventos if e["level"] == "WARN")
    pipeline_events = [e for e in eventos if e["category"] == "PIPELINE_STEP"]
    ui_errors = [e for e in eventos if e["category"] == "UI_ERROR"]
    last_pipeline = pipeline_events[-1] if pipeline_events else None

    linhas = []
    linhas.append(f"# Relatório de Sessão ({minutes} min)")
    linhas.append("")
    linhas.append(f"**Período:** {cutoff.isoformat()} até {datetime.now().isoformat()}")
    linhas.append(f"**Total de eventos:** {len(eventos)}")
    linhas.append(f"**Erros:** {error_count}")
    linhas.append(f"**Alertas:** {warn_count}")
    linhas.append("")

    if last_pipeline:
        linhas.append("## Última execução de pipeline")
        linhas.append(f"- Evento: {last_pipeline.get('event', 'N/A')}")
        linhas.append(f"- Mensagem: {last_pipeline.get('message', 'N/A')}")
        linhas.append("")

    if ui_errors:
        linhas.append("## Erros de UI")
        for e in ui_errors[:5]:
            linhas.append(f"- {e.get('message', 'N/A')} ({e.get('ts', '')})")
        linhas.append("")

    linhas.append("## Eventos por categoria")
    cats = {}
    for e in eventos:
        cat = e["category"]
        cats[cat] = cats.get(cat, 0) + 1
    for cat, count in sorted(cats.items()):
        linhas.append(f"- {cat}: {count}")
    linhas.append("")

    linhas.append("## Eventos recentes (últimos 10)")
    for e in eventos[-10:]:
        linhas.append(f"- [{e['ts']}] {e['level']} {e['category']}: {e['message']}")

    report = "\n".join(linhas)

    return {
        "success": True,
        "report": report,
        "event_count": len(eventos),
        "error_count": error_count
    }