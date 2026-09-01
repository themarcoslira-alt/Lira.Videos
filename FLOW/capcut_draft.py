"""
Módulo para geração de drafts compatíveis com CapCut Desktop (Windows).
Estratégia: clona um projeto existente do usuário como template e substitui
o vídeo + segmentos, garantindo compatibilidade com a versão instalada.
"""

import copy
import json
import os
import shutil
import time
import uuid
from pathlib import Path


# Info de plataforma EXATA lida de um projeto real do CapCut instalado (v8.7.0).
# O CapCut exige que 'platform' e 'last_modified_platform' sejam objetos com a
# versão do app — se for string ("pc") ou versão vazia, ele abre e fecha.
_PLATFORM_INFO = {
    "app_id": 359289,
    "app_source": "cc",
    "app_version": "8.7.0",
    "os": "windows",
    "os_version": "10.0.19044",
    "device_id": "0dd38ac30cc292375b5de2f19310be9a",
    "hard_disk_id": "ebd4a8c30804de6d69a812a397f69460",
    "mac_address": "8dd798e9a6481f9cf9535a582ecf7de0",
}


def gerar_uuid() -> str:
    return str(uuid.uuid4()).upper()


def microsegundos(segundos: float) -> int:
    return int(segundos * 1_000_000)


def agora_us() -> int:
    return int(time.time() * 1_000_000)


def _encontrar_template(pasta_drafts: str, preferir: str = "") -> dict | None:
    """
    Encontra um draft existente para usar como template.
    Se 'preferir' for informado, tenta usar aquela pasta primeiro.
    Caso contrário, usa o projeto modificado mais recentemente.
    """
    pasta = Path(pasta_drafts)

    # Tenta usar o projeto preferido
    if preferir:
        candidato = pasta / preferir / "draft_content.json"
        if candidato.exists():
            with open(candidato, encoding="utf-8") as f:
                return json.load(f)

    # Fallback: projeto modificado mais recentemente (exceto pastas do pipeline)
    melhor = None
    mais_recente = 0.0
    for entrada in pasta.iterdir():
        if not entrada.is_dir():
            continue
        if entrada.name.startswith("TESTE_") or entrada.name.startswith("Pipeline_"):
            continue
        content_path = entrada / "draft_content.json"
        if not content_path.exists():
            continue
        mtime = content_path.stat().st_mtime
        if mtime > mais_recente:
            mais_recente = mtime
            melhor = content_path

    if melhor is None:
        return None

    with open(melhor, encoding="utf-8") as f:
        return json.load(f)


def _criar_segmento_video(
    material_id: str,
    source_start_us: int,
    source_dur_us: int,
    target_start_us: int,
    zoom: float = 1.0,
    pos_x: float = 0.0,
    pos_y: float = 0.0,
    speed: float = 1.0,
) -> dict:
    """Cria um segmento de vídeo com todos os campos que o CapCut exige."""
    return {
        "id": gerar_uuid(),
        "material_id": material_id,
        "source_timerange": {"start": source_start_us, "duration": source_dur_us},
        "target_timerange": {"start": target_start_us, "duration": source_dur_us},
        "render_timerange": {"start": 0, "duration": 0},
        "desc": "",
        "state": 0,
        "speed": 1.0,
        "is_loop": False,
        "is_tone_modify": False,
        "reverse": False,
        "intensifies_audio": False,
        "cartoon": False,
        "volume": 1.0,
        "last_nonzero_volume": 1.0,
        "clip": {
            "scale": {"x": zoom, "y": zoom},
            "rotation": 0.0,
            "transform": {"x": pos_x, "y": pos_y},
            "flip": {"vertical": False, "horizontal": False},
            "alpha": 1.0,
        },
        "speed": speed,
        "uniform_scale": {"on": True, "value": 1.0},
        "extra_material_refs": [],
        "render_index": 0,
        "keyframe_refs": [],
        "enable_lut": True,
        "enable_adjust": True,
        "enable_hsl": False,
        "visible": True,
        "group_id": "",
        "enable_color_curves": True,
        "enable_hsl_curves": True,
        "track_render_index": 0,
        "hdr_settings": {"mode": 1, "intensity": 1.0, "nits": 1000},
        "enable_color_wheels": True,
        "track_attribute": 0,
        "is_placeholder": False,
        "template_id": "",
        "enable_smart_color_adjust": False,
        "template_scene": "default",
        "common_keyframes": [],
        "caption_info": None,
        "responsive_layout": {
            "enable": False,
            "target_follow": "",
            "size_layout": 0,
            "horizontal_pos_layout": 0,
            "vertical_pos_layout": 0,
        },
        "enable_color_match_adjust": False,
        "enable_color_correct_adjust": False,
        "enable_adjust_mask": False,
        "raw_segment_id": "",
        "lyric_keyframes": None,
        "enable_video_mask": True,
        "digital_human_template_group_id": "",
        "color_correct_alg_result": "",
        "source": "segmentsourcenormal",
        "enable_mask_stroke": False,
        "enable_mask_shadow": False,
        "enable_color_adjust_pro": False,
    }


def _duracao_video_us(video_path: str) -> int:
    """Lê a duração real do vídeo (em microsegundos) via PyAV. 0 se falhar."""
    try:
        import av
        with av.open(video_path) as container:
            if container.duration:
                return int(container.duration)  # já em microsegundos
            vs = container.streams.video[0]
            return int(float(vs.duration * vs.time_base) * 1_000_000)
    except Exception:
        return 0


def _criar_material_video(video_path: str, largura: int, altura: int, duracao_us: int) -> dict:
    """Cria o objeto de material de vídeo no formato que o CapCut espera."""
    mat_id = gerar_uuid()
    return {
        "id": mat_id,
        "unique_id": "",
        "type": "video",
        "duration": duracao_us,
        "path": str(Path(video_path).resolve()).replace("\\", "/"),
        "media_path": "",
        "local_id": "",
        "has_audio": True,
        "reverse_path": "",
        "intensifies_path": "",
        "reverse_intensifies_path": "",
        "intensifies_audio_path": "",
        "cartoon_path": "",
        "width": largura,
        "height": altura,
        "category_id": "",
        "category_name": "local",
        "material_id": "",
        "material_name": Path(video_path).name,
        "material_url": "",
        "crop": {
            "upper_left_x": 0.0, "upper_left_y": 0.0,
            "upper_right_x": 1.0, "upper_right_y": 0.0,
            "lower_left_x": 0.0, "lower_left_y": 1.0,
            "lower_right_x": 1.0, "lower_right_y": 1.0,
        },
        "crop_ratio": "free",
        "audio_fade": None,
        "crop_scale": 1.0,
        "extra_type_option": 0,
        "stable": {"stable_level": 0, "matrix_path": "", "time_range": {"start": 0, "duration": 0}},
        "matting": {
            "flag": 0, "path": "", "interactiveTime": [],
            "has_use_quick_brush": False, "strokes": [],
            "has_use_quick_eraser": False, "expansion": 0,
            "feather": 0, "reverse": False, "custom_matting_id": "",
            "enable_matting_stroke": False, "is_clould": False,
            "mask_video_path": "", "cloud_product_fps": 0.0,
        },
        "source": 0,
        "source_platform": 0,
        "formula_id": "",
        "check_flag": 62978047,
        "video_algorithm": {
            "algorithms": [],
            "time_range": {"start": 0, "duration": duracao_us},
            "path": "",
            "gameplay_configs": [],
            "complement_frame_config": None,
            "motion_blur_config": None,
            "deflicker": None,
            "noise_reduction": None,
            "quality_enhance": None,
            "super_resolution": None,
            "skip_algorithm_index": [],
        },
        "is_unified_beauty_mode": False,
        "is_set_beauty_mode": False,
        "object_locked": None,
        "smart_motion": None,
        "freeze": None,
        "picture_from": "none",
        "picture_set_category_id": "",
        "picture_set_category_name": "",
        "team_id": "",
        "local_material_id": str(uuid.uuid4()),
        "origin_material_id": "",
        "request_id": "",
        "has_sound_separated": False,
        "is_text_edit_overdub": False,
        "is_ai_generate_content": False,
        "aigc_type": "none",
        "is_copyright": False,
        # Campos extras exigidos pela versão instalada do CapCut (lidos de projeto real):
        "aigc_history_id": "",
        "aigc_item_id": "",
        "local_material_from": "",
        "smart_match_info": None,
        "multi_camera_info": None,
        "corner_pin": None,
        "content_feature_info": None,
        "live_photo_timestamp": -1,
        "live_photo_cover_path": "",
        "surface_trackings": [],
        "beauty_face_preset_infos": [],
        "beauty_body_preset_id": "",
        "beauty_face_auto_preset": {"preset_id": "", "name": "", "rate_map": "", "scene": ""},
        "beauty_face_auto_preset_infos": [],
        "beauty_body_auto_preset": None,
        "video_mask_stroke": {
            "resource_id": "", "path": "", "type": "", "color": "", "size": 0.0,
            "alpha": 0.0, "distance": 0.0, "texture": 0.0,
            "horizontal_shift": 0.0, "vertical_shift": 0.0,
        },
        "video_mask_shadow": {
            "resource_id": "", "path": "", "color": "", "alpha": 0.0,
            "blur": 0.0, "distance": 0.0, "angle": 0.0,
        },
    }


def _criar_material_texto(texto: str, fonte_tamanho: int, bold: bool) -> dict:
    """Cria material de texto/legenda."""
    return {
        "id": gerar_uuid(),
        "type": "text",
        "name": "",
        "content": texto,
        "font_size": fonte_tamanho,
        "bold": bold,
        "italic": False,
        "underline": False,
        "alignment": 1,
        "color": "1.0,1.0,1.0,1.0",
        "background_color": "0.0,0.0,0.0,1.0",
        "background_style": 1,
        "background_alpha": 0.6,
        "check_flag": 7,
        "combo_info": {"text_templates": []},
        "font_category_id": "",
        "font_category_name": "",
        "font_id": "",
        "font_name": "",
        "font_path": "",
        "font_resource_id": "",
        "font_source_platform": 0,
        "font_title": "none",
        "font_url": "",
        "fonts": [],
        "inner_padding": -1.0,
        "letter_spacing": 0.0,
        "line_feed": 1,
        "line_max_width": 0.82,
        "line_spacing": 0.02,
        "preset_id": "",
        "recognize_task_id": "",
        "recognize_type": 0,
        "style_name": "",
        "sub_type": 0,
        "text_alpha": 1.0,
        "text_preset_resource_id": "",
        "text_size": fonte_tamanho,
        "text_to_audio_ids": [],
        "tts_auto_update": False,
        "typesetting": 0,
        "underline_offset": 0.22,
        "underline_width": 0.05,
        "words": {"end_time": [], "start_time": [], "text": []},
        "original_size": [],
    }


def _segmento_texto(material_id: str, inicio_us: int, fim_us: int) -> dict:
    """Cria um segmento de texto/legenda na timeline."""
    return {
        "id": gerar_uuid(),
        "material_id": material_id,
        "source_timerange": None,
        "target_timerange": {"start": inicio_us, "duration": fim_us - inicio_us},
        "render_timerange": {"start": 0, "duration": 0},
        "desc": "",
        "state": 0,
        "render_index": 11000,
        "track_render_index": 0,
        "extra_material_refs": [],
        "keyframe_refs": [],
        "common_keyframes": [],
        "visible": True,
        "caption_info": None,
        "responsive_layout": {
            "enable": False, "target_follow": "",
            "size_layout": 0, "horizontal_pos_layout": 0, "vertical_pos_layout": 0,
        },
        "raw_segment_id": "",
        "lyric_keyframes": None,
    }


def criar_draft_capcut(
    video_path: str,
    segmentos: list[dict],
    legendas: list[dict],
    punch_ins: list[dict],
    marcadores_broll: list[dict],
    config: dict,
    pasta_destino: str,
    nome_projeto: str = "Pipeline_Auto",
    reframes: list[dict] | None = None,
    speed_map: list[dict] | None = None,
    keyframes_extras: list[dict] | None = None,
    lut_path: str = "",
) -> str:
    """
    Cria o draft do CapCut e registra no root_meta_info.json.
    """
    draft_id = gerar_uuid()
    ts_agora = agora_us()
    largura = config["video"]["largura"]
    altura = config["video"]["altura"]
    fps = config["video"]["fps"]

    # Pasta do draft
    nome_pasta = nome_projeto.replace("/", "_").replace("\\", "_").replace(":", "_")
    pasta_draft = Path(pasta_destino) / nome_pasta
    sufixo = 1
    while pasta_draft.exists():
        pasta_draft = Path(pasta_destino) / f"{nome_pasta}_{sufixo}"
        sufixo += 1
    pasta_draft.mkdir(parents=True, exist_ok=True)

    # Duração da timeline final (soma dos trechos mantidos)
    duracao_total_us = sum(
        microsegundos(s["fim"] - s["inicio"]) for s in segmentos
    ) if segmentos else 0

    # Duração do vídeo ORIGINAL (o material precisa declarar a duração da fonte,
    # senão segmentos que referenciam trechos lá no fim apontam além do material).
    duracao_fonte_us = _duracao_video_us(video_path) or duracao_total_us

    mat_video = _criar_material_video(video_path, largura, altura, duracao_fonte_us)
    mat_video_id = mat_video["id"]

    cfg_din = config["dinamismo"]
    reframes = reframes or []
    speed_map = speed_map or []

    # Segmentos de vídeo com zoom, reframe, speed.
    # Cada segmento recebe os 4 materiais OBRIGATÓRIOS do CapCut (velocidade,
    # canvas, mapeamento de som, separação vocal) — sem eles o CapCut fecha.
    clips_video = []
    materiais_por_segmento = []  # lista de dicts {lista_material: material}
    offset_us = 0

    for i, seg in enumerate(segmentos):
        src_start = microsegundos(seg["inicio"])
        dur_us = microsegundos(seg["fim"] - seg["inicio"])

        # Zoom do punch-in
        zoom = 1.0
        for p in punch_ins:
            if p["inicio"] <= seg["inicio"] < p["fim"]:
                zoom = p["zoom"] / 100.0
                break

        # Reframe (posição X/Y)
        pos_x, pos_y = 0.0, 0.0
        for r in reframes:
            if r["inicio"] <= seg["inicio"] < r["fim"]:
                pos_x, pos_y = r["x"], r["y"]
                break

        # Speed ramp
        speed = 1.0
        for s in speed_map:
            if abs(s["inicio"] - seg["inicio"]) < 0.1:
                speed = s["speed"]
                break

        clip = _criar_segmento_video(
            mat_video_id, src_start, dur_us, offset_us,
            zoom=zoom, pos_x=pos_x, pos_y=pos_y, speed=speed,
        )

        # Com velocidade, a duração na timeline encurta (target = source / speed)
        target_dur = int(dur_us / speed) if speed and speed != 1.0 else dur_us
        clip["target_timerange"] = {"start": offset_us, "duration": target_dur}

        # Escala extra (jump cut) — aplicada direto na escala (sem keyframe)
        escala_extra = seg.pop("_escala_extra", None)
        if escala_extra and escala_extra != 1.0:
            clip["clip"]["scale"]["x"] = zoom * escala_extra
            clip["clip"]["scale"]["y"] = zoom * escala_extra

        # Materiais obrigatórios do segmento (6, na ordem exata do CapCut real)
        refs, por_lista = _materiais_obrigatorios_segmento(speed)
        clip["extra_material_refs"] = refs
        clip["keyframe_refs"] = []
        seg.pop("_keyframe_ids", None)
        materiais_por_segmento.append(por_lista)

        clips_video.append(clip)
        offset_us += target_dur

    materiais_transicao = []  # transições desativadas (formato não validado)

    # Materiais e segmentos de texto (legendas)
    mats_texto = []
    clips_texto = []
    cfg_leg = config["legendas"]
    bold = cfg_leg["estilo"] == "bold"
    for leg in legendas:
        mat = _criar_material_texto(leg["texto"], cfg_leg["fonte_tamanho"], bold)
        mats_texto.append(mat)
        clips_texto.append(_segmento_texto(
            mat["id"],
            microsegundos(leg["inicio"]),
            microsegundos(leg["fim"]),
        ))

    # Sempre usa a estrutura mínima COMPLETA (reproduzida de um projeto real).
    # Não depende de template externo, que pode ser apagado e quebrar o draft.
    template = None

    if template:
        draft_content = copy.deepcopy(template)
        # Substitui campos essenciais
        draft_content["id"] = draft_id
        draft_content["name"] = nome_projeto
        draft_content["duration"] = duracao_total_us
        draft_content["create_time"] = int(time.time())
        draft_content["update_time"] = int(time.time())
        draft_content["canvas_config"] = {"ratio": "original", "width": largura, "height": altura, "background": None}
        draft_content["fps"] = float(fps)

        # Substitui materiais de vídeo
        draft_content["materials"]["videos"] = [mat_video]

        # Limpa textos antigos, adiciona os novos
        if "texts" in draft_content["materials"]:
            draft_content["materials"]["texts"] = mats_texto
        else:
            draft_content["materials"]["texts"] = mats_texto

        # Reconstrói as trilhas mantendo apenas video e text
        trilha_video = {
            "attribute": 0, "flag": 0, "id": gerar_uuid(),
            "is_default_name": True, "name": "",
            "segments": clips_video, "type": "video",
        }
        trilha_texto = {
            "attribute": 0, "flag": 0, "id": gerar_uuid(),
            "is_default_name": True, "name": "",
            "segments": clips_texto, "type": "text",
        }
        draft_content["tracks"] = [trilha_video, trilha_texto]

        # Limpa materiais antigos do template (serão recriados por segmento)
        for chave in ["speeds", "placeholder_infos", "canvases",
                      "sound_channel_mappings", "material_colors", "vocal_separations",
                      "adjusts", "effects", "filters",
                      "stickers", "beats", "audio_effects", "audio_fades",
                      "audio_pannings", "audio_pitch_shifts", "loudnesses",
                      "material_animations", "video_effects", "video_trackings"]:
            if chave in draft_content["materials"]:
                draft_content["materials"][chave] = []
        if "audios" in draft_content["materials"]:
            draft_content["materials"]["audios"] = []

    else:
        # Fallback: monta do zero com estrutura mínima conhecida
        draft_content = _draft_minimo(
            draft_id, nome_projeto, duracao_total_us,
            largura, altura, fps,
            mat_video, clips_video, mats_texto, clips_texto,
        )

    # ── Injeção de materiais (roda em AMBOS os caminhos: template e mínimo) ──
    # 1. Materiais OBRIGATÓRIOS por segmento (6 por segmento).
    _injetar_materiais_segmentos(draft_content, materiais_por_segmento)
    # 2. Limpa qualquer referência órfã restante (keyframes/transições não usados),
    #    sem mexer nos refs válidos dos materiais obrigatórios.
    _resolver_referencias(draft_content, clips_video, materiais_transicao, keyframes_extras, lut_path)

    # Salva draft_content.json
    with open(pasta_draft / "draft_content.json", "w", encoding="utf-8") as f:
        json.dump(draft_content, f, ensure_ascii=False, separators=(",", ":"))

    # Salva draft_meta_info.json
    draft_meta = _criar_draft_meta(
        draft_id, nome_projeto, video_path, largura, altura,
        duracao_total_us, ts_agora, pasta_draft, pasta_destino,
    )
    with open(pasta_draft / "draft_meta_info.json", "w", encoding="utf-8") as f:
        json.dump(draft_meta, f, ensure_ascii=False, separators=(",", ":"))

    # Arquivos auxiliares e subpastas
    _criar_auxiliares(pasta_draft)

    # Registra no root_meta_info.json
    _registrar_root_meta(pasta_destino, pasta_draft, draft_id, nome_projeto, duracao_total_us, ts_agora)

    return str(pasta_draft)


def _draft_minimo(draft_id, nome, duracao_us, largura, altura, fps,
                  mat_video, clips_video, mats_texto, clips_texto):
    """Estrutura mínima de draft_content quando não há template disponível."""
    return {
        "canvas_config": {"ratio": "original", "width": largura, "height": altura, "background": None},
        "color_space": 0,
        "config": {
            "adjust_max_index": 1, "attachment_info": [],
            "combination_max_index": 1, "export_range": None,
            "extract_audio_last_index": 1, "lyrics_recognition_id": "",
            "lyrics_sync": False, "lyrics_taskinfo": [],
            "maintrack_adsorb": True, "material_save_mode": 0,
            "multi_language_current": "none", "multi_language_list": [],
            "multi_language_main": "none", "multi_language_mode": "none",
            "original_sound_last_index": 1, "record_audio_last_index": 1,
            "sticker_max_index": 1, "subtitle_recognition_id": "",
            "subtitle_sync": True, "subtitle_taskinfo": [],
            "system_font_list": [], "video_mute": False, "zoom_info_params": None,
        },
        "cover": "draft_cover.jpg",
        "create_time": int(time.time()),
        "draft_type": "",
        "duration": duracao_us,
        "extra_info": None,
        "fps": float(fps),
        "free_render_index_mode_on": False,
        "function_assistant_info": None,
        "group_container": None,
        "id": draft_id,
        "is_drop_frame_timecode": False,
        "keyframe_graph_list": [],
        "keyframes": {"adjusts": [], "audios": [], "effects": [], "filters": [], "handwrites": [], "stickers": [], "texts": [], "videos": []},
        "last_modified_platform": _PLATFORM_INFO,
        "lyrics_effects": [],
        "materials": {
            "ai_translates": [], "audios": [], "audio_balances": [],
            "audio_effects": [], "audio_fades": [], "audio_pannings": [],
            "audio_pitch_shifts": [], "audio_track_indexes": [],
            "beats": [], "canvases": [], "chromas": [], "color_curves": [],
            "common_mask": [], "digital_humans": [], "digital_human_model_dressing": [],
            "drafts": [], "effects": [], "flowers": [], "green_screens": [],
            "handwrites": [], "hsl": [], "hsl_curves": [], "images": [],
            "log_color_wheels": [], "loudnesses": [], "manual_beautys": [],
            "manual_deformations": [], "material_animations": [], "material_colors": [],
            "multi_language_refs": [], "placeholders": [], "placeholder_infos": [],
            "plugin_effects": [], "primary_color_wheels": [], "realtime_denoises": [],
            "shapes": [], "smart_crops": [], "smart_relights": [],
            "sound_channel_mappings": [], "speeds": [], "stickers": [],
            "tail_leaders": [], "texts": mats_texto, "text_templates": [],
            "time_marks": [], "transitions": [], "videos": [mat_video],
            "video_effects": [], "video_radius": [], "video_shadows": [],
            "video_strokes": [], "video_trackings": [], "vocal_beautifys": [],
            "vocal_separations": [],
        },
        "mutable_config": None,
        "name": nome,
        "new_version": "171.0.0",
        "path": "",
        "platform": dict(_PLATFORM_INFO),
        "relationships": [],
        "render_index_track_mode_on": False,
        "retouch_cover": None,
        "smart_ads_info": None,
        "source": "default",
        "static_cover_image_path": "",
        "time_marks": None,
        "tracks": [
            {"attribute": 0, "flag": 0, "id": gerar_uuid(), "is_default_name": True, "name": "", "segments": clips_video, "type": "video"},
            {"attribute": 0, "flag": 0, "id": gerar_uuid(), "is_default_name": True, "name": "", "segments": clips_texto, "type": "text"},
        ],
        "uneven_animation_template_info": None,
        "update_time": int(time.time()),
        "version": 360000,
    }


def _criar_draft_meta(draft_id, nome, video_path, largura, altura, duracao_us, ts_agora, pasta_draft, pasta_destino):
    return {
        "cloud_draft_cover": False,
        "cloud_draft_sync": False,
        "cloud_package_completed_time": "",
        "draft_cloud_capcut_purchase_info": "",
        "draft_cloud_last_action_download": False,
        "draft_cloud_package_type": "",
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": "draft_cover.jpg",
        "draft_deeplink_url": "",
        "draft_enterprise_info": {"draft_enterprise_extra": "", "draft_enterprise_id": "", "draft_enterprise_name": "", "enterprise_material": []},
        "draft_fold_path": str(pasta_draft).replace("\\", "/"),
        "draft_id": draft_id,
        "draft_is_ae_produce": False,
        "draft_is_ai_packaging_used": False,
        "draft_is_ai_shorts": False,
        "draft_is_ai_translate": False,
        "draft_is_article_video_draft": False,
        "draft_is_cloud_temp_draft": False,
        "draft_is_from_deeplink": "false",
        "draft_is_invisible": False,
        "draft_is_pippit_draft": False,
        "draft_is_web_article_video": False,
        "draft_materials": [
            {"type": 0, "value": [{"ai_group_type": "", "create_time": int(time.time()), "duration": duracao_us, "enter_from": 0, "extra_info": Path(video_path).name, "file_Path": str(Path(video_path).resolve()).replace("\\", "/"), "height": altura, "id": str(uuid.uuid4()), "import_time": int(time.time()), "import_time_ms": ts_agora, "item_source": 1, "md5": "", "metetype": "video", "roughcut_time_range": {"duration": duracao_us, "start": 0}, "sub_time_range": {"duration": -1, "start": -1}, "type": 0, "width": largura}]},
            {"type": 1, "value": []}, {"type": 2, "value": []}, {"type": 3, "value": []},
            {"type": 6, "value": []}, {"type": 7, "value": []}, {"type": 8, "value": []},
        ],
        "draft_materials_copied_info": [],
        "draft_name": nome,
        "draft_need_rename_folder": False,
        "draft_new_version": "",
        "draft_removable_storage_device": "",
        "draft_root_path": str(pasta_destino).replace("\\", "/"),
        "draft_segment_extra_info": [],
        "draft_timeline_materials_size_": 0,
        "draft_type": "",
        "draft_web_article_video_enter_from": "",
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_entry_id": -1,
        "tm_draft_cloud_modified": 0,
        "tm_draft_cloud_parent_entry_id": -1,
        "tm_draft_cloud_space_id": -1,
        "tm_draft_cloud_user_id": -1,
        "tm_draft_create": ts_agora,
        "tm_draft_modified": ts_agora,
        "tm_draft_removed": 0,
        "tm_duration": duracao_us,
    }


def _materiais_obrigatorios_segmento(speed_val: float):
    """
    Cria os 6 materiais que TODO segmento de vídeo do CapCut precisa ter ligados,
    nas estruturas EXATAS lidas de um projeto real (versão 8.7.0 / draft 360000).
    Sem eles (ou faltando algum) o CapCut abre o projeto e fecha na hora.

    Retorna (refs_na_ordem_certa, dict_por_lista_de_material).
    """
    sid = gerar_uuid()   # speed
    pid = gerar_uuid()   # placeholder_info
    cid = gerar_uuid()   # canvas_color
    mid = gerar_uuid()   # sound_channel_mapping
    colid = gerar_uuid() # material_color
    vid = gerar_uuid()   # vocal_separation

    m_speed = {"id": sid, "type": "speed", "mode": 0, "speed": float(speed_val), "curve_speed": None}
    m_placeholder = {"id": pid, "type": "placeholder_info", "meta_type": "none",
                     "res_path": "", "res_text": "", "error_path": "", "error_text": ""}
    m_canvas = {"id": cid, "type": "canvas_color", "color": "", "blur": 0.0,
                "image": "", "album_image": "", "image_id": "", "image_name": "",
                "source_platform": 0, "team_id": ""}
    m_sound = {"id": mid, "type": "", "audio_channel_mapping": 0, "is_config_open": False}
    m_color = {"id": colid, "is_color_clip": False, "is_gradient": False,
               "solid_color": "", "gradient_colors": [], "gradient_percents": [],
               "gradient_angle": 90.0, "width": 0.0, "height": 0.0}
    m_vocal = {"id": vid, "type": "vocal_separation", "choice": 0, "removed_sounds": [],
               "time_range": None, "production_path": "", "final_algorithm": "", "enter_from": ""}

    # A ORDEM dos refs segue o projeto real
    refs = [sid, pid, cid, mid, colid, vid]
    por_lista = {
        "speeds": m_speed,
        "placeholder_infos": m_placeholder,
        "canvases": m_canvas,
        "sound_channel_mappings": m_sound,
        "material_colors": m_color,
        "vocal_separations": m_vocal,
    }
    return refs, por_lista


def _injetar_materiais_segmentos(draft_content, materiais_por_segmento):
    """
    Coloca os materiais obrigatórios dos segmentos em draft_content.materials.
    materiais_por_segmento: lista de dicts {lista: material} (um por segmento).
    """
    mats = draft_content.setdefault("materials", {})
    for por_lista in materiais_por_segmento:
        for lista, material in por_lista.items():
            mats.setdefault(lista, []).append(material)


def _resolver_referencias(draft_content, clips_video, materiais_transicao, keyframes_extras, lut_path):
    """
    Garante que NENHUM segmento referencie material inexistente (causa de o
    CapCut abrir e fechar). Os efeitos de dinamismo que JÁ estão aplicados
    diretamente no segmento (zoom via clip.scale, reframe via clip.transform,
    jump-cut via escala, velocidade via speed) são preservados.

    Keyframes (Ken Burns/whip/freeze) e transições usam um formato que ainda
    não foi validado contra a versão do CapCut instalada — por segurança são
    desativados aqui para o draft sempre abrir. Reativar só após validar o
    formato real exportando um projeto de teste do próprio CapCut.
    """
    mats = draft_content.setdefault("materials", {})

    # Coleta TODOS os IDs de materiais existentes (incluindo os obrigatórios
    # que acabaram de ser injetados: speeds, canvases, sound_channel_mappings...)
    ids_existentes = set()
    for chave, valor in mats.items():
        if isinstance(valor, list):
            for m in valor:
                if isinstance(m, dict) and "id" in m:
                    ids_existentes.add(m["id"])

    # Em cada segmento: remove keyframes (formato não validado) e mantém só
    # os extra_material_refs que apontam pra materiais REALMENTE existentes.
    for clip in clips_video:
        clip["keyframe_refs"] = []
        clip["common_keyframes"] = []
        refs = clip.get("extra_material_refs", [])
        clip["extra_material_refs"] = [r for r in refs if r in ids_existentes]

    # Transições e LUT desativados (formato não validado) — sem deixar refs soltas.
    mats["transitions"] = []
    if not isinstance(mats.get("keyframes"), dict):
        mats["keyframes"] = {
            "adjusts": [], "audios": [], "effects": [], "filters": [],
            "handwrites": [], "stickers": [], "texts": [], "videos": [],
        }
    else:
        mats["keyframes"]["videos"] = []
    mats.pop("luts", None)


def _criar_auxiliares(pasta_draft: Path):
    for nome in ["adjust_mask", "common_attachment", "loudness", "matting", "Resources", "smart_crop", "subdraft", "Timelines"]:
        (pasta_draft / nome).mkdir(exist_ok=True)
    for nome, conteudo in [
        ("draft_agency_config.json", {"material_status": []}),
        ("draft_biz_config.json", {"export_range": None, "use_premium": False}),
        ("draft_virtual_store.json", {"draft_materials": [], "time": int(time.time())}),
    ]:
        with open(pasta_draft / nome, "w", encoding="utf-8") as f:
            json.dump(conteudo, f)


def _registrar_root_meta(pasta_destino, pasta_draft, draft_id, nome, duracao_us, ts_agora):
    root_path = Path(pasta_destino) / "root_meta_info.json"
    if root_path.exists():
        shutil.copy2(root_path, str(root_path) + ".bak")
        with open(root_path, encoding="utf-8") as f:
            root = json.load(f)
    else:
        root = {"all_draft_store": [], "draft_ids": 0, "root_path": str(pasta_destino).replace("\\", "/")}

    entrada = {
        "cloud_draft_cover": False, "cloud_draft_sync": False,
        "draft_cloud_last_action_download": False, "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "", "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": str(pasta_draft / "draft_cover.jpg").replace("\\", "/"),
        "draft_fold_path": str(pasta_draft).replace("\\", "/"),
        "draft_id": draft_id,
        "draft_is_ai_shorts": False, "draft_is_cloud_temp_draft": False,
        "draft_is_invisible": False, "draft_is_web_article_video": False,
        "draft_json_file": str(pasta_draft / "draft_content.json").replace("\\", "/"),
        "draft_name": nome, "draft_new_version": "",
        "draft_root_path": str(pasta_destino).replace("\\", "/"),
        "draft_timeline_materials_size": 0, "draft_type": "",
        "draft_web_article_video_enter_from": "",
        "streaming_edit_draft_ready": True, "tm_draft_cloud_completed": "",
        "tm_draft_cloud_entry_id": -1, "tm_draft_cloud_modified": 0,
        "tm_draft_cloud_parent_entry_id": -1, "tm_draft_cloud_space_id": -1,
        "tm_draft_cloud_user_id": -1,
        "tm_draft_create": ts_agora, "tm_draft_modified": ts_agora,
        "tm_draft_removed": 0, "tm_duration": duracao_us,
    }

    root["all_draft_store"].insert(0, entrada)
    root["draft_ids"] = len(root["all_draft_store"])

    with open(root_path, "w", encoding="utf-8") as f:
        json.dump(root, f, ensure_ascii=False, separators=(",", ":"))
