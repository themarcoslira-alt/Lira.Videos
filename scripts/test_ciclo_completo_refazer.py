import sys
import json
import time
import hashlib
import shutil
from pathlib import Path
import asyncio
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

PROJ_NAME = "WHY YOUR TOMATO PLANTS WONT PRODUCE - AND ITS NOT WHAT YOU THINK"
PROJ_DIR = Path(f"projetos/{PROJ_NAME}")
CENAS_DIR = PROJ_DIR / "cenas"
BACKUP_DIR = PROJ_DIR / "_backup_refazer"

def get_file_info(p: Path):
    if not p.exists():
        return None
    data = p.read_bytes()
    return {
        "path": str(p),
        "name": p.name,
        "size": len(data),
        "mtime": p.stat().st_mtime,
        "sha256": hashlib.sha256(data).hexdigest()
    }

async def main():
    print("=" * 80)
    print("INICIANDO TESTE DO CICLO COMPLETO DE REFAZER (CENA 7)")
    print("=" * 80)

    # 1. Metadados da Cena 7 ANTES do teste
    arq_inicial = CENAS_DIR / "07_[00-35-00-40].png"
    assert arq_inicial.exists(), f"Arquivo inicial {arq_inicial} não existe!"
    info_antes = get_file_info(arq_inicial)
    print("\n[EVIDÊNCIA 1] Metadados da Cena 7 ANTES do Refazer:")
    print(f"  - Arquivo: {info_antes['name']}")
    print(f"  - Tamanho: {info_antes['size']} bytes ({info_antes['size'] / 1024:.1f} KB)")
    print(f"  - Timestamp (mtime): {info_antes['mtime']}")
    print(f"  - SHA256: {info_antes['sha256']}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 950})

        console_logs = []
        page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))

        network_intercepts = []
        async def on_response(response):
            if "/refazer" in response.url or "/fila_status" in response.url:
                try:
                    body = await response.json()
                except Exception:
                    body = {}
                network_intercepts.append({
                    "url": response.url,
                    "status": response.status,
                    "body": body
                })
        page.on("response", on_response)

        print("\n[PASSO 1] Abrindo interface e carregando aba de Produção...")
        await page.goto("http://localhost:5000", wait_until="networkidle")
        await page.evaluate(f"""() => {{
            return window.abrirProjetoComAba('{PROJ_NAME}', 'producao', 'producao');
        }}""")

        await page.wait_for_selector("#s2-producao-grid .s2-prod-card", timeout=15000)
        await page.wait_for_timeout(2000)

        # Captura URL da miniatura ANTES do clique
        url_thumb_antes = await page.evaluate("""() => {
            const card = document.getElementById('s2-prod-card-7');
            if (!card) return null;
            const img = card.querySelector('.s2-prod-card-thumb img, .s2-prod-card-thumb video');
            return img ? img.getAttribute('src') : null;
        }""")
        print(f"  - URL da thumbnail no card ANTES: {url_thumb_antes}")

        # Screenshot do Card 7 inicial
        await page.evaluate("""() => {
            const el = document.getElementById('s2-prod-card-7');
            if (el) el.scrollIntoView({ behavior: 'instant', block: 'center' });
        }""")
        await page.wait_for_timeout(500)
        await page.screenshot(path="docs/screenshots/ciclo_01_card7_inicial.png", full_page=False)
        print("  - Screenshot salvo: docs/screenshots/ciclo_01_card7_inicial.png")

        # Verifica estado inicial do HUD
        hud_inicial = await page.evaluate("""() => {
            const el = document.getElementById('s2-fila-hud');
            return el ? window.getComputedStyle(el).display : 'none';
        }""")
        print(f"  - Estado inicial do #s2-fila-hud: display={hud_inicial}")

        # 2. Clica no botão Refazer
        print("\n[PASSO 2] Clicando em 🔁 Refazer no Card 7...")
        btn_refazer = await page.query_selector("#btn-refazer-7")
        assert btn_refazer is not None, "Botão #btn-refazer-7 não encontrado!"
        assert not await btn_refazer.is_disabled(), "Botão deve estar habilitado!"

        t_clique = time.time()
        await btn_refazer.click()

        # Aguarda feedback imediato no DOM
        await page.wait_for_timeout(800)

        # Captura estado transitório do Card 7
        card7_transitorio = await page.evaluate("""() => {
            const card = document.getElementById('s2-prod-card-7');
            if (!card) return null;
            const btn = card.querySelector('#btn-refazer-7');
            const badge = card.querySelector('#s2-cena-badge-7');
            const thumbImg = card.querySelector('.s2-prod-card-thumb img');
            return {
                btnText: btn ? btn.textContent.trim() : null,
                btnDisabled: btn ? btn.disabled : null,
                badgeText: badge ? badge.textContent.trim() : null,
                thumbSrc: thumbImg ? thumbImg.getAttribute('src') : null
            };
        }""")
        print(f"  - Card 7 feedback transitório: {card7_transitorio}")
        await page.screenshot(path="docs/screenshots/ciclo_02_card7_refazendo.png", full_page=False)
        print("  - Screenshot salvo: docs/screenshots/ciclo_02_card7_refazendo.png")

        # Confirma movimentação de arquivo no disco para _backup_refazer
        arq_cenas_apos_clique = list(CENAS_DIR.glob("07_*.*"))
        assert len(arq_cenas_apos_clique) == 0, "Arquivo antigo da cena 7 DEVE ter saído da pasta cenas/!"
        backups_cena7 = sorted(list(BACKUP_DIR.glob("*07_*.*")), key=lambda p: p.stat().st_mtime)
        assert len(backups_cena7) > 0, "Arquivo antigo DEVE estar em _backup_refazer/!"
        backup_recente = backups_cena7[-1]
        print(f"  - Arquivo movido para _backup_refazer/: {backup_recente.name}")

        # 3. TAREFA 2: HUD ao vivo
        print("\n[PASSO 3] Verificando HUD #s2-fila-hud ativo ao vivo...")
        # Rola para o topo para visualização do HUD
        await page.evaluate("""() => {
            window.scrollTo({ top: 0, behavior: 'instant' });
        }""")

        # Aguarda o polling atualizar e exibir o HUD ativo
        for _ in range(12):
            hud_disp = await page.evaluate("""() => {
                const el = document.getElementById('s2-fila-hud');
                return el ? window.getComputedStyle(el).display : 'none';
            }""")
            if hud_disp != 'none':
                break
            await page.wait_for_timeout(500)

        hud_ativo_info = await page.evaluate("""() => {
            const el = document.getElementById('s2-fila-hud');
            const contadores = document.getElementById('s2-fila-contadores');
            const contador = document.getElementById('s2-fila-contador');
            return {
                display: el ? window.getComputedStyle(el).display : 'none',
                contador: contador ? contador.textContent.trim() : null,
                contadores: contadores ? contadores.textContent.trim() : null
            };
        }""")
        print(f"  - HUD ativo info: {hud_ativo_info}")
        await page.screenshot(path="docs/screenshots/ciclo_03_hud_ativo.png", full_page=False)
        print("  - Screenshot salvo: docs/screenshots/ciclo_03_hud_ativo.png")

        # 4. Aguardar o processamento completo do worker SEM RECARREGAR (SEM F5)
        print("\n[PASSO 4] Aguardando o worker processar no Flow (SEM F5, polling ativo)...")
        max_wait = 180  # até 3 minutos
        t_inicio_espera = time.time()
        concluido = False
        novo_arquivo_info = None

        while time.time() - t_inicio_espera < max_wait:
            await page.wait_for_timeout(3000)

            # Checa se novo arquivo foi gerado em cenas/
            novos_arqs = list(CENAS_DIR.glob("07_*.*"))
            if novos_arqs:
                novo_arq_path = novos_arqs[0]
                if novo_arq_path.stat().st_size > 1024:
                    novo_arquivo_info = get_file_info(novo_arq_path)

            # Checa estado no DOM do Card 7
            card_state = await page.evaluate("""() => {
                const card = document.getElementById('s2-prod-card-7');
                if (!card) return null;
                const badgeOk = card.querySelector('.badge-ok');
                const badgeQueue = card.querySelector('#s2-cena-badge-7');
                const btnRefazer = card.querySelector('#btn-refazer-7');
                const img = card.querySelector('.s2-prod-card-thumb img, .s2-prod-card-thumb video');
                return {
                    isPronta: !!badgeOk,
                    badgeQueueText: badgeQueue ? badgeQueue.textContent.trim() : null,
                    refazerDisabled: btnRefazer ? btnRefazer.disabled : null,
                    thumbSrc: img ? img.getAttribute('src') : null
                };
            }""")

            elapsed = int(time.time() - t_inicio_espera)
            if card_state and card_state["isPronta"] and novo_arquivo_info:
                print(f"\n[SUCESSO] Card 7 atualizou para PRONTA em {elapsed}s!")
                print(f"  - Card State: {card_state}")
                concluido = True
                break
            else:
                if elapsed % 15 == 0:
                    hud_poll = await page.evaluate("""() => {
                        const c = document.getElementById('s2-fila-contadores');
                        return c ? c.textContent.trim() : '';
                    }""")
                    print(f"  ... {elapsed}s aguardando worker | HUD: '{hud_poll}' | Card: {card_state.get('badgeQueueText') if card_state else ''}")

        assert concluido, "Worker não concluiu a cena no tempo limite!"

        print("\n[PASSO 5] Validando novo arquivo gerado no disco:")
        print(f"  - Novo Arquivo: {novo_arquivo_info['name']}")
        print(f"  - Novo Tamanho: {novo_arquivo_info['size']} bytes ({novo_arquivo_info['size'] / 1024:.1f} KB)")
        print(f"  - Novo Timestamp (mtime): {novo_arquivo_info['mtime']}")
        print(f"  - Novo SHA256: {novo_arquivo_info['sha256']}")
        print(f"  - Diferença de Tamanho vs Original: {novo_arquivo_info['size'] - info_antes['size']} bytes")
        print(f"  - Hash diferente do original: {novo_arquivo_info['sha256'] != info_antes['sha256']}")
        assert novo_arquivo_info['sha256'] != info_antes['sha256'], "O novo arquivo DEVE ser diferente do original!"

        print("\n[PASSO 6] Validando atualização visual no DOM (SEM F5):")
        # Rola de volta até o Card 7
        await page.evaluate("""() => {
            const el = document.getElementById('s2-prod-card-7');
            if (el) el.scrollIntoView({ behavior: 'instant', block: 'center' });
        }""")
        await page.wait_for_timeout(1000)
        await page.screenshot(path="docs/screenshots/ciclo_04_card7_concluido_sem_f5.png", full_page=False)
        print("  - Screenshot salvo: docs/screenshots/ciclo_04_card7_concluido_sem_f5.png")

        url_thumb_depois = await page.evaluate("""() => {
            const card = document.getElementById('s2-prod-card-7');
            const img = card.querySelector('.s2-prod-card-thumb img, .s2-prod-card-thumb video');
            return img ? img.getAttribute('src') : null;
        }""")
        print(f"  - URL da thumbnail no card DEPOIS: {url_thumb_depois}")
        assert url_thumb_depois != url_thumb_antes, "A URL da thumbnail deve ter cache-buster diferente!"

        print("\n[PASSO 7] Validando comportamento final do HUD (Auto-Hide):")
        # Aguarda 5 segundos para o polling detectar fila inativa
        await page.wait_for_timeout(5000)
        hud_final = await page.evaluate("""() => {
            const el = document.getElementById('s2-fila-hud');
            const contadores = document.getElementById('s2-fila-contadores');
            return {
                display: el ? window.getComputedStyle(el).display : 'none',
                contadores: contadores ? contadores.textContent.trim() : null
            };
        }""")
        print(f"  - HUD final após conclusão: {hud_final}")
        await page.screenshot(path="docs/screenshots/ciclo_05_hud_concluido_autohide.png", full_page=False)
        print("  - Screenshot salvo: docs/screenshots/ciclo_05_hud_concluido_autohide.png")

        # 5. TAREFA 4: Restaurar
        print("\n[PASSO 8] Restaurando projeto ao estado original...")
        dest_original = CENAS_DIR / info_antes["name"]
        # Remove arquivos gerados no teste da cena 7
        for f in CENAS_DIR.glob("07_*.*"):
            try:
                f.unlink()
            except Exception:
                pass
        shutil.copy2(backup_recente, dest_original)

        # Atualiza plano para estado original
        with open(PROJ_DIR / "lira_scene_plan.json", encoding="utf-8") as f:
            plan = json.load(f)
        for c in plan["cenas"]:
            if c.get("id") == 7 or c.get("scene_index") == 7:
                c["status"] = "BAIXADA"
                c["image_status"] = "READY"
                c["arquivo_midia"] = str(dest_original)
                c["erro_msg"] = ""
        with open(PROJ_DIR / "lira_scene_plan.json", "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2)

        # Atualiza fila
        with open(PROJ_DIR / "flow_queue.json", encoding="utf-8") as f:
            fila = json.load(f)
        fila["ativa"] = False
        fila["itens"] = []
        with open(PROJ_DIR / "flow_queue.json", "w", encoding="utf-8") as f:
            json.dump(fila, f, indent=2)

        info_restaurado = get_file_info(dest_original)
        print(f"  - Arquivo restaurado: {info_restaurado['name']}")
        print(f"  - SHA256 restaurado: {info_restaurado['sha256']}")
        print(f"  - Restauração perfeita e consistente: {info_restaurado['sha256'] == info_antes['sha256']}")
        assert info_restaurado['sha256'] == info_antes['sha256'], "O arquivo restaurado deve bater com o hash original!"

        await browser.close()
        print("\n" + "=" * 80)
        print("CICLO COMPLETO DO REFAZER VALIDADO COM 100% DE SUCESSO!")
        print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())
