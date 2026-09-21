# -*- coding: utf-8 -*-
"""
Regressão — services/api_v2.py :: v2_usar_srt (pertinência do SRT ao projeto)

Cenário do bug: QUALQUER SRT era aceito cegamente. O endpoint sobrescrevia
`srt/roteiro_transcricao.srt`, `roteiro_transcricao.json` e `cenas.json` do
projeto e ainda regenerava o scene plan com force=True — então o SRT de OUTRO
projeto (ex: Gypsum) destruía o roteiro/plano do projeto atual (ex: Tomato
Plants).

Correção (guarda de pertinência): compara as 3 primeiras palavras significativas
do SRT recebido com as do `roteiro_transcricao.txt` do projeto (fonte de verdade
do áudio original). Sem NENHUMA coincidência E com mais de
LIMIAR_CENAS_PERTINENCIA cenas em `cenas.json` → HTTP 409 {"blocked": true} +
diagnóstico, SEM escrever nada em disco (nem o .srt). Projeto sem
`roteiro_transcricao.txt` (novo, sem transcrição anterior) continua aceitando.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from services import api_v2

# Textos REAIS (reduzidos) dos dois projetos do cenário reportado.
TOMATO_TXT = (
    "[00:00] I grew 12 tomato plants last summer, 12, and you know how many "
    "actually produced tomatoes?\n"
    "[00:29] I did everything right, and yet 11 of those plants looked the same.\n"
)
GYPSUM_SRT = (
    "0:00 Everyone tells you the same thing. Your\n"
    "0:02 lawn's not thick enough, fertilize it.\n"
    "0:05 Your grass is weak, fertilize it.\n"
)
TOMATO_SRT = (
    "[00:00] I grew 12 tomato plants again this season\n"
    "[00:06] I did everything right with full sun and good soil\n"
)


def _arvore_com_hashes(raiz: Path) -> dict:
    """Mapa {caminho relativo: sha256} de todos os ARQUIVOS sob `raiz`."""
    saida = {}
    for caminho in sorted(raiz.rglob("*")):
        if caminho.is_file():
            rel = str(caminho.relative_to(raiz)).replace("\\", "/")
            saida[rel] = hashlib.sha256(caminho.read_bytes()).hexdigest().upper()
    return saida


class _BaseUsarSrt(unittest.TestCase):
    PROJ = "why-your-tomato-plants"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.projetos = Path(self.tmp.name) / "projetos"
        self.pdir = self.projetos / self.PROJ
        (self.pdir / "srt").mkdir(parents=True, exist_ok=True)

        # Estado de um projeto JÁ produzido (conteúdo real que seria destruído).
        self.txt = self.pdir / "roteiro_transcricao.txt"
        self.txt.write_text(TOMATO_TXT, encoding="utf-8")
        (self.pdir / "srt" / "roteiro_transcricao.srt").write_text(
            "[00:00] I grew 12 tomato plants\n", encoding="utf-8")
        (self.pdir / "roteiro_transcricao.json").write_text(
            json.dumps({"project": self.PROJ, "segments": [1, 2, 3]}),
            encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _escrever_cenas(self, n: int):
        (self.pdir / "cenas.json").write_text(
            json.dumps([{"id": i, "texto": f"cena {i}"} for i in range(1, n + 1)]),
            encoding="utf-8",
        )

    def _post(self, srt_texto: str):
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(api_v2.api_v2_bp, url_prefix="/api/v2")
        client = app.test_client()
        with patch("services.api_v2.PROJETOS_DIR", self.projetos), \
             patch("services.scene_plan_service.PROJETOS_DIR", self.projetos), \
             patch.object(api_v2.scene_plan_svc, "gerar_scene_plan",
                          return_value={"cenas": [], "ok": True}) as mock_plan:
            res = client.post(
                f"/api/v2/transcricao/{self.PROJ}/usar_srt",
                json={"srt_texto": srt_texto},
            )
        return res, mock_plan


class TestSrtDeOutroProjetoBloqueado(_BaseUsarSrt):
    def test_01_gypsum_no_projeto_tomato_retorna_409_sem_escrever_nada(self):
        self._escrever_cenas(12)
        antes = _arvore_com_hashes(self.pdir)

        res, mock_plan = self._post(GYPSUM_SRT)

        self.assertEqual(res.status_code, 409, res.data)
        data = res.get_json()
        self.assertTrue(data.get("blocked"))
        self.assertFalse(data.get("success"))
        self.assertEqual(data.get("error"), "SRT não pertence a este projeto")
        self.assertEqual(data.get("projeto_id"), self.PROJ)
        # TAREFA 3 — diagnóstico no corpo do 409
        self.assertEqual(data.get("primeiras_palavras_srt"),
                         ["everyone", "tells", "same"])
        self.assertEqual(data.get("primeiras_palavras_projeto"),
                         ["grew", "tomato", "plants"])
        self.assertEqual(data.get("n_cenas_existentes"), 12)

        # Nada foi criado/sobrescrito e o scene plan NÃO foi regenerado.
        self.assertEqual(_arvore_com_hashes(self.pdir), antes)
        mock_plan.assert_not_called()

    def test_02_projeto_com_10_cenas_ou_menos_nao_bloqueia(self):
        self._escrever_cenas(10)  # 10 NÃO é "mais de 10"
        res, _ = self._post(GYPSUM_SRT)
        self.assertEqual(res.status_code, 200, res.data)

    def test_03_projeto_sem_txt_de_transcricao_aceita_o_srt(self):
        self.txt.unlink()  # projeto novo, sem transcrição anterior
        self._escrever_cenas(50)
        res, _ = self._post(GYPSUM_SRT)
        self.assertEqual(res.status_code, 200, res.data)


class TestSrtDoProprioProjetoAceito(_BaseUsarSrt):
    def test_04_srt_do_mesmo_projeto_e_aceito_e_grava(self):
        self._escrever_cenas(50)
        res, mock_plan = self._post(TOMATO_SRT)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.get_json().get("total_cenas"), 2)
        self.assertIn(
            "tomato plants again",
            (self.pdir / "roteiro_transcricao.json").read_text(encoding="utf-8"),
        )
        mock_plan.assert_called_once()


class TestHelpersPertinencia(unittest.TestCase):
    def test_05_palavras_significativas_ignoram_timestamps_e_stopwords(self):
        self.assertEqual(api_v2._primeiras_palavras_significativas(GYPSUM_SRT),
                         ["everyone", "tells", "same"])
        self.assertEqual(
            api_v2._primeiras_palavras_significativas("[00:00] I grew 12 tomato plants"),
            ["grew", "tomato", "plants"],
        )

    def test_06_comparacao_ignora_acentos(self):
        self.assertEqual(
            api_v2._primeiras_palavras_significativas("Transcrição completa de teste"),
            api_v2._primeiras_palavras_significativas("transcricao completa de teste"),
        )

    def test_07_contar_cenas_json_tolera_ausente_ilegivel_e_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            projetos = Path(tmp)
            with patch("services.api_v2.PROJETOS_DIR", projetos):
                self.assertEqual(api_v2._contar_cenas_json("inexistente"), 0)
                d = projetos / "com_dict"
                d.mkdir()
                (d / "cenas.json").write_text("{invalido", encoding="utf-8")
                self.assertEqual(api_v2._contar_cenas_json("com_dict"), 0)
                (d / "cenas.json").write_text(
                    json.dumps({"cenas": [1, 2, 3]}), encoding="utf-8")
                self.assertEqual(api_v2._contar_cenas_json("com_dict"), 3)


if __name__ == "__main__":
    unittest.main()

