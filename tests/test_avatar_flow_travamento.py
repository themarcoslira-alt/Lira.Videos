# -*- coding: utf-8 -*-
"""
Regressão — travamento da janela de escolha da foto ao criar o avatar.

Causa-raiz: a automação do Flow via CDP roda no MESMO Chrome que exibe a UI e usa
o seletor de arquivos do navegador (set_input_files / expect_file_chooser). O
diálogo nativo aberto pela UI e a automação disputavam o mesmo recurso — a janela
de arquivos ficava "não respondendo". Além disso, o backend criava o job apenas
DENTRO do worker (setdefault), então dois cliques quase simultâneos subiam DUAS
automações CDP no mesmo Chrome.

Correção verificada aqui:
  * front: lock bidirecional (avatarAutomacaoAtiva) + o fluxo de criação AGUARDA a
    escolha da foto (escolherFotoAvatar) antes de disparar o POST;
  * botões/caixa de foto travados durante a automação;
  * backend: job RESERVADO antes de subir a thread (+ job preso libera após 900s).
"""
import io
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def _bloco(inicio: str, fim: str) -> str:
    """Trecho do app.js entre `inicio` e `fim` (falha se não existir)."""
    i = JS.find(inicio)
    if i == -1:
        raise AssertionError(f"'{inicio}' não encontrado em static/app.js")
    j = JS.find(fim, i)
    if j == -1:
        raise AssertionError(f"'{fim}' não encontrado após '{inicio}'")
    return JS[i:j]


class TestFrontLockJanelaDeFoto(unittest.TestCase):
    def test_01_ids_da_foto_existem_no_html(self):
        for el_id in ("btn-s2-escolher-avatar", "s2-input-avatar-file", "s2-preview-avatar-box"):
            self.assertIn(f'id="{el_id}"', HTML, el_id)

    def test_02_lock_bidirecional_declarado(self):
        self.assertIn("let avatarAutomacaoAtiva = false;", JS)
        self.assertIn("let avatarPickerResolver = null;", JS)

    def test_03_abrir_seletor_bloqueia_durante_a_automacao(self):
        bloco = _bloco("const abrirSeletorAvatar", 'window.addEventListener("focus"')
        self.assertIn("if (avatarAutomacaoAtiva)", bloco)
        # o return do lock precisa vir ANTES do fileInput.click()
        self.assertLess(bloco.find("if (avatarAutomacaoAtiva)"), bloco.find("fileInput.click()"))

    def test_04_fluxo_de_criacao_aguarda_a_escolha_da_foto(self):
        corpo = _bloco("async function criar_personagem_flow()",
                       "window.criar_personagem_flow = criar_personagem_flow;")
        self.assertIn("await escolherFotoAvatar()", corpo)
        self.assertNotIn(
            'alert("Selecione uma imagem/foto de referência para criar o personagem no Google Flow.")',
            corpo)
        # o POST (automação) só pode acontecer DEPOIS de resolver a escolha da foto
        self.assertLess(corpo.find("await escolherFotoAvatar()"), corpo.find("apiForm("))

    def test_05_lock_e_trava_dos_botoes_ao_redor_do_post(self):
        corpo = _bloco("async function criar_personagem_flow()",
                       "window.criar_personagem_flow = criar_personagem_flow;")
        post = corpo.find("apiForm(")
        self.assertLess(corpo.find("avatarAutomacaoAtiva = true;"), post)
        self.assertLess(corpo.find("_travarBotoesFotoAvatar(true);"), post)
        finally_idx = corpo.find("} finally {")
        self.assertGreater(finally_idx, post)
        self.assertIn("avatarAutomacaoAtiva = false;", corpo[finally_idx:])
        self.assertIn("_travarBotoesFotoAvatar(false);", corpo[finally_idx:])

    def test_06_picker_resolve_no_change_do_input(self):
        bloco = _bloco('if ($("s2-input-avatar-file")) {', "// Suporte a Drag & Drop")
        self.assertIn("avatarPickerResolver", bloco)
        self.assertIn("resolver(file || null);", bloco)


class TestBackendJobReservadoAntesDaThread(unittest.TestCase):
    PROJ = "proj_avatar_travamento"

    def setUp(self):
        from services import api_v2
        api_v2._AVATAR_FLOW_JOBS.pop(self.PROJ, None)

    def tearDown(self):
        from services import api_v2
        api_v2._AVATAR_FLOW_JOBS.pop(self.PROJ, None)

    def _post(self):
        from app_web import app
        client = app.test_client()
        data = {"nome": "Marcos", "estilo_visual": "photorealistic_cinematic",
                "imagem": (io.BytesIO(b"fake_image"), "foto.png")}
        return client.post(f"/api/v2/personagem/{self.PROJ}/criar_flow",
                           data=data, content_type="multipart/form-data")

    @staticmethod
    def _patches():
        from services import api_v2
        return [
            patch.object(api_v2.character_svc, "obter_identidade_projeto", return_value=None),
            patch.object(api_v2.character_svc, "validar_personagem_do_projeto",
                         return_value=(None, "sem identidade")),
            patch.object(api_v2.character_svc, "personagem_e_generico", return_value=False),
            patch.object(api_v2.character_svc, "salvar_identidade_projeto",
                         return_value={"success": True}),
        ]

    def test_07_job_reservado_antes_da_thread_e_segundo_clique_ignorado(self):
        from services import api_v2
        pats = self._patches()
        with patch("services.api_v2.threading.Thread") as mock_thread:
            for p in pats:
                p.start()
            try:
                r1 = self._post()
                self.assertEqual(r1.status_code, 200)
                self.assertEqual(r1.get_json().get("status"), "iniciado")
                # job JÁ reservado (mesmo com a thread mockada, que nunca executa)
                self.assertEqual(api_v2._AVATAR_FLOW_JOBS[self.PROJ]["status"], "executando")
                r2 = self._post()
            finally:
                for p in pats:
                    p.stop()
        self.assertEqual(r2.get_json().get("status"), "executando")
        self.assertEqual(mock_thread.call_count, 1,
                         "dois cliques NÃO podem subir duas automações CDP no mesmo Chrome")
        self.assertIn("já está em andamento", r2.get_json().get("mensagem", ""))

    def test_08_job_preso_libera_nova_tentativa(self):
        from services import api_v2
        api_v2._AVATAR_FLOW_JOBS[self.PROJ] = {
            "status": "executando", "etapa": "travado", "erro": None, "resultado": None,
            "iniciado_em": time.time() - (api_v2._AVATAR_FLOW_JOB_TIMEOUT_S + 60),
        }
        pats = self._patches()
        with patch("services.api_v2.threading.Thread") as mock_thread:
            for p in pats:
                p.start()
            try:
                r = self._post()
            finally:
                for p in pats:
                    p.stop()
        self.assertEqual(r.get_json().get("status"), "iniciado")
        self.assertEqual(mock_thread.call_count, 1)


if __name__ == "__main__":
    unittest.main()

