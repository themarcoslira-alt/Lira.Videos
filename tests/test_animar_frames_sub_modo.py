# -*- coding: utf-8 -*-
"""
tests/test_animar_frames_sub_modo.py — TAREFAS 1-4 (fonte/mock, SEM tocar no Flow)
=================================================================================
  (a) `_set_output_mode("video")` sem `sub_modo` continua no padrão "elementos";
  (b) com `sub_modo="frames"` o alvo é o rádio "Frames" (nunca Elementos) e há log
      de aria-checked ANTES/DEPOIS;
  (c) o Passo 6 do fluxo 'Animar' chama com `sub_modo="frames"`;
  (d) o chip ancorado do DOM real (`.frame-trigger`) é aceito e há
      `ANIMAR_FALHA_PASSO_5` quando nada casa;
  (e) o cancelamento explícito da rodada anterior segue intacto;
  (T4) o ramo morto `if tem_png_referencia:` foi removido.
"""
import pathlib
import re
import unittest

SRC = pathlib.Path("services/playwright_flow.py").read_text(encoding="utf-8")


class TestSubModoFrames(unittest.TestCase):

    def test_01_assinatura_com_default_elementos(self):
        trecho = SRC[SRC.index("def _set_output_mode("):][:700]
        self.assertIn('sub_modo: str = "elementos"', trecho)

    def test_02_com_frames_o_alvo_e_o_radio_frames(self):
        i = SRC.index('_sub_sel = ("frames" if str(sub_modo).lower() == "frames" else "elementos")')
        trecho = SRC[i:i + 2200]
        self.assertIn('_rd_frames = _grupo.filter(has_text=re.compile(r"frames", re.I))', trecho)
        self.assertIn('_rd_elementos = _grupo.filter(has_text=re.compile(r"elementos", re.I))', trecho)
        self.assertIn('_alvo = _rd_frames if _sub_sel == "frames" else _rd_elementos', trecho)
        self.assertIn('_alvo.get_attribute("aria-checked") != "true"', trecho)
        self.assertNotIn('_rd_elementos.click', trecho)
        self.assertIn("Sub-modo ANTES:", trecho)
        self.assertIn("Sub-modo DEPOIS:", trecho)

    def test_03_passo6_pede_frames(self):
        i = SRC.index('ok_vm = self._set_output_mode("video"')
        linha = SRC[i:SRC.index("\n", i)]
        self.assertIn('sub_modo="frames"', linha)

    def test_04_so_um_chamador_pede_frames(self):
        chamadas = re.findall(r"_set_output_mode\((.*?)\)", SRC, re.S)
        com_frames = [c for c in chamadas if 'sub_modo="frames"' in c]
        self.assertEqual(len(com_frames), 1, "apenas o Passo 6 do 'Animar' pode pedir frames")
        self.assertGreaterEqual(len(chamadas), 8, "os demais chamadores continuam existindo")

    def test_05_chip_frame_trigger_e_falha_passo_5(self):
        self.assertIn('"flow-ingredient-bar .frame-trigger flow-image-ingredient-chip"', SRC)
        self.assertIn('".frame-trigger flow-image-ingredient-chip"', SRC)
        self.assertIn('"flow-image-ingredient-chip",', SRC)      # genérico preservado
        self.assertIn("ANIMAR_FALHA_PASSO_5:", SRC)

    def test_06_cancelamento_explicito_intacto(self):
        marcador = "if not animar_ok and tem_png_referencia:"
        trecho = SRC[SRC.index(marcador):]
        trecho = trecho[:trecho.index("if not animar_ok:")]
        self.assertIn('"Animação cancelada: não foi possível fixar a imagem. "', trecho)
        self.assertIn("scene_plan_svc.STATUS_ERRO", trecho)
        self.assertIn("return False, _msg_canc", trecho)
        for proibido in ("keyboard.press", "insert_text"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, trecho)

    def test_07_ramo_morto_removido(self):
        i = SRC.index("TAREFA 4 — chega aqui SOMENTE sem PNG de referência")
        trecho = SRC[i:i + 700]
        self.assertNotIn("AVISO: Fluxo 'Animar' não conseguiu fixar a imagem", trecho)
        self.assertIn("self._safe_click(editor)", trecho)   # T2V (sem PNG) continua
        self.assertIn('self.page.keyboard.press("Control+A")', SRC)   # caminho sem PNG intacto


if __name__ == "__main__":
    unittest.main(verbosity=2)
