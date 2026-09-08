# -*- coding: utf-8 -*-
"""
Testes da REGRA DE MARCA DO CANAL (público 55+ — nunca gerar mulher
vestindo terno/roupa formal de negócios).

Cobre os 3 pontos de implementação:
1. deepseek_prompt_service.analisar_contexto_global — diretriz no prompt do LLM
2. visual_presets_service — negativo nos presets (via NEGATIVE_LOCK_BASE)
3. prompt_builder_service._sanitizar_prompt_marca_canal — validação pré-envio
"""
import unittest
from pathlib import Path


class TestRegraMarcaDeepSeek(unittest.TestCase):
    """1. A diretriz deve estar no prompt enviado ao LLM (analisar_contexto_global)."""

    @classmethod
    def setUpClass(cls):
        cls.src = (Path(__file__).resolve().parent.parent / 'services' / 'deepseek_prompt_service.py').read_text(encoding='utf-8')

    def test_diretriz_publico_55_presente(self):
        """O user_prompt deve conter 'Target audience: adults aged 55+'."""
        self.assertIn('Target audience: adults aged 55+', self.src)

    def test_diretriz_proibicao_business_suit(self):
        """O user_prompt deve conter a proibição explícita de business suit."""
        self.assertIn('NEVER depict a woman wearing a business suit', self.src)

    def test_diretriz_no_contexto_do_user_prompt(self):
        """A diretriz deve estar dentro do user_prompt (após char_rule, antes de CHOSEN VISUAL STYLE)."""
        # Localiza a posição dos marcadores
        idx_char_rule = self.src.find('{char_rule}')
        idx_brand = self.src.find('CHANNEL BRAND DIRECTIVE')
        idx_style = self.src.find('CHOSEN VISUAL STYLE')
        self.assertGreater(idx_brand, -1, 'CHANNEL BRAND DIRECTIVE não encontrado')
        self.assertGreater(idx_style, idx_brand, 'CHANNEL BRAND DIRECTIVE deve vir antes de CHOSEN VISUAL STYLE')
        self.assertGreater(idx_brand, idx_char_rule, 'CHANNEL BRAND DIRECTIVE deve vir após char_rule')


class TestRegraMarcaPresets(unittest.TestCase):
    """2. A regra deve estar nos negative_defaults de TODOS os presets."""

    @classmethod
    def setUpClass(cls):
        cls.src = (Path(__file__).resolve().parent.parent / 'services' / 'visual_presets_service.py').read_text(encoding='utf-8')

    def test_constante_marca_definida(self):
        """NEGATIVE_LOCK_MARCA deve existir e conter a regra."""
        self.assertIn('NEGATIVE_LOCK_MARCA = ', self.src)
        self.assertIn('no woman in business suit', self.src)

    def test_concatenada_na_base(self):
        """NEGATIVE_LOCK_BASE deve concatenar NEGATIVE_LOCK_MARCA."""
        self.assertIn('NEGATIVE_LOCK_BASE', self.src)
        self.assertIn('+ NEGATIVE_LOCK_MARCA', self.src)

    def test_todos_presets_usam_base(self):
        """Todos os presets usam NEGATIVE_LOCK_BASE direta ou indiretamente."""
        # Todos os negative_defaults nos presets derivam de NEGATIVE_LOCK_BASE
        self.assertGreaterEqual(self.src.count('"negative_defaults"'), 11)
        # Nenhum preset define negative_defaults SEM usar NEGATIVE_LOCK_BASE
        import re
        sem_base = re.findall(r'"negative_defaults":\s*"(?!NEGATIVE_LOCK_BASE)', self.src)
        self.assertEqual(len(sem_base), 0, f'Presets sem NEGATIVE_LOCK_BASE: {sem_base}')


class TestRegraMarcaPromptBuilder(unittest.TestCase):
    """3. A validação pré-envio deve detectar e sanitizar 'suit'."""

    @classmethod
    def setUpClass(cls):
        import sys
        import importlib.util
        root = str(Path(__file__).resolve().parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        # Carrega o módulo DIRETAMENTE do arquivo (evita cache antigo do sys.modules)
        path_pbs = Path(root) / 'services' / 'prompt_builder_service.py'
        spec = importlib.util.spec_from_file_location('prompt_builder_service_marca', path_pbs)
        pbs = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = pbs
        spec.loader.exec_module(pbs)
        cls.pbs = pbs
        cls._PADRAO_TERMO_SUIT = pbs._PADRAO_TERMO_SUIT

    def _sanitizar(self, prompt):
        """Chama a função de módulo diretamente (evita binding de método)."""
        return self.pbs._sanitizar_prompt_marca_canal(prompt)

    def test_rejeita_business_suit(self):
        """Prompt com 'woman in a business suit' deve ser sanitizado."""
        prompt = "A woman in a business suit stands in a garden, smiling at the camera."
        resultado = self._sanitizar(prompt)
        self.assertNotIn('suit', resultado.lower())
        self.assertIn('casual comfortable outfit', resultado.lower())

    def test_rejeita_formal_attire(self):
        """Prompt com 'formal business attire' deve ser sanitizado."""
        prompt = "A female presenter wearing formal business attire talks to the camera."
        resultado = self._sanitizar(prompt)
        self.assertNotIn('formal business attire', resultado.lower())
        self.assertIn('casual comfortable outfit', resultado.lower())

    def test_rejeita_blazer(self):
        """Prompt com 'blazer' deve ser sanitizado."""
        prompt = "A woman in a navy blazer explains gardening tips."
        resultado = self._sanitizar(prompt)
        self.assertNotIn('blazer', resultado.lower())

    def test_nao_afeta_prompt_sem_terno(self):
        """Prompt sem termos proibidos deve passar intacto."""
        prompt = "A gardener in casual outdoor attire with a sun hat tends rose bushes."
        resultado = self._sanitizar(prompt)
        self.assertEqual(resultado, prompt)

    def test_padrao_detecta_variacoes(self):
        """O padrão regex deve detectar as variações principais."""
        casos = [
            "business suit",
            "formal business attire",
            "wearing a suit",
            "suit and tie",
            "blazer",
            "tuxedo",
        ]
        for termo in casos:
            self.assertIsNotNone(
                self._PADRAO_TERMO_SUIT.search(f"A woman wearing {termo} stands here."),
                f'Padrão não detectou: {termo}',
            )


if __name__ == '__main__':
    unittest.main()
