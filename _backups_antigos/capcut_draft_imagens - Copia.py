"""
capcut_draft_imagens.py — Exportação de projeto ULTRACUT3 para o CapCut (PC).

Gera uma pasta de rascunho (draft) válida para o CapCut:
  C:\\Users\\{usuario}\\AppData\\Local\\CapCut\\User Data\\Projects\\com\\lveditor\\draft\\<nome_projeto>\\

Cada cena vira um segmento na trilha de vídeo (imagem importada ou vídeo), com o
timing derivado de cenas.json. O áudio original é adicionado como trilha única.

Formato: draft_content.json no padrão do CapCut PC (draft_version 2.x).
NÃO usa API externa — apenas os arquivos já disponíveis no projeto.
"""

import json
import os
import shutil
import uuid
from pathlib import Path


def detectar_pasta_drafts() -> str:
    """
    Detecta automaticamente a pasta de rascunhos do CapCut.
    Retorna o caminho ou "" se não encontrado.
    """
    usuario = os.environ.get("USERNAME", "")
    candidatos = []
    if usuario:
        candidatos.append(
            rf"C:\Users\{usuario}\AppData\Local\CapCut\User Data\Projects\com\lveditor\draft"
        )
    candidatos += [
        r"C:\Users\Public\Documents\CapCut\User Data\Projects\com\lveditor\draft",
        str(Path.home() / r"AppData\Local\CapCut\User Data\Projects\com\lveditor\draft"),
    ]
    for c in candidatos:
        if Path(c).exists() and Path(c).is_dir():
            return c
    return ""


def _gerar_placeholder(preto_path: Path) -> Path:
    """Cria uma imagem preta 640x360 para cenas sem mídia (placeholder)."""
    if preto_path.exists():
        return preto_path
    from config import FFMPEG_PATH
    import subprocess
    try:
        subprocess.run(
            [FFMPEG_PATH, "-y", "-v", "error",
             "-f", "lavfi", "-i", "color=c=black:s=640x360",
             "-frames:v", "1", str(preto_path)],
            capture_output=True, timeout=30,
        )
        if preto_path.exists():
            return preto_path
    except Exception:
        pass
    # fallback: grava um PNG 1x1 válido via Pillow se disponível
    try:
        from PIL import Image
        Image.new("RGB", (640, 360), (0, 0, 0)).save(str(preto_path))
    except Exception:
        pass
    return preto_path

def _material_base(mid, caminho, tipo, duracao, altura=1080, largura=1920) -> dict:
    """Monta o dicionário de material (vídeo/foto) no formato CapCut."""
    p = Path(caminho)
    base = {
        "id": mid,
        "height": altura,
        "width": largura,
        "is_ai_generated": 0,
        "is_audio_looped": False,
        "is_rotate_avatar_available": False,
        "is_unified_beauty_available": False,
        "material_name": p.name,
        "material_url": "",
        "path": str(p),
        "prefer_original_album_image": 0,
        "ratio": "16:9",
        "search_info": {
            "id": "", "is_preset": False, "key_word": "", "label": "",
            "origin_category": "", "resource_cat": "",
        },
        "type": tipo,
    }
    if tipo == "photo":
        base["duration"] = round(float(duracao), 3)
        base["mutable_material"] = {
            "blur_info": {"blur_sigma": 0.0, "blur_type": 0},
            "crop": {"max": [1, 1], "min": [0, 0], "rotation": 0, "transform": [1, 0, 0, 1]},
            "filter_info": {"brightness": 0, "contrast": 0, "hsl": 0, "saturation": 0,
                            "temperature": 0, "vignette": 0},
            "motion_scale": 0.0, "motion_speed": 0.0,
            "rotate": {"rotation": 0.0, "scale": 1.0},
            "stabilization_info": {"is_enable": 0, "type": 0},
        }
        base["video_algorithm"] = "resize_scale_1_2_3"
    else:
        base["duration"] = round(float(duracao), 3)
        base["has_audio"] = 0
        base["video_algorithm"] = "resize_scale_1_2_3"
    return base

def criar_draft_imagens(project_name: str, lista_cenas: list, arquivo_audio: str,
                        destino_drafts: str, nome_projeto: str = None) -> dict:
    """
    Cria um rascunho CapCut com as cenas e o áudio original.

    lista_cenas: list de dicts:
        {"start": float, "arquivo": str|None, "media_type": "photo"|"video",
         "duracao": float}
    - Cenas com `arquivo` usam o arquivo (importado/baixado).
    - Cenas sem arquivo usam um placeholder (imagem preta).
    - A trilha de vídeo é montada em sequência (offsets acumulados).
    - O áudio original é colocado na trilha de áudio (duração total).

    Retorna {"success": True, "draft_dir": str, "nome": str} em sucesso.
    """
    from services.event_logger import log_event

    nome = nome_projeto or project_name
    nome_sanitizado = "".join(c for c in nome if c not in '<>:"/\\|?*').strip()[:80] or project_name

    destino = Path(destino_drafts)
    if not destino.exists() or not destino.is_dir():
        return {"success": False, "error": f"Pasta de rascunhos do CapCut não encontrada: {destino_drafts}"}

    draft_dir = destino / nome_sanitizado
    draft_dir.mkdir(parents=True, exist_ok=True)

    # Remove conteúdo antigo do rascunho (regeneração limpa)
    for item in draft_dir.iterdir():
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            try:
                item.unlink()
            except OSError:
                pass

    try:
        duracao_total = sum(max(0.5, float(c.get("duracao", 3.0))) for c in lista_cenas)

        materiais_videos = []
        materiais_audios = []
        segmentos_video = []
        offset = 0.0

        # Placeholder para cenas sem mídia
        preto = draft_dir / "__placeholder_640x360.jpg"
        _gerar_placeholder(preto)

        for i, cena in enumerate(lista_cenas, 1):
            dur = max(0.5, float(cena.get("duracao", 3.0)))
            arquivo = cena.get("arquivo")
            media_type = cena.get("media_type", "photo")
            inicio = max(0.0, float(cena.get("start", offset)))

            # Garante que a cena usa um arquivo existente (ou placeholder)
            if not arquivo or not Path(arquivo).exists():
                arquivo = str(preto)
                media_type = "photo"

            src = Path(arquivo)
            # Copia a mídia para dentro da pasta do draft (self-contained)
            destino_midia = draft_dir / src.name
            if src.resolve() != destino_midia.resolve():
                shutil.copy2(str(src), str(destino_midia))

            mid = f"V{i}"
            tipo = "photo" if media_type == "photo" else "video"
            mat = _material_base(mid, str(destino_midia), tipo, dur)
            materiais_videos.append(mat)

            seg = {
                "extra_material_refs": [],
                "id": f"S{i}",
                "is_placeholder": 0,
                "material_id": mid,
                "render_index": i,
                "source_timerange": {"start": 0, "duration": round(dur, 3)},
                "target_timerange": {
                    "start": round(offset, 3),
                    "duration": round(dur, 3),
                },
                "transform": {"scale": [1, 1], "translate": [0, 0]},
                "type": tipo,
            }
            segmentos_video.append(seg)
            offset += dur

        # Áudio original
        audio_src = Path(arquivo_audio)
        tem_audio = audio_src.exists() and audio_src.stat().st_size > 0
        if tem_audio:
            audio_dst = draft_dir / audio_src.name
            if audio_src.resolve() != audio_dst.resolve():
                shutil.copy2(str(audio_src), str(audio_dst))
            audio_dur = duracao_total
            try:
                from config import FFPROBE_PATH
                import subprocess
                r = subprocess.run(
                    [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(audio_dst)],
                    capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    audio_dur = float(r.stdout.strip())
            except Exception:
                pass
            mat_audio = {
                "id": "A1",
                "is_audio_looped": False,
                "material_name": audio_dst.name,
                "material_url": "",
                "path": str(audio_dst),
                "type": "audio",
                "duration": round(audio_dur, 3),
            }
            materiais_audios.append(mat_audio)
            seg_audio = {
                "extra_material_refs": [],
                "id": "S_A",
                "is_placeholder": 0,
                "material_id": "A1",
                "render_index": 0,
                "source_timerange": {"start": 0, "duration": round(audio_dur, 3)},
                "target_timerange": {"start": 0, "duration": round(duracao_total, 3)},
                "transform": {"scale": [1, 1], "translate": [0, 0]},
                "type": "audio",
            }

        # Trilha de áudio (só se houver áudio válido)
        trilhas = []
        if tem_audio:
            trilhas.append({
                "attribute": 0, "flag": 0, "id": "T_A",
                "is_offline": 0, "is_touch_locked": 0,
                "materials_origin_value": [], "materials_target_value": [],
                "segment": [seg_audio], "type": "audio", "visible": 1,
            })
        trilhas.append({
            "attribute": 0, "flag": 0, "id": "T_V",
            "is_offline": 0, "is_touch_locked": 0,
            "materials_origin_value": [], "materials_target_value": [],
            "segment": segmentos_video, "type": "video", "visible": 1,
        })

        ids_videos = [m["id"] for m in materiais_videos]
        ids_audios = [m["id"] for m in materiais_audios]

        draft_content = {
            "canvas_config": {
                "canvas_ratio": "16:9", "export_ratio": "16:9",
                "height": 1080, "width": 1920,
            },
            "draft_content": {
                "tracks": trilhas,
                "materials": {
                    "audios": materiais_audios,
                    "images": [],
                    "texts": [],
                    "videos": materiais_videos,
                },
                "timeline": {
                    "audio": {"content_used": [], "global_used": ids_audios, "start_time": 0},
                    "video": {"content_used": [], "global_used": ids_videos, "start_time": 0},
                },
            },
            "draft_fold_path": str(draft_dir),
            "draft_materials": {"ai_segment_index": 0, "audios": [], "images": [],
                                "texts": [], "videos": []},
            "draft_meta_info": {
                "draft_cover": "", "draft_cover_path": "", "draft_cover_style": 0,
                "draft_fold_path": str(draft_dir),
                "draft_id": uuid.uuid4().hex,
                "draft_is_click_canvas_changed": False,
                "draft_last_modified_platform": "pc",
                "draft_materials_updated": False,
                "draft_name": nome_sanitizado,
                "draft_removable_storages": [],
                "draft_root_path": str(destino),
                "draft_storyboard_updated": True,
                "draft_tape": 0,
                "draft_timeline_out_updated": True,
                "draft_timeline_preview_updated": True,
                "draft_updated_time": 0,
                "tm_draft_cloud_updated": 0, "tm_draft_cloud_video_updated": 0,
                "tm_draft_deep_updated": 0, "tm_draft_edit_updated": 0,
                "tm_draft_share_updated": 0, "tm_draft_updated": 0,
            },
            "draft_new_version": "1.0.0",
            "draft_project_type": "default",
            "draft_removable_storages": [],
            "draft_revision": "",
            "draft_root_path": str(destino),
            "draft_scripts": [],
            "draft_settings": {
                "fps": 25, "is_init": True, "resolution": [1920, 1080],
                "video_algorithm": "resize_scale_1_2_3",
            },
            "draft_templates": [],
            "draft_version": "2.0.0",
        }

        with open(draft_dir / "draft_content.json", "w", encoding="utf-8") as f:
            json.dump(draft_content, f, ensure_ascii=False, indent=2)

        log_event("RENDER", f"CapCut draft criado: {draft_dir} "
                            f"({len(segmentos_video)} cenas, audio={'sim' if tem_audio else 'nao'})",
                  level="info")

        return {"success": True, "draft_dir": str(draft_dir), "nome": nome_sanitizado}

    except Exception as e:  # noqa: BLE001
        log_event("RENDER", f"Falha ao criar draft CapCut: {e}", level="error")
        return {"success": False, "error": str(e)}
