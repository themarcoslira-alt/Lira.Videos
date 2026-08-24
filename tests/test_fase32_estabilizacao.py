import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

"""
tests/test_fase32_estabilizacao.py — FASE 3.2 (Estabilização do Pipeline de Produção)

Cobre as 3 prioridades:
  P1: escrita de mídia unificada em salvar_midia_cena_estruturada() (job-result delega).
  P2: validação REAL do arquivo antes de BAIXADA/storyboard/galeria (falha -> ERRO).
  P3: scene_plan.tipo como fonte única de tipo (animar_depois/animate_later não alteram).
"""

import base64
import io
import json
import shutil
import unittest
from pathlib import Path

import app_web
from config import PROJETOS_DIR
import services.scene_plan_service as scene_plan_svc


def _png_valida(cor=(10, 120, 10), tam=(96, 96)) -> bytes:
    """Gera uma imagem PNG REAL em memória (via Pillow) com pixels aleatórios
    (incompressíveis) para garantir tamanho > 1KB no teste de validação."""
    import os
    from PIL import Image
    buf = io.BytesIO()
    img = Image.new("RGB", tam)
    px = img.load()
    w, h = tam
    for x in range(w):
        for y in range(h):
            px[x, y] = tuple(os.urandom(3))
    img.save(buf, format="PNG")
    return buf.getvalue()


PROJ = "_t_fase32"


def _montar_plan(cenas):
    plan = {"projeto": PROJ, "versao": 2, "total": len(cenas), "cenas": cenas}
    scene_plan_svc.salvar_scene_plan(PROJ, plan)
    return plan


def _limpar_projeto():
    d = PROJETOS_DIR / PROJ
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


class TestValidacaoMidia(unittest.TestCase):
    """P2 — validação real de arquivo."""

    def test_imagem_valida_passa(self):
        r = scene_plan_svc.validar_midia_bytes(_png_valida(), is_video=False)
        self.assertTrue(r["valid"])

    def test_imagem_vazia_falha(self):
        self.assertFalse(scene_plan_svc.validar_midia_bytes(b"", False)["valid"])

    def test_imagem_lixo_falha_decodificacao(self):
        # tamanho acima do mínimo, mas bytes não são imagem
        r = scene_plan_svc.validar_midia_bytes(b"\x00\x01\x02" * 500, False)
        self.assertFalse(r["valid"])
        self.assertIn("decodific", r["error"])

    def test_video_falso_sem_assinatura_falha(self):
        r = scene_plan_svc.validar_midia_bytes(b"\x00" * 20000, True)
        self.assertFalse(r["valid"])

    def test_video_ftyp_mp4_passa(self):
        dados = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 9000
        self.assertTrue(scene_plan_svc.validar_midia_bytes(dados, True)["valid"])

    def test_video_webm_passa(self):
        dados = b"\x1a\x45\xdf\xa3" + b"\x00" * 9000
        self.assertTrue(scene_plan_svc.validar_midia_bytes(dados, True)["valid"])

    def test_tamanho_minimo_imagem(self):
        self.assertIn(
            "pequena",
            scene_plan_svc.validar_midia_bytes(b"a" * 100, False)["error"],
        )


class TestTipoUnico(unittest.TestCase):
    """P3 — fonte única de tipo."""

    def test_tipo_image(self):
        self.assertEqual(scene_plan_svc.tipo_efetivo_cena({"tipo": "image"}), "image")

    def test_tipo_video(self):
        self.assertEqual(scene_plan_svc.tipo_efetivo_cena({"tipo": "video"}), "video")

    def test_animate_later_nao_altera_image(self):
        # animar_depois/animate_later NÃO tornam image em vídeo
        cena = {"tipo": "image", "animar_depois": True, "animate_later": True}
        self.assertEqual(scene_plan_svc.tipo_efetivo_cena(cena), "image")

    def test_animar_true_cai_para_video(self):
        self.assertEqual(scene_plan_svc.tipo_efetivo_cena({"animar": True}), "video")

    def test_sem_tipo_default_image(self):
        self.assertEqual(scene_plan_svc.tipo_efetivo_cena({}), "image")
class TestEscritaUnificada(unittest.TestCase):
    """P1 + P2 — escrita canônica com validação."""

    def setUp(self):
        _limpar_projeto()
        _montar_plan([
            {"id": 1, "tempo_inicio": 0.0, "tempo_fim": 3.0, "duracao": 3.0,
             "prompt_imagem": "garden", "tipo": "image"},
            {"id": 2, "tempo_inicio": 3.0, "tempo_fim": 6.0, "duracao": 3.0,
             "prompt_imagem": "portrait", "tipo": "image"},
        ])

    def tearDown(self):
        _limpar_projeto()

    def test_midia_valida_salva_em_storyboard_e_galeria(self):
        r = scene_plan_svc.salvar_midia_cena_estruturada(
            projeto_id=PROJ, cid=1, ts_ini=0.0, ts_fim=3.0,
            prompt_texto="prompt", midia_bytes=_png_valida(), is_video=False,
        )
        self.assertTrue(r["success"])
        self.assertEqual(r["tipo"], "image")

        # storyboard
        sb = scene_plan_svc.carregar_storyboard(PROJ)
        self.assertEqual(len(sb["cenas"]), 1)
        self.assertEqual(sb["cenas"][0]["cena"], 1)
        self.assertEqual(sb["cenas"][0]["status"], scene_plan_svc.STATUS_BAIXADA)
        self.assertTrue(Path(sb["cenas"][0]["arquivo_path"]).exists())

        # galeria
        gal = scene_plan_svc.carregar_galeria(PROJ)
        self.assertEqual(gal["total_itens"], 1)

    def test_midia_invalida_nao_entra_no_storyboard(self):
        r = scene_plan_svc.salvar_midia_cena_estruturada(
            projeto_id=PROJ, cid=1, ts_ini=0.0, ts_fim=3.0,
            prompt_texto="prompt", midia_bytes=b"\x00\x01\x02" * 1500, is_video=False,
        )
        self.assertFalse(r["success"])
        self.assertIn("decodific", r["error"])

        sb = scene_plan_svc.carregar_storyboard(PROJ)
        self.assertEqual(len(sb["cenas"]), 0, "mídia inválida NUNCA entra no storyboard")

        gal = scene_plan_svc.carregar_galeria(PROJ)
        self.assertEqual(gal["total_itens"], 0, "mídia inválida NUNCA entra na galeria")

        # status da cena vira ERRO
        plan = scene_plan_svc.carregar_scene_plan(PROJ)
        cena = next(c for c in plan["cenas"] if c["id"] == 1)
        self.assertEqual(cena["status"], scene_plan_svc.STATUS_ERRO)


class TestJobResultDelega(unittest.TestCase):
    """P1 — /api/flow/job-result delega para salvar_midia_cena_estruturada().

    Nenhum arquivo deve ser gravado em pasta paralela 'midias/'.
    """

    def setUp(self):
        _limpar_projeto()
        _montar_plan([
            {"id": 1, "tempo_inicio": 0.0, "tempo_fim": 3.0, "duracao": 3.0,
             "prompt_imagem": "garden", "tipo": "image"},
        ])

    def tearDown(self):
        _limpar_projeto()

    def _b64_png(self):
        return "data:image/png;base64," + base64.b64encode(_png_valida()).decode()

    def test_job_result_usa_caminho_canonico(self):
        client = app_web.app.test_client()
        r = client.post("/api/flow/job-result", json={
            "jobId": "job1",
            "sceneId": 1,
            "projetoId": PROJ,
            "result": {"files": [{"dataUrl": self._b64_png()}], "videoMode": False},
        })
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json().get("success"))

        # mídia em cenas/ (caminho canônico), NÃO em midias/ paralela
        self.assertTrue((PROJETOS_DIR / PROJ / "cenas" / "001.png").exists())
        self.assertFalse((PROJETOS_DIR / PROJ / "midias" / "001.png").exists())

        # storyboard atualizado via canônico
        sb = scene_plan_svc.carregar_storyboard(PROJ)
        self.assertEqual(len(sb["cenas"]), 1)


if __name__ == "__main__":
    unittest.main()