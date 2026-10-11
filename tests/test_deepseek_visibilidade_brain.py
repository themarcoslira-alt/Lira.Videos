"""
Testes de validação da Visibilidade DeepSeek no Brain e Alerta de Falha
Cobre TAREFA 1, TAREFA 2, TAREFA 3, TAREFA 4 e TAREFA 5.
"""
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import json
import shutil

ROOT_DIR = Path(__file__).resolve().parent.parent


class TestDeepSeekVisibilidadeBrain(unittest.TestCase):
    def setUp(self):
        self.temp_project = "proj_test_deepseek_visibilidade"
        self.pdir = ROOT_DIR / "projetos" / self.temp_project
        if self.pdir.exists():
            shutil.rmtree(self.pdir)
        self.pdir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self.pdir.exists():
            shutil.rmtree(self.pdir)

    def test_tarefa1_brain_sucesso_deepseek_ok(self):
        """Valida que quando DeepSeek funciona, deepseek_status é 'ok' e prompts_fonte é 'deepseek'."""
        from services.brain_system_director import BrainSystemDirector

        director = BrainSystemDirector(self.temp_project)
        entrada = {
            "projeto_id": self.temp_project,
            "roteiro_transcricao": {"text": "A beautiful garden scene with organic compost.", "duration": 8.0},
            "cenas": [
                {"id": 1, "texto": "A beautiful garden scene.", "tipo": "broll_macro"},
                {"id": 2, "texto": "With organic compost.", "tipo": "environment"}
            ]
        }

        mock_scenes = [
            {"id": 1, "scene_index": 1, "prompt_imagem": "Macro of garden soil", "prompt_animacao": "Slow push-in"},
            {"id": 2, "scene_index": 2, "prompt_imagem": "Wide garden angle", "prompt_animacao": "Pan right"}
        ]

        with patch("services.brain_system_director.gerar_prompts_lote", return_value={"scenes": mock_scenes}):
            res = director.orquestar_producao_completa(entrada, options={"include_transicoes": False})

        self.assertIn("deepseek", res)
        ds = res["deepseek"]
        self.assertEqual(ds["status"], "ok")
        self.assertIsNone(ds["erro"])
        self.assertEqual(ds["prompts_fonte"], "deepseek")

    def test_tarefa1_brain_erro_402_sem_credito(self):
        """Valida que erro HTTP 402 resulta em status 'sem_credito' e prompts_fonte 'fallback_local'."""
        from services.brain_system_director import BrainSystemDirector

        director = BrainSystemDirector(self.temp_project)
        entrada = {
            "projeto_id": self.temp_project,
            "roteiro_transcricao": {"text": "A beautiful garden scene.", "duration": 4.0},
            "cenas": [
                {"id": 1, "texto": "A beautiful garden scene.", "tipo": "broll_macro"}
            ]
        }

        with patch("services.brain_system_director.gerar_prompts_lote", side_effect=RuntimeError("Erro HTTP 402: Insufficient Balance")):
            res = director.orquestar_producao_completa(entrada, options={"include_transicoes": False})

        self.assertIn("deepseek", res)
        ds = res["deepseek"]
        self.assertEqual(ds["status"], "sem_credito")
        self.assertIn("402", ds["erro"])
        self.assertEqual(ds["prompts_fonte"], "fallback_local")
        # Garante que fallback local gerou prompt de qualquer forma
        cena1 = res["cenas"]["cena_1"]
        self.assertTrue(len(cena1["prompts"]["prompt_imagem"]) > 10)

    def test_tarefa1_brain_erro_401_chave_invalida(self):
        """Valida que erro HTTP 401 resulta em status 'chave_invalida'."""
        from services.brain_system_director import BrainSystemDirector

        director = BrainSystemDirector(self.temp_project)
        entrada = {
            "projeto_id": self.temp_project,
            "roteiro_transcricao": {"text": "A beautiful garden scene.", "duration": 4.0},
            "cenas": [
                {"id": 1, "texto": "A beautiful garden scene.", "tipo": "broll_macro"}
            ]
        }

        with patch("services.brain_system_director.gerar_prompts_lote", side_effect=RuntimeError("Erro HTTP 401 da API DeepSeek: Unauthorized")):
            res = director.orquestar_producao_completa(entrada, options={"include_transicoes": False})

        self.assertIn("deepseek", res)
        ds = res["deepseek"]
        self.assertEqual(ds["status"], "chave_invalida")
        self.assertEqual(ds["prompts_fonte"], "fallback_local")

    def test_tarefa1_brain_erro_conexao(self):
        """Valida que erro de rede / timeout resulta em status 'sem_conexao'."""
        from services.brain_system_director import BrainSystemDirector

        director = BrainSystemDirector(self.temp_project)
        entrada = {
            "projeto_id": self.temp_project,
            "roteiro_transcricao": {"text": "A beautiful garden scene.", "duration": 4.0},
            "cenas": [
                {"id": 1, "texto": "A beautiful garden scene.", "tipo": "broll_macro"}
            ]
        }

        with patch("services.brain_system_director.gerar_prompts_lote", side_effect=RuntimeError("Erro de conexao com DeepSeek: URLError timed out")):
            res = director.orquestar_producao_completa(entrada, options={"include_transicoes": False})

        self.assertIn("deepseek", res)
        ds = res["deepseek"]
        self.assertEqual(ds["status"], "sem_conexao")
        self.assertEqual(ds["prompts_fonte"], "fallback_local")

    def test_tarefa2_api_v2_repassa_deepseek(self):
        """Valida que executar_brain_system_director em api_v2 repassa o bloco deepseek."""
        from services.api_v2 import executar_brain_system_director

        with patch("services.brain_system_director.gerar_prompts_lote", side_effect=RuntimeError("402 Insufficient Balance")):
            res = executar_brain_system_director(
                self.temp_project,
                entrada={
                    "projeto_id": self.temp_project,
                    "roteiro_transcricao": {"text": "Garden test", "duration": 4.0},
                    "cenas": [{"id": 1, "texto": "Garden test", "tipo": "broll_macro"}]
                },
                force=True
            )

        self.assertTrue(res.get("success"))
        self.assertIn("deepseek", res)
        self.assertEqual(res["deepseek"]["status"], "sem_credito")
        self.assertEqual(res["deepseek"]["prompts_fonte"], "fallback_local")

    def test_tarefa3_index_html_contem_elementos(self):
        """Valida que index.html possui o container e label do status do DeepSeek no Brain."""
        index_html = (ROOT_DIR / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="brain-deepseek-status-box"', index_html)
        self.assertIn('id="brain-deepseek-label"', index_html)

    def test_tarefa4_e_5_app_js_contem_tratamentos(self):
        """Valida que app.js possui tratamento visual para todos os estados e auto-teste."""
        app_js = (ROOT_DIR / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("brain-deepseek-label", app_js)
        self.assertIn("brain-deepseek-status-box", app_js)
        self.assertIn("DeepSeek IA ✅", app_js)
        self.assertIn("Sem crédito ⚠️", app_js)
        self.assertIn("Chave inválida ❌", app_js)
        self.assertIn("Sem conexão ❌", app_js)
        self.assertIn("Fallback local 🟡", app_js)
        self.assertIn('testarConexaoAPI("deepseek", true)', app_js)


    def test_eeat_habilitado_no_brain_repassado_ao_lote(self):
        """Valida que eeat_enabled=True é repassado ao gerar_prompts_lote e registrado no output."""
        from services.brain_system_director import BrainSystemDirector

        director = BrainSystemDirector(self.temp_project)
        entrada = {
            "projeto_id": self.temp_project,
            "roteiro_transcricao": {"text": "A beautiful garden scene with organic compost.", "duration": 4.0},
            "cenas": [{"id": 1, "texto": "A beautiful garden scene.", "tipo": "broll_macro"}]
        }

        mock_scenes = [{"id": 1, "scene_index": 1, "prompt_imagem": "Macro of garden soil", "prompt_animacao": "Slow push-in"}]

        with patch("services.brain_system_director.gerar_prompts_lote", return_value={"scenes": mock_scenes}) as mock_lote:
            res = director.orquestar_producao_completa(entrada, options={"include_transicoes": False}, eeat_enabled=True)

        mock_lote.assert_called_once()
        self.assertTrue(mock_lote.call_args[1].get("eeat_enabled"))
        self.assertTrue(res["deepseek"]["eeat_enabled"])

    def test_eeat_desabilitado_no_brain_repassado_ao_lote(self):
        """Valida que eeat_enabled=False desativa o E-E-A-T no lote e registra no output."""
        from services.brain_system_director import BrainSystemDirector

        director = BrainSystemDirector(self.temp_project)
        entrada = {
            "projeto_id": self.temp_project,
            "roteiro_transcricao": {"text": "A beautiful garden scene with organic compost.", "duration": 4.0},
            "cenas": [{"id": 1, "texto": "A beautiful garden scene.", "tipo": "broll_macro"}]
        }

        mock_scenes = [{"id": 1, "scene_index": 1, "prompt_imagem": "Macro of garden soil", "prompt_animacao": "Slow push-in"}]

        with patch("services.brain_system_director.gerar_prompts_lote", return_value={"scenes": mock_scenes}) as mock_lote:
            res = director.orquestar_producao_completa(entrada, options={"include_transicoes": False}, eeat_enabled=False)

        mock_lote.assert_called_once()
        self.assertFalse(mock_lote.call_args[1].get("eeat_enabled"))
        self.assertFalse(res["deepseek"]["eeat_enabled"])

    def test_eeat_checkbox_no_html_e_app_js(self):
        """Valida que index.html tem o checkbox brain-checkbox-eeat e app.js trata o status com EEAT."""
        index_html = (ROOT_DIR / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="brain-checkbox-eeat"', index_html)
        self.assertIn('checked', index_html)

        app_js = (ROOT_DIR / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('brain-checkbox-eeat', app_js)
        self.assertIn('eeat_enabled', app_js)
        self.assertIn('DeepSeek IA ✅ + E-E-A-T', app_js)


if __name__ == "__main__":
    unittest.main()
