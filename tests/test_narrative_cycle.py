import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

"""
test_narrative_cycle.py — Lira Studio v0.3.5+
=============================================
Valida o ciclo narrativo Avatar → Imagem → Vídeo:
  1. gerar_ciclos_narrativos produz múltiplo de 3 e sequência 1→2→3;
  2. nunca 2 avatar_intro consecutivos;
  3. scene_schema.validar_ciclo valida sequência/posicao/efeito;
  4. prompt_builder.construir_prompt_por_tipo respeita o tipo_cena;
  5. media_manager.gerar_filename_padrao gera nome canônico.
"""

import unittest

from services.scene_schema import (
    CYCLE_TIPOS,
    nova_cena_ciclo,
    aplicar_campos_ciclo,
    validar_ciclo,
    eh_ciclo_valido,
)
from services.storyboard_builder import gerar_ciclos_narrativos, _aplicar_ciclo_narrativo
from services.prompt_builder_service import construir_prompt_por_tipo
from services.media_manager import gerar_filename_padrao


ROTEIRO = [
    {"topico": "cuidados com a rosa", "conteudo": "botões e folhas",
     "acao": "pulverizar água"},
    {"topico": "adubo de banana", "conteudo": "casca de banana",
     "acao": "cortar cascas"},
    {"topico": "solo", "conteudo": "terra solta", "acao": "afrouxar o solo"},
]


class TestCicloNarrativo(unittest.TestCase):

    def test_ciclo_gerado_multiplo_de_3_e_sequencia(self):
        cenas = gerar_ciclos_narrativos(86, ROTEIRO)
        self.assertEqual(len(cenas), 9)  # 3 blocos x 3 cenas
        self.assertEqual(len(cenas) % 3, 0, "Ciclo não é múltiplo de 3")
        for i, cena in enumerate(cenas):
            posicao_esperada = (i % 3) + 1
            self.assertEqual(cena["posicao_ciclo"], posicao_esperada,
                             f"Cena {i}: posição errada")
            self.assertEqual(cena["tipo_cena"], CYCLE_TIPOS[posicao_esperada - 1])

    def test_nunca_2_avatares_consecutivos(self):
        cenas = gerar_ciclos_narrativos(86, ROTEIRO)
        for i in range(len(cenas) - 1):
            if cenas[i]["tipo_cena"] == "avatar_intro":
                self.assertNotEqual(cenas[i + 1]["tipo_cena"], "avatar_intro",
                                    "Avatar consecutivo detectado")

    def test_posicao_ciclo_alterna_123(self):
        cenas = gerar_ciclos_narrativos(86, ROTEIRO)
        posicoes = [c["posicao_ciclo"] for c in cenas]
        self.assertEqual(posicoes, [1, 2, 3, 1, 2, 3, 1, 2, 3])

    def test_validar_ciclo_ok(self):
        cenas = gerar_ciclos_narrativos(86, ROTEIRO)
        self.assertTrue(eh_ciclo_valido(cenas))
        self.assertEqual(validar_ciclo(cenas), [])


    def test_validar_ciclo_detecta_avatar_consecutivo(self):
        cenas = gerar_ciclos_narrativos(86, ROTEIRO)
        cenas[0]["tipo_cena"] = "avatar_intro"
        cenas[1]["tipo_cena"] = "avatar_intro"
        cenas[1]["posicao_ciclo"] = 1
        erros = validar_ciclo(cenas)
        self.assertTrue(any("avatar_intro consecutivo" in e for e in erros))

    def test_validar_ciclo_detecta_sequencia_quebrada(self):
        cenas = gerar_ciclos_narrativos(86, ROTEIRO)
        cenas[2]["posicao_ciclo"] = 2  # i=2 espera posicao 3 -> quebra
        erros = validar_ciclo(cenas)
        self.assertTrue(any("quebra a sequência" in e for e in erros))

    def test_validar_ciclo_detecta_tipo_nulo(self):
        cenas = gerar_ciclos_narrativos(86, ROTEIRO)
        cenas[0]["tipo_cena"] = None
        erros = validar_ciclo(cenas)
        self.assertTrue(any("tipo_cena" in e for e in erros))

    def test_nova_cena_ciclo_campos(self):
        cena = nova_cena_ciclo(1, posicao_ciclo=2, ciclo_numero=1,
                               texto="macro", efeito="", duracao_planejada=0)
        self.assertEqual(cena["tipo_cena"], "imagem_zoom")
        self.assertEqual(cena["efeito"], "zoom_in")
        self.assertEqual(cena["posicao_ciclo"], 2)
        self.assertEqual(cena["duracao_planejada"], 12.0)
        self.assertEqual(cena["ciclo_numero"], 1)

    def test_construir_prompt_por_tipo_avatar(self):
        cena = {"tipo_cena": "avatar_intro", "narracao": "Olá jardineiros"}
        r = construir_prompt_por_tipo(cena, "avatar_intro")
        self.assertIn("@presenter", r["prompt_imagem"])
        self.assertIn("Olá jardineiros", r["prompt_imagem"])

    def test_construir_prompt_por_tipo_imagem(self):
        cena = {"tipo_cena": "imagem_zoom", "conteudo": "rosa em botão",
                "detalhe": "pétalas com orvalho"}
        r = construir_prompt_por_tipo(cena, "imagem_zoom")
        self.assertNotIn("@presenter", r["prompt_imagem"])
        self.assertIn("rosa em botão", r["prompt_imagem"])
        self.assertIn("zoom", r["prompt_imagem"].lower())

    def test_construir_prompt_por_tipo_video(self):
        cena = {"tipo_cena": "video_acao", "acao": "pulverizar"}
        r = construir_prompt_por_tipo(cena, "video_acao")
        self.assertNotIn("@presenter", r["prompt_imagem"])
        self.assertIn("hands", r["prompt_imagem"].lower())

    def test_filename_padrao(self):
        nome = gerar_filename_padrao(1, 0.0, 5.0, "png")
        self.assertEqual(nome, "01_[00-00-00-05].png")
        nome2 = gerar_filename_padrao(22, 100.0, 105.0, "mp4")
        self.assertEqual(nome2, "22_[01-40-01-45].mp4")


if __name__ == "__main__":
    unittest.main()


    def test_aplicar_campos_ciclo_existente(self):
        cena = {"id": 5, "scene_index": 5, "texto": "olá"}
        aplicar_campos_ciclo(cena, posicao_ciclo=3, ciclo_numero=2)
        self.assertEqual(cena["tipo_cena"], "video_acao")
        self.assertEqual(cena["efeito"], "fade")
        self.assertEqual(cena["posicao_ciclo"], 3)
        self.assertEqual(cena["ciclo_numero"], 2)

    def test_aplicar_ciclo_narrativo_preserva_contagem(self):
        scenes = [
            {"scene_id": 1, "duration_sec": 4.0, "text": "a", "media_type": "video"},
            {"scene_id": 2, "duration_sec": 4.0, "text": "b", "media_type": "photo"},
            {"scene_id": 3, "duration_sec": 4.0, "text": "c", "media_type": "video"},
        ]
        _aplicar_ciclo_narrativo(scenes)
        self.assertEqual(len(scenes), 3)
        self.assertEqual([s["posicao_ciclo"] for s in scenes], [1, 2, 3])
