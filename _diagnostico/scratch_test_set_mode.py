from playwright.sync_api import sync_playwright
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from services.playwright_flow import FlowQueueWorker

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    pg = browser.contexts[0].pages[1]
    worker = FlowQueueWorker.get_worker()
    worker.page = pg

    print("--- 1. Set Video ---")
    worker._configured_mode = None
    v = worker._set_output_mode("video", modelo_solicitado="Veo 3.1 - Lite")
    dock = pg.locator("button.settings-trigger-button").first
    print("Video set result:", v, "Dock:", repr(dock.inner_text()))

    print("\n--- 2. Set Image ---")
    worker._configured_mode = None
    im = worker._set_output_mode("image", modelo_solicitado="Nano Banana 2")
    print("Image set result:", im, "Dock:", repr(dock.inner_text()))
