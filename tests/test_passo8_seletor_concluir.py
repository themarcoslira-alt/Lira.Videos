# -*- coding: utf-8 -*-
"""
tests/test_passo8_seletor_concluir.py
Valida a constante SELETORES_BOTAO_CONCLUIR_PERSONAGEM e sua capacidade de casar
com o outerHTML real do botão de conclusão do assistente de personagem do Flow.
NOTA: Estes testes unitários validam a presença da constante e a compatibilidade
de seus seletores de atributo com o HTML do botão, mas NÃO provam em execução real
que o clique conclui a edição (validação E2E requer ciclo real completo no browser).
"""
import unittest
import re
from pathlib import Path
import services.playwright_flow as pf


class TestPasso8SeletorConcluir(unittest.TestCase):

    def test_constante_existe_e_contem_edicao_concluida(self):
        """1. A constante SELETORES_BOTAO_CONCLUIR_PERSONAGEM deve existir em services.playwright_flow

        e conter pelo menos um seletor contemplando 'Edição concluída'.
        """
        self.assertTrue(
            hasattr(pf, "SELETORES_BOTAO_CONCLUIR_PERSONAGEM"),
            "Constante SELETORES_BOTAO_CONCLUIR_PERSONAGEM não encontrada em services.playwright_flow."
        )
        seletores = getattr(pf, "SELETORES_BOTAO_CONCLUIR_PERSONAGEM", [])
        self.assertIsInstance(seletores, (list, tuple))
        tem_edicao_concluida = any(
            "edição concluída" in str(s).lower() or "edicao concluida" in str(s).lower()
            for s in seletores
        )
        self.assertTrue(
            tem_edicao_concluida,
            f"Nenhum seletor em SELETORES_BOTAO_CONCLUIR_PERSONAGEM contém 'Edição concluída': {seletores}"
        )

    def test_seletor_casa_com_outerhtml_real_do_botao(self):
        """2. Avalia os seletores de aria-label da constante contra o outerHTML real

        do botão salvo em scratch/editor_personagem.html.
        """
        html_path = Path("scratch/editor_personagem.html")
        self.assertTrue(html_path.exists(), f"Arquivo {html_path} não encontrado.")
        html_content = html_path.read_text(encoding="utf-8")

        # Extrai o outerHTML do botão com aria-label="Edição concluída"
        match = re.search(r'(<button[^>]+aria-label=["\']Edição concluída["\'][^>]*>.*?</button>)', html_content, re.IGNORECASE | re.DOTALL)
        self.assertIsNotNone(match, "Botão com aria-label='Edição concluída' não encontrado em scratch/editor_personagem.html")
        botao_outer_html = match.group(1)

        # Extrai o atributo aria-label real do botão
        aria_label_match = re.search(r'aria-label=["\']([^"\']+)["\']', botao_outer_html, re.IGNORECASE)
        self.assertIsNotNone(aria_label_match, "Atributo aria-label não encontrado no outerHTML do botão")
        aria_label_val = aria_label_match.group(1)

        self.assertTrue(
            hasattr(pf, "SELETORES_BOTAO_CONCLUIR_PERSONAGEM"),
            "Constante SELETORES_BOTAO_CONCLUIR_PERSONAGEM não encontrada em services.playwright_flow."
        )
        seletores = getattr(pf, "SELETORES_BOTAO_CONCLUIR_PERSONAGEM", [])

        # Avalia se pelo menos um seletor que use aria-label casa com o aria-label real
        casou = False
        for s in seletores:
            m_aria = re.search(r'\[aria-label\*=["\']?([^"\'\]]+)["\']?\s*(?:i)?\]', s, re.IGNORECASE)
            if m_aria:
                termo = m_aria.group(1).lower()
                if termo in aria_label_val.lower():
                    casou = True
                    break

        self.assertTrue(
            casou,
            f"Nenhum seletor com aria-label da constante casou com o aria-label real '{aria_label_val}'."
        )


if __name__ == "__main__":
    unittest.main()
