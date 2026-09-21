import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

"""Testes do Channel Profile Service (perfil EDITORIAL do canal)."""
from services.channel_profile_service import (
    CHANNEL_PROFILE_FILE,
    _CAMPOS_OBRIGATORIOS,
    create_default_channel_profile,
    get_audience,
    get_content_patterns,
    get_editorial_profile,
    get_flow_step,
    get_hook_structure,
    get_narration_minutes_range,
    get_narration_rhythm,
    get_niche,
    get_retention_techniques,
    get_script_flow,
    get_word_count_examples,
    get_word_count_target,
    get_writing_style,
    load_channel_profile,
    save_channel_profile,
    update_channel_profile,
)


class TestChannelProfile(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_default_profile(self):
        p = create_default_channel_profile(channel_name="Canal Teste", base_dir=self.base_dir)
        for k in _CAMPOS_OBRIGATORIOS:
            self.assertIn(k, p)
        self.assertTrue((self.base_dir / CHANNEL_PROFILE_FILE).exists())
        self.assertEqual(p["channel_name"], "Canal Teste")

    def test_missing_file_created_on_load(self):
        p = load_channel_profile(base_dir=self.base_dir)
        self.assertTrue((self.base_dir / CHANNEL_PROFILE_FILE).exists())
        self.assertEqual(p["channel_name"], "Lira Jardinagem")
        self.assertEqual(p["word_count"]["average"], 5487)
        self.assertEqual(p["hook"]["window_seconds"], 30)

    def test_update_and_save_aninhado(self):
        self.assertTrue(update_channel_profile("hook.window_seconds", 45, base_dir=self.base_dir))
        self.assertTrue(update_channel_profile("word_count.average", 5000, base_dir=self.base_dir))
        p = load_channel_profile(base_dir=self.base_dir)
        self.assertEqual(p["hook"]["window_seconds"], 45)
        self.assertEqual(p["word_count"]["average"], 5000)

    def test_completude_retroativa(self):
        """Arquivo parcial antigo ganha os campos obrigatórios no load."""
        save_channel_profile({"channel_name": "Antigo"}, base_dir=self.base_dir)
        p = load_channel_profile(base_dir=self.base_dir)
        for k in _CAMPOS_OBRIGATORIOS:
            self.assertIn(k, p)
        self.assertEqual(p["channel_name"], "Antigo")

    def test_script_flow_tem_8_blocos_na_ordem(self):
        flow = get_script_flow(base_dir=self.base_dir)
        self.assertEqual(len(flow), 8)
        self.assertEqual([f["step"] for f in flow], list(range(1, 9)))
        self.assertEqual(flow[0]["id"], "hook")
        self.assertEqual(flow[7]["id"], "recapitulacao_cta")
        self.assertEqual(flow[7]["cta_cascade"],
                         ["like", "comente", "compartilhe", "inscreva-se"])
        numerados = [f["id"] for f in flow if f.get("numbered")]
        self.assertEqual(numerados, ["mitos", "passos", "erros_comuns"])

    def test_get_flow_step(self):
        self.assertEqual(get_flow_step(5, base_dir=self.base_dir)["cue"], "Step 1, Step 2...")
        self.assertEqual(get_flow_step(99, base_dir=self.base_dir), {})

    def test_getters_editoriais(self):
        self.assertIn("mito vs. verdade", get_niche(base_dir=self.base_dir)["focus"])
        self.assertIn("30-60", get_audience(base_dir=self.base_dir)["profile"])
        self.assertIn("professor confiável", get_writing_style(base_dir=self.base_dir)["tone"])
        self.assertTrue(get_hook_structure(base_dir=self.base_dir)["subscribe_cta_after_hook"])
        self.assertEqual(get_word_count_target(base_dir=self.base_dir), 5487)
        self.assertEqual(get_word_count_examples(base_dir=self.base_dir),
                         {"gypsum": 6384, "rosemary": 4591})
        self.assertEqual(get_narration_minutes_range(base_dir=self.base_dir), [18, 22])
        self.assertEqual(len(get_retention_techniques(base_dir=self.base_dir)), 6)
        ids_ret = {t["id"] for t in get_retention_techniques(base_dir=self.base_dir)}
        self.assertIn("cta_comentario_especifico", ids_ret)
        self.assertIn("3-6 palavras", get_narration_rhythm(base_dir=self.base_dir)["cadence"])
        self.assertEqual(get_content_patterns(base_dir=self.base_dir)["monetization"]["lead_magnet"],
                         "ebook")

    def test_editorial_profile_exclui_channel_name(self):
        ed = get_editorial_profile(base_dir=self.base_dir)
        self.assertNotIn("channel_name", ed)
        self.assertEqual(set(ed.keys()), set(_CAMPOS_OBRIGATORIOS) - {"channel_name"})


if __name__ == "__main__":
    unittest.main()
