"""Contrato de IDs do index.html servido na rota / (Lira Studio).

POR QUE ESTE TESTE EXISTE
-------------------------
O frontend (static/app.js, ~356 KB) resolve 374 elementos por id e o proprio
index.html tem um <script> inline que resolve mais 22. O redesign visual
(TokyoX) altera HTML/CSS, mas NUNCA pode remover id / name / data-*.

Sem esta checagem, um id removido aparece apenas como erro silencioso no
browser (getElementById devolve null e o handler estoura no clique).

O que e verificado:
  1. Todo id referenciado em app.js existe no index.html;
  2. Todo id referenciado no <script> inline do index.html existe no proprio HTML;
  3. Nenhum id do baseline congelado (pos-v4.14, pre-redesign TokyoX) foi perdido;
  4. Os 3 assets externos (/static/app.js, /static/style.css, placeholder) existem;
  5. O <link> do Google Fonts (Space Grotesk / Inter / JetBrains Mono) esta no head.
"""
import re
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
INDEX = STATIC_DIR / "index.html"
APP_JS = STATIC_DIR / "app.js"
BASELINE = Path(__file__).resolve().parent / "ids_index_baseline.txt"

# Padroes REAIS de resolucao de id no frontend (helper `$` = getElementById).
PADROES_ID = (
    r"\$\s*\(\s*['\"]([A-Za-z0-9_-]+)['\"]\s*\)",
    r"getElementById\s*\(\s*['\"]([A-Za-z0-9_-]+)['\"]",
    r"querySelector\s*\(\s*['\"]#([A-Za-z0-9_-]+)['\"]",
    r"\$id\s*\(\s*['\"]([A-Za-z0-9_-]+)['\"]",
)

# Ids de elementos criados em RUNTIME pelo app.js (nao existem no HTML estatico).
# Cada entrada e VERIFICADA por test_06 (o app.js precisa realmente criar o id).
IDS_DINAMICOS_APP_JS = {
    "toast-container",        # app.js:56  cont.id = "toast-container"
    "tela-projetos",          # app.js:305 tela.id = "tela-projetos"
    "media-modal-video-erro",  # app.js:2303 p.id = "media-modal-video-erro"
    "projetos-grid",          # app.js:313 innerHTML '...id="projetos-grid"...'
    "projetos-count",         # app.js     innerHTML '...id="projetos-count"...'
}

# HOOKS ORFAOS PRE-EXISTENTES (debito anterior ao redesign TokyoX).
# Sao ids que o app.js procura mas que NUNCA existiram no index.html —
# em sua maioria protegidos por `if ($("x"))` (no-op silencioso) ou pertencentes
# a paineis que nunca foram portados para o HTML atual. NAO foram criados nem
# removidos por este redesign; a lista existe apenas para congelar o estado e
# garantir que o port visual nao introduza NENHUM id faltante novo.
HOOKS_ORFAOS_PRE_REDESIGN = {
    "avatar-img-preview", "avatar-placeholder", "btn-s2-baixar-prompts",
    "btn-s2-copiar-prompts", "btn-s2-produzir-pendentes", "btn-s2-refazer-plano",
    "btn-upload-avatar-global", "d3-bible-clothing", "d3-bible-lighting",
    "d3-bible-main-obj", "d3-bible-rules-list", "d3-bible-world",
    "d3-cenas-aprovadas-val", "d3-cenas-count-badge", "d3-cenas-timeline",
    "d3-continuidade-val", "d3-final-grade-badge", "d3-intervencao-humana-val",
    "d3-retencao-prevista-val", "d3-score-visual-val", "flow-conta",
    "flow-status-dot", "flow-status-texto", "input-avatar-global",
    "label-capcut-trans-dur", "s2-badge-diretor-score", "s2-cnt-midia-anim",
    "s2-montagem-msg", "s2-nle-audio-track-label", "s2-nle-track-audio",
    "s2-painel-plano-edicao", "s2-plano-edicao-counters", "s2-plano-edicao-lista",
    "s2-prod-modelo", "s2-storyboard-grid", "s2-total-cenas-label",
}


def _ler(caminho: Path) -> str:
    return caminho.read_text(encoding="utf-8", errors="replace")


def _ids_do_html(html: str) -> set:
    return set(re.findall(r'id="([^"]+)"', html))


def _ids_referenciados(fonte: str) -> set:
    achados = set()
    for padrao in PADROES_ID:
        achados.update(re.findall(padrao, fonte))
    return achados


def _script_inline(html: str) -> str:
    blocos = re.findall(r"(?s)<script(?![^>]*src=)[^>]*>(.*?)</script>", html)
    return "\n".join(blocos)


class TestContratoIdsIndex(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _ler(INDEX)
        cls.ids_html = _ids_do_html(cls.html)
        cls.app_js = _ler(APP_JS)
        cls.inline = _script_inline(cls.html)

    def test_01_ids_referenciados_no_app_js_existem_no_html(self):
        referenciados = _ids_referenciados(self.app_js) - IDS_DINAMICOS_APP_JS
        faltando = referenciados - self.ids_html
        novos = sorted(faltando - HOOKS_ORFAOS_PRE_REDESIGN)
        self.assertEqual(
            novos, [],
            "REGRESSAO: IDs usados pelo app.js que sumiram de static/index.html: %s" % novos,
        )

    def test_02_ids_referenciados_no_script_inline_existem_no_html(self):
        referenciados = _ids_referenciados(self.inline) - IDS_DINAMICOS_APP_JS
        faltando = sorted(referenciados - self.ids_html)
        self.assertEqual(
            faltando, [],
            "IDs usados no <script> inline que NAO existem em static/index.html: %s" % faltando,
        )

    def test_03_nenhum_id_do_baseline_foi_perdido(self):
        self.assertTrue(BASELINE.exists(), "Baseline ausente: %s" % BASELINE)
        baseline = {l.strip() for l in _ler(BASELINE).splitlines() if l.strip()}
        perdidos = sorted(baseline - self.ids_html)
        self.assertEqual(
            perdidos, [],
            "IDs do baseline (pre-redesign) que sumiram do index.html: %s" % perdidos,
        )

    def test_04_assets_estaticos_referenciados_e_presentes(self):
        for nome in ("static/app.js", "static/style.css"):
            self.assertIn(
                nome, self.html, "index.html nao referencia /%s" % nome
            )
        for arquivo in ("index.html", "app.js", "style.css", "placeholder_cena.png"):
            caminho = STATIC_DIR / arquivo
            self.assertTrue(caminho.exists(), "Asset ausente: %s" % caminho)
            self.assertGreater(caminho.stat().st_size, 0, "Asset vazio: %s" % caminho)

    def test_05_google_fonts_no_head(self):
        head = self.html.split("</head>", 1)[0]
        self.assertIn(
            "fonts.googleapis.com", head,
            "O <link> do Google Fonts (Space Grotesk/Inter/JetBrains Mono) saiu do <head>.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
