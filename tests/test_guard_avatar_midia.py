# -*- coding: utf-8 -*-
"""
tests/test_guard_avatar_midia.py — Bug (ANTIGRAVITY): b-roll contaminado pelo reference.png
============================================================================================
Duas barreiras implementadas em `services/playwright_flow.py`:

1. GUARD DE CONTEÚDO (`_imagem_e_avatar_de_referencia`) — cenas com
   uses_character=False NUNCA aceitam uma imagem praticamente idêntica ao
   reference.png, nem no polling (`_processar_cena_individual`) nem antes do
   Image-to-Video (`_animar_imagem_galeria_flow`). Critério: diferença média
   absoluta RGB < LIMIAR_DIFERENCA_MEDIA_RGB_AVATAR (15.0) E
   Hamming(dHash 64) <= LIMIAR_HAMMING_DHASH_AVATAR (4).
2. CHAVES DO UPLOAD CONHECIDAS (TAREFA 3) — o tile do avatar enviado ao Flow é
   registrado em `_chaves_upload_conhecidas`; `_media_item_e_novo` deixa de
   tratá-lo como "mídia nova" (grade virtualizada entra no DOM depois do snapshot).

Validação por SIMULAÇÃO (PIL + worker/page stub): nada aqui foi validado em
execução real contra o Flow.
"""
import base64
import io
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

import numpy as np
from PIL import Image

import services.character_service as chars  # noqa: F401
import services.playwright_flow as pf


# ---------------------------------------------------------------- helpers
class _Relogio:
    """Relógio controlável (substitui pf.time.time) para o polling não esperar."""

    def __init__(self, t=1000.0):
        self.t = t

    def time(self):
        return self.t

    def avancar(self, s):
        self.t += s


def _imagem_bytes(seed=1, size=(320, 240)) -> bytes:
    """PNG sintético determinístico, distinto por seed."""
    x = np.linspace(0, 255, size[0], dtype=np.uint16)[None, :]
    y = np.linspace(0, 255, size[1], dtype=np.uint16)[:, None]
    arr = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    arr[:, :, 0] = ((x + seed * 7) % 255).astype(np.uint8)
    arr[:, :, 1] = ((y + seed * 13) % 255).astype(np.uint8)
    arr[:, :, 2] = (((x // 2) + (y // 3) + seed * 29) % 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, format="PNG")
    return buf.getvalue()


def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _copia_com_ruido(png: bytes, amplitude: int = 3) -> bytes:
    """Cópia praticamente idêntica (jitter RGB pequeno — como as cenas 002/004)."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    arr = np.asarray(img, dtype=np.int16)
    rng = np.random.RandomState(7)
    arr = np.clip(arr + rng.randint(-amplitude, amplitude + 1, arr.shape), 0, 255)
    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8), "RGB").save(buf, format="PNG")
    return buf.getvalue()


def _worker_guard():
    """Worker stub mínimo para os helpers do guard."""
    w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
    w.port = 9222
    w.page = MagicMock()
    w.worker_id = 0
    w.current_project_id = "proj_guard"
    w.stop_requested = threading.Event()
    return w


# ---------------------------------------------------------------- guard de conteúdo
class TestImagemEAvatarDeReferencia(unittest.TestCase):
    """TAREFA 2 — comparação RGB + dHash contra o reference.png (limiares nomeados)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="guard_avatar_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "proj_guard").mkdir(parents=True, exist_ok=True)
        self.ref_png = _imagem_bytes(seed=1)
        self.ref_path = self.tmp / "proj_guard" / "reference.png"
        self.ref_path.write_bytes(self.ref_png)
        self.w = _worker_guard()
        self._p = mock.patch.object(pf, "PROJETOS_DIR", self.tmp)
        self._p.start()
        self.addCleanup(self._p.stop)

    def test_01_limiares_nomeados_no_topo(self):
        self.assertEqual(pf.LIMIAR_DIFERENCA_MEDIA_RGB_AVATAR, 15.0)
        self.assertEqual(pf.LIMIAR_HAMMING_DHASH_AVATAR, 4)
        self.assertEqual(pf.LIMIAR_GERACAO_RAPIDA_SEG, 5.0)

    def test_02_mesma_imagem_e_avatar(self):
        self.assertTrue(self.w._imagem_e_avatar_de_referencia("proj_guard", self.ref_png))
        self.assertTrue(self.w._imagem_e_avatar_de_referencia("proj_guard", str(self.ref_path)))

    def test_03_copia_com_ruido_pequeno_e_avatar(self):
        # Caso real das cenas 002/004 (dHash 0; diferença RGB ~1.7/0.8) → rejeitado.
        self.assertTrue(self.w._imagem_e_avatar_de_referencia("proj_guard",
                                                              _copia_com_ruido(self.ref_png)))

    def test_04_imagem_distinta_nao_e_avatar(self):
        self.assertFalse(self.w._imagem_e_avatar_de_referencia("proj_guard", _imagem_bytes(seed=9)))

    def test_05_referencia_ausente_desativa_guard_com_log(self):
        w = _worker_guard()
        with mock.patch.object(pf, "REFERENCE_PNG_IDENTIDADES_FALLBACK",
                               str(self.tmp / "nao_existe.png")), \
             mock.patch.object(chars, "resolver_imagem_avatar_projeto", return_value=None), \
             mock.patch.object(pf, "pw_log") as m_log:
            self.assertFalse(w._imagem_e_avatar_de_referencia("proj_sem_ref", _imagem_bytes(seed=2)))
            msgs = " ".join(str(c.args[0]) for c in m_log.call_args_list if c.args)
        self.assertIn("[GUARD_AVATAR] desativado: referência ausente", msgs)
        # Cacheado: segunda chamada NÃO relê nem loga de novo (uma vez por worker).
        with mock.patch.object(pf, "pw_log") as m_log2:
            self.assertFalse(w._imagem_e_avatar_de_referencia("proj_sem_ref", _imagem_bytes(seed=2)))
            self.assertEqual(m_log2.call_count, 0)

    def test_06_dhash64_tem_64_bits_e_e_estavel(self):
        img = Image.open(io.BytesIO(self.ref_png)).convert("RGB")
        h1 = self.w._dhash64(img)
        h2 = self.w._dhash64(img)
        self.assertEqual(h1, h2)
        self.assertLessEqual(h1.bit_length(), 64)
        self.assertEqual(bin(h1 ^ h1).count("1"), 0)
        # ruído pequeno não muda o hash (distância 0, como nas cenas contaminadas)
        img_ruido = Image.open(io.BytesIO(_copia_com_ruido(self.ref_png))).convert("RGB")
        self.assertLessEqual(bin(h1 ^ self.w._dhash64(img_ruido)).count("1"), 4)


class TestResolvedorReferencePng(unittest.TestCase):
    """Origem do resolvedor: projetos/<id>/reference.png → identidades → character_service."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="guard_resolver_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_01_projeto_tem_prioridade(self):
        (self.tmp / "proj").mkdir(parents=True, exist_ok=True)
        ref_proj = self.tmp / "proj" / "reference.png"
        ref_proj.write_bytes(_imagem_bytes(seed=1))
        fallback = self.tmp / "identidades_ref.png"
        fallback.write_bytes(_imagem_bytes(seed=2))
        w = _worker_guard()
        with mock.patch.object(pf, "PROJETOS_DIR", self.tmp), \
             mock.patch.object(pf, "REFERENCE_PNG_IDENTIDADES_FALLBACK", str(fallback)), \
             mock.patch.object(chars, "resolver_imagem_avatar_projeto", return_value=None):
            self.assertEqual(w._resolver_reference_png("proj"), str(ref_proj.resolve()))

    def test_02_fallback_identidades_quando_projeto_sem_reference(self):
        fallback = self.tmp / "identidades_ref.png"
        fallback.write_bytes(_imagem_bytes(seed=2))
        w = _worker_guard()
        with mock.patch.object(pf, "PROJETOS_DIR", self.tmp), \
             mock.patch.object(pf, "REFERENCE_PNG_IDENTIDADES_FALLBACK", str(fallback)), \
             mock.patch.object(chars, "resolver_imagem_avatar_projeto", return_value=None):
            self.assertEqual(w._resolver_reference_png("proj_vazio"), str(fallback.resolve()))

    def test_03_character_service_como_ultimo_recurso(self):
        chars_ref = self.tmp / "chars_ref.png"
        chars_ref.write_bytes(_imagem_bytes(seed=3))
        w = _worker_guard()
        with mock.patch.object(pf, "PROJETOS_DIR", self.tmp), \
             mock.patch.object(pf, "REFERENCE_PNG_IDENTIDADES_FALLBACK",
                               str(self.tmp / "nao_existe.png")), \
             mock.patch.object(chars, "resolver_imagem_avatar_projeto", return_value=str(chars_ref)):
            self.assertEqual(w._resolver_reference_png("proj_x"), str(chars_ref))

    def test_04_sem_nenhuma_referencia_retorna_none(self):
        w = _worker_guard()
        with mock.patch.object(pf, "PROJETOS_DIR", self.tmp), \
             mock.patch.object(pf, "REFERENCE_PNG_IDENTIDADES_FALLBACK",
                               str(self.tmp / "nao_existe.png")), \
             mock.patch.object(chars, "resolver_imagem_avatar_projeto", return_value=None):
            self.assertIsNone(w._resolver_reference_png("proj_x"))


# ---------------------------------------------------------------- polling (a)(b)(c)
class TestPollingGuardAvatar(unittest.TestCase):
    """TAREFA 2 — o guard roda DENTRO do polling de _processar_cena_individual."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="guard_polling_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "proj_guard").mkdir(parents=True, exist_ok=True)
        self.ref_png = _imagem_bytes(seed=1)
        (self.tmp / "proj_guard" / "reference.png").write_bytes(self.ref_png)
        self._worker_instance_antes = getattr(pf, "_worker_instance", None)
        self.addCleanup(self._restaurar_worker_instance)

    def _restaurar_worker_instance(self):
        try:
            pf._worker_instance = self._worker_instance_antes
        except Exception:
            pass

    def _executar(self, *, usa_char: bool, bytes_resultado: bytes, avancar_s: float,
                  media_key: str = "k_cena002"):
        """Dirige o polling real com page stub; devolve (ok, msg, logs, mock_salvar, worker)."""
        relogio = _Relogio(1000.0)
        # ISOLAMENTO: _processar_cena_individual grava pf._worker_instance (global);
        # guardamos o valor para restaurar no tearDown (evita poluir outros testes).
        self._worker_instance_antes = getattr(pf, "_worker_instance", None)
        item = {
            "id": f"media_{media_key}_320",
            "mediaKey": media_key,
            "name": media_key,
            "src": f"https://flow.example/media?name={media_key}",
            "type": "image",
            "width": 320,
            "height": 240,
            "domIndex": 0,
            "dataUrl": _data_url(bytes_resultado),
        }

        def _router(js, *args):
            if js is pf.JS_FETCH_MEDIA_LIST:
                relogio.avancar(avancar_s)
                return {"ok": True, "media": [item], "count": 1}
            if js is pf.JS_EXTRACT_CANVAS_DATA_URL:
                return {"ok": True, "dataUrl": item["dataUrl"]}
            # Qualquer outro JS (detecção de erro/card/progresso) → resultado VAZIO
            # (falsy) para não abortar a cena por engano no stub.
            return {}

        w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
        w.port = 9222
        w.worker_id = 0
        w.is_running_queue = False
        w.page = MagicMock()
        w.page.evaluate.side_effect = _router
        _loc = MagicMock()
        _loc.is_visible.return_value = True
        w.page.locator.return_value.first = _loc
        w.stop_requested = threading.Event()
        w.current_model = "Nano Banana 2"
        w.current_flow_mode = "imagem"
        w.current_download_quality = "1K"
        w.current_download_quality_video = "1080p"
        w.current_project_id = "proj_guard"
        w.get_exact_balance = MagicMock(return_value=None)
        w._garantir_aba_flow_aberta = MagicMock(return_value=True)
        w._ensure_project_open = MagicMock(return_value=True)
        w._disable_agent_mode = MagicMock()
        w._fechar_modais_bloqueantes = MagicMock()
        w._limpar_overlays_e_backdrops = MagicMock()
        w._set_output_mode = MagicMock(return_value=True)
        w._get_existing_media_snapshot = MagicMock(return_value=(set(), 0))
        w._clean_prompt_text = MagicMock(side_effect=lambda p, **kw: p)
        w._selecionar_referencia_flow = MagicMock(return_value=True)
        w._animar_imagem_galeria_flow = MagicMock(return_value=True)
        w._tratar_indicador_limite = MagicMock(return_value=None)
        w._detectar_erro_ou_limite_modelo = MagicMock(return_value=None)
        w._checar_recusa_politica = MagicMock(return_value=None)
        w._etapa_timing = MagicMock()
        w._marcar_cards_falha_existentes = MagicMock()
        w._upscale_tentado_cena = False
        w._fallback_video_para_imagem = False
        # atributos criados no __init__ real (o stub usa __new__)
        w.cena_ativa = None
        w.queue_start_time = None
        w.scene_durations = []
        w.current_delay_info = None
        w.creditos_atuais = None
        w._personagem_card_persistido = False
        w._configured_mode = None
        w.account_email = None
        w.current_project_name = None
        w._project_url_saved = False
        w._timing = {}

        cena = {"id": 2, "tipo": "image", "texto": "macro de solo",
                "prompt_imagem": "close-up macro of garden soil"}
        char_info = {"uses_character": usa_char, "nome": "Marcos",
                     "character_ref": "@Marcos"}

        with mock.patch.object(pf, "PROJETOS_DIR", self.tmp), \
             mock.patch.object(pf, "REFERENCE_PNG_IDENTIDADES_FALLBACK",
                               str(self.tmp / "nao_existe.png")), \
             mock.patch.object(pf.time, "time", relogio.time), \
             mock.patch.object(pf, "pw_log") as m_log, \
             mock.patch("services.playwright_flow._detectar_cena_avatar", return_value=False), \
             mock.patch("services.character_service.obter_personagem_cena",
                        return_value=char_info), \
             mock.patch("services.scene_plan_service.atualizar_cena"), \
             mock.patch("services.scene_plan_service.atualizar_status_cena"), \
             mock.patch("services.scene_plan_service.salvar_midia_cena_estruturada",
                        return_value={"success": False, "error": "stub: nao salvar"}) as m_salvar, \
             mock.patch("services.playwright_flow.check_flow_credits",
                        return_value={"has_credits": True}):
            ok, msg = w._processar_cena_individual("proj_guard", cena, is_anim=False)
            logs = " ".join(str(c.args[0]) for c in m_log.call_args_list if c.args)
        return ok, msg, logs, m_salvar, w

    def test_a_cena_sem_personagem_rejeita_o_avatar_e_continua_o_polling(self):
        """(a) uses_character False + resultado == reference → rejeitado, chave ignorada, nada salvo."""
        ok, msg, logs, m_salvar, w = self._executar(
            usa_char=False, bytes_resultado=self.ref_png, avancar_s=200.0)
        self.assertFalse(ok)
        self.assertIn("Timeout", msg)
        self.assertEqual(m_salvar.call_count, 0,
                         "nada pode ser salvo quando o resultado é o avatar de referência")
        self.assertIn("[MIDIA_CONTAMINADA]", logs)
        self.assertGreaterEqual(len(w._chaves_ignoradas), 2,
                                "mediaKey e id/src contaminados devem ficar ignorados na fila")

    def test_b_cena_com_personagem_aceita_a_mesma_imagem(self):
        """(b) uses_character True (cenas 158/216/222) → a MESMA imagem é aceita."""
        ok, msg, logs, m_salvar, w = self._executar(
            usa_char=True, bytes_resultado=self.ref_png, avancar_s=2.4)
        self.assertEqual(m_salvar.call_count, 1, "a mídia deve chegar ao salvamento")
        self.assertNotIn("[MIDIA_CONTAMINADA]", logs)
        self.assertFalse(getattr(w, "_chaves_ignoradas", set()))

    def test_c_imagem_diferente_aceita_mesmo_com_render_de_2_4s(self):
        """(c) imagem distinta + render 2.4s → aceita, com [GERACAO_RAPIDA] (nunca rejeita)."""
        ok, msg, logs, m_salvar, w = self._executar(
            usa_char=False, bytes_resultado=_imagem_bytes(seed=9), avancar_s=2.4)
        self.assertEqual(m_salvar.call_count, 1)
        self.assertIn("[GERACAO_RAPIDA]", logs)
        self.assertIn("2.4s", logs)
        self.assertNotIn("[MIDIA_CONTAMINADA]", logs)


# ---------------------------------------------------------------- image-to-video (d)
class TestAnimarGuardAvatar(unittest.TestCase):
    """TAREFA 2 — Image-to-Video NÃO pode animar o avatar em cena sem personagem."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="guard_animar_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "proj_guard").mkdir(parents=True, exist_ok=True)
        self.ref = self.tmp / "proj_guard" / "reference.png"
        self.ref.write_bytes(_imagem_bytes(seed=1))
        self.outro = self.tmp / "outro.png"
        self.outro.write_bytes(_imagem_bytes(seed=9))

    def _worker(self):
        w = _worker_guard()
        w.current_project_id = "proj_guard"
        w._upload_arquivo_flow = MagicMock(return_value=True)
        w._limpar_overlays_e_backdrops = MagicMock()
        w._selecionar_referencia_flow = MagicMock(return_value=True)
        w._set_output_mode = MagicMock(return_value=True)
        return w

    def _chamar(self, w, caminho, **kw):
        with mock.patch.object(pf, "PROJETOS_DIR", self.tmp), \
             mock.patch.object(pf, "pw_log") as m_log:
            res = w._animar_imagem_galeria_flow("proj_guard", caminho,
                                                "prompt de movimento", **kw)
            msgs = " ".join(str(c.args[0]) for c in m_log.call_args_list if c.args)
        return res, msgs

    def test_d1_uses_character_false_aborta_sem_enviar_ao_veo(self):
        """(d) uses_character False + entrada == reference → falha explícita, nada vai ao Veo."""
        w = self._worker()
        res, msgs = self._chamar(w, str(self.ref), uses_character=False)
        self.assertFalse(res)
        w._upload_arquivo_flow.assert_not_called()
        self.assertIn("[GUARD_AVATAR] Image-to-Video ABORTADO", msgs)
        self.assertIn("nada foi enviado ao Veo", msgs)

    def test_d2_uses_character_true_legitimo_continua(self):
        """Cenas 158/216/222 (uses_character True) NÃO passam pelo guard."""
        w = self._worker()
        self._chamar(w, str(self.ref), uses_character=True)
        w._upload_arquivo_flow.assert_called_once()

    def test_d3_chamador_legado_none_nao_aplica_o_guard(self):
        """uses_character=None (chamadores legados) → guard não aplica (compatibilidade)."""
        w = self._worker()
        self._chamar(w, str(self.ref))
        w._upload_arquivo_flow.assert_called_once()

    def test_d4_imagem_distinta_com_uses_character_false_continua(self):
        """B-roll legítimo (imagem ≠ reference) segue para o upload/Animar."""
        w = self._worker()
        self._chamar(w, str(self.outro), uses_character=False)
        w._upload_arquivo_flow.assert_called_once()


# ---------------------------------------------------------------- chaves (f) + TAREFA 3
class TestChavesGuardAvatar(unittest.TestCase):
    """TAREFA 3 — chaves do upload do avatar (e chaves ignoradas) nunca são 'mídia nova'."""

    def test_f_chave_do_upload_nunca_e_nova(self):
        w = _worker_guard()
        item = {"mediaKey": "k_avatar", "id": "media_k_avatar_320",
                "src": "https://flow/media?name=k_avatar"}
        self.assertTrue(w._media_item_e_novo(item, set()))
        # (f) chave registrada pelo upload do avatar → NUNCA nova
        w._chaves_upload_conhecidas = {"k_avatar"}
        self.assertFalse(w._media_item_e_novo(item, set()))
        # exclusão por src e por id (qualquer chave do item)
        self.assertFalse(w._media_item_e_novo(
            {"mediaKey": "k_outro", "id": "id_x", "src": "src_x"}, {"src_x"}))
        self.assertFalse(w._media_item_e_novo(
            {"mediaKey": "k_outro2", "id": "id_upload", "src": "src_y"},
            {"id_upload"}))
        # chaves IGNORADAS (contaminação já detectada) também nunca são novas
        w._chaves_ignoradas = {"k_extra"}
        self.assertFalse(w._media_item_e_novo(
            {"mediaKey": "k_extra", "id": "i", "src": "s"}, set()))
        # contrato preservado: item sem mediaKey nunca é novo
        self.assertFalse(w._media_item_e_novo({"id": "i", "src": "s"}, set()))

    def test_registrar_chaves_upload_detecta_ingestao(self):
        w = _worker_guard()
        w._get_existing_media_snapshot = MagicMock(return_value=({"a", "b_nova"}, 2))
        with mock.patch.object(pf, "pw_log") as m_log:
            n = w._registrar_chaves_upload_avatar({"a"}, timeout_s=0.5)
            msgs = " ".join(str(c.args[0]) for c in m_log.call_args_list if c.args)
        self.assertEqual(n, 1)
        self.assertIn("b_nova", w._chaves_upload_conhecidas)
        self.assertIn("registrado como mídia conhecida", msgs)

    def test_registrar_chaves_upload_snapshot_vazio_nao_registra(self):
        w = _worker_guard()
        with mock.patch.object(pf, "pw_log") as m_log:
            n = w._registrar_chaves_upload_avatar(set(), timeout_s=0.1)
            msgs = " ".join(str(c.args[0]) for c in m_log.call_args_list if c.args)
        self.assertEqual(n, 0)
        self.assertIn("snapshot inicial indisponível", msgs)
        self.assertFalse(getattr(w, "_chaves_upload_conhecidas", set()))

    def test_registrar_chaves_upload_sem_ingestao_loga_aviso(self):
        w = _worker_guard()
        w.page.wait_for_timeout = MagicMock()
        w._get_existing_media_snapshot = MagicMock(return_value=({"a"}, 1))
        with mock.patch.object(pf, "pw_log") as m_log:
            n = w._registrar_chaves_upload_avatar({"a"}, timeout_s=0.05)
            msgs = " ".join(str(c.args[0]) for c in m_log.call_args_list if c.args)
        self.assertEqual(n, 0)
        self.assertIn("não detectada", msgs)
        self.assertFalse(getattr(w, "_chaves_upload_conhecidas", set()))


if __name__ == "__main__":
    unittest.main()




