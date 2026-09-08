# -*- coding: utf-8 -*-
"""Valida as 4 correções do failover de contas em services/playwright_flow.py."""
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import playwright_flow as pf
from services.playwright_flow import PlaywrightCDPWorker


def _worker(**kw):
    w = PlaywrightCDPWorker.__new__(PlaywrightCDPWorker)
    w.port = 9222
    w.page = None
    w.browser = None
    w.context = None
    w.playwright = None
    w.current_project_id = ""
    w.is_fallback_active = False
    w.current_model = "Nano Banana 2"
    w._project_url_saved = False
    for k, v in kw.items():
        setattr(w, k, v)
    return w


def test_c1_frases_credito_e_retorno():
    src = (ROOT / "services" / "playwright_flow.py").read_text(encoding="utf-8")
    for frase in [
        "you've reached your daily limit", "insufficient credits", "quota exceeded",
        "créditos insuficientes", "limite diário", "out of credits",
        "no credits remaining", "not enough compute credits", "créditos esgotados",
        "upgrade to continue", "upgrade your plan", "ran out of credits", "credit limit",
    ]:
        assert frase in src, f"frase ausente no código: {frase}"

    pagina_mock = mock.Mock()
    pagina_mock.evaluate.return_value = "sorry, out of credits for today"
    w = _worker(page=pagina_mock)
    with mock.patch.object(pf.PlaywrightCDPWorker, "_rotacionar_conta") as m_rot:
        ret = w._detectar_erro_ou_limite_modelo()
        assert ret == "credito_esgotado", f"retorno inesperado: {ret!r}"
        m_rot.assert_called_once()

    pagina_mock2 = mock.Mock()
    pagina_mock2.evaluate.side_effect = [
        "normal page content",       # 1º evaluate (texto_pagina / frases_credito)
        "limite de geração",          # 2º evaluate (indicators + toasts JS)
    ]
    w2 = _worker(page=pagina_mock2)
    with mock.patch.object(pf.PlaywrightCDPWorker, "_rotacionar_conta") as m_rot2:
        ret2 = w2._detectar_erro_ou_limite_modelo()
        assert ret2 == "credito_esgotado", f"toast debe devolver código canónico, recibido: {ret2!r}"
        m_rot2.assert_called_once()

    # BURACO 1 — el toast en modo VÍDEO DEBE setear _fallback_video_para_imagem y
    # NUNCA rotar la cuenta (antes devolvía el string crudo y la flag quedaba False).
    pagina_mock3 = mock.Mock()
    pagina_mock3.evaluate.side_effect = [
        "normal page content",
        "unable to generate image",
    ]
    w3 = _worker(page=pagina_mock3)
    with mock.patch.object(pf.PlaywrightCDPWorker, "_rotacionar_conta") as m_rot3:
        ret3 = w3._detectar_erro_ou_limite_modelo(video_mode=True)
        assert ret3 == "credito_esgotado_video", f"toast en modo vídeo: {ret3!r}"
        assert w3._fallback_video_para_imagem is True, "flag DEBE setearse en el camino toast"
        m_rot3.assert_not_called()
    print("PASS C1: frases expandidas + retorno 'credito_esgotado' en cualquier variación")
    print("PASS BURACO 1: toast/seletor JS setea _fallback_video_para_imagem (video) y rota (imagen)")


def test_c3_rotacionar_conta_reconecta():
    with tempfile.TemporaryDirectory() as tmp:
        acc_file = Path(tmp) / "flow_accounts.json"
        acc_file.write_text(json.dumps({"contas": [
            {"nome": "A", "ativa": True, "creditos_esgotados": False},
            {"nome": "B", "ativa": False, "creditos_esgotados": False},
        ]}), encoding="utf-8")

        page_mock = mock.Mock()
        page_mock.url = "https://labs.google/fx/tools/flow/project/abc"
        w = _worker(page=page_mock, current_project_id="ProjX")
        calls = {"iniciar": 0}

        def _fake_iniciar_sessao():
            calls["iniciar"] += 1
            w.browser = mock.Mock()
            w.context = mock.Mock()
            w.page = page_mock
            return True, "ok"

        orig_read = pf.Path.read_text
        orig_write = pf.Path.write_text
        orig_exists = pf.Path.exists

        def _fake_exists(self):
            if str(self) == "config/flow_accounts.json":
                return True
            return orig_exists(self)

        def _fake_read_text(self, *a, **k):
            if str(self) == "config/flow_accounts.json":
                return acc_file.read_text(encoding="utf-8")
            return orig_read(self, *a, **k)

        def _fake_write_text(self, data, *a, **k):
            if str(self) == "config/flow_accounts.json":
                acc_file.write_text(data, encoding="utf-8")
                return
            return orig_write(self, data, *a, **k)

        with mock.patch.object(pf.PlaywrightCDPWorker, "_encerrar_sessao"), \
             mock.patch.object(pf.PlaywrightCDPWorker, "_iniciar_sessao_thread", side_effect=_fake_iniciar_sessao), \
             mock.patch.object(pf, "ensure_chrome_cdp", return_value=(True, "ok")) as m_cdp, \
             mock.patch.object(pf, "carregar_projeto_flow_url", return_value="https://labs.google/fx/project/salvo"), \
             mock.patch.object(pf.Path, "exists", _fake_exists), \
             mock.patch.object(pf.Path, "read_text", _fake_read_text), \
             mock.patch.object(pf.Path, "write_text", _fake_write_text):
            w._rotacionar_conta()

        m_cdp.assert_called_once()
        assert m_cdp.call_args.kwargs.get("force_restart") is True, "deve reiniciar Chrome (force_restart)"
        assert calls["iniciar"] == 1, "deve reconectar o Playwright após o restart"
        assert page_mock.goto.called, "deve navegar à URL salva do projeto"
        page_mock.wait_for_timeout.assert_called()
        print("PASS C3: rotação reinicia Chrome, reconecta Playwright e navega à URL salva")


def test_c2_deteccao_loop_3x():
    src = (ROOT / "services" / "playwright_flow.py").read_text(encoding="utf-8")
    assert "self.page.wait_for_timeout(4000)" in src, "wait inicial deve ser 4000ms"
    assert "for _tent_detect in range(3):" in src, "loop de 3 tentativas ausente"
    # A chamada real passa video_mode (linha ~2389); a forma sem args nunca existiu.
    assert "err_limite = self._detectar_erro_ou_limite_modelo(video_mode=video_mode)" in src
    assert "self.page.wait_for_timeout(2000)" in src
    print("PASS C2: detecção pós-envio com wait 4000ms + até 3 tentativas (2s entre elas)")


def test_c4_nao_marca_erro_ao_recolocar():
    src = (ROOT / "services" / "playwright_flow.py").read_text(encoding="utf-8")
    assert "cenas_a_processar.insert(0, cena)" in src, "recoloca no INÍCIO (insert 0)"
    assert "status\": scene_plan_svc.STATUS_PENDENTE" in src
    assert "recolocada_por_credito" in src
    assert "elif recolocada_por_credito:" in src
    assert "NÃO registra erro. O loop externo continua sem break." in src
    assert "cenas_a_processar.insert(idx, cena)" not in src, "insert antigo no idx removido"
    print("PASS C4: recolocação usa PENDENTE + insert(0) e evita bloco de STATUS_ERRO")


if __name__ == "__main__":
    test_c1_frases_credito_e_retorno()
    test_c2_deteccao_loop_3x()
    test_c3_rotacionar_conta_reconecta()
    test_c4_nao_marca_erro_ao_recolocar()
    print("\nRESULTADO: CORREÇÕES 1-4 VALIDADAS")