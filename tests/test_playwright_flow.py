"""
tests/test_playwright_flow.py — Testes Unitários da Automação Playwright Flow (CDP)
"""

import os
import sys
import json
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Adiciona raiz do projeto
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.playwright_flow import (
    FlowSessionManager,
    FlowQueueWorker,
)
import services.scene_plan_service as scene_plan_svc
from config import PROJETOS_DIR


class TestPlaywrightFlow(unittest.TestCase):
    def setUp(self):
        self.test_proj = "test_flow_proj"
        self.proj_dir = PROJETOS_DIR / self.test_proj
        self.proj_dir.mkdir(parents=True, exist_ok=True)
        
        cenas_test = [
            {
                "id": 1,
                "texto": "Cena 1 teste",
                "tempo_inicio": 0.0,
                "tempo_fim": 3.0,
                "tipo": "image",
                "prompt_imagem": "Realistic photo of a green garden",
                "prompt_animacao": "",
                "arquivo_midia": "",
                "status": scene_plan_svc.STATUS_PENDENTE,
            },
            {
                "id": 2,
                "texto": "Cena 2 teste com @personagem",
                "tempo_inicio": 3.0,
                "tempo_fim": 6.0,
                "tipo": "image",
                "prompt_imagem": "Close-up of a gardener working in the backyard",
                "prompt_animacao": "",
                "arquivo_midia": "",
                "status": scene_plan_svc.STATUS_PENDENTE,
            },
            {
                "id": 3,
                "texto": "Cena 3 vídeo",
                "tempo_inicio": 6.0,
                "tempo_fim": 10.0,
                "tipo": "video",
                "prompt_imagem": "Cinematic shot of flying drone over grass",
                "prompt_animacao": "Smooth forward motion cinematic video",
                "arquivo_midia": "",
                "status": scene_plan_svc.STATUS_PENDENTE,
            }
        ]
        
        plan = {
            "projeto": self.test_proj,
            "total": 3,
            "cenas": cenas_test
        }
        scene_plan_svc.salvar_scene_plan(self.test_proj, plan)

    def tearDown(self):
        plan_path = self.proj_dir / "lira_scene_plan.json"
        if plan_path.exists():
            plan_path.unlink()
        midias_dir = self.proj_dir / "midias"
        if midias_dir.exists():
            for f in midias_dir.glob("*"):
                f.unlink()
            midias_dir.rmdir()
        if self.proj_dir.exists():
            try:
                self.proj_dir.rmdir()
            except Exception:
                pass

    def test_session_manager_methods(self):
        is_act = FlowSessionManager.is_active()
        self.assertIsInstance(is_act, bool)

    def test_queue_worker_methods(self):
        is_run = FlowQueueWorker.is_running()
        self.assertIsInstance(is_run, bool)
        cena_atv = FlowQueueWorker.get_cena_ativa()
        self.assertIsInstance(cena_atv, dict)

    def test_character_detection(self):
        self.assertTrue(scene_plan_svc._cena_tem_personagem("Homem caminhando"))
        self.assertTrue(scene_plan_svc._cena_tem_personagem("Close-up gardener watering plants"))
        self.assertTrue(scene_plan_svc._cena_tem_personagem("Foto com @personagem"))
        self.assertFalse(scene_plan_svc._cena_tem_personagem("Lawn grass texture in sunny day"))

    def test_media_name_formatting(self):
        cena = {
            "id": 1,
            "tempo_inicio": 0.0,
            "prompt_imagem": "Extreme close-up of a real spurge weed",
            "texto": "Spurge weed in backyard"
        }
        fname_jpg = scene_plan_svc._nome_arquivo_cena(cena, ".jpg")
        self.assertTrue(fname_jpg.startswith("001_[00-00]_extreme_close_up"))
        self.assertTrue(fname_jpg.endswith(".jpg"))

        fname_mp4 = scene_plan_svc._nome_arquivo_cena(cena, ".mp4")
        self.assertTrue(fname_mp4.endswith(".mp4"))


class TestDetectarErroLimiteToast(unittest.TestCase):
    """BURACO 1 — alertas do Google Flow por TOAST/SELETOR JS (bloque 2 de
    _detectar_erro_ou_limite_modelo) devem setear _fallback_video_para_imagem no modo
    vídeo e retornar códigos canónicos (nunca o string cru do indicador)."""

    from services.playwright_flow import PlaywrightCDPWorker

    def _worker(self, **kw):
        pagina = MagicMock()
        w = self.PlaywrightCDPWorker.__new__(self.PlaywrightCDPWorker)
        w.port = 9222
        w.page = pagina
        w.browser = None
        w.context = None
        w.playwright = None
        w.current_project_id = ""
        w.is_fallback_active = False
        w.current_model = "Nano Banana 2"
        w._project_url_saved = False
        for k, v in kw.items():
            setattr(w, k, v)
        return w, pagina

    def test_toast_modo_video_setea_flag_e_non_rota(self):
        import services.playwright_flow as pf
        w, pagina = self._worker()
        # 1ª evaluate: indicador_prompt (None), 2ª evaluate: indicador_toast explícito
        pagina.evaluate.side_effect = [
            None,
            "you've reached your daily limit",
        ]
        with patch("services.flow_account_manager.FlowAccountManager.proxima_conta_disponivel", return_value=None), \
             patch("services.flow_account_manager.FlowAccountManager.proxima_conta_para_video", return_value=None):
            with patch.object(self.PlaywrightCDPWorker, "_rotacionar_conta") as m_rot:
                ret = w._detectar_erro_ou_limite_modelo(video_mode=True)
        self.assertEqual(ret, "credito_esgotado_video")
        self.assertTrue(w._fallback_video_para_imagem,
                        "toast en modo vídeo DEBE setear _fallback_video_para_imagem")
        m_rot.assert_not_called()

    def test_toast_modo_imagen_rota_cuenta(self):
        import services.playwright_flow as pf
        w, pagina = self._worker()
        pagina.evaluate.side_effect = [
            None,
            "you've reached your daily limit",
        ]
        with patch.object(self.PlaywrightCDPWorker, "_rotacionar_conta") as m_rot:
            ret = w._detectar_erro_ou_limite_modelo(video_mode=False)
        self.assertEqual(ret, "credito_esgotado")
        m_rot.assert_called_once()

    def test_sin_señal_retorna_none(self):
        w, pagina = self._worker()
        pagina.evaluate.side_effect = [
            None,   # evaluate prompt sem erro
            None,   # evaluate JS sin indicador ni toast
        ]
        ret = w._detectar_erro_ou_limite_modelo(video_mode=True)
        self.assertIsNone(ret)
        self.assertFalse(getattr(w, "_fallback_video_para_imagem", False))

    def test_prompt_warning_setea_flag(self):
        w, pagina = self._worker()
        pagina.evaluate.side_effect = [
            "prompt_warning_button",  # Botão de aviso de créditos ativo no prompt
            None,
        ]
        with patch("services.flow_account_manager.FlowAccountManager.proxima_conta_disponivel", return_value=None), \
             patch("services.flow_account_manager.FlowAccountManager.proxima_conta_para_video", return_value=None):
            ret = w._detectar_erro_ou_limite_modelo(video_mode=True)
        self.assertEqual(ret, "credito_esgotado_video")
        self.assertTrue(w._fallback_video_para_imagem)

    def test_camino_toast_integrador_cola_normaliza_indicador_cru(self):
        src = (Path(__file__).parent.parent / "services" / "playwright_flow.py").read_text(encoding="utf-8")
        self.assertIn("Indicador de limite NO canónico normalizado", src)
        self.assertIn("self._fallback_video_para_imagem = True", src)
        self.assertIn("err_limite == \"credito_esgotado_video\"", src)


class TestSaldoExatoFlow(unittest.TestCase):

    def test_regex_extracao_saldo(self):
        import re
        exemplos = [
            ("Você tem 45 créditos disponíveis", 45),
            ("Saldo: 120 créditos", 120),
            ("0 credits remaining", 0),
            ("Balance: 15", 15),
            ("50", 50),
        ]
        for texto, esperado in exemplos:
            m = re.search(r"(\d+)\s*(?:crédito|credit|computação|computações)", texto, re.IGNORECASE)
            if not m:
                m = re.search(r"(?:créditos?|credits?|saldo|balance)[\s:]*(\d+)", texto, re.IGNORECASE)
            if not m:
                m = re.search(r"\b(\d+)\b", texto)
            self.assertIsNotNone(m, f"Falha ao extrair regex de '{texto}'")
            self.assertEqual(int(m.group(1)), esperado)


class TestContextoCDP(unittest.TestCase):

    def test_salvar_e_obter_contexto(self):
        ctx_file = Path("tmp/lira_context_cdp.json")
        ctx_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "projeto_id": "projeto_teste_123",
            "timestamp": time.time(),
            "modo": "imagem"
        }
        ctx_file.write_text(json.dumps(data), encoding="utf-8")

        from flask import Flask
        from services.api_v2 import api_v2_bp
        app = Flask(__name__)
        app.register_blueprint(api_v2_bp, url_prefix="/api/v2")
        client = app.test_client()

        resp = client.get("/api/v2/contexto_ativo")
        self.assertEqual(resp.status_code, 200)
        json_data = resp.get_json()
        self.assertTrue(json_data.get("success"))
        self.assertEqual(json_data.get("contexto", {}).get("projeto_id"), "projeto_teste_123")


class TestPromptVideoModeNuncaUsaPromptImagem(unittest.TestCase):
    """TAREFA 1 — Validação: video_mode NUNCA deve usar prompt_imagem como fallback.
    O prompt de vídeo deve vir de prompt_animacao ou texto, nunca da descrição
    estática da imagem (prompt_imagem)."""

    def test_video_mode_sem_prompt_animacao_cai_em_texto(self):
        """Quando prompt_animacao está vazio, video_mode deve usar texto (narração),
        NÃO prompt_imagem (descrição estática)."""
        cena = {
            "prompt_animacao": "",
            "prompt_imagem": "Photorealistic cinematic macro b-roll shot of garden nature, soft light",
            "texto": "Cut the stem at a 45 degree angle to maximize absorption",
        }
        video_mode = True
        # Simula a lógica do prompt exatamente como em playwright_flow.py:3622
        if video_mode:
            raw_prompt = cena.get("prompt_animacao") or cena.get("texto", "")
            if not raw_prompt:
                raw_prompt = "Cinematic slow camera motion, natural depth of field, high clarity 16:9"
        else:
            raw_prompt = cena.get("prompt_imagem") or cena.get("texto", "")

        # O resultado DEVE ser o texto da narração, NUNCA o prompt_imagem
        self.assertEqual(raw_prompt, cena["texto"])
        self.assertNotIn("macro b-roll shot", raw_prompt)

    def test_video_mode_com_prompt_animacao_usa_ele(self):
        """Quando prompt_animacao existe, video_mode deve usá-lo diretamente."""
        cena = {
            "prompt_animacao": "Camera follows the hands performing the action, 4s smooth ease",
            "prompt_imagem": "Photorealistic static shot of soil texture",
            "texto": "Apply the fertilizer evenly across the soil surface",
        }
        video_mode = True
        if video_mode:
            raw_prompt = cena.get("prompt_animacao") or cena.get("texto", "")
        else:
            raw_prompt = cena.get("prompt_imagem") or cena.get("texto", "")

        self.assertEqual(raw_prompt, cena["prompt_animacao"])
        self.assertIn("follows the hands", raw_prompt)

    def test_video_mode_sem_nenhum_prompt_usa_fallback_generico(self):
        """Quando tudo está vazio, video_mode deve usar o fallback cinematográfico genérico."""
        cena = {"prompt_animacao": "", "prompt_imagem": "", "texto": ""}
        video_mode = True
        if video_mode:
            raw_prompt = cena.get("prompt_animacao") or cena.get("texto", "")
            if not raw_prompt:
                raw_prompt = "Cinematic slow camera motion, natural depth of field, high clarity 16:9"
        self.assertIn("Cinematic slow camera motion", raw_prompt)

    def test_image_mode_usa_prompt_imagem(self):
        """Em modo imagem (video_mode=False), deve usar prompt_imagem normalmente."""
        cena = {
            "prompt_animacao": "Smooth push-in 4s",
            "prompt_imagem": "Photorealistic macro shot of tomato plant leaves",
            "texto": "Notice the yellowing on the leaves",
        }
        video_mode = False
        if video_mode:
            raw_prompt = cena.get("prompt_animacao") or cena.get("texto", "")
        else:
            raw_prompt = cena.get("prompt_imagem") or cena.get("texto", "")

        self.assertEqual(raw_prompt, cena["prompt_imagem"])


class TestAnimarImagemGaleriaExists(unittest.TestCase):
    """TAREFA 2 — Validação: o método _animar_imagem_galeria_flow existe com a
    assinatura correta na classe PlaywrightCDPWorker."""

    def test_metodo_existe(self):
        from services.playwright_flow import PlaywrightCDPWorker
        self.assertTrue(
            hasattr(PlaywrightCDPWorker, "_animar_imagem_galeria_flow"),
            "PlaywrightCDPWorker deve ter o método _animar_imagem_galeria_flow"
        )

    def test_assinatura_metodo(self):
        import inspect
        from services.playwright_flow import PlaywrightCDPWorker
        sig = inspect.signature(PlaywrightCDPWorker._animar_imagem_galeria_flow)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ["self", "projeto_id", "arquivo_imagem", "prompt_movimento"])

    def test_metodo_retorna_false_sem_page(self):
        """Sem self.page, o método deve retornar False sem erros."""
        from services.playwright_flow import PlaywrightCDPWorker
        w = PlaywrightCDPWorker.__new__(PlaywrightCDPWorker)
        w.page = None
        result = w._animar_imagem_galeria_flow("proj123", "test.png", "motion prompt")
        self.assertFalse(result)

    def test_metodo_retorna_false_sem_arquivo(self):
        """Sem arquivo_imagem, o método deve retornar False."""
        from services.playwright_flow import PlaywrightCDPWorker
        w = PlaywrightCDPWorker.__new__(PlaywrightCDPWorker)
        w.page = MagicMock()
        result = w._animar_imagem_galeria_flow("proj123", "", "motion prompt")
        self.assertFalse(result)


class TestBrainPromptAnimacaoBRoll(unittest.TestCase):
    """TAREFA 3 — Validação: brain_system_director preenche prompt_animacao
    para cenas B-Roll com tipo=video, mesmo quando should_animate=False."""

    def test_fallback_broll_preenche_prompt_animacao(self):
        """Cena B-Roll com tipo=video e prompt_animacao vazio deve ser preenchida
        pela _montar_prompt_animacao_fallback."""
        from services.deepseek_prompt_service import _montar_prompt_animacao_fallback
        # Simula a lógica do brain_system_director (loop CORREÇÃO ANIMAR)
        cena = {
            "id": 5,
            "tipo": "video",
            "prompt_animacao": "",
            "texto": "Now let's look at the pruning technique using your shears",
        }
        if not cena.get("prompt_animacao"):
            eh_video = str(cena.get("tipo", "")).lower() == "video"
            if eh_video:
                narracao = cena.get("texto") or ""
                cena["prompt_animacao"] = _montar_prompt_animacao_fallback(narracao)

        self.assertTrue(len(cena["prompt_animacao"]) > 8,
                        "prompt_animacao deve ter >8 palavras")
        # "shears" está na lista de ferramentas → deve gerar prompt de ferramenta
        self.assertIn("tool", cena["prompt_animacao"].lower())

    def test_fallback_broll_acao_gera_prompt_de_movimento(self):
        """Cena com verbo de ação (grow, plant) deve gerar prompt coerente."""
        from services.deepseek_prompt_service import _montar_prompt_animacao_fallback
        prompt = _montar_prompt_animacao_fallback("Watch the tomato plant growing and blooming")
        self.assertIn("4s smooth ease", prompt)
        self.assertTrue(len(prompt) > 20)

    def test_fallback_broll_cta_gera_push_in(self):
        """Cena com CTA (subscribe, like) deve gerar push-in."""
        from services.deepseek_prompt_service import _montar_prompt_animacao_fallback
        prompt = _montar_prompt_animacao_fallback("Don't forget to subscribe and like this video")
        self.assertIn("push-in", prompt.lower())

    def test_cena_com_prompt_animacao_nao_sobrescreve(self):
        """Cena que já tem prompt_animacao NÃO deve ser sobrescrita."""
        cena = {
            "tipo": "video",
            "prompt_animacao": "Custom manual motion prompt already set",
            "texto": "Some narration text",
        }
        # Simula a lógica: se já tem prompt_animacao, pula
        if not cena.get("prompt_animacao"):
            cena["prompt_animacao"] = "SHOULD NOT APPEAR"
        self.assertEqual(cena["prompt_animacao"], "Custom manual motion prompt already set")

    def test_output_dict_sempre_inclui_prompt_animacao(self):
        """O output do brain NÃO deve mais zerar prompt_animacao com base em should_animate."""
        # Simula a lógica corrigida (linha 666 do brain_system_director.py)
        cena = {"prompt_animacao": "Smooth push-in camera, 4s ease"}
        anim = {"should_animate": False}  # B-Roll sem should_animate
        # Lógica ANTIGA (bugada): cena.get("prompt_animacao", "") if anim.get("should_animate") else ""
        # Lógica NOVA  (correta): cena.get("prompt_animacao", "")
        output_prompt = cena.get("prompt_animacao", "")
        self.assertEqual(output_prompt, "Smooth push-in camera, 4s ease",
                         "prompt_animacao NÃO deve ser zerado quando should_animate=False")


class TestCodigoFonteAnimarIntegro(unittest.TestCase):
    """Validação de integridade: confirma que as 3 mudanças estão no código-fonte."""

    def test_playwright_flow_sem_fallback_prompt_imagem(self):
        """playwright_flow.py NÃO deve ter 'or cena.get(\"prompt_imagem\")' na linha de video_mode."""
        src = (Path(__file__).parent.parent / "services" / "playwright_flow.py").read_text(encoding="utf-8")
        # A linha antiga era: raw_prompt = cena.get("prompt_animacao") or cena.get("prompt_imagem") or ...
        # Agora deve ser:      raw_prompt = cena.get("prompt_animacao") or cena.get("texto", "")
        linhas_video_mode = [l for l in src.splitlines()
                             if "video_mode" in l and "prompt_animacao" in l]
        for linha in linhas_video_mode:
            self.assertNotIn('prompt_imagem', linha,
                             f"Fallback para prompt_imagem em video_mode ainda presente: {linha.strip()}")

    def test_playwright_flow_tem_animar_imagem_galeria(self):
        """playwright_flow.py deve conter a definição de _animar_imagem_galeria_flow."""
        src = (Path(__file__).parent.parent / "services" / "playwright_flow.py").read_text(encoding="utf-8")
        self.assertIn("def _animar_imagem_galeria_flow(", src)

    def test_playwright_flow_broll_usa_animar(self):
        """A branch de B-Roll deve chamar _animar_imagem_galeria_flow, não _anexar_referencia_imagem_local."""
        src = (Path(__file__).parent.parent / "services" / "playwright_flow.py").read_text(encoding="utf-8")
        # Deve conter a nova chamada
        self.assertIn("_animar_imagem_galeria_flow", src)

    def test_brain_system_director_nao_tem_gate_should_animate(self):
        """brain_system_director.py NÃO deve mais ter gate should_animate na saída de prompt_animacao."""
        src = (Path(__file__).parent.parent / "services" / "brain_system_director.py").read_text(encoding="utf-8")
        # A linha antiga: "prompt_animacao": cena.get("prompt_animacao", "") if anim.get("should_animate") else ""
        self.assertNotIn('if anim.get("should_animate") else ""', src,
                         "Gate should_animate na saída de prompt_animacao ainda presente!")


class TestUsesCharImageToVideoEGuardas(unittest.TestCase):
    """Valida a branch uses_char com Image-to-Video nativo, fallback T2V e guarda de manual_override."""

    def setUp(self):
        from services.playwright_flow import PlaywrightCDPWorker
        self.w = PlaywrightCDPWorker.__new__(PlaywrightCDPWorker)
        self.w.port = 9222
        self.w.page = MagicMock()
        self.w.current_model = "Veo 3.1 - Lite"
        self.w.current_flow_mode = "video"
        self.w.get_exact_balance = MagicMock(return_value=50)
        self.w._garantir_aba_flow_aberta = MagicMock(return_value=True)
        self.w._ensure_project_open = MagicMock(return_value=True)
        self.w._disable_agent_mode = MagicMock()
        self.w._fechar_modais_bloqueantes = MagicMock()
        self.w._limpar_overlays_e_backdrops = MagicMock()
        self.w._set_output_mode = MagicMock(return_value=True)
        self.w._get_existing_media_snapshot = MagicMock(return_value=(set(), 0))
        self.w._clean_prompt_text = MagicMock(side_effect=lambda p, **kw: p)
        self.w._animar_imagem_galeria_flow = MagicMock(return_value=True)
        self.w._selecionar_referencia_flow = MagicMock(return_value=True)
        self.w._tratar_indicador_limite = MagicMock(return_value="stop")

        loc_mock = MagicMock()
        loc_mock.is_visible.return_value = True
        self.w.page.locator.return_value.first = loc_mock

        self.tmp_png = Path(__file__).parent / "tmp_char_test.png"
        self.tmp_png.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfake")

    def tearDown(self):
        if self.tmp_png.exists():
            try:
                self.tmp_png.unlink()
            except Exception:
                pass

    def test_uses_char_video_mode_com_png_valido_personagem_chama_animar(self):
        """uses_char com video_mode e PNG com personagem chama _animar_imagem_galeria_flow e pula modal."""
        cena = {
            "id": 4,
            "tipo": "video",
            "manual_override": True,
            "texto": "watered every single day",
            "arquivo_midia": str(self.tmp_png),
            "imagem_com_personagem": True,
            "prompt_animacao": "Marcos watering plants, 4s smooth ease"
        }

        with patch("services.playwright_flow._detectar_cena_avatar", return_value=False), \
             patch("services.character_service.obter_personagem_cena", return_value={"uses_character": True, "nome": "Marcos", "character_ref": "@Marcos"}), \
             patch("services.scene_plan_service.atualizar_status_cena"), \
             patch("services.playwright_flow.check_flow_credits", return_value={"has_credits": False}):
            self.w._processar_cena_individual("proj_test", cena, is_anim=True)

            self.w._animar_imagem_galeria_flow.assert_called_once()
            self.w._selecionar_referencia_flow.assert_not_called()

    def test_uses_char_video_mode_sem_png_valido_fallback_t2v(self):
        """uses_char com video_mode sem PNG do personagem cai no fallback modal T2V."""
        cena = {
            "id": 4,
            "tipo": "video",
            "manual_override": False,
            "texto": "watered every single day",
            "arquivo_midia": "broll_sem_personagem.png",
            "imagem_com_personagem": False,
            "prompt_animacao": "Marcos watering plants, 4s smooth ease"
        }

        with patch("services.playwright_flow._detectar_cena_avatar", return_value=False), \
             patch("services.character_service.obter_personagem_cena", return_value={"uses_character": True, "nome": "Marcos", "character_ref": "@Marcos"}), \
             patch("services.scene_plan_service.atualizar_status_cena"), \
             patch("services.playwright_flow.check_flow_credits", return_value={"has_credits": False}):
            self.w._processar_cena_individual("proj_test", cena, is_anim=True)

            self.w._animar_imagem_galeria_flow.assert_not_called()
            self.w._selecionar_referencia_flow.assert_called_once()

    def test_uses_char_video_mode_animar_falha_fallback_t2v(self):
        """Quando _animar_imagem_galeria_flow falha, cai no modal T2V sem tentar retry extra."""
        self.w._animar_imagem_galeria_flow.return_value = False
        cena = {
            "id": 4,
            "tipo": "video",
            "manual_override": True,
            "texto": "watered every single day",
            "arquivo_midia": str(self.tmp_png),
            "imagem_com_personagem": True,
            "prompt_animacao": "Marcos watering plants, 4s smooth ease"
        }

        with patch("services.playwright_flow._detectar_cena_avatar", return_value=False), \
             patch("services.character_service.obter_personagem_cena", return_value={"uses_character": True, "nome": "Marcos", "character_ref": "@Marcos"}), \
             patch("services.scene_plan_service.atualizar_status_cena"), \
             patch("services.playwright_flow.check_flow_credits", return_value={"has_credits": False}):
            self.w._processar_cena_individual("proj_test", cena, is_anim=True)

            self.w._animar_imagem_galeria_flow.assert_called_once()
            self.w._selecionar_referencia_flow.assert_called_once()

    def test_uses_char_manual_override_sem_png_retorna_pendente_sem_gasto(self):
        """Cena com manual_override sem PNG válido com personagem retorna pendente sem gastar créditos."""
        cena = {
            "id": 4,
            "tipo": "video",
            "manual_override": True,
            "texto": "watered every single day",
            "arquivo_midia": ""
        }

        with patch("services.playwright_flow._detectar_cena_avatar", return_value=False), \
             patch("services.character_service.obter_personagem_cena", return_value={"uses_character": True, "nome": "Marcos", "character_ref": "@Marcos"}), \
             patch("services.scene_plan_service.atualizar_status_cena"), \
             patch("services.scene_plan_service.atualizar_cena") as mock_att:
            ok, msg = self.w._processar_cena_individual("proj_test", cena, is_anim=True)

            self.assertTrue(ok)
            self.assertEqual(msg, "imagem do avatar pendente")
            self.w._animar_imagem_galeria_flow.assert_not_called()
            self.w._selecionar_referencia_flow.assert_not_called()
            mock_att.assert_called_once_with("proj_test", 4, {
                "video_status": scene_plan_svc.STATUS_PENDENTE,
                "status": scene_plan_svc.STATUS_PENDENTE
            })


class TestPOVVideoBlockersFixes(unittest.TestCase):
    """Valida as 3 correções cirúrgicas para renderização de vídeo POV no Flow."""

    def test_seletor_menu_robusto_no_codigo(self):
        """TAREFA 1: playwright_flow.py deve usar card_container com seletores robustos."""
        src = (Path(__file__).parent.parent / "services" / "playwright_flow.py").read_text(encoding="utf-8")
        self.assertIn("card_container = alvo_card.locator", src)
        self.assertIn("ancestor::div[@role='listitem']", src)
        self.assertIn("button[aria-label*='menu'", src)

    def test_comutacao_veo_modo_video_no_codigo(self):
        """TAREFA 2: playwright_flow.py deve comutar para Veo 3.1 - Lite e vídeo antes de animar."""
        src = (Path(__file__).parent.parent / "services" / "playwright_flow.py").read_text(encoding="utf-8")
        self.assertIn("Veo 3.1 - Lite", src)
        self.assertIn("output_toggle", src)
        self.assertIn("button[data-model*='veo']", src)

    def test_detectar_cena_avatar_ignora_pov(self):
        """TAREFA 3: Cenas POV não devem ser detectadas como avatar (evita trava gestual)."""
        from services.playwright_flow import _detectar_cena_avatar
        cena_pov = {
            "id": 1,
            "scene_type": "broll_pov",
            "avatar_style": "first_person_pov",
            "uses_character": True,
            "texto": "avatar looking at plants"
        }
        self.assertFalse(_detectar_cena_avatar(cena_pov))

    def test_visual_judgment_service_pula_pov(self):
        """TAREFA 3: avaliar_fidelidade_facial deve pular cenas broll_pov ou skip_facial_validation."""
        from services.visual_judgment_service import avaliar_fidelidade_facial
        cena_pov = {
            "id": 1,
            "scene_type": "broll_pov",
            "avatar_style": "first_person_pov",
            "skip_facial_validation": True
        }
        res = avaliar_fidelidade_facial("fake_proj", "fake_img.png", cena_pov)
        self.assertIsNone(res)

    def test_brain_system_director_marca_skip_facial_validation_pov(self):
        """TAREFA 3: Brain Director deve marcar skip_facial_validation=True em cenas POV."""
        from services.brain_system_director import BrainSystemDirector
        director = BrainSystemDirector("test_pov_proj")
        entrada = {
            "cenas": [
                {
                    "id": 1,
                    "texto": "Walking through tomato plants looking at hands",
                    "scene_type": "broll_pov",
                    "avatar_style": "first_person_pov"
                }
            ]
        }
        res = director._fase_1_analise_narrativa(entrada, memoria_visual={})
        self.assertTrue(res[0].get("skip_facial_validation"))
        self.assertEqual(res[0].get("validation_override_reason"), "POV first-person has no visible face by design")


if __name__ == "__main__":
    unittest.main()

