"""Everyday questions that got the wrong answer when asked about real replays: each test is one of them."""

from __future__ import annotations

import unittest

import demo_season
from ask_engine import Vocab, ask
from assistant import answer_anything
from stats_db import StatsDB, nice_time


class TestEverydayQuestions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = StatsDB(":memory:")
        demo_season.load(cls.db)
        cls.vocab = Vocab.from_db(cls.db)
        cls.mine = [m for m in cls.db.match_list(cls.vocab.me) if m.get("player")]  # newest first

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def answer(self, question):
        return answer_anything(question, self.db).text

    def test_when_did_we_last_play_a_map(self):  # was: the last match, on any map
        newest = self.mine[0]
        other = next(m for m in self.mine if m["map"] != newest["map"])
        reply = self.answer(f"when did we last play {other['map']}?")
        self.assertIn(nice_time(other["played_at"]), reply)
        self.assertIn(f"on {other['map']}", reply)

    def test_what_map_did_we_play_last(self):  # was: your best map
        self.assertIn(f"on {self.mine[0]['map']}", self.answer("what map did we play last?"))

    def test_show_my_losses_lists_them(self):  # was: how many
        answer = ask("show my losses", self.db, vocab=self.vocab)
        losses = [m for m in self.mine if m["won"] == 0]
        self.assertEqual(answer.query.kind, "matches")
        self.assertEqual(len(answer.rows), len(losses))
        self.assertTrue(answer.headline.startswith(f"Your {len(losses)} loss"), answer.headline)
        self.assertEqual(ask("how many losses do I have", self.db, vocab=self.vocab).query.kind, "count")

    def test_how_many_headshots_is_a_count(self):  # was: the headshot rate
        answer = ask("how many headshots did I get?", self.db, vocab=self.vocab)
        self.assertEqual(answer.query.metrics, ["headshots"])
        total = self.db.query("SELECT SUM(headshots) AS n FROM match_players WHERE player_key = ?",
                              [self.vocab.me])[0]["n"]
        self.assertEqual(answer.rows[0]["Headshots"], total)
        self.assertEqual(ask("my headshot rate", self.db, vocab=self.vocab).query.metrics, ["hs"])

    def test_the_ace_is_a_five_kill_round(self):  # was: the operator Ace
        for question in ("who got the ace?", "did anyone get an ace?"):
            query = ask(question, self.db, vocab=self.vocab).query
            self.assertEqual((query.metrics, query.operators), (["aces"], []), question)
        self.assertEqual(ask("my K/D as Ace", self.db, vocab=self.vocab).query.operators, ["Ace"])

    def test_what_operators_does_someone_play(self):  # was: their best operator by win rate
        self.assertIn("most played operator", self.answer("what operators do I play?"))


if __name__ == "__main__":
    unittest.main()
