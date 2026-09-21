# -*- coding: utf-8 -*-
"""
Regressão — B-Roll ancorado na narração (broll_intelligence + Crítico Automático).

Bug: casamento por SUBSTRING cego gerava objetos de outro domínio — "app" dentro
de "applied"/"happens"/"approach" acionava a lista de tecnologia (teclado
mecânico, code syntax) num vídeo de jardim; "flower" (substring) acionava rosa
ornamental para qualquer menção a flores.

Contrato novo:
  * casamento por PALAVRA INTEIRA (\\bpalavra\\b) — "app" NÃO casa "applied";
  * ação física   -> "Gardener's hands <ação> <objeto>, POV close-up, garden environment";
  * conceitual    -> macro botânico puro, SEM presença humana;
  * resultado     -> macro do resultado citado (flores/frutos/tomate/raízes/folhas);
  * crítico       -> prompt B-Roll com objeto de outro domínio é SUBSTITUÍDO pelo
                     macro botânico determinístico (exceto projeto de tecnologia).
"""
import unittest
from unittest.mock import patch

import services.broll_intelligence_service as br
from services import deepseek_prompt_service as ds

CTX = {"world": "Lush botanical rustic garden with green foliage, rich dark soil and natural daylight"}
CTX_PACK = {"theme": "Gardening, botanical care and organic cultivation",
            "world": "Lush botanical rustic garden with green foliage, rich dark soil and natural daylight",
            "recurring_elements": ["rich dark garden soil", "pruning shears"]}
HUMANOS = ("hands", "hand", "person", "people", "gardener", "presenter", "man", "woman", "face")


def _tem_presenca_humana(texto: str) -> bool:
    """Checa presença humana por PALAVRA INTEIRA ('face' não casa 'surface')."""
    return any(br._casa(br._compilar((t,)), br._normalizar(texto)) for t in HUMANOS)


class TestCasamentoPorPalavraInteira(unittest.TestCase):
    def test_01_app_nao_casa_applied_happens_approach(self):
        for fala in ("I applied fertilizer to the soil",
                     "and here's why that happens",
                     "changes your entire approach",
                     "Application is simple"):
            itens = br.gerar_broll_inteligente({"id": 1, "narration": fala}, "broll_macro", CTX)
            texto = " ".join(itens).lower()
            for proibido in ("keyboard", "code syntax", "workstation", "dark mode display"):
                self.assertNotIn(proibido, texto, f"{fala} -> {texto}")

    def test_02_flor_nao_casa_flower_e_ingles_tem_chave_propria(self):
        # "flor" (PT) NÃO casa o inglês "flower" — a chave EN é declarada à parte.
        self.assertFalse(br._casa(br._compilar(("flor",)), br._normalizar("the flower is open")))
        itens = br.gerar_broll_inteligente({"id": 1, "narration": "the flower is open"}, "broll_macro", CTX)
        self.assertIn("flower", " ".join(itens).lower())

    def test_03_rose_nao_casa_nitrogen_glucose_purpose(self):
        for fala in ("nitrogen is the key", "glucose levels rise", "the purpose of the leaves"):
            itens = br.gerar_broll_inteligente({"id": 1, "narration": fala}, "broll_macro", CTX)
            self.assertNotIn("rose petals", " ".join(itens).lower(), fala)

    def test_04_roses_real_aciona_o_resultado_de_flor(self):
        itens = br.gerar_broll_inteligente({"id": 1, "narration": "the roses are blooming"},
                                           "broll_macro", CTX)
        self.assertIn("flower", " ".join(itens).lower())


class TestAncoragemNaNarracao(unittest.TestCase):
    def test_05_acao_fisica_vira_pov_de_maos(self):
        itens = br.gerar_broll_inteligente(
            {"id": 142, "narration": "I applied gypsum to the soil"}, "broll_macro", CTX)
        self.assertEqual(len(itens), 3)
        self.assertEqual(itens[0],
                         "Gardener's hands applying gypsum to the soil, POV close-up, garden environment")

    def test_06_conceitual_vira_macro_botanico_sem_humano(self):
        itens = br.gerar_broll_inteligente(
            {"id": 1, "narration": "And here's why that happens. Nitrogen."}, "broll_macro", CTX)
        texto = " ".join(itens).lower()
        self.assertFalse(_tem_presenca_humana(texto), texto)
        self.assertTrue(any(t in texto for t in ("soil", "roots", "foliage")))

    def test_07_resultado_visual_flores_quase_nenhuma(self):
        itens = br.gerar_broll_inteligente(
            {"id": 1, "narration": "the flowers were almost none"}, "broll_macro", CTX)
        self.assertIn("tomato plant", itens[0].lower())
        self.assertIn("flowers", itens[0].lower())

    def test_08_resultado_visual_tomate_raiz_folha(self):
        casos = {"the tomatoes are ripening": "tomato",
                 "the root system is weak": "root",
                 "the leaves are yellowing": "leaf"}
        for fala, esperado in casos.items():
            itens = br.gerar_broll_inteligente({"id": 1, "narration": fala}, "broll_macro", CTX)
            self.assertIn(esperado, itens[0].lower(), fala)
            self.assertFalse(_tem_presenca_humana(" ".join(itens).lower()), fala)

    def test_09_sem_ancora_usa_macro_botanico_base(self):
        itens = br.gerar_broll_inteligente({"id": 1, "narration": "And you know what?"},
                                           "broll_macro", CTX)
        self.assertEqual(itens[0],
                         "Extreme close-up macro of rich dark garden soil texture, natural daylight, "
                         "shallow depth of field, photorealistic")

    def test_10_projeto_de_tecnologia_mantem_lista_tech(self):
        itens = br.gerar_broll_inteligente(
            {"id": 1, "narration": "this code needs a refactor"}, "broll_macro",
            {"world": "Modern tech workspace with sleek workstation"})
        self.assertIn("keyboard", " ".join(itens).lower())

    def test_11_narracoes_reais_do_projeto_tomate_sem_objeto_fora_de_dominio(self):
        falas = ["And here's why that happens. Nitrogen. Nitrogen",
                 "open it up your soil structure. Application is simple.",
                 "Somehow have tomatoes by July, while mine were still bare.",
                 "to my garden. I had been growing tomatoes for three years using standard approaches."]
        for fala in falas:
            itens = br.gerar_broll_inteligente({"id": 1, "narration": fala}, "broll_macro", CTX)
            texto = " ".join(itens).lower()
            for proibido in ("keyboard", "code syntax", "workstation", "monitor", "screen"):
                self.assertNotIn(proibido, texto, fala)


class TestCriticoDominioBroll(unittest.TestCase):
    ANIM_OK = "Slow cinematic push-in with parallax drift across the garden, 4s smooth ease"

    def _criticar(self, prompt, context_pack=None, cena_extra=None):
        esperada = {"id": 1, "scene_type": "broll_macro",
                    "narration": "and here's why that happens"}
        if cena_extra:
            esperada.update(cena_extra)
        gerada = {"scene_index": 1, "prompt_imagem": prompt, "prompt_animacao": self.ANIM_OK,
                  "scene_type": esperada.get("scene_type")}
        with patch.object(ds, "gerar_prompts_lote") as mock_lote:
            finais, reprovadas, _usage = ds.executar_critic_prompts(
                [gerada], [esperada], context_pack or CTX_PACK, api_key=None)
        return finais, reprovadas, mock_lote

    def test_12_keyboard_em_broll_e_substituido_pelo_fallback(self):
        finais, reprovadas, mock_lote = self._criticar(
            "Hands typing on a minimalist mechanical keyboard, POV close-up, garden environment")
        self.assertEqual(reprovadas, [])
        mock_lote.assert_not_called()
        self.assertEqual(finais[0]["prompt_imagem"], ds.PROMPT_BROLL_FALLBACK)
        self.assertEqual(finais[0]["status"], "PROMPT_SUBSTITUIDO_DOMINIO")
        self.assertIn("keyboard", finais[0]["prompt_imagem_original"].lower())

    def test_13_todos_os_termos_proibidos_sao_substituidos(self):
        for termo in ("mechanical keyboard", "a wide monitor", "on screen", "wooden desk",
                      "modern workstation", "clean code syntax", "dark mode display"):
            finais, _rep, _m = self._criticar(f"Cinematic shot of {termo} inside the room")
            self.assertEqual(finais[0]["prompt_imagem"], ds.PROMPT_BROLL_FALLBACK, termo)

    def test_14_projeto_de_tecnologia_preserva_o_prompt(self):
        pack = {"theme": "Technology, digital innovation and software development",
                "world": "Modern tech workspace with sleek workstation and ambient LED lighting"}
        prompt = "Hands typing on a minimalist mechanical keyboard, POV close-up, soft LED lighting"
        finais, _rep, _m = self._criticar(prompt, context_pack=pack)
        self.assertEqual(finais[0]["prompt_imagem"], prompt)
        self.assertEqual(finais[0]["status"], "PROMPT_VALIDADO")

    def test_15_cena_avatar_nao_e_substituida(self):
        prompt = "The presenter speaks to camera with a screen wall behind the studio set"
        finais, _rep, _m = self._criticar(prompt, cena_extra={"scene_type": "avatar_talking",
                                                             "narrative_role": "AVATAR",
                                                             "avatar_required": True})
        self.assertEqual(finais[0]["prompt_imagem"], prompt)

    def _criticar_reprovada(self, patch_lote):
        """Força uma cena reprovada (prompt_animacao curto) e testa o regen."""
        esperada = {"id": 7, "scene_type": "broll_macro", "narration": "and here's why that happens"}
        gerada = {"scene_index": 7, "prompt_imagem": "Cinematic garden shot of rich dark soil",
                  "prompt_animacao": "push in", "scene_type": "broll_macro"}
        with patch.object(ds, "gerar_prompts_lote", **patch_lote):
            finais, reprovadas, _u = ds.executar_critic_prompts(
                [gerada], [esperada], CTX_PACK, api_key=None)
        return finais, reprovadas

    def test_16_prompt_regenerado_com_keyboard_tambem_e_substituido(self):
        finais, reprovadas = self._criticar_reprovada({
            "side_effect": lambda *_a, **_k: {
                "scenes": [{"scene_index": 7,
                            "prompt_imagem": "Hands typing on a mechanical keyboard, dark mode display",
                            "prompt_animacao": "Slow cinematic push-in with parallax drift in the garden"}],
                "usage": {}}})

        self.assertEqual(len(reprovadas), 1)
        self.assertEqual(finais[0]["prompt_imagem"], ds.PROMPT_BROLL_FALLBACK)
        self.assertEqual(finais[0]["status"], "PROMPT_SUBSTITUIDO_DOMINIO")

    def test_17_falha_na_regeneracao_nao_devolve_prompt_contaminado(self):
        finais, _rep = self._criticar_reprovada({"side_effect": RuntimeError("api off")})
        self.assertEqual(len(finais), 1)
        for proibido in ("keyboard", "code syntax", "workstation"):
            self.assertNotIn(proibido, finais[0]["prompt_imagem"].lower())


if __name__ == "__main__":
    unittest.main()

