# -*- coding: utf-8 -*-
"""
Teste End-to-End via Playwright / CDP para o botão unificado "🎬 Mídia de Movimento" (btn-midia-movimento).
Valida:
1. Carregamento do projeto e cálculo de dispBroll comparando p.prontas/p.total vs prod.resume_info.
2. Clique no botão "🎬 Mídia de Movimento".
3. Captura das requisições de rede:
   - POST /api/v2/producao/{projeto_id}/marcar_hook_video (payload, status code, response)
   - POST /api/v2/producao/{projeto_id}/iniciar_fila (payload, status code, response)
4. Verificação do toast de sucesso exibido na tela com contagens de vídeos/imagens.
5. Verificação da consistência de dispBroll e relatório de divergências.
"""

import sys
import json
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')

PROJETO_ID = "Why Your Tomato Plants Wont Produce - And Its Not What You Think"
BASE_URL = f"http://127.0.0.1:5000/projeto/{urllib.parse.quote(PROJETO_ID)}"

print(f"=== INICIANDO TESTE E2E BROWSER: {PROJETO_ID} ===")

captured_requests = []
captured_responses = []
captured_dialogs = []

def run_test():
    with sync_playwright() as pw:
        print("[1] Conectando ao Chrome via CDP (porta 9222)...")
        browser = pw.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        
        # Encontra ou cria a aba do Lira Studio
        page = None
        for p in context.pages:
            if "127.0.0.1:5000" in p.url:
                page = p
                break
        
        if not page:
            page = context.new_page()
            
        print(f"[2] Navegando para {BASE_URL}...")
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # Hook para interceptar alert e showToast diretamente no JS runtime
        page.evaluate("""() => {
            window._TEST_TOASTS = [];
            window._TEST_ALERTS = [];
            
            const origShowToast = window.showToast;
            window.showToast = function(msg, tipo) {
                window._TEST_TOASTS.push({ text: String(msg), tipo: tipo, time: Date.now() });
                if (typeof origShowToast === 'function') {
                    try { origShowToast(msg, tipo); } catch(e){}
                }
            };

            const origAlert = window.alert;
            window.alert = function(msg) {
                window._TEST_ALERTS.push({ text: String(msg), time: Date.now() });
                console.warn('[INTERCEPTED ALERT]', msg);
            };
        }""")

        def on_dialog(d):
            print(f"  [DIALOG] {d.type}: {d.message}")
            captured_dialogs.append({"type": d.type, "message": d.message})
            try:
                d.accept()
            except Exception:
                pass

        page.on("dialog", on_dialog)

        # Configura interceptação de rede
        def on_request(req):
            if "marcar_hook_video" in req.url or "iniciar_fila" in req.url:
                post_data = None
                try:
                    post_data = req.post_data_json
                except Exception:
                    post_data = req.post_data
                captured_requests.append({
                    "url": req.url,
                    "method": req.method,
                    "post_data": post_data,
                    "timestamp": time.time()
                })
                print(f"  -> [REDE REQ] {req.method} {req.url.split('/api/v2')[-1]}")

        def on_response(res):
            if "marcar_hook_video" in res.url or "iniciar_fila" in res.url:
                body = None
                try:
                    body = res.json()
                except Exception:
                    body = res.text()
                captured_responses.append({
                    "url": res.url,
                    "status": res.status,
                    "body": body,
                    "timestamp": time.time()
                })
                print(f"  <- [REDE RES] {res.status} {res.url.split('/api/v2')[-1]}: {body}")

        page.on("request", on_request)
        page.on("response", on_response)

        # [3] Análise de consistência dispBroll no browser
        print("\n[3] Comparando p.prontas/p.total contra prod.resume_info via runtime do browser...")
        analise_disp = page.evaluate("""async (pid) => {
            const prod = await api(`/api/v2/producao/${encodeURIComponent(pid)}/status`);
            const p = prod.progresso || {};
            const rInfo = prod.resume_info || {};
            const prontosTotal = (p.prontas !== undefined ? p.prontas : (rInfo && rInfo.prontas_count)) || 0;
            const totalCenas = (p.total !== undefined ? p.total : (rInfo && rInfo.total)) || 0;
            const dispBroll = (totalCenas > 0 && prontosTotal > 0) ? "inline-block" : "none";

            return {
                p_total: p.total,
                p_prontas: p.prontas,
                p_por_status: p.por_status,
                rinfo_total: rInfo.total,
                rinfo_prontas_count: rInfo.prontas_count,
                rinfo_pendentes_count: rInfo.pendentes_count,
                prontosTotal: prontosTotal,
                totalCenas: totalCenas,
                dispBroll: dispBroll,
                dispBroll_com_p: (p.total > 0 && p.prontas > 0) ? "inline-block" : "none",
                dispBroll_com_rinfo: (rInfo.total > 0 && rInfo.prontas_count > 0) ? "inline-block" : "none",
                cenas_count: (prod.cenas || []).length
            };
        }""", PROJETO_ID)

        print(f"  p.total: {analise_disp['p_total']} | rinfo.total: {analise_disp['rinfo_total']}")
        print(f"  p.prontas: {analise_disp['p_prontas']} | rinfo.prontas_count: {analise_disp['rinfo_prontas_count']}")
        print(f"  Divergência de contagem: delta = {analise_disp['p_prontas'] - analise_disp['rinfo_prontas_count']}")
        print(f"  dispBroll com p: {analise_disp['dispBroll_com_p']}")
        print(f"  dispBroll com rinfo: {analise_disp['dispBroll_com_rinfo']}")
        print(f"  dispBroll resultante efetivo: {analise_disp['dispBroll']}")

        # [4] Navegar para a Aba Produção
        print("\n[4] Ativando a Aba 3: PRODUÇÃO...")
        page.evaluate("""() => {
            const tabBtn = document.querySelector('button[data-s2-tab="producao"]');
            if (tabBtn) tabBtn.click();
            else if (typeof navegarAbaS2 === 'function') navegarAbaS2('producao');
        }""")
        page.wait_for_timeout(1500)

        # [5] Verificar estado visual do botão btn-midia-movimento
        print("\n[5] Inspecionando elemento #btn-midia-movimento no DOM...")
        btn_info = page.evaluate("""() => {
            const btn = document.getElementById('btn-midia-movimento');
            if (!btn) return { exists: false };
            const style = window.getComputedStyle(btn);
            return {
                exists: true,
                id: btn.id,
                textContent: btn.textContent.trim(),
                inlineDisplay: btn.style.display,
                computedDisplay: style.display,
                visibility: style.visibility,
                disabled: btn.disabled,
                offsetParent: btn.offsetParent !== null
            };
        }""")
        print(f"  Botão encontrado: {btn_info.get('exists')}")
        print(f"  Texto do botão: '{btn_info.get('textContent')}'")
        print(f"  Display inline: '{btn_info.get('inlineDisplay')}' | Computed: '{btn_info.get('computedDisplay')}'")
        print(f"  Visível na tela (offsetParent != null): {btn_info.get('offsetParent')}")

        # Garante que a fila esteja limpa para permitir enfileiramento sem 409
        from services.playwright_flow import _ler_fila, _salvar_fila
        from config import PROJETOS_DIR
        fq = _ler_fila(PROJETO_ID, PROJETOS_DIR) or {}
        for it in fq.get("itens", []):
            if it.get("status") in ("enfileirada", "gerando"):
                it["status"] = "cancelada"
        _salvar_fila(PROJETO_ID, fq, PROJETOS_DIR)

        # [6] Clicar no botão "🎬 Mídia de Movimento"
        print("\n[6] Disparando clique em #btn-midia-movimento...")
        click_success = page.evaluate("""() => {
            const btn = document.getElementById('btn-midia-movimento');
            if (!btn) return false;
            btn.click();
            return true;
        }""")
        print(f"  Clique acionado via DOM: {click_success}")

        # Aguarda as chamadas de rede e o toast
        print("\n[7] Aguardando requisições de rede e renderização do toast (até 75s)...")
        t0 = time.time()
        while time.time() - t0 < 75.0:
            toasts = page.evaluate("() => window._TEST_TOASTS || []")
            alerts = page.evaluate("() => window._TEST_ALERTS || []")
            btn_txt = page.evaluate("() => { const b = document.getElementById('btn-midia-movimento'); return b ? b.textContent.trim() : ''; }")
            if len(captured_responses) >= 2 and (toasts or alerts):
                print(f"  -> Concluído em {round(time.time() - t0, 1)}s!")
                break
            page.wait_for_timeout(1000)
            elapsed = round(time.time() - t0, 1)
            if int(elapsed) % 4 == 0:
                print(f"  ... {elapsed}s decorridos | btn text: '{btn_txt}' | reqs: {len(captured_requests)} | resps: {len(captured_responses)}")

        # Coleta toasts e alerts finais
        toasts_finais = page.evaluate("""() => {
            const fromInterceptor = window._TEST_TOASTS || [];
            const alerts = window._TEST_ALERTS || [];
            const inDom = Array.from(document.querySelectorAll('.toast, #toast-container div')).map(el => ({
                text: el.textContent.trim(),
                className: el.className
            }));
            const btn = document.getElementById('btn-midia-movimento');
            const btnTextFinal = btn ? btn.textContent.trim() : '';
            return { fromInterceptor, alerts, inDom, btnTextFinal };
        }""")

        # Para imediatamente o worker de produção para não consumir recursos
        print("\n[8] Parando FlowQueueWorker após validação...")
        try:
            from services.playwright_flow import FlowQueueWorker
            FlowQueueWorker.stop_worker()
            print("  FlowQueueWorker parado com sucesso.")
        except Exception as e_stop:
            print(f"  Aviso ao parar FlowQueueWorker: {e_stop}")

        # Monta relatório final estruturado
        resultado = {
            "projeto_id": PROJETO_ID,
            "analise_dispBroll": analise_disp,
            "botao_info": btn_info,
            "requests_capturados": captured_requests,
            "responses_capturados": captured_responses,
            "toasts": toasts_finais
        }

        report_file = ROOT / "tmp" / "resultado_e2e_midia_movimento.json"
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(json.dumps(resultado, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n[9] Relatório salvo em: {report_file}")
        return resultado

if __name__ == "__main__":
    res = run_test()
    print("\n=== RESUMO EXECUTIVO DO TESTE ===")
    print(f"1. Requisições capturadas: {len(res['requests_capturados'])}")
    for r in res['requests_capturados']:
        print(f"   - {r['method']} {r['url'].split('/')[-1]}: {r.get('post_data')}")
    print(f"2. Respostas capturadas: {len(res['responses_capturados'])}")
    for r in res['responses_capturados']:
        print(f"   - Status {r['status']}: {r.get('body')}")
    print(f"3. Toasts capturados: {res['toasts']}")
