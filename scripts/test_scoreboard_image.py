"""Tests for scoreboard_image.py: the scoreboard as a picture. Run from the repo root:
python -m unittest discover -s scripts"""

from __future__ import annotations

import copy
import io
import unittest
from unittest import mock

from PIL import Image

import scoreboard_image
from metrics_engine import compute_match_metrics, pro_league_rows
from sample_data import SAMPLE_MATCH
from scoreboard_image import CARD_WIDTH, player_card_png, scoreboard_png


def _rows(match: dict) -> list[dict]:
    return pro_league_rows(compute_match_metrics(match))


def _open(png: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(png))
    image.load()
    return image


class TestScoreboardImage(unittest.TestCase):

    def test_the_demo_match(self):
        image = _open(scoreboard_png(SAMPLE_MATCH, _rows(SAMPLE_MATCH), "#e8b04a", "Sep 25, 8:00 PM"))
        self.assertEqual(image.format, "PNG")
        self.assertGreaterEqual(image.width, 900)
        self.assertLess(image.width, 2000)
        self.assertGreater(image.height, 500)
        self.assertEqual(image.getpixel((5, 2))[:3], (0xE8, 0xB0, 0x4A))  # the theme's color along the top

    def test_a_bad_color_falls_back(self):
        for color in (None, "", "red", "#12345", "#gggggg", 7):
            with self.subTest(color=color):
                image = _open(scoreboard_png(SAMPLE_MATCH, _rows(SAMPLE_MATCH), color))
                self.assertEqual(image.getpixel((5, 2))[:3], (0x52, 0xD5, 0xF2))

    def test_odd_names_and_teams(self):
        match = copy.deepcopy(SAMPLE_MATCH)
        match["team_names"] = ["A" * 300, "Équipe 東京 🎮"]
        rows = _rows(match)
        rows[0]["Player"] = "W" * 200  # cut short rather than stretching the picture
        rows[1]["Player"] = "Ünïcødé_名前.TAG"
        short = _open(scoreboard_png(SAMPLE_MATCH, _rows(SAMPLE_MATCH)))
        image = _open(scoreboard_png(match, rows))
        self.assertLessEqual(image.width, short.width + scoreboard_image.MAX_NAME)

    def test_one_team_no_rounds_and_missing_fields(self):
        rows = [r for r in _rows(SAMPLE_MATCH) if r["Team"] == 0]
        one = _open(scoreboard_png(SAMPLE_MATCH, rows))
        both = _open(scoreboard_png(SAMPLE_MATCH, _rows(SAMPLE_MATCH)))
        self.assertLess(one.height, both.height)
        self.assertTrue(_open(scoreboard_png({}, [])))  # nothing at all still draws
        self.assertTrue(_open(scoreboard_png({"team_names": ["Only one"], "final_score": [1]}, rows)))

    def test_without_any_font_on_the_computer(self):
        scoreboard_image.font.cache_clear()
        self.addCleanup(scoreboard_image.font.cache_clear)
        with mock.patch.object(scoreboard_image, "_FONT_FOLDERS", []):
            self.assertTrue(_open(scoreboard_png(SAMPLE_MATCH, _rows(SAMPLE_MATCH))))

    def test_a_damaged_font_file_is_skipped(self):
        scoreboard_image.font.cache_clear()
        self.addCleanup(scoreboard_image.font.cache_clear)
        with mock.patch("PIL.ImageFont.truetype", side_effect=OSError("bad font")):
            self.assertTrue(scoreboard_image.font(17))
            self.assertTrue(_open(scoreboard_png(SAMPLE_MATCH, _rows(SAMPLE_MATCH))))  # in the bitmap font


class TestPlayerCard(unittest.TestCase):
    """A player's profile as a picture (Players' Player card)."""

    STATS = [("EPS", "105"), ("K/D", "1.13"), ("Kills per round", "0.71"), ("Headshot %", "41%"),
             ("KOST %", "68%"), ("Survival %", "37%"), ("Entry +/-", "+18"), ("Win %", "50%")]

    def test_a_profile(self):
        picture = Image.open(io.BytesIO(player_card_png("Fabian", "12 matches · 139 rounds", self.STATS,
                                                        [("Most kills", "12 kills")] * 5, "#ff0000")))
        self.assertEqual(picture.format, "PNG")
        self.assertEqual(picture.width, CARD_WIDTH)
        self.assertEqual(picture.getpixel((2, 2))[:3], (255, 0, 0))  # the accent bar, in the theme's color

    def test_odd_names_no_bests_and_a_bad_color(self):
        for name in ("🔥Blaze", "x" * 300, "Robert'); DROP TABLE players;--", ""):
            with self.subTest(name=name):
                png = player_card_png(name, "", self.STATS[:3], [], "not a color")
                self.assertEqual(Image.open(io.BytesIO(png)).width, CARD_WIDTH)


if __name__ == "__main__":
    unittest.main()
