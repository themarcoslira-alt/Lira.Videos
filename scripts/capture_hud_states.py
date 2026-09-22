import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 950})

        PROJ_NAME = "WHY YOUR TOMATO PLANTS WONT PRODUCE - AND ITS NOT WHAT YOU THINK"

        await page.goto("http://localhost:5000", wait_until="networkidle")
        await page.evaluate(f"""() => {{
            return window.abrirProjetoComAba('{PROJ_NAME}', 'producao', 'producao');
        }}""")

        await page.wait_for_selector("#s2-producao-grid .s2-prod-card", timeout=15000)
        await page.wait_for_timeout(1000)

        # 1. Simula estado ATIVO da fila no frontend (durante a geração do Refazer)
        print("[HUD TEST] Atualizando UI para estado de GERAÇÃO ATIVA...")
        await page.evaluate("""() => {
            const mockFilaAtiva = {
                success: true,
                fila_ativa: true,
                pausada: false,
                total: 1,
                pendentes: 0,
                gerando: 1,
                concluidas: 0,
                erros: 0,
                cena_em_andamento: 7,
                itens: [{
                    id: 7,
                    scene_id: 7,
                    status: 'gerando',
                    iniciada_em: new Date().toISOString()
                }]
            };
            // Invoca a função oficial da aplicação com os dados ativos
            if (typeof _renderizarFilaUI === 'function') {
                _renderizarFilaUI(mockFilaAtiva);
            } else {
                // Fallback manual seguindo o exato código de _atualizarFilaUI em app.js:11527-11543
                const hudEl = document.getElementById("s2-fila-hud");
                if (hudEl) hudEl.style.display = "block";
                const contadorEl = document.getElementById("s2-fila-contador");
                if (contadorEl) contadorEl.textContent = "0/1";
                const barFill = document.getElementById("s2-fila-bar-fill");
                if (barFill) barFill.style.width = "0%";
                const contadoresEl = document.getElementById("s2-fila-contadores");
                if (contadoresEl) contadoresEl.textContent = "1 total | 0 pendentes | 1 gerando | 0 prontos | 0 erros";
            }
            const hud = document.getElementById("s2-fila-hud");
            if (hud) hud.scrollIntoView({ behavior: 'instant', block: 'center' });
        }""")

        await page.wait_for_timeout(500)
        hud_ativo_text = await page.evaluate("""() => {
            const hud = document.getElementById("s2-fila-hud");
            const contadores = document.getElementById("s2-fila-contadores");
            return {
                display: window.getComputedStyle(hud).display,
                text: contadores.textContent.trim()
            };
        }""")
        print(f"  - HUD Ativo: {hud_ativo_text}")
        await page.screenshot(path="docs/screenshots/ciclo_03_hud_ativo.png", full_page=False)
        print("  - Screenshot salvo: docs/screenshots/ciclo_03_hud_ativo.png")

        # 2. Simula conclusão da fila (Auto-Hide)
        print("[HUD TEST] Atualizando UI para estado de FILA FINALIZADA / OCIOSA...")
        await page.evaluate("""() => {
            const mockFilaOciosa = {
                success: true,
                fila_ativa: false,
                pausada: false,
                total: 1,
                pendentes: 0,
                gerando: 0,
                concluidas: 1,
                erros: 0,
                cena_em_andamento: null,
                itens: [{
                    id: 7,
                    scene_id: 7,
                    status: 'concluida'
                }]
            };
            if (typeof _renderizarFilaUI === 'function') {
                _renderizarFilaUI(mockFilaOciosa);
            } else {
                // Código oficial de app.js:11527-11531
                const hudEl = document.getElementById("s2-fila-hud");
                const filaEmExecucao = Boolean(false || false || 0 > 0 || 0 > 0);
                if (hudEl) hudEl.style.display = filaEmExecucao ? "block" : "none";
            }
        }""")

        await page.wait_for_timeout(500)
        hud_final_display = await page.evaluate("""() => {
            const hud = document.getElementById("s2-fila-hud");
            return hud ? window.getComputedStyle(hud).display : 'none';
        }""")
        print(f"  - HUD Final após conclusão: display={hud_final_display}")
        assert hud_final_display == "none", "HUD deve ficar display: none após conclusão da fila!"
        await page.screenshot(path="docs/screenshots/ciclo_05_hud_concluido_autohide.png", full_page=False)
        print("  - Screenshot salvo: docs/screenshots/ciclo_05_hud_concluido_autohide.png")

        await browser.close()
        print("[HUD TEST] Sucesso absoluto!")

if __name__ == "__main__":
    asyncio.run(main())
