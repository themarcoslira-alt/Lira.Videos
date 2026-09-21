# -*- coding: utf-8 -*-
"""
Regressão — services/character_service.py :: isolamento da BIBLIOTECA GLOBAL

Bug: os personagens da biblioteca global eram candidatos em QUALQUER projeto por
simples coincidência de palavra na narração — a cena 142 ("at the two to three
week mark") injetava o @Mark da biblioteca, sem o projeto ter chamado ninguém.

Contrato novo: personagem da biblioteca global só é candidato quando chamado
EXPLICITAMENTE pelo projeto:
  (a) "@Nome" citado no texto da cena (prompt/narração);
  (b) nome em "personagens_autorizados" da config do projeto
      (project_config.json ou meta.json);
  (c) nome é o avatar/personagem principal do projeto
      (avatar_nome / personagem_global / nome_personagem).
Fora disso, é bloqueado e registrado no log:
  "PERSONAGEM_BLOQUEADO: {nome} não autorizado no projeto {proj} (palavra
   coincidente mas sem @menção explícita)".

Personagens do PRÓPRIO projeto (characters/) e o modo "só biblioteca"
(projeto_id == "") continuam com o comportamento anterior.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import services.character_service as char_svc

DUMMY_IMG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
             b"\x01\x08\x06\x00\x00\x00\x1f\x15c4")


class _BaseIsolamento(unittest.TestCase):
    PROJ = "proj_isolamento_biblioteca"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self.tmp.name)
        self.projetos = self.raiz / "projetos"
        self.pdir = self.projetos / self.PROJ
        self.pdir.mkdir(parents=True)
        self.biblioteca = self.raiz / "Biblioteca" / "Personagens"
        self.biblioteca.mkdir(parents=True)
        # Biblioteca global com os dois casos do relato.
        self._add_biblioteca("Mark")
        self._add_biblioteca("Lira")
        self._p1 = patch("services.character_service.PROJETOS_DIR", self.projetos)
        self._p2 = patch("services.character_service._get_biblioteca_personagens_dir",
                         lambda: self.biblioteca)
        self._p1.start()
        self._p2.start()

    def tearDown(self):
        self._p2.stop()
        self._p1.stop()
        self.tmp.cleanup()

    def _add_biblioteca(self, nome, **extra):
        d = self.biblioteca / nome
        d.mkdir(parents=True)
        (d / "reference.png").write_bytes(DUMMY_IMG)
        data = {"nome": nome, "referencia_flow": f"@{nome}",
                "imagem_abs": str(d / "reference.png")}
        data.update(extra)
        (d / "character.json").write_text(json.dumps(data, ensure_ascii=False),
                                          encoding="utf-8")

    def _add_character_projeto(self, nome):
        d = self.pdir / "characters" / nome
        d.mkdir(parents=True)
        (d / "reference.png").write_bytes(DUMMY_IMG)
        (d / "character.json").write_text(
            json.dumps({"nome": nome, "imagem_abs": str(d / "reference.png")},
                       ensure_ascii=False), encoding="utf-8")

    def _escrever_config(self, arquivo, dados):
        (self.pdir / arquivo).write_text(json.dumps(dados, ensure_ascii=False),
                                        encoding="utf-8")

    def _detectar(self, texto, cena_extra=None):
        cena = {"id": 142, "texto": texto}
        if cena_extra:
            cena.update(cena_extra)
        with patch("services.character_service.log_event") as mock_log:
            res = char_svc.detectar_personagens_cena(self.PROJ, cena)
        bloqueados = [c.args[1] for c in mock_log.call_args_list
                      if "PERSONAGEM_BLOQUEADO" in str(c.args[1])]
        return res, bloqueados


class TestBibliotecaGlobalIsolada(_BaseIsolamento):
    def test_01_palavra_coincidente_sem_arroba_nao_injeta_mark(self):
        res, bloqueados = self._detectar("at the two to three week mark")
        self.assertEqual(res["total_detectados"], 0)
        self.assertEqual([p["nome"] for p in res["personagens"]], [])
        # TAREFA 3 — log do bloqueio no formato exigido
        self.assertEqual(bloqueados, [
            f"PERSONAGEM_BLOQUEADO: Mark não autorizado no projeto {self.PROJ} "
            "(palavra coincidente mas sem @menção explícita)"])

    def test_02_arroba_explicito_continua_funcionando(self):
        res, bloqueados = self._detectar("@Lira looking towards the camera")
        self.assertEqual([p["nome"] for p in res["personagens"]], ["Lira"])
        self.assertEqual(res["personagens"][0]["origem"], "biblioteca_global")
        self.assertEqual(bloqueados, [])

    def test_03_personagens_autorizados_no_project_config(self):
        self._escrever_config("project_config.json",
                              {"personagens_autorizados": ["Lira"]})
        res, _ = self._detectar("Lira olha para a camera")
        self.assertEqual([p["nome"] for p in res["personagens"]], ["Lira"])

    def test_04_avatar_principal_autorizado_pelo_meta(self):
        self._escrever_config("meta.json", {"nome_personagem": "Lira"})
        res, _ = self._detectar("Lira olha para a camera")
        self.assertEqual([p["nome"] for p in res["personagens"]], ["Lira"])

    def test_05_caminho_de_imagem_do_avatar_nao_autoriza(self):
        # personagem_global_path (arquivo) NÃO autoriza nenhum nome
        self._escrever_config("meta.json",
                              {"personagem_global_path": "C:/p/personagem_global.png"})
        res, _ = self._detectar("Lira olha para a camera")
        self.assertEqual(res["total_detectados"], 0)

    def test_06_sem_autorizacao_nenhum_personagem_da_biblioteca_entra(self):
        res, bloqueados = self._detectar("Mark e Lira no jardim")
        self.assertEqual(res["total_detectados"], 0)
        self.assertEqual(len(bloqueados), 2)

    def test_07_autorizado_no_project_config_nao_bloqueia_nem_loga(self):
        self._escrever_config("project_config.json",
                              {"personagens_autorizados": [{"nome": "Mark"}]})
        res, bloqueados = self._detectar("at the two to three week mark")
        self.assertEqual([p["nome"] for p in res["personagens"]], ["Mark"])
        self.assertEqual(bloqueados, [])

    def test_08_projeto_id_vazio_usa_biblioteca_integral(self):
        with patch("services.character_service.log_event") as mock_log:
            res = char_svc.detectar_personagens_cena("", {"texto": "Mark no jardim"})
        self.assertEqual([p["nome"] for p in res["personagens"]], ["Mark"])
        bloqueados = [c for c in mock_log.call_args_list
                      if "PERSONAGEM_BLOQUEADO" in str(c.args[1])]
        self.assertEqual(bloqueados, [])

    def test_09_personagem_do_proprio_projeto_segue_intocado(self):
        self._add_character_projeto("Ana")
        res, _ = self._detectar("Ana e Mark no jardim")
        self.assertEqual([p["nome"] for p in res["personagens"]], ["Ana"])
        self.assertEqual(res["personagens"][0]["origem"], "character.json")

    def test_10_nome_do_projeto_igual_ao_da_biblioteca_nao_e_bloqueado(self):
        self._add_character_projeto("Mark")
        res, bloqueados = self._detectar("at the two to three week mark")
        self.assertEqual([p["nome"] for p in res["personagens"]], ["Mark"])
        self.assertEqual(res["personagens"][0]["origem"], "character.json")
        self.assertEqual(bloqueados, [])


if __name__ == "__main__":
    unittest.main()

