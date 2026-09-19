"""
Testes de regressão para resolução estrita por scene_index e prevenção de colisão com id.
Reproduz o cenário real do projeto 'tomatinho':
  - Cena 1: id=3, scene_index=1 -> 03_[00-00-00-05].png
  - Cena 2: id=4, scene_index=2 -> 04_[00-05-00-11].png
  - Cena 3: id=5, scene_index=3 -> 05_[00-11-00-16].png
  - Cena 4: id=6, scene_index=4 -> 06_[00-16-00-22].png

Garante que buscar por scene_index=3 retorne a Cena 3 (adubo/fertilizante)
e não a Cena 1 (galpão), mesmo que o id da Cena 1 seja 3.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import scene_plan_service as sp_svc
from services.scene_plan_service import (
    resolver_arquivo_cena,
    limpar_midia_cenas_por_ids,
    carregar_scene_plan,
)
from app_web import app, _arquivo_midia_cena


class TestResolucaoCenaSceneIndex(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.proj_dir = Path(self.tmp_dir) / "test_tomatinho"
        self.cenas_dir = self.proj_dir / "cenas"
        self.cenas_dir.mkdir(parents=True, exist_ok=True)

        # Cria 4 arquivos fictícios com mais de 500 bytes
        self.f1 = self.cenas_dir / "03_[00-00-00-05].png"
        self.f2 = self.cenas_dir / "04_[00-05-00-11].png"
        self.f3 = self.cenas_dir / "05_[00-11-00-16].png"
        self.f4 = self.cenas_dir / "06_[00-16-00-22].png"

        for f in (self.f1, self.f2, self.f3, self.f4):
            f.write_bytes(b"A" * 1024)

        # Monta scene_plan idêntico ao tomatinho
        self.plan = {
            "projeto": "test_tomatinho",
            "cenas": [
                {
                    "id": 3,
                    "scene_index": 1,
                    "tempo_inicio": 0.0,
                    "tempo_fim": 5.7,
                    "arquivo_midia": str(self.f1),
                    "filename": self.f1.name,
                    "status": "BAIXADA",
                    "image_status": "READY",
                    "prompt_imagem": "Prompt 1 Galpao",
                    "visual_prompt": "Prompt 1 Galpao",
                },
                {
                    "id": 4,
                    "scene_index": 2,
                    "tempo_inicio": 5.7,
                    "tempo_fim": 11.32,
                    "arquivo_midia": str(self.f2),
                    "filename": self.f2.name,
                    "status": "BAIXADA",
                    "image_status": "READY",
                    "prompt_imagem": "Prompt 2 Regador",
                    "visual_prompt": "Prompt 2 Regador",
                },
                {
                    "id": 5,
                    "scene_index": 3,
                    "tempo_inicio": 11.32,
                    "tempo_fim": 16.93,
                    "arquivo_midia": str(self.f3),
                    "filename": self.f3.name,
                    "status": "BAIXADA",
                    "image_status": "READY",
                    "prompt_imagem": "Prompt 3 Fertilizante",
                    "visual_prompt": "Prompt 3 Fertilizante",
                },
                {
                    "id": 6,
                    "scene_index": 4,
                    "tempo_inicio": 16.93,
                    "tempo_fim": 22.51,
                    "arquivo_midia": str(self.f4),
                    "filename": self.f4.name,
                    "status": "BAIXADA",
                    "image_status": "READY",
                    "prompt_imagem": "Prompt 4 Tomateiro",
                    "visual_prompt": "Prompt 4 Tomateiro",
                },
            ]
        }

        self.plan_path = self.proj_dir / "lira_scene_plan.json"
        self.plan_path.write_text(json.dumps(self.plan, indent=2), encoding="utf-8")

        # Patches de PROJETOS_DIR e _project_dir
        self.patcher1 = patch.object(sp_svc, "PROJETOS_DIR", Path(self.tmp_dir))
        self.patcher2 = patch.object(sp_svc, "_project_dir", lambda p: Path(self.tmp_dir) / p)
        self.patcher3 = patch("app_web.PROJETOS_DIR", Path(self.tmp_dir))
        self.patcher4 = patch("services.api_v2._project_dir", lambda p: Path(self.tmp_dir) / p)
        self.patcher1.start()
        self.patcher2.start()
        self.patcher3.start()
        self.patcher4.start()

        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        self.patcher1.stop()
        self.patcher2.stop()
        self.patcher3.stop()
        self.patcher4.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_resolver_arquivo_cena_retorna_cena_correta(self):
        """resolver_arquivo_cena(cid=3) deve retornar a Cena 3 (05_...), NUNCA a Cena 1 (03_...)."""
        arq3 = resolver_arquivo_cena("test_tomatinho", 3)
        self.assertIsNotNone(arq3)
        self.assertEqual(arq3.name, "05_[00-11-00-16].png",
                         "cid=3 deveria resolver para o arquivo da Cena 3 (05_...), e não da Cena 1")

        arq4 = resolver_arquivo_cena("test_tomatinho", 4)
        self.assertIsNotNone(arq4)
        self.assertEqual(arq4.name, "06_[00-16-00-22].png",
                         "cid=4 deveria resolver para o arquivo da Cena 4 (06_...), e não da Cena 2")

        arq1 = resolver_arquivo_cena("test_tomatinho", 1)
        self.assertIsNotNone(arq1)
        self.assertEqual(arq1.name, "03_[00-00-00-05].png")

        arq2 = resolver_arquivo_cena("test_tomatinho", 2)
        self.assertIsNotNone(arq2)
        self.assertEqual(arq2.name, "04_[00-05-00-11].png")

    def test_arquivo_midia_cena_web(self):
        """_arquivo_midia_cena(scene_id=3) deve retornar o path da Cena 3."""
        arq3 = _arquivo_midia_cena("test_tomatinho", 3)
        self.assertIsNotNone(arq3)
        self.assertTrue(arq3.endswith("05_[00-11-00-16].png"))

    def test_api_v2_obter_cena(self):
        """GET /api/v2/projeto/<id>/cena/3 deve retornar a cena com scene_index=3."""
        res = self.client.get("/api/v2/projeto/test_tomatinho/cena/3")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        cena = data.get("cena", {})
        self.assertEqual(cena.get("scene_index"), 3)
        self.assertEqual(cena.get("id"), 5)
        self.assertEqual(cena.get("prompt_imagem"), "Prompt 3 Fertilizante")

    def test_api_v2_editar_prompt_cena(self):
        """PATCH na cena 3 deve atualizar apenas a Cena 3, preservando a Cena 1 (id=3)."""
        novo_prompt = "Novo Prompt Exclusivo da Cena 3"
        res = self.client.patch(
            "/api/v2/projeto/test_tomatinho/cena/3/prompt",
            json={"prompt_imagem": novo_prompt}
        )
        self.assertEqual(res.status_code, 200)

        plan_reloaded = carregar_scene_plan("test_tomatinho")
        c1 = next(c for c in plan_reloaded["cenas"] if c["scene_index"] == 1)
        c3 = next(c for c in plan_reloaded["cenas"] if c["scene_index"] == 3)

        self.assertEqual(c3["prompt_imagem"], novo_prompt)
        self.assertEqual(c1["prompt_imagem"], "Prompt 1 Galpao",
                         "Cena 1 (id=3) NÃO pode ter sido modificada pela edição da Cena 3!")

    def test_api_v2_remover_midia_cena_nao_afeta_outra_cena(self):
        """DELETE de mídia na cena 3 deve remover o arquivo físico da cena 3 e preservar o da cena 1."""
        self.assertTrue(self.f1.exists())
        self.assertTrue(self.f3.exists())

        res = self.client.delete("/api/v2/projetos/test_tomatinho/cenas/3/midia")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("ok"))

        # O arquivo da cena 3 foi apagado
        self.assertFalse(self.f3.exists(), "Arquivo da cena 3 deveria ter sido removido")
        # O arquivo da cena 1 PERMANECE intacto
        self.assertTrue(self.f1.exists(), "Arquivo da cena 1 (03_...) NÃO pode ter sido apagado pelo DELETE da cena 3!")

        plan_reloaded = carregar_scene_plan("test_tomatinho")
        c1 = next(c for c in plan_reloaded["cenas"] if c["scene_index"] == 1)
        c3 = next(c for c in plan_reloaded["cenas"] if c["scene_index"] == 3)

        self.assertEqual(c3["arquivo_midia"], "")
        self.assertEqual(c3["status"], "PENDENTE")
        self.assertTrue(c1["arquivo_midia"].endswith("03_[00-00-00-05].png"))

    def test_limpar_midia_cenas_preserva_colisao(self):
        """limpar_midia_cenas_por_ids([3]) deve limpar a cena 3 e não apagar a cena 1."""
        res = limpar_midia_cenas_por_ids("test_tomatinho", [3])
        self.assertTrue(res.get("success"))

        self.assertFalse(self.f3.exists(), "Arquivo da cena 3 deveria ter sido removido")
        self.assertTrue(self.f1.exists(), "Arquivo da cena 1 (id=3) deve ser preservado!")

    def test_fallback_legado_sem_scene_index(self):
        """Cenas de projetos legados sem a chave scene_index devem resolver por id."""
        legacy_plan = {
            "projeto": "legacy_proj",
            "cenas": [
                {
                    "id": 99,
                    "tempo_inicio": 0.0,
                    "arquivo_midia": str(self.f1),
                }
            ]
        }
        leg_dir = Path(self.tmp_dir) / "legacy_proj"
        leg_dir.mkdir(parents=True, exist_ok=True)
        (leg_dir / "lira_scene_plan.json").write_text(json.dumps(legacy_plan), encoding="utf-8")

        arq = resolver_arquivo_cena("legacy_proj", 99)
        self.assertIsNotNone(arq)
        self.assertEqual(arq.name, "03_[00-00-00-05].png")


if __name__ == "__main__":
    unittest.main()
