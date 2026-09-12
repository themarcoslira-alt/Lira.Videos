# -*- coding: utf-8 -*-
"""
tests/test_antigravity.py — CORREÇÕES ANTIGRAVITY (avatar cascata)

1. Pré-voo sem falso positivo: _verificar_personagem_na_biblioteca só retorna True
   quando o NOME do personagem existe (fallbacks de "qualquer card"/forced-True
   removidos) e o pré-voo cria automaticamente quando ausente.
2. Reprovision no início da fila: _reprovisionar_personagem_apos_rotacao aceita
   projeto_id/forcar_criacao e é usado também no início da fila.
3. Validação com visão computacional: motor facial (facial_fidelity_engine) +
   integração em visual_judgment_service (avatar_fidelity < 70 → rejeita).
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image, ImageDraw

import services.playwright_flow as pf
import services.visual_judgment_service as vjs
from services import facial_fidelity_engine as ffe

ROOT = Path(__file__).resolve().parent.parent

PELE_MARCOS = (205, 150, 115)   # tom de pele de referência (@Marcos)
PELE_OUTRA = (220, 180, 170)    # tom pálido/rosado (outra pessoa)


def _heuristica_offline():
    """Força o motor a usar a heurística Pillow/YCbCr (determinística e offline),
    independentemente de cv2/face_recognition estarem instalados."""
    return [
        mock.patch.object(ffe, "_face_libs_disponiveis", return_value=False),
        mock.patch.object(ffe, "_cv2_disponivel", return_value=False),
    ]


def _criar_retrato(cor_pele=PELE_MARCOS, largura=240, altura=320):
    img = Image.new("RGB", (largura, altura), (70, 70, 70))
    d = ImageDraw.Draw(img)
    d.rectangle([0, int(altura * 0.72), largura, altura], fill=(45, 45, 60))
    rw, rh = int(largura * 0.40), int(altura * 0.40)
    x0 = (largura - rw) // 2
    y0 = int(altura * 0.16)
    d.ellipse([x0, y0, x0 + rw, y0 + rh], fill=cor_pele)
    d.ellipse([x0 - int(rw * 0.08), y0 - int(rh * 0.18),
               x0 + rw + int(rw * 0.08), y0 + int(rh * 0.22)], fill=(40, 30, 20))
    return np.asarray(img, dtype=np.uint8)


def _criar_cena_avatar(cor_pele=PELE_MARCOS, pessoa_x=0.30, escala=0.5):
    img = Image.new("RGB", (960, 540), (96, 110, 90))
    d = ImageDraw.Draw(img)
    rw, rh = int(320 * escala), int(420 * escala)
    cx = int(960 * pessoa_x)
    cy = 460
    d.ellipse([cx - rw // 2, cy - rh, cx + rw // 2, cy - int(rh * 0.55)], fill=cor_pele)
    d.rectangle([cx - rw // 2, cy - int(rh * 0.55), cx + rw // 2, cy], fill=(40, 60, 90))
    return np.asarray(img, dtype=np.uint8)


def _worker(**kw):
    w = pf.PlaywrightCDPWorker.__new__(pf.PlaywrightCDPWorker)
    w.port = 9222
    w.page = mock.Mock()
    w.browser = None
    w.context = None
    w.playwright = None
    w.current_flow_reference = None
    w.current_project_id = "ProjX"
    w.current_flow_mode = "imagem"
    w.current_project_name = None
    w.account_email = None
    w._avatar_uploaded = False
    w._project_url_saved = False
    w.current_delay_info = None
    w.current_model = "Nano Banana 2"
    w.is_fallback_active = False
    for k, v in kw.items():
        setattr(w, k, v)
    return w


class TestMotorFidelidadeFacial(unittest.TestCase):
    """ANTIGRAVITY #3 — motor de visão (facial_fidelity_engine)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self._off = _heuristica_offline()
        for p in self._off:
            p.start()
        self.addCleanup(self._parar_off)
        self.ref = str(self.dir / "reference.png")
        Image.fromarray(_criar_retrato()).save(self.ref)

    def _parar_off(self):
        for p in self._off:
            p.stop()

    def tearDown(self):
        self.tmp.cleanup()

    def test_mesma_pessoa_acima_do_limiar(self):
        gen = str(self.dir / "gen_same.png")
        Image.fromarray(_criar_cena_avatar(PELE_MARCOS, pessoa_x=0.72)).save(gen)
        r = ffe.calcular_fidelidade_facial(self.ref, gen)
        self.assertTrue(r["ok"])
        self.assertGreaterEqual(r["fidelidade"], ffe.LIMIAR_FIDELIDADE_AVATAR)

    def test_referencia_igual_a_si_mesma_alta(self):
        r = ffe.calcular_fidelidade_facial(self.ref, self.ref)
        self.assertGreaterEqual(r["fidelidade"], 90)

    def test_outra_pessoa_abaixo_do_limiar(self):
        gen = str(self.dir / "gen_outra.png")
        Image.fromarray(_criar_cena_avatar(PELE_OUTRA, pessoa_x=0.55, escala=0.45)).save(gen)
        r = ffe.calcular_fidelidade_facial(self.ref, gen)
        self.assertTrue(r["ok"])
        self.assertLess(r["fidelidade"], ffe.LIMIAR_FIDELIDADE_AVATAR)

    def test_sem_rosto_fidelidade_zero(self):
        gen = str(self.dir / "gen_vazio.png")
        Image.fromarray(np.full((960, 540, 3), (40, 80, 60), dtype=np.uint8)).save(gen)
        r = ffe.calcular_fidelidade_facial(self.ref, gen)
        self.assertEqual(r["fidelidade"], 0)
        self.assertFalse(r["ok"])

    def test_caminhos_inexistentes(self):
        r = ffe.calcular_fidelidade_facial(str(self.dir / "nao_existe.png"), self.ref)
        self.assertEqual(r["fidelidade"], 0)
        self.assertFalse(r["ok"])


class TestVisualJudgmentAvatar(unittest.TestCase):
    """ANTIGRAVITY #3 — integração em avaliar_imagem_cena (cena avatar)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self._off = _heuristica_offline()
        for p in self._off:
            p.start()
        self.addCleanup(self._parar_off)
        self.ref = str(self.dir / "reference.png")
        Image.fromarray(_criar_retrato()).save(self.ref)

    def _parar_off(self):
        for p in self._off:
            p.stop()

    def tearDown(self):
        self.tmp.cleanup()

    def _cena_avatar(self):
        return {
            "id": 3,
            "uses_character": True,
            "character_ref": "@Marcos",
            "scene_type": "avatar_talking",
            "prompt_imagem": "A gardener @Marcos gesturing to camera, 16:9.",
        }

    def test_cena_avatar_baixa_fidelidade_rejeita(self):
        gen = str(self.dir / "baixa.png")
        Image.fromarray(_criar_cena_avatar(PELE_OUTRA)).save(gen)
        with mock.patch("services.character_service.resolver_imagem_avatar_projeto",
                        return_value=self.ref):
            j = vjs.avaliar_imagem_cena("ProjX", self._cena_avatar(), {}, gen)
        self.assertIsNotNone(j.get("avatar_fidelity"))
        self.assertLess(j["avatar_fidelity"], vjs.LIMIAR_FIDELIDADE_AVATAR)
        self.assertFalse(j["checks"]["character"])
        self.assertEqual(j["judgment_status"], "rejected")

    def test_cena_broll_sem_cv(self):
        gen = str(self.dir / "x.png")
        Image.fromarray(_criar_cena_avatar(PELE_MARCOS)).save(gen)
        j = vjs.avaliar_imagem_cena("ProjX", {"id": 1, "uses_character": False,
                                              "scene_type": "broll_macro",
                                              "prompt_imagem": "close up"}, {}, gen)
        self.assertIsNone(j.get("avatar_fidelity"))


class TestPreVooSemFalsoPositivo(unittest.TestCase):
    """ANTIGRAVITY #1/#2 — fonte (verificação estrita e criação automática)."""

    def setUp(self):
        self.src = (ROOT / "services" / "playwright_flow.py").read_text(encoding="utf-8")

    def test_fonte_sem_falso_positivo(self):
        self.assertNotIn("loc_qualquer = dialog.locator", self.src)
        self.assertNotIn("verificado (modo text-only ativo)", self.src)
        self.assertNotIn("encontrado = True  # Permite prosseguir com consistência textual", self.src)
        self.assertIn("NÃO encontrado na aba Personagens.", self.src)

    def test_pre_voo_cria_automaticamente(self):
        self.assertIn("não encontrado na biblioteca. Criando automaticamente...", self.src)
        self.assertIn("projeto_id=projeto_id, forcar_criacao=True", self.src)
        self.assertIn('pw_log(f"[PRE_VOO] Personagem @{_nome_char} criado automaticamente")', self.src)
        self.assertIn("AUTO_CRIAR_PERSONAGEM_FLOW = True", self.src)

    def test_pre_voo_final_antes_do_loop(self):
        self.assertIn("[QUEUE] Executando pré-voo final de personagens...", self.src)
        self.assertIn("[QUEUE] Pré-voo de personagens concluído.", self.src)
        self.assertIn("projeto_id=projeto_id, forcar_criacao=False", self.src)

    def test_vj_avatar_fonte(self):
        self.assertIn("[VISUAL_JUDGMENT_AVATAR] Fidelidade facial: {_fid_av}% (método={_vj.get('metodo')})", self.src)
        self.assertIn("Fidelidade facial {_fid_av}% < 70% — rejeitando cena", self.src)


class TestReprovisionInicioFila(unittest.TestCase):
    """ANTIGRAVITY #2 — reprovision com forcar_criacao=False (início de fila)."""

    def _patches(self):
        from services import scene_plan_service as sps
        from services import character_service as chars
        foto = str(Path(__file__).resolve().parent / "reference_teste.png")
        return [
            mock.patch.object(sps, "carregar_scene_plan",
                              return_value={"cenas": [{"uses_character": True,
                                                       "scene_type": "avatar_talking"}]}),
            mock.patch.object(chars, "obter_identidade_projeto", return_value={"nome": "Marcos"}),
            mock.patch.object(chars, "resolver_imagem_avatar_projeto", return_value=foto),
            mock.patch.object(pf.Path, "exists", return_value=True),
        ]

    def test_ja_existe_nao_cria_e_marca_upload(self):
        w = _worker()
        w.current_project_id = "ProjX"
        p = self._patches()
        with mock.patch.object(pf.PlaywrightCDPWorker, "_verificar_personagem_na_biblioteca",
                               return_value=True) as m_ver, \
             mock.patch.object(pf, "criar_personagem_flow") as m_criar, \
             p[0], p[1], p[2], p[3]:
            ok = w._reprovisionar_personagem_apos_rotacao(projeto_id="ProjX", forcar_criacao=False)
        self.assertTrue(ok)
        m_ver.assert_called_once()
        m_criar.assert_not_called()
        self.assertTrue(w._avatar_uploaded)

    def test_ausente_cria_automaticamente(self):
        w = _worker()
        w.current_project_id = "ProjX"
        p = self._patches()
        with mock.patch.object(pf.PlaywrightCDPWorker, "_verificar_personagem_na_biblioteca",
                               return_value=False), \
             mock.patch.object(pf, "criar_personagem_flow", return_value=True) as m_criar, \
             p[0], p[1], p[2], p[3]:
            ok = w._reprovisionar_personagem_apos_rotacao(projeto_id="ProjX", forcar_criacao=False)
        self.assertTrue(ok)
        m_criar.assert_called_once()
        self.assertTrue(w._avatar_uploaded)


class TestAvaliarAvatarFidelidadePublico(unittest.TestCase):
    """ANTIGRAVITY #3 — API pública avaliar_avatar_fidelidade_facial()."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self._off = _heuristica_offline()
        for p in self._off:
            p.start()
        self.addCleanup(self._parar_off)
        self.ref = str(self.dir / "reference.png")
        Image.fromarray(_criar_retrato()).save(self.ref)

    def _parar_off(self):
        for p in self._off:
            p.stop()

    def tearDown(self):
        self.tmp.cleanup()

    def test_sem_referencia_motivo_claro(self):
        with mock.patch("services.character_service.resolver_imagem_avatar_projeto",
                        return_value=""):
            r = vjs.avaliar_avatar_fidelidade_facial("ProjX", 7, "qualquer.png", nome_personagem="")
        self.assertFalse(r["aprovado"])
        self.assertEqual(r["score_fidelidade"], 0)
        self.assertIn("Referência não encontrada", r["motivo"])

    def test_aprovado_quando_mesma_pessoa(self):
        gen = str(self.dir / "same.png")
        Image.fromarray(_criar_cena_avatar(PELE_MARCOS, pessoa_x=0.6)).save(gen)
        with mock.patch("services.character_service.resolver_imagem_avatar_projeto",
                        return_value=self.ref):
            r = vjs.avaliar_avatar_fidelidade_facial("ProjX", 7, gen, nome_personagem="Marcos")
        self.assertTrue(r["aprovado"])
        self.assertGreaterEqual(r["score_fidelidade"], vjs.LIMIAR_FIDELIDADE_AVATAR)

    def test_rejeitado_outra_pessoa(self):
        gen = str(self.dir / "outra.png")
        Image.fromarray(_criar_cena_avatar(PELE_OUTRA)).save(gen)
        with mock.patch("services.character_service.resolver_imagem_avatar_projeto",
                        return_value=self.ref):
            r = vjs.avaliar_avatar_fidelidade_facial("ProjX", 7, gen, nome_personagem="Marcos")
        self.assertFalse(r["aprovado"])
        self.assertLess(r["score_fidelidade"], vjs.LIMIAR_FIDELIDADE_AVATAR)
        self.assertIn("Fidelidade facial", r["motivo"])


class TestVisualRealComCV(unittest.TestCase):
    """ANTIGRAVITY #3 — validação REAL via OpenCV/embeddings (Biblioteca global)."""

    _TEM_CV2 = importlib.util.find_spec("cv2") is not None

    def test_mesma_referencia_real_aprovada(self):
        if not self._TEM_CV2:
            self.skipTest("opencv não instalado")
        ref = Path("Biblioteca/Personagens/Coringa/reference.png")
        if not ref.exists() or ref.stat().st_size < 100_000:
            self.skipTest("referência real Coringa indisponível")
        r = ffe.calcular_fidelidade_facial(str(ref), str(ref))
        if not r.get("ok"):
            self.skipTest(f"face não detectada no ambiente ({r.get('detalhe')})")
        self.assertGreaterEqual(r["fidelidade"], ffe.LIMIAR_FIDELIDADE_AVATAR)
        self.assertIn(r["metodo"], ("embeddings", "opencv_haar"))

    def test_placeholder_invalido_reprovado(self):
        if not self._TEM_CV2:
            self.skipTest("opencv não instalado")
        ref = Path("Biblioteca/Personagens/Marcos/reference.png")
        if not ref.exists():
            self.skipTest("placeholder Marcos indisponível")
        r = ffe.calcular_fidelidade_facial(str(ref), str(ref))
        self.assertEqual(r["fidelidade"], 0)
        self.assertFalse(r["ok"])

    def test_alias_placeholder_reprovado(self):
        if not self._TEM_CV2:
            self.skipTest("opencv não instalado")
        ref = Path("Biblioteca/Personagens/Marcos/reference.png")
        if not ref.exists():
            self.skipTest("placeholder Marcos indisponível")
        with mock.patch("services.character_service.resolver_imagem_avatar_projeto",
                        return_value=""):
            r = vjs.avaliar_avatar_fidelidade_facial("ProjX", 3, str(ref), nome_personagem="Marcos")
        self.assertFalse(r["aprovado"])


if __name__ == "__main__":
    unittest.main()