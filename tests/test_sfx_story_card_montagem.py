# -*- coding: utf-8 -*-
"""
Testes de unidade e integração para Story Cards visíveis, SFX contextualizado,
sincronização de fala e exportação para CapCut Draft.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import capcut_draft_imagens as draft_mod
from services import scene_plan_service as sp_svc


class TestSFXAndStoryCards(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="lira_sfx_test_"))
        self.projeto_id = "test_sfx_proj"
        self.proj_dir = self._tmp / self.projeto_id
        self.proj_dir.mkdir(parents=True, exist_ok=True)

        # Configurar plano de cenas inicial
        self.plano = {
            "projeto_id": self.projeto_id,
            "cenas": [
                {
                    "id": 1,
                    "tempo_inicio": 0.0,
                    "tempo_fim": 3.0,
                    "duracao": 3.0,
                    "texto_transcricao": "Este projeto custou um dolar a mais.",
                    "tipo": "photo",
                    "media_type": "photo",
                    "is_story_card": False,
                },
                {
                    "id": 2,
                    "tempo_inicio": 3.0,
                    "tempo_fim": 6.5,
                    "duracao": 3.5,
                    "texto_transcricao": "Veja o crescimento expressivo nos dados.",
                    "tipo": "photo",
                    "media_type": "photo",
                    "is_story_card": False,
                },
            ]
        }
        (self.proj_dir / "lira_scene_plan.json").write_text(
            json.dumps(self.plano, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        # Mock PROJETOS_DIR em scene_plan_service e api_v2
        self._orig_proj_dir_sp = getattr(sp_svc, "PROJETOS_DIR", None)
        sp_svc.PROJETOS_DIR = self._tmp

    def tearDown(self):
        if self._orig_proj_dir_sp:
            sp_svc.PROJETOS_DIR = self._orig_proj_dir_sp
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_mapeamento_keywords_e_transicoes(self):
        kw = sp_svc.mapear_keywords_sfx()
        self.assertIn("dolar", kw)
        self.assertIn("dollar", kw)
        self.assertIn("crescimento", kw)
        self.assertEqual(kw["dolar"]["arquivo"], "cash_register.mp3")

        tr = sp_svc.mapeador_transicoes_sfx()
        self.assertIn("segment_change", tr)
        self.assertIn("data_reveal", tr)
        self.assertEqual(tr["segment_change"]["arquivo"], "transition_whoosh.mp3")

    def test_adicionar_sfx_a_cena(self):
        sfx_cfg = {
            "tipo": "keyword",
            "trigger": "dolar",
            "arquivo_sfx": "cash_register.mp3",
            "timecode_ms": 500,
            "volume": 0.7,
            "duracao_ms": 800
        }
        res = sp_svc.adicionar_sfx_a_cena(self.projeto_id, 1, sfx_cfg)
        self.assertTrue(res.get("success"))

        # Recarregar do disco e checar
        plan_reloaded = sp_svc.carregar_scene_plan(self.projeto_id)
        cena1 = plan_reloaded["cenas"][0]
        self.assertIn("sfx_lista", cena1)
        self.assertEqual(len(cena1["sfx_lista"]), 1)
        item = cena1["sfx_lista"][0]
        self.assertEqual(item["trigger"], "dolar")
        self.assertEqual(item["arquivo_sfx"], "cash_register.mp3")
        self.assertEqual(item["timecode_ms"], 500)
        self.assertEqual(item["volume"], 0.7)

    def test_gerar_trilha_sfx_capcut(self):
        cenas = [
            {
                "id": 1,
                "tempo_inicio": 2.0,
                "duracao": 4.0,
                "sfx_lista": [
                    {
                        "tipo": "keyword",
                        "trigger": "dolar",
                        "arquivo_sfx": "cash_register.mp3",
                        "timecode_ms": 500,
                        "volume": 0.65,
                        "duracao_ms": 800,
                    }
                ]
            }
        ]
        trilhas = draft_mod._gerar_trilha_sfx(cenas)
        self.assertEqual(len(trilhas), 1)
        t = trilhas[0]
        self.assertEqual(t["type"], "audio")
        self.assertEqual(t["id"], "sfx_1_dolar")
        self.assertEqual(t["name"], "cash_register.mp3")
        self.assertEqual(t["source_timerange"]["duration"], 800_000)
        # target_timerange start = (2.0 + 0.5) * 1_000_000 = 2_500_000
        self.assertEqual(t["target_timerange"]["start"], 2_500_000)
        self.assertEqual(t["target_timerange"]["duration"], 800_000)
        self.assertEqual(t["volume"], 0.65)

    def test_exportar_capcut_com_sfx_gera_tracks_e_materiais(self):
        destino = self._tmp / "drafts"
        destino.mkdir(exist_ok=True)
        img1 = self._tmp / "img1.png"
        img1.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")

        cenas = [
            {
                "id": 1,
                "start": 0.0,
                "duracao": 4.0,
                "arquivo": str(img1),
                "media_type": "photo",
                "texto": "Teste com SFX",
                "sfx_lista": [
                    {
                        "tipo": "keyword",
                        "trigger": "dolar",
                        "arquivo_sfx": "cash_register.mp3",
                        "timecode_ms": 1000,
                        "volume": 0.8,
                        "duracao_ms": 600,
                    }
                ]
            }
        ]

        res = draft_mod.criar_draft_imagens("proj_sfx_export", cenas, "", str(destino), nome_projeto="proj_sfx_export")
        self.assertTrue(res["success"])

        draft_content_path = Path(res["draft_dir"]) / "draft_content.json"
        self.assertTrue(draft_content_path.exists())

        dc = json.loads(draft_content_path.read_text(encoding="utf-8"))
        audio_tracks = [t for t in dc.get("tracks", []) if t.get("type") == "audio"]
        self.assertGreaterEqual(len(audio_tracks), 1)
        sfx_track = next((t for t in audio_tracks if "sfx_1_dolar" in t.get("id", "")), None)
        self.assertIsNotNone(sfx_track, "Trilha de SFX deve existir em tracks")
        self.assertEqual(sfx_track["target_timerange"]["start"], 1_000_000)

    def test_api_routes(self):
        from flask import Flask
        from services import api_v2

        app = Flask(__name__)
        app.register_blueprint(api_v2.api_v2_bp, url_prefix="/api/v2")
        client = app.test_client()

        # Mock PROJETOS_DIR
        orig_proj_api = getattr(api_v2, "PROJETOS_DIR", None)
        api_v2.PROJETOS_DIR = self._tmp

        try:
            # 1. POST /montagem/<projeto_id>/cena/1/sfx
            resp = client.post(f"/api/v2/montagem/{self.projeto_id}/cena/1/sfx", json={
                "tipo": "keyword",
                "trigger": "dolar",
                "arquivo_sfx": "cash_register.mp3",
                "timecode_ms": 750,
                "volume": 0.6,
                "duracao_ms": 500
            })
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data.get("success"))

            # 2. POST /montagem/<projeto_id>/cena/1/sincronizar_fala
            # Criar word_timestamps.json no projeto
            word_ts = {
                "segments": [
                    {
                        "start": 0.0,
                        "end": 2.75,
                        "text": "Este projeto custou um dolar a mais.",
                        "words": [
                            {"w": "Este", "s": 0.0, "e": 0.4},
                            {"w": "mais", "s": 2.2, "e": 2.75}
                        ]
                    }
                ]
            }
            (self.proj_dir / "word_timestamps.json").write_text(json.dumps(word_ts), encoding="utf-8")

            resp = client.post(f"/api/v2/montagem/{self.projeto_id}/cena/1/sincronizar_fala")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data.get("success"))
            cena1 = next(c for c in data["plan"]["cenas"] if c["id"] == 1)
            self.assertEqual(cena1["tempo_fim"], 2.75)
            self.assertEqual(cena1["duracao"], 2.75)

            # 3. POST /montagem/<projeto_id>/cena/1/regenerar_story_card
            resp = client.post(f"/api/v2/montagem/{self.projeto_id}/cena/1/regenerar_story_card")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data.get("success"))
            self.assertTrue(data.get("is_story_card"))
            self.assertIn("prompt", data)

            # 4. PATCH /projeto/<projeto_id>/cena/1/story_card
            resp = client.patch(f"/api/v2/projeto/{self.projeto_id}/cena/1/story_card", json={
                "estilo_selecionado": "data_viz"
            })
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("story_card_style"), "data_viz")

        finally:
            if orig_proj_api:
                api_v2.PROJETOS_DIR = orig_proj_api

    def test_brain_system_director_gera_sfx_lista_e_capcut_consome(self):
        """Valida que o Brain gera sfx_lista com os campos corretos e o CapCut cria as trilhas correspondentes."""
        from services.brain_system_director import BrainSystemDirector

        director = BrainSystemDirector(self.projeto_id)
        entrada = {
            "projeto_id": self.projeto_id,
            "roteiro_transcricao": {"text": "Abertura com gancho. Depois comparamos o solo.", "duration": 8.0},
            "cenas": [
                {"id": 1, "texto": "Abertura com gancho.", "start": 0.0, "end": 4.0, "role": "TRANSITION"},
                {"id": 2, "texto": "Depois comparamos o solo.", "start": 4.0, "end": 8.0}
            ]
        }

        saida = director.orquestar_producao_completa(entrada)
        cenas_out = saida["cenas"]
        scene_plan = saida["scene_plan"]

        # 1. Verifica existência de sfx_lista e sfx_ruidos na cena 1 (tem transição)
        cena1_out = cenas_out["cena_1"]
        self.assertIn("sfx_lista", cena1_out)
        self.assertIn("sfx_ruidos", cena1_out)
        self.assertGreaterEqual(len(cena1_out["sfx_lista"]), 1)

        # 2. Valida campos de cada item em sfx_lista
        item = cena1_out["sfx_lista"][0]
        self.assertIn("arquivo_sfx", item)
        self.assertIn("timecode_ms", item)
        self.assertIn("duracao_ms", item)
        self.assertIn("volume", item)
        self.assertIn("trigger", item)
        self.assertEqual(item.get("tipo"), "sfx")

        # 3. Valida que o scene_plan preserva sfx_lista
        cena1_plan = scene_plan["cenas"][0]
        self.assertIn("sfx_lista", cena1_plan)
        self.assertIn("sfx_ruidos", cena1_plan)

        # 4. Valida consumo pelo gerador de rascunho CapCut
        trilhas = draft_mod._gerar_trilha_sfx([cena1_plan])
        self.assertGreaterEqual(len(trilhas), 1)
        t = trilhas[0]
        self.assertEqual(t["type"], "audio")
        self.assertEqual(t["segments"][0]["volume"], item["volume"])
        self.assertGreater(t["target_timerange"]["duration"], 0)


if __name__ == "__main__":
    unittest.main()

