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

    ANTIGRAVITY (eficiência): antes lia e parseava o arquivo INTEIRO (que pode
    passar de 50MB / centenas de milhares de linhas) para devolver só o tail —
    o api_criar_projeto chamava com linhas=200000 e o polling a cada 500ms,
    gerando a 'lentidão tipo travamento' intermitente. Agora lê apenas o
    FINAL do arquivo (tail progressivo), crescendo o bloco até obter `linhas`.
    """
    if not EVENTS_FILE.exists():
        return []

    try:
        tamanho = EVENTS_FILE.stat().st_size
    except Exception:
        return []
    if tamanho <= 0:
        return []

    # Estimativa de bytes por linha (eventos costumam ter 100-300 chars UTF-8)
    bytes_por_linha = 220
    bloco_inicial = max(16 * 1024, linhas * bytes_por_linha)

    eventos = []
    offset_inicio = 0
    bloco = bloco_inicial
    for _tentativa in range(6):  # cresce exponencialmente até cobrir
        offset_inicio = max(0, tamanho - bloco)
        try:
            with open(EVENTS_FILE, "rb") as f:
                f.seek(offset_inicio)
                dados = f.read()
        except Exception:
            return []
        texto = dados.decode("utf-8", errors="replace")
        linhas_texto = texto.splitlines()
        if offset_inicio > 0:
            linhas_texto = linhas_texto[1:]  # 1ª linha pode vir incompleta

        eventos = []
        for line in linhas_texto:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
                if categoria and categoria != "TODOS":
                    if evt.get("category") != categoria:
                        continue
                eventos.append(evt)
            except (json.JSONDecodeError, ValueError):
                continue

        # Suficiente OU já cobre o arquivo todo
        if len(eventos) >= linhas or offset_inicio <= 0:
            break
        bloco *= 4

    return eventos[-linhas:]


def contar_linhas_eventos() -> int:
    """Conta as linhas do arquivo de eventos de forma eficiente (leitura em bloco).

    ANTIGRAVITY: usado pelo api_criar_projeto para calcular o índice inicial do
    polling sem ler/parsear o arquivo inteiro via ler_eventos(200000).
    """
    if not EVENTS_FILE.exists():
        return 0
    try:
        tamanho = EVENTS_FILE.stat().st_size
    except Exception:
        return 0
    if tamanho <= 0:
        return 0
    total = 0
    try:
        with open(EVENTS_FILE, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)  # 1MB por vez
                if not chunk:
                    break
                total += chunk.count(b"\n")
    except Exception:
        return 0
    return total


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