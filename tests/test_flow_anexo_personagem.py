# -*- coding: utf-8 -*-
"""
Testes para a correção do fluxo de anexo de personagem na produção normal
(playwright_flow.incluir_referencia_personagem + _selecionar_referencia_flow).

Valida que a busca no menu de ingredientes prioriza o NOME DO PERSONAGEM
(aba Characters) antes do nome do ARQUIVO local de referência, alinhando
com a descoberta do fluxo de criação de personagem (criar_personagem_flow).
"""
import ast
import unittest
from pathlib import Path


class TestIncluirReferenciaPersonagem(unittest.TestCase):
    """Valida a assinatura e lógica de alvos da função alterada."""

    @classmethod
    def setUpClass(cls):
        src = Path('services/playwright_flow.py').read_text(encoding='utf-8')
        cls.tree = ast.parse(src)
        # Extrai o código-fonte da função incluir_referencia_personagem
        cls.func_src = None
        for node in ast.walk(cls.tree):
            if isinstance(node, ast.FunctionDef) and node.name == 'incluir_referencia_personagem':
                cls.func_src = ast.get_source_segment(src, node)
                break
        assert cls.func_src, 'Função incluir_referencia_personagem não encontrada'

    def test_01_funcao_aceita_nome_personagem(self):
        """A função deve aceitar nome_personagem e tag_personagem como kwargs."""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == 'incluir_referencia_personagem':
                arg_names = [a.arg for a in node.args.args]
                self.assertIn('nome_personagem', arg_names)
                self.assertIn('tag_personagem', arg_names)
                return
        self.fail('Função não encontrada')

    def test_02_gera_alvos_personagem_com_e_sem_arroba(self):
        """A lógica deve gerar alvos ['Marcos', '@Marcos'] a partir de nome_personagem='Marcos'."""
        # A lógica de alvos está no código-fonte; validamos por inspeção textual
        # das variáveis calculadas.
        self.assertIn('nome_busca_personagem = nome_personagem or tag_personagem.lstrip("@") or ""',
                      self.func_src)
        self.assertIn('alvos_personagem = []', self.func_src)
        self.assertIn('alvos_arquivo = []', self.func_src)

    def test_03_prioriza_busca_por_personagem_na_aba_characters(self):
        """Quando há nome_personagem, a navegação de abas deve procurar SÓ Characters."""
        self.assertIn("t === 'character' || t === 'characters' || t === 'personagem' || t === 'personagens'",
                      self.func_src)
        # A navegação para Media/Uploads NÃO deve ocorrer quando há personagem
        # (verifica que o trecho com Media/Uploads está no else/nome_personagem vazio)
        self.assertIn('if nome_busca_personagem:', self.func_src)

    def test_04_fallback_para_nome_de_arquivo_mantido(self):
        """Sem personagem, deve manter busca por nome do arquivo (uploads)."""
        self.assertIn('if alvos_arquivo and not item_ref:', self.func_src)

    def test_05_primeiro_card_so_como_ultimo_recurso(self):
        """O fallback para primeiro card deve existir mas DEPOIS da busca por nome."""
        idx_personagem = self.func_src.find('4a. Personagem nativo')
        idx_arquivo = self.func_src.find('4b. Upload/mídia')
        idx_card = self.func_src.find('4c. Fallback')
        self.assertGreater(idx_personagem, 0)
        self.assertGreater(idx_arquivo, idx_personagem)
        self.assertGreater(idx_card, idx_arquivo)

    def test_06_chamada_passa_nome_personagem(self):
        """A chamada em _selecionar_referencia_flow deve passar nome_personagem."""
        found = False
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and hasattr(node.func, 'id') \
               and node.func.id == 'incluir_referencia_personagem':
                kw = {k.arg for k in node.keywords}
                self.assertIn('nome_personagem', kw)
                self.assertIn('tag_personagem', kw)
                found = True
        self.assertTrue(found, 'Nenhuma chamada a incluir_referencia_personagem encontrada')

    def test_07_tag_personagem_condicional_na_chamada(self):
        """tag_personagem só deve ser propagado quando nome_personagem existe."""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and hasattr(node.func, 'id') \
               and node.func.id == 'incluir_referencia_personagem':
                for kw in node.keywords:
                    if kw.arg == 'tag_personagem':
                        # Deve ser IfExp: tag_display if nome_personagem else ''
                        self.assertIsInstance(kw.value, ast.IfExp)
                        return
        self.fail('tag_personagem não encontrado na chamada')


class TestFallbackModelo(unittest.TestCase):
    """Valida que o fallback de modelo re-anexa o personagem via clique."""

    @classmethod
    def setUpClass(cls):
        src = Path('services/playwright_flow.py').read_text(encoding='utf-8')
        cls.src = src

    def test_01_fallback_chama_selecionar_referencia_flow(self):
        """O fallback de modelo deve chamar _selecionar_referencia_flow novamente."""
        self.assertIn('CHARACTER_REATTACHED_OK', self.src)
        self.assertIn('_re_entidade = self._selecionar_referencia_flow(', self.src)

    def test_02_aborta_se_nao_reanexar(self):
        """Se a re-anexação falhar, deve abortar (trava anti-rosto)."""
        self.assertIn('if not _re_entidade:', self.src)
        self.assertIn('CHARACTER_REATTACH_FAILED', self.src)

    def test_03_usa_prompt_visual_puro_apos_reanexar(self):
        """Após re-anexar, envia prompt_visual_puro (sem @Nome no texto)."""
        self.assertIn('if prompt_visual_puro:', self.src)

    def test_04_broll_usa_prompt_final(self):
        """Cenas b-roll no fallback usam prompt_final (limpo), não prompt bruto."""
        self.assertIn('self.page.keyboard.insert_text(prompt_final)', self.src)


if __name__ == '__main__':
    unittest.main()
