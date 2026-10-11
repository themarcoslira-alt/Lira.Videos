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
from services.capcut_library_service import capcut_library

_REF_PATH = Path(__file__).resolve().parent / "_ref_capcut_imagens.json"

# Ordem EXATA dos materiais auxiliares que cada segmento precisa referenciar
# (lida de um projeto real montado à mão e validado no CapCut do usuário).
_ORDEM_AUX_VIDEO = ["speeds", "placeholder_infos", "canvases",
                    "material_animations", "sound_channel_mappings",
                    "material_colors", "vocal_separations"]
_ORDEM_AUX_AUDIO = ["speeds", "placeholder_infos", "beats",
                    "sound_channel_mappings", "vocal_separations"]



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
    Gera keyframes de movimento (Ken Burns/Pan) para o CapCut 9.x conforme o
    motion_preset calculado no Studio:
      - 'zoom_in'       : escala 1.0 → 1.15 (aproximando do centro)
      - 'zoom_out'      : escala 1.15 → 1.0 (recuando para o centro)
      - 'zoom_in_slow'  : escala 1.0 → 1.08 (aproximação suave/cinematográfica)
      - 'zoom_out_slow' : escala 1.08 → 1.0 (recuo suave/cinematográfico)
      - 'pan_right'     : escala fixa 1.15 + KFTypePositionX 0 → +0.06
      - 'pan_left'      : escala fixa 1.15 + KFTypePositionX 0 → -0.06
      - 'pan_up'        : escala fixa 1.15 + KFTypePositionY 0 → +0.06
      - 'pan_down'      : escala fixa 1.15 + KFTypePositionY 0 → -0.06
      - 'estatico'      : [] (sem movimento — comportamento explícito)
    Retrocompatibilidade: ken_burns_ativo=True SEM motion_preset -> 'zoom_in'
    (mesmo comportamento do toggle antigo). Sem ativo e sem preset -> [].
    Retorna common_keyframes pronto para injetar no segmento.
    """
    mp = (motion_preset or "").strip().lower()

    # 'estatico' nunca gera keyframe, mesmo que ken_burns_ativo esteja True.
    if mp == "estatico":
        return []
    
    presets_suportados = (
        "zoom_in", "zoom_out", "zoom_in_slow", "zoom_out_slow",
        "pan_right", "pan_left", "pan_up", "pan_down"
    )
    if mp not in presets_suportados:
        if not ativo:
            return []
        mp = "zoom_in"  # fallback do toggle antigo

    def _escala(de: float, para: float, curve: str = "Line"):
        return [
            {
                "property_type": "KFTypeScaleX",
                "keyframe_list": [
                    {"time_offset": 0, "values": [de], "curveType": curve},
                    {"time_offset": dur_us, "values": [para], "curveType": curve},
                ],
            },
            {
                "property_type": "KFTypeScaleY",
                "keyframe_list": [
                    {"time_offset": 0, "values": [de], "curveType": curve},
                    {"time_offset": dur_us, "values": [para], "curveType": curve},
                ],
            },
        ]

    def _posicao(eixo: str, de: float, para: float, curve: str = "Line"):
        prop = "KFTypePositionX" if eixo == "X" else "KFTypePositionY"
        return {
            "property_type": prop,
            "keyframe_list": [
                {"time_offset": 0, "values": [de], "curveType": curve},
                {"time_offset": dur_us, "values": [para], "curveType": curve},
            ],
        }

    if mp == "zoom_in":
        return _escala(1.0, 1.15)
    if mp == "zoom_out":
        return _escala(1.15, 1.0)
    if mp == "zoom_in_slow":
        return _escala(1.0, 1.08)
    if mp == "zoom_out_slow":
        return _escala(1.08, 1.0)

    # Pans horizontais
    if mp in ("pan_right", "pan_left"):
        delta = 0.06 if mp == "pan_right" else -0.06
        return _escala(1.15, 1.15) + [_posicao("X", 0.0, delta)]

    # Pans verticais
    if mp in ("pan_up", "pan_down"):
        delta = 0.06 if mp == "pan_up" else -0.06
        return _escala(1.15, 1.15) + [_posicao("Y", 0.0, delta)]

    return []


def _gerar_keyframes_entrada_texto(dur_us: int, fade_ms: int = 300, slide: bool = False,
                                   y_final: float = 0.0) -> list:
    """
    Fade (e slide opcional) de ENTRADA para segmentos de TEXTO (legenda/badge),
    via keyframe nativo do CapCut — 100% LOCAL: dado puro do segmento, sem
    `resource_id`, sem `depend`, sem CDN, sem catálogo externo.

    property_type de opacidade de texto: "KFTypeGlobalAlpha" (confirmado
    empiricamente em draft real; o motor aceita também KFTypePositionX/Y,
    KFTypeScaleX/Y, KFTypeRotation e KFTypeVolume).

    ATENÇÃO (semântica): keyframe SOBRESCREVE a propriedade ao longo do tempo
    com valores ABSOLUTOS — o `clip.alpha`/`clip.transform` estáticos continuam
    existindo em paralelo, mas o ÚLTIMO ponto precisa fechar no valor final
    desejado (1.0 no alpha; `y_final` na posição), senão o texto fica com
    opacidade/posição errada depois do fade.

    Retorna `common_keyframes` pronto para injetar no segmento (lista vazia
    quando não há duração válida).
    """
    try:
        dur_us = int(dur_us)
    except (TypeError, ValueError):
        return []
    if dur_us <= 0:
        return []

    fade_us = min(int(fade_ms) * 1000, max(1, dur_us // 3))
    kfs = [{
        "property_type": "KFTypeGlobalAlpha",
        "keyframe_list": [
            {"time_offset": 0,       "values": [0.0], "curveType": "Line"},
            {"time_offset": fade_us, "values": [1.0], "curveType": "Line"},
        ],
    }]
    if slide:
        kfs.append({
            "property_type": "KFTypePositionY",
            "keyframe_list": [
                {"time_offset": 0,       "values": [y_final - 0.06], "curveType": "Line"},
                {"time_offset": fade_us, "values": [y_final],        "curveType": "Line"},
            ],
        })
    return kfs


def _norm_projeto(valor) -> str:
    """Normaliza um nome de projeto/draft para comparação (a-z0-9, minúsculo)."""
    return re.sub(r"[^a-z0-9]+", "", str(valor or "").lower())


def _anc_eh_do_projeto_alvo(anc, project_name: str, nome_sanitizado: str = None) -> bool:
    """
    True só quando `anc` é o diretório do PROJETO-ALVO desta exportação.

    ANTIGRAVITY (correção do bug de sobrescrita de áudio):
    `project_name` (1º argumento = projeto_id, que também é o nome da pasta em
    projetos/<id>) é a referência autoritativa; `nome_sanitizado` (nome do draft,
    que nos fluxos legados pode ser um display_name) é aceito como alternativa
    com tolerância de prefixo.

    Isso impede que uma exportação para o projeto A resolva (e GRAVE) dentro do
    projeto B — foi o caso real em que um draft de teste com o áudio do GYPSUM
    sobrescreveu projetos/<GYPSUM>/audio/audio_original.mp3.
    """
    nome_anc = _norm_projeto(Path(anc).name if anc is not None else "")
    if not nome_anc:
        return False
    for alvo in (_norm_projeto(project_name), _norm_projeto(nome_sanitizado)):
        if not alvo:
            continue
        if nome_anc == alvo:
            return True
        if alvo.startswith("teste") or alvo.startswith("test"):
            continue
        if nome_anc.startswith(alvo) or alvo.startswith(nome_anc):
            return True
    return False


def _log_audio_projeto_estranho(anc, project_name, origem: str) -> None:
    """Aviso (best-effort) quando a resolução do projeto aponta para OUTRO projeto."""
    try:
        from services.event_logger import log_event
        log_event(
            "CAPCUT_AUDIO_PROJETO_ESTRANHO",
            "RESOLUCAO RECUSADA: o alvo da exportacao e projetos/%s, mas %s resolveu "
            "para %s. O audio NAO foi canonicalizado/gravado naquele projeto "
            "(evita sobrescrever audio_original.mp3 de outro projeto)."
            % (project_name, origem, anc),
            level="warn",
        )
    except Exception:
        pass


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


def _localizar_projeto_dir(arquivo_audio: str = "", lista_cenas: list = None,
                           nome_projeto: str = None):
    """
    Localiza o diretório raiz do projeto a partir do áudio ou das cenas (marcador
    lira_scene_plan.json).

    ANTIGRAVITY: quando `nome_projeto` (o ALVO da exportação) é informado, a
    resolução só aceita um diretório que corresponda a esse projeto; qualquer
    outro é RECUSADO (com log de aviso) em vez de devolvido silenciosamente.
    """
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
                    if nome_projeto and not _anc_eh_do_projeto_alvo(anc, nome_projeto):
                        _log_audio_projeto_estranho(anc, nome_projeto, str(cand))
                        return None
                    return anc
        except Exception:
            continue
    return None


# ── Estilos de Legenda ──────────────────────────────────────────


# TAREFA 4 — posição da legenda -> transform normalizado do CapCut (x, y em -1..1).
# y: topo 0.75 / centro 0.0 / base -0.75.  x: esquerda -0.5 / centro 0.0 / direita 0.5.
# O default é (0.0, -0.75) — EXATAMENTE o valor que era hardcoded antes desta tarefa,
# portanto projetos sem caption_custom continuam idênticos (zero regressão).
_CAPTION_POS_TRANSFORM = {
    "top-left": (-0.5, 0.75),
    "top-center": (0.0, 0.75),
    "top-right": (0.5, 0.75),
    "middle-left": (-0.5, 0.0),
    "middle-center": (0.0, 0.0),
    "middle-right": (0.5, 0.0),
    "bottom-left": (-0.5, -0.75),
    "bottom-center": (0.0, -0.75),
    "bottom-right": (0.5, -0.75),
}


def _transform_posicao_legenda(posicao) -> tuple:
    """Converte a posição lógica ("bottom-center") no transform (x, y) do CapCut."""
    return _CAPTION_POS_TRANSFORM.get(str(posicao or "").strip().lower(), (0.0, -0.75))


# ---------------------------------------------------------------------------
# TAREFA 2 — BADGE EDITORIAL ("Passo N" / "Erro N") como overlay próprio
# ---------------------------------------------------------------------------
# Preset PRÓPRIO do badge: deliberadamente NÃO reusa o estilo da legenda, para o
# badge ficar visualmente distinto do conteúdo da cena.
_PRESET_BADGE_EDITORIAL = "borda_preta_pop"

# render_index do badge é MAIOR que o da legenda (12000, valor legado mantido
# intacto logo abaixo) => o badge fica ACIMA na composição.
RENDER_INDEX_BADGE_EDITORIAL = 12010

# TAREFA 2 — duração de exibição do badge ancorado na palavra (segundos).
# É só o DEFAULT: o valor efetivo vem de editorial_style["duration_s"] (gravado
# por scene_plan_service), limitado ao fim da cena.
_BADGE_DURATION_DEFAULT_S = 3.0

# TAREFA 2 (v3) — PISO MÍNIMO legível. Sem ele, um badge cujo anchor cai perto
# do fim da cena saía com 0,48s/0,58s (medido no GYPSUM) — ilegível.
# O piso PODE ultrapassar o fim da cena da própria narração: o badge é uma
# camada de texto sobreposta (render_index próprio) e o vídeo de fundo continua
# trocando normalmente, então a "invasão" visual da cena seguinte é aceitável.
# NÃO há mais truncamento por fim de cena.
_BADGE_DURATION_MIN_S = 1.5

# TAREFA 1 (v4) — TETO de segurança. Com o piso mas SEM teto, a invasão da cena
# seguinte ficava ilimitada: em roteiros com cenas curtas isso poderia encadear
# invasões profundas ou colisões reais entre badges. O teto é FIXO (4,0s) e
# independente de onde a cena termina — NÃO é o truncamento antigo por fim de cena.
# Duração efetiva do badge fica sempre no intervalo [1.5s, 4.0s].
_BADGE_DURATION_MAX_S = 4.0


def _log_badge_sobreposicao(a: dict, b: dict) -> None:
    """
    Aviso (best-effort, nunca quebra o export) de SOBREPOSIÇÃO entre o badge de
    uma cena e o badge da cena seguinte — possível desde que o piso mínimo passou
    a permitir ultrapassar o fim da cena.
    """
    try:
        from services.event_logger import log_event
        log_event(
            "CAPCUT_BADGE_SOBREPOSICAO",
            "badge '%s' (cena %s) vai de %.3fs a %.3fs e SOBREPOE o badge '%s' "
            "(cena %s), que comeca em %.3fs (sobreposicao de %.3fs)."
            % (a.get("badge"), a.get("cena"), a.get("ini_s"), a.get("fim_s"),
               b.get("badge"), b.get("cena"), b.get("ini_s"),
               a.get("fim_s") - b.get("ini_s")),
            level="warn",
        )
    except Exception:
        pass


def _log_badge_dessincronia(cena: dict, t_ini_s: float, anchor_s: float) -> None:
    """
    Aviso (best-effort, nunca quebra o export) de dessincronia entre
    word_timestamps e a fronteira da cena.

    Caso real medido no roteiro GYPSUM: a cena 192 tem anchor.s ANTES do
    tempo_inicio da cena (a frase "Mistake three" é falada no fim da cena
    anterior). O badge é clampado no início da cena e fica registrado no log
    para revisão manual das fronteiras do SRT/plano.
    """
    try:
        from services.event_logger import log_event
        log_event(
            "CAPCUT_BADGE_DESSINCRONIA",
            "cena %s: anchor.s=%.3fs é ANTERIOR ao inicio da cena (%.3fs); "
            "badge ancorado no inicio (clamp) — revisar fronteiras do SRT/plano."
            % (cena.get("id"), anchor_s, t_ini_s),
            level="warn",
        )
    except Exception:
        pass

# Posição padrão da legenda quando a cena NÃO define caption_custom.position:
# bottom-center (mesmo default do preview em CSS e do transform legado y=-0.75).
_CAPTION_POS_PADRAO = "bottom-center"

# Banda OPOSTA: a legenda pode ocupar QUALQUER uma das 9 posições via
# caption_custom.position, então o badge é posicionado por lookup EXPLÍCITO —
# sem y fixo hardcoded.
#   top*    <-> bottom*  (equivalente, na mesma coluna)
#   middle* ->  top*     (a faixa do meio fica livre; o badge usa a faixa de cima,
#                         mantendo a coluna alinhada)
_BADGE_POS_OPOSTA = {
    "top-left": "bottom-left",
    "top-center": "bottom-center",
    "top-right": "bottom-right",
    "middle-left": "top-left",
    "middle-center": "top-center",
    "middle-right": "top-right",
    "bottom-left": "top-left",
    "bottom-center": "top-center",
    "bottom-right": "top-right",
}


def _banda_oposta_legenda(posicao) -> str:
    """
    Posição do badge editorial = banda OPOSTA à da legenda da cena.

    Aceita a posição da legenda (caption_custom.position) e devolve a posição do
    badge. Posição ausente/desconhecida cai no default da legenda (bottom-center),
    logo o badge vai para top-center.
    """
    chave = str(posicao or "").strip().lower()
    if chave not in _BADGE_POS_OPOSTA:
        chave = _CAPTION_POS_PADRAO
    return _BADGE_POS_OPOSTA[chave]


def _gerar_trilha_texto(cenas: list, style_key: str = "amarelo_capcut") -> tuple:
    """
    Gera materials.texts[] e trilhas type='text' para o draft do CapCut 9.x.
    Retorna (lista_materials_text, lista_trilhas).
    Legendas e Badges editoriais são separados em trilhas independentes para
    evitar colisão temporal (o CapCut rejeita dois segmentos sobrepostos na mesma trilha).
    Cenas sem texto, com texto vazio ou com legenda desativada (legenda_ativa=False) são ignoradas.
    """
    materials_texts = []
    segmentos_legendas = []
    segmentos_badges = []
    # TAREFA 2(a) — intervalos dos badges p/ detectar SOBREPOSIÇÃO entre badges de
    # cenas vizinhas (checado após o loop, quando todos já são conhecidos).
    _badges_intervalos = []

    for cena in cenas:
        # Verifica se a legenda está ativa nesta cena (suporta legenda_ativa e caption_ativo)
        legenda_ativa = cena.get("legenda_ativa")
        if legenda_ativa is None:
            legenda_ativa = cena.get("caption_ativo", True)
        if not legenda_ativa:
            continue

        texto = (cena.get("texto_transcricao") or cena.get("texto") or cena.get("narration") or "").strip()
        if not texto:
            continue

        t_ini = float(cena.get("_ts_start") if cena.get("_ts_start") is not None else cena.get("start", cena.get("tempo_inicio", 0)))
        dur = float(cena.get("duracao", 0))
        t_ini_us = int(round(t_ini * 1_000_000))
        dur_us   = int(round(dur * 1_000_000))

        mat_id = _novo_id()
        estilo_alvo = cena.get("estilo_legenda") or cena.get("caption_style") or style_key or "amarelo_capcut"
        # TAREFA 4: overrides por cena (caption_custom) — tamanho/fonte/cor vão no
        # material de texto; posicao vira o transform do segmento (abaixo).
        custom = cena.get("caption_custom") if isinstance(cena.get("caption_custom"), dict) else None
        mat_text = capcut_library.resolver_material_legenda(estilo_alvo, texto, mat_id, overrides=custom)
        materials_texts.append(mat_text)

        # TAREFA 4: posicao dinâmica. Sem caption_custom => (0.0, -0.75), idêntico ao
        # valor que era hardcoded ({"x":0.0,"y":-0.75}) — comportamento preservado.
        tr_x, tr_y = _transform_posicao_legenda(custom.get("position") if custom else None)

        segmentos_legendas.append({
            "id": _novo_id(),
            "material_id": mat_id,
            "source_timerange": {"start": 0, "duration": dur_us},
            "target_timerange": {"start": t_ini_us, "duration": dur_us},
            "render_index": 12000,
            "clip": {
                "scale": {"x": 1.0, "y": 1.0},
                "transform": {"x": tr_x, "y": tr_y},
                "rotation": 0.0,
                "flip": {"horizontal": False, "vertical": False},
                "alpha": 1.0,
            },
            "extra_material_refs": [],
            "keyframe_refs": [],
            "common_keyframes": [],
            "visible": True,
        })

        # ------------------------------------------------------------------
        # TAREFA 2 — BADGE EDITORIAL ("Passo N" / "Erro N") como overlay PRÓPRIO
        # ------------------------------------------------------------------
        # Carimbado por scene_plan_service._detectar_editorial_style() (tier 3).
        # Nesta etapa SOMENTE o badge é emitido: editorial_style["title"] segue
        # None (o título é escopo futuro — não derivamos texto de título agora).
        # Garantias:
        #   * 1 material + 1 segmento ADICIONAIS (a legenda padrão acima fica);
        #   * Trilha separada (Track 3) => sem colisão com a legenda (Track 2);
        #   * render_index 12010 > 12000 => fica acima na composição;
        #   * preset PRÓPRIO (_PRESET_BADGE_EDITORIAL), não o estilo da cena;
        #   * posição = banda OPOSTA à da legenda (lookup explícito, sem y fixo);
        #   * TAREFA 2 — TIMING ANCORADO NA PALAVRA: quando a cena traz
        #     editorial_style["anchor"] (word_timestamps), o badge começa no
        #     instante EXATO em que a frase é falada e dura
        #     editorial_style["duration_s"] (default 3s), limitado ao fim da cena.
        #     Sem `anchor` (cenas carimbadas antes desta mudança) => comportamento
        #     anterior: badge cobrindo a cena inteira. Zero regressão.
        estilo_editorial = cena.get("editorial_style")
        if not estilo_editorial and cena.get("is_story_card"):
            cfg = cena.get("story_card_config") or {}
            estilo_editorial = {
                "tier": 3,
                "variant": cena.get("story_card_style", "step_badge"),
                "badge": str(cfg.get("title") or cena.get("story_card_type") or "STORY CARD").upper(),
                "duration_s": float(cfg.get("duration_ms", 3000)) / 1000.0,
                "animacao": "fade",
                "badge_preset": cfg.get("badge_preset", _PRESET_BADGE_EDITORIAL),
                "anchor": cena.get("story_card_anchor"),
            }
        _badge_txt = ""
        if isinstance(estilo_editorial, dict):
            _badge_txt = str(estilo_editorial.get("badge") or "").strip()

        if _badge_txt:
            mat_id_badge = _novo_id()
            pos_badge = _banda_oposta_legenda(custom.get("position") if custom else None)
            bx, by = _transform_posicao_legenda(pos_badge)

            # ---- TAREFA 2: timing do badge (âncora > cena inteira) ------------
            t_badge_us = t_ini_us          # legado: badge = cena inteira
            dur_badge_us = dur_us
            _ancora = None
            if isinstance(estilo_editorial, dict):
                _ancora = estilo_editorial.get("anchor")
            if isinstance(_ancora, dict) and _ancora.get("s") is not None:
                try:
                    s_anc = float(_ancora["s"])
                except (TypeError, ValueError):
                    s_anc = None
                if s_anc is not None:
                    if s_anc < t_ini - 0.001:
                        # Clamp (piso = início da cena) + aviso p/ revisão manual.
                        _log_badge_dessincronia(cena, t_ini, s_anc)
                        s_anc = t_ini
                    try:
                        dur_fixa = float(estilo_editorial.get("duration_s")
                                         or _BADGE_DURATION_DEFAULT_S)
                    except (TypeError, ValueError):
                        dur_fixa = _BADGE_DURATION_DEFAULT_S
                    # TAREFA 2 (v3) + TAREFA 1 (v4): a duração efetiva do badge fica
                    # sempre no intervalo [1.5s, 4.0s]:
                    #   * piso  -> garante legibilidade;
                    #   * teto  -> limita a invasão da cena seguinte (casos de
                    #              cenas curtas em roteiros futuros).
                    # Nenhum dos dois usa o fim da cena: não há truncamento por
                    # fim de cena (removido na v3).
                    dur_fixa = min(max(dur_fixa, _BADGE_DURATION_MIN_S),
                                   _BADGE_DURATION_MAX_S)
                    t_badge_us = int(round(s_anc * 1_000_000))
                    dur_badge_us = int(round(dur_fixa * 1_000_000))
                    _badges_intervalos.append({
                        "cena": cena.get("id"),
                        "badge": _badge_txt,
                        "ini_s": round(t_badge_us / 1e6, 3),
                        "fim_s": round((t_badge_us + dur_badge_us) / 1e6, 3),
                    })
            # -------------------------------------------------------------------

            preset_badge = (
                estilo_editorial.get("badge_preset")
                if isinstance(estilo_editorial, dict) and estilo_editorial.get("badge_preset")
                else _PRESET_BADGE_EDITORIAL
            )
            mat_badge = capcut_library.resolver_material_legenda(
                preset_badge, _badge_txt, mat_id_badge
            )
            materials_texts.append(mat_badge)

            # ---- TAREFA 3: ANIMAÇÃO DE ENTRADA (opt-in por cena) --------------
            # Gate explícito: SÓ quando a cena traz editorial_style["animacao"]
            # em ("fade", "slide"). Sem o campo => [] (comportamento atual,
            # zero regressão nos badges já em produção).
            # y_final = transform.y JÁ calculado do badge (o keyframe usa valores
            # ABSOLUTOS e sobrescreve o estático, então o último ponto fecha em by).
            _anim_badge = ""
            if isinstance(estilo_editorial, dict):
                _anim_badge = str(estilo_editorial.get("animacao") or "").strip().lower()
            _kfs_badge = (_gerar_keyframes_entrada_texto(
                dur_badge_us, slide=(_anim_badge == "slide"), y_final=by)
                if _anim_badge in ("fade", "slide") else [])

            segmentos_badges.append({
                "id": _novo_id(),
                "material_id": mat_id_badge,
                "source_timerange": {"start": 0, "duration": dur_badge_us},
                "target_timerange": {"start": t_badge_us, "duration": dur_badge_us},
                "render_index": RENDER_INDEX_BADGE_EDITORIAL,
                "clip": {
                    "scale": {"x": 1.0, "y": 1.0},
                    "transform": {"x": bx, "y": by},
                    "rotation": 0.0,
                    "flip": {"horizontal": False, "vertical": False},
                    "alpha": 1.0,
                },
                "extra_material_refs": [],
                "keyframe_refs": [],
                "common_keyframes": _kfs_badge,
                "visible": True,
            })

    # TAREFA 2(a) — SOBREPOSIÇÃO entre badges de cenas vizinhas: com o piso mínimo
    # (e sem teto no fim da cena) o badge de uma cena pode invadir o da PRÓXIMA.
    # Avisa no log e ajusta a duração para o início do próximo, garantindo
    # ZERO colisão temporal na trilha de badges (o CapCut rejeita sobreposição no mesmo track).
    _ordenados = sorted(_badges_intervalos, key=lambda b: b["ini_s"])
    for _i in range(len(_ordenados) - 1):
        _a, _b = _ordenados[_i], _ordenados[_i + 1]
        if _a["cena"] != _b["cena"] and _b["ini_s"] < _a["fim_s"]:
            _log_badge_sobreposicao(_a, _b)

    segmentos_badges = sorted(segmentos_badges, key=lambda s: s["target_timerange"]["start"])
    for _i in range(len(segmentos_badges) - 1):
        _seg_atual = segmentos_badges[_i]
        _seg_prox = segmentos_badges[_i + 1]
        _fim_atual = _seg_atual["target_timerange"]["start"] + _seg_atual["target_timerange"]["duration"]
        _ini_prox = _seg_prox["target_timerange"]["start"]
        if _fim_atual > _ini_prox:
            _nova_dur = max(500_000, _ini_prox - _seg_atual["target_timerange"]["start"])
            _seg_atual["target_timerange"]["duration"] = _nova_dur
            _seg_atual["source_timerange"]["duration"] = _nova_dur

    trilhas = []
    if segmentos_legendas:
        trilhas.append({
            "attribute": 0,
            "flag": 0,
            "id": _novo_id(),
            "is_default_name": True,
            "name": "",
            "segments": segmentos_legendas,
            "type": "text",
        })
    if segmentos_badges:
        trilhas.append({
            "attribute": 0,
            "flag": 0,
            "id": _novo_id(),
            "is_default_name": True,
            "name": "",
            "segments": segmentos_badges,
            "type": "text",
        })

    if not trilhas:
        return [], []

    return materials_texts, trilhas


def _gerar_trilha_sfx(cenas, style_key="default"):
    """
    Gera trilhas de SFX para o rascunho CapCut.
    Itera por todas as cenas e aplica SFX configurados.
    """
    trilhas_sfx = []

    for cena in cenas:
        if not cena.get("sfx_lista"):
            continue

        t_base = float(cena.get("tempo_inicio") if cena.get("tempo_inicio") is not None else (cena.get("start") or cena.get("_ts_start") or 0))
        for sfx in cena.get("sfx_lista", []):
            dur_us = int(float(sfx.get("duracao_ms", 800)) * 1000)
            tc_ms = float(sfx.get("timecode_ms", 0))
            if tc_ms >= t_base * 1000.0 and t_base > 0.05:
                start_us = int(tc_ms * 1000)
            else:
                start_us = int((t_base + tc_ms / 1000.0) * 1_000_000)
            vol = float(sfx.get("volume", 0.7))
            sfx_id = f"sfx_{cena.get('id')}_{sfx.get('trigger', 'sfx')}"

            seg = {
                "id": _novo_id(),
                "material_id": sfx_id,
                "source_timerange": {
                    "start": 0,
                    "duration": dur_us,
                },
                "target_timerange": {
                    "start": start_us,
                    "duration": dur_us,
                },
                "volume": vol,
            }
            trilha = {
                "type": "audio",
                "id": sfx_id,
                "name": sfx.get("arquivo_sfx", "SFX"),
                "attribute": 0,
                "flag": 0,
                "is_default_name": True,
                "segments": [seg],
                "source_timerange": {
                    "start": 0,
                    "duration": dur_us,  # converter pra microsegundos
                },
                "target_timerange": {
                    "start": start_us,
                    "duration": dur_us,
                },
                "volume": vol,
            }
            trilhas_sfx.append(trilha)

    return trilhas_sfx


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

    # ── GUARD ASSERTION (SEGURANÇA): Protege pastas de projeto do Lira Studio ──
    from config import PROJETOS_DIR
    draft_dir_res = draft_dir.resolve()
    projetos_dir_res = PROJETOS_DIR.resolve()

    # (a) e (b) Impede que draft_dir seja a pasta de projetos ou esteja contido nela
    if draft_dir_res == projetos_dir_res or projetos_dir_res in draft_dir_res.parents:
        msg_erro = f"SEGURANÇA: draft_dir ({draft_dir_res}) não pode ser a pasta de um projeto do Lira Studio ({projetos_dir_res})."
        log_event("CAPCUT_DRAFT", msg_erro, level="error")
        raise RuntimeError(msg_erro)

    # (c) Checagem de arquivos-canário: se contiver artefatos vitais do Lira, aborta limpeza
    if draft_dir.exists():
        canarios = ["lira_scene_plan.json", "cenas.json", "meta.json", "word_timestamps.json"]
        canarios_encontrados = [c for c in canarios if (draft_dir / c).exists()]
        if canarios_encontrados:
            msg_erro = (
                f"SEGURANÇA: draft_dir ({draft_dir_res}) contém arquivos vitais de projeto do Lira Studio "
                f"({', '.join(canarios_encontrados)}). Operação abortada para evitar perda de dados!"
            )
            log_event("CAPCUT_DRAFT", msg_erro, level="error")
            raise RuntimeError(msg_erro)

    if draft_dir.exists():
        for item in draft_dir.iterdir():
            if item.is_dir():
                try:
                    shutil.rmtree(item)
                except Exception as e_dir:
                    log_event("CAPCUT_DRAFT", f"Falha ao remover pasta {item} no draft: {e_dir}", level="warn")
            else:
                try:
                    item.unlink()
                except OSError as e_file:
                    log_event("CAPCUT_DRAFT", f"Falha ao remover arquivo {item} no draft: {e_file}", level="warn")
    draft_dir.mkdir(parents=True, exist_ok=True)
    # Marca a pasta como draft GERADO pelo ULTRACUT3: nunca pode ser usada como
    # "draft nativo" ao detectar o 'platform' (evitava ler de volta o nosso
    # hard_disk_id errado — ver capcut_draft._plataforma_de_draft_nativo).
    cc.marcar_draft_gerado(draft_dir, nome_sanitizado)

    try:
        ref = _ref()
        LARGURA, ALTURA = 1920, 1080
        FPS = 30.0
        # ── Ordena cenas cronologicamente e garante continuidade total (zero gaps) ──
        dur_audio_s = 0.0
        if arquivo_audio and Path(str(arquivo_audio)).is_file():
            dur_aud_us = _duracao_audio_us(str(arquivo_audio))
            if dur_aud_us:
                dur_audio_s = dur_aud_us / 1_000_000

        cenas = sorted(
            lista_cenas,
            key=lambda c: float(c.get("start", 0)) if c.get("start") is not None else 0,
        )
        n_cenas = len(cenas)
        prev_fim = 0.0
        for i, cena in enumerate(cenas):
            # Primeira cena SEMPRE começa em 0.00s (zera delay inicial de narração)
            if i == 0:
                start = 0.0
            else:
                start = prev_fim

            if i + 1 < n_cenas:
                # Cada cena preenche exatamente até o início da próxima cena
                prox_start = float(cenas[i + 1].get("start", 0) if cenas[i + 1].get("start") is not None else start)
                dur = max(0.5, prox_start - start)
            else:
                # Última cena: estende até o fim do áudio ou duração residual
                if dur_audio_s and dur_audio_s > start:
                    dur = max(0.5, dur_audio_s - start)
                else:
                    dur = max(0.5, float(cena.get("duracao", 3.0)))

            cena["_ts_start"] = start
            cena["_ts_fim"] = start + dur
            cena["duracao"] = dur
            prev_fim = start + dur
        duracao_total_v = prev_fim

        # ── Placeholder para cenas sem mídia ──
        preto = draft_dir / "__placeholder_640x360.jpg"
        _gerar_placeholder(preto)

        # ANTIGRAVITY: passa o ALVO (project_name = projeto_id) para que a resolução
        # recuse diretórios de OUTRO projeto em vez de devolvê-los silenciosamente.
        anc_proj = _localizar_projeto_dir(arquivo_audio, lista_cenas,
                                          nome_projeto=project_name)
        ts_agora = cc.agora_us()
        meta_materiais_map = {}

        # ── Áudio (copia + duração real) ──
        tem_audio = bool(arquivo_audio and Path(arquivo_audio).is_file() and Path(arquivo_audio).stat().st_size > 0)
        audio_dst = None
        audio_canonico = None
        audio_dur_us = 0
        pertence_ao_anc = False
        _anc = None
        if tem_audio:
            audio_src = Path(arquivo_audio)
            # ANTIGRAVITY: áudio master canônico em projetos/<id>/audio/audio_original.mp3
            # (minúsculo) — copiado quando o arquivo de origem é legado (raiz .MP3 etc.).
            try:
                _anc = anc_proj or _projeto_dir_de_audio(str(audio_src))
                # GUARDA (bug 2): jamais canonicalizar/GRAVAR áudio dentro de um
                # projeto que não seja o ALVO desta exportação. Sem esta guarda,
                # passar o áudio de outro projeto sobrescrevia o master daquele
                # projeto (caso real: projetos/<GYPSUM>/audio/audio_original.mp3
                # foi destruído por um draft de teste de 14s).
                if _anc is not None and not _anc_eh_do_projeto_alvo(
                        _anc, project_name, nome_sanitizado):
                    _log_audio_projeto_estranho(_anc, project_name, str(audio_src))
                    _anc = None

                if _anc is not None:
                    try:
                        audio_src.resolve().relative_to(_anc.resolve())
                        pertence_ao_anc = True
                    except (ValueError, Exception):
                        pertence_ao_anc = False

                if _anc is not None and pertence_ao_anc:
                    _aud_dir = _anc / "audio"
                    _aud_dir.mkdir(parents=True, exist_ok=True)
                    _canon = _aud_dir / "audio_original.mp3"
                    # Só copia para _canon se não existir ainda (preserva o master existente)
                    if not _canon.exists() and audio_src.resolve() != _canon.resolve():
                        shutil.copy2(str(audio_src), str(_canon))
                    audio_canonico = _canon if _canon.is_file() else audio_src
                else:
                    # Sem projeto-alvo resolvido ou áudio externo: usa a origem como está
                    audio_canonico = audio_src
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
            elif ext in (".mp4", ".mov", ".mkv", ".webm", ".avi"):
                media_type = "video"

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

            # ── Transição de saída — biblioteca nativa do CapCut (ex: "Bordas difusas", "Sobrepor") ──
            # Aplica entre cenas adjacentes; nunca ultrapassa a duração da cena.
            # Não aplica na ÚLTIMA cena (i == len(cenas)) — não existe clipe subsequente.
            if i != len(cenas):
                try:
                    _tr_saida = (cena or {}).get("transicao_saida") or {}
                    _tr_tipo = str(_tr_saida.get("tipo") or "none")
                    if _tr_tipo and _tr_tipo.lower() != "none":
                        _tr_dur_ms = max(100, min(2000, int(_tr_saida.get("duracao_ms") or 500)))
                        mat_tr = capcut_library.resolver_material_transicao(_tr_tipo, _tr_dur_ms)
                        if mat_tr:
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
                seg["common_keyframes"] = []
            else:
                dur_us = seg["target_timerange"]["duration"]
                # Efeito de movimento cinematográfico suave para fotos estáticas (Ken Burns)
                mp = str(cena.get("motion_preset") or "").strip().lower()
                if not mp:
                    mp = "zoom_in_slow"   # fallback mínimo só se Brain não enviou nada
                seg["common_keyframes"] = _gerar_keyframes_zoom(dur_us, True, mp)

            render_index += 1
            seg["render_index"] = render_index
            segs_video.append(seg)

        duracao_total_us = _us(duracao_total_v)
        # ── Áudio ──
        segs_audio = []
        if tem_audio and audio_dst:
            if audio_canonico and audio_canonico.is_file() and _anc and pertence_ao_anc:
                audio_path_abs = str(audio_canonico).replace("\\", "/")
            else:
                audio_path_abs = str(audio_dst).replace("\\", "/")
            nome_arq_audio = Path(audio_path_abs).name
            maaudio = copy.deepcopy(ref["material_audio"])
            maaudio["id"] = _novo_id()
            # ANTIGRAVITY: path ABSOLUTO canônico (minúsculo) do áudio master — mesmo
            # estilo de path absoluto das imagens no draft; evita "Mídia perdida".
            maaudio["path"] = audio_path_abs
            maaudio["name"] = nome_arq_audio
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

        # ── SFX (Efeitos Sonoros) ──
        # SUSPENSO: biblioteca de .mp3 não montada ainda.
        # Reativar: substituir [] por _gerar_trilha_sfx(cenas)
        # Gatilhos planejados: HOOK, Story Card, virada de ato
        trilhas_sfx = []
        if trilhas_sfx:
            tracks.extend(trilhas_sfx)
            for t_sfx in trilhas_sfx:
                try:
                    mat_sfx = copy.deepcopy(ref["material_audio"])
                    mat_sfx["id"] = t_sfx["id"]
                    mat_sfx["name"] = t_sfx.get("name", "SFX")
                    mat_sfx["duration"] = t_sfx.get("source_timerange", {}).get("duration", 800000)
                    mat_sfx["local_material_id"] = str(uuid.uuid4())
                    mat_sfx["music_id"] = str(uuid.uuid4())
                    mat_sfx["path"] = ""
                    mats["audios"].append(mat_sfx)
                except Exception:
                    pass
            log_event("CAPCUT", f"{len(trilhas_sfx)} trilha(s) de SFX adicionadas ao draft", level="info")

        draft["tracks"] = tracks

        # ── Legendas (Nativas CapCut 9.x) ─────────────────────────────
        caption_style = next(
            (c.get("estilo_legenda") or c.get("caption_style") for c in lista_cenas if (c.get("texto_transcricao") or c.get("texto") or c.get("narration"))),
            "amarelo_capcut"
        )
        if any((c.get("texto_transcricao") or c.get("texto") or c.get("narration")) for c in cenas):
            mats_text, trilhas_texto = _gerar_trilha_texto(cenas, caption_style)
            if trilhas_texto:
                draft["materials"]["texts"] = mats_text
                if isinstance(trilhas_texto, list):
                    draft["tracks"].extend(trilhas_texto)
                else:
                    draft["tracks"].append(trilhas_texto)

        sfx_max_dur_us = 0
        if trilhas_sfx:
            for t_sfx in trilhas_sfx:
                tgt = t_sfx.get("target_timerange", {})
                fim_s = tgt.get("start", 0) + tgt.get("duration", 0)
                if fim_s > sfx_max_dur_us:
                    sfx_max_dur_us = fim_s
        draft["duration"] = max(duracao_total_us, audio_dur_us, bgm_dur_us, sfx_max_dur_us)

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