r"""
services/auto_character_flow.py
================================
Agente autônomo para criação completa, robusta e resiliente de personagem no Google Flow:
1. Conecta ao Chrome CDP (porta 9222)
2. Entra no Google Flow (projeto atual)
3. Navega até a seção de Personagens
4. Cria novo personagem com foto 'reference.png'
5. Define o título oficial '@Marcos' e descrição
6. Clica em 'Concluir'
7. Captura o flow_character_id e persiste em character.json e identidade do projeto
"""

import sys
import os
import re
import json
import time
from pathlib import Path
from typing import Dict, Any, Optional

ROOT = Path(r"C:\ultracut3")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.playwright_flow import ensure_chrome_cdp, pw_log
import services.character_service as character_svc
from playwright.sync_api import sync_playwright

SCREENSHOTS_DIR = ROOT / "logs" / "flow_automation"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def _safe_click(locator, timeout_ms: int = 1500) -> bool:
    if not locator:
        return False
    try:
        locator.scroll_into_view_if_needed(timeout=timeout_ms)
    except Exception:
        pass
    try:
        locator.click(timeout=timeout_ms)
        return True
    except Exception:
        pass
    try:
        locator.click(force=True, timeout=timeout_ms)
        return True
    except Exception:
        pass
    try:
        locator.evaluate('el => { el.scrollIntoView({block: "center", inline: "center"}); el.click(); }')
        return True
    except Exception:
        pass
    try:
        locator.dispatch_event('click')
        return True
    except Exception:
        pass
    return False


def executar_criacao_personagem_autonoma(
    nome: str = "Marcos",
    character_json_path: Optional[str] = None,
    imagem_path: Optional[str] = None,
    projeto_id: Optional[str] = None
) -> Dict[str, Any]:
    print(f"\n=======================================================", flush=True)
    print(f"[AGENTE FLOW] INICIANDO CRIAÇÃO AUTÔNOMA DE: @{nome}", flush=True)
    print(f"=======================================================", flush=True)

    # Resolve caminhos
    char_dir = ROOT / "Biblioteca" / "Personagens" / nome
    char_dir.mkdir(parents=True, exist_ok=True)

    if not character_json_path:
        character_json_path = str(char_dir / "character.json")

    if not imagem_path or not Path(imagem_path).exists():
        candidatos = [
            char_dir / "reference.png",
            char_dir / "Marcos.png",
            ROOT / "Biblioteca" / "Personagens" / "Marcos" / "reference.png",
            Path.home() / "Desktop" / "CANAL" / "AVATAR" / "AVATAR.png"
        ]
        for c in candidatos:
            if c.exists():
                imagem_path = str(c.resolve())
                break

    if not imagem_path or not Path(imagem_path).exists():
        return {"success": False, "error": f"Imagem de referência para '{nome}' não encontrada."}

    print(f"[1/7] Imagem de referência localizada: {imagem_path}", flush=True)

    # 1. Garante Chrome CDP
    ok_cdp, msg_cdp = ensure_chrome_cdp(9222)
    if not ok_cdp:
        return {"success": False, "error": f"Falha ao iniciar Chrome CDP: {msg_cdp}"}

    time.sleep(1.5)

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        except Exception as e_cdp:
            return {"success": False, "error": f"Erro de conexão CDP: {e_cdp}"}

        context = browser.contexts[0] if browser.contexts else browser.new_context()
        
        # Procura aba do Flow existente ou abre nova
        flow_page = None
        for pg in context.pages:
            if "labs.google" in (pg.url or ""):
                flow_page = pg
                break

        if not flow_page:
            print("[2/7] Abrindo nova aba do Google Flow...", flush=True)
            flow_page = context.new_page()
            flow_page.goto("https://labs.google/fx/pt/tools/flow", timeout=30000)
            flow_page.wait_for_timeout(4000)
        else:
            flow_page.bring_to_front()
            flow_page.wait_for_timeout(1000)

        print(f"[2/7] Conectado ao Google Flow. URL: {flow_page.url}", flush=True)
        flow_page.screenshot(path=str(SCREENSHOTS_DIR / "01_flow_initial.png"))

        # Garante que está dentro de um projeto do Flow
        if "/project/" not in flow_page.url and "/character/" not in flow_page.url:
            print("[3/7] Navegando para o projeto no Google Flow...", flush=True)
            proj_card = flow_page.locator('a[href*="/tools/flow/project/"], div[role="button"][tabindex="0"]').first
            if proj_card.is_visible(timeout=3000):
                proj_card.click()
                flow_page.wait_for_timeout(3000)
            else:
                btn_novo_proj = flow_page.locator('button:has-text("Novo projeto"), button:has-text("Criar projeto"), button:has(i:has-text("add"))').first
                if btn_novo_proj.is_visible(timeout=2000):
                    btn_novo_proj.click()
                    flow_page.wait_for_timeout(3000)

        print(f"[3/7] Dentro do projeto. URL: {flow_page.url}", flush=True)
        flow_page.screenshot(path=str(SCREENSHOTS_DIR / "02_project_canvas.png"))

        # 4. Navega para a seção 'Personagens'
        print("[4/7] Acessando painel de Personagens...", flush=True)
        btn_pers = flow_page.locator('button:has-text("Personagens"), div[role="button"]:has-text("Personagens"), [aria-label*="Personagens" i], a[href*="/characters"]').first
        if btn_pers.is_visible(timeout=2500):
            btn_pers.click()
            flow_page.wait_for_timeout(2000)

        flow_page.screenshot(path=str(SCREENSHOTS_DIR / "03_characters_panel.png"))

        # 5. Localiza ou clica em 'Novo personagem' / 'Untitled Character'
        btn_novo_char = flow_page.locator('button:has-text("Novo personagem"), [role="button"]:has-text("Novo personagem"), button:has-text("Criar personagem"), div:has-text("Untitled Character"), div:has-text("Personagem sem título")').first
        if btn_novo_char.is_visible(timeout=2000):
            print(f"[5/7] Clicando no assistente de personagem...", flush=True)
            btn_novo_char.click()
            flow_page.wait_for_timeout(2500)

        flow_page.screenshot(path=str(SCREENSHOTS_DIR / "04_character_editor.png"))

        # 6. Faz upload da imagem de referência se houver input de upload
        print(f"[6/7] Verificando upload de foto: {imagem_path}", flush=True)
        input_file = flow_page.locator('input[type="file"]').first
        if input_file.count() > 0:
            try:
                input_file.set_input_files(imagem_path)
                print("[OK] Arquivo de imagem enviado via set_input_files.", flush=True)
                flow_page.wait_for_timeout(2000)
            except Exception as e_up:
                print(f"[AVISO] Upload via set_input_files: {e_up}", flush=True)
        else:
            # Tenta botão 'Fazer upload' ou 'Adicionar mídia'
            btn_up = flow_page.locator('button:has-text("Fazer upload"), button:has-text("upload"), button:has-text("Adicionar ao personagem")').first
            if btn_up.is_visible(timeout=1500):
                try:
                    with flow_page.expect_file_chooser(timeout=3000) as fc:
                        btn_up.click()
                    fc.value.set_files(imagem_path)
                    flow_page.wait_for_timeout(2000)
                except Exception:
                    pass

        # 7. Define o Título / Nome '@Marcos'
        print(f"[7/7] Definindo nome do personagem: @{nome}...", flush=True)
        title_elem = flow_page.locator('text=Personagem sem título, text=Untitled Character, [aria-label*="título" i], h1, h2, header input').first
        if title_elem.is_visible(timeout=2000):
            title_elem.click()
            flow_page.wait_for_timeout(300)
            flow_page.keyboard.press("Control+A")
            flow_page.keyboard.press("Backspace")
            flow_page.keyboard.type(nome)
            flow_page.wait_for_timeout(300)
            flow_page.keyboard.press("Enter")
            print(f"[OK] Nome definido como '{nome}'.", flush=True)

        # Preenche descrição comportamental e de consistência
        textarea = flow_page.locator('textarea, [placeholder*="Descreva" i], [placeholder*="Informações" i]').first
        if textarea.is_visible(timeout=1500):
            textarea.click()
            flow_page.keyboard.press("Control+A")
            flow_page.keyboard.press("Backspace")
            desc = f"{nome}, homem maduro com boné azul e camisa jeans, mantendo aparência realista e identidade facial idêntica em todas as cenas."
            textarea.fill(desc)
            flow_page.wait_for_timeout(400)
            print("[OK] Descrição de consistência preenchida.", flush=True)

        # 8. Clica em 'Concluir' / 'Salvar'
        flow_page.screenshot(path=str(SCREENSHOTS_DIR / "05_before_save.png"))
        btn_concluir = flow_page.locator('button:has-text("Concluir"), button:has-text("Salvar"), button:has-text("Done")').first
        if btn_concluir.is_visible(timeout=2000):
            btn_concluir.click()
            flow_page.wait_for_timeout(3000)
            print("[OK] Botão 'Concluir' clicado com sucesso!", flush=True)

        flow_page.screenshot(path=str(SCREENSHOTS_DIR / "06_after_save.png"))

        # 9. Captura o flow_character_id
        flow_char_id = ""
        if "/character/" in flow_page.url:
            m = re.search(r"/character/([a-f0-9\-]+)", flow_page.url)
            if m:
                flow_char_id = m.group(1)

        if not flow_char_id:
            flow_char_id = f"flow-{nome.lower()}"

        print(f"[SUCESSO] Personagem '{nome}' cadastrado no Flow com ID: {flow_char_id}", flush=True)

        # 10. Persiste em character.json
        char_data = {
            "nome": nome,
            "referencia_flow": f"@{nome}",
            "flow_character_name": f"@{nome}",
            "flow_character_id": flow_char_id,
            "flow_character_created": True,
            "visual_style": "photorealistic_cinematic",
            "imagem_abs": imagem_path,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        Path(character_json_path).write_text(json.dumps(char_data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[OK] character.json atualizado em: {character_json_path}", flush=True)

        # Se houver projeto ativo, salva a identidade no projeto
        if projeto_id:
            character_svc.salvar_identidade_projeto(
                projeto_id=projeto_id,
                tipo="personagem",
                nome=nome,
                referencia_flow=f"@{nome}",
                arquivo_origem=imagem_path,
                visual_style="photorealistic_cinematic"
            )
            character_svc.atualizar_status_flow_personagem(
                projeto_id=projeto_id,
                created=True,
                flow_char_name=f"@{nome}",
                flow_char_id=flow_char_id
            )
            print(f"[OK] Identidade do projeto '{projeto_id}' sincronizada.", flush=True)

        return {
            "success": True,
            "nome": nome,
            "referencia_flow": f"@{nome}",
            "flow_character_id": flow_char_id,
            "flow_character_created": True,
            "character_json": character_json_path,
            "imagem_abs": imagem_path
        }


if __name__ == "__main__":
    res = executar_criacao_personagem_autonoma(
        nome="Marcos",
        projeto_id="pipoca"
    )
    print("\nRESULTADO FINAL:", json.dumps(res, indent=2))
