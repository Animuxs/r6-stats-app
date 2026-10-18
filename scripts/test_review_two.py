"""Regression tests for what a second review (of #55-#61) found: each test is one finding."""

from __future__ import annotations

import copy
import unittest
from datetime import date, timedelta
from unittest import mock

import demo_season
import scouting
from ask_engine import ask
from assistant import answer_anything
from matchday_data import MatchdayWorkspace, fixture_when, head_to_head
from sample_data import SAMPLE_MATCH
from stats_db import StatsDB


class TestQuestions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = StatsDB(":memory:")
        demo_season.load(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def answer(self, question):
        return answer_anything(question, self.db).text

    def test_main_map_is_a_map(self):
        self.assertIn("most played map", self.answer("what's our main map"))
        self.assertIn("most played operator", self.answer("my main"))

    def test_how_often_is_a_rate(self):
        self.assertIn("win rate is", self.answer("how often do we win"))
        self.assertIn("6 wins over 12 matches", self.answer("how many matches did we win"))

    def test_per_round_stats_dont_make_win_rate_per_round(self):
        self.assertEqual(ask("my win rate and kills per round", self.db).query.level, "match")
        self.assertEqual(ask("how many rounds did we win on Border", self.db).query.level, "round")

    def test_an_operator_pick_isnt_a_map_veto(self):
        self.assertTrue(self.answer("what should I pick on attack").startswith("Your best operator on attack"))
        self.assertIn("best operator", self.answer("what do i pick on Bank"))
        self.assertRegex(self.answer("what should we ban"), r"^Ban \*\*")

    def test_your_stats_against_a_team_arent_a_scouting_report(self):
        self.assertTrue(self.answer("tell me about my stats against Metro Tech").startswith("You: "))
        self.assertTrue(self.answer("tell me about Metro Tech").startswith("2 meetings with Metro Tech"))


class TestMeetings(unittest.TestCase):
    def _db(self, *names_and_scores):
        db = StatsDB(":memory:")
        for i, (names, score) in enumerate(names_and_scores):
            match = copy.deepcopy(SAMPLE_MATCH)
            match.update(match_id=f"m{i}", team_names=names, final_score=score, recording_player="Fabian",
                         played_at=f"2026-10-0{i + 1} 20:00:00")
            db.import_match(f"M{i}", match)
        return db

    def test_accents_and_spacing(self):
        db = self._db((["Liquid", "ÉQUIPE  Nord"], [7, 3]))
        self.assertEqual(len(scouting.scout(db, "équipe nord").meetings), 1)
        self.assertTrue(head_to_head(db, "Équipe Nord").startswith("Your record: 1–0"))
        db.close()

    def test_draws_counted_the_same_everywhere(self):
        db = self._db((["Liquid", "Metro Tech"], [7, 3]), (["Liquid", "Metro Tech"], [6, 6]))
        report = scouting.scout(db, "Metro Tech")
        wins, losses, draws = report.record
        self.assertEqual(draws, 1)
        self.assertTrue(head_to_head(db, "Metro Tech").startswith(f"Your record: {wins}–{losses}–{draws}"))
        db.close()


class TestSchedule(unittest.TestCase):
    def test_one_date_format(self):
        fixture = {"when": "2026-10-19T19:00:00", "timezone": "Central Time"}
        self.assertEqual(fixture_when(fixture), "Mon, Oct 19 · 19:00 · Central Time")
        self.assertEqual(fixture_when(fixture, zone=False), "Mon, Oct 19 · 19:00")

    def test_the_dock_survives_unreadable_planning(self):
        import dock

        class Broken:
            store = {}

            def get(self, key, default=None):
                return self.store.get(key, default)

            def set(self, key, value):
                self.store[key] = value

            @property
            def matchday(self):
                raise ValueError("bad planning data")

        with mock.patch.object(dock, "Session", Broken):
            self.assertEqual(dock.schedule().fixtures, [])
            Broken.store.clear()

    def test_the_dock_reads_the_schedule_once_a_minute(self):
        import dock

        reads = []
        future = {"id": "a", "opponent": "X", "when": f"{date.today() + timedelta(days=1)}T19:00:00",
                  "timezone": "", "kind": "", "team": ""}

        class Counting:
            store = {}

            def get(self, key, default=None):
                return self.store.get(key, default)

            def set(self, key, value):
                self.store[key] = value

            @property
            def matchday(self):
                reads.append(1)
                return MatchdayWorkspace([future])

        with mock.patch.object(dock, "Session", Counting):
            dock.schedule()
            dock.schedule()
        self.assertEqual(len(reads), 1)


if __name__ == "__main__":
    unittest.main()
