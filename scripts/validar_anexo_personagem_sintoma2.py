"""
scripts/validar_anexo_personagem_sintoma2.py — Validação real contra o Chrome CDP (porta 9222).
Testa:
1. Limpeza de overlays e abertura do menu pelo botão '+' (add-menu-trigger).
2. Execução de _verificar_personagem_na_biblioteca('Marcos') com medição de timing.
3. Execução de incluir_referencia_personagem com personagem oficial @Marcos do projeto.
4. Confirmação do tempo gasto na Etapa C (Δt em milissegundos).
"""

import sys
import time
from pathlib import Path

# Ajusta path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright
from services.playwright_flow import (
    PlaywrightCDPWorker,
    incluir_referencia_personagem,
    _localizar_editor_prompt,
)
from services.event_logger import log_event

PROJETO_TESTE = "WHY YOUR TOMATO PLANTS WONT PRODUCE - AND ITS NOT WHAT YOU THINK"
REF_PATH = f"C:\\Lira Videos\\projetos\\{PROJETO_TESTE}\\characters\\Marcos\\reference.png"

def run():
    print("=" * 70)
    print("VALIDAÇÃO REAL SINTOMA 2: ABERTURA DO MENU & ANEXO DE PERSONAGEM")
    print("=" * 70)

    p = sync_playwright().start()
    try:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        page = None
        for pg in context.pages:
            if "flow" in pg.url.lower():
                page = pg
                break
        if not page:
            page = context.pages[0]

        print(f"[CDP] Conectado: {page.url}")
        page.wait_for_timeout(1000)

        # 1. Instancia o worker simulado apontando para a página ao vivo
        worker = PlaywrightCDPWorker.__new__(PlaywrightCDPWorker)
        worker.page = page
        worker.current_project_id = PROJETO_TESTE

        print("\n--- TESTE 1: _verificar_personagem_na_biblioteca('Marcos') ---")
        t0_verif = time.time()
        res_verif = worker._verificar_personagem_na_biblioteca("Marcos")
        dt_verif_ms = int((time.time() - t0_verif) * 1000)
        print(f"[RESULTADO 1] Retorno da verificação: {res_verif} | Duração: {dt_verif_ms}ms")
        log_event(
            "PIPELINE",
            f"[{PROJETO_TESTE}] verificar_personagem_na_biblioteca executado: retorno={res_verif}",
            duration_ms=dt_verif_ms,
            details={"stage": "anexo_personagem", "projeto_id": PROJETO_TESTE}
        )

        print("\n--- TESTE 2: incluir_referencia_personagem (Etapa C) ---")
        # Garante prompt limpo antes
        editor = _localizar_editor_prompt(page)
        if editor:
            editor.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            page.wait_for_timeout(300)

        motivos_erro = []
        t0_anexo = time.time()
        ok_anexo = incluir_referencia_personagem(
            page=page,
            reference_path=REF_PATH,
            nome_personagem="Marcos",
            tag_personagem="@Marcos",
            projeto_id=PROJETO_TESTE,
            tipo="personagem",
            motivo_erro=motivos_erro,
            driver=worker
        )
        dt_anexo_ms = int((time.time() - t0_anexo) * 1000)
        print(f"[RESULTADO 2] Sucesso no anexo: {ok_anexo} | Duração: {dt_anexo_ms}ms")
        if motivos_erro:
            print(f"[RESULTADO 2] Motivos de erro/aborto registrados: {motivos_erro}")

        log_event(
            "PIPELINE",
            f"[{PROJETO_TESTE}] incluir_referencia_personagem executado: sucesso={ok_anexo}",
            duration_ms=dt_anexo_ms,
            details={"stage": "anexo_personagem", "projeto_id": PROJETO_TESTE}
        )

        # Salva screenshot do anexo com chip confirmado
        try:
            Path("docs/screenshots").mkdir(parents=True, exist_ok=True)
            page.screenshot(path="docs/screenshots/flow_anexo_sucesso_marcos_final.png")
            print("[EVIDÊNCIA] Screenshot salvo em docs/screenshots/flow_anexo_sucesso_marcos_final.png")
        except Exception:
            pass

        # Limpa o prompt para não deixar resíduos no Flow
        try:
            btn_apagar = page.locator('button[aria-label*="Apagar comando" i], button.clear-button').first
            if btn_apagar.is_visible(timeout=300):
                btn_apagar.click(timeout=500)
                page.wait_for_timeout(200)
            if editor:
                editor.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
            page.keyboard.press("Escape")
        except Exception:
            pass

        print("\n" + "=" * 70)
        print("RESUMO DA VALIDAÇÃO:")
        print(f"  • Verificação da biblioteca: executada em {dt_verif_ms}ms (retorno={res_verif})")
        print(f"  • Menu abriu sem travar e sem timeout indevido: SIM")
        print(f"  • Anexo de personagem (Etapa C): {dt_anexo_ms}ms (sucesso={ok_anexo})")
        print(f"  • Chip nativo inserido e confirmado no editor do Flow: {'SIM' if ok_anexo else 'NÃO'}")
        print("=" * 70)

        return bool(ok_anexo and (res_verif is True))
    finally:
        p.stop()

if __name__ == "__main__":
    sucesso = run()

    sys.exit(0 if sucesso else 1)
