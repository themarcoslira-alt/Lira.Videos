import sys
import asyncio
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding='utf-8')

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 900})

        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        print("[1] Navegando para http://localhost:5000...")
        await page.goto("http://localhost:5000", wait_until="networkidle")

        print("[2] Abrindo projeto 'WHY YOUR TOMATO PLANTS WONT PRODUCE - AND ITS NOT WHAT YOU THINK' diretamente na aba de produção...")
        await page.evaluate("""() => {
            return window.abrirProjetoComAba('WHY YOUR TOMATO PLANTS WONT PRODUCE - AND ITS NOT WHAT YOU THINK', 'producao', 'producao');
        }""")

        print("[3] Aguardando carregamento do grid de produção...")
        try:
            await page.wait_for_selector("#s2-producao-grid .s2-prod-card", timeout=15000)
            print("Grid carregado com sucesso!")
        except Exception as e:
            print(f"Aviso ao esperar grid: {e}")

        await page.wait_for_timeout(3000)

        print("[4] Verificando se s2-fila-hud está oculto...")
        hud_info = await page.evaluate("""() => {
            const el = document.getElementById('s2-fila-hud');
            if (!el) return { exists: false };
            const style = window.getComputedStyle(el);
            return {
                exists: true,
                display: style.display,
                visibility: style.visibility,
                offsetParent: el.offsetParent !== null
            };
        }""")
        print(f"s2-fila-hud info: {hud_info}")

        print("[5] Verificando botões de Refazer no grid...")
        cards_info = await page.evaluate("""() => {
            const cards = document.querySelectorAll('#s2-producao-grid .s2-prod-card');
            const result = [];
            cards.forEach(card => {
                const cid = card.getAttribute('data-cid');
                const btnRefazer = card.querySelector('button[id^="btn-refazer-"], button[onclick*="refazerCenaIndividualFlow"]');
                const btnGerar = card.querySelector('button[onclick*="gerarCenaIndividualFlow"]');
                const badgeOk = card.querySelector('.badge-ok');
                const badgeWait = card.querySelector('.badge-wait');
                result.push({
                    cid: cid,
                    isPronta: !!badgeOk,
                    isPendente: !!badgeWait,
                    hasRefazer: !!btnRefazer,
                    refazerDisabled: btnRefazer ? btnRefazer.disabled : null,
                    refazerText: btnRefazer ? btnRefazer.textContent.trim() : null,
                    hasGerar: !!btnGerar
                });
            });
            return result;
        }""")

        prontas = [c for c in cards_info if c['isPronta']]
        refazers_ativos = [c for c in cards_info if c['hasRefazer'] and not c['refazerDisabled']]
        refazers_inativos = [c for c in cards_info if c['hasRefazer'] and c['refazerDisabled']]

        print(f"Total de cards no grid: {len(cards_info)}")
        print(f"Cards com status PRONTA: {len(prontas)}")
        print(f"Botões 'Refazer' ativos (habilitados): {len(refazers_ativos)}")
        print(f"Botões 'Refazer' desabilitados: {len(refazers_inativos)}")

        if len(cards_info) > 0:
            for sample in cards_info[:3]:
                print(f"Card {sample['cid']}: Pronta={sample['isPronta']}, RefazerAtivo={not sample['refazerDisabled']}, Texto='{sample['refazerText']}'")

        print("[6] Capturando screenshots...")
        await page.screenshot(path="docs/screenshots/producao_refazer_idle.png", full_page=False)

        # Rola para os cards
        await page.evaluate("""() => {
            const grid = document.getElementById('s2-producao-grid');
            if (grid) grid.scrollIntoView({ behavior: 'instant', block: 'center' });
        }""")
        await page.wait_for_timeout(1000)
        await page.screenshot(path="docs/screenshots/producao_refazer_cards.png", full_page=False)

        print("[7] Verificando erros no console:")
        if console_errors:
            print(f"Erros no console ({len(console_errors)}):")
            for err in console_errors[:5]:
                print(f"  - {err}")
        else:
            print("Nenhum erro de console encontrado!")

        await browser.close()
        print("[8] Validação Playwright concluída com sucesso!")

if __name__ == "__main__":
    asyncio.run(main())
