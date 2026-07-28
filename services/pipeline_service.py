"""
pipeline_service.py — Camada intermediária entre GUI e serviços.
GUI nunca importa main.py diretamente.
"""
import json
from pathlib import Path
from datetime import datetime
from typing import Optional
import os
from config import PROJETOS_DIR, PIPELINE_STEPS


def calcular_duracao_total(arquivos_video: list) -> float:
    """Calcula duracao total estimada de uma lista de videos via ffprobe."""
    total = 0.0
    from config import FFPROBE_PATH
    import subprocess, json
    for arq in arquivos_video:
        try:
            r = subprocess.run(
                [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", str(arq)],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode == 0:
                data = json.loads(r.stdout)
                dur = float(data.get("format", {}).get("duration", 0))
                total += dur
        except Exception:
            pass
    return total


def verificar_e_retomar_se_necessario(project_name: str) -> dict:
    """
    Verifica se a transcricao do projeto esta completa.
    Retorna: {"transcricao_completa": bool, "project": str, "meta": dict}
    """
    meta_file = PROJETOS_DIR / project_name / "meta.json"
    meta = {}
    transcricao_completa = False

    if meta_file.exists():
        try:
            meta = json.loads(open(str(meta_file), "r", encoding="utf-8").read())
            transcricao_completa = meta.get("transcricao_completa", False)
        except Exception:
            pass

    # Fallback: verifica se o arquivo JSON de transcricao existe
    if not transcricao_completa:
        transcricao_json = PROJETOS_DIR / project_name / "roteiro_transcricao.json"
        if transcricao_json.exists():
            try:
                data = json.loads(open(str(transcricao_json), "r", encoding="utf-8").read())
                if data.get("segment_count", 0) > 0:
                    transcricao_completa = True
                    # Atualiza metadata
                    meta["transcricao_completa"] = True
                    with open(str(meta_file), "w", encoding="utf-8") as f:
                        json.dump(meta, f, indent=2, ensure_ascii=False)
            except Exception:
                pass

    return {
        "transcricao_completa": transcricao_completa,
        "project": project_name,
        "meta": meta
    }


class PipelineService:
    """Serviço de pipeline — gerencia a execução das 5 etapas."""

    def __init__(self):
        self.project_name = None
        self.current_step = 0
        self.running = False
        self.paused = False
        self.cancelled = False
        self._step_results = {}
        self._biblioteca_reuse_count = 0
        self._on_progress = None  # callback: fn(step_index, status, message)

    def set_progress_callback(self, callback):
        """Define callback para atualização de progresso em tempo real."""
        self._on_progress = callback

    def _notify(self, step_index: int, status: str, message: str = ""):
        """Dispara callback de progresso se definido."""
        if self._on_progress:
            self._on_progress(step_index, status, message)

    def _salvar_log_projeto(self, etapa: str, status: str, detalhes: dict):
        """Salva log no arquivo do projeto."""
        if not self.project_name:
            return
        log_file = PROJETOS_DIR / self.project_name / "logs.json"
        entrada = {
            "ts": datetime.now().isoformat(sep=" ", timespec="seconds"),
            "etapa": etapa,
            "status": status,
            "detalhes": detalhes
        }
        entradas = []
        if log_file.exists():
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    entradas = json.load(f)
            except Exception:
                entradas = []
        entradas.append(entrada)
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(entradas, f, indent=2, ensure_ascii=False)

    def criar_projeto(self, nome: str, arquivo_audio: str = "") -> dict:
        """Cria um novo projeto e já seleciona."""
        from services.video_encoder import sanitizar_nome_arquivo
        # Sanitiza o nome para usar como nome de diretorio (sem apostrofos, etc.)
        nome_projeto = sanitizar_nome_arquivo(nome)
        project_dir = PROJETOS_DIR / nome_projeto
        if project_dir.exists():
            return {"success": False, "error": f"Projeto '{nome}' já existe"}

        project_dir.mkdir(parents=True)
        self.project_name = nome_projeto  # nome sanitizado usado em todos os caminhos

        meta = {
            "name": nome_projeto,
            "display_name": nome,  # nome original preservado para exibicao na GUI
            "steps": {},
            "arquivo_audio": arquivo_audio,
            "created": datetime.now().isoformat(),
            "transcricao_completa": False
        }

        (project_dir / "input").mkdir(exist_ok=True)

        self._salvar_meta(meta)
        self._salvar_log_projeto("projeto", "criado", {"nome": nome_projeto, "audio": arquivo_audio})
        return {"success": True, "project": nome_projeto}

    def _salvar_meta(self, meta: dict):
        meta_file = PROJETOS_DIR / self.project_name / "meta.json"
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

    def _carregar_meta(self) -> dict:
        meta_file = PROJETOS_DIR / self.project_name / "meta.json"
        if meta_file.exists():
            with open(meta_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def listar_projetos(self) -> list:
        projetos = []
        for p in PROJETOS_DIR.iterdir():
            if p.is_dir():
                meta_file = p / "meta.json"
                if meta_file.exists():
                    try:
                        with open(meta_file, "r", encoding="utf-8") as f:
                            meta = json.load(f)
                        projetos.append(meta)
                    except Exception:
                        projetos.append({"name": p.name, "steps": {}})
                else:
                    projetos.append({"name": p.name, "steps": {}})
        return projetos

    def transcrever(self, arquivo_video: str) -> dict:
        import shutil, subprocess, json as _json, sys
        from pathlib import Path
        from config import PROJETOS_DIR
        from services.event_logger import log_event
        
        # Captura TODA excecao em arquivo de log para diagnostico
        try:
            return self._transcrever_interno(arquivo_video)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            log_event("TRANSCRIBE", f"EXCECAO NAO CAPTURADA em transcrever: {tb[:500]}", level="error")
            try:
                with open("logs/crash_log.txt", "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.now()}] CRASH em transcrever({arquivo_video}):\n{tb}\n")
            except:
                pass
            return {"success": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}

    def _transcrever_interno(self, arquivo_video: str) -> dict:
        import shutil, json as _json, sys
        from pathlib import Path
        from config import PROJETOS_DIR, FFPROBE_PATH
        from services.event_logger import log_event

        log_event("CHECKPOINT", "_transcrever_interno iniciado", level="info")

        # Calcula duracao total do audio para mostrar no log inicial
        duracao_seg = 0
        if FFPROBE_PATH:
            try:
                import subprocess as _sp
                r = _sp.run([FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
                             "-of", "json", arquivo_video],
                            capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    data = _json.loads(r.stdout)
                    duracao_seg = int(float(data.get("format", {}).get("duration", 0)))
            except Exception:
                pass

        self._notify(0, "andamento", f"Transcrevendo áudio: {Path(arquivo_video).name} ({duracao_seg}s)")
        audio_src = Path(arquivo_video)
        audio_dst = PROJETOS_DIR / self.project_name / f"{self.project_name}{audio_src.suffix}"
        if not audio_dst.exists():
            shutil.copy2(str(audio_src), str(audio_dst))
        meta = self._carregar_meta()
        meta["arquivo_audio"] = str(audio_dst)
        self._salvar_meta(meta)
        self._notify(0, "andamento", "Transcrevendo audio via subprocesso isolado...")

        # Delega toda a lógica de subprocesso para services.transcriber.transcrever()
        from services.transcriber import transcrever as _tc
        result = _tc(self.project_name, str(audio_dst))

        if result.get("success"):
            self._atualizar_step("transcrever", "concluido", result)
            self._salvar_log_projeto("transcrever", "concluido", result)
            self._notify(0, "concluido",
                         f"Transcrição: {result.get('segments', 0)} segmentos em {result.get('duration', 0):.0f}s")

            # --- CORTE DE SILENCIO ---
            self._notify(0, "andamento", "Removendo silencios do audio...")
            from services.audio_processor import cortar_silencio
            from services.video_encoder import sanitizar_nome_arquivo

            # Carrega segmentos do transcricao.json para obter timestamps
            transcricao_json = PROJETOS_DIR / self.project_name / "roteiro_transcricao.json"
            segmentos = result.get("segmentos", [])
            if not segmentos and transcricao_json.exists():
                try:
                    data = _json.loads(open(str(transcricao_json), "r", encoding="utf-8").read())
                    segmentos = data.get("segments", [])
                except Exception:
                    pass

            # Busca o audio copiado para o projeto
            audio_orig = str(PROJETOS_DIR / self.project_name / f"{self.project_name}.mp3")
            if not Path(audio_orig).exists():
                audio_orig = str(PROJETOS_DIR / self.project_name / f"{self.project_name}.mp4")
            if not Path(audio_orig).exists():
                audio_orig = arquivo_video  # fallback

            safe_name = sanitizar_nome_arquivo(self.project_name)
            audio_saida = str(PROJETOS_DIR / self.project_name / f"{safe_name}_no_silence.mp3")

            if Path(audio_orig).exists() and segmentos:
                audio_processado, mapeamento = cortar_silencio(
                    segmentos, audio_orig, audio_saida
                )
                # Salva mapeamento para uso posterior
                if mapeamento:
                    try:
                        import json as _json
                        map_file = PROJETOS_DIR / self.project_name / "timestamp_map.json"
                        with open(str(map_file), "w", encoding="utf-8") as f:
                            _json.dump(mapeamento, f, indent=2)
                    except Exception:
                        pass
            else:
                self._notify(0, "andamento", "Pulando corte de silencio (sem segmentos ou audio)", level="warn")
        else:
            self._atualizar_step("transcrever", "erro", result)
            self._salvar_log_projeto("transcrever", "erro", result)
            self._notify(0, "erro", f"Transcrição falhou: {result.get('error', '')}")
        return result

    def gerar_cenas(self) -> dict:
        from services.scene_builder import gerar_cenas
        self._notify(1, "andamento", "Dividindo roteiro em cenas...")
        result = gerar_cenas(self.project_name)
        if result.get("success"):
            self._atualizar_step("gerar_cenas", "concluido", result)
            self._salvar_log_projeto("cenas", "concluido", result)
            self._notify(1, "concluido", f"Cenas: {result.get('cenas_count', 0)} cenas geradas")
        else:
            self._atualizar_step("gerar_cenas", "erro", result)
            self._salvar_log_projeto("cenas", "erro", result)
            self._notify(1, "erro", f"Cenas falhou: {result.get('error', '')}")
        return result

    def gerar_storyboard(self, usar_claude: bool = True) -> dict:
        from services.broll_director import gerar_storyboard
        self._notify(2, "andamento", "Gerando storyboard e B-roll...")
        result = gerar_storyboard(self.project_name, usar_claude)
        if result.get("success"):
            self._atualizar_step("storyboard_broll", "concluido", result)
            self._salvar_log_projeto("storyboard", "concluido", result)
            self._notify(2, "concluido",
                         f"Storyboard: {result.get('cenas_count', 0)} cenas (camada: {result.get('camada', 'local')})")
        else:
            self._atualizar_step("storyboard_broll", "erro", result)
            self._salvar_log_projeto("storyboard", "erro", result)
            self._notify(2, "erro", f"Storyboard falhou: {result.get('error', '')}")
        return result

    def gerar_queries(self) -> dict:
        from services.query_generator import gerar_queries
        result = gerar_queries(self.project_name)
        return result

    def buscar_midias(self) -> dict:
        from services.media_search import buscar_midias_projeto, set_callback
        # Configura callback para progresso detalhado em tempo real
        set_callback(self._on_progress)
        self._notify(3, "andamento", "Buscando midias (Pexels, Pixabay, Unsplash)...")
        result = buscar_midias_projeto(self.project_name)
        # Limpa callback apos conclusao
        set_callback(None)
        if result.get("success"):
            self._atualizar_step("buscar_midias", "concluido", result)
            self._salvar_log_projeto("midias", "concluido", result)
            self._notify(3, "concluido",
                         f"Mídias: {result.get('green', 0)} green, {result.get('yellow', 0)} yellow, "
                         f"{result.get('needs_media', 0)} pendentes")
        else:
            self._atualizar_step("buscar_midias", "erro", result)
            self._salvar_log_projeto("midias", "erro", result)
            self._notify(3, "erro", f"Busca de mídias falhou: {result.get('error', '')}")
        return result

    def renderizar(self) -> dict:
        from services.video_builder import construir_video
        from services.video_encoder import renderizar_video
        self._notify(4, "andamento", "Renderizando vídeo final...")

        build_result = construir_video(self.project_name)
        from services.event_logger import log_event
        qtd = len(build_result.get("arquivos_video", []))
        audio_path = build_result.get("arquivo_audio", "NENHUM")
        log_event("RENDER", f"construir_video: {qtd} cenas processadas, audio={audio_path}", level="info")
        if not build_result.get("success"):
            self._atualizar_step("renderizar", "erro", build_result)
            self._salvar_log_projeto("render", "erro", build_result)
            self._notify(4, "erro", f"Render falhou: {build_result.get('error', '')}")
            return build_result

        if not build_result.get("arquivos_video"):
            self._atualizar_step("renderizar", "erro",
                               {"error": "Nenhum arquivo de vídeo para renderizar"})
            self._notify(4, "erro", "Nenhum arquivo de vídeo para renderizar")
            return {"success": False, "error": "Nenhum arquivo de vídeo"}

        arquivo_audio = build_result.get("arquivo_audio")
        if not arquivo_audio:
            self._atualizar_step("renderizar", "erro",
                               {"error": "Nenhum arquivo de áudio encontrado"})
            self._notify(4, "erro", "Nenhum arquivo de áudio encontrado")
            return {"success": False, "error": "Nenhum áudio"}

        from services.event_logger import log_event as log_global
        log_global("RENDER", f"Iniciando renderizacao com h264_amf: {len(build_result['arquivos_video'])} clipes")

        result = renderizar_video(
            build_result["arquivos_video"],
            arquivo_audio,
            self.project_name
        )

        if result.get("success"):
            self._atualizar_step("renderizar", "concluido", result)
            self._salvar_log_projeto("render", "concluido", result)
            self._notify(4, "concluido",
                         f"Renderizado: {result.get('arquivo', '')} ({result.get('tamanho', 0)//1024} KB)")
        else:
            self._atualizar_step("renderizar", "erro", result)
            self._salvar_log_projeto("render", "erro", result)
            self._notify(4, "erro", f"Render falhou: {result.get('error', '')}")

        return result

    def executar_pipeline_completo(self, arquivo_audio: str) -> dict:
        """
        Executa pipeline completo (5 etapas em sequência) com progresso.
        """
        self.running = True
        self.cancelled = False
        results = {}

        # Etapa 1 - Transcrição
        if self.cancelled:
            return self._finalizar_pipeline(results)
        self._notify(0, "andamento", "Transcrevendo áudio...")
        results["transcrever"] = self.transcrever(arquivo_audio)
        if not results["transcrever"].get("success"):
            self.running = False
            self._notify(0, "erro", f"Falhou: {results['transcrever'].get('error', '')}")
            return self._finalizar_pipeline(results)

        # Etapa 2 - Cenas
        if self.cancelled:
            return self._finalizar_pipeline(results)
        self._notify(1, "andamento", "Gerando cenas...")
        results["gerar_cenas"] = self.gerar_cenas()
        if not results["gerar_cenas"].get("success"):
            self.running = False
            return self._finalizar_pipeline(results)

        # Etapa 3 - Storyboard
        if self.cancelled:
            return self._finalizar_pipeline(results)
        self._notify(2, "andamento", "Gerando storyboard...")
        results["storyboard_broll"] = self.gerar_storyboard()
        if not results["storyboard_broll"].get("success"):
            self.running = False
            return self._finalizar_pipeline(results)

        # Queries (parte do storyboard)
        results["gerar_queries"] = self.gerar_queries()

        # Etapa 4 - Mídias
        if self.cancelled:
            return self._finalizar_pipeline(results)
        self._notify(3, "andamento", "Buscando mídias...")
        results["buscar_midias"] = self.buscar_midias()

        # Etapa 5 - Render
        if self.cancelled:
            return self._finalizar_pipeline(results)
        self._notify(4, "andamento", "Renderizando...")
        results["renderizar"] = self.renderizar()

        self.running = False
        return self._finalizar_pipeline(results)

    def _finalizar_pipeline(self, results: dict) -> dict:
        self.running = False
        sucesso = all(
            r.get("success", False) for r in results.values()
        )
        return {
            "success": sucesso,
            "project": self.project_name,
            "results": results
        }

    def _atualizar_step(self, step: str, status: str, details: dict):
        meta = self._carregar_meta()
        if "steps" not in meta:
            meta["steps"] = {}
        meta["steps"][step] = {
            "status": status,
            "details": details
        }
        self._salvar_meta(meta)

    def get_step_status(self, step: str) -> Optional[dict]:
        meta = self._carregar_meta()
        return meta.get("steps", {}).get(step)

    def get_logs_projeto(self) -> list:
        """Retorna logs salvos do projeto."""
        if not self.project_name:
            return []
        log_file = PROJETOS_DIR / self.project_name / "logs.json"
        if log_file.exists():
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def cancelar(self):
        self.cancelled = True
        self.running = False

    def pausar(self):
        self.paused = True

    def continuar(self):
        self.paused = False