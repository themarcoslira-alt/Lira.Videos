# -*- coding: utf-8 -*-
"""
Regressão — upload de avatar sob Chrome CDP (TAREFAS 1-3).

Bug: dentro do Chrome controlado por CDP o clique no input[type=file] não abre o
seletor nativo do usuário — o navegador tenta usar a sessão isolada da automação e
trava ao listar Downloads.

Contrato:
  * CDP detectado (navigator.webdriver === true, window.__LIRA_CDP__ ou ?cdp=1) ->
    o clique NÃO abre o seletor, e a mensagem "Use Ctrl+V ou arraste a foto aqui"
    é exibida; o upload segue por drag & drop / Ctrl+V;
  * navegador normal (webdriver === false, sem ?cdp=1) -> comportamento inalterado
    (clique abre o seletor nativo).
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
PLAYWRIGHT_FLOW = (ROOT / "services" / "playwright_flow.py").read_text(encoding="utf-8")
MSG = "Use Ctrl+V ou arraste a foto aqui"


def _bloco(inicio: str, fim: str) -> str:
    i = JS.find(inicio)
    if i == -1:
        raise AssertionError(f"'{inicio}' não encontrado em static/app.js")
    j = JS.find(fim, i)
    if j == -1:
        raise AssertionError(f"'{fim}' não encontrado após '{inicio}'")
    return JS[i:j]


class TestTarefa1InputDoAvatar(unittest.TestCase):
    def test_01_input_file_do_avatar_localizado(self):
        self.assertIn('<input id="s2-input-avatar-file" type="file" accept="image/*"', HTML)
        self.assertIn('id="btn-s2-escolher-avatar"', HTML)
        self.assertIn('id="s2-preview-avatar-box"', HTML)
        self.assertIn('id="s2-avatar-dica-upload"', HTML)

    def test_02_dica_de_upload_comeca_oculta(self):
        i = HTML.find('id="s2-avatar-dica-upload"')
        self.assertIn("hidden", HTML[i:i + 200], "a dica só aparece em modo CDP")


class TestTarefa2DeteccaoCdp(unittest.TestCase):
    def test_03_detecta_navigator_webdriver(self):
        bloco = _bloco("const _cdpAtivo = () =>", "let avatarModoCdp = false;")
        self.assertIn("window.navigator.webdriver === true", bloco)

    def test_04_detecta_parametro_cdp_e_persiste_na_sessao(self):
        bloco = _bloco("const _cdpAtivo = () =>", "let avatarModoCdp = false;")
        self.assertIn('params.get("cdp") === "1"', bloco)
        self.assertIn('sessionStorage.setItem("lira_cdp", "1")', bloco)
        self.assertIn('sessionStorage.getItem("lira_cdp")', bloco)

    def test_05_mensagem_exigida(self):
        self.assertIn(f'MSG_UPLOAD_CDP = "{MSG}"', JS)

    def test_06_abrir_seletor_nao_clica_em_cdp(self):
        bloco = _bloco("const abrirSeletorAvatar", 'window.addEventListener("focus"')
        self.assertIn("if (avatarModoCdp || _cdpAtivo())", bloco)
        self.assertIn("_avisarAvatar(MSG_UPLOAD_CDP)", bloco)
        # o return do modo CDP vem ANTES do click no input
        self.assertLess(bloco.find("if (avatarModoCdp || _cdpAtivo())"),
                        bloco.find("fileInput.click()"))

    def test_07_input_desabilitado_em_cdp(self):
        bloco = _bloco("const _aplicarModoUploadAvatar = () =>", "let tempAvatarFile = null;")
        self.assertIn("fileInput.disabled = avatarModoCdp;", bloco)
        self.assertIn('dica.classList.toggle("hidden", !avatarModoCdp)', bloco)

    def test_08_fluxo_de_criacao_nao_fica_preso_em_cdp(self):
        bloco = _bloco("const escolherFotoAvatar = () => new Promise", "// LOCK: não iniciar")
        self.assertIn("if (avatarModoCdp || _cdpAtivo())", bloco)
        self.assertIn("resolve(null);", bloco)

    def test_09_lock_nao_reativa_o_input_em_cdp(self):
        bloco = _bloco("const _travarBotoesFotoAvatar = (ativo) =>", "const abrirSeletorAvatar")
        self.assertIn('id === "s2-input-avatar-file"', bloco)
        self.assertIn("avatarModoCdp || _cdpAtivo()", bloco)

    def test_10_modo_aplicado_na_inicializacao(self):
        self.assertIn("_aplicarModoUploadAvatar();", JS)


class TestTarefa2CaminhosAlternativosPreservados(unittest.TestCase):
    def test_11_drag_and_drop_chama_aplicar_arquivo(self):
        bloco = _bloco("const dropZones", "// Suporte a Colar imagem")
        self.assertIn('zone.addEventListener("drop"', bloco)
        self.assertIn("aplicarArquivoAvatar(e.dataTransfer.files[0]);", bloco)

    def test_12_ctrl_v_paste_chama_aplicar_arquivo(self):
        bloco = _bloco('window.addEventListener("paste"', "_aplicarModoUploadAvatar();")
        self.assertIn("clipboardData", bloco)
        self.assertIn("getAsFile()", bloco)
        self.assertIn("aplicarArquivoAvatar(file);", bloco)


class TestTarefa3NavegadorNormal(unittest.TestCase):
    def test_13_comparacao_estrita_nao_afeta_usuario_normal(self):
        bloco = _bloco("const _cdpAtivo = () =>", "let avatarModoCdp = false;")
        # `=== true` (e não truthy) garante que webdriver=false não ative o modo CDP
        self.assertIn("=== true", bloco)
        self.assertNotIn("if (window.navigator.webdriver)", bloco)

    def test_14_flag_cdp_no_chrome_da_automacao(self):
        self.assertIn("http://127.0.0.1:5000/?cdp=1", PLAYWRIGHT_FLOW)
        self.assertGreaterEqual(PLAYWRIGHT_FLOW.count("http://127.0.0.1:5000/?cdp=1"), 2,
                                "aba do app (PUT /json/new) e janela nova (Popen)")

    def test_15_comportamento_normal_preservado_no_codigo(self):
        bloco = _bloco("const abrirSeletorAvatar", 'window.addEventListener("focus"')
        # sem CDP e sem automação, o fluxo continua clicando no input
        self.assertIn("fileInput.click();", bloco)


if __name__ == "__main__":
    unittest.main()
