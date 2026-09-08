# -*- coding: utf-8 -*-
"""
Regressão — services/api_v2.py :: v2_usar_srt

Cenário do bug: projetos que nunca passaram por transcrição Whisper podem não
ter a pasta `srt/` (PASTAS_PROJETO_V2 de scene_plan_service NÃO inclui "srt").
Nesses casos o POST /api/v2/transcricao/<id>/usar_srt falhava com:

    FileNotFoundError: ...\\srt\\roteiro_transcricao.srt

A correção cria o parent (`srt/`) antes de escrever o arquivo .srt.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from services import api_v2


class TestV2UsarSrtCriaPasta(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.projetos = Path(self.tmp.name) / "projetos"
        self.projetos.mkdir(parents=True, exist_ok=True)
        self.proj = "proj_sem_srt"
        # Projeto existe, mas NÃO tem pasta srt/ (estado real do bug Batman).
        (self.projetos / self.proj).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _post_srt(self):
        srt_texto = (
            "1\n00:00:00,000 --> 00:00:03,000\nPrimeira fala de teste\n\n"
            "2\n00:00:03,000 --> 00:00:06,000\nSegunda fala de teste\n"
        )
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(api_v2.api_v2_bp, url_prefix="/api/v2")
        client = app.test_client()
        with patch("services.api_v2.PROJETOS_DIR", self.projetos), \
             patch("services.scene_plan_service.PROJETOS_DIR", self.projetos), \
             patch.object(api_v2.scene_plan_svc, "gerar_scene_plan",
                          return_value={"cenas": [], "ok": True}) as mock_plan:
            res = client.post(
                f"/api/v2/transcricao/{self.proj}/usar_srt",
                json={"srt_texto": srt_texto},
            )
        return res, mock_plan

    def test_upload_srt_sem_pasta_srt_cria_pasta(self):
        """Upload SRT em projeto SEM pasta srt/ deve funcionar (regressão)."""
        res, _ = self._post_srt()
        self.assertEqual(res.status_code, 200, res.data)

        pdir = self.projetos / self.proj
        srt_file = pdir / "srt" / "roteiro_transcricao.srt"
        self.assertTrue(srt_file.exists(), "srt/roteiro_transcricao.srt não criado")
        conteudo = srt_file.read_text(encoding="utf-8")
        self.assertIn("Primeira fala de teste", conteudo)
        self.assertIn("Segunda fala de teste", conteudo)

        # Segunda escrita dentro de srt/ também precisa funcionar.
        srt_json = pdir / "srt" / "roteiro_transcricao.json"
        self.assertTrue(srt_json.exists(), "srt/roteiro_transcricao.json não criado")

        # Artefatos no nível do projeto preservados (fluxo normal).
        self.assertTrue((pdir / "roteiro_transcricao.json").exists())
        self.assertTrue((pdir / "cenas.json").exists())

    def test_upload_srt_segmentos_extraidos(self):
        """Segmentos e meta continuam corretos após o fix."""
        res, _ = self._post_srt()
        self.assertEqual(res.status_code, 200, res.data)
        data = res.get_json()
        self.assertEqual(data.get("total_cenas"), 2)

        pdir = self.projetos / self.proj
        dados = (pdir / "roteiro_transcricao.json").read_text(encoding="utf-8")
        self.assertEqual(len(__import__("json").loads(dados)["segments"]), 2)


if __name__ == "__main__":
    unittest.main()
