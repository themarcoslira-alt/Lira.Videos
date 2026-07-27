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
        project_dir = PROJETOS_DIR / nome
        if project_dir.exists():
            return {"success": False, "error": f"Projeto '{nome}' já existe"}

        project_dir.mkdir(parents=True)
        self.project_name = nome

        meta = {
            "name": nome,
            "steps": {},
            "arquivo_audio": arquivo_audio,
            "created": datetime.now().isoformat()
        }

        (project_dir / "input").mkdir(exist_ok=True)

        self._salvar_meta(meta)
        self._salvar_log_projeto("projeto", "criado", {"nome": nome, "audio": arquivo_audio})
        return {"success": True, "project": nome}

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
                    with open(meta_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    projetos.append(meta)
                else:
                    projetos.append({"name": p.name, "steps": {}})
        return projetos

    def transcrever(self, arquivo_video: str) -> dict:
        from services.transcriber import transcrever
        self._notify(0, "andamento", "Transcrevendo áudio...")
        import shutil
        from pathlib import Path
        from config import PROJETOS_DIR
        audio_src = Path(arquivo_video)
        # Copia o audio com o mesmo nome do projeto para facilitar identificacao
        audio_dst = PROJETOS_DIR / self.project_name / f"{self.project_name}{audio_src.suffix}"
        if not audio_dst.exists():
            shutil.copy2(str(audio_src), str(audio_dst))
        # Salva o caminho no meta.json para o pipeline sempre encontrar
        meta = self._carregar_meta()
        meta["arquivo_audio"] = str(audio_dst)
        self._salvar_meta(meta)
        self._notify(0, "andamento", "Transcrevendo audio (modelo faster-whisper)...")
        result = transcrever(self.project_name, arquivo_video)
        if result.get("success"):
            self._atualizar_step("transcrever", "concluido", result)
            self._salvar_log_projeto("transcrever", "concluido", result)
            self._notify(0, "concluido",
                         f"Transcrição: {result.get('segments', 0)} segmentos em {result.get('duration', 0):.0f}s")
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