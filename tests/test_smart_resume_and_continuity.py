import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import shutil
import unittest
from pathlib import Path
from app_web import app, PROJETOS_DIR
import services.scene_plan_service as scene_plan_svc


class TestSmartResumeAndContinuity(unittest.TestCase):
    def setUp(self):
        self.proj_id = "_test_smart_resume_proj"
        self.pdir = PROJETOS_DIR / self.proj_id
        if self.pdir.exists():
            shutil.rmtree(self.pdir, ignore_errors=True)
        self.pdir.mkdir(parents=True, exist_ok=True)
        (self.pdir / "imagens").mkdir(parents=True, exist_ok=True)
        (self.pdir / "cenas").mkdir(parents=True, exist_ok=True)

        meta = {
            "id": self.proj_id,
            "nome": "Smart Resume Test",
            "modo_execucao": "automatico"
        }
        (self.pdir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

        # Cria 5 cenas no plano
        cenas = [
            {"id": 1, "texto": "Scene 1", "tempo_inicio": 0, "tempo_fim": 4, "status": "PENDENTE"},
            {"id": 2, "texto": "Scene 2", "tempo_inicio": 4, "tempo_fim": 8, "status": "PENDENTE"},
            {"id": 3, "texto": "Scene 3", "tempo_inicio": 8, "tempo_fim": 12, "status": "PENDENTE"},
            {"id": 4, "texto": "Scene 4", "tempo_inicio": 12, "tempo_fim": 16, "status": "PENDENTE"},
            {"id": 5, "texto": "Scene 5", "tempo_inicio": 16, "tempo_fim": 20, "status": "PENDENTE"},
        ]
        scene_plan_svc.salvar_scene_plan(self.proj_id, {"cenas": cenas})
        self.client = app.test_client()

    def tearDown(self):
        if self.pdir.exists():
            shutil.rmtree(self.pdir, ignore_errors=True)

    def test_smart_resume_detecta_arquivos_disco(self):
        # Simula que as cenas 1 e 2 foram baixadas no disco com sucesso
        (self.pdir / "imagens" / "001.png").write_bytes(b"X" * 1024)
        (self.pdir / "cenas" / "002.png").write_bytes(b"Y" * 1024)

        # Chama status da produção
        res = self.client.get(f"/api/v2/producao/{self.proj_id}/status").get_json()
        self.assertTrue(res["success"])
        
        rinfo = res["resume_info"]
        self.assertEqual(rinfo["total"], 5)
        self.assertEqual(rinfo["prontas_count"], 2)
        self.assertEqual(rinfo["pendentes_count"], 3)
        self.assertEqual(rinfo["proxima_cena_id"], 3)
        self.assertTrue(rinfo["pode_retomar"])
        self.assertFalse(rinfo["concluido"])

    def test_retomar_fila_ignora_prontas(self):
        # Simula cenas 1 e 2 prontas
        (self.pdir / "imagens" / "001.png").write_bytes(b"X" * 1024)
        (self.pdir / "cenas" / "002.png").write_bytes(b"Y" * 1024)

        # Dispara retomada
        res = self.client.post(f"/api/v2/producao/{self.proj_id}/retomar", json={}).get_json()
        self.assertTrue(res["success"])
        # As cenas restantes para enfileirar devem ser apenas 3 (cenas 3, 4, 5)
        self.assertEqual(res["enfileiradas"], 3)
        self.assertEqual(res["proxima_cena_id"], 3)

    def test_registro_de_erro_e_retentar_erros(self):
        # Marca cena 3 com erro e salva motivo e timestamp
        scene_plan_svc.atualizar_cena(self.proj_id, 3, {
            "status": "ERRO",
            "erro_msg": "Timeout no Google Flow ao gerar imagem",
            "erro_ts": "2026-08-24 01:50:00"
        })

        res_status = self.client.get(f"/api/v2/producao/{self.proj_id}/status").get_json()
        self.assertTrue(res_status["success"])
        rinfo = res_status["resume_info"]
        self.assertEqual(rinfo["erros_count"], 1)
        self.assertIn(3, rinfo["cenas_erro_ids"])

        # Dispara re-tentar apenas os erros
        res_retry = self.client.post(f"/api/v2/producao/{self.proj_id}/retentar_erros").get_json()
        self.assertTrue(res_retry["success"])
        self.assertEqual(res_retry["enfileiradas"], 1)
        self.assertEqual(res_retry["scene_ids"], [3])


if __name__ == "__main__":
    unittest.main()
