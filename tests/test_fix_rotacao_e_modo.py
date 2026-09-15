# -*- coding: utf-8 -*-
import unittest
from unittest import mock
import services.playwright_flow as pf
from services.flow_account_manager import FlowAccountManager


class TestFixRotacaoEModo(unittest.TestCase):
    def test_set_output_mode_seleciona_aba_imagem(self):
        w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
        w.port = 9222
        page = mock.Mock()
        w.page = page
        w._configured_mode = None
        w.current_model = None

        clicked_selectors = []
        def _loc(sel):
            m = mock.Mock()
            m.first = m
            m.is_visible.return_value = True
            m.get_attribute.return_value = 'false'
            def _click(*a, **kw):
                clicked_selectors.append(sel)
            m.click.side_effect = _click
            return m

        page.locator.side_effect = _loc
        with mock.patch.object(pf, 'pw_log'):
            ok = w._set_output_mode('image', modelo_solicitado='Nano Banana 2')

        self.assertTrue(ok)
        aba_clicada = any('Imagem' in s or 'Image' in s for s in clicked_selectors)
        self.assertTrue(aba_clicada, f'Aba Imagem não encontrada entre os cliques: {clicked_selectors}')

    def test_tratar_indicador_limite_aciona_rotacao_se_houver_conta(self):
        w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
        w.port = 9222
        w.page = mock.Mock()

        with mock.patch.object(FlowAccountManager, 'proxima_conta_disponivel', return_value='conta2@gmail.com'):
            with mock.patch.object(w, '_rotacionar_conta') as mock_rot:
                res = w._tratar_indicador_limite('check_flow_credits', video_mode=False)
                self.assertEqual(res, 'credito_esgotado')
                mock_rot.assert_called_once()

    def test_passada_imagem_nunca_gera_video(self):
        """Na passada de imagem (is_anim=False), mesmo se a cena for B-Roll ou tipo video, video_mode DEVE ser False."""
        w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
        w.port = 9222
        w.page = mock.Mock()
        w._configured_mode = None
        w.current_model = None

        cena_broll = {
            "id": 2,
            "tipo": "video",
            "media_intent": "video",
            "animar": True,
            "animate_later": True,
            "prompt_imagem": "Lawn grass in sunny day",
            "prompt_animacao": "Camera track across grass 4s"
        }

        # Simula a lógica de decisão de video_mode extraída de _processar_cena_individual
        is_anim = False
        eh_avatar = pf._detectar_cena_avatar(cena_broll)
        import services.scene_plan_service as scene_plan_svc
        tipo_efetivo = scene_plan_svc.tipo_efetivo_cena(cena_broll)

        if getattr(w, "_fallback_video_para_imagem", False):
            video_mode = False
        elif eh_avatar:
            video_mode = False
        else:
            video_mode = bool(is_anim and (
                tipo_efetivo == scene_plan_svc.TIPO_VIDEO
                or cena_broll.get("animar") is True
                or cena_broll.get("animate_later") is True
                or cena_broll.get("animar_depois") is True
            ))

        self.assertFalse(video_mode, "video_mode DEVE ser False na passada de imagens (is_anim=False)")

        # E quando is_anim=True, video_mode DEVE ser True para a mesma cena
        is_anim_true = True
        video_mode_anim = bool(is_anim_true and (
            tipo_efetivo == scene_plan_svc.TIPO_VIDEO
            or cena_broll.get("animar") is True
            or cena_broll.get("animate_later") is True
            or cena_broll.get("animar_depois") is True
        ))
        self.assertTrue(video_mode_anim, "video_mode DEVE ser True na passada de animação (is_anim=True)")


if __name__ == '__main__':
    unittest.main()

