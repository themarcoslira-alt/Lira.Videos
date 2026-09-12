"""
services/bgm_service.py — Background Music (BGM) & Audio Ducking Engine
======================================================================
Responsabilidades:
1. Analisar o roteiro e nicho do projeto para recomendar o perfil musical ideal (BPM, humor, instrumentos).
2. Gerenciar trilhas na pasta Biblioteca/Musicas/ ou diretório do projeto.
3. Mixar narração e trilha sonora com Ducking Inteligente via FFmpeg (música reduz automaticamente na fala).
"""

import os
import json
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional

from config import PROJETOS_DIR, BASE_DIR, FFMPEG_PATH
from services.event_logger import log_event

BIBLIOTECA_MUSICAS_DIR = Path(BASE_DIR) / "Biblioteca" / "Musicas"



NICHOS_MUSICAIS = {
    "jardinagem_natureza": {
        "label": "Jardinagem, Botânica & Natureza",
        "keywords": ["planta", "rosa", "orquídea", "orquidea", "raiz", "adubo", "banana", "solo", "jardim", "folha", "cultivo", "vaso", "garden", "plant", "nature"],
        "estilo": "Folk Acústico / Orgânico / Piano Calmo",
        "humor": "Calmo, acolhedor, inspirador e didático",
        "bpm_ideal": "85 - 105 BPM",
        "instrumentos": ["Violão de nylon e aço", "Piano suave", "Pads ambientais", "Percussão leve e natural"],
        "tags_busca": ["nature acoustic", "gardening background", "gentle acoustic guitar", "peaceful piano", "organic folk", "warm daylight"],
        "volume_sugerido": 0.14
    },
    "tecnologia_negocios": {
        "label": "Tecnologia, Finanças & Negócios",
        "keywords": ["dinheiro", "finanças", "investimento", "software", "ia", "tecnologia", "canal", "vendas", "mercado", "dólar", "bitcoin", "crypto"],
        "estilo": "Minimalista Corporativo / Ambient Lo-Fi",
        "humor": "Focado, moderno, inteligente e dinâmico",
        "bpm_ideal": "100 - 120 BPM",
        "instrumentos": ["Sintetizadores suaves", "Bateria eletrônica minimalista", "Baixo acústico/synth", "Pianos modernos"],
        "tags_busca": ["corporate ambient", "minimal technology", "smart lo-fi", "business background", "modern electronic beat"],
        "volume_sugerido": 0.12
    },
    "curiosidades_misterio": {
        "label": "Curiosidades, Documentário & Mistério",
        "keywords": ["segredo", "mistério", "misterio", "incrível", "descobriu", "ciência", "história", "verdade", "chocante", "fatos", "universo"],
        "estilo": "Cinematic Ambient / Tensão Leve / Investigativo",
        "humor": "Curioso, intrigante, reflexivo e envolvente",
        "bpm_ideal": "75 - 95 BPM",
        "instrumentos": ["Cordas orquestrais com reverb", "Pianos cinematográficos", "Drones atmosféricos", "Pulsos rítmicos"],
        "tags_busca": ["cinematic documentary", "curiosity ambient", "investigative piano", "mystery background", "atmospheric suspense"],
        "volume_sugerido": 0.13
    },
    "educativo_geral": {
        "label": "Educativo, Lifestyle & Geral",
        "keywords": [],
        "estilo": "Acústico Suave / Inspiração Quotidiana",
        "humor": "Amigável, positivo e agradável",
        "bpm_ideal": "90 - 110 BPM",
        "instrumentos": ["Violão acústico", "Piano clássico", "Cordas leves"],
        "tags_busca": ["inspiring acoustic", "friendly background", "gentle positive guitar", "upbeat light"],
        "volume_sugerido": 0.14
    }
}


def analisar_perfil_musical_projeto(projeto_id: str) -> Dict[str, Any]:
    """
    Analisa os textos do projeto (transcrição, cenas, tags) e retorna o perfil musical recomendado.
    """
    pdir = PROJETOS_DIR / projeto_id
    texto_corpus = ""

    # Lê roteiro transcrição
    transc_file = pdir / "roteiro_transcricao.txt"
    if transc_file.exists():
        texto_corpus += " " + transc_file.read_text(encoding="utf-8", errors="ignore")

    # Lê prompt/cenas
    plan_file = pdir / "lira_scene_plan.json"
    if plan_file.exists():
        try:
            plan = json.loads(plan_file.read_text(encoding="utf-8"))
            for c in plan.get("cenas", []):
                texto_corpus += f" {c.get('narration','')} {c.get('texto','')} {c.get('visual_prompt','')}"
        except Exception:
            pass

    texto_lower = texto_corpus.lower()

    # Identifica o melhor nicho por frequência de keywords
    melhor_nicho = "educativo_geral"
    maior_score = 0

    for nid, ndata in NICHOS_MUSICAIS.items():
        kws = ndata.get("keywords", [])
        if not kws:
            continue
        score = sum(texto_lower.count(k) for k in kws)
        if score > maior_score:
            maior_score = score
            melhor_nicho = nid

    perfil = dict(NICHOS_MUSICAIS[melhor_nicho])
    perfil["nicho_id"] = melhor_nicho
    perfil["nicho"] = perfil.get("label", melhor_nicho)
    perfil["nicho_nome"] = perfil.get("label", melhor_nicho)
    perfil["bpm_sugerido"] = perfil.get("bpm_ideal", "90-110 BPM")
    perfil["tags"] = perfil.get("tags_busca", [])
    perfil["score_correspondencia"] = maior_score

    # Verifica se o projeto já tem trilha selecionada
    meta_file = pdir / "meta.json"
    trilha_atual = None
    if meta_file.exists():
        try:
            m = json.loads(meta_file.read_text(encoding="utf-8"))
            trilha_atual = m.get("trilha_sonora")
        except Exception:
            pass

    perfil["trilha_atual"] = trilha_atual
    perfil["config_atual"] = trilha_atual
    perfil["trilhas_disponiveis"] = listar_trilhas_disponiveis(melhor_nicho)

    log_event("BGM", f"Projeto '{projeto_id}': nicho musical detectado '{perfil['label']}' (score={maior_score})", level="info")
    return perfil


def listar_trilhas_disponiveis(nicho: Optional[str] = None) -> List[Dict[str, str]]:
    """Varre a pasta Biblioteca/Musicas/ e retorna as trilhas de áudio disponíveis."""
    BIBLIOTECA_MUSICAS_DIR.mkdir(parents=True, exist_ok=True)
    extensoes = (".mp3", ".wav", ".m4a", ".aac", ".ogg")
    trilhas = []

    for f in BIBLIOTECA_MUSICAS_DIR.rglob("*"):
        if f.is_file() and f.suffix.lower() in extensoes:
            rel = f.relative_to(BIBLIOTECA_MUSICAS_DIR)
            trilhas.append({
                "nome": f.stem.replace("_", " "),
                "arquivo": str(f.resolve()),
                "caminho": str(f.resolve()),
                "relativo": str(rel),
                "tamanho_kb": round(f.stat().st_size / 1024, 1),
                "tamanho_mb": round(f.stat().st_size / (1024 * 1024), 2)
            })

    return trilhas


def vincular_trilha_projeto(
    projeto_id: str,
    caminho_musica: str,
    volume: float = 0.14,
    ducking: bool = True
) -> Dict[str, Any]:
    """Salva a configuração de trilha sonora no meta.json do projeto."""
    pdir = PROJETOS_DIR / projeto_id
    meta_file = pdir / "meta.json"
    if not meta_file.exists():
        return {"success": False, "error": "meta.json não encontrado"}

    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        dados_trilha = {
            "arquivo": caminho_musica,
            "nome": Path(caminho_musica).name if caminho_musica else "",
            "volume": float(volume),
            "ducking": bool(ducking),
            "ativo": bool(caminho_musica and Path(caminho_musica).exists())
        }
        meta["trilha_sonora"] = dados_trilha
        meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        log_event("BGM", f"Trilha sonora atualizada para '{projeto_id}': {dados_trilha['nome']} (vol={volume})", level="info")
        return {"success": True, "trilha_sonora": dados_trilha}
    except Exception as e:
        log_event("BGM", f"Erro ao salvar trilha no meta.json: {e}", level="error")
        return {"success": False, "error": str(e)}


def mixar_voz_e_musica_ffmpeg(
    audio_voz_path: str,
    audio_musica_path: str,
    saida_audio_path: str,
    volume_musica: float = 0.14,
    ducking: bool = True
) -> bool:
    """
    Combina a voz principal com a música de fundo usando FFmpeg com Ducking Inteligente.
    A música entra em loop contínuo e tem o volume atenuado durante a fala.
    """
    voz = Path(audio_voz_path)
    musica = Path(audio_musica_path)
    saida = Path(saida_audio_path)
    saida.parent.mkdir(parents=True, exist_ok=True)

    if not voz.exists():
        log_event("BGM_MIX", f"Áudio de voz não encontrado: {audio_voz_path}", level="error")
        return False

    if not musica.exists():
        log_event("BGM_MIX", f"Música não encontrada: {audio_musica_path}", level="warn")
        return False

    vol = max(0.02, min(0.50, float(volume_musica)))

    if ducking:
        # Ducking inteligente via sidechaincompress:
        # A música toca a um volume base (vol * 1.5) nos silêncios e abaixa quando a voz entra
        vol_base = round(min(0.40, vol * 1.6), 3)
        filtro_audio = (
            f"[1:a]aloop=loop=-1:size=2e+09,volume={vol_base}[bgm];"
            f"[bgm][0:a]sidechaincompress=threshold=0.08:ratio=5:attack=40:release=350[ducked];"
            f"[0:a][ducked]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
    else:
        # Mixagem direta com volume constante da música
        filtro_audio = (
            f"[1:a]aloop=loop=-1:size=2e+09,volume={vol:.3f}[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )

    cmd = [
        FFMPEG_PATH, "-y",
        "-i", str(voz.resolve()),
        "-i", str(musica.resolve()),
        "-filter_complex", filtro_audio,
        "-map", "[aout]",
        "-c:a", "aac",
        "-b:a", "192k",
        str(saida.resolve())
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if res.returncode == 0 and saida.exists() and saida.stat().st_size > 1000:
            log_event("BGM_MIX", f"Mixagem concluída com sucesso: {saida.name} ({saida.stat().st_size // 1024} KB)", level="info")
            return True
        else:
            log_event("BGM_MIX", f"Erro no FFmpeg ao mixar áudio: {res.stderr[-300:]}", level="error")
            return False
    except Exception as e:
        log_event("BGM_MIX", f"Exceção ao mixar áudio com música: {e}", level="error")
        return False

