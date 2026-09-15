# -*- coding: utf-8 -*-
"""
tests/test_personagem_identidade_obrigatoria.py
==============================================
Correção do defeito: "o sistema cria/usa um @avatar GENÉRICO quando deveria
usar o @Marcos do projeto".

Fonte única de verdade: `identidade.json` do projeto.

Cobertura:
1. `character_service.validar_personagem_do_projeto` — erro CLARO para
   projeto_id vazio / identidade ausente / JSON corrompido / sem personagem, e
   o personagem OFICIAL (com '@') quando a identidade é válida.
2. `character_service.personagem_e_generico` — nomes genéricos nunca passam.
3. `tag_personagem_valida` — só tags do catálogo do projeto (identidade +
   references.json) são aceitas.
4. `playwright_flow.incluir_referencia_personagem` (a função que clica no '+'
   do campo de prompt) — recebe `projeto_id`, valida identidade, tem `log_char`
   e NÃO usa mais o fallback "primeiro card" para personagem.
5. `_selecionar_referencia_flow` — sem "@Marcos" hardcoded; lê identidade.json.
6. `api_v2._executar_criar_avatar_background` — cancela a criação com mensagem
   clara quando não há identidade válida (nunca cria genérico no Flow).
7. `character_service.salvar_identidade_projeto` — RECUSA (sem gravar nada)
   nome genérico/vazio: um projeto nunca persiste "avatar"/"personagem"/"me".
8. `api_v2` — `/personagem/<id>/criar_flow` recusa nome genérico com 400 e
   `/personagem/<id>/cadastrar` não tem mais o fallback "PersonagemPrincipal".
9. Pré-voo da fila (`playwright_flow`) — identidade com nome genérico cancela o
   provisionamento no Flow (log [CHAR] de erro) em vez de criar personagem.
"""
import ast
import io
import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from config import PROJETOS_DIR

import services.character_service as char_svc


# Foto mínima aceita pelo multipart do endpoint (conteúdo irrelevante: o teste
# não abre o Google Flow — a thread é mockada).
FOTO_FAKE = b"\x89PNG\r\n\x1a\nfoto-de-teste"


TMP_ROOT = PROJETOS_DIR / "_t_char_identidade"


def _criar_projeto_teste(nome: str, identidade=None) -> str:
    pdir = TMP_ROOT / nome
    pdir.mkdir(parents=True, exist_ok=True)
    if identidade is not None:
        (pdir / "identidade.json").write_text(
            json.dumps(identidade, ensure_ascii=False), encoding="utf-8")
    return nome


class TestValidarPersonagemDoProjeto(unittest.TestCase):

    @classmethod
    def tearDownClass(cls):
        if TMP_ROOT.exists():
            shutil.rmtree(TMP_ROOT, ignore_errors=True)

    def setUp(self):
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self._patch = patch.object(char_svc, "PROJETOS_DIR", TMP_ROOT)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_01_projeto_id_vazio(self):
        personagem, erro = char_svc.validar_personagem_do_projeto("")
        self.assertIsNone(personagem)
        self.assertIn("Projeto ID não fornecido", erro)

    def test_02_identidade_ausente(self):
        _criar_projeto_teste("sem_identidade", None)
        personagem, erro = char_svc.validar_personagem_do_projeto("sem_identidade")
        self.assertIsNone(personagem)
        self.assertIn("não tem identidade configurada", erro)
        self.assertIn("sem_identidade", erro)

    def test_03_identidade_corrompida(self):
        pdir = TMP_ROOT / "corrompido"
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "identidade.json").write_text("{nao é json", encoding="utf-8")
        personagem, erro = char_svc.validar_personagem_do_projeto("corrompido")
        self.assertIsNone(personagem)
        self.assertIn("corrompido", erro)

    def test_04_identidade_sem_personagem(self):
        _criar_projeto_teste("vazio", {"tipo": "personagem", "status": "novo"})
        personagem, erro = char_svc.validar_personagem_do_projeto("vazio")
        self.assertIsNone(personagem)
        self.assertIn("Nenhum personagem configurado", erro)

    def test_05_identidade_valida_devolve_marcos(self):
        _criar_projeto_teste("com_marcos", {
            "tipo": "personagem",
            "nome": "Marcos",
            "referencia_flow": "Marcos",   # sem '@' → normalizado
            "imagem": "characters/Marcos/reference.png",
            "flow_character_id": "flow-char-marcos",
            "flow_character_created": True,
            "personagens": [{"nome": "Marcos", "referencia_flow": "@Marcos", "principal": True}],
        })
        personagem, erro = char_svc.validar_personagem_do_projeto("com_marcos")
        self.assertIsNone(erro)
        self.assertEqual(personagem["nome"], "Marcos")
        self.assertTrue(personagem["referencia_flow"].startswith("@"))
        self.assertEqual(personagem["referencia_flow"], "@Marcos")
        self.assertEqual(personagem["flow_character_id"], "flow-char-marcos")
        self.assertTrue(personagem["flow_character_created"])

    def test_06_personagem_principal_da_lista_e_escolhido(self):
        _criar_projeto_teste("multiref", {
            "tipo": "personagem",
            "nome": "",
            "personagens": [
                {"nome": "Ana", "referencia_flow": "@Ana"},
                {"nome": "Marcos", "referencia_flow": "@Marcos", "principal": True},
            ],
        })
        personagem, erro = char_svc.validar_personagem_do_projeto("multiref")
        self.assertIsNone(erro)
        self.assertEqual(personagem["nome"], "Marcos")
        self.assertEqual(personagem["referencia_flow"], "@Marcos")


class TestPersonagemGenerico(unittest.TestCase):

    def test_01_genericos_reprovados(self):
        for nome in ("avatar", "@Avatar", "@me", "personagem", "Personagens",
                     "character", "meu avatar", "", None, "   "):
            self.assertTrue(char_svc.personagem_e_generico(nome), f"'{nome}' deveria ser genérico")

    def test_02_personagem_real_aprovado(self):
        for nome in ("Marcos", "@Marcos", "Dona Ana", "@batman"):
            self.assertFalse(char_svc.personagem_e_generico(nome), f"'{nome}' NÃO é genérico")


class TestTagPersonagemValida(unittest.TestCase):

    @classmethod
    def tearDownClass(cls):
        if TMP_ROOT.exists():
            shutil.rmtree(TMP_ROOT, ignore_errors=True)

    def setUp(self):
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self._patch = patch.object(char_svc, "PROJETOS_DIR", TMP_ROOT)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        _criar_projeto_teste("cat", {
            "tipo": "personagem",
            "nome": "Marcos",
            "referencia_flow": "@Marcos",
            "personagens": [{"nome": "Marcos", "referencia_flow": "@Marcos", "principal": True}],
        })

    def test_01_tag_oficial_valida(self):
        self.assertTrue(char_svc.tag_personagem_valida("cat", "@Marcos"))
        self.assertTrue(char_svc.tag_personagem_valida("cat", "Marcos"))

    def test_02_tag_generica_ou_inexistente_reprovada(self):
        self.assertFalse(char_svc.tag_personagem_valida("cat", "@avatar"))
        self.assertFalse(char_svc.tag_personagem_valida("cat", "@Batman"))
        self.assertFalse(char_svc.tag_personagem_valida("cat", ""))
        self.assertFalse(char_svc.tag_personagem_valida("projeto_inexistente", "@Marcos"))


class TestFlowAnexoAntiGenerico(unittest.TestCase):
    """Inspeção estrutural + runtime de playwright_flow."""

    @classmethod
    def setUpClass(cls):
        cls.src = Path("services/playwright_flow.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.src)
        cls.func_srcs = {}
        for node in ast.walk(cls.tree):
            if isinstance(node, ast.FunctionDef):
                cls.func_srcs.setdefault(node.name, ast.get_source_segment(cls.src, node) or "")

    def test_01_log_char_com_timestamp(self):
        self.assertIn('def log_char(', self.src)
        src_log = self.func_srcs.get("log_char", "")
        self.assertIn("[CHAR]", src_log)
        self.assertIn('strftime("%H:%M:%S.%f")[:-3]', src_log)

    def test_02_incluir_referencia_le_identidade_do_projeto(self):
        src = self.func_srcs.get("incluir_referencia_personagem", "")
        self.assertIn("validar_personagem_do_projeto(projeto_id)", src)
        self.assertIn("_nome_personagem_generico(", src)

    def test_03_fallback_primeiro_card_bloqueado_para_personagem(self):
        src = self.func_srcs.get("incluir_referencia_personagem", "")
        self.assertIn("if not item_ref and not nome_busca_personagem:", src)
        self.assertIn('anexo CANCELADO (nunca anexa card genérico)', src)

    def test_04_selecionar_referencia_sem_marcos_hardcoded(self):
        src = self.func_srcs.get("_selecionar_referencia_flow", "")
        self.assertNotIn('else "@Marcos")', src)
        self.assertIn("validar_personagem_do_projeto(projeto_id)", src)
        self.assertIn("projeto_id=projeto_id,", src)

    def test_05_criar_avatar_valida_identidade_antes_de_criar(self):
        src = self.func_srcs.get("criar_avatar_flow_via_playwright", "")
        self.assertIn("validar_personagem_do_projeto(projeto_id)", src)
        self.assertIn("não criamos personagem genérico", src)

    def test_06_runtime_sem_identidade_falha_sem_tocar_no_flow(self):
        """Sem identidade válida (projeto inexistente) NÃO abre o Flow: retorna False."""
        from services.playwright_flow import incluir_referencia_personagem
        motivos = []
        ok = incluir_referencia_personagem(None, projeto_id="projeto_inexistente_xyz",
                                           motivo_erro=motivos)
        self.assertFalse(ok)
        self.assertTrue(motivos, "a função deve devolver o motivo claro do aborto")
        self.assertIn("não tem identidade configurada", motivos[-1])

    def test_07_runtime_nome_generico_sem_identidade_aborta(self):
        from services.playwright_flow import incluir_referencia_personagem
        motivos = []
        ok = incluir_referencia_personagem(None, nome_personagem="avatar",
                                           tag_personagem="@avatar",
                                           projeto_id="projeto_inexistente_xyz",
                                           motivo_erro=motivos)
        self.assertFalse(ok)
        self.assertTrue(motivos)
        self.assertIn("genérico", motivos[-1].lower())

    def test_08_runtime_upload_de_imagem_nao_e_bloqueado(self):
        """tipo='imagem' (b-roll/upload) não passa pela validação de personagem."""
        src = self.func_srcs.get("incluir_referencia_personagem", "")
        self.assertIn("if eh_personagem:", src)
        self.assertIn('in ("personagem", "character", "avatar")', src)


class TestApiV2CriacaoAntiGenerico(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.src = Path("services/api_v2.py").read_text(encoding="utf-8")

    def test_01_job_valida_identidade(self):
        self.assertIn("personagem_projeto, erro_identidade = character_svc.validar_personagem_do_projeto(projeto_id)",
                      self.src)
        self.assertIn("nunca criamos personagem genérico", self.src)

    def test_02_runtime_cancela_sem_identidade(self):
        from services import api_v2
        pid = "_t_char_job_sem_identidade"
        pdir = PROJETOS_DIR / pid
        api_v2._AVATAR_FLOW_JOBS.pop(pid, None)
        try:
            api_v2._executar_criar_avatar_background(pid, "avatar", "photorealistic_cinematic")
            job = api_v2._AVATAR_FLOW_JOBS.get(pid)
            self.assertIsNotNone(job, "o job deve registrar o erro")
            self.assertEqual(job["status"], "erro")
            self.assertIn("cancelada", job["erro"])
            self.assertIn("nunca criamos personagem genérico", job["erro"])
        finally:
            api_v2._AVATAR_FLOW_JOBS.pop(pid, None)
            shutil.rmtree(pdir, ignore_errors=True)

    def test_03_criar_flow_recusa_nome_generico_400(self):
        """A criação no Flow recusa 'avatar'/'personagem' ANTES de abrir o navegador."""
        self.assertIn("character_svc.personagem_e_generico(nome_limpo)", self.src)
        self.assertIn("é um nome genérico", self.src)
        self.assertIn("aba Identidade", self.src)

    def test_04_cadastrar_sem_fallback_personagem_principal(self):
        """Não existe mais nome genérico criado por fallback no cadastro."""
        self.assertNotIn('nome = "PersonagemPrincipal"', self.src)
        self.assertIn("nunca criamos", self.src)


class TestSalvarIdentidadeAntiGenerico(unittest.TestCase):
    """Runtime: identidade genérica NUNCA é persistida no projeto."""

    @classmethod
    def tearDownClass(cls):
        if TMP_ROOT.exists():
            shutil.rmtree(TMP_ROOT, ignore_errors=True)

    def setUp(self):
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self._patch = patch.object(char_svc, "PROJETOS_DIR", TMP_ROOT)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_01_nome_generico_recusado_sem_gravar(self):
        for nome in ("avatar", "@Avatar", "personagem", "@me", "character", "", "   "):
            res = char_svc.salvar_identidade_projeto(
                projeto_id="trava", tipo="personagem", nome=nome)
            self.assertFalse(res.get("success"), f"'{nome}' deveria ser recusado")
            self.assertIn("genérico", str(res.get("error", "")).lower())
        self.assertFalse((TMP_ROOT / "trava" / "identidade.json").exists(),
                         "nada pode ser gravado quando o nome é genérico")

    def test_02_nome_oficial_persistido(self):
        res = char_svc.salvar_identidade_projeto(
            projeto_id="oficial", tipo="personagem", nome="Marcos",
            referencia_flow="@Marcos")
        self.assertTrue(res.get("success"))
        personagem, erro = char_svc.validar_personagem_do_projeto("oficial")
        self.assertIsNone(erro)
        self.assertEqual(personagem["referencia_flow"], "@Marcos")

    def test_03_tipo_avatar_legado_nao_e_bloqueado(self):
        """tipo='avatar' (Google Flow 'me', legado) continua permitido."""
        res = char_svc.salvar_identidade_projeto(
            projeto_id="legado", tipo="avatar", nome="me")
        self.assertTrue(res.get("success"))


class TestPreVooAntiGenerico(unittest.TestCase):
    """Pré-voo da fila não provisiona personagem genérico."""

    @classmethod
    def setUpClass(cls):
        cls.src = Path("services/playwright_flow.py").read_text(encoding="utf-8")

    def test_01_pre_voo_bloqueia_nome_generico(self):
        self.assertIn("character_svc.personagem_e_generico(_nome_char)", self.src)
        self.assertIn("nunca criamos", self.src)
        self.assertIn("pré-voo ABORTADO", self.src)

    def test_02_reprovisao_le_identidade_do_projeto(self):
        self.assertIn("validar_personagem_do_projeto(_projeto)", self.src)
        self.assertIn("reprovisão de personagem CANCELADA", self.src)


class TestProjetoNovoAntiGenerico(unittest.TestCase):
    """Cenário reportado: PROJETO NOVO.

    O clique no '+' (criar personagem no Google Flow) roda o endpoint REAL
    `POST /api/v2/personagem/<projeto_id>/criar_flow`. Ele DEVE ler o
    `identidade.json` do `projeto_id` recebido e:

      * usar o personagem OFICIAL (@Marcos) quando a identidade existe;
      * devolver erro CLARO quando não existe;
      * NUNCA criar um '@avatar' genérico na conta do Google Flow.
    """

    PID = "_t_char_projeto_novo"

    def setUp(self):
        from app_web import app
        from services import api_v2
        self.client = app.test_client()
        self.api_v2 = api_v2
        api_v2._AVATAR_FLOW_JOBS.pop(self.PID, None)
        self.pdir = PROJETOS_DIR / self.PID
        if self.pdir.exists():
            shutil.rmtree(self.pdir, ignore_errors=True)
        self.pdir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.api_v2._AVATAR_FLOW_JOBS.pop(self.PID, None)
        shutil.rmtree(self.pdir, ignore_errors=True)

    def _escrever_identidade_marcos(self) -> str:
        """Cria identidade @Marcos + reference.png reais neste projeto."""
        ref_dir = self.pdir / "characters" / "Marcos"
        ref_dir.mkdir(parents=True, exist_ok=True)
        ref = ref_dir / "reference.png"
        ref.write_bytes(FOTO_FAKE)
        (self.pdir / "identidade.json").write_text(json.dumps({
            "tipo": "personagem",
            "nome": "Marcos",
            "referencia_flow": "@Marcos",
            "imagem": "characters/Marcos/reference.png",
            "imagem_abs": str(ref),
            "status": "vinculado",
            "personagens": [{"nome": "Marcos", "referencia_flow": "@Marcos",
                             "principal": True, "imagem_abs": str(ref)}],
        }, ensure_ascii=False), encoding="utf-8")
        return str(ref)

    def _post_criar_flow(self, nome: str, com_imagem: bool = True):
        """POST no endpoint real; a thread de automação é mockada (sem Chrome)."""
        data = {"nome": nome, "estilo_visual": "photorealistic_cinematic"}
        if com_imagem:
            data["imagem"] = (io.BytesIO(FOTO_FAKE), "foto.png")
        with patch("services.api_v2.threading.Thread") as m_thread:
            resp = self.client.post(
                f"/api/v2/personagem/{self.PID}/criar_flow",
                data=data, content_type="multipart/form-data",
            )
        return resp, m_thread

    def test_01_nome_generico_em_projeto_novo_falha_claro_e_nao_cria(self):
        """'@avatar' em projeto NOVO => 400 com erro claro; nada vai para o Flow."""
        resp, m_thread = self._post_criar_flow("@avatar")
        self.assertEqual(resp.status_code, 400)
        erro = (resp.get_json() or {}).get("error", "")
        self.assertIn("genérico", erro.lower())
        self.assertIn("@Marcos", erro, "a mensagem deve orientar o nome oficial")
        self.assertFalse((self.pdir / "identidade.json").exists(),
                         "identidade.json NÃO pode ser gravada com nome genérico")
        m_thread.assert_not_called()

    def test_02_nome_oficial_em_projeto_novo_grava_identidade_e_dispara_flow(self):
        """'@Marcos' em projeto NOVO => identidade oficial gravada + job iniciado."""
        resp, m_thread = self._post_criar_flow("@Marcos")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual((resp.get_json() or {}).get("status"), "iniciado")
        m_thread.assert_called_once()
        args = m_thread.call_args.kwargs.get("args") or ()
        self.assertEqual(args[0], self.PID)
        self.assertEqual(args[1].lstrip("@"), "Marcos")

        idt = json.loads((self.pdir / "identidade.json").read_text(encoding="utf-8"))
        self.assertEqual(idt["nome"], "Marcos")
        self.assertEqual(idt["referencia_flow"], "@Marcos")

        personagem, erro = char_svc.validar_personagem_do_projeto(self.PID)
        self.assertIsNone(erro)
        self.assertEqual(personagem["referencia_flow"], "@Marcos")

    def test_03_identidade_existente_prevalece_sobre_nome_generico(self):
        """Projeto COM '@Marcos': o request genérico é ignorado (usa o oficial)."""
        self._escrever_identidade_marcos()
        resp, m_thread = self._post_criar_flow("@avatar", com_imagem=False)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual((resp.get_json() or {}).get("status"), "iniciado")
        args = m_thread.call_args.kwargs.get("args") or ()
        self.assertNotIn("avatar", str(args[1]).lower(),
                         "o job de criação NÃO pode receber o nome genérico")
        self.assertEqual(str(args[1]).lstrip("@").lower(), "marcos",
                         "deve criar o personagem OFICIAL do projeto, nunca o genérico")

        idt_txt = (self.pdir / "identidade.json").read_text(encoding="utf-8").lower()
        self.assertNotIn("avatar", idt_txt)
        self.assertIn("@marcos", idt_txt)

    def test_04_log_char_tem_timestamp_e_projeto(self):
        """Logging obrigatório: '[HH:MM:SS.mmm] [CHAR] projeto=...' (TAREFA 5)."""
        from services.playwright_flow import log_char
        import re as _re
        capturado = []

        def _fake_pw_log(msg, level="info"):
            capturado.append(msg)

        with patch("services.playwright_flow.pw_log", side_effect=_fake_pw_log):
            log_char("validação de teste", projeto_id="proj_log", level="info")

        self.assertTrue(any("[CHAR]" in m and "projeto=proj_log" in m for m in capturado),
                        "log_char deve carimbar [CHAR] e o projeto")
        self.assertTrue(any(_re.match(r"\[\d{2}:\d{2}:\d{2}\.\d{3}\]", m) for m in capturado),
                        "log_char deve começar com timestamp HH:MM:SS.mmm")


class TestSelecionarReferenciaRuntime(unittest.TestCase):
    """Runtime do caminho do '+': `_selecionar_referencia_flow`.

    Prova, com o worker REAL e apenas o `page` mockado, que:
      * projeto com identidade => o personagem oficial (@Marcos) é o anexado;
      * projeto sem identidade => o anexo é CANCELADO (nunca card genérico).
    """

    def _worker(self):
        from services.playwright_flow import PlaywrightCDPWorker
        from unittest.mock import MagicMock
        w = PlaywrightCDPWorker.__new__(PlaywrightCDPWorker)
        w.page = MagicMock()
        return w

    def test_01_projeto_com_identidade_anexa_marcos(self):
        from unittest.mock import MagicMock
        import services.playwright_flow as pf
        w = self._worker()
        capturado = {}

        def _fake_incluir(page, reference_path="reference.png", nome_personagem="",
                          tag_personagem="", projeto_id="", tipo="personagem",
                          motivo_erro=None):
            capturado.update(nome_personagem=nome_personagem, tag_personagem=tag_personagem,
                             projeto_id=projeto_id, tipo=tipo,
                             reference_path=reference_path)
            return True

        pid = "_t_char_projeto_novo"
        pdir = PROJETOS_DIR / pid
        if pdir.exists():
            shutil.rmtree(pdir, ignore_errors=True)
        pdir.mkdir(parents=True, exist_ok=True)
        try:
            # REQ (TAREFA 1): a identidade PRECISA ter a foto de referência real —
            # sem ela o anexo é cancelado (nunca usa 'reference.png' genérica).
            ref_dir = pdir / "characters" / "Marcos"
            ref_dir.mkdir(parents=True, exist_ok=True)
            ref = ref_dir / "reference.png"
            ref.write_bytes(b"\x89PNG\r\n\x1a\nfoto-de-teste")
            (pdir / "identidade.json").write_text(json.dumps({
                "tipo": "personagem", "nome": "Marcos", "referencia_flow": "@Marcos",
                "imagem": "characters/Marcos/reference.png", "imagem_abs": str(ref),
                "personagens": [{"nome": "Marcos", "referencia_flow": "@Marcos",
                                 "principal": True, "imagem_abs": str(ref)}],
            }, ensure_ascii=False), encoding="utf-8")

            with patch("services.playwright_flow.incluir_referencia_personagem",
                       side_effect=_fake_incluir):
                ok = w._selecionar_referencia_flow(projeto_id=pid, tipo="personagem")

            self.assertTrue(ok)
            self.assertEqual(capturado.get("projeto_id"), pid)
            self.assertEqual(capturado.get("nome_personagem"), "Marcos")
            self.assertEqual(capturado.get("tag_personagem"), "@Marcos")
            self.assertNotIn("avatar", str(capturado.get("tag_personagem")).lower())
            # A referência usada é a imagem REAL do projeto (não o literal genérico).
            self.assertEqual(Path(str(capturado.get("reference_path"))).resolve(), ref.resolve())
        finally:
            shutil.rmtree(pdir, ignore_errors=True)

    def test_02_projeto_sem_identidade_cancela_o_anexo(self):
        from unittest.mock import MagicMock
        w = self._worker()
        pid = "_t_char_projeto_sem_identidade_xyz"
        pdir = PROJETOS_DIR / pid
        try:
            with patch("services.playwright_flow.incluir_referencia_personagem") as m_inc:
                ok = w._selecionar_referencia_flow(projeto_id=pid, tipo="personagem")
            self.assertFalse(ok, "sem identidade o anexo tem de ser cancelado")
            m_inc.assert_not_called()
            self.assertNotEqual(getattr(w, "current_flow_reference", ""), "@Marcos")
        finally:
            shutil.rmtree(pdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

