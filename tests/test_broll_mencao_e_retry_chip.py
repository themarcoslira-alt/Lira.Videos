# -*- coding: utf-8 -*-
"""
Testes de regressão das TAREFAS 2 e 3 (Lira Studio — avatar x b-roll).

TAREFA 2 (services/character_service.py :: obter_personagem_cena)
    Cena marcada como B-roll (narrative_role == "BROLL" ou avatar_required=False)
    MAS com menção EXPLÍCITA "@NomePersonagem" no prompt_imagem/visual_prompt
    NÃO pode ser bloqueada: deve manter uses_character=True e o character_ref
    correto (ex: "@Marcos"). Sem isso o Flow gerava pessoas aleatórias em vez do
    avatar do projeto.

TAREFA 3 (services/playwright_flow.py :: retry pós-timeout)
    No retry, após limpar o editor com Control+A + Backspace, o chip do
    personagem deve ser RE-ANEXADO via _selecionar_referencia_flow antes de
    reinserir o texto — garantindo que o chip é restaurado.

Como rodar:
    python -m unittest tests.test_broll_mencao_e_retry_chip -v
"""
import ast
import json
import shutil
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent  # raiz do projeto (ex: C:\\Lira Videos)
sys.path.insert(0, str(ROOT_DIR))

import services.character_service as character_svc  # noqa: E402

FONTE_PLAYWRIGHT = ROOT_DIR / "services" / "playwright_flow.py"


class TestMencaoExplicitaVenceBloqueioBroll(unittest.TestCase):
    """TAREFA 2 — obter_personagem_cena()."""

    PROJ = "test_tarefa2_mencao_explicita_broll"

    @classmethod
    def setUpClass(cls):
        cls.pdir = ROOT_DIR / "projetos" / cls.PROJ
        if cls.pdir.exists():
            shutil.rmtree(cls.pdir)
        cls.pdir.mkdir(parents=True, exist_ok=True)
        (cls.pdir / "identidade.json").write_text(
            json.dumps(
                {
                    "tipo": "personagem",
                    "nome": "Marcos",
                    "referencia_flow": "@Marcos",
                    "imagem_abs": "",
                    "status": "vinculado",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.pdir, ignore_errors=True)

    def _cena(self, **kwargs):
        base = {
            "id": 1,
            "narrative_role": "BROLL",
            "avatar_required": False,
            "scene_type": "broll_macro",
            "uses_character": False,
        }
        base.update(kwargs)
        return base

    def test_01_broll_com_mencao_explicita_nao_bloqueia(self):
        res = character_svc.obter_personagem_cena(
            self.PROJ,
            self._cena(prompt_imagem="@Marcos looking at the camera, medium shot"),
        )
        self.assertTrue(res["uses_character"])
        self.assertEqual(res["character_ref"], "@Marcos")

    def test_02_broll_sem_mencao_continua_bloqueado(self):
        res = character_svc.obter_personagem_cena(
            self.PROJ,
            self._cena(prompt_imagem="Macro shot of rose petals, no people"),
        )
        self.assertFalse(res["uses_character"])
        self.assertEqual(res["character_ref"], "")

    def test_03_mencao_case_insensitive_no_visual_prompt(self):
        res = character_svc.obter_personagem_cena(
            self.PROJ,
            self._cena(visual_prompt="@marcos watering the tomato plants"),
        )
        self.assertTrue(res["uses_character"])
        self.assertEqual(res["character_ref"], "@Marcos")

    def test_04_avatar_required_false_com_mencao_nao_bloqueia(self):
        res = character_svc.obter_personagem_cena(
            self.PROJ,
            self._cena(narrative_role="HOOK", prompt_imagem="@Marcos gesticulating"),
        )
        self.assertTrue(res["uses_character"])
        self.assertEqual(res["character_ref"], "@Marcos")

    def test_05_mencao_a_outro_nome_nao_libera(self):
        res = character_svc.obter_personagem_cena(
            self.PROJ,
            self._cena(prompt_imagem="@Mario walking in the garden"),
        )
        self.assertFalse(res["uses_character"])
        self.assertEqual(res["character_ref"], "")

    def test_06_avatar_action_segue_excecao_legada(self):
        res = character_svc.obter_personagem_cena(
            self.PROJ,
            self._cena(scene_type="avatar_action", prompt_imagem="hands planting a seedling"),
        )
        self.assertTrue(res["uses_character"])
        self.assertEqual(res["character_ref"], "@Marcos")


class TestRetryReanexaChip(unittest.TestCase):
    """TAREFA 3 — bloco de retry pós-timeout do PlaywrightCDPWorker."""

    @classmethod
    def setUpClass(cls):
        cls.src = FONTE_PLAYWRIGHT.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.src)
        cls.i_retry = cls.src.find("SEGUNDA TENTATIVA IMEDIATA")
        cls.i_fim = cls.src.find("ERRO definitivo após a segunda tentativa", cls.i_retry)
        cls.bloco = cls.src[cls.i_retry:cls.i_fim] if cls.i_retry >= 0 and cls.i_fim > cls.i_retry else ""

    def test_01_bloco_de_retry_localizado(self):
        self.assertGreater(self.i_retry, -1, "Bloco 'SEGUNDA TENTATIVA IMEDIATA' não encontrado")
        self.assertGreater(self.i_fim, self.i_retry, "Marcador final do retry não encontrado")
        self.assertIn('press("Control+A")', self.bloco)
        self.assertIn('press("Backspace")', self.bloco)

    def test_02_retry_reanexa_o_chip(self):
        self.assertIn("_selecionar_referencia_flow(", self.bloco,
                      "O retry deve re-chamar _selecionar_referencia_flow")
        self.assertIn("if uses_char:", self.bloco,
                      "A re-anexação deve ocorrer apenas em cena com personagem")

    def test_03_ordem_limpar_anexar_reinserir(self):
        i_limpar = self.bloco.find('press("Backspace")')
        i_anexar = self.bloco.find("_selecionar_referencia_flow(")
        i_inserir = self.bloco.find("insert_text(_texto_retry)")
        self.assertTrue(
            0 <= i_limpar < i_anexar < i_inserir,
            "Ordem esperada no retry: limpar editor -> re-anexar chip -> reinserir texto",
        )

    def test_04_parametros_do_reanexo_completos(self):
        for kw in ("projeto_id=", "nome_personagem=", "tipo=", "ref_tag=",
                   "arquivo_flow=", "imagem_abs=", "flow_character_id="):
            self.assertIn(kw, self.bloco, f"Parâmetro '{kw}' ausente no re-anexo do retry")

    def test_05_logs_de_reanexo_presentes(self):
        self.assertIn("CHARACTER_REATTACHED_OK no retry", self.bloco)
        self.assertIn("CHARACTER_REATTACH_FAILED no retry", self.bloco)

    def test_06_retry_nao_insere_texto_antes_do_anexo(self):
        """Garante que o texto NÃO é reinserido antes de restaurar o chip."""
        i_inserir = self.bloco.find("insert_text(_texto_retry)")
        i_anexar = self.bloco.find("_selecionar_referencia_flow(")
        self.assertGreater(i_inserir, i_anexar)


if __name__ == "__main__":
    unittest.main(verbosity=2)
