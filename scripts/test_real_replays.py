"""Real replays, when this computer has some: every match folder in R6_REAL_REPLAYS (or the example folder
C:/ExampleReplays/MatchReplay) is parsed, added to a throwaway stats database, and every page's engine runs on
it. Skipped where there are none (CI, most computers): the made-up seasons can't catch what real games do."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import parser as replay_parser
import player_profile
import recap
import scouting
from assistant import answer_anything
from ask_engine import Vocab
from metrics_engine import compute_match_metrics, pro_league_rows, round_rows
from stats_db import StatsDB

FOLDER = Path(os.environ.get("R6_REAL_REPLAYS", "C:/ExampleReplays/MatchReplay"))
QUESTIONS = ["my stats", "which opponent is the hardest", "my last 3 matches", "how did I do last night",
             "our retakes", "who should I bench", "my main", "best attack map", "how many rounds did we win",
             "analyze my team", "my goals", "chart my K/D over time"]


@unittest.skipUnless(FOLDER.is_dir() and replay_parser.r6_dissect_available(), "no real replays on this computer")
class TestRealReplays(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.TemporaryDirectory()
        cls.db = StatsDB(Path(cls.home.name) / "stats.db")
        cls.matches = []
        for folder in sorted(p for p in FOLDER.iterdir() if p.is_dir()):
            recs = sorted(str(r) for r in folder.glob("*.rec"))
            if recs:
                match, _raw, _warnings = replay_parser.parse_match(recs)
                cls.matches.append(match)
                cls.db.import_match(folder.name, match, files=len(recs))

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.home.cleanup()

    def test_every_match_adds_up(self):
        for match in self.matches:
            with self.subTest(match=match.get("match_id")):
                stats = compute_match_metrics(match)
                rows = pro_league_rows(stats)
                self.assertEqual(len(rows), len(match["players"]))
                self.assertEqual(len(round_rows(match, stats)), len(match["rounds"]))
                self.assertEqual(sum(match["final_score"]), len(match["rounds"]))

    def test_every_engine_runs(self):
        me = self.db.me()
        vocab = Vocab.from_db(self.db)
        self.assertTrue(player_profile.profile(self.db, me).matches)
        for sitting in recap.sittings(self.db):
            recap.recap(self.db, sitting, me)
        for opponent in vocab.opponents:
            self.assertTrue(scouting.scout(self.db, opponent).meetings, opponent)
        self.assertNotIn("ENEMY TEAM", [o.upper() for o in vocab.opponents])
        for question in QUESTIONS:
            with self.subTest(question):
                self.assertTrue(answer_anything(question, self.db).text)


if __name__ == "__main__":
    unittest.main()
