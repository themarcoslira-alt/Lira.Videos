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
            cwd=str(BASE_DIR)
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
                    result["segmentos"] = data.get("segments", [])
                    result["segments"] = data.get("segment_count", result.get("segments", 0))
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