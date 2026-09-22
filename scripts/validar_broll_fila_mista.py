# -*- coding: utf-8 -*-
"""
Validação da fila de cenas mistas (B-roll vs Personagem Real)
Demonstra os logs gerados pelo PlaywrightCDPWorker:
- SCENE_SKIPPED_NO_CHARACTER para todas as B-roll (independente de @Marcos no prompt ou character_ref)
- ATTACHING_CHARACTER_ENTITY exclusivamente para cenas com uses_character: True real
"""
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import services.character_service as character_svc
from services.playwright_flow import PlaywrightCDPWorker

PROJETO = "WHY YOUR TOMATO PLANTS WONT PRODUCE - AND ITS NOT WHAT YOU THINK"

cenas_teste = [
    {
        "id": 1,
        "narrative_role": "BROLL",
        "avatar_required": False,
        "uses_character": False,
        "character_ref": "@Marcos",
        "scene_type": "broll_pov",
        "prompt_imagem": "First-person POV hands examining 12 tomato plants @Marcos",
        "texto": "I grew 12 tomato plants last summer..."
    },
    {
        "id": 2,
        "narrative_role": "BROLL",
        "avatar_required": False,
        "uses_character": False,
        "character_ref": "",
        "scene_type": "broll_macro",
        "prompt_imagem": "Extreme close-up macro of rich dark garden soil texture with @marcos organic guide",
        "texto": "Barely any buds on the rose plant."
    },
    {
        "id": 3,
        "narrative_role": "BROLL",
        "avatar_required": False,
        "uses_character": False,
        "character_ref": "@Marcos",
        "scene_type": "broll_action",
        "prompt_imagem": "Watering seedlings in rustic garden bed",
        "texto": "Watering every single day."
    },
    {
        "id": 4,
        "scene_type": "cinematic",
        "uses_character": False,
        "character_ref": "@Marcos",
        "prompt_imagem": "Landscape view of greenhouse @Marcos template",
        "texto": "Inside the greenhouse."
    },
    {
        "id": 5,
        "narrative_role": "CTA",
        "avatar_required": True,
        "uses_character": True,
        "character_ref": "@Marcos",
        "scene_type": "avatar_talking",
        "prompt_imagem": "@Marcos speaking directly to the camera with passionate expression",
        "texto": "If you want healthy tomatoes, subscribe now!"
    },
    {
        "id": 6,
        "narrative_role": "ACTION",
        "avatar_required": True,
        "uses_character": True,
        "character_ref": "@Marcos",
        "scene_type": "avatar_action",
        "prompt_imagem": "@Marcos demonstrating pruning technique with garden shears",
        "texto": "Watch how I prune this branch."
    }
]

def rodar_validacao():
    w = PlaywrightCDPWorker.__new__(PlaywrightCDPWorker)
    w.port = 9222
    w.page = MagicMock()
    w.current_model = "Veo 3.1 - Lite"
    w.current_flow_mode = "image"
    w.creditos_atuais = 50
    w.is_fallback_active = False
    w.get_exact_balance = MagicMock(return_value=50)
    w._garantir_aba_flow_aberta = MagicMock(return_value=True)
    w._ensure_project_open = MagicMock(return_value=True)
    w._disable_agent_mode = MagicMock()
    w._fechar_modais_bloqueantes = MagicMock()
    w._limpar_overlays_e_backdrops = MagicMock()
    w._set_output_mode = MagicMock(return_value=True)
    w._get_existing_media_snapshot = MagicMock(return_value=(set(), 0))
    w._clean_prompt_text = MagicMock(side_effect=lambda p, **kw: p)
    w._animar_imagem_galeria_flow = MagicMock(return_value=False)
    w._selecionar_referencia_flow = MagicMock(return_value=True)
    w._tratar_indicador_limite = MagicMock(return_value="ok")

    mock_editor = MagicMock()
    mock_editor.is_visible.return_value = True
    w.page.locator.return_value.first = mock_editor

    print("=" * 80)
    print("EXECUÇÃO DA FILA DE TESTE COM CENAS MISTAS (PlaywrightCDPWorker)")
    print("=" * 80)

    logs_capturados = []

    with patch("services.scene_plan_service.atualizar_status_cena"), \
         patch("services.scene_plan_service.atualizar_cena"), \
         patch("services.playwright_flow.check_flow_credits", return_value={"has_credits": True}), \
         patch("services.playwright_flow.pw_log") as mock_pw_log:

        def registrar_pw_log(msg, level="info"):
            logs_capturados.append(f"[PW_LOG] [{level.upper()}] {msg}")
            if "PROMPT_SENT_OK" in msg:
                w.stop_requested.set()

        mock_pw_log.side_effect = registrar_pw_log

        for cena in cenas_teste:
            w.stop_requested = threading.Event()
            cid = cena["id"]
            role = cena.get("narrative_role", "N/A")
            req = cena.get("avatar_required", "N/A")
            u_char = cena.get("uses_character", "N/A")
            c_ref = cena.get("character_ref", "")
            p_img = cena.get("prompt_imagem", "")[:50] + "..."

            print(f"\n--- Fila processando Cena {cid:03d} (role={role}, avatar_req={req}, uses_char={u_char}, char_ref={c_ref}) ---")
            print(f"    Prompt: '{p_img}'")

            w._processar_cena_individual(PROJETO, cena, is_anim=False)

    print("\n" + "=" * 80)
    print("RESUMO DOS EVENTOS DE PERSONAGEM REGISTRADOS NA FILA:")
    print("=" * 80)
    for log in logs_capturados:
        if "SCENE_SKIPPED_NO_CHARACTER" in log or "ATTACHING_CHARACTER_ENTITY" in log:
            print(log)

if __name__ == "__main__":
    rodar_validacao()
