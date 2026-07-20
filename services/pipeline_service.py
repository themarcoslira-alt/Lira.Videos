"""
pipeline_service.py — Camada intermediária entre GUI e serviços.
GUI nunca importa main.py diretamente.
"""
import json
from pathlib import Path
from typing import Optional
from config import PROJETOS_DIR, PIPELINE_STEPS


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

    def criar_projeto(self, nome: str) -> dict:
        """Cria um novo projeto."""
        project_dir = PROJETOS_DIR / nome
        if project_dir.exists():
            return {"success": False, "error": f"Projeto '{nome}' já existe"}

        project_dir.mkdir(parents=True)
        self.project_name = nome

        # Salva metadados
        meta = {
            "name": nome,
            "steps": {},
            "created": str(Path(nome).stat().st_mtime) if Path.exists else ""
        }

        # Cria diretórios internos
        (project_dir / "input").mkdir(exist_ok=True)

        self._salvar_meta(meta)
        return {"success": True, "project": nome}

    def _salvar_meta(self, meta: dict):
        """Salva metadados do projeto."""
        meta_file = PROJETOS_DIR / self.project_name / "meta.json"
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

    def _carregar_meta(self) -> dict:
        """Carrega metadados do projeto."""
        meta_file = PROJETOS_DIR / self.project_name / "meta.json"
        if meta_file.exists():
            with open(meta_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def listar_projetos(self) -> list:
        """Lista todos os projetos."""
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
        """Etapa 1: Transcrição."""
        from services.transcriber import transcrever
        result = transcrever(self.project_name, arquivo_video)
        if result.get("success"):
            self._atualizar_step("transcrever", "concluido", result)
        else:
            self._atualizar_step("transcrever", "erro", result)
        return result

    def gerar_cenas(self) -> dict:
        """Etapa 2: Gerar Cenas."""
        from services.scene_builder import gerar_cenas
        result = gerar_cenas(self.project_name)
        if result.get("success"):
            self._atualizar_step("gerar_cenas", "concluido", result)
        else:
            self._atualizar_step("gerar_cenas", "erro", result)
        return result

    def gerar_storyboard(self, usar_claude: bool = True) -> dict:
        """Etapa 3: Storyboard/B-roll."""
        from services.broll_director import gerar_storyboard
        result = gerar_storyboard(self.project_name, usar_claude)
        if result.get("success"):
            self._atualizar_step("storyboard_broll", "concluido", result)
        else:
            self._atualizar_step("storyboard_broll", "erro", result)
        return result

    def gerar_queries(self) -> dict:
        """Etapa 3.5: Gerar queries (parte do storyboard)."""
        from services.query_generator import gerar_queries
        result = gerar_queries(self.project_name)
        return result

    def buscar_midias(self) -> dict:
        """Etapa 4: Buscar Mídias."""
        from services.media_search import buscar_midias_projeto
        result = buscar_midias_projeto(self.project_name)
        if result.get("success"):
            self._atualizar_step("buscar_midias", "concluido", result)
        else:
            self._atualizar_step("buscar_midias", "erro", result)
        return result

    def renderizar(self) -> dict:
        """Etapa 5: Renderizar."""
        from services.video_builder import construir_video
        from services.video_encoder import renderizar_video

        # Prepara construção
        build_result = construir_video(self.project_name)
        if not build_result.get("success"):
            self._atualizar_step("renderizar", "erro", build_result)
            return build_result

        if not build_result.get("arquivos_video"):
            self._atualizar_step("renderizar", "erro",
                               {"error": "Nenhum arquivo de vídeo para renderizar"})
            return {"success": False, "error": "Nenhum arquivo de vídeo"}

        arquivo_audio = build_result.get("arquivo_audio")
        if not arquivo_audio:
            self._atualizar_step("renderizar", "erro",
                               {"error": "Nenhum arquivo de áudio encontrado"})
            return {"success": False, "error": "Nenhum áudio"}

        # Renderiza
        result = renderizar_video(
            build_result["arquivos_video"],
            arquivo_audio,
            self.project_name
        )

        if result.get("success"):
            self._atualizar_step("renderizar", "concluido", result)
        else:
            self._atualizar_step("renderizar", "erro", result)

        return result

    def executar_pipeline_completo(self, arquivo_video: str) -> dict:
        """
        Executa pipeline completo (5 etapas em sequência).
        """
        self.running = True
        results = {}

        # Etapa 1
        if self.cancelled:
            return self._finalizar_pipeline(results)
        results["transcrever"] = self.transcrever(arquivo_video)
        if not results["transcrever"].get("success"):
            self.running = False
            return self._finalizar_pipeline(results)

        # Etapa 2
        if self.cancelled:
            return self._finalizar_pipeline(results)
        results["gerar_cenas"] = self.gerar_cenas()
        if not results["gerar_cenas"].get("success"):
            self.running = False
            return self._finalizar_pipeline(results)

        # Etapa 3
        if self.cancelled:
            return self._finalizar_pipeline(results)
        results["storyboard_broll"] = self.gerar_storyboard()
        if not results["storyboard_broll"].get("success"):
            self.running = False
            return self._finalizar_pipeline(results)

        # Queries
        results["gerar_queries"] = self.gerar_queries()

        # Etapa 4
        if self.cancelled:
            return self._finalizar_pipeline(results)
        results["buscar_midias"] = self.buscar_midias()

        # Etapa 5
        if self.cancelled:
            return self._finalizar_pipeline(results)
        results["renderizar"] = self.renderizar()

        self.running = False
        return self._finalizar_pipeline(results)

    def _finalizar_pipeline(self, results: dict) -> dict:
        """Monta resultado final do pipeline."""
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
        """Atualiza status de uma etapa no meta.json."""
        meta = self._carregar_meta()
        if "steps" not in meta:
            meta["steps"] = {}
        meta["steps"][step] = {
            "status": status,
            "details": details
        }
        self._salvar_meta(meta)

    def get_step_status(self, step: str) -> Optional[dict]:
        """Retorna status de uma etapa específica."""
        meta = self._carregar_meta()
        return meta.get("steps", {}).get(step)

    def cancelar(self):
        """Cancela a execução do pipeline."""
        self.cancelled = True
        self.running = False

    def pausar(self):
        """Pausa a execução do pipeline."""
        self.paused = True

    def continuar(self):
        """Continua a execução do pipeline."""
        self.paused = False