"""
capcut_draft_imagens.py — Exportação de projeto ULTRACUT3 para o CapCut Desktop (9.x).

Gera uma pasta de rascunho (draft) no formato NATIVO do CapCut 9.x
(`draft_content.json` com `version=360000`, `tracks[*].segments`,
`materials` por tipo) — o MESMO schema que os projetos reais criados no
CapCut 9.3.0 desta máquina usam (pastas 0817/0819 em com.lveditor.draft).

CORREÇÃO (v9.3 / CapCut 9.x):
- Antes este módulo gravava `draft_version: "2.0.0"` (formato legado 2021,
  com `draft_content`, `timeline`, `materials` aninhado). O CapCut 9.3.0 NÃO
  migra mais drafts auto-gerados nesse schema: o rascunho aparece na lista,
  mas o app abre e fecha na hora (caso do projeto "dandelion").
- Agora o draft é montado exatamente como o fluxo ELTON validado
  (`capcut_draft.py` + `_ref_capcut_imagens.json` — estruturas lidas de
  projetos reais): `version=360000`, `tracks` com `segments`, `materials`
  achatado por tipo (videos/audios/speeds/...), plataforma real.

Assinaturas públicas `criar_draft_imagens(...)` e `detectar_pasta_drafts()`
preservadas (usadas por app_web.py e services/api_v2.py).
"""

import copy
import json
import os
import re
import shutil
import struct
import subprocess
import time
import tempfile
import uuid
from pathlib import Path

import capcut_draft as cc  # helpers validados do fluxo Elton (esqueleto nativo 360000)

# ANTIGRAVITY: garantia de codec H.264/MP4 nos clipes de vídeo antes do draft
from services.video_encoder import garantir_video_h264_compat

_REF_PATH = Path(__file__).resolve().parent / "_ref_capcut_imagens.json"

# Ordem EXATA dos materiais auxiliares que cada segmento precisa referenciar
# (lida de um projeto real montado à mão e validado no CapCut do usuário).
_ORDEM_AUX_VIDEO = ["speeds", "placeholder_infos", "canvases",
                    "material_animations", "sound_channel_mappings",
                    "material_colors", "vocal_separations"]
_ORDEM_AUX_AUDIO = ["speeds", "placeholder_infos", "beats",
                    "sound_channel_mappings", "vocal_separations"]

# Transición real de CapCut 9.1 disponible nesta máquina (formato "Combinar" lido de
# um draft real em .recycle_bin). Estrutura/material idênticos ao que o app grava.
_TRANS_CAPCUT_CACHE = str(Path.home() / "AppData/Local/CapCut/User Data/Cache/effect/6724845717472416269/7b53f4c008c4c684fccf8c7d4d46cc92").replace("\\", "/")
_TRANS_MATERIAL_REF = {
    "id": "96898E3E-A576-4b0f-BA26-FF768D6EF894",
    "type": "transition",
    "name": "Combinar",
    "effect_id": "6724845717472416269",
    "resource_id": "6724845717472416269",
    "third_resource_id": "6724845717472416269",
    "source_platform": 1,
    "path": _TRANS_CAPCUT_CACHE,
    "duration": 600000,
    "is_overlap": True,
    "platform": "all",
    "category_id": "100000",
    "category_name": "",
    "request_id": "",
    "is_ai_transition": False,
    "video_path": "",
    "task_id": "",
}


def _trans_request_id() -> str:
    """Simula o request_id real do CapCut (formato: YYYYMMDDHHMMSS + 16 hex)."""
    try:
        from datetime import datetime
        return "%s%016X" % (datetime.now().strftime("%Y%m%d%H%M%S"),
                            int(time.time() * 1000) % 0xFFFFFFFFFFFFFFFF)
    except Exception:
        return str(uuid.uuid4()).replace("-", "")[:30]


def _ref():
    return json.loads(_REF_PATH.read_text(encoding="utf-8"))


def _novo_id():
    return str(uuid.uuid4()).upper()


def _us(segundos):
    return int(round(segundos * 1_000_000))
def _dims_imagem(path):
    """Lê (width, height) de PNG/JPEG/WEBP sem dependências. Fallback 1920x1080."""
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", head[16:24])
                return int(w), int(h)
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                fmt = head[12:16]
                if fmt == b"VP8X":
                    f.seek(24)
                    b = f.read(6)
                    w = 1 + (b[0] | (b[1] << 8) | (b[2] << 16))
                    h = 1 + (b[3] | (b[4] << 8) | (b[5] << 16))
                    return w, h
                if fmt == b"VP8 ":
                    f.seek(26)
                    b = f.read(4)
                    w = ((b[1] << 8) | b[0]) & 0x3FFF
                    h = ((b[3] << 8) | b[2]) & 0x3FFF
                    return w, h
            # JPEG — percorre marcadores SOF
            f.seek(2)
            b = f.read(1)
            while b and b == b"\xff":
                marker = f.read(1)
                while marker == b"\xff":
                    marker = f.read(1)
                if marker and 0xC0 <= marker[0] <= 0xCF and marker[0] not in (0xC4, 0xC8, 0xCC):
                    f.read(3)
                    hh, ww = struct.unpack(">HH", f.read(4))
                    return int(ww), int(hh)
                seg_len = struct.unpack(">H", f.read(2))[0]
                f.seek(seg_len - 2, 1)
                b = f.read(1)
    except Exception:
        pass
    return 1920, 1080


def _dims_video(path, fw=1920, fh=1080):
    """(width, height) de um vídeo via PyAV; fallback (fw, fh)."""
    try:
        import av
        with av.open(path) as c:
            vs = c.streams.video[0]
            return int(vs.codec_context.width), int(vs.codec_context.height)
    except Exception:
        return fw, fh


def _duracao_audio_us(path):
    """Duração de um arquivo de áudio via ffprobe (µs). 0 se falhar."""
    try:
        from config import FFPROBE_PATH
        if not FFPROBE_PATH:
            return 0
        r = subprocess.run(
            [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return _us(float(r.stdout.strip()))
    except Exception:
        pass
    return 0


def _duracao_video_us(path):
    """Duração real de um vídeo via PyAV (µs). 0 se falhar."""
    try:
        return cc._duracao_video_us(path)
    except Exception:
        return 0


def _gerar_placeholder(preto_path: Path) -> Path:
    """Cria uma imagem preta 640x360 para cenas sem mídia (placeholder)."""
    if preto_path.exists():
        return preto_path
    try:
        from config import FFMPEG_PATH
        subprocess.run(
            [FFMPEG_PATH, "-y", "-v", "error",
             "-f", "lavfi", "-i", "color=c=black:s=640x360",
             "-frames:v", "1", str(preto_path)],
            capture_output=True, timeout=30,
        )
    except Exception:
        pass
    if preto_path.exists():
        return preto_path
    try:
        from PIL import Image
        Image.new("RGB", (640, 360), (0, 0, 0)).save(str(preto_path))
    except Exception:
        pass
    return preto_path


def _is_image(path: str) -> bool:
    return Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}


def _gerar_capa(draft_dir: Path, lista_cenas):
    """Gera draft_cover.jpg a partir da primeira mídia com imagem; senão deixa o arquivo de referência."""
    draft_cover = draft_dir / "draft_cover.jpg"
    cover_src = None
    for c in lista_cenas:
        arq = c.get("arquivo")
        if arq and Path(arq).exists():
            cover_src = Path(arq)
            break
    if not cover_src:
        return draft_cover if draft_cover.exists() else None
    try:
        from PIL import Image
        im = Image.open(cover_src).convert("RGB")
        im.save(str(draft_cover), "JPEG", quality=92)
    except Exception:
        try:
            if not draft_cover.exists():
                shutil.copy2(str(cover_src), str(draft_cover))
        except Exception:
            pass
    return draft_cover
def detectar_pasta_drafts() -> str:
    """
    Detecta a pasta oficial de rascunhos do CapCut PC.
    Retorna o caminho ou cria se a pasta base de projetos existir.
    """
    usuario = os.environ.get("USERNAME", "")
    candidatos = []
    if usuario:
        candidatos.append(
            rf"C:\Users\{usuario}\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft"
        )
        candidatos.append(
            rf"C:\Users\{usuario}\AppData\Local\CapCut\User Data\Projects\com\lveditor\draft"
        )
    candidatos += [
        str(Path.home() / r"AppData\Local\CapCut\User Data\Projects\com.lveditor.draft"),
        str(Path.home() / r"AppData\Local\CapCut\User Data\Projects\com\lveditor\draft"),
        r"C:\Users\Public\Documents\CapCut\User Data\Projects\com.lveditor.draft",
    ]
    for c in candidatos:
        if Path(c).exists() and Path(c).is_dir():
            return c

    # Se pasta pai de Projects existir, garante criação de com.lveditor.draft
    bases_pai = [
        rf"C:\Users\{usuario}\AppData\Local\CapCut\User Data\Projects" if usuario else "",
        str(Path.home() / r"AppData\Local\CapCut\User Data\Projects"),
    ]
    for base in bases_pai:
        if base and Path(base).exists():
            p = Path(base) / "com.lveditor.draft"
            p.mkdir(parents=True, exist_ok=True)
            return str(p)
    return ""


def detectar_versao_capcut() -> dict:
    """Detecta a versão do CapCut Desktop instalado (heurística).

    ANTIGRAVITY — validação de versão no export:
    - Lê 'version'/'displayVersion' do registro do Windows quando disponível;
    - Usa a presença da pasta oficial de drafts como confirmação de instalação.
    - `old=True` quando a versão é < 1.8 (formatos legados pré-360000 que NÃO
      leem o draft nativo 9.x gerado por este módulo).

    Retorna {"instalado": bool, "versao": str, "draft_version": int, "old": bool}.
    """
    versao = ""
    try:
        import winreg
        chaves = [
            r"Software\Bytedance\CapCut",
            r"Software\CapCut",
            r"Software\WOW6432Node\Bytedance\CapCut",
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall\CapCut",
        ]
        for chave in chaves:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, chave) as k:
                    for idx in range(winreg.QueryInfoKey(k)[1]):
                        nome, val, _ = winreg.EnumValue(k, idx)
                        nl = nome.lower()
                        if nl in ("version", "displayversion", "appversion", "version_number"):
                            versao = str(val)
                            break
                if versao:
                    break
            except OSError:
                continue
    except Exception:
        versao = ""

    pasta = detectar_pasta_drafts()
    instalado = bool(pasta) or bool(versao)
    if not versao and instalado:
        versao = "desconhecida"

    old = False
    if versao and versao != "desconhecida":
        try:
            partes = re.split(r"[.\-]", versao)
            major = int(partes[0]) if partes and partes[0].isdigit() else 0
            minor = int(partes[1]) if len(partes) > 1 and partes[1].isdigit() else 0
            old = (major, minor) < (1, 8)
        except Exception:
            old = False

    return {
        "instalado": instalado,
        "versao": versao or "",
        "draft_version": 360000,
        "old": old,
    }


def _gerar_keyframes_zoom(dur_us: int, ativo: bool, motion_preset: str = "") -> list:
    """
    Gera keyframes de movimento (Ken Burns/Pan) para o CapCut 9.1 conforme o
    motion_preset calculado no Studio (REDESIGN F1):
      - 'zoom_in'  : escala 1.0 → 1.15 (aproximando do centro)
      - 'zoom_out' : escala 1.15 → 1.0 (recuando para o centro)
      - 'pan_right': escala fixa 1.15 + KFTypePositionX 0 → +0.06
      - 'pan_left' : escala fixa 1.15 + KFTypePositionX 0 → -0.06
      - 'estatico' : [] (sem movimento — comportamento explícito)
    Retrocompatibilidade: ken_burns_ativo=True SEM motion_preset -> 'zoom_in'
    (mesmo comportamento do toggle antigo). Sem ativo e sem preset -> [].
    Retorna common_keyframes pronto para injetar no segmento.
    """
    mp = (motion_preset or "").strip().lower()

    # 'estatico' nunca gera keyframe, mesmo que ken_burns_ativo esteja True.
    if mp == "estatico":
        return []
    if mp not in ("zoom_in", "zoom_out", "pan_right", "pan_left"):
        if not ativo:
            return []
        mp = "zoom_in"  # fallback do toggle antigo

    def _escala(de, para):
        return [
            {
                "property_type": "KFTypeScaleX",
                "keyframe_list": [
                    {"time_offset": 0, "values": [de], "curveType": "Line"},
                    {"time_offset": dur_us, "values": [para], "curveType": "Line"},
                ],
            },
            {
                "property_type": "KFTypeScaleY",
                "keyframe_list": [
                    {"time_offset": 0, "values": [de], "curveType": "Line"},
                    {"time_offset": dur_us, "values": [para], "curveType": "Line"},
                ],
            },
        ]

    if mp == "zoom_in":
        return _escala(1.0, 1.15)
    if mp == "zoom_out":
        return _escala(1.15, 1.0)

    # pan: escala constante (margem de movimento) + deslocamento horizontal X
    delta = 0.06 if mp == "pan_right" else -0.06
    return _escala(1.15, 1.15) + [
        {
            "property_type": "KFTypePositionX",
            "keyframe_list": [
                {"time_offset": 0, "values": [0.0], "curveType": "Line"},
                {"time_offset": dur_us, "values": [delta], "curveType": "Line"},
            ],
        }
    ]


def _projeto_dir_de_audio(arquivo_audio: str):
    """Localiza o diretório raiz do projeto a partir do áudio (marcador lira_scene_plan.json)."""
    try:
        p = Path(str(arquivo_audio or "")).resolve()
        for anc in [p, *p.parents]:
            if (anc / "lira_scene_plan.json").is_file():
                return anc
    except Exception:
        return None
    return None


def _localizar_projeto_dir(arquivo_audio: str = "", lista_cenas: list = None):
    """Localiza o diretório raiz do projeto a partir do áudio ou das cenas (marcador lira_scene_plan.json)."""
    candidatos = [arquivo_audio]
    if lista_cenas:
        for c in lista_cenas:
            if isinstance(c, dict) and c.get("arquivo"):
                candidatos.append(c.get("arquivo"))
    for cand in candidatos:
        if not cand:
            continue
        try:
            p = Path(str(cand)).resolve()
            for anc in [p, *p.parents]:
                if (anc / "lira_scene_plan.json").is_file():
                    return anc
        except Exception:
            continue
    return None


# ── Estilos de Legenda ──────────────────────────────────────────
_CAPTION_STYLES = {
    "modern": {
        "font_size": 15.0,
        "font_color": "#ffffff",
        "border_width": 0.0,
        "border_color": "#000000",
        "background_color": "",
        "background_alpha": 0.0,
        "shadow_color": "#000000",
        "shadow_alpha": 0.8,
        "shadow_blur": 5.0,
        "shadow_distance": 3.0,
        "shadow_angle": -45.0,
    },
    "classic": {
        "font_size": 15.0,
        "font_color": "#FFE135",
        "border_width": 2.0,
        "border_color": "#000000",
        "background_color": "",
        "background_alpha": 0.0,
        "shadow_color": "#000000",
        "shadow_alpha": 0.0,
        "shadow_blur": 0.0,
        "shadow_distance": 0.0,
        "shadow_angle": 0.0,
    },
    "popup": {
        "font_size": 18.0,
        "font_color": "#ffffff",
        "border_width": 0.0,
        "border_color": "#000000",
        "background_color": "#000000",
        "background_alpha": 0.6,
        "shadow_color": "#000000",
        "shadow_alpha": 0.0,
        "shadow_blur": 0.0,
        "shadow_distance": 0.0,
        "shadow_angle": 0.0,
    },
}


def _gerar_trilha_texto(cenas: list, style_key: str = "modern") -> tuple:
    """
    Gera materials.texts[] e a trilha type='text' para o draft do CapCut 9.1.
    Retorna (lista_materials_text, trilha_dict).
    Cenas sem texto ou com texto vazio são ignoradas.
    """
    style = _CAPTION_STYLES.get(style_key, _CAPTION_STYLES["modern"])
    materials_texts = []
    segmentos = []

    for cena in cenas:
        texto = (cena.get("texto") or "").strip()
        if not texto:
            continue
        if not cena.get("caption_ativo", False):
            continue

        t_ini_us = int(round(cena["start"] * 1_000_000))
        dur_us   = int(round(cena["duracao"] * 1_000_000))

        mat_id = _novo_id()
        materials_texts.append({
            "id": mat_id,
            "type": "text",
            "content": f'<font color="{style["font_color"]}"><span>{texto}</span></font>',
            "font_title": "System Font",
            "font_path": "",
            "font_resource_id": "3911606",
            "font_size": style["font_size"],
            "font_color": style["font_color"],
            "text_alpha": 1.0,
            "align_type": 1,
            "typesetting": 0,
            "border_color": style["border_color"],
            "border_width": style["border_width"],
            "background_color": style["background_color"],
            "background_alpha": style["background_alpha"],
            "shadow_color": style["shadow_color"],
            "shadow_alpha": style["shadow_alpha"],
            "shadow_blur": style["shadow_blur"],
            "shadow_distance": style["shadow_distance"],
            "shadow_angle": style["shadow_angle"],
            "source_platform": 0,
        })

        segmentos.append({
            "id": _novo_id(),
            "material_id": mat_id,
            "source_timerange": {"start": 0, "duration": dur_us},
            "target_timerange": {"start": t_ini_us, "duration": dur_us},
            "render_index": 12000,
            "clip": {
                "scale": {"x": 1.0, "y": 1.0},
                "transform": {"x": 0.0, "y": -0.75},
                "rotation": 0.0,
                "flip": {"horizontal": False, "vertical": False},
                "alpha": 1.0,
            },
            "extra_material_refs": [],
            "keyframe_refs": [],
            "common_keyframes": [],
            "visible": True,
        })

    if not segmentos:
        return [], None

    trilha = {
        "attribute": 0,
        "flag": 0,
        "id": _novo_id(),
        "is_default_name": True,
        "name": "",
        "segments": segmentos,
        "type": "text",
    }
    return materials_texts, trilha


def criar_draft_imagens(project_name: str, lista_cenas: list, arquivo_audio: str,
                        destino_drafts: str, nome_projeto: str = None) -> dict:
    """
    Cria um rascunho CapCut (formato NATIVO 9.x — version=360000) com as cenas
    e o áudio original.

    lista_cenas: list de dicts:
        {"start": float, "arquivo": str|None, "media_type": "photo"|"video",
         "duracao": float}
    - Cenas com `arquivo` usam o arquivo (importado/baixado), copiado para
      dentro da pasta do draft (self-contained).
    - Cenas sem arquivo usam um placeholder (imagem preta).
    - A trilha de vídeo é montada respeitando `start`/`duracao` (alinhado ao
      áudio original), sem sobreposição.
    - O áudio original vira uma trilha de áudio (duração real via ffprobe).

    Retorna {"success": True, "draft_dir": str, "nome": str, "cenas_exportadas": N,
             "duracao_total": float, "registrado_capcut": True} em sucesso.
    """
    from services.event_logger import log_event

    nome = nome_projeto or project_name
    nome_sanitizado = "".join(c for c in nome if c not in '<>:"/\\|?*').strip()[:80] or project_name

    destino = Path(destino_drafts)
    if not destino.exists() or not destino.is_dir():
        return {"success": False, "error": f"Pasta de rascunhos do CapCut não encontrada: {destino_drafts}"}

    draft_dir = destino / nome_sanitizado
    if draft_dir.exists():
        for item in draft_dir.iterdir():
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                try:
                    item.unlink()
                except OSError:
                    pass
    draft_dir.mkdir(parents=True, exist_ok=True)

    try:
        ref = _ref()
        LARGURA, ALTURA = 1920, 1080
        FPS = 30.0
# ── Ordena cenas sem sobreposição (alinhado ao áudio) ──
        cenas = sorted(
            lista_cenas,
            key=lambda c: float(c.get("start", 0)) if c.get("start") is not None else 0,
        )
        prev_fim = 0.0
        for cena in cenas:
            dur = max(0.5, float(cena.get("duracao", 3.0)))
            start = float(cena.get("start", prev_fim)) if cena.get("start") is not None else prev_fim
            if start < prev_fim:
                start = prev_fim
            cena["_ts_start"] = start
            cena["_ts_fim"] = start + dur
            prev_fim = start + dur
        duracao_total_v = prev_fim

        # ── Placeholder para cenas sem mídia ──
        preto = draft_dir / "__placeholder_640x360.jpg"
        _gerar_placeholder(preto)

        anc_proj = _localizar_projeto_dir(arquivo_audio, lista_cenas)
        ts_agora = cc.agora_us()
        meta_materiais_map = {}

        # ── Áudio (copia + duração real) ──
        tem_audio = bool(arquivo_audio and Path(arquivo_audio).is_file() and Path(arquivo_audio).stat().st_size > 0)
        audio_dst = None
        audio_canonico = None
        audio_dur_us = 0
        if tem_audio:
            audio_src = Path(arquivo_audio)
            # ANTIGRAVITY: áudio master canônico em projetos/<id>/audio/audio_original.mp3
            # (minúsculo) — copiado quando o arquivo de origem é legado (raiz .MP3 etc.).
            try:
                _anc = anc_proj or _projeto_dir_de_audio(str(audio_src))
                _aud_dir = ((_anc / "audio") if _anc else (audio_src.parent / "audio"))
                _aud_dir.mkdir(parents=True, exist_ok=True)
                _canon = _aud_dir / "audio_original.mp3"
                if audio_src.resolve() != _canon.resolve():
                    shutil.copy2(str(audio_src), str(_canon))
                audio_canonico = _canon
            except Exception:
                audio_canonico = audio_src
            audio_dst = draft_dir / "audio_original.mp3"
            if Path(str(audio_canonico)).resolve() != audio_dst.resolve():
                shutil.copy2(str(audio_canonico), str(audio_dst))
            audio_dur_us = _duracao_audio_us(str(audio_dst))
            if not audio_dur_us:
                audio_dur_us = _us(duracao_total_v)

        # ── Esqueleto NATIVO (version=360000) ──
        draft_id = cc.gerar_uuid()
        mat_tmp = cc._criar_material_video(str(preto), LARGURA, ALTURA, 0)
        draft = cc._draft_minimo(draft_id, nome_sanitizado, _us(duracao_total_v),
                                 LARGURA, ALTURA, FPS, mat_tmp, [], [], [])
        draft["canvas_config"] = {"ratio": "original", "width": LARGURA,
                                  "height": ALTURA, "background": None}
        mats = draft["materials"]
        for chave in ["videos", "audios", "speeds", "placeholder_infos", "canvases",
                      "material_animations", "sound_channel_mappings", "material_colors",
                      "vocal_separations", "beats"]:
            mats[chave] = []

        # ── Loop de cenas: materiais + segmentos ──
        segs_video = []
        render_index = 0
        # Bloco 4: soma real dos arquivos de mídia copiados para o draft
        # (alimenta draft_timeline_materials_size[_] em draft_meta_info/root_meta_info).
        tamanho_materiais_bytes = 0
        for i, cena in enumerate(cenas, 1):
            dur = cena["_ts_fim"] - cena["_ts_start"]
            start = cena["_ts_start"]
            arquivo = cena.get("arquivo")
            media_type = cena.get("media_type", "photo")
            if not arquivo or not Path(arquivo).exists():
                arquivo = str(preto)
                media_type = "photo"
            src = Path(arquivo)
            ext = (src.suffix or ".jpg").lower()

            # ANTIGRAVITY: se o tipo declarado é "video" mas o arquivo é imagem
            # (PNG/JPG/JPEG/WEBP), força "photo" — nunca tratar imagem como vídeo.
            if media_type == "video" and arquivo and _is_image(arquivo):
                media_type = "photo"

            # Nome canônico da mídia com extensão minúscula
            base_nome = src.stem
            nome_midia = f"{base_nome}{ext}" if src.name else f"cena_{i}{ext}"

            # ANTIGRAVITY BLINDAGEM: se faz parte de um projeto Lira, garante cópia
            # canônica na pasta 'cenas/' (ex: caso venha de 'conteudo/' como a cena 38)
            midia_canonico = None
            if anc_proj and src.resolve() != preto.resolve():
                try:
                    _cenas_dir = anc_proj / "cenas"
                    _cenas_dir.mkdir(parents=True, exist_ok=True)
                    _canon = _cenas_dir / nome_midia
                    if src.is_file() and src.resolve() != _canon.resolve() and not _canon.exists():
                        shutil.copy2(str(src), str(_canon))
                    if _canon.is_file():
                        midia_canonico = _canon
                except Exception:
                    pass

            # ANTIGRAVITY BLINDAGEM: vídeo SEMPRE H.264/MP4 (CapCut old compat).
            # Clips de origem podem vir em H.265/HEVC/AV1 — converte antes de
            # copiar para dentro do draft (self-contained).
            if media_type == "video":
                src_compat = Path(garantir_video_h264_compat(str(midia_canonico or src), destino_dir=str(draft_dir)))
                if src_compat.parent == draft_dir and src_compat.exists():
                    # conversão gravou no draft_dir — mantém o MESMO nome do arquivo
                    # (padrão {id}_{timestamp}.png/mp4, sem prefixo de índice inventado)
                    nome_midia = f"{src_compat.stem}{src_compat.suffix.lower()}"
                    destino_midia = draft_dir / nome_midia
                    if src_compat != destino_midia:
                        if destino_midia.exists():
                            destino_midia.unlink()
                        shutil.move(str(src_compat), str(destino_midia))
                else:
                    # já era H.264 (ou conversão falhou) — copia com o MESMO nome
                    destino_midia = draft_dir / nome_midia
                    if src.resolve() != destino_midia.resolve():
                        shutil.copy2(str(midia_canonico or src), str(destino_midia))
            else:
                # Copia mídia para dentro do draft (self-contained) com o MESMO nome
                # do arquivo de origem ({id}_{timestamp}.png)
                destino_midia = draft_dir / nome_midia
                if src.resolve() != destino_midia.resolve():
                    shutil.copy2(str(midia_canonico or src), str(destino_midia))

            # Bloco 4: acumula o tamanho real do arquivo de mídia no draft
            try:
                if destino_midia.exists():
                    tamanho_materiais_bytes += destino_midia.stat().st_size
            except OSError:
                pass

            # ANTIGRAVITY: path ABSOLUTO canônico (minúsculo, barras '/') — mesmo padrão
            # rigoroso do áudio master para draft_content.json e draft_meta_info.json,
            # evitando definitivamente "Mídia perdida" no CapCut.
            midia_abs = str((midia_canonico or destino_midia).resolve()).replace("\\", "/")

            dur_us = _us(dur)
            start_us = _us(start)

            if media_type == "video":
                w, h = _dims_video(str(destino_midia), LARGURA, ALTURA)
                clip_us = _duracao_video_us(str(destino_midia)) or dur_us
                if clip_us >= dur_us:
                    src_dur = dur_us
                    speed = 1.0
                else:
                    src_dur = clip_us
                    speed = max(clip_us / dur_us, 0.1)
                mat = cc._criar_material_video(str(destino_midia), w, h, src_dur)
                mat["id"] = _novo_id()
                mat["path"] = midia_abs
                mat["material_name"] = destino_midia.name
                mats["videos"].append(mat)
                material_id = mat["id"]
            else:
                w, h = _dims_imagem(str(destino_midia))
                mphoto = copy.deepcopy(ref["material_photo"])
                mphoto["id"] = _novo_id()
                mphoto["path"] = midia_abs
                mphoto["material_name"] = destino_midia.name
                mphoto["width"], mphoto["height"] = w, h
                mphoto["local_material_id"] = ""
                mphoto["duration"] = dur_us
                mats["videos"].append(mphoto)
                material_id = mphoto["id"]
                src_dur, speed = dur_us, 1.0

            # Registra no pool de materiais para draft_meta_info.json (path absoluto)
            if midia_abs not in meta_materiais_map:
                meta_materiais_map[midia_abs] = {
                    "ai_group_type": "",
                    "create_time": int(time.time()),
                    "duration": dur_us,
                    "enter_from": 0,
                    "extra_info": destino_midia.name,
                    "file_Path": midia_abs,
                    "height": h,
                    "id": str(uuid.uuid4()),
                    "import_time": int(time.time()),
                    "import_time_ms": ts_agora,
                    "item_source": 1,
                    "material_color_tag": "",
                    "md5": "",
                    "metetype": "video" if media_type == "video" else "photo",
                    "roughcut_time_range": {"duration": -1, "start": -1},
                    "sub_time_range": {"duration": -1, "start": -1},
                    "type": 0,
                    "width": w,
                }

            # Materiais auxiliares do segmento (clona do ref, novo id)
            refs = []
            for lista in _ORDEM_AUX_VIDEO:
                aux = copy.deepcopy(ref["aux_video"][lista])
                aux["id"] = _novo_id()
                if lista == "speeds" and speed != 1.0:
                    aux["speed"] = speed
                mats[lista].append(aux)
                refs.append(aux["id"])

            # ── Transición de saída (P6) — material real do CapCut ("Combinar") ──
            # Aplica entre cenas adjacentes; nunca ultrapassa a duração da cena.
            # Bloco 6: NÃO aplica na ÚLTIMA cena (i == len(cenas)) — não existe clipe
            # subsequente para a transição de saída referenciar.
            if i != len(cenas):
                try:
                    _tr_saida = (cena or {}).get("transicao_saida") or {}
                    _tr_tipo = str(_tr_saida.get("tipo") or "fade_out")
                    if _tr_tipo and _tr_tipo != "none":
                        _tr_dur_ms = max(100, min(1000, int(_tr_saida.get("duracao_ms") or 300)))
                        mat_tr = copy.deepcopy(_TRANS_MATERIAL_REF)
                        mat_tr["id"] = _novo_id()
                        mat_tr["duration"] = _us(_tr_dur_ms / 1000.0)
                        mat_tr["request_id"] = _trans_request_id()
                        mats.setdefault("transitions", []).append(mat_tr)
                        # Ordem real observada no draft com transições: ref no índice 2
                        refs.insert(2, mat_tr["id"])
                except Exception as e_tr:
                    log_event("RENDER", f"[TRANS] aviso ao criar transição na cena {i}: {e_tr}", level="warn")

            # Segmento (clona do real, ajusta tempos e refs)
            seg = copy.deepcopy(ref["segmento_video"])
            seg["id"] = _novo_id()
            seg["material_id"] = material_id
            seg["source_timerange"] = {"start": 0, "duration": src_dur}
            seg["target_timerange"] = {"start": start_us, "duration": dur_us}
            seg["extra_material_refs"] = refs
            if media_type == "video":
                if speed != 1.0:
                    seg["speed"] = speed
                seg["volume"] = 0.0  # muta o áudio do clipe (a trilha é a narração)
            render_index += 1
            seg["render_index"] = render_index

            dur_us = seg["target_timerange"]["duration"]
            seg["common_keyframes"] = _gerar_keyframes_zoom(
                dur_us,
                bool(cena.get("ken_burns_ativo", False)),
                str(cena.get("motion_preset") or ""),
            )
            segs_video.append(seg)

        duracao_total_us = _us(duracao_total_v)
        # ── Áudio ──
        segs_audio = []
        if tem_audio and audio_dst:
            audio_path_abs = str(audio_canonico or audio_dst).replace("\\", "/")
            maaudio = copy.deepcopy(ref["material_audio"])
            maaudio["id"] = _novo_id()
            # ANTIGRAVITY: path ABSOLUTO canônico (minúsculo) do áudio master — mesmo
            # estilo de path absoluto das imagens no draft; evita "Mídia perdida".
            maaudio["path"] = audio_path_abs
            maaudio["name"] = (audio_canonico or audio_dst).name
            maaudio["duration"] = audio_dur_us
            maaudio["local_material_id"] = str(uuid.uuid4())
            maaudio["music_id"] = str(uuid.uuid4())
            mats["audios"].append(maaudio)

            if audio_path_abs not in meta_materiais_map:
                meta_materiais_map[audio_path_abs] = {
                    "ai_group_type": "",
                    "create_time": int(time.time()),
                    "duration": audio_dur_us,
                    "enter_from": 0,
                    "extra_info": (audio_canonico or audio_dst).name,
                    "file_Path": audio_path_abs,
                    "height": ALTURA,
                    "id": str(uuid.uuid4()),
                    "import_time": int(time.time()),
                    "import_time_ms": ts_agora,
                    "item_source": 1,
                    "material_color_tag": "",
                    "md5": "",
                    "metetype": "music",
                    "roughcut_time_range": {"duration": audio_dur_us, "start": 0},
                    "sub_time_range": {"duration": -1, "start": -1},
                    "type": 0,
                    "width": LARGURA,
                }

            refs_a = []
            for lista in _ORDEM_AUX_AUDIO:
                aux = copy.deepcopy(ref.get("aux_audio", {}).get(lista))
                if not isinstance(aux, dict):
                    aux = {"id": "", "type": lista}
                aux["id"] = _novo_id()
                mats.setdefault(lista, []).append(aux)
                refs_a.append(aux["id"])

            seg_a = copy.deepcopy(ref["segmento_audio"])
            seg_a["id"] = _novo_id()
            seg_a["material_id"] = maaudio["id"]
            seg_a["source_timerange"] = {"start": 0, "duration": audio_dur_us}
            seg_a["target_timerange"] = {"start": 0, "duration": audio_dur_us}
            seg_a["extra_material_refs"] = refs_a
            segs_audio.append(seg_a)

        # ── Trilha sonora (BGM) — lida de meta.json["trilha_sonora"] ──
        # Vai para uma TRILHA DE ÁUDIO SEPARADA (o CapCut não aceita dois
        # segmentos sobrepostos no mesmo track) e usa o MESMO material nativo
        # `material_audio`/`segmento_audio` do áudio original.
        segs_bgm = []
        bgm_dur_us = 0
        try:
            _proj_bgm = anc_proj or _projeto_dir_de_audio(str(arquivo_audio or ""))
            trilha_cfg = {}
            if _proj_bgm:
                _meta_bgm = Path(_proj_bgm) / "meta.json"
                if _meta_bgm.is_file():
                    _m = json.loads(_meta_bgm.read_text(encoding="utf-8")) or {}
                    trilha_cfg = _m.get("trilha_sonora") or {}
            bgm_arq = trilha_cfg.get("arquivo") if isinstance(trilha_cfg, dict) else None
            if bgm_arq and trilha_cfg.get("ativo", True):
                _bgm_path = Path(str(bgm_arq))
                if not _bgm_path.is_absolute() and _proj_bgm:
                    _bgm_path = Path(_proj_bgm) / str(bgm_arq)
                if _bgm_path.is_file() and _bgm_path.stat().st_size > 0:
                    bgm_dst = draft_dir / _bgm_path.name
                    if _bgm_path.resolve() != bgm_dst.resolve():
                        shutil.copy2(str(_bgm_path), str(bgm_dst))
                    bgm_dur_us = _duracao_audio_us(str(bgm_dst)) or _us(duracao_total_v)
                    bgm_abs = str(bgm_dst).replace("\\", "/")

                    mabgm = copy.deepcopy(ref["material_audio"])
                    mabgm["id"] = _novo_id()
                    mabgm["path"] = bgm_abs
                    mabgm["name"] = bgm_dst.name
                    mabgm["duration"] = bgm_dur_us
                    mabgm["local_material_id"] = str(uuid.uuid4())
                    mabgm["music_id"] = str(uuid.uuid4())
                    mats["audios"].append(mabgm)

                    if bgm_abs not in meta_materiais_map:
                        meta_materiais_map[bgm_abs] = {
                            "ai_group_type": "",
                            "create_time": int(time.time()),
                            "duration": bgm_dur_us,
                            "enter_from": 0,
                            "extra_info": bgm_dst.name,
                            "file_Path": bgm_abs,
                            "height": ALTURA,
                            "id": str(uuid.uuid4()),
                            "import_time": int(time.time()),
                            "import_time_ms": ts_agora,
                            "item_source": 1,
                            "material_color_tag": "",
                            "md5": "",
                            "metetype": "music",
                            "roughcut_time_range": {"duration": bgm_dur_us, "start": 0},
                            "sub_time_range": {"duration": -1, "start": -1},
                            "type": 0,
                            "width": LARGURA,
                        }

                    refs_bgm = []
                    for lista in _ORDEM_AUX_AUDIO:
                        aux = copy.deepcopy(ref.get("aux_audio", {}).get(lista))
                        if not isinstance(aux, dict):
                            aux = {"id": "", "type": lista}
                        aux["id"] = _novo_id()
                        mats.setdefault(lista, []).append(aux)
                        refs_bgm.append(aux["id"])

                    seg_bgm = copy.deepcopy(ref["segmento_audio"])
                    seg_bgm["id"] = _novo_id()
                    seg_bgm["material_id"] = mabgm["id"]
                    seg_bgm["source_timerange"] = {"start": 0, "duration": bgm_dur_us}
                    seg_bgm["target_timerange"] = {"start": 0, "duration": bgm_dur_us}
                    seg_bgm["extra_material_refs"] = refs_bgm
                    try:
                        seg_bgm["volume"] = float(trilha_cfg.get("volume", 0.14) or 0.14)
                    except (TypeError, ValueError):
                        seg_bgm["volume"] = 0.14
                    segs_bgm.append(seg_bgm)
                    log_event("CAPCUT", f"Trilha sonora (BGM) adicionada ao draft: "
                                        f"{bgm_dst.name} (vol={seg_bgm['volume']})", level="info")
        except Exception as e_bgm:  # noqa: BLE001
            log_event("CAPCUT", f"Aviso: falha ao adicionar trilha sonora ao draft: {e_bgm}", level="warn")

        # ── Trilhas ──
        # ORDEM OBRIGATÓRIA: a Main Track de vídeo SEMPRE em tracks[0].
        # O CapCut usa tracks[0] como trilha principal; se áudio/bgm vierem
        # primeiro, o vídeo não é reconhecido como main track.
        tracks = []
        tracks.append({
            "attribute": 0, "flag": 0, "id": cc.gerar_uuid(),
            "is_default_name": True, "name": "",
            "segments": segs_video, "type": "video",
        })
        if segs_audio:
            tracks.append({
                "attribute": 0, "flag": 0, "id": cc.gerar_uuid(),
                "is_default_name": True, "name": "",
                "segments": segs_audio, "type": "audio",
            })
        if segs_bgm:
            tracks.append({
                "attribute": 0, "flag": 0, "id": cc.gerar_uuid(),
                "is_default_name": True, "name": "",
                "segments": segs_bgm, "type": "audio",
            })
        draft["tracks"] = tracks

        # ── Legendas ──────────────────────────────────────────────────
        caption_style = next(
            (c.get("caption_style", "modern") for c in lista_cenas if c.get("texto")),
            "modern"
        )
        if any(c.get("texto") for c in lista_cenas):
            mats_text, trilha_texto = _gerar_trilha_texto(lista_cenas, caption_style)
            if trilha_texto:
                draft["materials"]["texts"] = mats_text
                draft["tracks"].append(trilha_texto)

        draft["duration"] = max(duracao_total_us, audio_dur_us, bgm_dur_us)

        # ── Capa ──
        _gerar_capa(draft_dir, cenas)

        # ── Salva draft_content.json com ESCRITURA ATÓMICA + VALIDACIÓN ──
        draft_path = draft_dir / "draft_content.json"
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".json", dir=str(draft_dir))
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f_tmp:
                json.dump(draft, f_tmp, ensure_ascii=False, separators=(",", ":"))
            # Validar: recargar el JSON temporal y verificar estructura CapCut
            with open(tmp_path, "r", encoding="utf-8") as f_val:
                draft_validado = json.load(f_val)
            for _chave_draft in ("version", "tracks", "materials"):
                if _chave_draft not in draft_validado:
                    raise ValueError(f"Draft sem chave obrigátoria '{_chave_draft}'")
            if not isinstance(draft_validado.get("tracks"), list) or not draft_validado["tracks"]:
                raise ValueError("Campo 'tracks' inválido ou vazio")
            if not isinstance(draft_validado.get("materials"), dict) or not draft_validado["materials"]:
                raise ValueError("Campo 'materials' inválido ou vazio")
            # Substituição atómica (nunca deja el archivo a medias)
            os.replace(tmp_path, draft_path)
            log_event("CAPCUT", f"draft_content.json gravado e validado atomicamente: {draft_path}", level="info")
        except (json.JSONDecodeError, ValueError, OSError) as _e_draft:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            log_event("CAPCUT", f"ERRO ao gravar/validar draft_content.json: {_e_draft}", level="error")
            raise

        # ── Meta + auxiliares + registro no root_meta_info.json ──
        draft_meta = cc._criar_draft_meta(draft_id, nome_sanitizado,
                                          str(audio_canonico or audio_dst).replace("\\", "/") if (audio_canonico or audio_dst) else str(preto),
                                          LARGURA, ALTURA, draft["duration"],
                                          ts_agora, draft_dir, destino,
                                          tamanho_materiais_bytes)
        if meta_materiais_map:
            draft_meta["draft_materials"][0]["value"] = list(meta_materiais_map.values())
        with open(draft_dir / "draft_meta_info.json", "w", encoding="utf-8") as f:
            json.dump(draft_meta, f, ensure_ascii=False, separators=(",", ":"))

        cc._criar_auxiliares(draft_dir, draft_id)
        cc._registrar_root_meta(destino, draft_dir, draft_id,
                                nome_sanitizado, draft["duration"], ts_agora,
                                tamanho_materiais_bytes)

        log_event("RENDER", f"CapCut draft criado (formato nativo 9.x version=360000): {draft_dir} "
                            f"({len(segs_video)} cenas, audio={'sim' if tem_audio else 'nao'})",
                  level="info")

        versao_capcut = detectar_versao_capcut()
        aviso_old = ""
        if versao_capcut.get("old"):
            aviso_old = ("⚠ Sua versão do CapCut Desktop é antiga (< 1.8). O draft gerado usa o "
                         "formato NATIVO 9.x (version=360000) que NÃO é lido por versões antigas. "
                         "Atualize o CapCut para abrir este projeto.")
            log_event("RENDER", aviso_old, level="warn")

        return {
            "success": True,
            "draft_dir": str(draft_dir),
            "nome": nome_sanitizado,
            "cenas_exportadas": len(segs_video),
            "duracao_total": round(duracao_total_v, 3),
            "registrado_capcut": True,
            "capcut_versao": versao_capcut.get("versao", ""),
            "capcut_instalado": versao_capcut.get("instalado", False),
            "capcut_old": versao_capcut.get("old", False),
            "aviso_capcut_old": aviso_old,
        }

    except Exception as e:  # noqa: BLE001
        try:
            log_event("RENDER", f"Falha ao criar draft CapCut: {e}", level="error")
        except Exception:
            pass
        return {"success": False, "error": str(e)}