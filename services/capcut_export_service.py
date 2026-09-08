"""
Serviço de exportação para CapCut.
Cria: export_capcut/media/ com imagens + vídeos em sequência + metadados.
"""
import json
import shutil
import csv
from pathlib import Path
from typing import Dict, List, Optional

from config import PROJETOS_DIR
from services.scene_plan_service import carregar_scene_plan


class CapCutExportService:
    def __init__(self, projeto_id: str, pasta_projetos_base: Optional[str] = None):
        # Base padrão = PROJETOS_DIR do projeto (config.py), portável.
        # Aceita base customizada (ex.: "projetos" relativo à raiz do repositório).
        if pasta_projetos_base:
            base = Path(pasta_projetos_base)
            if not base.is_absolute():
                base = base.resolve()
        else:
            base = PROJETOS_DIR
        self.projeto_id = projeto_id
        self.pasta_proj = base / projeto_id
        self.pasta_export = self.pasta_proj / "export_capcut"
        self.pasta_media = self.pasta_export / "media"

    def _carregar_scene_plan(self) -> Optional[Dict]:
        """Carrega lira_scene_plan.json da pasta do projeto (base customizada).

        Se a base for a padrão do repositório, delega ao helper canônico
        `carregar_scene_plan(projeto_id)` (que aplica normalização de caminhos
        e backfills de transições). Em base customizada, lê o JSON diretamente.
        """
        plan_file = self.pasta_proj / "lira_scene_plan.json"
        if plan_file.exists():
            try:
                return json.loads(plan_file.read_text(encoding="utf-8"))
            except Exception:
                return None
        # Fallback via helper canônico (usa PROJETOS_DIR) — recebe o NOME, nunca o path
        return carregar_scene_plan(self.projeto_id)

    def exportar(self) -> Dict:
        """
        Exporta projeto para CapCut.
        Retorna: { success, export_dir, total_cenas, msg }
        """
        try:
            # Limpa e recria pastas
            if self.pasta_export.exists():
                shutil.rmtree(self.pasta_export)
            self.pasta_media.mkdir(parents=True, exist_ok=True)

            # Carrega scene_plan
            scene_plan = self._carregar_scene_plan()
            if not scene_plan:
                return {"success": False, "error": "Scene plan não encontrado"}

            cenas = scene_plan.get("cenas", [])
            if not cenas:
                return {"success": False, "error": "Nenhuma cena no projeto"}

            # Processa cada cena
            csv_data: List[Dict] = []
            srt_blocos: List[str] = []
            contador = 0

            for cena in cenas:
                # cid numérico (scene_index ou id)
                cid_raw = cena.get("scene_index") if cena.get("scene_index") is not None else cena.get("id")
                if cid_raw is None:
                    continue
                try:
                    cid = int(cid_raw)
                except (TypeError, ValueError):
                    continue

                # Arquivo de mídia (aceita caminho absoluto ou relativo à pasta do projeto)
                arquivo_midia = str(cena.get("arquivo_midia") or "")
                if not arquivo_midia:
                    arquivo_midia = str(cena.get("download_path") or "")
                if not arquivo_midia:
                    continue
                origem = Path(arquivo_midia)
                if not origem.is_absolute():
                    origem = self.pasta_proj / origem
                if not origem.exists():
                    continue

                contador += 1

                # Detecta tipo (vídeo ou imagem) pela extensão REAL do arquivo
                suf = origem.suffix.lower()
                eh_video = suf in (".mp4", ".mov", ".webm")
                ext = suf.lstrip(".") if suf in (".mp4", ".mov", ".webm", ".png", ".jpg", ".jpeg", ".webp") else ("mp4" if eh_video else "png")

                # Timecode (aceita start_time/start/tempo_inicio)
                timecode_start = float(cena.get("start_time") if cena.get("start_time") is not None
                                       else cena.get("start") if cena.get("start") is not None
                                       else cena.get("tempo_inicio", 0))
                timecode_end = float(cena.get("end_time") if cena.get("end_time") is not None
                                     else cena.get("end") if cena.get("end") is not None
                                     else cena.get("tempo_fim", timecode_start + 5))
                if timecode_end <= timecode_start:
                    timecode_end = timecode_start + 5.0
                timecode_str = self._tempo_para_timecode(timecode_start)

                # Nome no padrão: 001_[00-00-05].png ou 003_[00-09-13].mp4
                nome_destino = f"{cid:03d}_[{timecode_str}].{ext}"
                arquivo_destino = self.pasta_media / nome_destino

                # Copia arquivo
                shutil.copy2(str(origem), str(arquivo_destino))

                # Adiciona ao CSV
                csv_data.append({
                    "cena_id": cid,
                    "arquivo": nome_destino,
                    "tipo": "VIDEO" if eh_video else "IMAGEM",
                    "timecode": timecode_str,
                    "duracao_seg": max(1, int(round(timecode_end - timecode_start))),
                    "naracao": cena.get("texto", cena.get("narration", "")),
                })

                # Adiciona ao SRT
                srt_blocos.append(self._gerar_srt_bloco(
                    numero=contador,
                    start=timecode_start,
                    end=timecode_end,
                    texto=cena.get("texto", cena.get("narration", "")),
                ))
            # Copia áudio (se existir) — fonte única: meta.arquivo_audio, depois raiz/audio/
            audio_origem = self._resolver_audio_projeto()
            if audio_origem:
                ext_audio = audio_origem.suffix or ".mp3"
                shutil.copy2(str(audio_origem), str(self.pasta_export / f"{self.projeto_id}{ext_audio}"))

            # Salva CSV
            campos_csv = ["cena_id", "arquivo", "tipo", "timecode", "duracao_seg", "naracao"]
            with open(self.pasta_export / "capcut.csv", "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=campos_csv)
                writer.writeheader()
                writer.writerows(csv_data)

            # Salva SRT (blocos separados por linha em branco — padrão SRT)
            with open(self.pasta_export / "legendas.srt", "w", encoding="utf-8") as f:
                f.write("\n\n".join(srt_blocos))
                if srt_blocos:
                    f.write("\n")

            return {
                "success": True,
                "export_dir": str(self.pasta_export),
                "total_cenas": contador,
                "msg": f"✅ {contador} cenas exportadas para CapCut",
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    def _resolver_audio_projeto(self) -> Optional[Path]:
        """Localiza o áudio original do projeto (meta.json → pasta raiz → audio/)."""
        meta_file = self.pasta_proj / "meta.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                arq = meta.get("arquivo_audio") or ""
                if arq:
                    p = Path(arq)
                    if not p.is_absolute():
                        p = self.pasta_proj / p
                    if p.exists():
                        return p
            except Exception:
                pass

        for candidato in [self.pasta_proj / f"{self.projeto_id}.mp3",
                          self.pasta_proj / f"{self.projeto_id}.wav"]:
            if candidato.exists():
                return candidato

        pasta_audio = self.pasta_proj / "audio"
        if pasta_audio.exists():
            for f in sorted(pasta_audio.iterdir()):
                if f.is_file() and f.suffix.lower() in (".mp3", ".wav", ".m4a", ".aac", ".ogg"):
                    return f
        return None

    @staticmethod
    def _tempo_para_timecode(segundos: float) -> str:
        """Converte segundos para HH-MM-SS (ex: 00-39-44)"""
        total = int(segundos)
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        return f"{h:02d}-{m:02d}-{s:02d}"

    @staticmethod
    def _gerar_srt_bloco(numero: int, start: float, end: float, texto: str) -> str:
        """Gera bloco SRT formatado"""
        def seg_para_srt(seg):
            total_ms = int(seg * 1000)
            h = total_ms // 3600000
            m = (total_ms % 3600000) // 60000
            s = (total_ms % 60000) // 1000
            ms = total_ms % 1000
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

        return f"{numero}\n{seg_para_srt(start)} --> {seg_para_srt(end)}\n{texto}"
