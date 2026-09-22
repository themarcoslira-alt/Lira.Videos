import time
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright
from services.playwright_flow import ensure_chrome_cdp, get_exact_balance

ok, msg = ensure_chrome_cdp(9222, profile_dir="lira_profiles/conta_03")
print("ensure_chrome_cdp:", ok, msg)

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    ctx = browser.contexts[0]
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://flow.google.com/about", timeout=60000)
    page.wait_for_timeout(3000)
    
    # Procura botao Crie com o Google Flow
    btn = page.get_by_text("Crie com o Google Flow")
    if btn.count() > 0 and btn.first.is_visible():
        print("Clicking Crie com o Google Flow...")
        btn.first.click()
        page.wait_for_timeout(5000)
    
    # Se caiu na tela de escolha de conta
    if "signin/accountchooser" in page.url:
        print("Tela de escolha de conta detectada. Clicando em MARCOS LIRA (liraboymonetizze@gmail.com)...")
        conta_el = page.locator("text=liraboymonetizze@gmail.com").first
        if conta_el.is_visible():
            conta_el.click()
            page.wait_for_timeout(6000)

    print("Current URL:", page.url)
    Path("docs/screenshots").mkdir(parents=True, exist_ok=True)
    page.screenshot(path="docs/screenshots/screenshot_after_click.png")
    saldo = get_exact_balance(page)
    print("Saldo extraido:", saldo)
