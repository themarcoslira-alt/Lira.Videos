import sys
from playwright.sync_api import sync_playwright

p = sync_playwright().start()
b = p.chromium.connect_over_cdp('http://localhost:9222')
page = [pg for c in b.contexts for pg in c.pages if 'flow' in pg.url.lower()][0]

# Listar todos os botões visíveis na metade inferior da tela
btns = page.locator('button').all()
print(f"Total de botões na página: {len(btns)}")
for b in btns:
    try:
        box = b.bounding_box()
        if box and box['y'] > 600:
            txt = b.inner_text().strip().replace('\n', ' ')
            aria = b.get_attribute('aria-label') or ''
            cl = b.get_attribute('class') or ''
            print(f"  Botão y={int(box['y'])}: txt='{txt}' aria='{aria}' class='{cl}'")
    except Exception:
        pass
