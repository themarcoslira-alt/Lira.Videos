import sys
import json
import time
import shutil
from pathlib import Path
import asyncio
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding='utf-8')

PROJ_NAME = "WHY YOUR TOMATO PLANTS WONT PRODUCE - AND ITS NOT WHAT YOU THINK"
PROJ_DIR = Path(f"projetos/{PROJ_NAME}")
CENAS_DIR = PROJ_DIR / "cenas"
BACKUP_DIR = PROJ_DIR / "_backup_refazer"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 900})

        console_logs = []
        page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))

        network_responses = []
        page.on("response", lambda resp: network_responses.append({
            "url": resp.url,
            "status": resp.status
        }) if "/refazer" in resp.url or "/fila" in resp.url else None)

        print("[1] Abrindo app e navegando para a aba de produção...")
        await page.goto("http://localhost:5000", wait_until="networkidle")
        await page.evaluate(f"""() => {{
            return window.abrirProjetoComAba('{PROJ_NAME}', 'producao', 'producao');
        }}""")

        await page.wait_for_selector("#s2-producao-grid .s2-prod-card", timeout=15000)
        await page.wait_for_timeout(2000)

        print("[2] Localizando Card 7...")
        card_7 = await page.query_selector("#s2-prod-card-7")
        assert card_7 is not None, "Card 7 não encontrado!"

        btn_refazer_7 = await page.query_selector("#btn-refazer-7")
        assert btn_refazer_7 is not None, "Botão #btn-refazer-7 não encontrado!"
        is_disabled = await btn_refazer_7.is_disabled()
        print(f"Botão #btn-refazer-7 inicial: disabled={is_disabled}")
        assert not is_disabled, "Botão #btn-refazer-7 deveria estar habilitado para cena PRONTA!"

        # Verifica arquivo no disco ANTES do clique
        arq_antes = list(CENAS_DIR.glob("07_*.*"))
        print(f"Arquivos da cena 7 em cenas/ antes do clique: {[f.name for f in arq_antes]}")
        assert len(arq_antes) > 0, "Deveria existir mídia para a cena 7 antes do refazer!"

        print("[3] Clicando em #btn-refazer-7...")
        # Rola até o card 7 para visualização limpa
        await page.evaluate("""() => {
            const el = document.getElementById('s2-prod-card-7');
            if (el) el.scrollIntoView({ behavior: 'instant', block: 'center' });
        }""")
        await page.wait_for_timeout(500)

        # Clica no botão Refazer
        await btn_refazer_7.click()

        # Aguarda feedback visual e resposta de rede
        await page.wait_for_timeout(1500)

        print("[4] Capturando screenshot com cena 7 refazendo...")
        await page.screenshot(path="docs/screenshots/producao_refazer_scene7_active.png", full_page=False)

        print("[5] Verificando s2-fila-hud (deve ficar visível quando a fila tem itens)...")
        hud_info_active = await page.evaluate("""() => {
            const el = document.getElementById('s2-fila-hud');
            if (!el) return { exists: false };
            const style = window.getComputedStyle(el);
            return {
                exists: true,
                display: style.display,
                offsetParent: el.offsetParent !== null
            };
        }""")
        print(f"s2-fila-hud ativo: {hud_info_active}")

        # Rola para o topo para capturar screenshot do HUD ativo
        await page.evaluate("""() => {
            window.scrollTo({ top: 0, behavior: 'instant' });
        }""")
        await page.wait_for_timeout(500)
        await page.screenshot(path="docs/screenshots/producao_refazer_hud_active.png", full_page=False)

        print("[6] Validando disco e lira_scene_plan.json:")
        arq_depois = list(CENAS_DIR.glob("07_*.*"))
        print(f"Arquivos da cena 7 em cenas/ depois: {[f.name for f in arq_depois]}")
        assert len(arq_depois) == 0, "O arquivo antigo da cena 7 DEVE ter saído de cenas/!"

        # Verifica pasta de backup
        backups_cena7 = list(BACKUP_DIR.glob("*07_*.*"))
        print(f"Arquivos da cena 7 em _backup_refazer/: {[f.name for f in backups_cena7]}")
        assert len(backups_cena7) > 0, "O arquivo antigo DEVE estar salvo em _backup_refazer/!"

        # Verifica status da cena 7 no plano
        with open(PROJ_DIR / "lira_scene_plan.json", encoding="utf-8") as f:
            plan = json.load(f)
        c7_plan = [c for c in plan.get("cenas", []) if c.get("id") == 7 or c.get("scene_index") == 7][0]
        print(f"Cena 7 no scene_plan: status={c7_plan.get('status')}, image_status={c7_plan.get('image_status')}, arquivo_midia='{c7_plan.get('arquivo_midia')}'")
        assert c7_plan.get("status") == "GERANDO", "Status no plano deve ser GERANDO!"
        assert c7_plan.get("arquivo_midia") == "", "arquivo_midia no plano deve estar limpo!"

        print("[7] Respostas de rede capturadas:")
        for r in network_responses:
            print(f"  - {r['status']} {r['url']}")

        print("[8] Restaurando cena 7 para estado original para preservar projeto...")
        # Copia o backup de volta para cenas/
        dest_original = CENAS_DIR / arq_antes[0].name
        shutil.copy2(backups_cena7[-1], dest_original)
        c7_plan["status"] = "BAIXADA"
        c7_plan["image_status"] = "READY"
        c7_plan["arquivo_midia"] = str(dest_original)
        with open(PROJ_DIR / "lira_scene_plan.json", "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2)

        # Atualiza flow_queue para concluída
        with open(PROJ_DIR / "flow_queue.json", encoding="utf-8") as f:
            fila = json.load(f)
        for it in fila.get("itens", []):
            if it.get("scene_id") == 7:
                it["status"] = "concluida"
        fila["ativa"] = False
        with open(PROJ_DIR / "flow_queue.json", "w", encoding="utf-8") as f:
            json.dump(fila, f, indent=2)

        print("[9] Projeto restaurado com sucesso! Teste concluído com 100% de sucesso!")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
