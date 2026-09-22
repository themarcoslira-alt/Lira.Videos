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
    
    worker._configured_mode = None
    res = worker._set_output_mode('video', modelo_solicitado='Veo 3.1 - Lite', proporcao_solicitada='16:9')
    print('res:', res)
