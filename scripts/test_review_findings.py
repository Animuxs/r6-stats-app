"""Regression tests for what a review of the Players, recap, opponents and assistant work found: each test is one
finding, failing before its fix."""

from __future__ import annotations

import copy
import unittest
from datetime import datetime

import demo_season
import player_profile as pp
from ask_engine import Vocab, ask, opponent_names
from assistant import Conversation, answer_anything
from matchday_data import head_to_head
from sample_data import SAMPLE_MATCH
from stats_db import StatsDB

NOW = datetime(2026, 10, 10, 12)


def _with_teams(*pairs: list[str]) -> StatsDB:
    """The sample match once per pair of team names, a day apart, recorded by Fabian (team 0)."""
    db = StatsDB(":memory:")
    for i, names in enumerate(pairs):
        match = copy.deepcopy(SAMPLE_MATCH)
        match.update(match_id=f"t{i}", team_names=names, recording_player="Fabian",
                     played_at=f"2026-10-0{i + 1} 20:00:00")
        db.import_match(f"Match-{i}", match)
    return db


class TestDemo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = StatsDB(":memory:")
        demo_season.load(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def answer(self, question):
        return answer_anything(question, self.db, now=NOW)

    def test_best_against_is_the_easiest(self):
        self.assertIn("easiest opponent", ask("which opponent do we do best against", self.db).headline)
        self.assertIn("hardest opponent", ask("which opponent do we do worst against", self.db).headline)

    def test_the_match_on_screen_isnt_its_own_last_meeting(self):
        last = self.db.query("SELECT m.match_id, m.played_at FROM matches m WHERE m.team1 = 'Metro Tech' "
                             "ORDER BY m.played_at DESC LIMIT 1")[0]
        later = "2099-01-01 00:00:00"  # a replay's time a little after the saved one: still this match left out
        with_it = head_to_head(self.db, "Metro Tech", before=later)
        without = head_to_head(self.db, "Metro Tech", before=later, leave_out=last["match_id"])
        self.assertNotEqual(with_it, without)
        self.assertNotIn(f"last met {last['played_at'][:10]}", without)

    def test_several_last_matches_are_asks(self):
        self.assertNotIn("You last played", self.answer("did we win our last 5 matches?").text)
        self.assertNotIn("You last played", self.answer("did I win my last match on Bank?").text)
        self.assertIn("You last played", self.answer("did we win our last match").text)

    def test_him_after_a_comparison_is_the_other_player(self):
        chat = Conversation()
        chat.reply("compare me and Sacri", self.db, now=NOW)
        self.assertEqual(chat.reply("analyze him", self.db, now=NOW).resolved, "analyze Sacri")

    def test_tonight_isnt_always_a_recap(self):
        for question in ("what time do we play tonight?", "who are we playing tonight", "when is our next match"):
            with self.subTest(question):
                self.assertEqual(self.answer(question).text.splitlines()[0], "**Match day**")
        kills = self.answer("how many kills did I get last night")
        self.assertIn("kills", kills.text)
        self.assertNotIn("Top EPS", kills.text)
        self.assertIn("Top EPS", self.answer("how did I do last night").text)
        self.assertEqual(self.answer("is there a recap?").kind, "guide")
        self.assertIn("win most with", self.answer("who do we play best with").text)

    def test_situations_keep_the_question_s_filters(self):
        on_bank = self.answer("our retake % on Bank")
        self.assertTrue(on_bank.text.startswith("Over 2 matches on Bank: "), on_bank.text)
        self.assertTrue(self.answer("our retakes in the last 3 matches").text.startswith("Over your last 3 matches: "))
        self.assertNotEqual(on_bank.rows, self.answer("our retakes").rows)


class TestStandInNames(unittest.TestCase):
    def test_ranked_s_enemy_team_isnt_ranked_or_charted(self):
        db = _with_teams(["YOUR TEAM", "ENEMY TEAM"], ["Liquid", "Metro Tech "], ["Team A", "Team B"])
        self.assertEqual(opponent_names(db, "Fabian"), ["Metro Tech"])
        answer = ask("which opponent is the hardest", db, vocab=Vocab.from_db(db))
        self.assertEqual([r["Opponent"] for r in answer.rows], ["Metro Tech"])
        db.close()

    def test_a_name_saved_with_spaces_still_matches(self):
        db = _with_teams(["Liquid", "  Metro Tech "])
        answer = ask("my stats vs Metro Tech", db, vocab=Vocab.from_db(db))
        self.assertTrue(answer.ok)
        self.assertIn("1 match", answer.headline)
        db.close()

    def test_your_side_mid_sentence(self):
        db = _with_teams(["Liquid", "Spacestation"])  # no saved team: "your side"
        text = answer_anything("how often do we win when we plant", db, now=NOW).text
        self.assertNotIn(", Your side", text)
        self.assertIn("your side", text)
        db.close()


class TestLevelValues(unittest.TestCase):
    def test_values_that_look_the_same_are_level(self):
        self.assertIsNone(pp.leader("kost", 61.2, 61.4))  # both "61%"
        self.assertEqual(pp.leader("kost", 61.2, 62.4), 1)
        self.assertIsNone(pp.leader("kd", 1.204, 1.196))  # both "1.20"


if __name__ == "__main__":
    unittest.main()
