import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.playwright_flow import ensure_chrome_cdp, _cdp_host
from playwright.sync_api import sync_playwright

print("Iniciando Chrome CDP para animacao (Conta 4)...")
ok, msg = ensure_chrome_cdp(9222, force_restart=True, modo="animacao")
print("Resultado ensure_chrome_cdp:", ok, msg)

if ok:
    with sync_playwright() as p:
        h = _cdp_host(9222)
        b = p.chromium.connect_over_cdp(f"http://{h}:9222")
        print("Pages abertas:")
        for i, pg in enumerate(b.contexts[0].pages):
            print(f"  {i}: {pg.url}")
