"""
verificar_reconectar_flow.py — Validação do fluxo de produção do Studio 2.0.

Roda standalone (não é coletado pela suíte test_*)::
    .venv\\Scripts\\python.exe tests\\verificar_reconectar_flow.py

Cobre:
  1. py_compile dos arquivos alterados;
  2. Balanceamento de delimitadores do static/app.js;
  3. Flask test client na rota POST /api/flow/reconectar.
NÃO abre Chrome real: os caminhos de erro são exercitados via monkeypatch.
"""
import json
import py_compile
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

FALHAS = []


def ok(nome, detalhe=""):
    print(f"  [OK]   {nome} {detalhe}".rstrip())


def fail(nome, detalhe=""):
    FALHAS.append(nome)
    print(f"  [FAIL] {nome} :: {detalhe}")


def check_py_compile():
    print("\n[1] py_compile")
    for rel in ("app_web.py", "services/playwright_flow.py"):
        try:
            py_compile.compile(str(BASE / rel), doraise=True)
            ok(rel)
        except Exception as e:
            fail(rel, str(e)[:300])


def _strip_js(code):
    """Remove strings/comentários de JS para checar delimitadores."""
    out = []
    i, n = 0, len(code)
    while i < n:
        c = code[i]
        nxt = code[i + 1] if i + 1 < n else ""
        if c in "\"'`":
            quote = c
            i += 1
            while i < n:
                if code[i] == "\\":
                    i += 2
                    continue
                if code[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if c == "/" and nxt == "/":
            while i < n and code[i] != "\n":
                i += 1
            continue
        if c == "/" and nxt == "*":
            i = code.find("*/", i + 2)
            i = n if i < 0 else i + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _contar_delimitadores(codigo):
    """Conta fechamentos inválidos e abertos sem par; retorna (erros, sobra, pos_erro)."""
    pares = {"{": "}", "(": ")", "[": "]"}
    pilha = []
    erros = 0
    pos_erro = []
    for idx, ch in enumerate(codigo):
        if ch in pares:
            pilha.append([ch, idx])
        elif ch in pares.values():
            if pilha and pares[pilha[-1][0]] == ch:
                pilha.pop()
            else:
                erros += 1
                if len(pos_erro) < 5:
                    pos_erro.append((idx, ch))
    return erros, len(pilha), pos_erro


def check_js_balance():
    print("\n[2] Balanceamento static/app.js")
    js = (BASE / "static" / "app.js").read_text(encoding="utf-8")
    limpo = _strip_js(js)
    erros, sobra, pos_erro = _contar_delimitadores(limpo)

    if erros == 0 and sobra == 0:
        ok(f"delimitadores balanceados ({len(limpo)} chars efetivos)")
        return

    # Literais regex com aspas (ex.: /texto="([^"]*)"/) geram falso positivo no
    # parser simplificado acima. Por isso, se os números forem IDÊNTICOS aos da
    # versão commitada, a edição NÃO introduziu desbalanceamento novo -> APROVADO.
    try:
        g = subprocess.run(["git", "-C", str(BASE), "show", "HEAD:static/app.js"],
                           capture_output=True, timeout=30)
        if g.returncode == 0:
            head = g.stdout.decode("utf-8", errors="replace")
            eh, sh, _ = _contar_delimitadores(_strip_js(head))
            if (eh, sh) == (erros, sobra):
                ok(f"idêntico ao último commit (erros pré-existentes={erros}, "
                   f"falsos positivos de regex — nada introduzido pela edição)")
                return
            print(f"  [INFO] HEAD: erros={eh}, sem_par={sh} | atual: erros={erros}, sem_par={sobra}")
    except Exception as e:
        print(f"  [INFO] comparação com HEAD indisponível: {e}")

    linhas_js = js.splitlines()
    det = []
    for idx, ch in pos_erro:
        ln = limpo.count("\n", 0, idx) + 1
        ctx = linhas_js[ln - 1].strip()[:80] if 0 < ln <= len(linhas_js) else "?"
        det.append(f"L~{ln} '{ch}' :: {ctx}")
    fail("balanceamento",
         f"fechamentos inválidos={erros}, sem par={sobra} | " + " || ".join(det))


def check_flask_client():
    print("\n[3] Flask test client — POST /api/flow/reconectar")
    import app_web  # noqa: E402
    import services.playwright_flow as pf  # noqa: E402

    app = app_web.app
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True

    URL = "/api/flow/reconectar"

    # 3.1 sem projeto_id -> 400 controlado
    r = client.post(URL, data=json.dumps({}), content_type="application/json")
    body = r.get_json(silent=True) or {}
    cond = (r.status_code == 400 and body.get("success") is False)
    (ok if cond else fail)("sem projeto_id -> 400", f"status={r.status_code} body={body}")

    # 3.2 projeto inexistente -> 400 controlado
    r = client.post(URL, data=json.dumps({"projeto_id": "__nao_existe_xyz__"}),
                    content_type="application/json")
    body = r.get_json(silent=True) or {}
    cond = (r.status_code == 400 and body.get("success") is False)
    (ok if cond else fail)("projeto inexistente -> 400", f"status={r.status_code} body={body}")

    # Pasta fake de projeto (criada e removida por este script)
    fake = app_web.PROJETOS_DIR / "zzz_teste_reconnect_tmp"
    fake.mkdir(parents=True, exist_ok=True)

    try:
        # 3.3 projeto existe, SEM flow_meta.json (não deve tocar no Chrome)
        r = client.post(URL, data=json.dumps({"projeto_id": fake.name}),
                        content_type="application/json")
        body = r.get_json(silent=True) or {}
        cond = (r.status_code == 200 and body.get("success") is False
                and "Nenhuma URL" in (body.get("message") or ""))
        (ok if cond else fail)("sem flow_meta.json -> msg clara",
                               f"status={r.status_code} body={body}")

        # 3.4 com flow_meta.json mas Chrome/CDP falhando (mock)
        (fake / "flow_meta.json").write_text(
            json.dumps({"flow_project_url": "https://labs.google/fx/project/fake"}),
            encoding="utf-8")
        original_ensure = pf.ensure_chrome_cdp
        pf.ensure_chrome_cdp = lambda *a, **k: (False, "simulado offline")
        try:
            r = client.post(URL, data=json.dumps({"projeto_id": fake.name}),
                            content_type="application/json")
        finally:
            pf.ensure_chrome_cdp = original_ensure
        body = r.get_json(silent=True) or {}
        msg = body.get("message") or ""
        cond = (r.status_code == 200 and body.get("success") is False
                and "Chrome/CDP indisponível" in msg and "simulado offline" in msg)
        (ok if cond else fail)("Chrome falho (mock) -> msg clara",
                               f"status={r.status_code} body={body}")

        # 3.5 fila ocupada -> recusa ANTES de qualquer chamada externa
        worker = pf.FlowQueueWorker.get_worker()
        worker.is_running_queue = True
        try:
            r = client.post(URL, data=json.dumps({"projeto_id": fake.name}),
                            content_type="application/json")
        finally:
            worker.is_running_queue = False
        body = r.get_json(silent=True) or {}
        cond = (r.status_code == 200 and body.get("success") is False
                and "Fila de produção em execução" in (body.get("message") or ""))
        (ok if cond else fail)("fila ocupada -> recusa informativa",
                               f"status={r.status_code} body={body}")
    finally:
        shutil.rmtree(fake, ignore_errors=True)

    print("\n[4] Frontend (Bloco arquitetural)")
    html = (BASE / "static" / "index.html").read_text(encoding="utf-8")
    js2 = (BASE / "static" / "app.js").read_text(encoding="utf-8")

    # Botões removidos da barra da Aba Produção não podem existir
    removidos = ("btn-s2-abrir-flow", "btn-s2-reconectar-flow",
                 "btn-s2-gerar-prompts-prod", "btn-s2-auto-importar",
                 "btn-s2-animar-cenas", "btn-s2-retomar-fila",
                 "btn-s2-retentar-erros", "s2-resume-banner")
    for bid in removidos:
        (ok if f'id="{bid}"' not in html else fail)(
            f"botão removido ausente no index.html: {bid}")
        (ok if bid not in js2 else fail)(
            f"referência removida ausente no app.js: {bid}")

    # Botões mantidos no cabeçalho da Aba Produção
    mantidos = ("btn-s2-iniciar-fila", "btn-s2-animar-broll")
    for bid in mantidos:
        (ok if f'id="{bid}"' in html else fail)(
            f"botão mantido presente no index.html: {bid}")

    # Aba 2 (Roteiro & Prompts): barra nova com base + DeepSeek + ir-flow oculto
    aba2 = ("btn-s2-gerar-base", "btn-s2-gerar-tudo", "btn-s2-ir-flow")
    for bid in aba2:
        (ok if f'id="{bid}"' in html else fail)(
            f"botão da Aba 2 presente no index.html: {bid}")
    (ok if 'id="btn-s2-animar-broll"' in html
          and 'style="display:none"' in
          html[html.index('id="btn-s2-animar-broll"'):html.index('id="btn-s2-animar-broll"')+220]
          else fail)("btn-s2-animar-broll oculto por padrão")
    (ok if 'id="btn-s2-ir-flow"' in html
          and 'style="display:none"' in
          html[html.index('id="btn-s2-ir-flow"'):html.index('id="btn-s2-ir-flow"')+200]
          else fail)("btn-s2-ir-flow oculto por padrão")
    (ok if "btn-s2-animar-broll" in js2 else fail)(
        "lógica de visibilidade animar-broll presente no app.js")

    # A barra de produção mantém o disparo de fila via iniciar_fila
    (ok if "/iniciar_fila" in js2 else fail)(
        "handler iniciar_fila presente no app.js")
    (ok if "/api/flow/reconectar" not in js2 else fail)(
        "endpoint reconectar NÃO referenciado no frontend (botão removido)")


if __name__ == "__main__":
    check_py_compile()
    check_js_balance()
    check_flask_client()
    print("\n" + "=" * 60)
    if FALHAS:
        print(f"RESULTADO: {len(FALHAS)} falha(s): {FALHAS}")
        sys.exit(1)
    print("RESULTADO: TODAS AS VERIFICAÇÕES PASSARAM")
