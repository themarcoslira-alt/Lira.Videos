import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

"""Testes do Brand Profile Service (Fase 1)."""
from services.brand_profile_service import (
    BRAND_PROFILE_FILE,
    create_default_profile,
    get_music_path,
    get_presenter_reference,
    load_brand_profile,
    update_profile,
)


class TestBrandProfile(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_default_profile(self):
        p = create_default_profile(channel_name="Canal Teste", base_dir=self.base_dir)
        for k in (
            "channel_name", "presenter_name", "caption_style", "avatar_config",
            "video_mix_ratio", "quality_settings", "capcut_integration",
        ):
            self.assertIn(k, p)
        self.assertTrue((self.base_dir / BRAND_PROFILE_FILE).exists())
        self.assertEqual(p["channel_name"], "Canal Teste")

    def test_missing_file_created_on_load(self):
        p = load_brand_profile(base_dir=self.base_dir)
        self.assertTrue((self.base_dir / BRAND_PROFILE_FILE).exists())
        self.assertEqual(p["channel_name"], "Lira Jardinagem")
        self.assertEqual(p["presenter_name"], "Marcos")

    def test_update_and_save(self):
        self.assertTrue(update_profile("presenter_name", "Ana", base_dir=self.base_dir))
        self.assertTrue(update_profile("avatar_config.fps", 60, base_dir=self.base_dir))
        p = load_brand_profile(base_dir=self.base_dir)
        self.assertEqual(p["presenter_name"], "Ana")
        self.assertEqual(p["avatar_config"]["fps"], 60)

    def test_get_presenter_reference(self):
        p = create_default_profile("Lira", base_dir=self.base_dir)
        ref = get_presenter_reference("Marcos", base_dir=self.base_dir)
        self.assertEqual(ref, p["presenter_reference_path"])

    def test_get_music_path(self):
        create_default_profile("Lira", base_dir=self.base_dir)
        mp = get_music_path(base_dir=self.base_dir)
        self.assertTrue(mp.endswith("background_garden_upbeat.mp3"), mp)


if __name__ == "__main__":
    unittest.main()