# -*- coding: utf-8 -*-
"""
tests/test_animar_i2v_cancelamento.py — TAREFAS 1 e 2 (SEM tocar no Flow)
=======================================================================
Valida por FONTE/mensagem (mock — nenhum Chrome, nenhuma conta real):
  (a) o prompt de movimento tem guarda para nunca chegar vazio (uses_char);
  (b) animar_ok=False COM PNG de referência ⇒ cancelamento: nenhum Control+A/
      Backspace, nenhuma geração T2V, cena em STATUS_ERRO e return (False, msg);
  (c) SEM PNG de referência o caminho antigo (T2V) continua existindo;
  (d) a mensagem do cancelamento não casa com os gatilhos de quarentena/rotação.
"""
import pathlib
import unittest

SRC = pathlib.Path("services/playwright_flow.py").read_text(encoding="utf-8")
MSG = "Animação cancelada: não foi possível fixar a imagem. Nenhum crédito gasto."
MARCADOR = "if not animar_ok and tem_png_referencia:"


class TestPromptNuncaVazio(unittest.TestCase):

    def test_01_guarda_do_prompt_final_existe(self):
        self.assertIn("TAREFA 1 — o prompt de movimento NUNCA pode chegar vazio", SRC)
        self.assertIn('ref_tag=(tag_char if uses_char else "")', SRC)
        self.assertIn('prompt_final = prompt_final or "Cinematic slow camera motion."', SRC)

    def test_02_log_de_tamanho_do_prompt(self):
        self.assertIn("PROMPT_ANIMACAO: {len(prompt_final)} caracteres", SRC)

    def test_03_guarda_vem_antes_da_chamada_de_animacao(self):
        i_guarda = SRC.index('ref_tag=(tag_char if uses_char else "")')
        i_chamada = SRC.index("_animar_imagem_galeria_flow(\n                        projeto_id, arquivo_img_ref")
        self.assertLess(i_guarda, i_chamada)


class TestCancelamentoSemT2V(unittest.TestCase):

    def test_01_cancelamento_vem_antes_do_control_a(self):
        i_canc = SRC.index(MARCADOR)
        # primeiro Control+A DEPOIS do cancelamento (há outros no arquivo)
        i_kb = SRC.index('self.page.keyboard.press("Control+A")', i_canc)
        self.assertGreater(i_kb, i_canc, "o cancelamento precisa vir ANTES de apagar o editor")

    def test_02_bloco_de_cancelamento_nao_gera_nada(self):
        trecho = SRC[SRC.index(MARCADOR):]
        trecho = trecho[:trecho.index("if not animar_ok:")]
        self.assertIn('"Animação cancelada: não foi possível fixar a imagem. "', trecho)
        self.assertIn("Nenhum crédito gasto.", trecho)
        self.assertIn("scene_plan_svc.STATUS_ERRO", trecho)
        self.assertIn('"erro_msg": _msg_canc', trecho)
        self.assertIn("return False, _msg_canc", trecho)
        for proibido in ("keyboard.press", "insert_text", "_set_output_mode", "_safe_click"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, trecho)

    def test_03_mensagem_nao_aciona_quarentena_nem_rotacao(self):
        low = MSG.lower()
        for gatilho in ("atividade incomum", "credito_esgotado", "créditos esgotados",
                        "unusual activity", "limite diário", "credito_esgotado_video"):
            with self.subTest(gatilho=gatilho):
                self.assertNotIn(gatilho, low)
        # o fallback reativo só pausa a fila com "notamos uma atividade incomum"
        self.assertNotIn("notamos uma atividade incomum", low)

    def test_04_sem_png_mantem_o_comportamento_antigo(self):
        self.assertIn('self.page.keyboard.press("Control+A")', SRC)
        self.assertIn('self.page.keyboard.press("Backspace")', SRC)
        # T4 — o ramo `if tem_png_referencia:` (morto) foi removido; o caminho SEM PNG
        # continua imediatamente depois do bloco de cancelamento.
        i = SRC.index("TAREFA 4 — chega aqui SOMENTE sem PNG de referência")
        self.assertIn("self._safe_click(editor)", SRC[i:i + 700])

    def test_05_contrato_tuple_preservado(self):
        self.assertIn("return False, _msg_canc", SRC)
        self.assertIn("def _processar_cena_individual(", SRC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
