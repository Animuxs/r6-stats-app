"""Opponents: the teams you've played against, by name. Ask filters by them ("my K/D vs Metro Tech"), ranks
them ("which opponent is the hardest?") and Charts keeps to them; a replay's stand-in names ("Team A",
"Blue") aren't opponents."""

from __future__ import annotations

import copy
import unittest

import chart_engine
import demo_season
from ask_engine import Vocab, ask, opponent_names
from sample_data import SAMPLE_MATCH
from stats_db import StatsDB


class TestDemoSeason(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = StatsDB(":memory:")
        demo_season.load(cls.db)
        cls.vocab = Vocab.from_db(cls.db)
        cls.me = cls.vocab.me
        # each opponent's matches and your wins against them, straight from the matches
        cls.record = {}
        for r in cls.db.query("SELECT m.team1 AS opponent, mp.won FROM match_players mp JOIN matches m "
                              "ON m.match_id = mp.match_id WHERE mp.player_key = ? AND mp.team = 0", [cls.me]):
            played, won = cls.record.get(r["opponent"], (0, 0))
            cls.record[r["opponent"]] = (played + 1, won + (r["won"] == 1))

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def ask(self, question):
        return ask(question, self.db, vocab=self.vocab)

    def test_the_opponents_are_the_other_teams(self):
        self.assertEqual(self.vocab.opponents, sorted(self.record, key=str.casefold))
        self.assertNotIn("Lakeside Varsity", self.vocab.opponents)  # your own side isn't one

    def test_filtered_to_one_opponent(self):
        played, won = self.record["Metro Tech"]
        answer = self.ask("my stats vs Metro Tech")
        self.assertTrue(answer.ok)
        self.assertIn(f"{played} matches, {won}–{played - won}", answer.headline)
        self.assertTrue(answer.headline.endswith("against Metro Tech."))
        self.assertIn("against Metro Tech", self.ask("my K/D against metro tech on attack").headline)

    def test_hardest_and_easiest(self):
        rates = {o: w / p for o, (p, w) in self.record.items() if p >= 2}
        hardest = min(rates.values())
        answer = self.ask("which opponent is the hardest")
        self.assertTrue(answer.headline.startswith("Your hardest opponent by win rate is "))
        self.assertIn(answer.headline.split(" is ")[1].split(":")[0], [o for o, r in rates.items() if r == hardest])
        self.assertEqual(answer.columns[0], "Opponent")
        self.assertTrue(self.ask("easiest opponent").headline.startswith("Your easiest opponent"))
        self.assertTrue(self.ask("toughest opponents").headline.startswith("Your hardest opponent"))

    def test_match_lists_say_who_it_was_against(self):
        answer = self.ask("my last 3 matches")
        self.assertIn("Opponent", answer.columns)
        last = self.ask("my last match")
        self.assertIn(f" against {last.rows[0]['Opponent']}, ", last.headline)

    def test_two_opponents_compared(self):
        answer = self.ask("compare Metro Tech and Harbor State")
        self.assertEqual({r["Opponent"] for r in answer.rows}, {"Metro Tech", "Harbor State"})
        self.assertTrue(answer.headline.startswith("You do better against "), answer.headline)

    def test_a_chart_keeps_to_the_opponent(self):
        spec = chart_engine.parse("my K/D over time against Metro Tech", self.vocab)
        self.assertEqual(spec.opponents, ["Metro Tech"])
        self.assertIn("against Metro Tech", spec.title(self.vocab))
        chart = chart_engine.build(spec, self.db, self.vocab)
        self.assertEqual(len(chart.rows), self.record["Metro Tech"][0])

    def test_a_chart_along_the_opponents(self):
        spec = chart_engine.parse("win rate by opponent", self.vocab)
        self.assertEqual((spec.kind, spec.x, spec.metric), ("bar", "opponent", "win_rate"))
        chart = chart_engine.build(spec, self.db, self.vocab)
        self.assertEqual({r["x"] for r in chart.rows}, set(self.record))
        self.assertEqual(chart_engine.parse("pie of matches against each opponent", self.vocab).x, "opponent")
        self.assertNotEqual(chart_engine.parse("top opponents by K/D", self.vocab).x, "opponent")  # their players


class TestOnlyOne(unittest.TestCase):
    def test_one_opponent_isnt_the_hardest(self):
        db = StatsDB(":memory:")
        db.import_match("one", {**SAMPLE_MATCH, "recording_player": "Fabian"})
        headline = ask("which opponent is the hardest", db).headline
        self.assertTrue(headline.startswith("Only one opponent so far: Spacestation Gaming, "), headline)
        self.assertNotIn("hardest", headline)
        db.close()


class TestNamelessTeams(unittest.TestCase):
    def test_stand_in_names_arent_opponents(self):
        db = StatsDB(":memory:")
        for i, names in enumerate((["Team A", "Team B"], ["Blue", "Orange"], ["YOUR TEAM", "ENEMY TEAM"],
                                   ["Liquid", "Spacestation"])):
            match = copy.deepcopy(SAMPLE_MATCH)
            match.update(match_id=f"n{i}", team_names=names, recording_player="Fabian",
                         played_at=f"2026-10-0{i + 1} 20:00:00")  # Ranked says YOUR/ENEMY TEAM
            db.import_match(f"Match-{i}", match)
        team = next(p["team"] for p in SAMPLE_MATCH["players"] if p["name"] == "Fabian")
        expected = ["Spacestation" if team == 0 else "Liquid"]
        self.assertEqual(opponent_names(db, "Fabian"), expected)
        self.assertEqual(opponent_names(db, None), [])
        # Ranked's YOUR TEAM vs ENEMY TEAM alone: no Opponent column of blanks
        ranked = StatsDB(":memory:")
        ranked.import_match("ranked", {**SAMPLE_MATCH, "team_names": ["YOUR TEAM", "ENEMY TEAM"]})
        self.assertNotIn("Opponent", ask("Fabian's last 3 matches", ranked).columns)
        ranked.close()
        # "opponents" on their own are still the other team's players
        answer = ask("top 5 opponents by K/D", db, vocab=Vocab.from_db(db))
        self.assertEqual(answer.columns[0], "Player")
        db.close()


if __name__ == "__main__":
    unittest.main()
