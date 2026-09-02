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

        # ── Áudio (copia + duração real) ──
        tem_audio = bool(arquivo_audio and Path(arquivo_audio).is_file() and Path(arquivo_audio).stat().st_size > 0)
        audio_dst = None
        audio_dur_us = 0
        if tem_audio:
            audio_src = Path(arquivo_audio)
            audio_dst = draft_dir / (audio_src.name or "audio_original.mp3")
            if audio_src.resolve() != audio_dst.resolve():
                shutil.copy2(str(audio_src), str(audio_dst))
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
        for i, cena in enumerate(cenas, 1):
            dur = cena["_ts_fim"] - cena["_ts_start"]
            start = cena["_ts_start"]
            arquivo = cena.get("arquivo")
            media_type = cena.get("media_type", "photo")
            if not arquivo or not Path(arquivo).exists():
                arquivo = str(preto)
                media_type = "photo"
            src = Path(arquivo)
            ext = src.suffix.lower() or ".jpg"

            # ANTIGRAVITY: se o tipo declarado é "video" mas o arquivo é imagem
            # (PNG/JPG/JPEG/WEBP), força "photo" — nunca tratar imagem como vídeo.
            if media_type == "video" and arquivo and _is_image(arquivo):
                media_type = "photo"

            # ANTIGRAVITY BLINDAGEM: vídeo SEMPRE H.264/MP4 (CapCut old compat).
            # Clips de origem podem vir em H.265/HEVC/AV1 — converte antes de
            # copiar para dentro do draft (self-contained).
            if media_type == "video":
                src_compat = Path(garantir_video_h264_compat(str(src), destino_dir=str(draft_dir)))
                if src_compat.parent == draft_dir and src_compat.exists():
                    # conversão gravou no draft_dir — mantém o MESMO nome do arquivo
                    # (padrão {id}_{timestamp}.png/mp4, sem prefixo de índice inventado)
                    nome_midia = src_compat.name
                    destino_midia = draft_dir / nome_midia
                    if src_compat != destino_midia:
                        if destino_midia.exists():
                            destino_midia.unlink()
                        shutil.move(str(src_compat), str(destino_midia))
                else:
                    # já era H.264 (ou conversão falhou) — copia com o MESMO nome
                    nome_midia = src.name
                    destino_midia = draft_dir / nome_midia
                    if src.resolve() != destino_midia.resolve():
                        shutil.copy2(str(src), str(destino_midia))
            else:
                # Copia mídia para dentro do draft (self-contained) com o MESMO nome
                # do arquivo de origem ({id}_{timestamp}.png) — o path referenciado
                # em draft_content.json usa destino_midia.name, ficando IDÊNTICO.
                nome_midia = src.name if src.name else ('cena' + ext)
                destino_midia = draft_dir / nome_midia
                if src.resolve() != destino_midia.resolve():
                    shutil.copy2(str(src), str(destino_midia))
            path_rel = f"{nome_sanitizado}/{destino_midia.name}"  # usado no JSON

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
                mat["path"] = path_rel
                mat["material_name"] = destino_midia.name
                mats["videos"].append(mat)
                material_id = mat["id"]
            else:
                w, h = _dims_imagem(str(destino_midia))
                mphoto = copy.deepcopy(ref["material_photo"])
                mphoto["id"] = _novo_id()
                mphoto["path"] = path_rel
                mphoto["material_name"] = destino_midia.name
                mphoto["width"], mphoto["height"] = w, h
                mphoto["local_material_id"] = ""
                mphoto["duration"] = dur_us
                mats["videos"].append(mphoto)
                material_id = mphoto["id"]
                src_dur, speed = dur_us, 1.0

            # Materiais auxiliares do segmento (clona do ref, novo id)
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
            seg["target_timerange"] = {"start": start_us, "duration": dur_us}
            seg["extra_material_refs"] = refs
            if media_type == "video":
                if speed != 1.0:
                    seg["speed"] = speed
                seg["volume"] = 0.0  # muta o áudio do clipe (a trilha é a narração)
            render_index += 1
            seg["render_index"] = render_index
            segs_video.append(seg)

        duracao_total_us = _us(duracao_total_v)
# ── Áudio ──
        segs_audio = []
        if tem_audio and audio_dst:
            maaudio = copy.deepcopy(ref["material_audio"])
            maaudio["id"] = _novo_id()
            maaudio["path"] = f"{nome_sanitizado}/{audio_dst.name}"
            maaudio["name"] = audio_dst.name
            maaudio["duration"] = audio_dur_us
            maaudio["local_material_id"] = str(uuid.uuid4())
            maaudio["music_id"] = str(uuid.uuid4())
            mats["audios"].append(maaudio)

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

        # ── Trilhas ──
        tracks = []
        if tem_audio:
            tracks.append({
                "attribute": 0, "flag": 0, "id": cc.gerar_uuid(),
                "is_default_name": True, "name": "",
                "segments": segs_audio, "type": "audio",
            })
        tracks.append({
            "attribute": 0, "flag": 0, "id": cc.gerar_uuid(),
            "is_default_name": True, "name": "",
            "segments": segs_video, "type": "video",
        })
        draft["tracks"] = tracks
        draft["duration"] = max(duracao_total_us, audio_dur_us)

        # ── Capa ──
        _gerar_capa(draft_dir, cenas)

        # ── Salva draft_content.json ──
        with open(draft_dir / "draft_content.json", "w", encoding="utf-8") as f:
            json.dump(draft, f, ensure_ascii=False, separators=(",", ":"))

        # ── Meta + auxiliares + registro no root_meta_info.json ──
        ts_agora = cc.agora_us()
        draft_meta = cc._criar_draft_meta(draft_id, nome_sanitizado,
                                          str(audio_dst) if audio_dst else str(preto),
                                          LARGURA, ALTURA, draft["duration"],
                                          ts_agora, draft_dir, destino)
        with open(draft_dir / "draft_meta_info.json", "w", encoding="utf-8") as f:
            json.dump(draft_meta, f, ensure_ascii=False, separators=(",", ":"))

        cc._criar_auxiliares(draft_dir)
        cc._registrar_root_meta(destino, draft_dir, draft_id,
                                nome_sanitizado, draft["duration"], ts_agora)

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