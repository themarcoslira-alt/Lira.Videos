from playwright.sync_api import sync_playwright
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.playwright_flow import FlowQueueWorker

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp('http://[::1]:9222')
    page = [pg for pg in browser.contexts[0].pages if 'flow' in pg.url][0]
    
    worker = FlowQueueWorker.get_worker()
    worker.page = page
    
    # Primeiro garante vídeo
    print("=== Configurando Vídeo ===")
    worker._configured_mode = None
    v_ok = worker._set_output_mode('video', modelo_solicitado='Veo 3.1 - Lite', proporcao_solicitada='16:9')
    print("v_ok:", v_ok)
    page.wait_for_timeout(1000)
    
    # Agora configura imagem
    print("\n=== Configurando Imagem ===")
    worker._configured_mode = None
    i_ok = worker._set_output_mode('image', modelo_solicitado='Nano Banana 2', proporcao_solicitada='16:9')
    print("i_ok:", i_ok)
    
    dock_btn = page.locator("button.settings-trigger-button").first
    print("dock_btn final:", repr(dock_btn.inner_text().replace('\n', ' | ')))
