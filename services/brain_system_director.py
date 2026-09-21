"""
services/brain_system_director.py — Brain System Director Central (Lira Studio 3.0)
===================================================================================
Orquestrador central unificado que coordena atomicamente as 12 inteligências
de produção audiovisual do Lira Studio:
  1. DeepSeek Prompt Service (Context Pack Global & Batch Prompts)
  2. LLM Client (Abstração com retries e fallback determinístico)
  3. Story Card Detector Service (Detecção de badges: Step, Myth, Mistake, Data)
  4. Story Card Prompt Builder (Prompts 2D flat vector Kurzgesagt/Vox)
  5. Enhanced Scene Classifier (Papéis narrativos, verbos de ação, emoção)
  6. Narrative Distributor (Equilíbrio global 8% Avatar / 92% B-Roll)
  7. Story Rhythm Service (Pacing e alternância cinemática de planos)
  8. Animation Director Service (Decisão inteligente de movimento Veo/Flow)
  9. CapCut Library Service (Catálogo e resolução de transições nativas)
 10. Audio Processor (Corte e remapeamento de silêncios)
 11. Visual Memory Engine (Bíblia Visual persistente do projeto)
 12. Continuity Checker Service (Auditoria contra desvios de personagem e estilo)
"""

import os
import json
import shutil
import logging
import time
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

from config import BASE_DIR, PROJETOS_DIR
from services.event_logger import log_event

# 1. Inteligência de Prompts e Raciocínio Contextual
from services.deepseek_prompt_service import (
    analisar_contexto_global,
    gerar_prompts_lote,
    executar_critic_prompts,
    aplicar_prompts_animacao_deepseek
)
from services.llm_client import LLMClient, extrair_json

# 2. Detecção e Prompts de Story Cards
from services.story_card_detector_service import (
    detect_story_cards,
    apply_story_cards_to_plan
)
from services.story_card_prompt_builder import (
    build_story_card_prompt
)

# 3. Análise Narrativa, Ritmo e Pacing Visual
from services.enhanced_scene_classifier import (
    get_narrative_role,
    get_action_verb,
    get_emotion,
    estimate_intensity,
    get_broll_query
)
from services.narrative_distributor import (
    rebalancear_narrativa,
    NARRATIVA_VERSAO
)
from services.story_rhythm_service import (
    otimizar_ritmo_cenas
)

# 4. Decisão de Animação e Movimento de Câmera
from services.animation_director_service import (
    direcionar_animacao_cena
)

# 5. Biblioteca Nativa do CapCut Desktop
from services.capcut_library_service import (
    CapCutLibraryService
)

# 6. Processamento de Áudio e Silêncios
from services.audio_processor import (
    cortar_silencio
)

# 7. Memória Visual do Projeto e Auditoria de Continuidade
from services.visual_memory_engine import (
    construir_memoria_visual_projeto,
    obter_memoria_visual_projeto,
    salvar_memoria_visual_projeto
)
from services.continuity_checker_service import (
    verificar_continuidade_cena
)

# 8. Camada de Persistência Oficial (Fonte Única da Verdade)
from services.scene_plan_service import (
    carregar_scene_plan,
    salvar_scene_plan,
    garantir_estrutura_pastas,
    _nova_cena
)


BRAIN_OUTPUT_FILE = "brain_director_output.json"
GLOBAL_CONTEXT_FILE = "global_context_pack.json"

AVATAR_STYLE_THIRD_PERSON = (
    "Third-person demonstration. Hands in foreground. "
    "No face-to-camera dialogue. Documentary garden setting."
)


def _is_avatar_scene(cena: Dict[str, Any]) -> bool:
    """Verifica se a cena é classificada como Avatar (por papel, tipo ou vínculo de personagem)."""
    if str(cena.get("avatar_style", "")).lower() in ("first_person_pov", "pov", "broll_pov"):
        return False
    role = str(cena.get("narrative_role", "")).upper()
    stype = str(cena.get("scene_type", "")).lower()
    uses_char = bool(cena.get("uses_character", False))
    char_ref = str(cena.get("character_ref", "") or cena.get("nome_personagem", "")).strip()
    return role == "AVATAR" or stype.startswith("avatar_") or (uses_char and bool(char_ref))


def _aplicar_estilo_avatar_base(
    prompt_imagem: str,
    prompt_animacao: str,
    character_ref: str = "@Marcos"
) -> Tuple[str, str]:
    """
    Aplica a regra sistemática 'Third-person demonstration' aos prompts de avatar.
    Garante os 4 pilares:
      1. Third-person demonstration
      2. Hands in foreground
      3. No face-to-camera dialogue
      4. Documentary garden setting
    Implementa proteção estrita anti-duplicação e substituição de termos de portrait.
    """
    char = character_ref if character_ref else "@Marcos"

    # --- 1. PROMPT DE IMAGEM ---
    p_img = (prompt_imagem or "").strip()
    if not p_img:
        p_img = (
            f"Photorealistic documentary third-person demonstration shot of {char} in rustic garden setting, "
            f"hands in foreground examining tomato plants, no face-to-camera dialogue, natural morning daylight with soft contrast, "
            f"35mm lens, shallow depth of field, 8k resolution, highly detailed."
        )
    else:
        # Substitui padrões de retrato frontal por enquadramento documental em terceira pessoa
        if "cinematic portrait shot" in p_img:
            p_img = p_img.replace("cinematic portrait shot", "documentary third-person demonstration shot")
        elif "portrait shot" in p_img:
            p_img = p_img.replace("portrait shot", "documentary third-person demonstration shot")
        elif "cinematic portrait" in p_img:
            p_img = p_img.replace("cinematic portrait", "documentary third-person demonstration shot")

        # Anti-duplicação: só adiciona se ainda não contiver "third-person" ou "third person"
        if "third-person" not in p_img.lower() and "third person" not in p_img.lower():
            p_img = f"Photorealistic documentary third-person demonstration shot of {char}, hands in foreground, no face-to-camera dialogue, {p_img}"

        # Assegura que "hands in foreground" esteja presente no prompt visual do avatar
        if "hands in foreground" not in p_img.lower():
            if "35mm lens" in p_img:
                p_img = p_img.replace("35mm lens", "hands in foreground, 35mm lens")
            elif "natural daylight" in p_img:
                p_img = p_img.replace("natural daylight", "hands in foreground, natural daylight")
            else:
                p_img = f"{p_img}, hands in foreground"

    # --- 2. PROMPT DE ANIMAÇÃO ---
    p_anim = (prompt_animacao or "").strip()
    # Remove artefatos de diálogo direto ou sincronia labial incompatíveis
    for bad in ["speaking naturally.", "speaking naturally,", "speaking naturally", "talking to camera.", "talking to camera"]:
        p_anim = p_anim.replace(bad, "").strip()

    # Anti-duplicação para animação
    if "third-person" not in p_anim.lower() and "third person" not in p_anim.lower():
        if p_anim:
            p_anim = f"{AVATAR_STYLE_THIRD_PERSON} {p_anim}"
        else:
            p_anim = f"{AVATAR_STYLE_THIRD_PERSON} Natural body movement, documentary camera angle, subtle handheld sway."

    return p_img, p_anim



class BrainSystemDirector:
    """
    Orquestrador Mestre do Lira Studio.
    Executa a direção audiovisual integrada do projeto em 5 fases atômicas.
    """

    def __init__(self, projeto_id: str, logger=None):
        self.projeto_id = str(projeto_id).strip()
        self.logger = logger or logging.getLogger(f"BrainDirector.{self.projeto_id}")
        self.project_dir = PROJETOS_DIR / self.projeto_id
        self._capcut_service = CapCutLibraryService()
        self._context_pack_cache: Optional[Dict[str, Any]] = None

    # =========================================================================
    # MÉTODO PRINCIPAL PÚBLICO
    # =========================================================================

    def orquestar_producao_completa(
        self,
        entrada_consolidada: Dict[str, Any],
        options: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Executa a pipeline audiovisual de 5 fases a partir de uma entrada consolidada.

        Fase 0: Ingestão e Inicialização da Bíblia Visual
        Fase 1: Análise Narrativa e Equilíbrio de Ritmo
        Fase 2: Decisões Audiovisuais e CapCut Native Mapping
        Fase 3: Enriquecimento Atômico, Prompts e Smart FX
        Fase 4: Compilação e Persistência em lira_scene_plan.json
        """
        inicio = time.monotonic()
        options = options or {}
        log_event("BRAIN_DIRECTOR", f"Iniciando orquestração completa para '{self.projeto_id}'")

        # Fase 0: Ingestão
        contexto_fase0 = self._fase_0_ingestao(entrada_consolidada)
        memoria_visual = contexto_fase0["memoria_visual"]
        context_pack = contexto_fase0["context_pack"]

        # Fase 1: Análise Narrativa
        cenas_analisadas = self._fase_1_analise_narrativa(entrada_consolidada, memoria_visual)
        self._aplicar_manual_overrides(cenas_analisadas)

        # Fase 2: Decisões Audiovisuais
        decisoes_fase2 = self._fase_2_decisoes_audiovisuais(entrada_consolidada, cenas_analisadas, options)

        # Fase 3: Enriquecimento Atômico
        saida_enriquecida = self._fase_3_enriquecimento_atomico(
            entrada=entrada_consolidada,
            decisoes=decisoes_fase2,
            memoria_visual=memoria_visual,
            context_pack=context_pack,
            options=options
        )

        # Fase 4: Compilação e Persistência
        self._fase_4_compilacao_persistencia(entrada_consolidada, saida_enriquecida)

        tempo_total = round(time.monotonic() - inicio, 2)
        saida_enriquecida["tempo_execucao_s"] = tempo_total
        log_event(
            "BRAIN_DIRECTOR",
            f"Orquestração de '{self.projeto_id}' concluída em {tempo_total}s ({len(saida_enriquecida.get('cenas', {}))} cenas)."
        )

        return saida_enriquecida

    # =========================================================================
    # OVERRIDES MANUAIS DE CENAS (manual_overrides.json)
    # =========================================================================

    def _carregar_manual_overrides(self) -> Dict[str, Any]:
        """Carrega manual_overrides.json da pasta do projeto com tratamento resiliente."""
        ov_file = self.project_dir / "manual_overrides.json"
        if not ov_file.exists():
            return {}
        try:
            dados = json.loads(ov_file.read_text(encoding="utf-8"))
            if isinstance(dados, dict):
                cenas_map = {}
                if "cenas" in dados and isinstance(dados["cenas"], dict):
                    cenas_map.update(dados["cenas"])
                if "overrides" in dados and isinstance(dados["overrides"], list):
                    for item in dados["overrides"]:
                        if isinstance(item, dict):
                            cid = item.get("cena_id") if "cena_id" in item else item.get("id")
                            if cid is not None:
                                cenas_map[str(cid)] = item
                if cenas_map:
                    return {"cenas": cenas_map}
            log_event("BRAIN_DIRECTOR", f"manual_overrides.json em '{self.projeto_id}' tem estrutura inválida (esperado 'cenas': dict ou 'overrides': list).", level="warn")
            return {}
        except Exception as e:
            log_event("BRAIN_DIRECTOR", f"Erro ao ler manual_overrides.json em '{self.projeto_id}': {e}", level="warn")
            return {}

    def _carregar_galeria(self, nome_arquivo: str) -> Optional[Dict[str, Any]]:
        """Carrega arquivo JSON de config/galerias/ com fallback resiliente caso ausente."""
        try:
            gal_path = BASE_DIR / "config" / "galerias" / nome_arquivo
            if gal_path.exists():
                return json.loads(gal_path.read_text(encoding="utf-8"))
        except Exception as e:
            log_event("BRAIN_DIRECTOR", f"Aviso ao carregar galeria '{nome_arquivo}': {e}", level="warn")
        return None

    def _aplicar_manual_overrides(self, cenas_analisadas: List[Dict[str, Any]]) -> None:
        """
        Aplica overrides manuais após Fase 1 e antes da Fase 3.
        Valida texto_contem, protege contra reescrita de prompts, e na primeira
        aplicação move mídias antigas para _backup_reclassificacao/.
        A Cena 1 aceita overrides normalmente.
        """
        overrides_data = self._carregar_manual_overrides()
        if not overrides_data:
            return

        overrides = overrides_data.get("cenas", {})
        cenas_by_id = {c["id"]: c for c in cenas_analisadas}

        backup_dir = self.project_dir / "_backup_reclassificacao"

        for cid_str, cfg in overrides.items():
            try:
                cid = int(cid_str)
            except (ValueError, TypeError):
                continue

            cena = cenas_by_id.get(cid)
            if not cena:
                continue

            # Validação estrita de texto_contem
            texto_contem = str(cfg.get("texto_contem", "")).strip().lower()
            texto_cena = str(cena.get("texto", "")).lower()
            if texto_contem and texto_contem not in texto_cena:
                log_event("BRAIN_DIRECTOR", f"Override abortado para Cena {cid:03d}: 'texto_contem' ('{texto_contem}') não encontrado no texto da cena.", level="error")
                continue

            role_antigo = cena.get("narrative_role")
            novo_role = cfg.get("narrative_role") or ("BROLL" if str(cfg.get("scene_type", "")).startswith("broll") or not cfg.get("uses_character", True) else "AVATAR")

            # Aplica campos do override
            cena["manual_override"] = True
            cena["narrative_role"] = novo_role
            if "scene_type" in cfg:
                cena["scene_type"] = cfg["scene_type"]
            if "avatar_style" in cfg:
                cena["avatar_style"] = cfg["avatar_style"]
            stype_lower = str(cena.get("scene_type", "")).lower()
            astyle_lower = str(cena.get("avatar_style", "")).lower()
            if stype_lower in ("broll_pov", "pov") or astyle_lower in ("first_person_pov", "pov", "broll_pov"):
                cena["skip_facial_validation"] = True
                cena["validation_override_reason"] = "POV first-person has no visible face by design"
                cena["uses_character"] = False
                cena["avatar_required"] = False
                log_event("BRAIN_DIRECTOR", f"Cena {cid:03d}: Validação facial desabilitada (POV)", level="info")
            else:
                cena["uses_character"] = bool(cfg.get("uses_character", True))
                cena["avatar_required"] = bool(cfg.get("uses_character", True))
            if "character_ref" in cfg:
                cena["character_ref"] = cfg["character_ref"]
            elif not cena["uses_character"]:
                cena["character_ref"] = ""
            if "prompt_imagem" in cfg and cfg["prompt_imagem"]:
                cena["prompt_imagem"] = cfg["prompt_imagem"]
            if "prompt_animacao" in cfg and cfg["prompt_animacao"]:
                cena["prompt_animacao"] = cfg["prompt_animacao"]

            # 1e: Na primeira aplicação que mudar o role da cena, mover mídia antiga
            # para _backup_reclassificacao/ e resetar status para pendente
            marcador = backup_dir / f".marcador_backup_cena_{cid:03d}.json"
            if not marcador.exists() and role_antigo != novo_role:
                backup_dir.mkdir(parents=True, exist_ok=True)
                arquivos_movidos = []
                for pasta in ["cenas", "conteudo"]:
                    p_dir = self.project_dir / pasta
                    if p_dir.exists():
                        for f in list(p_dir.glob(f"{cid:02d}_*")) + list(p_dir.glob(f"{cid}_*")):
                            dst = backup_dir / f"{pasta}_{f.name}"
                            try:
                                shutil.move(str(f), str(dst))
                                arquivos_movidos.append(str(dst))
                            except Exception as e_mv:
                                log_event("BRAIN_DIRECTOR", f"Aviso ao mover mídia antiga da Cena {cid} ({f.name}): {e_mv}", level="warn")

                try:
                    marcador.write_text(json.dumps({
                        "cena_id": cid,
                        "data": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "de_role": role_antigo,
                        "para_role": novo_role,
                        "arquivos_movidos": arquivos_movidos
                    }, indent=2), encoding="utf-8")
                except Exception:
                    pass

                cena["status_reset"] = True
                cena["status"] = "PENDENTE"
                cena["image_status"] = "PENDING"
                cena["video_status"] = "PENDING"
                cena["arquivo_midia"] = ""
                cena["imagem_com_personagem"] = False
                log_event("BRAIN_DIRECTOR", f"Cena {cid:03d} reclassificada ({role_antigo} -> {novo_role}). {len(arquivos_movidos)} arquivos movidos para _backup_reclassificacao/.", level="info")

    # =========================================================================
    # FASE 0: INGESTÃO E INICIALIZAÇÃO DA BÍBLIA VISUAL
    # =========================================================================

    def _fase_0_ingestao(self, entrada: Dict[str, Any]) -> Dict[str, Any]:
        """
        Valida a entrada contra pré-requisitos essenciais, carrega ou constrói a
        Bíblia Visual do projeto e gera/recupera o Context Pack Global com cache.
        """
        garantir_estrutura_pastas(self.projeto_id)
        roteiro_data = entrada.get("roteiro_transcricao") or {}
        roteiro_texto = (
            roteiro_data.get("text")
            or " ".join(c.get("texto", "") for c in entrada.get("cenas", []))
            or ""
        ).strip()

        nome_personagem = entrada.get("personagem", "")
        if nome_personagem.startswith("@"):
            nome_personagem = nome_personagem[1:]
        estilo_visual = entrada.get("estilo_visual", "photorealistic_cinematic")

        # 1. Recupera ou gera Context Pack Global (com cache em memória e em disco)
        context_pack = entrada.get("global_context_pack")
        cache_path = self.project_dir / GLOBAL_CONTEXT_FILE

        if not context_pack and self._context_pack_cache:
            context_pack = self._context_pack_cache
        elif not context_pack and cache_path.exists():
            try:
                context_pack = json.loads(cache_path.read_text(encoding="utf-8"))
                self._context_pack_cache = context_pack
            except Exception:
                context_pack = None

        if not context_pack:
            try:
                import services.visual_presets_service as presets_svc
                import services.character_service as char_svc

                # Formata cenas compatíveis com analisar_contexto_global
                raw_cenas = entrada.get("cenas", [])
                cenas_formatadas = []
                for idx, c in enumerate(raw_cenas):
                    cid = int(c.get("id") or idx + 1)
                    t_ini = float(c.get("start") if c.get("start") is not None else (c.get("start_time") if c.get("start_time") is not None else (c.get("tempo_inicio") or 0.0)))
                    t_fim = float(c.get("end") if c.get("end") is not None else (c.get("end_time") if c.get("end_time") is not None else (c.get("tempo_fim") or (t_ini + float(c.get("duracao") or c.get("duration") or 4.0)))))
                    txt = str(c.get("texto") or c.get("narration") or "").strip()
                    cenas_formatadas.append({
                        "id": cid,
                        "scene_index": cid,
                        "tempo_inicio": t_ini,
                        "tempo_fim": t_fim,
                        "texto": txt,
                        "narration": txt
                    })

                if not cenas_formatadas and roteiro_texto:
                    cenas_formatadas = [{
                        "id": 1,
                        "scene_index": 1,
                        "tempo_inicio": 0.0,
                        "tempo_fim": 10.0,
                        "texto": roteiro_texto,
                        "narration": roteiro_texto
                    }]

                # Referências visuais / personagens do projeto
                referencias = []
                try:
                    refs_proj = char_svc.listar_referencias_projeto(self.projeto_id)
                    if refs_proj and isinstance(refs_proj, list):
                        referencias.extend(refs_proj)
                except Exception:
                    pass

                if not referencias and nome_personagem:
                    referencias.append({
                        "tipo": "character",
                        "alias": f"@{nome_personagem}",
                        "nome": nome_personagem,
                        "descricao": f"Apresentador e personagem principal {nome_personagem}"
                    })

                preset_data = presets_svc.obter_preset_por_id(estilo_visual)

                context_pack = analisar_contexto_global(
                    cenas=cenas_formatadas,
                    referencias=referencias,
                    estilo_preset=preset_data
                )
                if not context_pack or not isinstance(context_pack, dict):
                    raise ValueError("Context pack retornado vazio ou inválido")
            except Exception as e:
                log_event("BRAIN_DIRECTOR", f"Fallback no context pack global: {e}", level="warn")
                context_pack = {
                    "main_theme": self.projeto_id.replace("_", " ").title(),
                    "tone": "educational and engaging",
                    "color_palette": {"primary": "natural garden greens", "secondary": "warm earthy tones"},
                    "lighting": "natural morning daylight with soft contrast",
                    "world": "botanical garden environment",
                    "negative_constraints": [
                        "no cartoon", "no 3d render", "no low quality", "no deformed hands",
                        "no split screen", "no text", "no watermarks"
                    ]
                }

            self._context_pack_cache = context_pack
            try:
                cache_path.write_text(json.dumps(context_pack, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

        # 2. Inicializa ou obtém a Bíblia Visual do projeto
        memoria_visual = obter_memoria_visual_projeto(self.projeto_id)
        if not memoria_visual or not memoria_visual.get("personagem", {}).get("name"):
            identidade = {
                "nome": nome_personagem,
                "referencia_flow": f"@{nome_personagem}" if nome_personagem else "",
                "visual_style": estilo_visual
            }
            memoria_visual = construir_memoria_visual_projeto(
                projeto_id=self.projeto_id,
                contexto_visual=context_pack,
                identidade=identidade,
                roteiro_texto=roteiro_texto
            )
            salvar_memoria_visual_projeto(self.projeto_id, memoria_visual)

        return {
            "memoria_visual": memoria_visual,
            "context_pack": context_pack,
            "roteiro_texto": roteiro_texto
        }

    # =========================================================================
    # FASE 1: ANÁLISE NARRATIVA E RITMO
    # =========================================================================

    def _fase_1_analise_narrativa(
        self,
        entrada: Dict[str, Any],
        memoria_visual: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Classifica papéis narrativos (HOOK, AVATAR, BROLL, CTA, CLOSING),
        detecta badges de Story Cards, aplica cota global 8% Avatar / 92% B-Roll
        e equilibra a alternância de planos.
        """
        raw_cenas = entrada.get("cenas", [])
        word_timestamps = entrada.get("word_timestamps", [])
        char_ref = memoria_visual.get("personagem", {}).get("reference", "")

        # Pré-processa word_timestamps: suporta lista plana ou lista de segmentos com 'words'
        flat_words = []
        if word_timestamps and isinstance(word_timestamps, list):
            for item in word_timestamps:
                if isinstance(item, dict):
                    if "words" in item and isinstance(item["words"], list):
                        flat_words.extend(item["words"])
                    elif "w" in item:
                        flat_words.append(item)

        cenas_analisadas = []
        for idx, c in enumerate(raw_cenas):
            cid = int(c.get("id") or idx + 1)
            texto = str(c.get("texto") or c.get("narration") or "").strip()
            start = float(c.get("start") if c.get("start") is not None else (c.get("start_time") if c.get("start_time") is not None else (c.get("tempo_inicio") or 0.0)))
            dur_default = float(c.get("duracao") or c.get("duration") or 4.0)
            end = float(c.get("end") if c.get("end") is not None else (c.get("end_time") if c.get("end_time") is not None else (c.get("tempo_fim") or (start + dur_default))))

            # 1. Classificação Narrativa Determinística
            role = get_narrative_role(texto, scene_index=idx + 1)
            verb = get_action_verb(texto, role)
            emotion = get_emotion(texto, role)
            intensity = estimate_intensity(texto, role)
            broll_q = get_broll_query(texto, c.get("scene_type", "")) if role == "BROLL" else None

            uses_char = (role in ("HOOK", "AVATAR", "CTA")) and bool(char_ref)
            stype = c.get("scene_type")
            if not stype:
                stype = "avatar_talking" if uses_char else "broll_macro"

            astyle = str(c.get("avatar_style") or "").strip().lower()
            stype_lower = str(stype or "").strip().lower()
            is_pov = (stype_lower in ("broll_pov", "pov") or astyle in ("first_person_pov", "pov", "broll_pov"))
            if is_pov:
                uses_char = False

            cena_dict = {
                "id": cid,
                "scene_index": cid,
                "start": round(start, 3),
                "end": round(end, 3),
                "tempo_inicio": round(start, 3),
                "tempo_fim": round(end, 3),
                "duracao": round(max(0.0, end - start), 3),
                "texto": texto,
                "narration": texto,
                "scene_type": stype,
                "avatar_style": astyle or ("first_person_pov" if is_pov else ""),
                "skip_facial_validation": bool(is_pov),
                "validation_override_reason": "POV first-person has no visible face by design" if is_pov else "",
                "narrative_role": role,
                "action_verb": verb,
                "emotion": emotion,
                "intensity": intensity,
                "broll_query": broll_q,
                "uses_character": uses_char,
                "character_ref": char_ref if uses_char else "",
                "prompt_imagem": c.get("prompt_imagem", ""),
                "prompt_animacao": c.get("prompt_animacao", ""),
                "tipo": c.get("tipo"),
                "animar": c.get("animar"),
                "animar_depois": c.get("animar_depois"),
                "animate_later": c.get("animate_later"),
                "media_intent": c.get("media_intent"),
                "arquivo_midia": c.get("arquivo_midia"),
            }
            if is_pov:
                log_event("BRAIN_DIRECTOR", f"Cena {cid:03d}: Validação facial desabilitada (POV)", level="info")
            cenas_analisadas.append(cena_dict)

        # 2. Detecção de Story Cards (Badges de Passo, Mito, Erro, Dados, Timeline)
        card_markers = detect_story_cards(cenas_analisadas)
        markers_by_id = {m["scene_id"]: m for m in card_markers}

        # Ancoragem fina nos word_timestamps
        for cena in cenas_analisadas:
            cid = cena["id"]
            marker = markers_by_id.get(cid)
            if marker:
                texto_cena = cena["texto"].lower()
                frag = str(marker.get("narration_fragment", "")).lower()
                anchor_s = None
                anchor_e = None

                # Busca palavra-âncora
                if flat_words:
                    for wt in flat_words:
                        w_str = str(wt.get("w", "")).lower()
                        s_val = wt.get("s")
                        e_val = wt.get("e")
                        if s_val is not None and w_str in frag and cena["start"] <= float(s_val) <= cena["end"] + 0.5:
                            anchor_s = float(s_val)
                            anchor_e = float(e_val) if e_val is not None else anchor_s + 0.5
                            break

                cena["is_story_card"] = True
                cena["story_card_type"] = marker.get("story_card_type")
                cena["story_card_style"] = marker.get("story_card_style")
                cena["story_card_config"] = marker.get("story_card_config", {})
                cena["story_card_anchor"] = {"s": anchor_s, "e": anchor_e} if anchor_s is not None else None
            else:
                cena["is_story_card"] = False
                cena["story_card_type"] = ""
                cena["story_card_style"] = ""
                cena["story_card_config"] = None
                cena["story_card_anchor"] = None

        # 3. Otimização de Ritmo (Story Rhythm Service)
        cenas_analisadas = otimizar_ritmo_cenas(cenas_analisadas, memoria_visual)

        # 4. Distribuição Global Avatar / B-Roll (Cota 8/92)
        rebalancear_narrativa(cenas_analisadas, projeto=self.projeto_id)

        return cenas_analisadas

    # =========================================================================
    # FASE 2: DECISÕES AUDIOVISUAIS E CAPCUT NATIVE MAPPING
    # =========================================================================

    def _fase_2_decisoes_audiovisuais(
        self,
        entrada: Dict[str, Any],
        cenas_analisadas: List[Dict[str, Any]],
        options: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Determina animações de câmera/sujeito, seleciona transições válidas
        no catálogo nativo do CapCut Desktop, processa corte de silêncios
        e prepara prompts visuais para Story Cards.
        """
        include_transicoes = options.get("include_transicoes", True)
        include_animacoes = options.get("include_animacoes", True)
        include_story_cards = options.get("include_story_cards", True)

        # 1. Catálogo de Transições do CapCut Desktop
        transicoes_disponiveis = self._capcut_service.obter_transicoes_disponiveis()
        transicao_padrao_id = "combinar"
        transicao_corte_id = "combinar"

        decisoes_cenas = []

        # 2. Processamento de Silêncios do Áudio para Timing de SFX
        audio_meta = entrada.get("audio_metadata") or {}
        audio_path = audio_meta.get("path", "")
        silencio_table = audio_meta.get("silencio_table") or []

        if not silencio_table and audio_path and Path(audio_path).exists():
            try:
                # Segmentos básicos para detecção
                segs = [
                    {"start": c["start"], "end": c["end"]}
                    for c in cenas_analisadas
                ]
                out_no_silence = str(self.project_dir / "audio_no_silence.mp3")
                _, silencio_table = cortar_silencio(segs, audio_path, out_no_silence)
            except Exception as e:
                log_event("BRAIN_DIRECTOR", f"Aviso ao analisar silêncios de áudio: {e}", level="warn")

        for idx, cena in enumerate(cenas_analisadas):
            # A) Decisão de Animação
            if include_animacoes:
                anim_info = direcionar_animacao_cena(cena, contexto_visual=None, index=idx)
            else:
                anim_info = {
                    "should_animate": False,
                    "animation_type": "static_freeze",
                    "motion_vector": "static",
                    "animation_priority": "none",
                    "animation_rationale": "Animações desabilitadas por opção do usuário."
                }

            # B) Decisão de Transição
            if include_transicoes:
                role = cena.get("narrative_role", "BROLL")
                tipo_trans = transicao_corte_id if role in ("HOOK", "CTA") else transicao_padrao_id
                dur_trans_ms = 400 if role in ("HOOK", "CTA") else 500
                trans_material = self._capcut_service.resolver_material_transicao(tipo_trans, duracao_ms=dur_trans_ms)
                trans_decision = {
                    "tipo": tipo_trans,
                    "duracao_ms": dur_trans_ms,
                    "capcut_effect_id": trans_material.get("effect_id", "") if trans_material else "",
                    "lado": "saida"
                }
            else:
                trans_decision = {
                    "tipo": "none",
                    "duracao_ms": 0,
                    "capcut_effect_id": "",
                    "lado": "saida"
                }

            # C) Prompt de Story Card
            story_card_prompt = ""
            if include_story_cards and cena.get("is_story_card") and cena.get("story_card_config"):
                marker_dummy = {
                    "story_card_type": cena.get("story_card_type"),
                    "story_card_style": cena.get("story_card_style"),
                    "story_card_config": cena.get("story_card_config")
                }
                story_card_prompt = build_story_card_prompt(marker_dummy)

            decisoes_cenas.append({
                "cena": cena,
                "animacao": anim_info,
                "transicao": trans_decision,
                "story_card_prompt": story_card_prompt
            })

        return {
            "decisoes": decisoes_cenas,
            "silencio_table": silencio_table,
            "transicoes_disponiveis_count": len(transicoes_disponiveis)
        }

    # =========================================================================
    # HEURÍSTICA DE KEYWORDS SMART FX / CLASSIFICAÇÃO DE MOOD
    # =========================================================================

    def _classificar_mood_cena(
        self,
        texto: str = "",
        prompt_imagem: str = "",
        prompt_animacao: str = ""
    ) -> Dict[str, Any]:
        """
        Classifica o humor e estilo audiovisual da cena via keywords
        (reimplementação em Python da heurística de static/app.js L7019-7066).
        Retorna dict com chaves: mood, lut, overlay_tipo, animacao_preset, sfx_arquivo, sfx_nome.
        """
        full_txt = f"{prompt_imagem or ''} {prompt_animacao or ''} {texto or ''}".lower()

        # Grupos de keywords canônicos
        palavras_natureza = [
            "jardim", "garden", "planta", "natureza", "árvore", "arvore",
            "campo", "sol", "verde", "tomate", "horta", "calmo"
        ]
        palavras_tech = [
            "tecnologia", "robô", "robo", "código", "codigo", "futuro",
            "cyber", "digital", "neon", "rede", "computador", "hacker"
        ]
        palavras_action = [
            "impacto", "forte", "urgente", "alerta", "choque", "ação",
            "acao", "dinheiro", "fogo"
        ]

        if any(w in full_txt for w in palavras_natureza):
            return {
                "mood": "🌿 Cena Natural & Orgânica • Iluminação Quente • Ritmo Suave",
                "lut": "golden_hour",
                "overlay_tipo": "vinheta",
                "animacao_preset": "loop_zoom_slow",
                "sfx_arquivo": "ambient_nature.mp3",
                "sfx_nome": "Natureza & Pássaros"
            }
        elif any(w in full_txt for w in palavras_tech):
            return {
                "mood": "👾 Estética Cyberpunk & Digital • Alto Contraste • Ritmo Dinâmico",
                "lut": "cyberpunk",
                "overlay_tipo": "vhs_retro",
                "animacao_preset": "in_scale_pop",
                "sfx_arquivo": "transition_whoosh.mp3",
                "sfx_nome": "Whoosh Transição"
            }
        elif any(w in full_txt for w in palavras_action):
            return {
                "mood": "💥 Cena de Alto Impacto • Ritmo Acelerado • Alta Atenção",
                "lut": "teal_orange",
                "overlay_tipo": "light_leak",
                "animacao_preset": "loop_shake",
                "sfx_arquivo": "transition_whoosh.mp3",
                "sfx_nome": "Whoosh Rápido"
            }
        else:
            return {
                "mood": "🎬 Visual Cinemático • Equilíbrio Narrativo • Foco na Clareza",
                "lut": "cinematic",
                "overlay_tipo": "vinheta",
                "animacao_preset": "in_zoom",
                "sfx_arquivo": "transition_whoosh.mp3",
                "sfx_nome": "Whoosh Transição"
            }

    # =========================================================================
    # FASE 3: ENRIQUECIMENTO ATÔMICO, PROMPTS E SMART FX
    # =========================================================================

    def _fase_3_enriquecimento_atomico(
        self,
        entrada: Dict[str, Any],
        decisoes: Dict[str, Any],
        memoria_visual: Dict[str, Any],
        context_pack: Dict[str, Any],
        options: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Gera prompts em inglês (com suporte a DeepSeek e fallback determinístico),
        configura trilhas de SFX e BGM, audita continuidade e monta o JSON atômico.
        """
        include_sfx = options.get("include_sfx", True)
        include_bgm = options.get("include_audio_bgm", True)
        smart_fx_modo = options.get("smart_fx_modo", "sugestoes")

        # Galerias declarativas (com fallback gracioso caso ausentes)
        smart_fx_cfg = self._carregar_galeria("smart_fx_rules.json") or {}
        smart_fx_regras = smart_fx_cfg.get("regras", {})

        sfx_cfg = self._carregar_galeria("sfx_ruidos.json") or {}
        sfx_catalogo = sfx_cfg.get("efeitos", {})

        bgm_cfg = self._carregar_galeria("audio_bgm.json") or {}
        bgm_presets = bgm_cfg.get("presets", {})

        lista_decisoes = decisoes.get("decisoes", [])
        cenas_para_prompts = [d["cena"] for d in lista_decisoes]

        # 1. Geração / Atualização de Prompts de Imagem e Animação
        cenas_sem_prompt = [c for c in cenas_para_prompts if not c.get("prompt_imagem")]
        if cenas_sem_prompt:
            try:
                res_lote = gerar_prompts_lote(
                    cenas_lote=cenas_sem_prompt,
                    context_pack=context_pack,
                    total_cenas=len(cenas_para_prompts)
                )
                if isinstance(res_lote, dict):
                    prompts_gerados = res_lote.get("scenes", [])
                elif isinstance(res_lote, list):
                    prompts_gerados = res_lote
                else:
                    prompts_gerados = []

                mapa_prompts = {p["id"]: p for p in prompts_gerados if isinstance(p, dict) and "id" in p}
                for c in cenas_para_prompts:
                    if c["id"] in mapa_prompts:
                        p_info = mapa_prompts[c["id"]]
                        c["prompt_imagem"] = p_info.get("prompt_imagem", c.get("prompt_imagem", ""))
                        if not c.get("prompt_animacao") and p_info.get("prompt_animacao"):
                            c["prompt_animacao"] = p_info.get("prompt_animacao")
            except Exception as e:
                log_event("BRAIN_DIRECTOR", f"Fallback de prompts locais: {e}", level="warn")
                # Fallback determinístico de alta qualidade em inglês
                for c in cenas_para_prompts:
                    if not c.get("prompt_imagem"):
                        char_ref = c.get("character_ref", "")
                        broll_q = c.get("broll_query", "cinematic garden scene")
                        if c.get("uses_character") and char_ref:
                            c["prompt_imagem"] = (
                                f"Photorealistic documentary third-person demonstration shot of {char_ref} in {context_pack.get('world', 'rustic garden')}, "
                                f"hands in foreground, no face-to-camera dialogue, "
                                f"{context_pack.get('lighting', 'natural daylight')}, 35mm lens, shallow depth of field, sharp focus, authentic expression, 8k resolution, highly detailed."
                            )
                        else:
                            c["prompt_imagem"] = (
                                f"Photorealistic cinematic macro b-roll shot of {broll_q}, "
                                f"{context_pack.get('lighting', 'soft natural lighting')}, shallow depth of field, 8k resolution, highly detailed, no human presence."
                            )

        # Garante prompt de animação para cenas com should_animate
        for d in lista_decisoes:
            cena = d["cena"]
            anim = d["animacao"]
            if anim.get("should_animate") and not cena.get("prompt_animacao"):
                m_vector = anim.get("motion_vector", "slow_dolly_push")
                if m_vector == "slow_dolly_push":
                    cena["prompt_animacao"] = "Smooth cinematic slow push-in forward, natural subtle breathing motion, 4s fluid ease, high visual stability."
                elif m_vector == "subtle_handheld_orbit":
                    cena["prompt_animacao"] = "Subtle cinematic handheld camera drift, fluid gentle pan, 4s smooth ease."
                else:
                    cena["prompt_animacao"] = "Slow cinematic camera glide reveal, natural atmospheric movement, 4s fluid ease."

        # CORREÇÃO ANIMAR: garante prompt_animacao preenchido para TODAS as cenas
        # (especialmente B-Roll). Nunca deixa nenhuma cena com prompt_animacao vazio,
        # para que o modo Animar / Image-to-Video no Flow sempre tenha instrução de
        # movimento coerente com a narração da cena.
        from services.deepseek_prompt_service import _montar_prompt_animacao_fallback
        for d in lista_decisoes:
            cena = d["cena"]
            if not cena.get("prompt_animacao"):
                narracao = cena.get("texto") or cena.get("narration") or ""
                cena["prompt_animacao"] = _montar_prompt_animacao_fallback(narracao)

        # REGRA SISTEMÁTICA DE AVATAR: Injeção de base obrigatória third-person hands-on
        for d in lista_decisoes:
            cena = d["cena"]
            if _is_avatar_scene(cena):
                if str(cena.get("avatar_style", "")).lower() in ("first_person_pov", "pov", "broll_pov"):
                    continue
                char_ref = cena.get("character_ref") or cena.get("nome_personagem") or "@Marcos"
                p_img, p_anim = _aplicar_estilo_avatar_base(
                    prompt_imagem=cena.get("prompt_imagem", ""),
                    prompt_animacao=cena.get("prompt_animacao", ""),
                    character_ref=char_ref
                )
                cena["prompt_imagem"] = p_img
                cena["visual_prompt"] = p_img
                cena["prompt_animacao"] = p_anim
                cena["avatar_style"] = "third_person_hands_on"

        # 2. Construção do Mapa Atômico por Cena
        cenas_output = {}
        total_sfx = 0
        total_cards = 0
        total_transicoes = 0

        for d in lista_decisoes:
            cena = d["cena"]
            cid = cena["id"]
            anim = d["animacao"]
            trans = d["transicao"]
            card_prompt = d["story_card_prompt"]

            start_s = float(cena["start"])
            end_s = float(cena["end"])
            dur_s = float(cena.get("duracao", max(0.0, end_s - start_s)))
            start_ms = int(round(start_s * 1000))
            end_ms = int(round(end_s * 1000))

            # Auditoria de Continuidade
            audit = verificar_continuidade_cena(
                cena=cena,
                memoria_visual=memoria_visual,
                contexto_visual=context_pack,
                index=cid - 1
            )

            # Story Card
            is_card = bool(cena.get("is_story_card"))
            story_card_dict = None
            if is_card:
                total_cards += 1
                cfg = cena.get("story_card_config") or {}
                anchor = cena.get("story_card_anchor")
                timing_card_ms = int(round(anchor["s"] * 1000)) if anchor else start_ms
                story_card_dict = {
                    "ativo": True,
                    "tipo": cena.get("story_card_type", "step_1"),
                    "estilo": cena.get("story_card_style", "step_intro"),
                    "timing_start_ms": timing_card_ms,
                    "duracao_ms": int(cfg.get("duration_ms", 3000)),
                    "anchor_word": anchor.get("word") if anchor else None,
                    "titulo": cfg.get("title", ""),
                    "subtitulo": cfg.get("subtitle", ""),
                    "prompt_card_visual": card_prompt
                }

            # Transição
            if trans.get("tipo") != "none":
                total_transicoes += 1

            # SFX
            sfx_list = []
            if include_sfx:
                # Swoosh na transição
                if trans.get("tipo") != "none":
                    swoosh_item = sfx_catalogo.get("swoosh_transicao", {})
                    sfx_list.append({
                        "som": swoosh_item.get("id", "swoosh_transicao"),
                        "volume": swoosh_item.get("volume_default", 0.65),
                        "timing_start_ms": max(0, end_ms - int(trans.get("duracao_ms", 500) // 2))
                    })
                    total_sfx += 1
                # Pop no Story Card
                if is_card:
                    pop_item = sfx_catalogo.get("card_pop_accent", {})
                    sfx_list.append({
                        "som": pop_item.get("id", "card_pop_accent"),
                        "volume": pop_item.get("volume_default", 0.75),
                        "timing_start_ms": story_card_dict["timing_start_ms"] if story_card_dict else start_ms
                    })
                    total_sfx += 1

            # BGM
            bgm_dict = None
            if include_bgm:
                bgm_preset = bgm_presets.get("cinematic_ambient_garden_v2") or {}
                bgm_dict = {
                    "faixa": bgm_preset.get("faixa", "cinematic_ambient_garden_v2"),
                    "volume": bgm_preset.get("volume", 0.22),
                    "fade_in_ms": bgm_preset.get("fade_in_ms", 600),
                    "fade_out_ms": bgm_preset.get("fade_out_ms", 600)
                }

            # Smart FX & Classificação de Humor
            mood_info = self._classificar_mood_cena(
                texto=cena.get("texto") or cena.get("narration") or "",
                prompt_imagem=cena.get("prompt_imagem") or "",
                prompt_animacao=cena.get("prompt_animacao") or ""
            )
            smart_fx = {"sugestoes_ia": [], "reasoning": "", "mood": mood_info["mood"]}
            if smart_fx_modo != "desabilitar":
                role = cena.get("narrative_role", "BROLL")
                if is_card and "STORY_CARD" in smart_fx_regras:
                    rule = smart_fx_regras["STORY_CARD"]
                    smart_fx["sugestoes_ia"] = list(rule.get("sugestoes_ia", ["subtle_card_shadow", "glow_badge"]))
                    smart_fx["reasoning"] = rule.get("reasoning", "Ponto pedagógico: realce no card estruturado.")
                elif role in smart_fx_regras:
                    rule = smart_fx_regras[role]
                    smart_fx["sugestoes_ia"] = list(rule.get("sugestoes_ia", []))
                    smart_fx["reasoning"] = rule.get("reasoning", "")
                elif role == "HOOK":
                    smart_fx["sugestoes_ia"] = ["lens_flare_subtle", "dramatic_opening_punch"]
                    smart_fx["reasoning"] = "Abertura do vídeo: destaque para gancho visual de alta retenção."
                elif role == "CTA":
                    smart_fx["sugestoes_ia"] = ["vignette_focus", "highlight_pulse"]
                    smart_fx["reasoning"] = "Chamada para ação: foco no apresentador e retenção final."
                elif is_card:
                    smart_fx["sugestoes_ia"] = ["subtle_card_shadow", "glow_badge"]
                    smart_fx["reasoning"] = "Ponto pedagógico: realce no card estruturado."

            cenas_output[f"cena_{cid}"] = {
                "scene_id": cid,
                "timing": {
                    "start_s": start_s,
                    "end_s": end_s,
                    "duracao_s": dur_s,
                    "start_ms": start_ms,
                    "end_ms": end_ms
                },
                "narrativa": {
                    "role": cena.get("narrative_role", "BROLL"),
                    "scene_type": cena.get("scene_type", ""),
                    "uses_character": cena.get("uses_character", False),
                    "character_ref": cena.get("character_ref", ""),
                    "intensity": cena.get("intensity", 0.5),
                    "emotion": cena.get("emotion", "neutral"),
                    "manual_override": bool(cena.get("manual_override", False)),
                    "status_reset": bool(cena.get("status_reset", False)),
                    "avatar_style": cena.get("avatar_style", ""),
                    "skip_facial_validation": bool(cena.get("skip_facial_validation", False)),
                    "validation_override_reason": str(cena.get("validation_override_reason") or "")
                },
                "prompts": {
                    "prompt_imagem": cena.get("prompt_imagem", ""),
                    "prompt_animacao": cena.get("prompt_animacao", ""),
                    "negative_prompt": ", ".join(context_pack.get("negative_constraints", []))
                },
                "transicao": trans,
                "story_card": story_card_dict,
                "audio": {
                    "bgm": bgm_dict
                },
                "animacao": anim,
                "sfx_ruidos": sfx_list,
                "smart_fx": smart_fx,
                "efeitos_visuais": {
                    "lut": mood_info["lut"],
                    "saturacao": 105,
                    "brilho": 100,
                    "contraste": 105,
                    "blur": 0,
                    "aberracao_cromatica": False,
                    "aberracao_intensidade": 4
                },
                "efeitos_cena": {
                    "overlay_tipo": mood_info["overlay_tipo"],
                    "intensidade": 65,
                    "posicao": "bordas",
                    "blend_mode": "screen",
                    "granulacao": 30
                },
                "animacao_movimento": {
                    "preset": mood_info["animacao_preset"],
                    "duracao_ms": 600,
                    "delay_ms": 0,
                    "easing": "ease-in-out"
                },
                "sfx": {
                    "arquivo": mood_info["sfx_arquivo"],
                    "nome": mood_info.get("sfx_nome", mood_info["sfx_arquivo"]),
                    "volume": 70,
                    "pan": 0.0
                },
                "auditoria_continuidade": {
                    "score": audit.get("continuity_score", 100),
                    "warnings": audit.get("warnings", [])
                }
            }

        return {
            "projeto_id": self.projeto_id,
            "diretor_versao": "3.0.0",
            "grade_producao": "A+",
            "metricas": {
                "total_cenas": len(cenas_output),
                "total_transicoes": total_transicoes,
                "total_story_cards": total_cards,
                "total_sfx": total_sfx,
                "duracao_total_s": sum(c["timing"]["duracao_s"] for c in cenas_output.values()),
                "duracao_total_ms": sum(c["timing"]["duracao_s"] * 1000 for c in cenas_output.values())
            },
            "cenas": cenas_output
        }

    # =========================================================================
    # FASE 4: COMPILAÇÃO E PERSISTÊNCIA ATÔMICA
    # =========================================================================

    def _fase_4_compilacao_persistencia(
        self,
        entrada: Dict[str, Any],
        saida_enriquecida: Dict[str, Any]
    ) -> bool:
        """
        Sincroniza o output do Brain com a estrutura canônica de lira_scene_plan.json
        e salva brain_director_output.json com garantia de escrita atômica.
        """
        cenas_dict = saida_enriquecida.get("cenas", {})
        if not cenas_dict:
            return False

        # 1. Carrega ou inicializa o Scene Plan oficial
        plan = carregar_scene_plan(self.projeto_id)
        if not plan:
            plan = {
                "projeto": self.projeto_id,
                "versao": 2,
                "narrativa_versao": NARRATIVA_VERSAO,
                "cenas": []
            }

        cenas_existentes = {c.get("id"): c for c in plan.get("cenas", [])}
        novas_cenas_plan = []

        for chave_cena, dados_cena in cenas_dict.items():
            cid = dados_cena["scene_id"]
            timing = dados_cena["timing"]
            narr = dados_cena["narrativa"]
            prompts = dados_cena["prompts"]
            trans = dados_cena["transicao"]
            sc = dados_cena["story_card"]
            anim = dados_cena["animacao"]

            c_plan = cenas_existentes.get(cid)
            if not c_plan:
                c_plan = _nova_cena(
                    cid=cid,
                    tempo_inicio=timing["start_s"],
                    tempo_fim=timing["end_s"],
                    texto=entrada.get("cenas", [{}])[cid - 1].get("texto", "") if cid - 1 < len(entrada.get("cenas", [])) else "",
                    tipo="video" if anim.get("should_animate") else "image",
                    animar=anim.get("should_animate", False),
                    nome_personagem=narr.get("character_ref", "").replace("@", "")
                )

            # Atualiza campos gerados pelo Brain
            c_plan["narrative_role"] = narr["role"]
            c_plan["uses_character"] = narr["uses_character"]
            c_plan["character_ref"] = narr["character_ref"]
            c_plan["prompt_imagem"] = prompts["prompt_imagem"]
            c_plan["visual_prompt"] = prompts["prompt_imagem"]
            c_plan["prompt_animacao"] = prompts["prompt_animacao"]
            if narr.get("avatar_style"):
                c_plan["avatar_style"] = narr["avatar_style"]
            elif dados_cena.get("avatar_style"):
                c_plan["avatar_style"] = dados_cena["avatar_style"]
            if narr.get("manual_override") or c_plan.get("manual_override"):
                c_plan["manual_override"] = True
                c_plan["uses_character"] = bool(narr.get("uses_character", False))
                c_plan["avatar_required"] = bool(narr.get("uses_character", False))
                if narr.get("scene_type"):
                    c_plan["scene_type"] = narr["scene_type"]
                if narr.get("character_ref"):
                    c_plan["nome_personagem"] = narr["character_ref"].replace("@", "")
                if narr.get("status_reset"):
                    c_plan["status"] = "PENDENTE"
                    c_plan["image_status"] = "PENDING"
                    c_plan["video_status"] = "PENDING"
                    c_plan["arquivo_midia"] = ""
                    c_plan["filename"] = ""
                    c_plan["imagem_com_personagem"] = False
            # Preserva intenção de vídeo / animação prévia se já configurada
            c_plan["animar"] = bool(anim.get("should_animate", False) or c_plan.get("animar", False))
            c_plan["animate_later"] = bool(anim.get("should_animate", False) or c_plan.get("animate_later", False) or c_plan.get("animar_depois", False))
            c_plan["media_intent"] = "video" if (c_plan["animar"] or c_plan["animate_later"] or str(c_plan.get("tipo", "")).lower() == "video") else "image"
            c_plan["animation_type"] = anim.get("animation_type", "static_freeze")
            c_plan["motion_vector"] = anim.get("motion_vector", "static")
            if narr.get("skip_facial_validation") or c_plan.get("skip_facial_validation"):
                c_plan["skip_facial_validation"] = True
                c_plan["validation_override_reason"] = narr.get("validation_override_reason") or c_plan.get("validation_override_reason") or "POV first-person has no visible face by design"

            # Transição
            if trans.get("tipo") != "none":
                c_plan["transicao_saida"] = {
                    "tipo": trans["tipo"],
                    "duracao_ms": trans["duracao_ms"]
                }

            # Story Card
            if sc and sc.get("ativo"):
                c_plan["is_story_card"] = True
                c_plan["story_card_type"] = sc["tipo"]
                c_plan["story_card_style"] = sc["estilo"]
                c_plan["story_card_prompt_imagem"] = sc.get("prompt_card_visual", "")
                c_plan["story_card_config"] = {
                    "title": sc["titulo"],
                    "subtitle": sc["subtitulo"],
                    "duration_ms": sc["duracao_ms"]
                }
                c_plan["editorial_style"] = {
                    "tier": 3,
                    "variant": sc["tipo"],
                    "badge": sc["titulo"],
                    "title": sc["subtitulo"],
                    "source": "brain_director",
                    "duration_s": sc["duracao_ms"] / 1000.0
                }

            # Efeitos Visuais, Efeitos de Cena, Animação de Movimento e SFX (Montagem 5.0)
            c_plan["efeitos_visuais"] = dados_cena.get("efeitos_visuais") or {
                "lut": "cinematic",
                "saturacao": 105,
                "brilho": 100,
                "contraste": 105,
                "blur": 0,
                "aberracao_cromatica": False,
                "aberracao_intensidade": 4
            }
            c_plan["efeitos_cena"] = dados_cena.get("efeitos_cena") or {
                "overlay_tipo": "vinheta",
                "intensidade": 65,
                "posicao": "bordas",
                "blend_mode": "screen",
                "granulacao": 30
            }
            c_plan["animacao_movimento"] = dados_cena.get("animacao_movimento") or {
                "preset": "in_zoom",
                "duracao_ms": 600,
                "delay_ms": 0,
                "easing": "ease-in-out"
            }
            c_plan["sfx"] = dados_cena.get("sfx") or {
                "arquivo": "transition_whoosh.mp3",
                "nome": "Whoosh Transição",
                "volume": 70,
                "pan": 0.0
            }

            novas_cenas_plan.append(c_plan)

        plan["cenas"] = novas_cenas_plan
        plan["narrativa_versao"] = NARRATIVA_VERSAO
        plan["brain_director_synced_em"] = time.strftime("%Y-%m-%d %H:%M:%S")

        # Persistência thread-safe do Scene Plan oficial
        sucesso_plan = salvar_scene_plan(self.projeto_id, plan)

        # Salva o arquivo JSON com a saída rica do Brain para consumo por downloads e CapCut
        try:
            out_file = self.project_dir / BRAIN_OUTPUT_FILE
            out_file.write_text(json.dumps(saida_enriquecida, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            log_event("BRAIN_DIRECTOR", f"Aviso ao salvar {BRAIN_OUTPUT_FILE}: {e}", level="warn")

        return sucesso_plan
