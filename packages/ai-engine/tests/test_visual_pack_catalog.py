from __future__ import annotations

from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from src.engine import visual_pack_catalog


class TestVisualPackCatalog(TestCase):
    def tearDown(self) -> None:
        visual_pack_catalog.get_visual_pack_catalog.cache_clear()

    def test_get_visual_pack_catalog_falls_back_to_defaults_for_shallow_runtime_layout(self) -> None:
        fake_runtime_file = Path("/app/src/engine/visual_pack_catalog.py")
        with patch.object(visual_pack_catalog, "__file__", str(fake_runtime_file)):
            visual_pack_catalog.get_visual_pack_catalog.cache_clear()
            path = visual_pack_catalog._catalog_path()
            self.assertTrue(str(path).endswith("visual-pack-catalog.json"))
            catalog = visual_pack_catalog.get_visual_pack_catalog()
        self.assertGreaterEqual(len(catalog), 1)
        self.assertEqual(catalog[0]["id"], visual_pack_catalog.DEFAULT_VISUAL_PACKS[0]["id"])

    def test_select_visual_pack_prefers_clean_edu_for_stem_classroom_puzzle_showcase(self) -> None:
        pack = visual_pack_catalog.select_visual_pack(
            game_type="puzzle",
            theme="k12 physics circuit education, classroom learning scenario",
            generation_tier="showcase",
            variation_seed="circuit-classroom",
        )
        self.assertEqual(pack["id"], "clean_edu")

    def test_select_visual_pack_keeps_retro_terminal_for_neon_terminal_themes(self) -> None:
        pack = visual_pack_catalog.select_visual_pack(
            game_type="puzzle",
            theme="retro neon terminal hacker puzzle in a cyber city",
            generation_tier="showcase",
            variation_seed="retro-terminal",
        )
        self.assertEqual(pack["id"], "retro_terminal")
