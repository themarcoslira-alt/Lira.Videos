import unittest
from unittest import mock
from pathlib import Path
import json
import tempfile
import contextlib

import services.playwright_flow as pf
from services.playwright_flow import PlaywrightCDPWorker


class TestRotacaoVerificaAntesCriar(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.acc_file = Path(self.tmpdir.name) / "flow_accounts.json"
        acc_data = {
            "contas": [
                {"id": 1, "nome": "Conta 1", "ativa": True, "creditos_esgotados": False},
                {"id": 2, "nome": "Conta 2", "ativa": False, "creditos_esgotados": False},
            ]
        }
        self.acc_file.write_text(json.dumps(acc_data, indent=2), encoding="utf-8")

        self.foto = Path(self.tmpdir.name) / "reference.png"
        self.foto.write_bytes(b"PNG_FAKE_BYTES")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _criar_worker(self):
        w = PlaywrightCDPWorker(port=9222)
        w.current_project_id = "ProjTest"
        w.page = mock.MagicMock()
        w.page.wait_for_timeout = mock.MagicMock()
        w.page.goto = mock.MagicMock()
        return w

    @contextlib.contextmanager
    def _patches_comuns(self, worker):
        from services import scene_plan_service as sps
        from services import character_service as chars

        orig_path = pf.Path

        def _fake_path(*args, **kwargs):
            if args and "flow_accounts.json" in str(args[0]):
                return self.acc_file
            return orig_path(*args, **kwargs)

        with mock.patch("services.playwright_flow.Path", side_effect=_fake_path), \
             mock.patch.object(pf, "ensure_chrome_cdp", return_value=(True, "ok")), \
             mock.patch.object(PlaywrightCDPWorker, "_encerrar_sessao"), \
             mock.patch.object(PlaywrightCDPWorker, "_iniciar_sessao_thread", return_value=(True, "ok")), \
             mock.patch.object(PlaywrightCDPWorker, "_ensure_project_open", return_value=True), \
             mock.patch.object(PlaywrightCDPWorker, "_verificar_sessao_google", return_value=True), \
             mock.patch.object(PlaywrightCDPWorker, "_upload_avatar_projeto"), \
             mock.patch.object(sps, "carregar_scene_plan",
                               return_value={"cenas": [{"uses_character": True, "scene_type": "avatar_talking"}]}), \
             mock.patch.object(chars, "obter_identidade_projeto",
                               return_value={"nome": "Marcos", "flow_character_created": True}), \
             mock.patch.object(chars, "resolver_imagem_avatar_projeto", return_value=str(self.foto)):
            yield

    def test_a_rotacionar_conta_chama_com_forcar_criacao_false(self):
        """(a) Confirma que _rotacionar_conta chama _reprovisionar_personagem_apos_rotacao com forcar_criacao=False."""
        w = self._criar_worker()
        spy = mock.MagicMock(wraps=w._reprovisionar_personagem_apos_rotacao)
        w._reprovisionar_personagem_apos_rotacao = spy

        with self._patches_comuns(w), \
             mock.patch.object(w, "_verificar_personagem_na_biblioteca", return_value=True), \
             mock.patch.object(pf, "criar_personagem_flow") as m_criar:
            w._rotacionar_conta()

            spy.assert_called_once()
            _, kwargs = spy.call_args
            self.assertIn("forcar_criacao", kwargs, "forcar_criacao deve ser passado explicitamente na rotação")
            self.assertIs(kwargs["forcar_criacao"], False, "forcar_criacao deve ser False")

    def test_b_verificacao_true_nao_cria_e_marca_avatar_uploaded(self):
        """(b) Verificação retorna True: criar_personagem_flow NÃO é chamado e _avatar_uploaded fica True."""
        w = self._criar_worker()
        with self._patches_comuns(w), \
             mock.patch.object(w, "_verificar_personagem_na_biblioteca", return_value=True), \
             mock.patch.object(pf, "criar_personagem_flow") as m_criar:
            w._rotacionar_conta()

            m_criar.assert_not_called()
            self.assertTrue(w._avatar_uploaded)

    def test_c_verificacao_false_chama_criar_uma_vez(self):
        """(c) Verificação retorna False: criar_personagem_flow é chamado exatamente 1 vez."""
        w = self._criar_worker()
        with self._patches_comuns(w), \
             mock.patch.object(w, "_verificar_personagem_na_biblioteca", return_value=False), \
             mock.patch.object(pf, "criar_personagem_flow", return_value=True) as m_criar:
            w._rotacionar_conta()

            self.assertEqual(m_criar.call_count, 1)
            self.assertTrue(w._avatar_uploaded)

    def test_d_verificacao_none_persistente_nao_cria_e_avatar_uploaded_false(self):
        """(d) Verificação retorna None de forma persistente: criar_personagem_flow NÃO é chamado e _avatar_uploaded fica False."""
        w = self._criar_worker()
        with self._patches_comuns(w), \
             mock.patch.object(w, "_verificar_personagem_na_biblioteca", return_value=None), \
             mock.patch.object(pf, "criar_personagem_flow") as m_criar:
            w._rotacionar_conta()

            m_criar.assert_not_called()
            self.assertFalse(w._avatar_uploaded)


if __name__ == "__main__":
    unittest.main()
