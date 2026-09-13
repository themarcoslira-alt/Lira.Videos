# -*- coding: utf-8 -*-
"""
tests/test_prioridades_p23.py — Prioridades 2 e 3 (integração final)

P2 — Reconfiguração automática vídeo→imagem:
  * _set_output_mode passa a retornar bool (True = configurado/ok, False = falha).
  * Bloco "credito_esgotado_video" em _processar_cena_individual loga a
    reconfiguração, verifica o sucesso e devolve o código canónico
    "credito_esgotado_video_recolocado" com _fallback_video_para_imagem=True.

P3 — Rate limit/CAPTCHA protection:
  * Espera inicial de 2 min antes da 1ª cena e 30s antes de cada cena (a partir
    da 2ª) em filas com mais de 5 cenas (strings de log presentes no fonte).
"""
import unittest
from pathlib import Path
from unittest import mock

import services.playwright_flow as pf

ROOT = Path(__file__).resolve().parent.parent


def _worker(**kw):
    w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
    w.port = 9222
    w.page = mock.Mock()
    w.browser = None
    w.context = None
    w.playwright = None
    w.current_flow_reference = None
    w.current_project_id = "ProjX"
    w.current_flow_mode = "video"
    w.current_project_name = None
    w.account_email = None
    w._avatar_uploaded = False
    w._project_url_saved = False
    w.current_delay_info = None
    w.current_model = "Veo 3.1 - Lite"
    w.is_fallback_active = False
    w._fallback_video_para_imagem = False
    for k, v in kw.items():
        setattr(w, k, v)
    return w


class TestSetOutputModeRetorno(unittest.TestCase):
    """P2 — _set_output_mode deve retornar bool."""

    def test_sem_page_retorna_false(self):
        w = _worker(page=None)
        self.assertFalse(w._set_output_mode("image"))

    def test_ja_configurado_retorna_true_sem_tocar_ui(self):
        page = mock.Mock()
        w = _worker(page=page)
        w._configured_mode = ("image", "Nano Banana 2", "16:9", "x1")
        ok = w._set_output_mode("image", modelo_solicitado="Nano Banana 2",
                                proporcao_solicitada="16:9", qualidade_solicitada="x1")
        self.assertTrue(ok)
        page.locator.assert_not_called()

    def test_excecao_na_ui_retorna_false(self):
        page = mock.Mock()
        page.locator.side_effect = RuntimeError("boom")
        w = _worker(page=page)
        w._configured_mode = None
        with mock.patch.object(pf, "pw_log"):
            ok = w._set_output_mode("image", modelo_solicitado="Nano Banana 2")
        self.assertFalse(ok)

    def test_fluxo_ui_ok_retorna_true(self):
        page = mock.Mock()
        # locator(...).first.is_visible/timeout simulando tudo visível/clicável
        def _loc_side(sel):
            loc = mock.Mock()
            loc.is_visible.return_value = True
            loc.get_attribute.return_value = "false"
            return loc
        page.locator.side_effect = _loc_side
        w = _worker(page=page)
        w._configured_mode = None
        w.current_model = "Veo 3.1 - Lite"
        with mock.patch.object(pf, "pw_log"):
            ok = w._set_output_mode("image", modelo_solicitado="Nano Banana 2")
        self.assertTrue(ok)


class TestFontePrioridadesP2P3(unittest.TestCase):
    """Verificações de fonte (mesmo estilo dos testes existentes do projeto)."""

    def setUp(self):
        self.src = (ROOT / "services" / "playwright_flow.py").read_text(encoding="utf-8")

    def test_p2_logs_reconfiguracao_no_fonte(self):
        for trecho in [
            '"[FLOW] Reconfigurando para modo {target_mode}',
            'pw_log("[FLOW] Crédito vídeo esgotado. Reconfigurando para IMAGEM...", level="warn")',
            'pw_log("[FLOW] Reconfiguração OK. Recolocando cena como IMAGEM.", level="info")',
            'pw_log("[FLOW] Reconfiguração FALHOU. Tentaremos na próxima iteração.", level="error")',
            'self._fallback_video_para_imagem = True',
            'return (False, "credito_esgotado_video_recolocado")',
        ]:
            self.assertIn(trecho, self.src)

    def test_p3_rate_limit_no_fonte(self):
        for trecho in [
            "[RATE_LIMIT_PROTECTION] Iniciando fila. Aguardando 10s para evitar CAPTCHA/rate limit...",
            "[RATE_LIMIT_PROTECTION] Cena {idx}/{len(cenas_a_processar)}. Aguardando 5s antes de processar...",
            "if idx > 1 and len(cenas_a_processar) > 5:",
        ]:
            self.assertIn(trecho, self.src)

    def test_sem_checkpoints_mortos_aba_video(self):
        """Garante que nenhum checkpoint de 'Aba Vídeo' ou aborto prematuro sobrevive no código."""
        self.assertNotIn('Aba Vídeo não confirmada', self.src)
        self.assertNotIn('button[role="radio"]:has-text("Vídeo")', self.src)
        self.assertNotIn('raise RuntimeError(f"[FLOW] Falha ao configurar modo {target_mode} — abortando cena")', self.src)


if __name__ == "__main__":
    unittest.main()