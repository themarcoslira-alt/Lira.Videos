from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp('http://[::1]:9222')
    page = [pg for pg in browser.contexts[0].pages if 'flow' in pg.url][0]
    
    dock_btn = page.locator('button.settings-trigger-button').first
    print('Initial dock_btn:', repr(dock_btn.inner_text().replace('\n', ' | ')))
    
    # Abre settings
    if not page.locator('flow-prompt-box-settings').is_visible():
        dock_btn.click()
        page.wait_for_timeout(500)
    
    settings = page.locator('flow-prompt-box-settings')
    print('settings visible:', settings.is_visible())
    
    # Clica em Vídeo
    v_btn = settings.locator('button[role="radio"]:has-text("Vídeo"), button[role="radio"]:has-text("Video")').first
    print('v_btn count:', v_btn.count(), 'visible:', v_btn.is_visible())
    if v_btn.is_visible():
        print('v_btn inner_text:', repr(v_btn.inner_text()))
        v_btn.click(force=True)
        page.wait_for_timeout(500)
        print('v_btn aria-checked:', v_btn.get_attribute('aria-checked'))
    
    # Dropdown de modelo
    dd_trigger = settings.locator('button[aria-label="Selecionar família de modelos"]').first
    print('dd_trigger count:', dd_trigger.count(), 'visible:', dd_trigger.is_visible())
    if dd_trigger.is_visible():
        print('dd_trigger text:', repr(dd_trigger.inner_text()))
        dd_trigger.click(force=True)
        page.wait_for_timeout(500)
        
        # Opções no menu
        menu_items = page.locator('.cdk-overlay-pane [role="menuitem"]').all()
        print('menu_items count:', len(menu_items))
        for m in menu_items:
            print('  item:', repr(m.inner_text()))
            if 'veo 3.1 - lite' in m.inner_text().lower():
                m.click(force=True)
                print('  -> clicked Veo 3.1 - Lite')
                break
        page.wait_for_timeout(500)
    
    page.keyboard.press('Escape')
    page.wait_for_timeout(500)
    print('Final dock_btn:', repr(dock_btn.inner_text().replace('\n', ' | ')))
