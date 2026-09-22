from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp('http://[::1]:9222')
    page = [pg for pg in browser.contexts[0].pages if 'flow' in pg.url][0]
    dock_btn = page.locator('button.settings-trigger-button').first
    if not page.locator('flow-prompt-box-settings').is_visible():
        dock_btn.click()
        page.wait_for_timeout(500)
    
    settings = page.locator('flow-prompt-box-settings')
    print('settings visible:', settings.is_visible())
    
    # Busca botão Imagem
    btn_img = settings.locator('button[role="radio"]:has-text("Imagem")').first
    print('btn_img count:', btn_img.count(), 'visible:', btn_img.is_visible())
    if btn_img.is_visible():
        print('btn_img inner_text:', repr(btn_img.inner_text()))
        btn_img.click(force=True)
        page.wait_for_timeout(800)
        print('after click, dock_btn text:', repr(dock_btn.inner_text()))
    
    page.keyboard.press('Escape')
