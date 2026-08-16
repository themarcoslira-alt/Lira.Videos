"""Testes dos AJUSTES 1-3 (ULTRACUT3 WEB):
- Ajuste 1: chaves de API mascaradas nas Configurações globais.
- Ajuste 2: criação de projeto SEM áudio (áudio apenas dentro do fluxo).
- Ajuste 3: etapa 'Buscar Vídeos' (Card 3) + renumeração do fluxo manual.
"""
import io
import unittest
from pathlib import Path

import app_web
from config import PROJETOS_DIR

client = app_web.app.test_client()

WEB_KEYS = Path(r"C:\ultracut3\web_keys.json")
WEB_CONFIG = Path(r"C:\ultracut3\web_config.json")


def _criar_sem_audio(nome, modo):
    return client.post("/api/criar_projeto", data={"nome": nome, "modo": modo},
                       content_type="multipart/form-data")


def _srt_manual(nome, linhas):
    srt = "\n".join(f"[{t}] {tx}" for t, tx in linhas)
    return client.post("/api/srt_manual", json={"projeto_id": nome, "srt": srt})


def _gerar_cenas(nome):
    from services.pipeline_service import PipelineService
    p = PipelineService()
    p.project_name = nome
    return p.gerar_cenas()


class TestAjuste1_ChavesApi(unittest.TestCase):
    def setUp(self):
        self._bk_keys = WEB_KEYS.read_text(encoding="utf-8") if WEB_KEYS.exists() else None
        self._bk_cfg = WEB_CONFIG.read_text(encoding="utf-8") if WEB_CONFIG.exists() else None

    def tearDown(self):
        if self._bk_keys is None:
            WEB_KEYS.unlink(missing_ok=True)
        else:
            WEB_KEYS.write_text(self._bk_keys, encoding="utf-8")
        if self._bk_cfg is None:
            WEB_CONFIG.unlink(missing_ok=True)
        else:
            WEB_CONFIG.write_text(self._bk_cfg, encoding="utf-8")

    def test_get_config_nunca_expoe_chave_completa(self):
        cfg = client.get("/api/config").get_json()
        self.assertTrue(cfg.get("success"))
        # os campos *_key_mascarada NUNCA contêm a chave completa
        for nome in ("claude", "pexels", "pixabay", "unsplash"):
            m = cfg.get(f"{nome}_key_mascarada", "")
            self.assertNotIn("sk-ant", m)
            self.assertTrue(m == "" or m.startswith("••••"))

    def test_salvar_chave_mascara_e_aplica(self):
        r = client.post("/api/config", json={
            "pasta_destino": r"C:\ultracut3\output\entregue",
            "pexels_api_key": "CHAVE123456789",
        }).get_json()
        self.assertTrue(r.get("success"))
        self.assertEqual(r.get("pexels_key_mascarada"), "••••6789")
        # pasta de destino preservada (não quebra config existente)
        self.assertEqual(r.get("pasta_destino"), r"C:\ultracut3\output\entregue")
        # chave efetiva aplicada (runtime)
        self.assertTrue(app_web._chave_efetiva("pexels").endswith("6789"))

    def test_placeholder_mascarado_nao_sobrescreve(self):
        client.post("/api/config", json={"pexels_api_key": "CHAVE123456789"})
        cfg = client.get("/api/config").get_json()
        # mandar o placeholder mascarado de volta NÃO troca a chave real
        r = client.post("/api/config", json={"pexels_api_key": cfg["pexels_key_mascarada"]}).get_json()
        self.assertTrue(r.get("success"))
        self.assertTrue(app_web._chave_efetiva("pexels").endswith("6789"))

    def test_mascarar_chave(self):
        self.assertEqual(app_web._mascarar_chave(""), "")
        self.assertEqual(app_web._mascarar_chave("abc"), "••••")
        self.assertEqual(app_web._mascarar_chave("abcdefgh"), "••••efgh")


class TestAjuste2_CriarSemAudio(unittest.TestCase):
    def tearDown(self):
        for nome in ("_t_aj2_manual", "_t_aj2_auto"):
            client.post(f"/api/deletar_projeto/{nome}")

    def test_criar_manual_sem_audio(self):
        r = _criar_sem_audio("_t_aj2_manual", "manual")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["status"], "aguardando_audio")
        st = client.get("/api/status/_t_aj2_manual").get_json()
        self.assertEqual(st.get("etapa"), "aguardando_audio")
        self.assertFalse(st.get("arquivo_audio"))
        # nenhuma transcrição iniciada
        self.assertFalse(st.get("transcricao_completa"))

    def test_criar_automatico_sem_audio_nao_inicia_pipeline(self):
        r = _criar_sem_audio("_t_aj2_auto", "automatico")
        self.assertEqual(r.status_code, 201)
        st = client.get("/api/status/_t_aj2_auto").get_json()
        self.assertEqual(st.get("etapa"), "aguardando_audio")
        self.assertFalse(st.get("arquivo_audio"))
        self.assertFalse(st.get("transcricao_completa"))

    def test_audio_so_dentro_do_fluxo(self):
        _criar_sem_audio("_t_aj2_manual", "manual")
        clip = io.BytesIO(b"not-a-real-audio")
        r = client.post("/api/upload_audio/_t_aj2_manual", data={
            "audio": (clip, "audio.mp3"),
        }, content_type="multipart/form-data")
        self.assertTrue(r.get_json().get("success"))
        st = client.get("/api/status/_t_aj2_manual").get_json()
        self.assertTrue(st.get("arquivo_audio"))
        self.assertEqual(st.get("etapa"), "transcrever")


class TestAjuste3_BuscarVideos(unittest.TestCase):
    def setUp(self):
        _criar_sem_audio("_t_aj3", "manual")
        _srt_manual("_t_aj3", [
            ("00:00", "There is a green lawn near the house."),
            ("00:05", "The roots are deep in the soil, and water moves down slowly."),
            ("00:10", "Maybe the sprinkler reaches every corner."),
        ])
        self.assertTrue(_gerar_cenas("_t_aj3").get("success"))

    def tearDown(self):
        client.post("/api/deletar_projeto/_t_aj3")

    def test_nome_video_elton_padrao(self):
        cenas = app_web._carregar_cenas("_t_aj3")
        self.assertTrue(cenas)
        nome = app_web._nome_video_elton(cenas[0])
        self.assertRegex(nome, r"^\d{3}_\[\d{2}-\d{2}\]_.+\.mp4$")

    def test_tipo_media_por_cena_constroi_beats_em_manual(self):
        tipos = app_web._tipo_media_por_cena("_t_aj3")
        self.assertTrue(tipos)
        self.assertTrue(all(v in ("video", "photo") for v in tipos.values()))
        # beats construído sob demanda em projeto manual
        sb = PROJETOS_DIR / "_t_aj3" / "storyboard.json"
        beats = PROJETOS_DIR / "_t_aj3" / "storyboard_beats.json"
        self.assertTrue(sb.exists() or beats.exists())

    def test_status_traz_buscar_videos(self):
        st = client.get("/api/status/_t_aj3").get_json()
        self.assertIn(st.get("buscar_videos_status"), ("idle", "concluido"))
        self.assertIsInstance(st.get("video_count"), int)
        self.assertFalse(st.get("buscar_videos_pulado"))

    def test_pular_buscar_videos_persiste(self):
        r = client.post("/api/pular_buscar_videos/_t_aj3").get_json()
        self.assertTrue(r.get("success"))
        st = client.get("/api/status/_t_aj3").get_json()
        self.assertEqual(st.get("buscar_videos_status"), "pulado")
        self.assertTrue(st.get("buscar_videos_pulado"))

    def test_upsert_e_validacao_midia(self):
        midias = []
        app_web._upsert_midia(midias, {"scene_id": 1, "success": False})
        self.assertFalse(app_web._midia_cena_valida(midias, 1))
        app_web._upsert_midia(midias, {"scene_id": 1, "success": True,
                                       "arquivo": "C:\\x.mp4"})
        self.assertEqual(len(midias), 1)
        app_web._upsert_midia(midias, {"scene_id": 2, "success": True,
                                       "arquivo": "C:\\y.mp4"})
        self.assertEqual(len(midias), 2)
        self.assertFalse(app_web._midia_cena_valida(midias, 1))  # arquivo não existe
        self.assertTrue(app_web._midia_cena_valida([{
            "scene_id": 1, "success": True,
            "arquivo": r"C:\ultracut3\requirements.txt"}], 1))

    def test_tipo_media_nao_constroi_beats_em_automatico(self):
        _criar_sem_audio("_t_aj3_auto", "automatico")
        try:
            _srt_manual("_t_aj3_auto", [("00:00", "A very quick test line for the lawn.")])
            _gerar_cenas("_t_aj3_auto")
            app_web._tipo_media_por_cena("_t_aj3_auto")
            # em modo automático NÃO é construído o storyboard de beats
            sb = PROJETOS_DIR / "_t_aj3_auto" / "storyboard.json"
            self.assertFalse(sb.exists())
        finally:
            client.post("/api/deletar_projeto/_t_aj3_auto")


if __name__ == "__main__":
    unittest.main()

