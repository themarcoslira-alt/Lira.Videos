"""
capcut_library_service.py — Gerenciador e Catálogo da Biblioteca Nativa do CapCut Desktop.

Mapeia as transições e efeitos reais disponíveis na instalação local do CapCut Desktop,
permitindo que o Lira Studio 2.0 (Aba 5 - Montagem) e o exportador nativo de drafts
(capcut_draft_imagens.py) apliquem efeitos reais do CapCut de forma direta e consistente.
"""

import json
import os
import uuid
import time
from pathlib import Path
from typing import Dict, List, Optional, Any

from config import BASE_DIR
from services.event_logger import log_event

CAPCUT_USER_DATA_DIR = Path.home() / "AppData" / "Local" / "CapCut" / "User Data"
CAPCUT_EFFECT_CACHE_DIR = CAPCUT_USER_DATA_DIR / "Cache" / "effect"
CAPCUT_DRAFTS_DIR = CAPCUT_USER_DATA_DIR / "Projects" / "com.lveditor.draft"
CONFIG_LIBRARY_PATH = BASE_DIR / "config" / "capcut_library.json"

# Transição padrão caso nenhuma seja especificada
TRANSICAO_DEFAULT_NOME = "Bordas difusas"

# Aliases semânticos e compatibilidade com UI / nomes legados
TRANSICOES_ALIASES = {
    # Crossfade / Dissolve
    "dissolve": "combinar",
    "crossfade": "combinar",
    "fusao": "combinar",
    # Fades e cortes suaves
    "fade_out": "sobrepor",
    "fade_in": "barra_de_luz",
    "fade": "sobrepor",
    "dip_to_black": "sobrepor",
    "dip_to_white": "barra_de_luz",
    "flash": "barra_de_luz",
    "light_leak": "barra_de_luz",
    # Movimentos lentos
    "slow_in": "bordas_difusas",
    "slow_out": "bordas_difusas",
    # Efeitos de espelho e glitch
    "mirror": "espelho",
    "flip": "espelho",
    "glitch": "retalhos_do_caos",
}

# Aliases de estilos de legenda — compatibilidade com projetos legados que
# salvaram em disco os nomes de estilo da v1 (modern/classic/popup) em vez dos
# ids do catálogo oficial (config/capcut_subtitles.json).
# Mesmo padrão de TRANSICOES_ALIASES; consumido por resolver_material_legenda().
LEGENDAS_ALIASES = {
    "classic": "amarelo_capcut",
    "modern": "tiktok_dinamico",
    "popup": "borda_preta_pop",
}


def _trans_request_id() -> str:
    """Gera um request_id idêntico ao padrão interno do CapCut."""
    try:
        from datetime import datetime
        return "%s%016X" % (
            datetime.now().strftime("%Y%m%d%H%M%S"),
            int(time.time() * 1000) % 0xFFFFFFFFFFFFFFFF,
        )
    except Exception:
        return str(uuid.uuid4()).replace("-", "")[:30]


class CapCutLibraryService:
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or CONFIG_LIBRARY_PATH
        self._cache_transicoes: Optional[List[Dict[str, Any]]] = None

    def carregar_catalogo(self, forcar_reload: bool = False) -> List[Dict[str, Any]]:
        """Carrega e enriquece a lista de transições do catálogo."""
        if self._cache_transicoes is not None and not forcar_reload:
            return self._cache_transicoes

        itens = []
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                itens = data.get("transicoes", [])
            except Exception as e:
                log_event("CAPCUT_LIB", f"Erro ao ler {self.config_path}: {e}", level="warn")

        # Verifica disponibilidade dos arquivos em disco
        for item in itens:
            effect_id = str(item.get("effect_id", ""))
            sub_hash = str(item.get("sub_hash", ""))
            path_disco = CAPCUT_EFFECT_CACHE_DIR / effect_id / sub_hash
            item["path_real"] = str(path_disco).replace("\\", "/")
            item["disponivel_local"] = path_disco.exists() and (path_disco / "config.json").exists()

        self._cache_transicoes = itens
        return itens

    def obter_transicoes_disponiveis(self) -> List[Dict[str, Any]]:
        """Retorna as transições disponíveis para a interface web."""
        catalogo = self.carregar_catalogo()
        res = [
            {
                "id": "none",
                "name": "Nenhuma (Corte Seco)",
                "category": "Básico",
                "duration_ms": 0,
                "recommended": False,
                "disponivel": True,
            }
        ]
        for item in catalogo:
            res.append({
                "id": item.get("id"),
                "name": item.get("name"),
                "category": item.get("category_name", "Populares"),
                "duration_ms": item.get("duration_default_ms", 500),
                "recommended": bool(item.get("recommended", False)),
                "disponivel": bool(item.get("disponivel_local", True)),
            })
        return res

    def resolver_material_transicao(
        self,
        nome_ou_id: str,
        duracao_ms: int = 500
    ) -> Optional[Dict[str, Any]]:
        """
        Retorna o dicionário de material de transição pronto para ser injetado
        em materials.transitions[] no draft_content.json do CapCut.
        Retorna None se for corte seco ('none').
        """
        if not nome_ou_id or str(nome_ou_id).lower() in ("none", "nenhuma", "corte_seco"):
            return None

        catalogo = self.carregar_catalogo()
        match = None
        raw_target = str(nome_ou_id).strip().lower()
        target = TRANSICOES_ALIASES.get(raw_target, raw_target)

        # Busca por id ou nome (testa primeiro target mapeado, depois o raw)
        for item in catalogo:
            item_id = item.get("id", "").lower()
            item_name = item.get("name", "").lower()
            if item_id == target or item_name == target or item_id == raw_target or item_name == raw_target:
                match = item
                break

        # Fallback se não encontrar: tenta Bordas difusas ou Combinar
        if not match:
            for item in catalogo:
                if item.get("name") == TRANSICAO_DEFAULT_NOME:
                    match = item
                    break
        if not match and catalogo:
            match = catalogo[0]

        if not match:
            return None

        # Duração em microssegundos (us)
        dur_us = int(round((duracao_ms / 1000.0) * 1_000_000))

        material_id = str(uuid.uuid4()).upper()
        return {
            "id": material_id,
            "type": "transition",
            "name": match.get("name"),
            "effect_id": str(match.get("effect_id")),
            "resource_id": str(match.get("resource_id")),
            "third_resource_id": str(match.get("third_resource_id", "0")),
            "source_platform": 1,
            "path": match.get("path_real"),
            "duration": dur_us,
            "is_overlap": bool(match.get("is_overlap", True)),
            "platform": "all",
            "category_id": str(match.get("category_id", "25822")),
            "category_name": str(match.get("category_name", "Populares")),
            "request_id": _trans_request_id(),
            "is_ai_transition": False,
            "video_path": "",
            "task_id": "",
        }

    def escanear_e_atualizar_catalogo(self) -> Dict[str, Any]:
        """Varre o cache do CapCut e drafts em busca de novas transições."""
        novas = 0
        catalogo = self.carregar_catalogo(forcar_reload=True)
        existentes_ids = {str(item.get("effect_id")) for item in catalogo}

        # 1. Varre drafts nativos do usuário para descobrir nomes humanos reais das transições
        draft_names = {}
        if CAPCUT_DRAFTS_DIR.exists():
            for d in CAPCUT_DRAFTS_DIR.iterdir():
                dc = d / "draft_content.json"
                if dc.exists():
                    try:
                        d_json = json.loads(dc.read_text(encoding="utf-8", errors="ignore"))
                        for tr in d_json.get("materials", {}).get("transitions", []):
                            eid = str(tr.get("effect_id", "")).strip()
                            nm = str(tr.get("name", "")).strip()
                            if eid and nm and not nm.startswith("Transição"):
                                draft_names[eid] = nm
                    except Exception:
                        pass

        # 2. Varre o diretório de cache de efeitos do CapCut
        if CAPCUT_EFFECT_CACHE_DIR.exists():
            for folder in CAPCUT_EFFECT_CACHE_DIR.iterdir():
                if not folder.is_dir() or folder.name in existentes_ids:
                    continue
                effect_id = folder.name
                for sub in folder.iterdir():
                    if not sub.is_dir():
                        continue
                    extra_json = sub / "extra.json"
                    if extra_json.exists():
                        try:
                            data = json.loads(extra_json.read_text(encoding="utf-8", errors="ignore"))
                            if "transition" in data:
                                t_info = data["transition"]
                                nome_real = draft_names.get(effect_id)
                                if not nome_real:
                                    cfg_p = sub / "config.json"
                                    if cfg_p.exists():
                                        try:
                                            c_data = json.loads(cfg_p.read_text(encoding="utf-8", errors="ignore"))
                                            cnm = c_data.get("name", "")
                                            if cnm and not cnm.startswith("AmazingAuto") and not cnm.startswith("AlgorithmGraph"):
                                                nome_real = cnm.replace("GESticker_", "").replace("_", " ").title()
                                        except Exception:
                                            pass
                                if not nome_real:
                                    nome_real = f"Transição {effect_id[:6]}"

                                # Gera slug seguro para o id
                                import re
                                slug = re.sub(r"[^a-z0-9_]+", "_", nome_real.lower().strip()).strip("_") or f"trans_{effect_id[:8]}"
                                catalogo.append({
                                    "id": slug,
                                    "name": nome_real,
                                    "effect_id": effect_id,
                                    "resource_id": effect_id,
                                    "third_resource_id": "0",
                                    "category_name": "Outras",
                                    "category_id": "9999",
                                    "sub_hash": sub.name,
                                    "duration_default_ms": int(t_info.get("defaultDura", 0.5) * 1000),
                                    "is_overlap": bool(t_info.get("isOverlap", True)),
                                })
                                novas += 1
                                existentes_ids.add(effect_id)
                        except Exception:
                            pass

        if novas > 0:
            try:
                self.config_path.write_text(
                    json.dumps({"transicoes": catalogo}, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
                self._cache_transicoes = None
                log_event("CAPCUT_LIB", f"{novas} novas transições catalogadas do CapCut.", level="info")
            except Exception as e:
                log_event("CAPCUT_LIB", f"Erro ao salvar catálogo atualizado: {e}", level="warn")

        return {"sucesso": True, "novas_adicionadas": novas, "total": len(catalogo)}

    # ---------------------------------------------------------------------------
    # CATÁLOGO DE LEGENDAS (CAPCUT SUBTITLES)
    # ---------------------------------------------------------------------------
    def carregar_catalogo_legendas(self) -> List[Dict[str, Any]]:
        """Carrega os presets de estilos de legendas do CapCut."""
        subtitles_path = BASE_DIR / "config" / "capcut_subtitles.json"
        if subtitles_path.exists():
            try:
                data = json.loads(subtitles_path.read_text(encoding="utf-8"))
                return data.get("legendas", [])
            except Exception as e:
                log_event("CAPCUT_LIB", f"Erro ao ler capcut_subtitles.json: {e}", level="warn")
        return []

    def obter_presets_legendas(self) -> List[Dict[str, Any]]:
        """Retorna lista de presets de legendas disponíveis para a interface."""
        return self.carregar_catalogo_legendas()

    def resolver_material_legenda(
        self,
        preset_id_ou_nome: str,
        texto: str,
        material_id: Optional[str] = None,
        overrides: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Gera o objeto materials.texts para o draft_content.json no estilo escolhido.

        TAREFA 4: `overrides` (opcional) = bloco caption_custom da cena
        ({font_size, font_family, font_color, position}). Aplica-se POR CIMA do
        preset; `position` é consumida no clip.transform por _gerar_trilha_texto.
        """
        presets = self.carregar_catalogo_legendas()
        target = str(preset_id_ou_nome or "").strip().lower()

        match = None
        for p in presets:
            if (
                p.get("id", "").lower() == target
                or p.get("name", "").lower() == target
                or p.get("tag", "").lower() == target
            ):
                match = p
                break

        # Alias de estilo legado (ex.: "classic"/"modern"/"popup" salvos em disco
        # antes da migração para o catálogo oficial): resolve para o preset mapeado
        # ANTES do fallback genérico, para não descartar a intenção do usuário.
        if not match and target in LEGENDAS_ALIASES:
            alvo_alias = LEGENDAS_ALIASES[target]
            for p in presets:
                if p.get("id", "").lower() == alvo_alias:
                    match = p
                    break

        # Fallback para Amarelo CapCut ou o primeiro
        if not match:
            for p in presets:
                if p.get("id") == "amarelo_capcut" or p.get("recommended"):
                    match = p
                    break
        if not match and presets:
            match = presets[0]

        style = match or {
            "font_color": "#FFE135",
            "font_size": 16.0,
            "border_color": "#000000",
            "border_width": 3.0,
            "background_color": "",
            "background_alpha": 0.0,
            "shadow_color": "#000000",
            "shadow_alpha": 0.8,
            "shadow_blur": 2.0,
            "shadow_distance": 0.0,
            "shadow_angle": 0.0,
        }

        # TAREFA 4 — overrides por cena (por cima do preset).
        # font_size e font_color sao aplicados nos DOIS lugares (campo do material +
        # dentro do content HTML) para nao divergirem no CapCut.
        font_title = "System Font"
        font_path = ""
        font_resource_id = "3911606"
        efetivo_size = style.get("font_size", 16.0)
        efetivo_color = style.get("font_color", "#FFE135")

        if isinstance(overrides, dict):
            fam = str(overrides.get("font_family") or "").strip()
            if fam and fam != "System Font":
                # LIMITACAO CONHECIDA (best-effort): `font_resource_id` e um id
                # INTERNO do acervo do CapCut. Sem o TTF correspondente instalado/
                # registrado, nao ha como resolve-lo aqui — entao pedimos a familia
                # apenas pelo nome (font_title) e mantemos font_path vazio; o CapCut
                # cai na fonte padrao caso nao encontre.
                font_title = fam
            try:
                if overrides.get("font_size") not in (None, ""):
                    efetivo_size = float(overrides["font_size"])
            except (TypeError, ValueError):
                pass
            cor_ov = str(overrides.get("font_color") or "").strip()
            if cor_ov:
                efetivo_color = cor_ov

        mat_id = material_id or str(uuid.uuid4()).upper()
        return {
            "id": mat_id,
            "type": "text",
            "content": f'<font color="{efetivo_color}"><span>{texto}</span></font>',
            "font_title": font_title,
            "font_path": font_path,
            "font_resource_id": font_resource_id,
            "font_size": efetivo_size,
            "font_color": efetivo_color,
            "text_alpha": 1.0,
            "align_type": 1,
            "typesetting": 0,
            "border_color": style.get("border_color", "#000000"),
            "border_width": style.get("border_width", 3.0),
            "background_color": style.get("background_color", ""),
            "background_alpha": style.get("background_alpha", 0.0),
            "shadow_color": style.get("shadow_color", "#000000"),
            "shadow_alpha": style.get("shadow_alpha", 0.8),
            "shadow_blur": style.get("shadow_blur", 2.0),
            "shadow_distance": style.get("shadow_distance", 0.0),
            "shadow_angle": style.get("shadow_angle", 0.0),
            "source_platform": 0,
        }


# Instância singleton padrão
capcut_library = CapCutLibraryService()

