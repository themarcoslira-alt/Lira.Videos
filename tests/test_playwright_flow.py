"""
tests/test_playwright_flow.py — Testes Unitários da Automação Playwright Flow (CDP)
"""

import os
import sys
import json
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Adiciona raiz do projeto
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.playwright_flow import (
    FlowSessionManager,
    FlowQueueWorker,
)
import services.scene_plan_service as scene_plan_svc
from config import PROJETOS_DIR


class TestPlaywrightFlow(unittest.TestCase):
    def setUp(self):
        self.test_proj = "test_flow_proj"
        self.proj_dir = PROJETOS_DIR / self.test_proj
        self.proj_dir.mkdir(parents=True, exist_ok=True)
        
        cenas_test = [
            {
                "id": 1,
                "texto": "Cena 1 teste",
                "tempo_inicio": 0.0,
                "tempo_fim": 3.0,
                "tipo": "image",
                "prompt_imagem": "Realistic photo of a green garden",
                "prompt_animacao": "",
                "arquivo_midia": "",
                "status": scene_plan_svc.STATUS_PENDENTE,
            },
            {
                "id": 2,
                "texto": "Cena 2 teste com @personagem",
                "tempo_inicio": 3.0,
                "tempo_fim": 6.0,
                "tipo": "image",
                "prompt_imagem": "Close-up of a gardener working in the backyard",
                "prompt_animacao": "",
                "arquivo_midia": "",
                "status": scene_plan_svc.STATUS_PENDENTE,
            },
            {
                "id": 3,
                "texto": "Cena 3 vídeo",
                "tempo_inicio": 6.0,
                "tempo_fim": 10.0,
                "tipo": "video",
                "prompt_imagem": "Cinematic shot of flying drone over grass",
                "prompt_animacao": "Smooth forward motion cinematic video",
                "arquivo_midia": "",
                "status": scene_plan_svc.STATUS_PENDENTE,
            }
        ]
        
        plan = {
            "projeto": self.test_proj,
            "total": 3,
            "cenas": cenas_test
        }
        scene_plan_svc.salvar_scene_plan(self.test_proj, plan)

    def tearDown(self):
        plan_path = self.proj_dir / "lira_scene_plan.json"
        if plan_path.exists():
            plan_path.unlink()
        midias_dir = self.proj_dir / "midias"
        if midias_dir.exists():
            for f in midias_dir.glob("*"):
                f.unlink()
            midias_dir.rmdir()
        if self.proj_dir.exists():
            try:
                self.proj_dir.rmdir()
            except Exception:
                pass

    def test_session_manager_methods(self):
        is_act = FlowSessionManager.is_active()
        self.assertIsInstance(is_act, bool)

    def test_queue_worker_methods(self):
        is_run = FlowQueueWorker.is_running()
        self.assertIsInstance(is_run, bool)
        cena_atv = FlowQueueWorker.get_cena_ativa()
        self.assertIsInstance(cena_atv, dict)

    def test_character_detection(self):
        self.assertTrue(scene_plan_svc._cena_tem_personagem("Homem caminhando"))
        self.assertTrue(scene_plan_svc._cena_tem_personagem("Close-up gardener watering plants"))
        self.assertTrue(scene_plan_svc._cena_tem_personagem("Foto com @personagem"))
        self.assertFalse(scene_plan_svc._cena_tem_personagem("Lawn grass texture in sunny day"))

    def test_media_name_formatting(self):
        cena = {
            "id": 1,
            "tempo_inicio": 0.0,
            "prompt_imagem": "Extreme close-up of a real spurge weed",
            "texto": "Spurge weed in backyard"
        }
        fname_jpg = scene_plan_svc._nome_arquivo_cena(cena, ".jpg")
        self.assertTrue(fname_jpg.startswith("001_[00-00]_extreme_close_up"))
        self.assertTrue(fname_jpg.endswith(".jpg"))

        fname_mp4 = scene_plan_svc._nome_arquivo_cena(cena, ".mp4")
        self.assertTrue(fname_mp4.endswith(".mp4"))


class TestDetectarErroLimiteToast(unittest.TestCase):
    """BURACO 1 — alertas do Google Flow por TOAST/SELETOR JS (bloque 2 de
    _detectar_erro_ou_limite_modelo) devem setear _fallback_video_para_imagem no modo
    vídeo e retornar códigos canónicos (nunca o string cru do indicador)."""

    from services.playwright_flow import PlaywrightCDPWorker

    def _worker(self, **kw):
        pagina = MagicMock()
        w = self.PlaywrightCDPWorker.__new__(self.PlaywrightCDPWorker)
        w.port = 9222
        w.page = pagina
        w.browser = None
        w.context = None
        w.playwright = None
        w.current_project_id = ""
        w.is_fallback_active = False
        w.current_model = "Nano Banana 2"
        w._project_url_saved = False
        for k, v in kw.items():
            setattr(w, k, v)
        return w, pagina

    def test_toast_modo_video_setea_flag_e_non_rota(self):
        import services.playwright_flow as pf
        w, pagina = self._worker()
        pagina.evaluate.side_effect = [
            "contenido normal sin señales",   # 1ª evaluate (body.innerText)
            "limite de geração",              # 2ª evaluate (indicators + toast JS)
        ]
        with patch.object(self.PlaywrightCDPWorker, "_rotacionar_conta") as m_rot:
            ret = w._detectar_erro_ou_limite_modelo(video_mode=True)
        self.assertEqual(ret, "credito_esgotado_video")
        self.assertTrue(w._fallback_video_para_imagem,
                        "toast en modo vídeo DEBE setear _fallback_video_para_imagem")
        m_rot.assert_not_called()

    def test_toast_modo_imagen_rota_cuenta(self):
        import services.playwright_flow as pf
        w, pagina = self._worker()
        pagina.evaluate.side_effect = [
            "contenido normal sin señales",
            "daily limit reached, unable to generate",
        ]
        with patch.object(self.PlaywrightCDPWorker, "_rotacionar_conta") as m_rot:
            ret = w._detectar_erro_ou_limite_modelo(video_mode=False)
        self.assertEqual(ret, "credito_esgotado")
        m_rot.assert_called_once()

    def test_sin_señal_retorna_none(self):
        w, pagina = self._worker()
        pagina.evaluate.side_effect = [
            "contenido normal",
            None,   # evaluate JS sin indicador ni toast
        ]
        ret = w._detectar_erro_ou_limite_modelo(video_mode=True)
        self.assertIsNone(ret)
        self.assertFalse(getattr(w, "_fallback_video_para_imagem", False))

    def test_frases_credito_continuam_seteando_flag(self):
        # Regresión: o camino frases_credito (bloque 1) segue ativando a flag.
        w, pagina = self._worker()
        pagina.evaluate.side_effect = [
            "sorry, you've reached your daily limit for videos",
            None,
        ]
        ret = w._detectar_erro_ou_limite_modelo(video_mode=True)
        self.assertEqual(ret, "credito_esgotado_video")
        self.assertTrue(w._fallback_video_para_imagem)

    def test_camino_toast_integrador_cola_normaliza_indicador_cru(self):
        # BURACO 2 (defensivo): si un retorno no canónico llega al caller, se normaliza
        # y la flag se setea — la señal NUNCA se pierde en silencio.
        src = (Path(__file__).parent.parent / "services" / "playwright_flow.py").read_text(encoding="utf-8")
        self.assertIn("Indicador de limite NO canónico normalizado", src)
        self.assertIn("self._fallback_video_para_imagem = True", src)
        self.assertIn("err_limite = \"credito_esgotado_video\"", src)


if __name__ == "__main__":
    unittest.main()
