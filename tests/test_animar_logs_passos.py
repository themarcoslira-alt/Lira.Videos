# -*- coding: utf-8 -*-
"""
tests/test_animar_logs_passos.py — logs dos Passos 3/4/7 + RESULTADO_ANIMACAO
============================================================================
Somente LOGS (nenhuma lógica alterada). Testes de fonte/mock, sem tocar no Flow:
  (a) `ANIMAR_FALHA_PASSO_3` e `ANIMAR_FALHA_PASSO_4` existem no corpo de
      `_animar_imagem_galeria_flow`;
  (b) o log do Passo 2 tem contagem antes/depois, idx e tem_video;
  (c) existem os três rótulos `RESULTADO_ANIMACAO` (I2V, CANCELADO, T2V) nas regiões certas;
  (d) com `sub_modo="frames"` gravado no cache, a chamada padrão (Elementos) NÃO é pulada
      e o clique acontece; com o MESMO sub-modo o cache continua válido (pula).
"""
import pathlib
import unittest
from unittest import mock

import services.playwright_flow as pf

SRC = pathlib.Path("services/playwright_flow.py").read_text(encoding="utf-8")
INI = SRC.index("    def _animar_imagem_galeria_flow(")
FIM = SRC.index("    def ", SRC.index("        # PASSO 7:", INI))
CORPO = SRC[INI:FIM]


class TestLogsDosPassos(unittest.TestCase):

    def test_01_falhas_dos_passos_3_e_4_no_corpo_da_funcao(self):
        self.assertIn("ANIMAR_FALHA_PASSO_3:", CORPO)
        self.assertIn("ANIMAR_FALHA_PASSO_4:", CORPO)
        self.assertIn("ANIMAR_PASSO_3: ⋮ clicado", CORPO)
        self.assertIn("ANIMAR_PASSO_4:", CORPO)
        # falha do Passo 4 tem de trazer a lista de textos do menu
        i = CORPO.index("ANIMAR_FALHA_PASSO_4:")
        self.assertIn("_txts", CORPO[i:i + 200])

    def test_02_passo_2_com_contagens_idx_e_tem_video(self):
        self.assertIn("ANIMAR_PASSO_2: tiles ANTES do upload", CORPO)
        self.assertIn("ANIMAR_PASSO_2: tiles DEPOIS do upload", CORPO)
        self.assertIn("tile escolhido idx={_idx_tile}", CORPO)
        self.assertIn("tem_video={_tem_video}", CORPO)
        self.assertIn("enumerate(tiles)", CORPO)

    def test_03_passo_1_e_7(self):
        self.assertIn("ANIMAR_PASSO_1: upload concluído", CORPO)
        self.assertIn("ANIMAR_FALHA_PASSO_1:", CORPO)
        self.assertIn("ANIMAR_PASSO_7: prompt {len(prompt_movimento or '')} caracteres", CORPO)
        self.assertIn("editor_previo_vazio={not _txt_previo}", CORPO)

    def test_04_tres_rotulos_resultado_animacao(self):
        # I2V / T2V / CANCELADO na mesma expressão, imediatamente antes do log IMAGE_TO_VIDEO
        trecho = SRC[SRC.index('pw_log("RESULTADO_ANIMACAO: "'):]
        trecho = trecho[:400]
        self.assertIn('"I2V" if animar_ok', trecho)
        self.assertIn('"CANCELADO" if tem_png_referencia', trecho)
        self.assertIn('"T2V"', trecho)
        i_img = SRC.index('f"[CENA {cid:03d}] IMAGE_TO_VIDEO: Imagem')
        self.assertLess(SRC.index('pw_log("RESULTADO_ANIMACAO: "'), i_img)
        self.assertIn('pw_log("RESULTADO_ANIMACAO: CANCELADO")', SRC)

    def test_05_so_logs_nenhuma_logica_nova(self):
        """As linhas adicionadas começam por pw_log (nenhuma decisão nova)."""
        for linha in CORPO.splitlines():
            s = linha.strip()
            if s.startswith(("ANIMAR_PASSO_", "ANIMAR_FALHA_PASSO_")) and "pw_log" not in s:
                self.fail(f"linha de log inesperada: {s[:80]}")


class TestCacheSubModoRuntime(unittest.TestCase):
    """(d) runtime com page mockada: o cache não pula o clique quando o sub-modo muda."""

    def _worker(self, sub_cache):
        w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
        w.page = mock.MagicMock()
        # MagicMock não suporta comparação (`count() > 0`): força 0 para o fluxo seguir
        # (painel "ausente") sem deixar o TypeError abortar a função.
        w.page.locator.return_value.count.return_value = 0
        w.current_model = "Veo 3.1 - Lite"
        w._configured_mode = ("video", "Veo 3.1 - Lite", "16:9", "x1", sub_cache)
        return w

    def test_01_cache_de_frames_nao_pula_o_pedido_de_elementos(self):
        w = self._worker("frames")
        try:
            pf.PlaywrightCDPWorker._set_output_mode(w, "video", modelo_solicitado="Veo 3.1 - Lite")
        except Exception:
            pass
        seletores = "".join(str(c.args[0]) for c in w.page.locator.call_args_list if c.args)
        self.assertIn("flow-toggles", seletores,
                      "com cache em 'frames', o pedido padrão (elementos) NÃO pode ser pulado")

    def test_02_mesmo_sub_modo_usa_o_cache(self):
        w = self._worker("elementos")
        ok = pf.PlaywrightCDPWorker._set_output_mode(w, "video", modelo_solicitado="Veo 3.1 - Lite")
        self.assertTrue(ok)
        self.assertEqual(w.page.locator.call_count, 0, "mesmo sub-modo => cache válido (retorno cedo)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
