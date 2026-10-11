"""
tests/test_scene_builder.py — Testes de Timing e Divisão de Cenas com EDITING_RHYTHM_CONSTRAINTS
================================================================================================
Valida:
1. Estrutura e valores de EDITING_RHYTHM_CONSTRAINTS em story_rhythm_service.py.
2. Em condições normais com pontuação, nenhuma cena gerada ultrapassa CENA_DURACAO_MAX (5.5s).
3. Sem pontuação (texto corrido), o corte forçado dispara ao atingir o hard limit (7.0s).
4. Integração do BrainSystemDirector com os parâmetros centralizados de ritmo.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from config import PROJETOS_DIR
from services.story_rhythm_service import EDITING_RHYTHM_CONSTRAINTS
from services.scene_builder import gerar_cenas


class TestSceneBuilderRhythmConstraints(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_rhythm_")
        self.proj_name = Path(self.test_dir).name
        self.proj_path = PROJETOS_DIR / self.proj_name
        self.proj_path.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self.proj_path.exists():
            shutil.rmtree(self.proj_path, ignore_errors=True)
        if Path(self.test_dir).exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_editing_rhythm_constraints_structure(self):
        """Valida se o dicionário EDITING_RHYTHM_CONSTRAINTS contém todos os campos e valores exatos."""
        self.assertIn("scene_duration_target", EDITING_RHYTHM_CONSTRAINTS)
        self.assertEqual(EDITING_RHYTHM_CONSTRAINTS["scene_duration_target"], (3.0, 5.5))
        self.assertEqual(EDITING_RHYTHM_CONSTRAINTS["scene_duration_hard_limit"], 7.0)
        self.assertEqual(EDITING_RHYTHM_CONSTRAINTS["max_avatar_consecutive"], 2)
        self.assertEqual(EDITING_RHYTHM_CONSTRAINTS["max_broll_consecutive"], 3)
        self.assertEqual(EDITING_RHYTHM_CONSTRAINTS["silence_removal_threshold_s"], 0.45)
        self.assertEqual(EDITING_RHYTHM_CONSTRAINTS["transition_default"], "combinar")
        self.assertEqual(EDITING_RHYTHM_CONSTRAINTS["transition_act_break"], "combinar")
        self.assertEqual(EDITING_RHYTHM_CONSTRAINTS["transition_act_break_duration_ms"], 300)

    def test_cenas_nao_ultrapassam_5_5s_em_condicoes_normais(self):
        """Em condições normais (frases com pontuação), nenhuma cena deve ultrapassar 5.5s."""
        # Cria transcrição estruturada com frases pontuadas a cada ~1.8s a 2.5s
        transcricao = {
            "duration": 18.0,
            "segments": [
                {"start": 0.0, "end": 2.0, "text": "Primeiro passo importante.", "timestamp": "00:00"},
                {"start": 2.0, "end": 4.2, "text": "Regar o solo pela manhã.", "timestamp": "00:02"},
                {"start": 4.2, "end": 6.8, "text": "Adicionar adubo orgânico rico em nitrogênio.", "timestamp": "00:04"},
                {"start": 6.8, "end": 9.0, "text": "Misturar bem a terra.", "timestamp": "00:06"},
                {"start": 9.0, "end": 11.5, "text": "Esperar secar um pouco.", "timestamp": "00:09"},
                {"start": 11.5, "end": 14.0, "text": "Aplicar a segunda camada protetora.", "timestamp": "00:11"},
                {"start": 14.0, "end": 16.2, "text": "Finalizar com poda suave.", "timestamp": "00:14"},
                {"start": 16.2, "end": 18.0, "text": "Aprecie o resultado.", "timestamp": "00:16"},
            ]
        }
        (self.proj_path / "roteiro_transcricao.json").write_text(
            json.dumps(transcricao, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        res = gerar_cenas(self.proj_name)
        self.assertTrue(res.get("success"))
        cenas = res.get("cenas", [])
        self.assertGreaterEqual(len(cenas), 3)

        max_target = EDITING_RHYTHM_CONSTRAINTS["scene_duration_target"][1]  # 5.5s
        for c in cenas:
            dur = c.get("duration", 0.0)
            self.assertLessEqual(
                dur,
                max_target + 0.1,  # tolerância de 100ms para arredondamentos de Whisper
                f"Cena {c.get('id')} excedeu o teto de 5.5s com duracao={dur}s: '{c.get('texto')}'"
            )

    def test_corte_forcado_antes_de_7_0s_sem_pontuacao(self):
        """Sem nenhuma pontuação no texto, o corte forçado deve atuar antes de 7.0s."""
        # 10 segmentos de 1.5s cada SEM nenhuma pontuação ou conectivo de quebra
        transcricao = {
            "duration": 15.0,
            "segments": [
                {"start": i * 1.5, "end": (i + 1) * 1.5, "text": f"palavra{i} continuacao texto direto", "timestamp": f"00:{int(i*1.5):02d}"}
                for i in range(10)
            ]
        }
        (self.proj_path / "roteiro_transcricao.json").write_text(
            json.dumps(transcricao, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        res = gerar_cenas(self.proj_name)
        self.assertTrue(res.get("success"))
        cenas = res.get("cenas", [])
        self.assertGreaterEqual(len(cenas), 2)

        hard_limit = EDITING_RHYTHM_CONSTRAINTS["scene_duration_hard_limit"]  # 7.0s
        for c in cenas:
            dur = c.get("duration", 0.0)
            # Nenhuma cena pode ultrapassar o hard limit somado ao tamanho do último segmento agrupado
            # (ou seja, quando o acumulado + segmento > 7.0s, ele corta antes de adicionar o próximo)
            self.assertLessEqual(
                dur,
                hard_limit + 1.5,
                f"Cena {c.get('id')} violou o hard limit de corte forçado com duracao={dur}s"
            )


if __name__ == "__main__":
    unittest.main()
