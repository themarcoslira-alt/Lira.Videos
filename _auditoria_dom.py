"""Auditoria temporaria do DOM real do Google Flow (somente leitura).

Coleta: URL atual, campos de texto, botoes visiveis (especialmente seta),
elementos proximos ao rodape, e testa os seletores atuais.
"""
import sys
from pathlib import Path

BASE = Path(r"C:\ultracut3")
sys.path.insert(0, str(BASE))

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9222"

JS_AUDIT = r"""
() => {
    const visivel = (el) => {
        const r = el.getBoundingClientRect();
        const st = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
    };
    const info = (el) => ({
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute('role') || '',
        class: (el.className || '').toString().slice(0, 90),
        placeholder: el.getAttribute('placeholder') || el.getAttribute('data-placeholder') || '',
        aria: el.getAttribute('aria-label') || '',
        top: Math.round(el.getBoundingClientRect().top),
        bottom: Math.round(el.getBoundingClientRect().bottom),
        h: Math.round(el.getBoundingClientRect().height),
        text: (el.innerText || '').trim().slice(0, 40),
    });

    const campos = [];
    document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"], input[type="text"]').forEach(el => {
        if (visivel(el)) campos.push(info(el));
    });

    const botoes = [];
    document.querySelectorAll('button').forEach(b => {
        if (!visivel(b)) return;
        const icone = b.querySelector('i') ? (b.querySelector('i').textContent || '') : '';
        const svg = b.querySelector('svg') ? 'SVG' : '';
        const spanOculto = Array.from(b.querySelectorAll('span')).map(s => s.textContent.trim()).filter(Boolean).join(' ');
        botoes.push({
            ...info(b),
            icone: icone.trim().slice(0, 20),
            svg: svg,
            spanOculto: spanOculto.slice(0, 30),
            disabled: b.disabled || b.getAttribute('aria-disabled'),
        });
    });

    return {
        url: location.href,
        title: document.title,
        viewport: { w: window.innerWidth, h: window.innerHeight },
        campos: campos,
        botoes: botoes,
    };
}
"""

pw = sync_playwright().start()
b = pw.chromium.connect_over_cdp(CDP)
ctx = b.contexts[0]

print("=== ABAS ===")
for p in ctx.pages:
    print(" -", (p.url or "")[:120])

flow = None
for p in ctx.pages:
    url = p.url or ""
    if "labs.google" in url or "flow" in url:
        flow = p
        break

if flow is None:
    print("SEM_ABA_FLOW")
    pw.stop()
    raise SystemExit(0)

print("\n=== URL ATUAL ===")
print(flow.url)

try:
    res = flow.evaluate(JS_AUDIT)
except Exception as e:
    print("ERRO_AVAL:", e)
    pw.stop()
    raise SystemExit(0)

print("TITULO:", res.get("title"))
print("VIEWPORT:", res.get("viewport"))
print("\n=== CAMPOS DE TEXTO VISIVEIS ===")
for c in res.get("campos", []):
    print(c)
if not res.get("campos"):
    print("(nenhum campo visivel)")

print("\n=== BOTOES VISIVEIS (primeiros 25) ===")
for i, bt in enumerate(res.get("botoes", [])[:25]):
    print(i, bt)
if not res.get("botoes"):
    print("(nenhum botao visivel)")

print("\n=== TESTE DOS SELETORES ATUAIS ===")
seletores = [
    'div[data-slate-editor="true"][contenteditable="true"]:not(aside *):not([role="dialog"] *)',
    'div[role="textbox"][data-slate-editor="true"]:not(aside *)',
    'div[role="textbox"][contenteditable="true"]:not(aside *)',
    'textarea:not(aside *)',
]
for sel in seletores:
    try:
        q = flow.locator(sel)
        n = q.count()
        vis = 0
        for i in range(min(n, 10)):
            try:
                if q.nth(i).is_visible():
                    vis += 1
            except Exception:
                pass
        print(f"  {sel!r}: count={n} visiveis={vis}")
    except Exception as e:
        print(f"  {sel!r}: ERRO {e}")

print("\n=== HTML DO RODAPE (ultimos 8k) ===")
try:
    html = flow.evaluate("() => document.body.innerHTML.slice(-8000)")
    print(html[:3500])
except Exception as e:
    print("ERRO:", e)

pw.stop()
