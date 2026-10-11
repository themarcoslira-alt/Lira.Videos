"""
services/brain_maestro_v4.py — Brain Maestro v4.0 Central Orchestrator
======================================================================
Orquestrador mestre do Lira Studio 4.0.
Recebe áudio bruto (WAV/MP3) e coordena ponta a ponta 8 etapas atômicas:
  1. Validação de integridade de áudio (ffprobe)
  2. Transcrição e alinhamento temporal (Faster-Whisper + word_timestamps)
  3. Divisão de roteiro em cenas rítmicas (Scene Builder 3-6s)
  4. Inteligência Audiovisual Diretor v3.0 (Cota 8/92, Prompts DeepSeek, Animação)
  5. Refinamento de Story Cards (Badges pedagógicos ancorados por palavra)
  6. Produção e renderização paralela (Multi-conta Chrome CDP Flow)
  7. Sound Design, BGM e Ducking Inteligente (FFmpeg)
  8. Montagem e Exportação do Rascunho Nativo CapCut Desktop (Schema 360000)
"""

import os
import sys
import json
import time
import copy
import shutil
import hashlib
import logging
import threading
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

from config import BASE_DIR, PROJETOS_DIR, FFMPEG_PATH, FFPROBE_PATH
from services.event_logger import log_event

# 1. Transcrição e Cenas
from services.transcriber import transcrever
from services.scene_builder import gerar_cenas

# 2. Diretor Audiovisual Core v3
from services.brain_system_director import BrainSystemDirector

# 3. Story Cards
from services.story_card_detector_service import detect_story_cards
from services.story_card_prompt_builder import build_story_card_prompt

# 4. Orquestrador Paralelo Flow
from services.parallel_flow_orchestrator import OrchestradorParalelo, DivisorBlocos

# 5. Áudio, BGM e Ducking
from services.bgm_service import (
    analisar_perfil_musical_projeto,
    mixar_voz_e_musica_ffmpeg,
    vincular_trilha_projeto
)
from services.audio_processor import cortar_silencio

# 6. CapCut Desktop e Validação H.264
from capcut_draft_imagens import criar_draft_imagens, detectar_pasta_drafts
from services.capcut_library_service import CapCutLibraryService
from services.video_encoder import garantir_video_h264_compat

# 7. Persistência de Scene Plan
from services.scene_plan_service import (
    carregar_scene_plan,
    salvar_scene_plan,
    resolver_arquivo_cena
)


ETAPAS_PADRAO = [
    "validacao_audio",
    "whisper",
    "scene_builder",
    "brain_v3_core",
    "story_cards_refinement",
    "producao_flow",
    "audio_design",
    "capcut_draft"
]


class BrainMaestroV4:
    """
    Orquestrador Maestro do Lira Studio 4.0.
    Executa o fluxo completo a partir de áudio bruto com suporte a checkpoints atômicos,
    pausa/cancelamento por threading.Event e telemetria granular.
    """

    def __init__(
        self,
        projeto_id: str,
        logger: Optional[logging.Logger] = None,
        stop_event: Optional[threading.Event] = None
    ):
        self.projeto_id = str(projeto_id).strip()
        self.project_dir = PROJETOS_DIR / self.projeto_id
        self.logger = logger or logging.getLogger(f"BrainMaestro.{self.projeto_id}")
        self.stop_event = stop_event or threading.Event()

        self.job_id = f"maestro_{self.projeto_id}_{int(time.time())}"
        self.estado_atual = "IDLE"
        self.etapa_atual = ""
        self.etapas_concluidas: List[str] = []
        self._flow_orchestrator: Optional[OrchestradorParalelo] = None
        self._lock_progresso = threading.Lock()

        self.progresso: Dict[str, Dict[str, Any]] = self._inicializar_progresso()
        self.checkpoint_path = self.project_dir / "brain_maestro_checkpoint.json"

    # =========================================================================
    # TELEMETRIA E PROGRESSO
    # =========================================================================

    def _inicializar_progresso(self) -> Dict[str, Dict[str, Any]]:
        return {
            "validacao_audio":        {"status": "pendente", "progresso_pct": 0, "tempo_decorrido_s": 0.0, "detalhes": ""},
            "whisper":                {"status": "pendente", "progresso_pct": 0, "tempo_decorrido_s": 0.0, "detalhes": ""},
            "scene_builder":          {"status": "pendente", "progresso_pct": 0, "tempo_decorrido_s": 0.0, "detalhes": ""},
            "brain_v3_core":          {"status": "pendente", "progresso_pct": 0, "tempo_decorrido_s": 0.0, "detalhes": ""},
            "story_cards_refinement": {"status": "pendente", "progresso_pct": 0, "tempo_decorrido_s": 0.0, "detalhes": ""},
            "producao_flow":          {"status": "pendente", "progresso_pct": 0, "tempo_decorrido_s": 0.0, "detalhes": ""},
            "audio_design":           {"status": "pendente", "progresso_pct": 0, "tempo_decorrido_s": 0.0, "detalhes": ""},
            "capcut_draft":           {"status": "pendente", "progresso_pct": 0, "tempo_decorrido_s": 0.0, "detalhes": ""},
        }

    def _atualizar_progresso(self, etapa: str, **kwargs):
        with self._lock_progresso:
            if etapa in self.progresso:
                self.progresso[etapa].update(kwargs)

    def get_progresso(self) -> Dict[str, Any]:
        """Retorna snapshot imutável do estado e progresso para o endpoint de polling."""
        with self._lock_progresso:
            prog = copy.deepcopy(self.progresso)
            etapas_concluidas_count = sum(1 for v in prog.values() if v["status"] == "concluido")
            progresso_global = int((etapas_concluidas_count / max(1, len(prog))) * 100)
            return {
                "job_id": self.job_id,
                "projeto_id": self.projeto_id,
                "estado_atual": self.estado_atual,
                "etapa_atual": self.etapa_atual,
                "progresso_global_pct": progresso_global,
                "etapas_concluidas": list(self.etapas_concluidas),
                "sub_sistemas": prog
            }

    # =========================================================================
    # CHECKPOINTS ATÔMICOS E RETOMADA RESILIENTE
    # =========================================================================

    def _salvar_checkpoint(self, status: str = "EM_ANDAMENTO", **kwargs) -> bool:
        """Salva brain_maestro_checkpoint.json de forma atômica."""
        self.project_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_data = {
            "versao_schema": "4.0.0",
            "projeto_id": self.projeto_id,
            "job_id": self.job_id,
            "status": status,
            "etapa_atual": self.etapa_atual,
            "etapas_concluidas": list(self.etapas_concluidas),
            "timestamp_checkpoint": datetime.now().isoformat(),
            "progresso": self.get_progresso(),
            **kwargs
        }
        tmp_file = self.checkpoint_path.with_suffix(".json.tmp")
        try:
            tmp_file.write_text(json.dumps(checkpoint_data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(str(tmp_file), str(self.checkpoint_path))
            return True
        except Exception as e:
            self.logger.warning(f"Aviso ao salvar checkpoint do Maestro: {e}")
            return False

    def _carregar_checkpoint(self) -> Optional[Dict[str, Any]]:
        """Carrega checkpoint anterior se existir e for válido."""
        if not self.checkpoint_path.exists():
            return None
        try:
            return json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    # =========================================================================
    # CONTROLE DE INTERRUPÇÃO E CANCELAMENTO
    # =========================================================================

    def cancelar(self):
        """Dispara a solicitação de parada limpa do Maestro e de subprocessos ativos."""
        self.stop_event.set()
        self.estado_atual = "CANCELADO"
        log_event("MAESTRO", f"Solicitação de cancelamento recebida para '{self.projeto_id}'", level="warn")
        if self._flow_orchestrator and hasattr(self._flow_orchestrator, "parar_todos"):
            try:
                self._flow_orchestrator.parar_todos()
            except Exception:
                pass

    def _verificar_interrupcao(self, etapa_nome: str) -> bool:
        """True se a interrupção foi solicitada."""
        if self.stop_event.is_set():
            self.estado_atual = "CANCELADO"
            self._atualizar_progresso(etapa_nome, status="cancelado", detalhes="Interrompido por cancelamento.")
            self._salvar_checkpoint(status="CANCELADO", etapa_interrompida=etapa_nome)
            log_event("MAESTRO", f"Execução cancelada antes ou durante '{etapa_nome}' para '{self.projeto_id}'.", level="warn")
            return True
        return False

    # =========================================================================
    # STATE MACHINE: AS 8 ETAPAS DO MAESTRO
    # =========================================================================

    def _etapa_1_validar_audio(self, audio_path: str) -> Dict[str, Any]:
        """Etapa 1: Validação de existência e integridade do arquivo de áudio via ffprobe."""
        inicio = time.time()
        self.etapa_atual = "validacao_audio"
        self._atualizar_progresso("validacao_audio", status="executando", progresso_pct=20, detalhes="Inspecionando headers...")

        p = Path(audio_path)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"Arquivo de áudio não encontrado: {audio_path}")

        tamanho_bytes = p.stat().st_size
        if tamanho_bytes < 1024:
            raise ValueError(f"Arquivo de áudio excessivamente pequeno ({tamanho_bytes} bytes), possível corrupção.")

        duracao_s = 0.0
        sample_rate = 44100
        channels = 2
        codec = p.suffix.lower().replace(".", "")

        if FFPROBE_PATH and Path(FFPROBE_PATH).exists():
            cmd = [
                str(FFPROBE_PATH),
                "-v", "error",
                "-show_entries", "stream=codec_name,sample_rate,channels:format=duration",
                "-of", "json",
                str(p.resolve())
            ]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                if res.returncode == 0 and res.stdout:
                    info = json.loads(res.stdout)
                    fmt = info.get("format", {})
                    duracao_s = float(fmt.get("duration", 0.0))
                    streams = info.get("streams", [])
                    if streams:
                        audio_stream = next((s for s in streams if "sample_rate" in s), streams[0])
                        sample_rate = int(audio_stream.get("sample_rate", 44100))
                        channels = int(audio_stream.get("channels", 2))
                        codec = str(audio_stream.get("codec_name", codec))
            except Exception as e_probe:
                self.logger.warning(f"Aviso ao executar ffprobe: {e_probe}")

        if duracao_s <= 0.0:
            # Fallback de duração aproximada
            duracao_s = round(max(3.0, tamanho_bytes / (192 * 1024 / 8)), 2)

        if duracao_s < 2.0:
            raise ValueError(f"Áudio possui duração insuficiente ({duracao_s}s). Mínimo exigido: 2.0s.")

        duracao_exec = round(time.time() - inicio, 2)
        resultado = {
            "valido": True,
            "audio_path": str(p.resolve()),
            "duracao_s": duracao_s,
            "sample_rate": sample_rate,
            "channels": channels,
            "codec": codec,
            "tamanho_bytes": tamanho_bytes
        }

        self._atualizar_progresso("validacao_audio", status="concluido", progresso_pct=100, tempo_decorrido_s=duracao_exec, detalhes=f"{duracao_s}s, {sample_rate}Hz, codec {codec}")
        self.etapas_concluidas.append("validacao_audio")
        return resultado

    def _etapa_2_executar_whisper(self, audio_path: str) -> Dict[str, Any]:
        """Etapa 2: Transcrição automatizada Faster-Whisper gerando roteiro e word timestamps."""
        inicio = time.time()
        self.etapa_atual = "whisper"
        self._atualizar_progresso("whisper", status="executando", progresso_pct=10, detalhes="Transcrevendo fala...")

        res = transcrever(self.projeto_id, audio_path)
        if not res or not res.get("success"):
            raise RuntimeError(f"Falha na transcrição Faster-Whisper: {res.get('error') if res else 'Retorno vazio'}")

        roteiro_json_path = self.project_dir / "roteiro_transcricao.json"
        word_ts_path = self.project_dir / "word_timestamps.json"

        if not roteiro_json_path.exists():
            raise FileNotFoundError("roteiro_transcricao.json não foi gerado pelo transcritor.")

        total_segmentos = int(res.get("segments") or len(res.get("segmentos") or []))
        duracao_exec = round(time.time() - inicio, 2)

        self._atualizar_progresso("whisper", status="concluido", progresso_pct=100, tempo_decorrido_s=duracao_exec, detalhes=f"{total_segmentos} frases transcritas.")
        self.etapas_concluidas.append("whisper")
        return {
            "roteiro_json": str(roteiro_json_path),
            "word_timestamps": str(word_ts_path) if word_ts_path.exists() else "",
            "total_segmentos": total_segmentos
        }

    def _etapa_3_executar_scene_builder(self) -> Dict[str, Any]:
        """Etapa 3: Segmentação de roteiro em cenas rítmicas de 3 a 6 segundos."""
        inicio = time.time()
        self.etapa_atual = "scene_builder"
        self._atualizar_progresso("scene_builder", status="executando", progresso_pct=30, detalhes="Agrupando falas em janelas rítmicas...")

        res = gerar_cenas(self.projeto_id)
        if not res or not res.get("success"):
            raise RuntimeError(f"Falha no Scene Builder: {res.get('error') if res else 'Erro desconhecido'}")

        cenas_json_path = self.project_dir / "cenas.json"
        if not cenas_json_path.exists():
            raise FileNotFoundError("cenas.json não foi gerado pelo scene_builder.")

        cenas_data = json.loads(cenas_json_path.read_text(encoding="utf-8"))
        total_cenas = len(cenas_data) if isinstance(cenas_data, list) else len(cenas_data.get("cenas", []))

        duracao_exec = round(time.time() - inicio, 2)
        self._atualizar_progresso("scene_builder", status="concluido", progresso_pct=100, tempo_decorrido_s=duracao_exec, detalhes=f"{total_cenas} cenas construídas.")
        self.etapas_concluidas.append("scene_builder")
        return {
            "cenas_json": str(cenas_json_path),
            "total_cenas": total_cenas
        }

    def _etapa_4_executar_brain_v3_core(self, audio_path: str, avatar_meta: Optional[Dict[str, Any]], options: Dict[str, Any]) -> Dict[str, Any]:
        """Etapa 4: Executa as 5 fases canônicas do BrainSystemDirector (Cota 8/92, Prompts DeepSeek, Animação)."""
        inicio = time.time()
        self.etapa_atual = "brain_v3_core"
        self._atualizar_progresso("brain_v3_core", status="executando", progresso_pct=20, detalhes="Aplicando 12 inteligências audiovisuais...")

        cenas_path = self.project_dir / "cenas.json"
        wt_path = self.project_dir / "word_timestamps.json"
        roteiro_path = self.project_dir / "roteiro_transcricao.json"

        cenas_raw = json.loads(cenas_path.read_text(encoding="utf-8")) if cenas_path.exists() else []
        if isinstance(cenas_raw, dict):
            cenas_raw = cenas_raw.get("cenas", [])

        word_ts = []
        if wt_path.exists():
            try:
                wt_dados = json.loads(wt_path.read_text(encoding="utf-8"))
                word_ts = wt_dados.get("segments", []) if isinstance(wt_dados, dict) else wt_dados
            except Exception:
                pass

        roteiro_texto = ""
        duracao_total = 0.0
        if roteiro_path.exists():
            try:
                r_dados = json.loads(roteiro_path.read_text(encoding="utf-8"))
                roteiro_texto = " ".join(s.get("text", "") for s in r_dados.get("segments", []))
                duracao_total = float(r_dados.get("duration", 0.0))
            except Exception:
                pass

        avatar_nome = ""
        estilo_visual = options.get("estilo_visual", "photorealistic_cinematic")
        if avatar_meta:
            avatar_nome = avatar_meta.get("alias") or avatar_meta.get("nome") or ""
            if avatar_meta.get("estilo_visual"):
                estilo_visual = avatar_meta["estilo_visual"]

        entrada_consolidada = {
            "projeto_id": self.projeto_id,
            "roteiro_transcricao": {"text": roteiro_texto, "duration": duracao_total},
            "word_timestamps": word_ts,
            "cenas": cenas_raw,
            "audio_metadata": {
                "path": audio_path,
                "duracao_total": duracao_total,
                "silencio_table": []
            },
            "personagem": avatar_nome,
            "estilo_visual": estilo_visual
        }

        director = BrainSystemDirector(projeto_id=self.projeto_id, logger=self.logger)
        saida_brain = director.orquestar_producao_completa(entrada_consolidada=entrada_consolidada, options=options)

        duracao_exec = round(time.time() - inicio, 2)
        total_cenas = len(saida_brain.get("cenas", {}))
        self._atualizar_progresso("brain_v3_core", status="concluido", progresso_pct=100, tempo_decorrido_s=duracao_exec, detalhes=f"{total_cenas} cenas enriquecidas com cota 8/92.")
        self.etapas_concluidas.append("brain_v3_core")
        return saida_brain

    def _etapa_5_refinar_story_cards(self) -> Dict[str, Any]:
        """Etapa 5: Refinamento cirúrgico de Story Cards e alinhamento milimétrico na palavra-âncora."""
        inicio = time.time()
        self.etapa_atual = "story_cards_refinement"
        self._atualizar_progresso("story_cards_refinement", status="executando", progresso_pct=30, detalhes="Ancorando badges em word_timestamps...")

        plan = carregar_scene_plan(self.projeto_id)
        if not plan or not plan.get("cenas"):
            self._atualizar_progresso("story_cards_refinement", status="concluido", progresso_pct=100, detalhes="Nenhuma cena para refinar.")
            return {"cards_refinados": 0}

        cenas = plan["cenas"]
        wt_path = self.project_dir / "word_timestamps.json"
        flat_words = []
        if wt_path.exists():
            try:
                wt = json.loads(wt_path.read_text(encoding="utf-8"))
                segs = wt.get("segments", []) if isinstance(wt, dict) else wt
                for item in segs:
                    if isinstance(item, dict) and "words" in item:
                        flat_words.extend(item["words"])
                    elif isinstance(item, dict) and "w" in item:
                        flat_words.append(item)
            except Exception:
                pass

        card_markers = detect_story_cards(cenas)
        markers_by_id = {m["scene_id"]: m for m in card_markers}
        cards_refinados = 0

        for cena in cenas:
            cid = int(cena.get("id") or 0)
            marker = markers_by_id.get(cid)
            if marker:
                cards_refinados += 1
                frag = str(marker.get("narration_fragment", "")).lower()
                anchor_s = None
                anchor_e = None
                anchor_word = None

                if flat_words:
                    for wt_w in flat_words:
                        w_str = str(wt_w.get("w", "")).lower()
                        s_val = wt_w.get("s")
                        e_val = wt_w.get("e")
                        if s_val is not None and w_str in frag:
                            anchor_s = float(s_val)
                            anchor_e = float(e_val) if e_val is not None else anchor_s + 0.5
                            anchor_word = wt_w.get("w")
                            break

                cena["is_story_card"] = True
                cena["story_card_type"] = marker.get("story_card_type", "step_1")
                cena["story_card_style"] = marker.get("story_card_style", "step_intro")
                cena["story_card_prompt_imagem"] = build_story_card_prompt(marker)
                cfg = marker.get("story_card_config", {})
                cena["story_card_config"] = cfg

                start_ms = int(round(float(cena.get("tempo_inicio", 0.0)) * 1000))
                anchor_ms = int(round(anchor_s * 1000)) if anchor_s is not None else start_ms

                cena["editorial_style"] = {
                    "tier": 3,
                    "variant": marker.get("story_card_type"),
                    "badge": cfg.get("title", "STEP"),
                    "title": cfg.get("subtitle", ""),
                    "source": "brain_maestro_v4",
                    "anchor_word": anchor_word,
                    "timing_start_ms": anchor_ms,
                    "duration_s": cfg.get("duration_ms", 3000) / 1000.0
                }

        salvar_scene_plan(self.projeto_id, plan)
        duracao_exec = round(time.time() - inicio, 2)

        self._atualizar_progresso("story_cards_refinement", status="concluido", progresso_pct=100, tempo_decorrido_s=duracao_exec, detalhes=f"{cards_refinados} cards refinados e ancorados.")
        self.etapas_concluidas.append("story_cards_refinement")
        return {"cards_refinados": cards_refinados}

    def _etapa_6_orquestrar_producao_flow(self, options: Dict[str, Any]) -> Dict[str, Any]:
        """Etapa 6: Geração paralela de imagens/vídeos via Google Flow Chrome CDP multi-worker."""
        inicio = time.time()
        self.etapa_atual = "producao_flow"
        pular_flow = bool(options.get("pular_flow", False))

        if pular_flow:
            self._atualizar_progresso("producao_flow", status="concluido", progresso_pct=100, detalhes="Produção Flow pulada por configuração.")
            self.etapas_concluidas.append("producao_flow")
            return {"pulado": True, "cenas_produzidas": 0}

        num_contas = int(options.get("num_contas_flow", 3))
        self._atualizar_progresso("producao_flow", status="executando", progresso_pct=5, detalhes=f"Inicializando {num_contas} workers CDP...")

        self._flow_orchestrator = OrchestradorParalelo(projeto_id=self.projeto_id, num_contas=num_contas)
        res = self._flow_orchestrator.iniciar_fila_paralela(
            projeto_id=self.projeto_id,
            num_contas=num_contas,
            modo=options.get("modo_producao", "imagem")
        )

        if not res.get("success"):
            raise RuntimeError(f"Falha ao iniciar produção paralela no Flow: {res.get('error')}")

        # Monitoramento síncrono com suporte a cancelamento gracioso
        while self._flow_orchestrator.is_running:
            if self.stop_event.is_set():
                self._flow_orchestrator.parar_todos()
                raise InterruptedError("Produção no Flow interrompida pelo operador.")

            time.sleep(1.5)
            telemetria = self._flow_orchestrator.obter_telemetria_global()
            pct = telemetria.get("progresso_pct", 0)
            concluidas = telemetria.get("cenas_concluidas", 0)
            total = telemetria.get("total_cenas", 1)
            self._atualizar_progresso("producao_flow", progresso_pct=pct, detalhes=f"{concluidas}/{total} cenas concluídas ({pct}%).")

        duracao_exec = round(time.time() - inicio, 2)
        self._atualizar_progresso("producao_flow", status="concluido", progresso_pct=100, tempo_decorrido_s=duracao_exec, detalhes="Todas as mídias foram renderizadas.")
        self.etapas_concluidas.append("producao_flow")
        return {"sucesso": True, "duracao_s": duracao_exec}

    def _etapa_7_executar_audio_design(self, audio_path: str, options: Dict[str, Any]) -> Dict[str, Any]:
        """Etapa 7: Seleção de trilha BGM por nicho, mixagem de ducking com FFmpeg e corte de silêncios."""
        inicio = time.time()
        self.etapa_atual = "audio_design"
        self._atualizar_progresso("audio_design", status="executando", progresso_pct=20, detalhes="Classificando nicho sonoro e aplicando ducking...")

        perfil_musical = analisar_perfil_musical_projeto(self.projeto_id)
        saida_audio_mixado = str(self.project_dir / "audio_mixado_ducking.mp3")

        # Seleciona música da biblioteca se disponível ou fallback
        caminho_musica = options.get("caminho_musica")
        if not caminho_musica:
            lib_musicas = BASE_DIR / "Biblioteca" / "Musicas"
            if lib_musicas.exists():
                musicas_encontradas = list(lib_musicas.glob("*.mp3")) + list(lib_musicas.glob("*.wav"))
                if musicas_encontradas:
                    caminho_musica = str(musicas_encontradas[0])

        mixou_com_sucesso = False
        if caminho_musica and Path(caminho_musica).exists():
            vol_sugerido = perfil_musical.get("volume_sugerido", 0.14)
            mixou_com_sucesso = mixar_voz_e_musica_ffmpeg(
                audio_voz_path=audio_path,
                audio_musica_path=caminho_musica,
                saida_audio_path=saida_audio_mixado,
                volume_musica=vol_sugerido,
                ducking=True
            )

        audio_final_efetivo = saida_audio_mixado if (mixou_com_sucesso and Path(saida_audio_mixado).exists()) else audio_path
        duracao_exec = round(time.time() - inicio, 2)

        self._atualizar_progresso("audio_design", status="concluido", progresso_pct=100, tempo_decorrido_s=duracao_exec, detalhes=f"Nicho: {perfil_musical.get('label', 'Geral')}. Ducking: {'Ativo' if mixou_com_sucesso else 'Bypass'}.")
        self.etapas_concluidas.append("audio_design")
        return {
            "perfil_musical": perfil_musical,
            "audio_mixado": audio_final_efetivo,
            "ducking_aplicado": mixou_com_sucesso
        }

    def _etapa_8_compilar_capcut_draft(self, audio_final_path: str, options: Dict[str, Any]) -> Dict[str, Any]:
        """Etapa 8: Exportação do rascunho CapCut Desktop 9.x nativo (schema 360000)."""
        inicio = time.time()
        self.etapa_atual = "capcut_draft"
        self._atualizar_progresso("capcut_draft", status="executando", progresso_pct=25, detalhes="Montando timeline nativa schema 360000...")

        pasta_drafts = options.get("pasta_capcut") or detectar_pasta_drafts()
        if not pasta_drafts or not Path(pasta_drafts).exists():
            self._atualizar_progresso("capcut_draft", status="falhou", detalhes=f"Pasta de drafts do CapCut não localizada ({pasta_drafts}).")
            return {"sucesso": False, "erro": "Pasta de rascunhos do CapCut Desktop não encontrada."}

        plan = carregar_scene_plan(self.projeto_id)
        if not plan or not plan.get("cenas"):
            cenas_p = self.project_dir / "cenas.json"
            if cenas_p.exists():
                try:
                    c_data = json.loads(cenas_p.read_text(encoding="utf-8"))
                    cenas_list = c_data if isinstance(c_data, list) else c_data.get("cenas", [])
                    if cenas_list:
                        plan = {"cenas": cenas_list}
                except Exception:
                    plan = None
        if not plan or not plan.get("cenas"):
            raise ValueError("Plano de cenas vazio para montagem do draft CapCut.")

        cenas_ativas = [c for c in plan["cenas"] if not c.get("removida") and c.get("status") != "REMOVIDA"]
        cenas_ativas.sort(key=lambda c: float(c.get("tempo_inicio", 0.0) or 0.0))
        n_cenas = len(cenas_ativas)

        lista_cenas_draft = []
        for idx, c in enumerate(cenas_ativas):
            cid = int(c.get("id") or 0)
            t_ini = float(c.get("tempo_inicio", 0.0))
            t_ini_export = 0.0 if idx == 0 else t_ini

            if idx + 1 < n_cenas:
                t_prox = float(cenas_ativas[idx + 1].get("tempo_inicio", 0.0) or t_ini_export)
                dur = max(0.5, t_prox - t_ini_export)
            else:
                dur_fala = float(c.get("duracao", 4.0) or 4.0)
                dur = max(0.5, dur_fala)

            arq_disco = resolver_arquivo_cena(self.projeto_id, cid, t_ini)
            caminho_midia = str(arq_disco.resolve()) if (arq_disco and arq_disco.exists()) else None

            # Garante codec H.264 para vídeos
            if caminho_midia and caminho_midia.lower().endswith((".mp4", ".mov")):
                try:
                    caminho_midia = garantir_video_h264_compat(caminho_midia)
                except Exception:
                    pass

            is_video = bool(c.get("tipo") == "video" or (caminho_midia and caminho_midia.lower().endswith((".mp4", ".webm", ".mov"))))

            lista_cenas_draft.append({
                "start": t_ini_export,
                "duracao": dur,
                "arquivo": caminho_midia,
                "media_type": "video" if is_video else "photo",
                "transicao": c.get("transicao_saida", {}).get("tipo", "combinar")
            })

        res_draft = criar_draft_imagens(
            project_name=self.projeto_id,
            lista_cenas=lista_cenas_draft,
            arquivo_audio=audio_final_path,
            destino_drafts=pasta_drafts,
            nome_projeto=self.projeto_id
        )

        if not res_draft.get("success"):
            raise RuntimeError(f"Falha ao gerar draft CapCut: {res_draft.get('error')}")

        duracao_exec = round(time.time() - inicio, 2)
        draft_dir = res_draft.get("draft_dir", "")
        self._atualizar_progresso("capcut_draft", status="concluido", progresso_pct=100, tempo_decorrido_s=duracao_exec, detalhes=f"Draft nativo pronto em: {draft_dir}")
        self.etapas_concluidas.append("capcut_draft")
        return res_draft

    # =========================================================================
    # MÉTODO PRINCIPAL EXECUTAR() COM STATE MACHINE E RESILIÊNCIA
    # =========================================================================

    def executar(
        self,
        audio_path: Optional[str] = None,
        avatar_metadata: Optional[Dict[str, Any]] = None,
        options: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Coordena sequencialmente as 8 etapas com suporte a cancelamento e checkpoints."""
        inicio_total = time.time()
        options = options or {}
        self.estado_atual = "EXECUTANDO"
        log_event("MAESTRO", f"Iniciando orquestração Brain Maestro v4.0 para '{self.projeto_id}' (Job {self.job_id})")

        # Resolve caminho do áudio de entrada
        audio_efetivo = audio_path
        if not audio_efetivo:
            from services.api_v2 import resolver_audio_projeto
            audio_efetivo = resolver_audio_projeto(self.projeto_id)

        if not audio_efetivo or not Path(audio_efetivo).exists():
            self.estado_atual = "ERRO"
            erro_msg = f"Arquivo de áudio master não encontrado para '{self.projeto_id}'."
            self._salvar_checkpoint(status="ERRO", erro=erro_msg, codigo_erro="AUDIO_NOT_FOUND")
            return {"sucesso": False, "status": "ERRO", "codigo_erro": "AUDIO_NOT_FOUND", "error": erro_msg}

        try:
            # ETAPA 1: Validação de Áudio
            if self._verificar_interrupcao("validacao_audio"):
                return {"sucesso": False, "status": "CANCELADO", "etapa_interrompida": "validacao_audio"}
            dados_audio = self._etapa_1_validar_audio(audio_efetivo)
            self._salvar_checkpoint()

            # ETAPA 2: Whisper
            if self._verificar_interrupcao("whisper"):
                return {"sucesso": False, "status": "CANCELADO", "etapa_interrompida": "whisper"}
            self._etapa_2_executar_whisper(dados_audio["audio_path"])
            self._salvar_checkpoint()

            # ETAPA 3: Scene Builder
            if self._verificar_interrupcao("scene_builder"):
                return {"sucesso": False, "status": "CANCELADO", "etapa_interrompida": "scene_builder"}
            self._etapa_3_executar_scene_builder()
            self._salvar_checkpoint()

            # ETAPA 4: Brain v3 Core
            if self._verificar_interrupcao("brain_v3_core"):
                return {"sucesso": False, "status": "CANCELADO", "etapa_interrompida": "brain_v3_core"}
            saida_brain = self._etapa_4_executar_brain_v3_core(dados_audio["audio_path"], avatar_metadata, options)
            self._salvar_checkpoint()

            # ETAPA 5: Story Cards
            if self._verificar_interrupcao("story_cards_refinement"):
                return {"sucesso": False, "status": "CANCELADO", "etapa_interrompida": "story_cards_refinement"}
            self._etapa_5_refinar_story_cards()
            self._salvar_checkpoint()

            # ETAPA 6: Produção Flow
            if self._verificar_interrupcao("producao_flow"):
                return {"sucesso": False, "status": "CANCELADO", "etapa_interrompida": "producao_flow"}
            self._etapa_6_orquestrar_producao_flow(options)
            self._salvar_checkpoint()

            # ETAPA 7: Audio Design & Ducking
            if self._verificar_interrupcao("audio_design"):
                return {"sucesso": False, "status": "CANCELADO", "etapa_interrompida": "audio_design"}
            saida_audio = self._etapa_7_executar_audio_design(dados_audio["audio_path"], options)
            self._salvar_checkpoint()

            # ETAPA 8: CapCut Draft
            if self._verificar_interrupcao("capcut_draft"):
                return {"sucesso": False, "status": "CANCELADO", "etapa_interrompida": "capcut_draft"}
            res_draft = self._etapa_8_compilar_capcut_draft(saida_audio["audio_mixado"], options)
            self._salvar_checkpoint(status="CONCLUIDO")

            self.estado_atual = "CONCLUIDO"
            tempo_total = round(time.time() - inicio_total, 2)
            log_event("MAESTRO", f"Brain Maestro v4.0 finalizado com sucesso para '{self.projeto_id}' em {tempo_total}s.", level="info")

            return {
                "sucesso": True,
                "status": "CONCLUIDO",
                "job_id": self.job_id,
                "projeto_id": self.projeto_id,
                "tempo_total_s": tempo_total,
                "checkpoint_path": str(self.checkpoint_path),
                "etapas_concluidas": list(self.etapas_concluidas),
                "arquivos_gerados": {
                    "audio_original": dados_audio["audio_path"],
                    "audio_mixado": saida_audio["audio_mixado"],
                    "capcut_draft_dir": res_draft.get("draft_dir", "")
                },
                "progresso": self.get_progresso()
            }

        except InterruptedError:
            self.estado_atual = "CANCELADO"
            return {"sucesso": False, "status": "CANCELADO", "etapa_interrompida": self.etapa_atual}
        except Exception as e:
            self.estado_atual = "ERRO"
            erro_str = str(e)
            log_event("MAESTRO", f"Exceção durante etapa '{self.etapa_atual}' em '{self.projeto_id}': {erro_str}", level="error")
            self._salvar_checkpoint(status="ERRO", erro=erro_str, etapa_falha=self.etapa_atual)
            return {
                "sucesso": False,
                "status": "ERRO",
                "codigo_erro": f"ERR_{self.etapa_atual.upper()}",
                "etapa_falha": self.etapa_atual,
                "error": erro_str
            }
