"""
transcriber.py — Transcrição de áudio/vídeo com faster-whisper (subprocesso)
Armazena:
  - roteiro_transcricao.txt  (formato texto simples, compatibilidade)
  - roteiro_transcricao.json (segmentos estruturados com start/end/text)
"""
import os
from pathlib import Path
import json
from config import PROJETOS_DIR


def _termina_frase(texto: str) -> bool:
    """True se o texto termina com pontuação de fim de frase."""
    t = (texto or "").strip()
    if not t:
        return False
    return t.endswith((".", "!", "?", "…", '."', '!"', '?"', ".)", "!)", "?)"))


def _montar_frase(grupo: list) -> dict:
    """Junta um grupo de segmentos em uma única linha de transcrição."""
    inicio = grupo[0]
    fim = grupo[-1]
    texto = " ".join((s.get("text") or "").strip() for s in grupo).strip()
    mins_s, secs_s = int(inicio["start"] // 60), int(inicio["start"] % 60)
    return {
        "start": round(inicio["start"], 2),
        "end": round(fim["end"], 2),
        "text": texto,
        "timestamp": f"{mins_s:02d}:{secs_s:02d}",
    }


def _agrupar_em_frases(segmentos: list, pausa_max: float = 1.0,
                       duracao_max: float = 15.0) -> list:
    """
    Agrupa segmentos consecutivos do Whisper em frases completas de sentido.

    O grupo é fechado quando:
      - o último segmento termina com . ! ? …  (frase completa)
      - pausa entre segmentos > pausa_max segundos
      - duração acumulada >= duracao_max segundos (evita linhas longas demais)

    Cada grupo vira uma linha com o timestamp de início do primeiro segmento.
    """
    if not segmentos:
        return []
    frases = []
    grupo = []
    for seg in segmentos:
        if grupo:
            prev = grupo[-1]
            pausa = seg["start"] - prev["end"]
            acumulado = seg["end"] - grupo[0]["start"]
            if (_termina_frase(prev.get("text", ""))
                    or pausa > pausa_max
                    or acumulado >= duracao_max):
                frases.append(_montar_frase(grupo))
                grupo = []
        grupo.append(seg)
    if grupo:
        frases.append(_montar_frase(grupo))
    return frases


def transcrever(project_name: str, arquivo_video: str) -> dict:
    """
    Dispara subprocesso de transcricao isolado (evita segfault do ctranslate2).
    O subprocesso cria roteiro_transcricao.txt no diretorio do projeto.
    Retorna dict com resultado.
    """
    import subprocess as _subprocess
    import sys
    from services.event_logger import log_event
    from config import BASE_DIR

    log_event("TRANSCRIBE", f"Iniciando transcricao: {arquivo_video}", level="info")
    log_event("CHECKPOINT", "transcrever() iniciado", level="info")

    try:
        project_dir = PROJETOS_DIR / project_name
        project_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(project_dir / "roteiro_transcricao.txt")

        log_event("CHECKPOINT", "Lancando subprocesso de transcricao...", level="info")

        # Usa a venv .venv (Python 3.11.x) para transcricao isolada
        python_exe = str(BASE_DIR / ".venv" / "Scripts" / "python.exe")
        if not Path(python_exe).exists():
            python_exe = str(Path(sys.executable).parent / "python.exe")
            if not Path(python_exe).exists() or "pythonw" in python_exe.lower():
                python_exe = "python"

        log_event("TRANSCRIBE", f"Python para subprocesso: {python_exe}", level="info")

        # Subprocesso executa _transcrever_subprocesso.py diretamente
        script_path = str(BASE_DIR / "_transcrever_subprocesso.py")
        cmd = [python_exe, "-u", script_path, arquivo_video, project_name, output_path]

        log_event("TRANSCRIBE", f"Executando: {' '.join(cmd)}", level="info")

        proc = _subprocess.Popen(
            cmd,
            stdout=_subprocess.PIPE,
            stderr=_subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(BASE_DIR),
            creationflags=_subprocess.CREATE_NO_WINDOW  # janela CMD invisível no Windows
        )

        linhas = []
        for linha in proc.stdout:
            linha = linha.rstrip()
            if linha:
                linhas.append(linha)
                # Mostra no console da GUI
                if linha.startswith("[SEGMENTO]") or linha.startswith("[SUBPROCESSO]"):
                    log_event("TRANSCRIBE", linha)
                elif linha.startswith("[TRANSCREVENDO]"):
                    log_event("CHECKPOINT", linha[15:])

        proc.wait()
        stderr_output = proc.stderr.read()

        log_event("CHECKPOINT", f"Subprocesso terminou com codigo {proc.returncode}", level="info")

        if proc.returncode != 0:
            log_event("TRANSCRIBE", f"Subprocesso falhou (codigo {proc.returncode})", level="error")
            if stderr_output:
                log_event("TRANSCRIBE", f"Stderr: {stderr_output[:500]}", level="error")
            return {"success": False, "error": f"Subprocesso retornou codigo {proc.returncode}"}

        # Extrair linha JSON (ultima que comeca com "{")
        json_line = None
        for linha in reversed(linhas):
            if linha.strip().startswith("{"):
                json_line = linha.strip()
                break

        if not json_line:
            log_event("TRANSCRIBE", "Nenhuma linha JSON no stdout do subprocesso", level="error")
            return {"success": False, "error": "Nenhum JSON no stdout"}

        result = json.loads(json_line)

        if result.get("success"):
            # Salva JSON estruturado (fonte de verdade temporal)
            saida_json = project_dir / "roteiro_transcricao.json"
            if saida_json.exists():
                try:
                    data = json.loads(saida_json.read_text(encoding="utf-8"))
                    segmentos_brutos = data.get("segments", [])
                    # ITEM 8: agrupa segmentos consecutivos em frases completas de
                    # sentido (fim de frase .!? / pausa >1s / máx 15s por linha)
                    frases = _agrupar_em_frases(segmentos_brutos)
                    if frases:
                        novo_json = {
                            "segments": frases,
                            "segment_count": len(frases),
                            "duration": data.get("duration", frases[-1]["end"] if frases else 0),
                            "language": data.get("language", "pt"),
                            "fonte": "whisper+frases",
                        }
                        saida_json.write_text(
                            json.dumps(novo_json, indent=2, ensure_ascii=False),
                            encoding="utf-8")
                        linhas_txt = [f"[{f['timestamp']}] {f['text']}" for f in frases]
                        (project_dir / "roteiro_transcricao.txt").write_text(
                            "\n".join(linhas_txt), encoding="utf-8")
                        result["segmentos"] = frases
                        result["segments"] = len(frases)
                        result["texto"] = "\n".join(linhas_txt)
                        log_event("TRANSCRIBE",
                                  f"Segmentos agrupados em {len(frases)} frases completas",
                                  level="info")
                    else:
                        result["segmentos"] = segmentos_brutos
                        result["segments"] = data.get("segment_count", len(segmentos_brutos))
                except Exception:
                    pass

            # Marca transcricao como completa no meta.json do projeto
            meta_path = PROJETOS_DIR / project_name / "meta.json"
            try:
                if meta_path.exists():
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    meta["transcricao_completa"] = True
                    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

            log_event("TRANSCRIBE", f"Transcricao concluida: {result.get('segments', 0)} segmentos", level="info")
        else:
            log_event("TRANSCRIBE", f"Transcricao falhou: {result.get('error', '')}", level="error")

        return result

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log_event("TRANSCRIBE", f"Erro na transcricao: {tb[:300]}", level="error")
        try:
            with open("logs/crash_log.txt", "a", encoding="utf-8") as f:
                f.write(f"[{__import__('datetime').datetime.now()}] CRASH: {tb}\n")
        except Exception:
            pass
        return {"success": False, "error": str(e)}