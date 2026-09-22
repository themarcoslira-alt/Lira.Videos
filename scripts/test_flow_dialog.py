import sys
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import json
from services.playwright_flow import PlaywrightCDPWorker

def main():
    worker = PlaywrightCDPWorker(port=9222)
    ok, msg = worker._iniciar_sessao_thread()
    if not ok:
        print("Erro ao conectar:", msg)
        return
    
    PROJETO_NOME = "WHY YOUR TOMATO PLANTS WONT PRODUCE - AND ITS NOT WHAT YOU THINK"
    worker.current_project_id = PROJETO_NOME
    ok_proj = worker._ensure_project_open(PROJETO_NOME)
    print("Project open:", ok_proj)
    print("URL atual:", worker.page.url)
    btn_html = worker.page.evaluate('''() => {
        const btn = document.querySelector('button.settings-trigger-button');
        return {
            html: btn ? btn.innerHTML : null,
            disabled: btn ? btn.disabled : null,
            hasPointerEvents: btn ? window.getComputedStyle(btn).pointerEvents : null
        };
    }''')
    print("btn_html:", json.dumps(btn_html, indent=2, ensure_ascii=False))
    
    # Check what appeared
    overlays = worker.page.evaluate('''() => {
        const panes = document.querySelectorAll('.cdk-overlay-pane, [role="menu"], [role="dialog"], mat-bottom-sheet-container');
        return Array.from(panes).map(p => ({
            tag: p.tagName,
            cls: p.className,
            text: p.innerText.replace(/\\n/g, ' | ').slice(0, 200)
        }));
    }''')
    print("Overlays apos click:", json.dumps(overlays, indent=2, ensure_ascii=False))
    
    # Check what appeared
    dom_new = worker.page.evaluate('''() => {
        const els = Array.from(document.querySelectorAll('body > *')).map(e => ({
            tag: e.tagName,
            cls: e.className,
            text: e.innerText ? e.innerText.slice(0, 100).replace(/\\n/g, ' ') : ''
        }));
        return els;
    }''')
    print("Body children:", json.dumps(dom_new, indent=2, ensure_ascii=False))
    
    # Check all elements with z-index or fixed/absolute position
    panes = worker.page.evaluate('''() => {
        const all = Array.from(document.querySelectorAll('*')).filter(el => {
            const s = window.getComputedStyle(el);
            return (s.position === 'fixed' || s.position === 'absolute') && parseInt(s.zIndex) > 10 && el.offsetHeight > 50;
        });
        return all.map(d => ({
            tag: d.tagName,
            cls: d.className,
            text: d.innerText ? d.innerText.replace(/\\n/g, ' | ').slice(0, 300) : ''
        }));
    }''')
    print("Panes apos click:", json.dumps(panes, indent=2, ensure_ascii=False))
    worker._encerrar_sessao()

if __name__ == "__main__":
    main()
