import unittest
from unittest import mock
from pathlib import Path
import json
import tempfile

import services.playwright_flow as pf
from services.playwright_flow import (
    PlaywrightCDPWorker,
    FlowCharacterCreationError,
    TIMEOUT_ESTABILIZACAO_PERSONAGENS_MS,
    INTERVALO_POLL_PERSONAGENS_MS,
)


class TestPreVooEstabilizacaoEBloqueio(unittest.TestCase):
    def setUp(self):
        self.w = PlaywrightCDPWorker(port=9222)
        self.w.current_project_id = "ProjTest"
        self.w.page = mock.MagicMock()

    def _patches_personagem(self):
        from services import scene_plan_service as sps
        from services import character_service as chars
        foto = str(Path(__file__).resolve().parent / "reference_teste.png")
        return [
            mock.patch.object(sps, "carregar_scene_plan",
                              return_value={"cenas": [{"uses_character": True, "scene_type": "avatar_talking"}]}),
            mock.patch.object(chars, "obter_identidade_projeto",
                              return_value={"nome": "Marcos", "flow_character_created": True}),
            mock.patch.object(chars, "resolver_imagem_avatar_projeto", return_value=foto),
            mock.patch.object(pf.Path, "exists", return_value=True),
        ]

    def test_identidade_nao_retorna_true_sem_confirmacao_ao_vivo(self):
        """1. identidade.json com flow_character_created=True NÃO retorna True sem confirmação ao vivo."""
        patches = self._patches_personagem()
        with patches[0], patches[1], patches[2], patches[3], \
             mock.patch.object(self.w, "_aguardar_lista_personagens_estavel", return_value="vazio"):
            self.w.page.locator.return_value.first.is_visible.return_value = True
            res = self.w._verificar_personagem_na_biblioteca("Marcos")
            self.assertNotEqual(res, True)
            self.assertIs(res, False)

    def test_helper_carregando_gera_none_e_sem_criacao(self):
        """2. Helper retornando 'carregando' gera None e nenhuma criação."""
        patches = self._patches_personagem()
        with patches[0], patches[1], patches[2], patches[3], \
             mock.patch.object(self.w, "_aguardar_lista_personagens_estavel", return_value="carregando"), \
             mock.patch.object(pf, "criar_personagem_flow") as m_criar:
            self.w.page.locator.return_value.first.is_visible.return_value = True
            res_verif = self.w._verificar_personagem_na_biblioteca("Marcos")
            self.assertIsNone(res_verif)

            res_rep = self.w._reprovisionar_personagem_apos_rotacao(projeto_id="ProjTest", forcar_criacao=False)
            self.assertFalse(res_rep)
            m_criar.assert_not_called()
            self.assertFalse(self.w._avatar_uploaded)

    def test_helper_vazio_gera_false(self):
        """3. Helper 'vazio' gera False."""
        patches = self._patches_personagem()
        with patches[0], patches[1], patches[2], patches[3], \
             mock.patch.object(self.w, "_aguardar_lista_personagens_estavel", return_value="vazio"):
            self.w.page.locator.return_value.first.is_visible.return_value = True
            res = self.w._verificar_personagem_na_biblioteca("Marcos")
            self.assertIs(res, False)

    def test_passo9_card_persistido_bloqueia_segunda_criacao(self):
        """4. Passo 9 falhando com card_persistido=True bloqueia a segunda criação no pré-voo final."""
        patches = self._patches_personagem()
        with patches[0], patches[1], patches[2], patches[3]:
            # Chamada 1 (forcar_criacao=True): passo 9 falha com card_persistido=True
            with mock.patch.object(pf, "criar_personagem_flow",
                                  side_effect=FlowCharacterCreationError("Passo 9 falhou", card_persistido=True)) as m_criar:
                ok1 = self.w._reprovisionar_personagem_apos_rotacao(projeto_id="ProjTest", forcar_criacao=True)
                self.assertFalse(ok1)
                self.assertTrue(self.w._personagem_card_persistido)
                self.assertFalse(self.w._avatar_uploaded)
                self.assertEqual(m_criar.call_count, 1)

            # Chamada 2 (simulando pré-voo final com forcar_criacao=False):
            with mock.patch.object(self.w, "_verificar_personagem_na_biblioteca", return_value=False), \
                 mock.patch.object(pf, "criar_personagem_flow") as m_criar2:
                ok2 = self.w._reprovisionar_personagem_apos_rotacao(projeto_id="ProjTest", forcar_criacao=False)
                self.assertFalse(ok2)
                m_criar2.assert_not_called()

    def test_rotacionar_conta_chama_upload_avatar_sem_interromper(self):
        """5. _rotacionar_conta chama _upload_avatar_projeto e não interrompe se ele lançar exceção."""
        with tempfile.TemporaryDirectory() as tmpdir:
            acc_file = Path(tmpdir) / "flow_accounts.json"
            acc_data = {
                "contas": [
                    {"id": 1, "nome": "Conta 1", "ativa": True, "creditos_esgotados": False},
                    {"id": 2, "nome": "Conta 2", "ativa": False, "creditos_esgotados": False},
                ]
            }
            acc_file.write_text(json.dumps(acc_data), encoding="utf-8")

            w = PlaywrightCDPWorker(port=9222)
            w.current_project_id = "ProjTest"
            w.page = mock.MagicMock()

            # Mock accounts_path in config/flow_accounts.json
            with mock.patch("services.playwright_flow.Path") as m_path, \
                 mock.patch.object(pf, "ensure_chrome_cdp", return_value=(True, "ok")), \
                 mock.patch.object(PlaywrightCDPWorker, "_encerrar_sessao"), \
                 mock.patch.object(PlaywrightCDPWorker, "_iniciar_sessao_thread", return_value=(True, "ok")), \
                 mock.patch.object(PlaywrightCDPWorker, "_verificar_sessao_google", return_value=True), \
                 mock.patch.object(PlaywrightCDPWorker, "_reprovisionar_personagem_apos_rotacao", return_value=True), \
                 mock.patch.object(PlaywrightCDPWorker, "_upload_avatar_projeto", side_effect=Exception("Erro upload simulado")) as m_up:
                m_path.side_effect = lambda *args: acc_file if any("flow_accounts.json" in str(a) for a in args) else Path(*args)
                w._rotacionar_conta()
                m_up.assert_called_once_with("ProjTest")
                self.assertFalse(w._personagem_card_persistido)


if __name__ == "__main__":
    unittest.main()
