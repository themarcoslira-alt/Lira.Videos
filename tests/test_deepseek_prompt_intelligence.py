"""
test_deepseek_prompt_intelligence.py
====================================
Testes unitários e de integração do DeepSeek Prompt Intelligence.
"""

import json
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from config import BASE_DIR, PROJETOS_DIR
import services.deepseek_prompt_service as deepseek_svc
import services.scene_plan_service as scene_plan_svc
import services.visual_presets_service as presets_svc
from app_web import app


class TestDeepSeekPromptIntelligence(unittest.TestCase):

    def setUp(self):
        self.temp_dir = BASE_DIR / "temp_test_keys_dir"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.mock_keys_file = self.temp_dir / "web_keys.json"
        deepseek_svc.WEB_KEYS_FILE = self.mock_keys_file

        self.temp_project = "test_prompt_ia_proj"
        self.pdir = PROJETOS_DIR / self.temp_project
        if self.pdir.exists():
            shutil.rmtree(self.pdir, ignore_errors=True)
        self.pdir.mkdir(parents=True, exist_ok=True)

        self.client = app.test_client()

        # Limpa env se existir durante teste
        self._orig_env_key = os.environ.get("DEEPSEEK_API_KEY")
        if "DEEPSEEK_API_KEY" in os.environ:
            del os.environ["DEEPSEEK_API_KEY"]

    def tearDown(self):
        if self.mock_keys_file.exists():
            self.mock_keys_file.unlink()
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        if self.pdir.exists():
            shutil.rmtree(self.pdir, ignore_errors=True)
        if self._orig_env_key is not None:
            os.environ["DEEPSEEK_API_KEY"] = self._orig_env_key
        deepseek_svc.WEB_KEYS_FILE = BASE_DIR / "web_keys.json"

    def test_gestao_chave_sem_chave(self):
        """Valida status quando não há chave configurada."""
        self.assertIsNone(deepseek_svc.obter_api_key_deepseek())
        st = deepseek_svc.obter_status_deepseek()
        self.assertFalse(st["configurado"])
        self.assertEqual(st["mascara"], "")

    def test_salvar_e_obter_chave(self):
        """Valida gravação e leitura mascarada segura de chave."""
        fake_key = "sk-1234567890abcdef"
        ok = deepseek_svc.salvar_api_key_deepseek(fake_key)
        self.assertTrue(ok)

        key = deepseek_svc.obter_api_key_deepseek()
        self.assertEqual(key, fake_key)

        st = deepseek_svc.obter_status_deepseek()
        self.assertTrue(st["configurado"])
        self.assertEqual(st["mascara"], "...cdef")
        self.assertNotIn("123456", st["mascara"])

    def test_analise_global_context_pack(self):
        """Valida a construção do Context Pack Global com mock da DeepSeek API."""
        fake_context_pack = {
            "theme": "Avanço da IA na criação de vídeo cinematográfico",
            "main_subject": "Inteligência Artificial e Direção de Arte",
            "characters": [
                {"alias": "@marcos", "role": "main_presenter", "visual_identity": "Homem, 35 anos, camisa azul"}
            ],
            "world": "Estúdio contemporâneo com iluminação cinematográfica",
            "environment": "Ambiente moderno e minimalista",
            "visual_progression": "De computadores e códigos para telas imersivas",
            "recurring_elements": ["Lentes anamórficas", "Telas holográficas"],
            "continuity_rules": ["Manter iluminação quente e paleta azul/dourada"],
            "style_lock": "Photorealistic cinematic still",
            "negative_lock": "no text, no watermark"
        }

        mock_resp = {
            "content": json.dumps(fake_context_pack),
            "usage": {"prompt_tokens": 500, "completion_tokens": 200, "total_tokens": 700, "custo_estimado_usd": 0.00012},
            "model": "deepseek-chat",
            "tempo_resposta_s": 1.2,
        }

        with patch.object(deepseek_svc, "_chamar_deepseek_api", return_value=mock_resp):
            res = deepseek_svc.analisar_contexto_global(
                cenas=[{"id": 1, "tempo_inicio": 0, "narration": "Olá"}],
                referencias=[],
                estilo_preset=presets_svc.obter_preset_por_id("photorealistic_cinematic"),
                api_key="fake_key"
            )

        cp = res["context_pack"]
        self.assertEqual(cp["theme"], "Avanço da IA na criação de vídeo cinematográfico")
        self.assertIn("@marcos", cp["characters"][0]["alias"])
        self.assertEqual(res["usage"]["total_tokens"], 700)

    def test_geracao_em_lote_e_critic(self):
        """Valida fluxo de geração em lote, passagem pelo critic e persistência atômica."""
        # Cria plano de cenas no projeto temporário
        cenas = [
            {
                "id": 1,
                "scene_index": 1,
                "tempo_inicio": 0.0,
                "tempo_fim": 4.0,
                "timestamp": "00:00 - 00:04",
                "narration": "No início do século XXI, a inteligência artificial transformou a produção visual.",
                "texto": "No início do século XXI, a inteligência artificial transformou a produção visual.",
            },
            {
                "id": 2,
                "scene_index": 2,
                "tempo_inicio": 4.0,
                "tempo_fim": 8.0,
                "timestamp": "00:04 - 00:08",
                "narration": "Marcos observava os detalhes minuciosos de cada frame gerado.",
                "texto": "Marcos observava os detalhes minuciosos de cada frame gerado.",
            },
            {
                "id": 3,
                "scene_index": 3,
                "tempo_inicio": 8.0,
                "tempo_fim": 12.0,
                "timestamp": "00:08 - 00:12",
                "narration": "O resultado final conectava realismo e direção de arte impecável.",
                "texto": "O resultado final conectava realismo e direção de arte impecável.",
            },
        ]
        scene_plan_svc.salvar_scene_plan(self.temp_project, {"cenas": cenas})

        fake_context = {
            "theme": "Tecnologia",
            "style_lock": "Photorealistic cinematic still, 16:9",
            "negative_lock": "no text",
        }

        cenas_res = [
            {
                "scene_index": 1,
                "timestamp": "00:00 - 00:04",
                "visual_role": "hook",
                "scene_type": "broll_macro",
                "prompt_imagem": "Photorealistic cinematic still, wide shot of glowing microchips, 8k render",
                "prompt_animacao": "Slow push-in 4s",
                "references": [],
                "continuity_notes": "Warm lighting"
            },
            {
                "scene_index": 2,
                "timestamp": "00:04 - 00:08",
                "visual_role": "explanation",
                "scene_type": "avatar_talking",
                "prompt_imagem": "Photorealistic cinematic still of @marcos smiling in studio, shallow depth of field",
                "prompt_animacao": "Subtle camera pan right",
                "references": ["@marcos"],
                "continuity_notes": "Preserves studio background"
            },
            {
                "scene_index": 3,
                "timestamp": "00:08 - 00:12",
                "visual_role": "climax",
                "scene_type": "broll_environment",
                "prompt_imagem": "Photorealistic cinematic still, epic modern architecture illuminated at twilight, 16:9",
                "prompt_animacao": "Aerial slow pull-back",
                "references": [],
                "continuity_notes": "Twilight lighting"
            }
        ]

        def mock_api_call(messages, *args, **kwargs):
            content = json.dumps({"scenes": cenas_res} if "BATCH" in str(messages) or "scenes" in str(messages) else fake_context)
            return {
                "content": content,
                "usage": {"prompt_tokens": 300, "completion_tokens": 150, "total_tokens": 450, "custo_estimado_usd": 0.00008},
                "model": "deepseek-chat",
                "tempo_resposta_s": 0.8,
            }

        with patch.object(deepseek_svc, "_chamar_deepseek_api", side_effect=mock_api_call):
            resultado = deepseek_svc.executar_pipeline_prompt_intelligence(
                projeto_id=self.temp_project,
                estilo_id="photorealistic_cinematic",
                api_key="fake_key"
            )

        self.assertTrue(resultado["success"])
        self.assertEqual(resultado["total_cenas"], 3)
        self.assertEqual(resultado["cenas_reprovadas_critic"], 0)

        # Valida persistência atômica no lira_scene_plan.json
        plan_atualizado = scene_plan_svc.carregar_scene_plan(self.temp_project)
        cenas_plan = plan_atualizado["cenas"]
        self.assertEqual(len(cenas_plan), 3)
        self.assertEqual(cenas_plan[0]["status"], scene_plan_svc.STATUS_PROMPT_PRONTO)
        self.assertIn("@marcos", cenas_plan[1]["prompt_imagem"])
        self.assertEqual(cenas_plan[1]["character_ref"], "@marcos")

        # Valida arquivos em prompts/
        self.assertTrue((self.pdir / "prompts" / "storyboard_prompts.txt").exists())
        self.assertTrue((self.pdir / "prompts" / "prompts.txt").exists())

    def test_api_v2_endpoints_deepseek_e_presets(self):
        """Valida os endpoints da API v2 para Presets e Configuração DeepSeek."""
        # 1. Presets
        res = self.client.get("/api/v2/presets/estilos")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["presets"]), 11)

        # 2. Status Inicial
        res_st = self.client.get("/api/v2/deepseek/status")
        self.assertEqual(res_st.status_code, 200)
        self.assertFalse(res_st.get_json()["configurado"])

        # 3. Configurar Chave
        res_cfg = self.client.post("/api/v2/deepseek/config", json={"api_key": "sk-secret-test-key-1234"})
        self.assertEqual(res_cfg.status_code, 200)
        data_cfg = res_cfg.get_json()
        self.assertTrue(data_cfg["configurado"])
        self.assertEqual(data_cfg["mascara"], "...1234")

        # 4. Status Atualizado
        res_st2 = self.client.get("/api/v2/deepseek/status")
        self.assertTrue(res_st2.get_json()["configurado"])


if __name__ == "__main__":
    unittest.main()
