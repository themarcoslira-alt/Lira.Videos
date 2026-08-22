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
import shutil
import hashlib
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
    Retorna a entidade oficial de personagem apropriada para a cena:
    - Identifica se a cena deve usar personagem (uses_character = True/False)
    - Prioriza o personagem real/oficial vinculado no Flow com ID e @Nome
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

    # Determina se esta cena específica envolve sujeito humano
    uses_char = detectar_presenca_personagem_cena(cena, nome_char)

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

    # 1. Busca por nome específico se informado na cena
    nome_cena = (cena.get("nome_personagem") or "").strip()
    ref_cena = (cena.get("referencia_flow") or "").strip()

    if nome_cena or ref_cena:
        for c in char_list:
            if nome_cena and c.get("nome", "").lower() == nome_cena.lower():
                c_ret = dict(c)
                c_ret["uses_character"] = True
                c_ret["character_ref"] = c.get("referencia_flow") or f"@{c.get('nome')}"
                return c_ret
            if ref_cena and c.get("referencia_flow", "").lower() == ref_cena.lower():
                c_ret = dict(c)
                c_ret["uses_character"] = True
                c_ret["character_ref"] = ref_cena
                return c_ret

    # 2. Retorna a entidade principal oficial vinculada
    return {
        "uses_character": True,
        "character_ref": ref_char,
        "nome": nome_char,
        "arquivo_flow": idt.get("arquivo_flow", "reference.png"),
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
