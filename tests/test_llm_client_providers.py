"""Testes da camada LLM: client (retry/schema), providers configuráveis,
e ausência de modelo hardcoded ("deepseek-chat")."""
import inspect
import json
import unittest

import config
from services import llm_providers
from services.llm_client import LLMClient, LLMInvalidResponse, LLMSchemaError, extrair_json
from services.llm_providers import DeepSeekProvider, LocalProvider, criar_provider


class FakeProvider:
    name = "fake"

    def __init__(self, respostas=None):
        self.respostas = list(respostas or [])
        self.chamadas = 0
        self.model = "fake-model"

    def generate(self, messages, model=None, temperature=0.2, max_tokens=2000):
        self.chamadas += 1
        if self.respostas:
            return self.respostas.pop(0)
        return json.dumps({"ok": True})


class TestLLMClient(unittest.TestCase):
    def test_extrair_json_com_fences(self):
        texto = "```json\n{\"a\": 1}\n```"
        self.assertEqual(extrair_json(texto), {"a": 1})

    def test_extrair_json_invalido(self):
        with self.assertRaises(LLMInvalidResponse):
            extrair_json("não tem json aqui")

    def test_complete_json_ok(self):
        client = LLMClient(FakeProvider(), retry_delay=0, max_retries=2)
        dados = client.complete_json("sistema", "usuário")
        self.assertEqual(dados, {"ok": True})

    def test_complete_json_schema_erro_retry(self):
        provider = FakeProvider(respostas=['{"a":1}', '{"a":1}', '{"a":1}'])
        client = LLMClient(provider, retry_delay=0, max_retries=2)
        with self.assertRaises(LLMSchemaError):
            client.complete_json("s", "u", validator=lambda d: ["campo x ausente"])
        self.assertEqual(provider.chamadas, 3)

    def test_complete_json_retry_transiente(self):
        def falha():
            raise RuntimeError("boom")

        provider = FakeProvider(respostas=[falha, '{"ok": true}'])
        client = LLMClient(provider, retry_delay=0, max_retries=2)
        dados = client.complete_json("s", "u")
        self.assertEqual(dados, {"ok": True})
        self.assertEqual(provider.chamadas, 2)


class TestProviders(unittest.TestCase):
    def test_criar_provider_local(self):
        p = criar_provider("local")
        self.assertIsInstance(p, LocalProvider)

    def test_criar_provider_deepseek_modelo_configuravel(self):
        p = criar_provider("deepseek", model="deepseek-coder", api_key="k")
        self.assertIsInstance(p, DeepSeekProvider)
        self.assertEqual(p.model, "deepseek-coder")

    def test_deepseek_sem_modelo_erro(self):
        p = DeepSeekProvider(api_key="k", model="")
        with self.assertRaises(Exception) as ctx:
            p.generate([{"role": "user", "content": "oi"}])
        self.assertIn("LLM_MODEL", str(ctx.exception))

    def test_deepseek_sem_chave_erro(self):
        p = DeepSeekProvider(api_key="", model="m")
        with self.assertRaises(Exception):
            p.generate([{"role": "user", "content": "oi"}])

    def test_provider_desconhecido(self):
        with self.assertRaises(ValueError):
            criar_provider("nao_existe")

    def test_nenhum_hardcode_deepseek_chat(self):
        fonte_providers = inspect.getsource(llm_providers)
        self.assertNotIn("deepseek-chat", fonte_providers)
        fonte_config = inspect.getsource(config)
        self.assertNotIn("deepseek-chat", fonte_config)
        self.assertIsInstance(config.LLM_MODEL, str)
        self.assertTrue(hasattr(config, "LLM_PROVIDER"))
        self.assertTrue(hasattr(config, "LLM_API_KEY"))


if __name__ == "__main__":
    unittest.main()
