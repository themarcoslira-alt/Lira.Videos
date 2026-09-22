import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.playwright_flow import (
    PlaywrightCDPWorker,
    incluir_referencia_personagem,
    _localizar_editor_prompt,
)
from services.event_logger import log_event

PROJETO_TESTE = "WHY YOUR TOMATO PLANTS WONT PRODUCE - AND ITS NOT WHAT YOU THINK"
REF_PATH = f"C:\\Lira Videos\\projetos\\{PROJETO_TESTE}\\characters\\Marcos\\reference.png"

p = sync_playwright().start()
b = p.chromium.connect_over_cdp("http://localhost:9222")
page = [pg for c in b.contexts for pg in c.pages if "flow" in pg.url.lower()][0]

worker = PlaywrightCDPWorker.__new__(PlaywrightCDPWorker)
worker.page = page
worker.current_project_id = PROJETO_TESTE

print("--- LIMPEZA PREVIA DO PROMPT ---")
editor = _localizar_editor_prompt(page)
if editor:
    editor.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.wait_for_timeout(300)

motivos_erro = []
print("--- EXECUTANDO incluir_referencia_personagem ---")
t0 = time.time()
ok = incluir_referencia_personagem(
    page=page,
    reference_path=REF_PATH,
    nome_personagem="Marcos",
    tag_personagem="@Marcos",
    projeto_id=PROJETO_TESTE,
    tipo="personagem",
    motivo_erro=motivos_erro,
    driver=worker
)
dt_ms = int((time.time() - t0) * 1000)

print(f"[RESULTADO] Sucesso: {ok} | Duracao: {dt_ms}ms | Motivos: {motivos_erro}")

# Screenshot do editor com a referencia anexada
Path("docs/screenshots").mkdir(parents=True, exist_ok=True)
page.screenshot(path="docs/screenshots/flow_anexo_sucesso_marcos.png")
print("Screenshot salvo em docs/screenshots/flow_anexo_sucesso_marcos.png")
