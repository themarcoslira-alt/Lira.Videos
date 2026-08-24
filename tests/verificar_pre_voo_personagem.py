"""
verificar_pre_voo_personagem.py — Validação das Tarefas A+B (pré-voo @Marcos +
respiro fixo entre cenas). Standalone, NÃO abre Chrome real (CDP mockado).

    .venv\\Scripts\\python.exe tests\\verificar_pre_voo_personagem.py
"""
import json
import py_compile
import re
import shutil
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

FALHAS = []


def ok(nome, detalhe=""):
    print(f"  [OK]   {nome} {detalhe}".rstrip())


def fail(nome, detalhe=""):
    FALHAS.append(nome)
    print(f"  [FAIL] {nome} :: {detalhe}")


SRC = ""


def check_py_compile():
    global SRC
    print("\n[1] py_compile")
    try:
        py_compile.compile(str(BASE / "services" / "playwright_flow.py"), doraise=True)
        ok("services/playwright_flow.py")
    except Exception as e:
        fail("services/playwright_flow.py", str(e)[:300])
    SRC = (BASE / "services" / "playwright_flow.py").read_text(encoding="utf-8")


def check_intervalo():
    print("\n[2] Respiro inter-cenas (Tarefa B)")
    m = re.search(r"^INTERVALO_ENTRE_CENAS_S\s*=\s*([\d.]+)", SRC, re.M)
    if not m:
        fail("constante INTERVALO_ENTRE_CENAS_S ausente")
        return
    val = float(m.group(1))
    (ok if 5.0 <= val <= 10.0 else fail)(f"INTERVALO_ENTRE_CENAS_S={val} (faixa 5-10s)")

    uso = "time.sleep(INTERVALO_ENTRE_CENAS_S)" in SRC
    cond = re.search(r"if idx < len\(cenas_a_processar\):\s*\n\s*time\.sleep\(INTERVALO_ENTRE_CENAS_S\)", SRC)
    (ok if uso and cond else fail)("sleep inter-cenas usa a constante e só ENTRE cenas")

    # Nenhum time.sleep com literal > 10s em todo o arquivo
    grandes = [(i + 1, l.strip()) for i, l in enumerate(SRC.splitlines())
               if re.search(r"time\.sleep\((?:1[1-9]|[2-9]\d|\d{3,})(?:\.0)?\)", l)]
    (ok if not grandes else fail)("nenhum time.sleep > 10s no arquivo", str(grandes[:3]))

    # Nenhum wait_for_timeout >= 10000ms (polling leve permanece em 1500ms)
    wgrandes = [(i + 1, l.strip()) for i, l in enumerate(SRC.splitlines())
                if re.search(r"wait_for_timeout\(\d{5,}\)", l)]
    (ok if not wgrandes else fail)("nenhum wait_for_timeout >= 10000ms", str(wgrandes[:3]))

    # Restrições preservadas: timeout 300s e polling leve de 1500ms
    (ok if "timeout_s = 300" in SRC else fail)("timeout_s = 300 preservado")
    (ok if "self.page.wait_for_timeout(1500)" in SRC else fail)("polling leve 1500ms preservado")


def check_unit_helpers():
    print("\n[3] Helpers do pré-voo (unitários, sem UI)")
    import services.playwright_flow as pf

    f = pf.PlaywrightCDPWorker._cenas_usam_personagem
    casos = [
        ([], False),
        ([{"uses_character": False, "scene_type": "broll_action"}], False),
        ([{"uses_character": True}], True),
        ([{"character_ref": "@Marcos"}], True),
        ([{"scene_type": "avatar_action"}], True),
        ([{"scene_type": "broll_action"}, {"uses_character": True}], True),
    ]
    for cenas, esperado in casos:
        got = f(cenas)
        (ok if got == esperado else fail)(f"_cenas_usam_personagem({json.dumps(cenas)[:60]})", f"esperado={esperado} got={got}")

    w = pf.PlaywrightCDPWorker(port=9222)
    w.page = None
    (ok if w._verificar_personagem_na_biblioteca("Marcos") is False else fail)(
        "_verificar_personagem_na_biblioteca com page=None -> False sem crash")

    # Estrutura: reset da flag por fila + bloco de pausa graciosa presentes
    (ok if "self._flow_character_library_empty = False" in SRC else fail)(
        "reset de _flow_character_library_empty por fila presente")
    (ok if "[PAUSA PRE-VOU]" not in SRC and "PAUSA PRE-VOO]" in SRC else fail)(
        "bloco de pausa pré-voo presente")
    (ok if '"pause_reason": getattr(worker' in SRC else fail)(
        "get_status expõe pause_reason")


def check_flask_pausa_retomada():
    print("\n[4] Flask — pausa pré-voo (simulada) e retomada pela rota real")
    import services.playwright_flow as pf
    from services.scene_plan_service import SCENE_PLAN_FILE  # noqa: F401
    from config import PROJETOS_DIR

    # Escolhe um projeto real que tenha lira_scene_plan.json
    projeto = None
    for d in sorted(PROJETOS_DIR.iterdir()):
        if (d / "lira_scene_plan.json").exists():
            projeto = d.name
            break
    if not projeto:
        fail("nenhum projeto com lira_scene_plan.json para o teste de fila")
        return

    worker = pf.FlowQueueWorker.get_worker()

    # --- 4a. Pausa simulada: fake do handler grava pause_reason e sai ---
    # _handle_run_queue vive em PlaywrightCDPWorker; start_worker o resolve
    # via instância do worker singleton.
    original_handler = pf.PlaywrightCDPWorker._handle_run_queue

    def _fake_handler(self, projeto_id, scene_ids, modo):
        self.is_running_queue = True
        time.sleep(0.2)
        self.last_queue_pause_reason = "PRE-VOO PAUSADO: '@Marcos' NAO consta na aba Personagens."
        self.is_running_queue = False

    pf.PlaywrightCDPWorker._handle_run_queue = _fake_handler
    try:
        r1 = pf.FlowQueueWorker.start_worker(projeto, modo="imagem")
        (ok if r1 is True else fail)("start_worker dispara a fila (1ª vez)")
        time.sleep(0.6)
        st = pf.FlowQueueWorker.get_status()
        cond = (st.get("rodando_fila") is False
                and "PRE-VOO PAUSADO" in str(st.get("pause_reason", "")))
        (ok if cond else fail)("após pausa: rodando_fila=False + pause_reason exposto",
                               f"status={st}")
    finally:
        pf.PlaywrightCDPWorker._handle_run_queue = original_handler

    # --- 4b. Retomada REAL pela rota Flask (CDP mockado como offline) ---
    pf.ensure_chrome_cdp = lambda *a, **k: (False, "simulado offline")
    fake_proj = None
    try:
        # Projeto DESCARTÁVEL com lira_scene_plan mínimo (nunca toca projeto real)
        fake_proj = PROJETOS_DIR / "zzz_pre_voo_tmp"
        fake_proj.mkdir(parents=True, exist_ok=True)
        (fake_proj / "lira_scene_plan.json").write_text(json.dumps({
            "cenas": [{"id": 1, "tempo_inicio": 0.0, "tempo_fim": 5.0,
                       "tipo": "image", "texto": "teste", "status": "PENDENTE"}]
        }), encoding="utf-8")

        import app_web
        app = app_web.app
        app.config["TESTING"] = True
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["authenticated"] = True

        url = f"/api/v2/producao/{fake_proj.name}/iniciar_fila"
        r = client.post(url, data=json.dumps({}), content_type="application/json")
        body = r.get_json(silent=True) or {}
        (ok if r.status_code == 200 and body.get("success") else fail)(
            "retomada: iniciar_fila aceita novo start_worker",
            f"status={r.status_code} body={str(body)[:200]}")

        # A thread real falha GRACIOSAMENTE no CDP mockado e libera o worker.
        for _ in range(50):
            if not pf.FlowQueueWorker.get_status()["rodando_fila"]:
                break
            time.sleep(0.3)
        (ok if not pf.FlowQueueWorker.get_status()["rodando_fila"] else fail)(
            "worker liberado após falha graciosa de CDP (pronto p/ nova retomada)")

        # Nova retomada imediata também aceita (fila não fica travada)
        r2 = client.post(url, data=json.dumps({}), content_type="application/json")
        body2 = r2.get_json(silent=True) or {}
        (ok if r2.status_code == 200 and body2.get("success") else fail)(
            "segunda retomada consecutiva aceita",
            f"status={r2.status_code} body={str(body2)[:200]}")
    except Exception as e:
        fail("retomada via Flask", repr(e)[:200])
    finally:
        del pf.ensure_chrome_cdp  # restaura o original do módulo
        if fake_proj:
            shutil.rmtree(fake_proj, ignore_errors=True)


if __name__ == "__main__":
    check_py_compile()
    check_intervalo()
    check_unit_helpers()
    check_flask_pausa_retomada()
    print("\n" + "=" * 60)
    if FALHAS:
        print(f"RESULTADO: {len(FALHAS)} falha(s): {FALHAS}")
        sys.exit(1)
    print("RESULTADO: TODAS AS VERIFICAÇÕES PASSARAM")


    # VALIDAÇÃO: chamada oficial a _selecionar_referencia_flow presente para anexação de Character Entity
    chamadas = len(re.findall(r"self\._selecionar_referencia_flow\(", SRC))
    (ok if chamadas > 0 else fail)(
        "anexação oficial de Character Entity ativa (_selecionar_referencia_flow presente)",
        f"chamadas encontradas={chamadas}")
