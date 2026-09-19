# -*- coding: utf-8 -*-
"""
tests/test_reprovisionar_retorno_canvas.py
Valida que _reprovisionar_personagem_apos_rotacao navega de volta ao canvas
do projeto (/project/<id>) e aguarda o editor div.ProseMirror quando
criar_personagem_flow falha (FlowCharacterCreationError ou erro genérico),
sem deixar a aba travada em /character/<id>.
"""
import unittest
from unittest import mock
from pathlib import Path
import services.playwright_flow as pf
from services.playwright_flow import PlaywrightCDPWorker, FlowCharacterCreationError


class TestReprovisionarRetornoCanvas(unittest.TestCase):

    def setUp(self):
        self.worker = PlaywrightCDPWorker(port=9222)
        self.worker.current_project_id = "tomatinho"
        self.mock_page = mock.MagicMock()
        self.worker.page = self.mock_page

    def _patches_personagem(self):
        return [
            mock.patch("services.scene_plan_service.carregar_scene_plan",
                       return_value={"cenas": [{"id": 1, "tipo": "broll", "uses_character": True}]}),
            mock.patch.object(self.worker, "_cenas_usam_personagem", return_value=True),
            mock.patch("services.character_service.validar_personagem_do_projeto",
                       return_value=({"nome": "Marcos"}, "")),
            mock.patch("services.character_service.resolver_imagem_avatar_projeto",
                       return_value=str(Path(__file__).resolve())),  # arquivo existente
        ]

    def test_flow_creation_error_retorna_ao_canvas(self):
        """Quando criar_personagem_flow falha com FlowCharacterCreationError em /character/<id>,

        page.goto deve ser chamado com a URL base do canvas (/project/<id>) e aguardar ProseMirror.
        """
        self.mock_page.url = "https://flow.google.com/project/92c37373-30db-4808-adf1-06bf6c9916bb/character/2cd46996-d6e3-49ec-acf5-e2b11ac982b0"

        patches = self._patches_personagem()
        with patches[0], patches[1], patches[2], patches[3]:
            with mock.patch.object(pf, "criar_personagem_flow",
                                  side_effect=FlowCharacterCreationError("Passo 8 falhou")):
                ok = self.worker._reprovisionar_personagem_apos_rotacao(projeto_id="tomatinho", forcar_criacao=True)
                self.assertFalse(ok)

                # Verifica chamada de retorno ao canvas
                self.mock_page.goto.assert_called_once_with(
                    "https://flow.google.com/project/92c37373-30db-4808-adf1-06bf6c9916bb",
                    timeout=15000
                )
                self.mock_page.wait_for_selector.assert_called_once_with(
                    'div.ProseMirror[contenteditable="true"]',
                    state="visible",
                    timeout=8000
                )

    def test_erro_na_navegacao_de_recuperacao_nao_mascara_falha(self):
        """Se a navegação de recuperação ao canvas falhar (ex: timeout), o erro não deve

        mascarar a falha original nem interromper a execução.
        """
        self.mock_page.url = "https://flow.google.com/project/92c37373-30db-4808-adf1-06bf6c9916bb/character/2cd46996-d6e3-49ec-acf5-e2b11ac982b0"
        self.mock_page.goto.side_effect = RuntimeError("Erro simulado de rede na recuperação")

        patches = self._patches_personagem()
        with patches[0], patches[1], patches[2], patches[3]:
            with mock.patch.object(pf, "criar_personagem_flow",
                                  side_effect=FlowCharacterCreationError("Passo 8 falhou")):
                ok = self.worker._reprovisionar_personagem_apos_rotacao(projeto_id="tomatinho", forcar_criacao=True)
                self.assertFalse(ok)
                self.mock_page.goto.assert_called_once()

    def test_erro_generico_retorna_ao_canvas(self):
        """Falhas genéricas em criar_personagem_flow também acionam o retorno ao canvas."""
        self.mock_page.url = "https://flow.google.com/project/92c37373-30db-4808-adf1-06bf6c9916bb/character/2cd46996-d6e3-49ec-acf5-e2b11ac982b0"

        patches = self._patches_personagem()
        with patches[0], patches[1], patches[2], patches[3]:
            with mock.patch.object(pf, "criar_personagem_flow",
                                  side_effect=RuntimeError("Timeout desconhecido")):
                ok = self.worker._reprovisionar_personagem_apos_rotacao(projeto_id="tomatinho", forcar_criacao=True)
                self.assertFalse(ok)
                self.mock_page.goto.assert_called_once_with(
                    "https://flow.google.com/project/92c37373-30db-4808-adf1-06bf6c9916bb",
                    timeout=15000
                )


if __name__ == "__main__":
    unittest.main()
