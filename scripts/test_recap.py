"""Tests for recap.py: matches grouped into sessions by the time between them, and each session's recap."""

from __future__ import annotations

import copy
import unittest
from datetime import timedelta

import demo_season
import recap
from sample_data import SAMPLE_MATCH
from stats_db import StatsDB

# one night of three (the last after a 3h20 break: still the same session), another night, and a match
# without a time
TIMES = ["2026-10-10 19:00:00", "2026-10-10 20:10:00", "2026-10-10 23:30:00", "2026-10-12 19:00:00", None]


def _night() -> StatsDB:
    db = StatsDB(":memory:")
    for i, when in enumerate(TIMES):
        match = copy.deepcopy(SAMPLE_MATCH)
        match.update(match_id=f"m{i}", played_at=when, recording_player="Fabian")
        db.import_match(f"Match-{i}", match)
    return db


class TestSittings(unittest.TestCase):
    def setUp(self):
        self.db = _night()

    def tearDown(self):
        self.db.close()

    def test_grouped_by_the_breaks_between_them(self):
        found = recap.sittings(self.db)
        self.assertEqual([s.match_ids for s in found], [["m3"], ["m2", "m1", "m0"], ["m4"]])  # newest first
        self.assertEqual(found[1].label.split(" · ")[1:], ["3 matches", "19:00–23:30"])
        self.assertEqual(found[0].label.split(" · ")[1:], ["1 match", "19:00"])
        self.assertTrue(found[2].label.startswith("A match without a date"))

    def test_a_shorter_break_splits_the_night(self):
        found = recap.sittings(self.db, gap=timedelta(hours=2))
        self.assertEqual([s.match_ids for s in found][:3], [["m3"], ["m2"], ["m1", "m0"]])

    def test_nothing(self):
        self.assertEqual(recap.sittings(StatsDB(":memory:")), [])


class TestRecap(unittest.TestCase):
    def setUp(self):
        self.db = _night()
        self.night = recap.sittings(self.db)[1]

    def tearDown(self):
        self.db.close()

    def test_the_night(self):
        r = recap.recap(self.db, self.night, "Fabian")
        won = SAMPLE_MATCH["final_score"][0] > SAMPLE_MATCH["final_score"][1]  # Fabian is on team 0
        self.assertEqual(r.record, (3, 0, 0) if won else (0, 3, 0))
        self.assertEqual(len(r.matches), 3)
        self.assertEqual({m["Result"] for m in r.matches}, {"Won" if won else "Lost"})
        self.assertEqual(len(r.players), len(SAMPLE_MATCH["players"]))
        self.assertTrue(all(p["Matches"] == 3 for p in r.players))
        eps = [p["EPS"] for p in r.players]
        self.assertEqual(eps, sorted(eps, reverse=True))
        self.assertEqual(r.standout, r.players[0]["Player"])
        text = recap.headline(r)
        self.assertTrue(text.startswith("3 matches, "))
        self.assertIn("winning" if won else "losing", text)
        self.assertIn(f"Top EPS: {r.standout}", text)

    def test_someone_who_wasnt_there(self):
        r = recap.recap(self.db, self.night, "a stranger")
        self.assertEqual(r.record, (0, 0, 0))
        self.assertEqual({m["Result"] for m in r.matches}, {"—"})
        self.assertTrue(recap.headline(r).startswith("3 matches."))

    def test_one_match(self):
        r = recap.recap(self.db, recap.sittings(self.db)[0], "Fabian")
        self.assertRegex(recap.headline(r), r"^One match, (won|lost|drawn)\.")

    def test_a_draw_and_an_even_night(self):
        r = recap.Recap(self.night, [{}, {}], (1, 1, 0), [], None)
        self.assertEqual(recap.headline(r), "2 matches, 1–1: an even session.")
        r = recap.Recap(self.night, [{}, {}, {}], (1, 1, 1), [], None)
        self.assertEqual(recap.headline(r), "3 matches, 1–1–1: an even session.")


class TestDemoSeason(unittest.TestCase):
    def test_every_session_recaps(self):
        db = StatsDB(":memory:")
        demo_season.load(db)
        found = recap.sittings(db)
        self.assertEqual(sum(len(s.match_ids) for s in found), 12)
        for sitting in found:
            r = recap.recap(db, sitting, db.me())
            self.assertEqual(sum(r.record), len(sitting.match_ids))
            self.assertIsNotNone(r.standout)
        db.close()


class TestAssistant(unittest.TestCase):
    """"How did I do last night?" is the newest session, not the time of day."""

    def test_recap_questions(self):
        import assistant

        db = _night()
        for question in ("how did I do last night", "recap of last night", "my last session", "recap",
                         "how did tonight go", "show me a session recap"):
            with self.subTest(question):
                reply = assistant.answer_anything(question, db)
                self.assertTrue(reply.text.startswith("**Oct 12 · 1 match · 19:00**"), reply.text)
                self.assertEqual(reply.links, [("history", "Earlier sessions on Match History")])
                self.assertTrue(reply.rows)
        for question in ("do I play better at night", "what time do I play best"):
            with self.subTest(question):
                self.assertNotIn("match ·", assistant.answer_anything(question, db).text)
        self.assertEqual(assistant.answer_anything("where is the session recap", db).kind, "guide")
        db.close()


if __name__ == "__main__":
    unittest.main()
