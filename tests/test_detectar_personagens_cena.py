"""
tests/test_detectar_personagens_cena.py
=======================================
Testes de `detectar_personagens_cena` (services/character_service.py):

1. Detecção por nome com limites de palavra (sem falsos positivos em substrings)
2. Resolução da referência na hierarquia:
   Flow Character ID > @nome > reference.png > upload comum
3. Sobreposição de nomes compostos (nome mais longo vence)
4. Fontes de candidatos (identidade, characters/, references.json, biblioteca)
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from config import PROJETOS_DIR
import services.character_service as char_svc


DUMMY_IMG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"


class TestResolverReferencia(unittest.TestCase):
    """Hierarquia pura: flow_id > @nome > reference.png > upload comum."""

    def test_flow_id_prioritario(self):
        r = char_svc._resolver_referencia_entidade(
            {"nome": "A", "flow_character_id": "flow_x", "referencia_flow": "@A"}, "P"
        )
        self.assertEqual(r["tipo"], "flow_id")
        self.assertEqual(r["valor"], "flow_x")

    def test_at_nome_quando_sem_flow_id(self):
        r = char_svc._resolver_referencia_entidade(
            {"nome": "A", "referencia_flow": "@Ana"}, "P"
        )
        self.assertEqual(r["tipo"], "@nome")
        self.assertEqual(r["valor"], "@Ana")

    def test_alias_usa_como_tag(self):
        r = char_svc._resolver_referencia_entidade(
            {"nome": "Marcos", "alias": "@marcos_lira"}, "P"
        )
        self.assertEqual(r["tipo"], "@nome")
        self.assertEqual(r["valor"], "@marcos_lira")

    def test_reference_png_sem_tag(self):
        # Sem flow_id e sem tag -> cai para reference.png (imagem isolada)
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "reference.png"
            img.write_bytes(DUMMY_IMG)
            r = char_svc._resolver_referencia_entidade(
                {"nome": "C", "reference_image_abs": str(img)}, "P"
            )
            self.assertEqual(r["tipo"], "reference.png")
            self.assertTrue(r["valor"].endswith("reference.png"))

    def test_upload_comum_quando_arquivo_original(self):
        # Arquivo original (nome != reference.png) -> upload comum
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "duda_original.jpeg"
            img.write_bytes(DUMMY_IMG)
            r = char_svc._resolver_referencia_entidade(
                {"nome": "D", "imagem_abs": str(img)}, "P"
            )
            self.assertEqual(r["tipo"], "upload")

    def test_sem_nada(self):
        r = char_svc._resolver_referencia_entidade({"nome": "E"}, "P")
        self.assertEqual(r["tipo"], "nenhuma")

    def test_hierarquia_completa_entidade(self):
        # Entidade que tem TUDO -> flow_id vence
        r = char_svc._resolver_referencia_entidade(
            {
                "nome": "Marcos",
                "flow_character_id": "flow_abc",
                "referencia_flow": "@Marcos",
                "reference_image_abs": r"C:\tmp\reference.png",
            },
            "P",
        )
        self.assertEqual(r["tipo"], "flow_id")


class TestNomesDeteccao(unittest.TestCase):
    """Detecção por nome NÃO usa tags divergentes nem arquivo_flow."""

    def test_tag_divergente_nao_vira_nome(self):
        # Personagem "MarcosS" com tag "@Marcos" -> só "marcoss" é nome detectável
        nomes = char_svc._nomes_deteccao_entidade(
            {"nome": "MarcosS", "flow_character_name": "@Marcos", "referencia_flow": "@Marcos"}
        )
        self.assertNotIn("marcos", [n.lower() for n in nomes])
        self.assertIn("marcoss", [n.lower() for n in nomes])

    def test_alias_canonico_e_nome(self):
        nomes = char_svc._nomes_deteccao_entidade({"nome": "Marcos", "alias": "@marcos_lira"})
        baixos = [n.lower() for n in nomes]
        self.assertIn("marcos", baixos)
        self.assertIn("marcos_lira", baixos)


class TestDetectarPersonagensCena(unittest.TestCase):

    def setUp(self):
        self.proj = "test_detectar_personagens_cena"
        self.pdir = PROJETOS_DIR / self.proj
        shutil.rmtree(self.pdir, ignore_errors=True)
        self.pdir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.pdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Helpers de montagem
    # ------------------------------------------------------------------
    def _add_character(self, nome, imagem="reference.png", extra=None):
        """Cria characters/<nome>/character.json (+ imagem)."""
        d = self.pdir / "characters" / nome
        d.mkdir(parents=True, exist_ok=True)
        (d / imagem).write_bytes(DUMMY_IMG)
        data = {
            "name": nome,
            "type": "human",
            "locked": True,
            "reference_image": f"characters/{nome}/{imagem}",
            "reference_image_abs": str(d / imagem),
        }
        if extra:
            data.update(extra)
        (d / "character.json").write_text(json.dumps(data), encoding="utf-8")

    def _add_identidade(self, nome, referencia_flow="", flow_character_id=""):
        idt = {
            "tipo": "personagem",
            "nome": nome,
            "referencia_flow": referencia_flow or f"@{nome}",
            "flow_character_id": flow_character_id,
            "personagens": [{"nome": nome, "referencia_flow": referencia_flow or f"@{nome}"}],
        }
        (self.pdir / "identidade.json").write_text(json.dumps(idt, ensure_ascii=False), encoding="utf-8")

    def _add_referencia(self, alias, nome, descricao=""):
        d = self.pdir / "references" / alias.lstrip("@")
        d.mkdir(parents=True, exist_ok=True)
        (d / "reference.png").write_bytes(DUMMY_IMG)
        ref = {
            "id": f"uuid-{alias.lstrip('@')}",
            "alias": alias,
            "nome": nome,
            "tipo": "character",
            "descricao": descricao,
            "imagem": f"references/{alias.lstrip('@')}/reference.png",
            "imagem_abs": str(d / "reference.png"),
        }
        self.pdir.joinpath("references.json").write_text(
            json.dumps([ref], ensure_ascii=False), encoding="utf-8"
        )


    # ------------------------------------------------------------------
    # 1. HIERARQUIA (via fluxo real, sem colidir com biblioteca global)
    # ------------------------------------------------------------------
    def test_01_flow_character_id_prioritario(self):
        # Personagem com nome raro (sem colisão com biblioteca real)
        self._add_character("Zefir Teste")
        self._add_identidade("Zefir Teste", referencia_flow="@Zefir Teste", flow_character_id="flow_abc")
        res = char_svc.detectar_personagens_cena(self.proj, {"texto": "Zefir Teste cuida do jardim"})
        self.assertEqual(res["total_detectados"], 1)
        ref = res["personagens"][0]["referencia"]
        self.assertEqual(ref["tipo"], "flow_id")
        self.assertEqual(ref["valor"], "flow_abc")

    def test_02_at_nome_via_references(self):
        # Só references.json (sem characters/) -> origem references.json
        self._add_referencia("@xaviera", "Xaviera", "Apresentadora")
        res = char_svc.detectar_personagens_cena(self.proj, {"texto": "Xaviera apresenta o video"})
        self.assertEqual(res["total_detectados"], 1)
        p = res["personagens"][0]
        self.assertEqual(p["origem"], "references.json")
        self.assertEqual(p["referencia"]["tipo"], "@nome")
        self.assertEqual(p["referencia"]["valor"], "@xaviera")

    def test_03_deteccao_dupla_flow_e_at(self):
        self._add_character("Zefir Teste")
        self._add_identidade("Zefir Teste", referencia_flow="@Zefir Teste", flow_character_id="flow_abc")
        self._add_referencia("@xaviera", "Xaviera", "Apresentadora")
        res = char_svc.detectar_personagens_cena(
            self.proj, {"texto": "Zefir Teste apresenta Xaviera no jardim"}
        )
        mapa = {p["nome"]: p["referencia"]["tipo"] for p in res["personagens"]}
        self.assertEqual(mapa.get("Zefir Teste"), "flow_id")
        self.assertEqual(mapa.get("Xaviera"), "@nome")

    # ------------------------------------------------------------------
    # 2. LIMITES DE PALAVRA E DETECÇÃO
    # ------------------------------------------------------------------
    def test_06_substring_nao_detecta(self):
        self._add_character("Ana")
        res = char_svc.detectar_personagens_cena(self.proj, {"texto": "Banana e uma fruta"})
        self.assertEqual(res["total_detectados"], 0)

    def test_07_sufixo_nao_detecta(self):
        self._add_character("Marcos")
        res = char_svc.detectar_personagens_cena(self.proj, {"texto": "O marcosinho brinca"})
        self.assertEqual(res["total_detectados"], 0)

    def test_08_nome_mais_longo_consome_posicao(self):
        self._add_character("Ana")
        self._add_character("Ana Carla")
        res = char_svc.detectar_personagens_cena(self.proj, {"texto": "Ana Carla cuida do jardim"})
        nomes = [p["nome"] for p in res["personagens"]]
        self.assertIn("Ana Carla", nomes)
        self.assertNotIn("Ana", nomes)  # 'Ana' está dentro de 'Ana Carla'

    def test_09_ambos_detectados_quando_citados(self):
        self._add_character("Ana")
        self._add_character("Ana Carla")
        res = char_svc.detectar_personagens_cena(self.proj, {"texto": "Ana e Ana Carla no jardim"})
        nomes = [p["nome"] for p in res["personagens"]]
        self.assertIn("Ana", nomes)
        self.assertIn("Ana Carla", nomes)

    def test_10_campos_avaliados(self):
        self._add_character("Ana")
        res = char_svc.detectar_personagens_cena(
            self.proj, {"id": 7, "texto": "", "narration": "Ana explica", "prompt_imagem": "Ana sorrindo"}
        )
        self.assertEqual(res["total_detectados"], 1)
        campos = res["personagens"][0]["campos"]
        self.assertIn("narration", campos)
        self.assertIn("prompt_imagem", campos)

    def test_11_cena_sem_texto_nao_detecta(self):
        self._add_character("Ana")
        res = char_svc.detectar_personagens_cena(self.proj, {"id": 1})
        self.assertEqual(res["total_detectados"], 0)

    def test_12_cena_invalida(self):
        res = char_svc.detectar_personagens_cena(self.proj, None)
        self.assertEqual(res["total_detectados"], 0)

    # ------------------------------------------------------------------
    # 3. FONTES DE CANDIDATOS E ORIGEM
    # ------------------------------------------------------------------
    def test_13_origem_identidade(self):
        self._add_character("Zefir Teste")
        self._add_identidade("Zefir Teste", referencia_flow="@Zefir Teste")
        res = char_svc.detectar_personagens_cena(self.proj, {"texto": "Zefir Teste fala"})
        self.assertEqual(res["personagens"][0]["origem"], "identidade")

    def test_14_origem_references(self):
        self._add_referencia("@xaviera", "Xaviera")
        res = char_svc.detectar_personagens_cena(self.proj, {"texto": "Xaviera aparece"})
        self.assertEqual(res["personagens"][0]["origem"], "references.json")

    def test_15_biblioteca_global_incluida(self):
        # Biblioteca real do projeto tem "Joy Boy"
        res = char_svc.detectar_personagens_cena(
            "", {"texto": "Joy Boy mostra o jardim"}  # sem projeto -> biblioteca global
        )
        self.assertEqual(res["total_detectados"], 1)
        self.assertEqual(res["personagens"][0]["nome"], "Joy Boy")

    def test_16_tag_divergente_nao_cria_falso_positivo(self):
        # Personagem com tag @Marcos mas nome MarcosS NÃO deve casar com "Marcos"
        # (teste unitário da lista de nomes — já coberto em TestNomesDeteccao;
        # aqui garante que a biblioteca global real "Marcos" é o único hit para 'Marcos')
        self._add_character("MarcosS", extra={
            "flow_character_name": "@Marcos",
            "referencia_flow": "@Marcos",
        })
        res = char_svc.detectar_personagens_cena(self.proj, {"texto": "Marcos fala"})
        nomes = [p["nome"] for p in res["personagens"]]
        self.assertNotIn("MarcosS", nomes)


if __name__ == "__main__":
    unittest.main()
