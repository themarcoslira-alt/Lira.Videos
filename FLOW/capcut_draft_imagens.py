"""
Gera um draft do CapCut com IMAGENS cena-por-cena + faixa de ÁUDIO,
totalmente editável (cada cena é um segmento, dá pra ajustar duração e
transição no CapCut). Os formatos de material foram extraídos de um
projeto REAL montado à mão e validado no CapCut do usuário (0623).

Uso:
    criar_draft_imagens(imagens, audio_path, audio_dur_seg, pasta_destino,
                        nome_projeto, largura, altura, fps)
    imagens = lista de (timestamp_segundos, caminho_imagem) ordenada.
"""

import copy
import json
import struct
import uuid
from pathlib import Path

import capcut_draft as cc  # reusa helpers validados (meta, root, auxiliares)

_REF_PATH = Path(__file__).resolve().parent / "_ref_capcut_imagens.json"

# Ordem EXATA dos materiais auxiliares em cada segmento (lida do projeto real)
_ORDEM_AUX_VIDEO = ["speeds", "placeholder_infos", "canvases",
                    "material_animations", "sound_channel_mappings",
                    "material_colors", "vocal_separations"]
_ORDEM_AUX_AUDIO = ["speeds", "placeholder_infos", "beats",
                    "sound_channel_mappings", "vocal_separations"]


def _ref():
    return json.loads(_REF_PATH.read_text(encoding="utf-8"))


def _novo_id():
    return str(uuid.uuid4()).upper()


def _dims_imagem(path: str):
    """Lê (width, height) de PNG/JPEG/WEBP sem dependências. Fallback 1920x1080."""
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            # PNG
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", head[16:24])
                return int(w), int(h)
            # WEBP (VP8X / VP8 / VP8L)
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


def _us(segundos: float) -> int:
    return int(round(segundos * 1_000_000))


def _dims_video(path: str, fw: int, fh: int):
    """(width, height) do vídeo via PyAV; cai para (fw, fh) se falhar."""
    try:
        import av
        with av.open(path) as c:
            vs = c.streams.video[0]
            return int(vs.codec_context.width), int(vs.codec_context.height)
    except Exception:
        return fw, fh


def criar_draft_imagens(imagens, audio_path, audio_dur_seg, pasta_destino,
                        nome_projeto="EltonVideo_Cenas",
                        largura=1920, altura=1080, fps=30, media_type="image"):
    """imagens = lista de (timestamp_seg, caminho). media_type: 'image' ou 'video'.
    Vídeo: cada clipe ocupa exatamente o slot [ts→próximo]; se for maior corta,
    se for menor preenche o slot ajustando a velocidade (sem buracos)."""
    if not imagens:
        raise ValueError("Nenhuma mídia fornecida.")
    ref = _ref()
    audio_dur_us = _us(audio_dur_seg)

    # ── Esqueleto de topo: reaproveita a estrutura mínima validada ──
    draft_id = cc.gerar_uuid()
    mat_dummy = cc._criar_material_video(audio_path, largura, altura, 0)  # placeholder p/ _draft_minimo
    draft = cc._draft_minimo(draft_id, nome_projeto, audio_dur_us,
                             largura, altura, fps, mat_dummy, [], [], [])
    draft["canvas_config"] = {"ratio": "original", "width": largura,
                              "height": altura, "background": None}
    mats = draft["materials"]
    # zera o que vamos preencher
    for chave in ["videos", "audios", "speeds", "placeholder_infos", "canvases",
                  "material_animations", "sound_channel_mappings", "material_colors",
                  "vocal_separations", "beats"]:
        mats[chave] = []

    # Agrupa imagens por timestamp. Quando há mais de uma cena no mesmo
    # segundo, o bloco (do ts até o próximo ts distinto) é dividido
    # igualmente entre elas — evita micro-segmentos de 0,1s.
    from itertools import groupby
    grupos = [(ts, [p for _, p in it]) for ts, it in groupby(imagens, key=lambda x: x[0])]

    segs_video = []
    offset = 0
    for gi, (ts, paths) in enumerate(grupos):
        fim = grupos[gi + 1][0] if gi + 1 < len(grupos) else audio_dur_seg
        total_us = max(_us(fim) - _us(ts), 100_000)
        n = len(paths)
        base_dur = total_us // n
        for k, midia_path in enumerate(paths):
            # a última do grupo absorve o resto da divisão (sem drift)
            dur_us = base_dur if k < n - 1 else (total_us - base_dur * (n - 1))

            if media_type == "video":
                # Clipe de vídeo: encaixa no slot. Maior→corta; menor→preenche
                # ajustando a velocidade (speed<1 estica), sem buracos.
                w, h = _dims_video(midia_path, largura, altura)
                clip_us = cc._duracao_video_us(midia_path) or dur_us
                if clip_us >= dur_us:
                    src_dur, speed = dur_us, 1.0
                else:
                    src_dur, speed = clip_us, max(clip_us / dur_us, 0.1)
                mat = cc._criar_material_video(midia_path, w, h, clip_us)
                mat["id"] = _novo_id()
                mats["videos"].append(mat)
                material_id = mat["id"]
            else:
                # Imagem parada (material photo do projeto real)
                w, h = _dims_imagem(midia_path)
                mphoto = copy.deepcopy(ref["material_photo"])
                mphoto["id"] = _novo_id()
                mphoto["path"] = str(Path(midia_path).resolve()).replace("\\", "/")
                mphoto["material_name"] = Path(midia_path).name
                mphoto["width"], mphoto["height"] = w, h
                mphoto["local_material_id"] = ""
                mats["videos"].append(mphoto)
                material_id = mphoto["id"]
                src_dur, speed = dur_us, 1.0

            # Materiais auxiliares do segmento (clona cada um, novo id)
            refs = []
            for lista in _ORDEM_AUX_VIDEO:
                aux = copy.deepcopy(ref["aux_video"][lista])
                aux["id"] = _novo_id()
                if lista == "speeds" and speed != 1.0:
                    aux["speed"] = speed
                mats[lista].append(aux)
                refs.append(aux["id"])

            # Segmento (clona do real, ajusta tempos e refs)
            seg = copy.deepcopy(ref["segmento_video"])
            seg["id"] = _novo_id()
            seg["material_id"] = material_id
            seg["source_timerange"] = {"start": 0, "duration": src_dur}
            seg["target_timerange"] = {"start": offset, "duration": dur_us}
            seg["extra_material_refs"] = refs
            if media_type == "video":
                if speed != 1.0 and "speed" in seg:
                    seg["speed"] = speed
                if "volume" in seg:      # muta o áudio do clipe (a trilha é a narração)
                    seg["volume"] = 0.0
            segs_video.append(seg)
            offset += dur_us

    # ── Áudio ──
    maudio = copy.deepcopy(ref["material_audio"])
    maudio["id"] = _novo_id()
    maudio["path"] = str(Path(audio_path).resolve()).replace("\\", "/")
    maudio["name"] = Path(audio_path).name
    maudio["duration"] = audio_dur_us
    maudio["local_material_id"] = str(uuid.uuid4())
    maudio["music_id"] = str(uuid.uuid4())
    mats["audios"].append(maudio)

    refs_a = []
    for lista in _ORDEM_AUX_AUDIO:
        aux = copy.deepcopy(ref["aux_audio"][lista])
        aux["id"] = _novo_id()
        mats.setdefault(lista, []).append(aux)
        refs_a.append(aux["id"])

    seg_a = copy.deepcopy(ref["segmento_audio"])
    seg_a["id"] = _novo_id()
    seg_a["material_id"] = maudio["id"]
    seg_a["source_timerange"] = {"start": 0, "duration": audio_dur_us}
    seg_a["target_timerange"] = {"start": 0, "duration": audio_dur_us}
    seg_a["extra_material_refs"] = refs_a

    # ── Trilhas ──
    draft["tracks"] = [
        {"attribute": 0, "flag": 0, "id": cc.gerar_uuid(), "is_default_name": True,
         "name": "", "segments": segs_video, "type": "video"},
        {"attribute": 0, "flag": 0, "id": cc.gerar_uuid(), "is_default_name": True,
         "name": "", "segments": [seg_a], "type": "audio"},
    ]
    draft["duration"] = max(offset, audio_dur_us)

    # ── Salva pasta do draft ──
    nome_pasta = nome_projeto.replace("/", "_").replace("\\", "_").replace(":", "_")
    pasta_draft = Path(pasta_destino) / nome_pasta
    sufixo = 1
    while pasta_draft.exists():
        pasta_draft = Path(pasta_destino) / f"{nome_pasta}_{sufixo}"
        sufixo += 1
    pasta_draft.mkdir(parents=True, exist_ok=True)

    with open(pasta_draft / "draft_content.json", "w", encoding="utf-8") as f:
        json.dump(draft, f, ensure_ascii=False, separators=(",", ":"))

    ts_agora = cc.agora_us()
    draft_meta = cc._criar_draft_meta(draft_id, nome_projeto, audio_path,
                                      largura, altura, draft["duration"],
                                      ts_agora, pasta_draft, pasta_destino)
    with open(pasta_draft / "draft_meta_info.json", "w", encoding="utf-8") as f:
        json.dump(draft_meta, f, ensure_ascii=False, separators=(",", ":"))

    cc._criar_auxiliares(pasta_draft)
    cc._registrar_root_meta(pasta_destino, pasta_draft, draft_id,
                            nome_projeto, draft["duration"], ts_agora)
    return str(pasta_draft)
