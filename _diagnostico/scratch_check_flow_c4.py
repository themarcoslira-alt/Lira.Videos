import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.playwright_flow import _cdp_host, FlowQueueWorker, open_flow_tab
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    h = _cdp_host(9222)
    b = p.chromium.connect_over_cdp(f"http://{h}:9222")
    ctx = b.contexts[0]
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    url_target = "https://flow.google.com/project/9b8bbb1f-a86c-4b4e-9a80-80dccdae1f41"
    print(f"Navegando para Flow da Conta 4: {url_target}")
    page.goto(url_target, timeout=60000)
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(4000)
    print("URL atual:", page.url)

    # Verifica se há botão de configurações / prompt box
    dock = page.locator("button.settings-trigger-button, button[aria-label='Gatilho de configurações']").first
    print("Dock button visível?", dock.is_visible(timeout=5000))
    if dock.is_visible():
        print("Dock button text:", repr(dock.inner_text()))
