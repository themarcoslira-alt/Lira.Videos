"""
services/character_service.py — Character Intelligence Layer
=============================================================
Gerencia o ciclo de vida permanente dos personagens de um projeto:
- Criação e persistência em characters/<NomePersonagem>/
- character.json (fonte de verdade de identidade visual e bloqueio)
- metadata.json (metadados de versão, timestamps e status)
- reference.png (imagem oficial de referência)
"""

import os
import json
import time
import shutil
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any, List

from config import PROJETOS_DIR
from services.event_logger import log_event


def _get_project_dir(projeto_id: str) -> Path:
    return PROJETOS_DIR / projeto_id


def _get_characters_dir(projeto_id: str) -> Path:
    d = _get_project_dir(projeto_id) / "characters"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cadastrar_personagem(projeto_id: str, nome: str, imagem_bytes: bytes,
                         tipo: str = "human", visual_style: str = "photorealistic_cinematic",
                         detalhes_identidade: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Cadastra um personagem permanentemente no projeto.
    Cria a estrutura:
      characters/<nome>/reference.png
      characters/<nome>/character.json
      characters/<nome>/metadata.json
    """
    nome_sanitizado = "".join(c for c in nome if c.isalnum() or c in ("-", "_", " ")).strip()
    if not nome_sanitizado:
        nome_sanitizado = "PersonagemPrincipal"

    char_dir = _get_characters_dir(projeto_id) / nome_sanitizado
    char_dir.mkdir(parents=True, exist_ok=True)

    # 1. Salva a imagem de referência
    ref_path = char_dir / "reference.png"
    ref_path.write_bytes(imagem_bytes)

    # Calcula hash da imagem
    img_hash = hashlib.sha256(imagem_bytes).hexdigest()[:16]

    # 2. Constrói a fonte de verdade da identidade
    identidade = {
        "face": "consistent facial structure, high fidelity facial features",
        "hair": "consistent hair style and natural texture",
        "age": "adult",
        "clothing": "consistent realistic attire matching character profile",
        "visual_style": visual_style,
    }
    if detalhes_identidade:
        identidade.update(detalhes_identidade)

    character_data = {
        "name": nome_sanitizado,
        "type": tipo,
        "locked": True,
        "reference_image": f"characters/{nome_sanitizado}/reference.png",
        "reference_image_abs": str(ref_path),
        "identity": identidade,
    }

    metadata_data = {
        "project_id": projeto_id,
        "character_name": nome_sanitizado,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "image_hash": img_hash,
        "image_size_kb": round(len(imagem_bytes) / 1024, 1),
        "status": "IDENTIDADE_BLOQUEADA",
        "active": True,
    }

    # Salva character.json e metadata.json
    (char_dir / "character.json").write_text(json.dumps(character_data, indent=2, ensure_ascii=False), encoding="utf-8")
    (char_dir / "metadata.json").write_text(json.dumps(metadata_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Define como personagem ativo no meta.json do projeto
    _set_personagem_ativo_projeto(projeto_id, nome_sanitizado, character_data)

    log_event("CHARACTER_INTEL", f"Personagem '{nome_sanitizado}' cadastrado e bloqueado com sucesso para o projeto '{projeto_id}'")

    return {
        "success": True,
        "character": character_data,
        "metadata": metadata_data,
        "dir": str(char_dir),
    }


def obter_personagem_ativo(projeto_id: str) -> Optional[Dict[str, Any]]:
    """Retorna os dados completos do personagem ativo no projeto."""
    char_root = _get_characters_dir(projeto_id)
    if not char_root.exists():
        return None

    # Procura a pasta do personagem ativo
    for folder in sorted(char_root.iterdir()):
        if folder.is_dir():
            char_json = folder / "character.json"
            meta_json = folder / "metadata.json"
            if char_json.exists():
                try:
                    cdata = json.loads(char_json.read_text(encoding="utf-8"))
                    mdata = json.loads(meta_json.read_text(encoding="utf-8")) if meta_json.exists() else {}
                    if mdata.get("active", True):
                        ref_img = folder / "reference.png"
                        cdata["has_image"] = ref_img.exists()
                        cdata["metadata"] = mdata
                        return cdata
                except Exception:
                    pass
    return None


def listar_personagens(projeto_id: str) -> List[Dict[str, Any]]:
    """Lista todos os personagens cadastrados no projeto."""
    char_root = _get_characters_dir(projeto_id)
    lista = []
    if not char_root.exists():
        return lista

    for folder in sorted(char_root.iterdir()):
        if folder.is_dir():
            char_json = folder / "character.json"
            meta_json = folder / "metadata.json"
            if char_json.exists():
                try:
                    cdata = json.loads(char_json.read_text(encoding="utf-8"))
                    mdata = json.loads(meta_json.read_text(encoding="utf-8")) if meta_json.exists() else {}
                    cdata["metadata"] = mdata
                    cdata["has_image"] = (folder / "reference.png").exists()
                    lista.append(cdata)
                except Exception:
                    pass
    return lista


def remover_personagem(projeto_id: str, nome: str) -> bool:
    """Remove o cadastro de um personagem do projeto."""
    char_dir = _get_characters_dir(projeto_id) / nome
    if char_dir.exists() and char_dir.is_dir():
        shutil.rmtree(char_dir)
        # Limpa referência no meta.json
        meta_file = _get_project_dir(projeto_id) / "meta.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                if meta.get("nome_personagem") == nome:
                    meta["nome_personagem"] = ""
                    meta["personagem_ref_global"] = None
                    meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
        log_event("CHARACTER_INTEL", f"Personagem '{nome}' removido do projeto '{projeto_id}'")
        return True
    return False


def _set_personagem_ativo_projeto(projeto_id: str, nome: str, character_data: Dict[str, Any]):
    """Atualiza o meta.json do projeto com a referência ativa."""
    meta_file = _get_project_dir(projeto_id) / "meta.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            meta["nome_personagem"] = nome
            meta["personagem_ref_global"] = character_data.get("reference_image_abs")
            meta["personagem_locked"] = True
            meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
