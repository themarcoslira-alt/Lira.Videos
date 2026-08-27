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
import re
import json
import time
import uuid
import shutil
import hashlib
import unicodedata
from pathlib import Path
from typing import Optional, Dict, Any, List

from config import PROJETOS_DIR
from services.event_logger import log_event


IDENTIDADE_FILE = "identidade.json"


def _get_project_dir(projeto_id: str) -> Path:
    return PROJETOS_DIR / projeto_id


def _get_characters_dir(projeto_id: str) -> Path:
    d = _get_project_dir(projeto_id) / "characters"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _identidade_path(projeto_id: str) -> Path:
    return _get_project_dir(projeto_id) / IDENTIDADE_FILE


def salvar_identidade_projeto(
    projeto_id: str,
    tipo: str,
    nome: str,
    referencia_flow: str = "",
    imagem_bytes: Optional[bytes] = None,
    arquivo_origem: Optional[str] = None,
    arquivo_flow: str = "",
    visual_style: str = "photorealistic_cinematic"
) -> Dict[str, Any]:
    """
    Salva a configuração de identidade permanente do projeto de forma 100% dinâmica e isolada:
    - Copia imagens externas para dentro do projeto (characters/<Nome>/reference.png).
    - Preserva o nome do recurso Flow em arquivo_flow (ex: Marcos.jpeg, João.png).
    - Salva em identidade.json com status: 'vinculado' e lista 'personagens'.
    """
    nome_sanitizado = "".join(c for c in (nome or "") if c.isalnum() or c in ("-", "_", " ")).strip()
    if not nome_sanitizado:
        nome_sanitizado = "Personagem" if tipo == "personagem" else "Avatar Google Flow"

    # Determina arquivo_flow e referência nativa do Flow
    arq_flow = (arquivo_flow or "").strip()
    if not arq_flow and arquivo_origem:
        arq_flow = Path(arquivo_origem).name
    elif not arq_flow and tipo == "personagem" and imagem_bytes:
        arq_flow = f"{nome_sanitizado}.png"
    elif not arq_flow and tipo == "avatar":
        arq_flow = "me"

    ref_flow = (referencia_flow or "").strip()
    if tipo == "avatar":
        if not ref_flow:
            ref_flow = "@me"
        elif not ref_flow.startswith("@"):
            ref_flow = f"@{ref_flow}"
    else:
        if not ref_flow:
            ref_flow = f"@{arq_flow}" if arq_flow else f"@{nome_sanitizado}"
        elif not ref_flow.startswith("@"):
            ref_flow = f"@{ref_flow}"

    pdir = _get_project_dir(projeto_id)
    pdir.mkdir(parents=True, exist_ok=True)

    rel_img = None
    abs_img = None

    # Isolamento de Imagem: Copia para dentro do projeto
    if tipo == "personagem":
        char_dir = _get_characters_dir(projeto_id) / nome_sanitizado
        char_dir.mkdir(parents=True, exist_ok=True)
        ref_path = char_dir / "reference.png"

        if arquivo_origem and Path(arquivo_origem).exists():
            if Path(arquivo_origem).resolve() != ref_path.resolve():
                shutil.copy2(arquivo_origem, ref_path)
            rel_img = f"characters/{nome_sanitizado}/reference.png"
            abs_img = str(ref_path)
            print("[OK] Imagem copiada para projeto", flush=True)
        elif imagem_bytes:
            ref_path.write_bytes(imagem_bytes)
        if not abs_img:
            char_dir = _get_characters_dir(projeto_id) / nome_sanitizado
            ref_path = char_dir / "reference.png"
            if ref_path.exists():
                rel_img = f"characters/{nome_sanitizado}/reference.png"
                abs_img = str(ref_path)

    # Recupera estado prévio do Flow se o personagem já existia
    prev_ident = obter_identidade_projeto(projeto_id)
    prev_created = False
    prev_flow_id = ""
    prev_flow_name = ref_flow
    if prev_ident and prev_ident.get("nome", "").lower() == nome_sanitizado.lower():
        prev_created = prev_ident.get("flow_character_created", False)
        prev_flow_id = prev_ident.get("flow_character_id", "")
        prev_flow_name = prev_ident.get("flow_character_name") or ref_flow
        if not rel_img and prev_ident.get("imagem"):
            rel_img = prev_ident.get("imagem")
        if not abs_img and prev_ident.get("imagem_abs"):
            abs_img = prev_ident.get("imagem_abs")

    char_principal_obj = {
        "nome": nome_sanitizado,
        "arquivo_flow": arq_flow,
        "referencia_flow": prev_flow_name or ref_flow,
        "principal": True,
        "tipo": tipo,
        "imagem": rel_img,
        "imagem_abs": abs_img,
        "flow_character_created": prev_created,
        "flow_character_name": prev_flow_name or ref_flow,
        "flow_character_id": prev_flow_id,
        "status": "vinculado"
    }

    identidade_data = {
        "tipo": tipo,
        "nome": nome_sanitizado,
        "arquivo_flow": arq_flow,
        "referencia_flow": prev_flow_name or ref_flow,
        "imagem": rel_img,
        "imagem_abs": abs_img,
        "flow_character_created": prev_created,
        "flow_character_name": prev_flow_name or ref_flow,
        "flow_character_id": prev_flow_id,
        "status": "vinculado",
        "atualizado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
        "personagens": [char_principal_obj]
    }

    _identidade_path(projeto_id).write_text(
        json.dumps(identidade_data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print(f"[OK] Referência Flow salva: {ref_flow}", flush=True)


    # Sincroniza com meta.json
    meta_file = pdir / "meta.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            meta["identidade_tipo"] = tipo
            meta["nome_personagem"] = nome_sanitizado
            meta["arquivo_flow"] = arq_flow
            meta["referencia_flow"] = ref_flow
            meta["personagem_ref_global"] = abs_img
            meta["personagem_locked"] = True
            meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    # Salva também na Biblioteca Global de Personagens para reutilização
    if tipo == "personagem" and abs_img and Path(abs_img).exists():
        try:
            salvar_personagem_biblioteca_global(
                nome=nome_sanitizado,
                imagem_abs=abs_img,
                referencia_flow=ref_flow,
                flow_char_id="",
                visual_style=visual_style
            )
        except Exception:
            pass

    log_event("CHARACTER_INTEL", f"Identidade '{ref_flow}' ({tipo}) salva para o projeto '{projeto_id}'")

    return {
        "success": True,
        "identidade": identidade_data,
    }


def atualizar_status_flow_personagem(
    projeto_id: str,
    created: bool = True,
    flow_char_name: str = "",
    flow_char_id: str = ""
) -> bool:
    """Atualiza o estado de criação do personagem no Google Flow dentro de identidade.json."""
    p = _identidade_path(projeto_id)
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        data["flow_character_created"] = created
        if flow_char_name:
            data["flow_character_name"] = flow_char_name
            data["referencia_flow"] = flow_char_name
        if flow_char_id:
            data["flow_character_id"] = flow_char_id
        data["atualizado_em"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if "personagens" in data and len(data["personagens"]) > 0:
            data["personagens"][0]["flow_character_created"] = created
            if flow_char_name:
                data["personagens"][0]["flow_character_name"] = flow_char_name
                data["personagens"][0]["referencia_flow"] = flow_char_name
            if flow_char_id:
                data["personagens"][0]["flow_character_id"] = flow_char_id
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

        # Atualiza também na biblioteca global
        nome = data.get("nome", "")
        if nome:
            atualizar_personagem_biblioteca_global(nome, flow_char_id=flow_char_id, flow_char_name=flow_char_name)

        return True
    except Exception as e:
        log_event("CHARACTER_INTEL", f"Erro ao atualizar status flow personagem: {e}", level="warn")
        return False


def _get_biblioteca_personagens_dir() -> Path:
    from config import BIBLIOTECA_DIR
    d = BIBLIOTECA_DIR / "Personagens"
    d.mkdir(parents=True, exist_ok=True)
    return d


def salvar_personagem_biblioteca_global(
    nome: str,
    imagem_abs: str,
    referencia_flow: str = "",
    flow_char_id: str = "",
    visual_style: str = "photorealistic_cinematic"
) -> Dict[str, Any]:
    """Salva um personagem na Biblioteca Global do sistema para reutilização entre projetos."""
    nome_sanitizado = "".join(c for c in nome if c.isalnum() or c in ("-", "_", " ")).strip()
    if not nome_sanitizado:
        nome_sanitizado = "Personagem"

    bdir = _get_biblioteca_personagens_dir() / nome_sanitizado
    bdir.mkdir(parents=True, exist_ok=True)

    dest_img = bdir / "reference.png"
    if imagem_abs and Path(imagem_abs).exists() and str(imagem_abs) != str(dest_img):
        shutil.copy2(imagem_abs, dest_img)

    info = {
        "nome": nome_sanitizado,
        "referencia_flow": referencia_flow or f"@{nome_sanitizado}",
        "flow_character_name": referencia_flow or f"@{nome_sanitizado}",
        "flow_character_id": flow_char_id,
        "flow_character_created": bool(flow_char_id),
        "visual_style": visual_style,
        "imagem_abs": str(dest_img),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    (bdir / "character.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    return info


def atualizar_personagem_biblioteca_global(nome: str, flow_char_id: str = "", flow_char_name: str = ""):
    """Atualiza o registro do personagem na Biblioteca Global."""
    bdir = _get_biblioteca_personagens_dir() / nome
    cjson = bdir / "character.json"
    if cjson.exists():
        try:
            data = json.loads(cjson.read_text(encoding="utf-8"))
            if flow_char_id:
                data["flow_character_id"] = flow_char_id
                data["flow_character_created"] = True
            if flow_char_name:
                data["flow_character_name"] = flow_char_name
                data["referencia_flow"] = flow_char_name
            data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            cjson.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


def listar_biblioteca_personagens() -> List[Dict[str, Any]]:
    """Lista todos os personagens disponíveis na biblioteca global para reutilização."""
    bdir = _get_biblioteca_personagens_dir()
    lista = []
    nomes_vistos = set()

    # 1. Busca da pasta Biblioteca/Personagens/
    if bdir.exists():
        for f in sorted(bdir.iterdir()):
            if f.is_dir():
                cjson = f / "character.json"
                ref_img = f / "reference.png"
                if cjson.exists():
                    try:
                        data = json.loads(cjson.read_text(encoding="utf-8"))
                        data["has_image"] = ref_img.exists()
                        data["imagem_abs"] = str(ref_img) if ref_img.exists() else data.get("imagem_abs", "")
                        lista.append(data)
                        nomes_vistos.add(data.get("nome", "").lower())
                    except Exception:
                        pass

    # 2. Busca também personagens cadastrados em projetos existentes para complementar
    if PROJETOS_DIR.exists():
        for pdir in sorted(PROJETOS_DIR.iterdir()):
            if pdir.is_dir():
                ipath = pdir / IDENTIDADE_FILE
                if ipath.exists():
                    try:
                        idata = json.loads(ipath.read_text(encoding="utf-8"))
                        nome = idata.get("nome", "").strip()
                        if nome and nome.lower() not in nomes_vistos and idata.get("tipo") == "personagem":
                            img_abs = idata.get("imagem_abs", "")
                            item = {
                                "nome": nome,
                                "referencia_flow": idata.get("referencia_flow", f"@{nome}"),
                                "flow_character_name": idata.get("flow_character_name", f"@{nome}"),
                                "flow_character_id": idata.get("flow_character_id", ""),
                                "flow_character_created": idata.get("flow_character_created", False),
                                "imagem_abs": img_abs,
                                "has_image": bool(img_abs and Path(img_abs).exists()),
                                "updated_at": idata.get("atualizado_em", "")
                            }
                            lista.append(item)
                            nomes_vistos.add(nome.lower())
                    except Exception:
                        pass

    return lista


def vincular_personagem_da_biblioteca(projeto_id: str, nome: str) -> Dict[str, Any]:
    """Vincula um personagem existente da Biblioteca Global diretamente ao projeto atual."""
    biblioteca = listar_biblioteca_personagens()
    char_match = next((c for c in biblioteca if c.get("nome", "").lower() == nome.lower()), None)
    if not char_match:
        return {"success": False, "error": f"Personagem '{nome}' não encontrado na biblioteca."}

    img_origem = char_match.get("imagem_abs", "")
    res = salvar_identidade_projeto(
        projeto_id=projeto_id,
        tipo="personagem",
        nome=char_match.get("nome", nome),
        referencia_flow=char_match.get("referencia_flow", f"@{nome}"),
        arquivo_origem=img_origem if (img_origem and Path(img_origem).exists()) else None,
        visual_style=char_match.get("visual_style", "photorealistic_cinematic")
    )
    if char_match.get("flow_character_created"):
        atualizar_status_flow_personagem(
            projeto_id=projeto_id,
            created=True,
            flow_char_name=char_match.get("flow_character_name", f"@{nome}"),
            flow_char_id=char_match.get("flow_character_id", "")
        )
    return {
        "success": True,
        "identidade": obter_identidade_projeto(projeto_id)
    }


def obter_identidade_projeto(projeto_id: str) -> Optional[Dict[str, Any]]:
    """
    Retorna a identidade configurada no projeto (identidade.json)
    com garantia de estrutura 'personagens' e fallback transparente.
    """
    ipath = _identidade_path(projeto_id)
    if ipath.exists():
        try:
            data = json.loads(ipath.read_text(encoding="utf-8"))
            if data.get("referencia_flow"):
                if "personagens" not in data or not data["personagens"]:
                    data["personagens"] = [{
                        "nome": data.get("nome", ""),
                        "referencia_flow": data.get("referencia_flow", ""),
                        "principal": True,
                        "tipo": data.get("tipo", "personagem"),
                        "imagem": data.get("imagem"),
                        "imagem_abs": data.get("imagem_abs")
                    }]
                return data
        except Exception:
            pass

    # Fallback para personagem ativo legado
    char_legado = obter_personagem_ativo(projeto_id)
    if char_legado:
        nome = char_legado.get("name", "Personagem")
        ref_abs = char_legado.get("reference_image_abs")
        ref_flow = f"@{nome}"
        char_obj = {
            "nome": nome,
            "referencia_flow": ref_flow,
            "principal": True,
            "tipo": "personagem",
            "imagem": char_legado.get("reference_image"),
            "imagem_abs": ref_abs
        }
        return {
            "tipo": "personagem",
            "nome": nome,
            "referencia_flow": ref_flow,
            "imagem": char_legado.get("reference_image"),
            "imagem_abs": ref_abs,
            "atualizado_em": "",
            "personagens": [char_obj]
        }

    return None


def detectar_presenca_personagem_cena(cena: Dict[str, Any], nome_personagem: str = "") -> bool:
    """
    Determina se a cena descreve um sujeito humano / apresentador (uses_character = True)
    ou se é um b-roll puro de objeto, planta, detalhe ou close-up sem pessoas (uses_character = False).
    """
    # Avalia o texto narrado / falado da cena
    fala = f"{cena.get('narration', '')} {cena.get('texto', '')} {cena.get('text', '')}".lower().strip()
    if not fala:
        fala = f"{cena.get('descricao', '')} {cena.get('prompt_imagem', '')}".lower()

    # 1. Termos humanos (avaliados estritamente por limites de palavras)
    termos_humanos = [
        "eu", "meu", "minha", "meus", "minhas", "mostro", "mostrando", "vou",
        "apresentador", "apresentadora", "homem", "mulher", "pessoa", "pessoas",
        "jardineiro", "jardineira", "rosto", "olhando", "segurando", "falando",
        "caminhando", "apontando", "expressão", "reação", "cara", "narrador",
        "host", "speaker", "presenter", "gardener", "person", "people", "man",
        "woman", "male", "female", "portrait", "standing", "holding", "talking",
        "looking", "hands holding", "character"
    ]
    if nome_personagem:
        termos_humanos.append(nome_personagem.lower().strip())

    # 2. Termos de b-roll sem pessoas
    termos_sem_pessoa = [
        "apenas a planta", "close na rosa", "close nas rosas", "detalhe da casca",
        "banana peels in soil", "close-up of banana", "soil texture", "pure b-roll",
        "no people", "no person", "macro shot of", "close-up of the rose",
        "planta crescendo", "adubo no solo", "close no solo", "still life", "vejam as rosas"
    ]

    # Checa termos sem pessoas se não houver menção explícita ao apresentador/eu
    for tsp in termos_sem_pessoa:
        if tsp in fala and not any(re.search(r'\b' + re.escape(th) + r'\b', fala) for th in ["eu", "meu", "mostro", "segurando", "apresentador", nome_personagem.lower()] if th):
            return False

    for th in termos_humanos:
        if not th:
            continue
        if re.search(r'\b' + re.escape(th) + r'\b', fala):
            return True

    return False


def obter_personagem_cena(projeto_id: str, cena: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Retorna a entidade oficial de personagem apropriada para a cena (FASE 11.1 - CHARACTER IDENTITY LOCK):
    - Prioridade 1: Se cena possui uses_character=True e character_ref preenchido, retornar imediatamente.
    - Prioridade 2: Se scene_type é avatar_talking, avatar_action, hybrid, cta (ou prompt tem @Nome), forçar personagem bloqueado.
    - Prioridade 3: Somente usar detecção por regex se não existir decisão anterior no Scene Plan.
    """
    idt = obter_identidade_projeto(projeto_id)
    if not idt:
        return {"uses_character": False, "character_ref": ""}

    char_list = idt.get("personagens") or []
    nome_char = idt.get("nome", "")
    ref_char = idt.get("referencia_flow", f"@{nome_char}" if nome_char else "@Personagem")
    flow_id = idt.get("flow_character_id", "")
    img_abs = idt.get("imagem_abs", "")
    tipo_char = idt.get("tipo", "personagem")
    arq_flow = idt.get("arquivo_flow", "reference.png")

    scene_type = (cena.get("scene_type") or "").strip().lower()
    prompt_txt = f"{cena.get('prompt_imagem', '')} {cena.get('visual_prompt', '')}".lower()
    tipos_humanos = {"avatar_talking", "avatar_action", "hybrid", "cta"}

    # PRIORIDADE 1: Se cena possui uses_character=True ou character_ref explícito
    uses_char_definido = cena.get("uses_character")
    char_ref_cena = (cena.get("character_ref") or "").strip()

    if uses_char_definido is True or bool(char_ref_cena):
        ref_final = char_ref_cena or ref_char
        return {
            "uses_character": True,
            "character_ref": ref_final,
            "nome": nome_char,
            "arquivo_flow": arq_flow,
            "referencia_flow": ref_final,
            "flow_character_id": flow_id,
            "flow_character_name": idt.get("flow_character_name", ref_final),
            "flow_character_created": idt.get("flow_character_created", False),
            "tipo": tipo_char,
            "imagem": idt.get("imagem"),
            "imagem_abs": img_abs,
            "status": idt.get("status", "vinculado"),
            "principal": True
        }

    # PRIORIDADE 2: Se scene_type é humano (avatar_talking, avatar_action, hybrid, cta) ou prompt contém @Nome
    tem_tag_no_prompt = bool(nome_char and f"@{nome_char.lower()}" in prompt_txt) or bool(ref_char and ref_char.lower() in prompt_txt)
    if (scene_type in tipos_humanos or tem_tag_no_prompt) and nome_char:
        return {
            "uses_character": True,
            "character_ref": ref_char,
            "nome": nome_char,
            "arquivo_flow": arq_flow,
            "referencia_flow": ref_char,
            "flow_character_id": flow_id,
            "flow_character_name": idt.get("flow_character_name", ref_char),
            "flow_character_created": idt.get("flow_character_created", False),
            "tipo": tipo_char,
            "imagem": idt.get("imagem"),
            "imagem_abs": img_abs,
            "status": idt.get("status", "vinculado"),
            "principal": True
        }

    # PRIORIDADE 3: Somente usar detecção por regex se não existir decisão prévia (ex: uses_character não estava no dicionário)
    if uses_char_definido is None:
        uses_char = detectar_presenca_personagem_cena(cena, nome_char)
    else:
        uses_char = bool(uses_char_definido)

    if not uses_char:
        return {
            "uses_character": False,
            "character_ref": "",
            "nome": nome_char,
            "flow_character_id": flow_id,
            "referencia_flow": ref_char,
            "tipo": tipo_char,
            "imagem_abs": img_abs,
            "status": idt.get("status", "vinculado")
        }

    # Retorna entidade oficial vinculada
    return {
        "uses_character": True,
        "character_ref": ref_char,
        "nome": nome_char,
        "arquivo_flow": arq_flow,
        "referencia_flow": ref_char,
        "flow_character_id": flow_id,
        "flow_character_name": idt.get("flow_character_name", ref_char),
        "flow_character_created": idt.get("flow_character_created", False),
        "tipo": tipo_char,
        "imagem": idt.get("imagem"),
        "imagem_abs": img_abs,
        "status": idt.get("status", "vinculado"),
        "principal": True
    }


def remover_identidade_projeto(projeto_id: str) -> bool:
    """Remove a identidade permanente e limpa as referências."""
    ipath = _identidade_path(projeto_id)
    if ipath.exists():
        try:
            ipath.unlink()
        except Exception:
            pass

    # Limpa meta.json
    meta_file = _get_project_dir(projeto_id) / "meta.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            meta["identidade_tipo"] = ""
            meta["nome_personagem"] = ""
            meta["referencia_flow"] = ""
            meta["personagem_ref_global"] = None
            meta["personagem_locked"] = False
            meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    log_event("CHARACTER_INTEL", f"Identidade removida do projeto '{projeto_id}'")
    return True


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


# ===========================================================================
# SISTEMA MULTIRREFERÊNCIA DE PERSONAGENS & ESTILOS (CONSISTÊNCIA) — FASE A.1
# ===========================================================================

REFERENCES_FILE = "references.json"

TIPOS_VALIDOS = {"character", "style"}
MAPA_TIPOS_COMPATIBILIDADE = {
    "personagem": "character",
    "character": "character",
    "estilo": "style",
    "style": "style",
}


def sanitizar_alias(alias: str) -> str:
    """
    Sanitiza e normaliza o alias para o formato canônico @nome_valido em lowercase.
    
    Regras estritas:
    - Remoção de acentos/diacríticos
    - Lowercase obrigatório
    - Espaços, hífens e caracteres especiais convertidos para underscore
    - Prefixo único '@'
    - Proteção contra path traversal (rejeita '..', '/', '\\', null bytes)
    - Rejeita strings vazias
    """
    raw = (alias or "").strip()
    if not raw:
        raise ValueError("Alias não pode ser vazio.")

    # Proteção estrita contra path traversal
    if ".." in raw or "/" in raw or "\\" in raw or "\x00" in raw:
        raise ValueError("Alias contém caracteres de path traversal inválidos.")

    if raw.startswith("@"):
        raw = raw[1:].strip()

    if not raw:
        raise ValueError("Alias não pode ser vazio.")

    # 1. Normalização NFKD para decompor caracteres acentuados
    nfkd = unicodedata.normalize("NFKD", raw)
    ascii_txt = "".join(c for c in nfkd if not unicodedata.combining(c))
    ascii_txt = ascii_txt.lower()

    # 2. Converte qualquer caractere não alfanumérico em underscore
    corpo = re.sub(r"[^a-z0-9]+", "_", ascii_txt).strip("_")

    if not corpo:
        raise ValueError("Alias inválido após sanitização.")

    return f"@{corpo}"


def normalizar_tipo_referencia(tipo: str) -> str:
    """
    Valida e normaliza o tipo de referência para os tipos canônicos: 'character' ou 'style'.
    Aceita 'personagem' e 'estilo' para compatibilidade na entrada, mapeando para o canônico.
    Rejeita 'objeto' ou qualquer outro tipo.
    """
    t = (tipo or "").strip().lower()
    if t not in MAPA_TIPOS_COMPATIBILIDADE:
        raise ValueError(
            f"Tipo de referência inválido: '{tipo}'. "
            f"Tipos permitidos: 'character' ou 'style'."
        )
    return MAPA_TIPOS_COMPATIBILIDADE[t]


def _references_path(projeto_id: str) -> Path:
    return _get_project_dir(projeto_id) / REFERENCES_FILE


def _get_references_dir(projeto_id: str) -> Path:
    d = _get_project_dir(projeto_id) / "references"
    d.mkdir(parents=True, exist_ok=True)
    return d


def listar_referencias_projeto(projeto_id: str) -> List[Dict[str, Any]]:
    """
    Retorna todas as referências cadastradas no projeto.
    
    OPERAÇÃO PURA DE LEITURA (FASE A.1):
    - Se references.json NÃO existir: retorna lista vazia [] (NÃO cria arquivo, NÃO migra nada).
    - Se references.json existir mas estiver corrompido/inválido: LANÇA erro explícito.
    - NÃO modifica o disco nem altera o sistema legado.
    """
    pdir = _get_project_dir(projeto_id)
    rpath = _references_path(projeto_id)

    if not rpath.exists():
        return []

    try:
        content = rpath.read_text(encoding="utf-8")
        raw = json.loads(content)
    except Exception as e:
        log_event("CHARACTER_INTEL", f"Erro crítico: references.json corrompido no projeto '{projeto_id}': {e}", level="error")
        raise ValueError(f"Arquivo references.json corrompido ou formato JSON inválido no projeto '{projeto_id}'.")

    if isinstance(raw, list):
        lista_refs = raw
    elif isinstance(raw, dict) and "references" in raw and isinstance(raw["references"], list):
        lista_refs = raw["references"]
    else:
        raise ValueError(f"Estrutura inválida em references.json no projeto '{projeto_id}': esperado array JSON.")

    # Apenas computa has_image dinamicamente em memória (sem escrever em disco)
    resultado = []
    for item in lista_refs:
        if not isinstance(item, dict):
            continue
        c_item = dict(item)
        img_abs = c_item.get("imagem_abs", "")
        img_rel = c_item.get("imagem", "")
        has_img = False
        if img_abs and Path(img_abs).exists():
            has_img = True
        elif img_rel and (pdir / img_rel).exists():
            has_img = True
        c_item["has_image"] = has_img
        resultado.append(c_item)

    return resultado


def salvar_referencias_projeto(projeto_id: str, lista_refs: List[Dict[str, Any]]) -> bool:
    """
    Persiste a lista de referências em references.json.
    DESACOPLADO DO LEGADO: controla exclusivamente references.json e NÃO toca em identidade.json/meta.json/characters/.
    """
    pdir = _get_project_dir(projeto_id)
    pdir.mkdir(parents=True, exist_ok=True)
    rpath = _references_path(projeto_id)
    try:
        rpath.write_text(json.dumps(lista_refs, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception as e:
        log_event("CHARACTER_INTEL", f"Erro ao salvar references.json: {e}", level="error")
        raise IOError(f"Falha ao gravar references.json no projeto '{projeto_id}': {e}")


def adicionar_referencia_projeto(
    projeto_id: str,
    alias: str,
    nome: str = "",
    tipo: str = "character",
    imagem_bytes: Optional[bytes] = None,
    arquivo_origem: Optional[str] = None,
    visual_style: str = "photorealistic_cinematic",
    descricao: str = ""
) -> Dict[str, Any]:
    """
    Adiciona uma nova referência visual (character ou style) no projeto:
    - Valida e normaliza o alias canônico (lowercase)
    - Valida o tipo canônico ('character' ou 'style')
    - Recusa duplicidade de alias (sem overwrite silencioso)
    - Salva foto em caminho relativo references/<alias_folder>/reference.png
    - Registra em references.json
    - DESACOPLADO: NÃO modifica identidade.json, meta.json ou characters/
    """
    alias_canonico = sanitizar_alias(alias)
    tipo_canonico = normalizar_tipo_referencia(tipo)
    nome_display = (nome or "").strip() or (alias or "").lstrip("@").strip() or alias_canonico.lstrip("@")
    folder_name = alias_canonico.lstrip("@")

    pdir = _get_project_dir(projeto_id)
    lista = listar_referencias_projeto(projeto_id)

    # 1. Verificação estrita de duplicidade
    if any(r.get("alias") == alias_canonico for r in lista):
        raise ValueError(f"Referência com alias '{alias_canonico}' já existe no projeto '{projeto_id}'.")

    # 2. Validação estrita de imagem obrigatória
    ref_dir = _get_references_dir(projeto_id) / folder_name
    ref_path = ref_dir / "reference.png"

    has_valid_source = False
    if arquivo_origem and Path(arquivo_origem).exists() and Path(arquivo_origem).is_file():
        has_valid_source = True
    elif imagem_bytes and len(imagem_bytes) > 0:
        has_valid_source = True
    elif ref_path.exists() and ref_path.is_file() and ref_path.stat().st_size > 0:
        has_valid_source = True

    if not has_valid_source:
        raise ValueError("Arquivo de imagem de referência é obrigatório para cadastrar personagem ou estilo.")

    # 3. Persistência de imagem isolada em references/<alias_folder>/reference.png
    ref_dir.mkdir(parents=True, exist_ok=True)
    if arquivo_origem and Path(arquivo_origem).exists():
        if Path(arquivo_origem).resolve() != ref_path.resolve():
            shutil.copy2(arquivo_origem, ref_path)
    elif imagem_bytes:
        ref_path.write_bytes(imagem_bytes)

    abs_img = str(ref_path)
    rel_img = f"references/{folder_name}/reference.png"

    ref_obj = {
        "id": str(uuid.uuid4()),
        "alias": alias_canonico,
        "nome": nome_display,
        "tipo": tipo_canonico,
        "descricao": descricao or f"Referência {tipo_canonico} {alias_canonico}",
        "imagem": rel_img,
        "imagem_abs": abs_img,
        "has_image": True,
        "flow_character_id": "",
        "flow_character_name": alias_canonico,
        "flow_character_created": False,
        "visual_style": visual_style,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    lista.append(ref_obj)
    salvar_referencias_projeto(projeto_id, lista)

    log_event("CHARACTER_INTEL", f"Referência '{alias_canonico}' ({tipo_canonico}) adicionada ao projeto '{projeto_id}'")
    return {
        "success": True,
        "referencia": ref_obj,
        "total_referencias": len(lista)
    }


def renomear_referencia_projeto(
    projeto_id: str,
    alias_atual: str,
    novo_nome_ou_alias: str
) -> Dict[str, Any]:
    """
    Renomeia uma referência existente com segurança:
    - Gera novo alias canônico
    - Recusa colisão se o novo alias já existir
    - Move/renomeia o diretório físico em references/<alias>/
    - Atualiza name, alias, caminhos de imagem e updated_at
    - Preserva id, dados do Flow e created_at
    """
    alias_orig = sanitizar_alias(alias_atual)
    novo_alias = sanitizar_alias(novo_nome_ou_alias)
    novo_nome = novo_nome_ou_alias.lstrip("@").strip() or novo_alias.lstrip("@")

    pdir = _get_project_dir(projeto_id)
    lista = listar_referencias_projeto(projeto_id)

    item = next((r for r in lista if r.get("alias") == alias_orig), None)
    if not item:
        raise KeyError(f"Referência '{alias_orig}' não encontrada no projeto '{projeto_id}'.")

    # Se o alias mudou, valida colisão e renomeia diretório
    if novo_alias != alias_orig:
        if any(r.get("alias") == novo_alias for r in lista):
            raise ValueError(f"Conflito: o alias '{novo_alias}' já existe no projeto '{projeto_id}'.")

        old_folder_name = alias_orig.lstrip("@")
        new_folder_name = novo_alias.lstrip("@")

        old_dir = _get_references_dir(projeto_id) / old_folder_name
        new_dir = _get_references_dir(projeto_id) / new_folder_name

        if old_dir.exists() and old_dir.is_dir():
            new_dir.parent.mkdir(parents=True, exist_ok=True)
            if new_dir.exists():
                shutil.rmtree(new_dir)
            old_dir.rename(new_dir)

        item["alias"] = novo_alias
        item["nome"] = novo_nome
        item["flow_character_name"] = novo_alias

        if item.get("imagem") or item.get("has_image"):
            item["imagem"] = f"references/{new_folder_name}/reference.png"
            item["imagem_abs"] = str(new_dir / "reference.png")
            item["has_image"] = (new_dir / "reference.png").exists()
    else:
        # Apenas alteração do nome de exibição
        item["nome"] = novo_nome

    item["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    salvar_referencias_projeto(projeto_id, lista)
    log_event("CHARACTER_INTEL", f"Referência '{alias_orig}' renomeada para '{novo_alias}' no projeto '{projeto_id}'")

    return {
        "success": True,
        "referencia": item
    }


def remover_referencia_projeto(projeto_id: str, alias: str) -> bool:
    """
    Remove uma referência do projeto:
    - Remove o registro de references.json
    - Deleta apenas a pasta daquela referência em references/<alias>/
    - NÃO promove referências para identidade principal
    - NÃO modifica identidade.json, meta.json ou characters/
    """
    alias_clean = sanitizar_alias(alias)
    lista = listar_referencias_projeto(projeto_id)

    item = next((r for r in lista if r.get("alias") == alias_clean), None)
    if not item:
        raise KeyError(f"Referência '{alias_clean}' não encontrada no projeto '{projeto_id}'.")

    lista = [r for r in lista if r.get("alias") != alias_clean]

    # Remove exclusivamente o diretório dessa referência
    folder_name = alias_clean.lstrip("@")
    ref_dir = _get_references_dir(projeto_id) / folder_name
    if ref_dir.exists() and ref_dir.is_dir():
        try:
            shutil.rmtree(ref_dir)
        except Exception as e:
            log_event("CHARACTER_INTEL", f"Aviso ao remover pasta {ref_dir}: {e}", level="warn")

    salvar_referencias_projeto(projeto_id, lista)
    log_event("CHARACTER_INTEL", f"Referência '{alias_clean}' removida do projeto '{projeto_id}'")
    return True


def obter_referencia_por_alias(projeto_id: str, alias: str) -> Optional[Dict[str, Any]]:
    """Retorna os dados da referência que casa com o alias informado (ex: '@marcos')."""
    try:
        alias_clean = sanitizar_alias(alias)
    except ValueError:
        return None
    lista = listar_referencias_projeto(projeto_id)
    return next((r for r in lista if r.get("alias") == alias_clean), None)


def atualizar_status_flow_referencia(
    projeto_id: str,
    alias: str,
    created: bool = True,
    flow_char_id: str = "",
    flow_char_name: str = ""
) -> bool:
    """Atualiza o estado de criação no Google Flow para uma referência específica."""
    alias_clean = sanitizar_alias(alias)
    lista = listar_referencias_projeto(projeto_id)
    item = next((r for r in lista if r.get("alias") == alias_clean), None)
    if not item:
        return False

    item["flow_character_created"] = created
    if flow_char_id:
        item["flow_character_id"] = flow_char_id
    if flow_char_name:
        item["flow_character_name"] = flow_char_name
    item["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    salvar_referencias_projeto(projeto_id, lista)
    return True


