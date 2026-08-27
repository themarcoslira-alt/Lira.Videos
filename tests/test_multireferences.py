"""
tests/test_multireferences.py — Suíte de Testes Automatizados FASE A.2
=======================================================================
Validações completas do Catálogo Multirreferência:
- IDs em formato UUID v4 estável (diferentes entre referências, preservado no rename)
- Imagem obrigatória para character e style (API e Service retornam erro sem foto)
- Sanitização de alias canônico (lowercase, sem acento, sem path traversal)
- Rejeição de tipos inválidos e de objeto
- Conflito e duplicidade (HTTP 409)
- JSON corrompido preservado sem sobrescrita
- Desacoplamento absoluto do sistema legado (identidade.json, meta.json, characters/)
- Operações completas da API REST v2
"""

import os
import json
import uuid
import shutil
import unittest
from io import BytesIO
from pathlib import Path

from config import PROJETOS_DIR
import services.character_service as char_svc
from app_web import app


class TestMultiReferencesFaseA2(unittest.TestCase):

    def setUp(self):
        self.proj_id = "test_multiref_fase_a2"
        self.pdir = PROJETOS_DIR / self.proj_id
        self.pdir.mkdir(parents=True, exist_ok=True)
        self.client = app.test_client()
        self.dummy_img = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"

    def tearDown(self):
        if self.pdir.exists():
            shutil.rmtree(self.pdir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. TESTES DE SANITIZAÇÃO DE ALIAS
    # -------------------------------------------------------------------------
    def test_01_alias_lowercase(self):
        self.assertEqual(char_svc.sanitizar_alias("Marcos"), "@marcos")
        self.assertEqual(char_svc.sanitizar_alias("@Marcos"), "@marcos")
        self.assertEqual(char_svc.sanitizar_alias("ESTILO"), "@estilo")

    def test_02_alias_com_acento(self):
        self.assertEqual(char_svc.sanitizar_alias("João Silva"), "@joao_silva")
        self.assertEqual(char_svc.sanitizar_alias("ÁRVORE Azul"), "@arvore_azul")
        self.assertEqual(char_svc.sanitizar_alias("Café"), "@cafe")

    def test_03_alias_com_espacos(self):
        self.assertEqual(char_svc.sanitizar_alias("Estilo Vintage"), "@estilo_vintage")
        self.assertEqual(char_svc.sanitizar_alias("   marcos   lira   "), "@marcos_lira")

    def test_04_alias_com_caracteres_invalidos(self):
        self.assertEqual(char_svc.sanitizar_alias("Marcos!@#$"), "@marcos")
        self.assertEqual(char_svc.sanitizar_alias("estilo---vintage###123"), "@estilo_vintage_123")

    def test_05_protecao_path_traversal(self):
        traversals = ["../marcos", "../../etc/passwd", "..\\marcos", "/root", "marcos/../outro", "\x00marcos"]
        for bad in traversals:
            with self.assertRaises(ValueError):
                char_svc.sanitizar_alias(bad)

    def test_06_nome_ou_alias_vazio_rejeitado(self):
        with self.assertRaises(ValueError):
            char_svc.sanitizar_alias("")
        with self.assertRaises(ValueError):
            char_svc.sanitizar_alias("   ")
        with self.assertRaises(ValueError):
            char_svc.sanitizar_alias("@")

    # -------------------------------------------------------------------------
    # 2. TESTES DE VALIDAÇÃO DE ID (UUID LOCAL E ESTÁVEL)
    # -------------------------------------------------------------------------
    def test_07_id_formato_uuid_valido(self):
        res = char_svc.adicionar_referencia_projeto(
            self.proj_id, "Marcos", tipo="character", imagem_bytes=self.dummy_img
        )
        ref_id = res["referencia"]["id"]
        # Valida que é um UUID v4 válido
        val = uuid.UUID(ref_id, version=4)
        self.assertEqual(str(val), ref_id)

    def test_08_duas_referencias_possuem_uuids_diferentes(self):
        res1 = char_svc.adicionar_referencia_projeto(
            self.proj_id, "Marcos", tipo="character", imagem_bytes=self.dummy_img
        )
        res2 = char_svc.adicionar_referencia_projeto(
            self.proj_id, "Vintage", tipo="style", imagem_bytes=self.dummy_img
        )
        id1 = res1["referencia"]["id"]
        id2 = res2["referencia"]["id"]
        self.assertNotEqual(id1, id2)

    def test_09_rename_preserva_mesmo_uuid(self):
        res = char_svc.adicionar_referencia_projeto(
            self.proj_id, "Marcos", tipo="character", imagem_bytes=self.dummy_img
        )
        id_original = res["referencia"]["id"]

        res_ren = char_svc.renomear_referencia_projeto(
            self.proj_id, "@marcos", "Marcos Lira"
        )
        self.assertEqual(res_ren["referencia"]["id"], id_original)

        # Consulta novamente do disco para garantir persistência
        item_disco = char_svc.obter_referencia_por_alias(self.proj_id, "@marcos_lira")
        self.assertEqual(item_disco["id"], id_original)

    # -------------------------------------------------------------------------
    # 3. TESTES DE IMAGEM OBRIGATÓRIA
    # -------------------------------------------------------------------------
    def test_10_character_sem_imagem_rejeitado(self):
        with self.assertRaises(ValueError) as ctx:
            char_svc.adicionar_referencia_projeto(self.proj_id, "Sem Foto", tipo="character")
        self.assertIn("imagem de referência é obrigatório", str(ctx.exception).lower())

    def test_11_style_sem_imagem_rejeitado(self):
        with self.assertRaises(ValueError) as ctx:
            char_svc.adicionar_referencia_projeto(self.proj_id, "Sem Foto Estilo", tipo="style")
        self.assertIn("imagem de referência é obrigatório", str(ctx.exception).lower())

    def test_12_api_post_sem_imagem_retorna_400(self):
        res = self.client.post(
            f"/api/v2/referencias/{self.proj_id}/adicionar",
            data={"alias": "Sem Foto API", "tipo": "character"}
        )
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data["success"])
        self.assertIn("imagem de referência é obrigatório", data["error"].lower())

    # -------------------------------------------------------------------------
    # 4. TESTES DE TIPOS E CADASTRO
    # -------------------------------------------------------------------------
    def test_13_adicionar_character_e_compatibilidade_personagem(self):
        res1 = char_svc.adicionar_referencia_projeto(
            self.proj_id, "Marcos", tipo="character", imagem_bytes=self.dummy_img
        )
        self.assertTrue(res1["success"])
        self.assertEqual(res1["referencia"]["tipo"], "character")
        self.assertEqual(res1["referencia"]["alias"], "@marcos")

        res2 = char_svc.adicionar_referencia_projeto(
            self.proj_id, "Joao", tipo="personagem", imagem_bytes=self.dummy_img
        )
        self.assertTrue(res2["success"])
        self.assertEqual(res2["referencia"]["tipo"], "character")
        self.assertEqual(res2["referencia"]["alias"], "@joao")

    def test_14_adicionar_style_e_compatibilidade_estilo(self):
        res1 = char_svc.adicionar_referencia_projeto(
            self.proj_id, "Estilo Vintage", tipo="style", imagem_bytes=self.dummy_img
        )
        self.assertTrue(res1["success"])
        self.assertEqual(res1["referencia"]["tipo"], "style")
        self.assertEqual(res1["referencia"]["alias"], "@estilo_vintage")

        res2 = char_svc.adicionar_referencia_projeto(
            self.proj_id, "Aquarela", tipo="estilo", imagem_bytes=self.dummy_img
        )
        self.assertTrue(res2["success"])
        self.assertEqual(res2["referencia"]["tipo"], "style")
        self.assertEqual(res2["referencia"]["alias"], "@aquarela")

    def test_15_rejeitar_objeto_e_tipo_invalido(self):
        with self.assertRaises(ValueError):
            char_svc.adicionar_referencia_projeto(self.proj_id, "Prod", tipo="objeto", imagem_bytes=self.dummy_img)
        with self.assertRaises(ValueError):
            char_svc.adicionar_referencia_projeto(self.proj_id, "Prod", tipo="musica", imagem_bytes=self.dummy_img)

    def test_16_duplicidade_de_alias_rejeitada(self):
        char_svc.adicionar_referencia_projeto(self.proj_id, "Marcos", tipo="character", imagem_bytes=self.dummy_img)
        with self.assertRaises(ValueError) as ctx:
            char_svc.adicionar_referencia_projeto(self.proj_id, "MARCOS", tipo="character", imagem_bytes=self.dummy_img)
        self.assertIn("já existe", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 5. TESTES DE CONSULTA, RENAME E REMOÇÃO
    # -------------------------------------------------------------------------
    def test_17_listar_catalogo_e_obter_por_alias(self):
        char_svc.adicionar_referencia_projeto(self.proj_id, "Marcos", tipo="character", imagem_bytes=self.dummy_img, descricao="Apresentador")
        char_svc.adicionar_referencia_projeto(self.proj_id, "Vintage", tipo="style", imagem_bytes=self.dummy_img)

        lista = char_svc.listar_referencias_projeto(self.proj_id)
        self.assertEqual(len(lista), 2)

        item = char_svc.obter_referencia_por_alias(self.proj_id, "@marcos")
        self.assertIsNotNone(item)
        self.assertEqual(item["nome"], "Marcos")
        self.assertEqual(item["descricao"], "Apresentador")

        item_inexistente = char_svc.obter_referencia_por_alias(self.proj_id, "@nao_existe")
        self.assertIsNone(item_inexistente)

    def test_18_rename_com_movimentacao_de_pasta(self):
        char_svc.adicionar_referencia_projeto(self.proj_id, "Marcos", tipo="character", imagem_bytes=self.dummy_img)
        old_file = self.pdir / "references" / "marcos" / "reference.png"
        self.assertTrue(old_file.exists())

        res = char_svc.renomear_referencia_projeto(self.proj_id, "@marcos", "Marcos Lira")
        self.assertTrue(res["success"])
        self.assertEqual(res["referencia"]["alias"], "@marcos_lira")

        new_file = self.pdir / "references" / "marcos_lira" / "reference.png"
        self.assertFalse(old_file.exists())
        self.assertTrue(new_file.exists())

    def test_19_rename_com_colisao(self):
        char_svc.adicionar_referencia_projeto(self.proj_id, "Marcos", tipo="character", imagem_bytes=self.dummy_img)
        char_svc.adicionar_referencia_projeto(self.proj_id, "Joao", tipo="character", imagem_bytes=self.dummy_img)
        with self.assertRaises(ValueError) as ctx:
            char_svc.renomear_referencia_projeto(self.proj_id, "@marcos", "Joao")
        self.assertIn("Conflito", str(ctx.exception))

    def test_20_remover_referencia_e_inexistente(self):
        char_svc.adicionar_referencia_projeto(self.proj_id, "Marcos", tipo="character", imagem_bytes=self.dummy_img)
        folder = self.pdir / "references" / "marcos"
        self.assertTrue(folder.exists())

        ok = char_svc.remover_referencia_projeto(self.proj_id, "@marcos")
        self.assertTrue(ok)
        self.assertEqual(len(char_svc.listar_referencias_projeto(self.proj_id)), 0)
        self.assertFalse(folder.exists())

        with self.assertRaises(KeyError):
            char_svc.remover_referencia_projeto(self.proj_id, "@inexistente")

    # -------------------------------------------------------------------------
    # 6. TESTES DE ARQUIVO E DESACOPLAMENTO LEGADO
    # -------------------------------------------------------------------------
    def test_21_references_json_inexistente_nao_cria_nada(self):
        ref_file = self.pdir / "references.json"
        if ref_file.exists():
            ref_file.unlink()

        res = char_svc.listar_referencias_projeto(self.proj_id)
        self.assertEqual(res, [])
        self.assertFalse(ref_file.exists(), "listar_referencias_projeto NÃO deve criar references.json")

    def test_22_references_json_corrompido_nao_sobrescrito(self):
        ref_file = self.pdir / "references.json"
        corrupted = "{CORRUPTED_JSON_ERROR..."
        ref_file.write_text(corrupted, encoding="utf-8")

        with self.assertRaises(ValueError) as ctx:
            char_svc.listar_referencias_projeto(self.proj_id)
        self.assertIn("corrompido", str(ctx.exception))
        self.assertEqual(ref_file.read_text(encoding="utf-8"), corrupted)

    def test_23_sistema_legado_intacto(self):
        # 1. Cria identidade.json, meta.json e characters/
        idt_file = self.pdir / "identidade.json"
        idt_content = json.dumps({"nome": "MarcosLegado", "referencia_flow": "@MarcosLegado"})
        idt_file.write_text(idt_content, encoding="utf-8")

        meta_file = self.pdir / "meta.json"
        meta_content = json.dumps({"nome_personagem": "MetaOriginal", "locked": True})
        meta_file.write_text(meta_content, encoding="utf-8")

        char_dir = self.pdir / "characters" / "MarcosAntigo"
        char_dir.mkdir(parents=True, exist_ok=True)
        (char_dir / "reference.png").write_bytes(b"OLD_REF")

        # 2. Operações no novo catálogo
        char_svc.adicionar_referencia_projeto(self.proj_id, "NovoChar", tipo="character", imagem_bytes=self.dummy_img)
        char_svc.renomear_referencia_projeto(self.proj_id, "@novochar", "NovoChar2")
        char_svc.remover_referencia_projeto(self.proj_id, "@novochar2")

        # 3. Garante que nada foi alterado no legado
        self.assertEqual(idt_file.read_text(encoding="utf-8"), idt_content)
        self.assertEqual(meta_file.read_text(encoding="utf-8"), meta_content)
        self.assertEqual((char_dir / "reference.png").read_bytes(), b"OLD_REF")

    # -------------------------------------------------------------------------
    # 7. TESTES DA API REST V2
    # -------------------------------------------------------------------------
    def test_24_api_get_listar(self):
        char_svc.adicionar_referencia_projeto(self.proj_id, "Marcos", tipo="character", imagem_bytes=self.dummy_img)
        res = self.client.get(f"/api/v2/referencias/{self.proj_id}")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["referencias"]), 1)
        self.assertEqual(data["referencias"][0]["alias"], "@marcos")

    def test_25_api_post_com_upload_de_imagem(self):
        res = self.client.post(
            f"/api/v2/referencias/{self.proj_id}/adicionar",
            data={
                "alias": "Estilo Vintage",
                "tipo": "style",
                "imagem": (BytesIO(self.dummy_img), "reference.png")
            },
            content_type="multipart/form-data"
        )
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["referencia"]["alias"], "@estilo_vintage")
        self.assertEqual(data["referencia"]["tipo"], "style")
        self.assertTrue(data["referencia"]["has_image"])

    def test_26_api_post_conflito_409(self):
        char_svc.adicionar_referencia_projeto(self.proj_id, "Marcos", tipo="character", imagem_bytes=self.dummy_img)
        res = self.client.post(
            f"/api/v2/referencias/{self.proj_id}/adicionar",
            data={
                "alias": "Marcos",
                "tipo": "character",
                "imagem": (BytesIO(self.dummy_img), "reference.png")
            },
            content_type="multipart/form-data"
        )
        self.assertEqual(res.status_code, 409)

    def test_27_api_patch_rename(self):
        char_svc.adicionar_referencia_projeto(self.proj_id, "Marcos", tipo="character", imagem_bytes=self.dummy_img)
        res = self.client.patch(
            f"/api/v2/referencias/{self.proj_id}/%40marcos",
            json={"novo_nome": "Marcos Lira"}
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["referencia"]["alias"], "@marcos_lira")

    def test_28_api_delete_remover(self):
        char_svc.adicionar_referencia_projeto(self.proj_id, "Marcos", tipo="character", imagem_bytes=self.dummy_img)
        res = self.client.delete(f"/api/v2/referencias/{self.proj_id}/%40marcos")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])

        # Segunda remoção deve retornar 404
        res_404 = self.client.delete(f"/api/v2/referencias/{self.proj_id}/%40marcos")
        self.assertEqual(res_404.status_code, 404)


if __name__ == "__main__":
    unittest.main()
