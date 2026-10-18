"""Tests for scouting.py: the report on a team you've played, and its section on Match day."""

from __future__ import annotations

import copy
import unittest

import demo_season
import scouting
from sample_data import SAMPLE_MATCH
from stats_db import StatsDB


class TestDemoSeason(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = StatsDB(":memory:")
        demo_season.load(cls.db)
        cls.games = cls.db.query("SELECT m.map, mp.won FROM match_players mp JOIN matches m ON m.match_id = mp.match_id "
                                 "WHERE mp.player_key = ? AND m.team1 = 'Metro Tech' ORDER BY m.played_at DESC",
                                 [cls.db.me().casefold()])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_the_report(self):
        report = scouting.scout(self.db, "  metro TECH ")  # however a fixture spells it
        self.assertEqual(report.opponent, "Metro Tech")  # as the replays do
        wins = sum(g["won"] == 1 for g in self.games)
        self.assertEqual(report.record, (wins, len(self.games) - wins, 0))
        self.assertEqual(len(report.meetings), len(self.games))
        self.assertEqual(report.meetings[0]["Map"], self.games[0]["map"])  # newest first
        self.assertEqual(sum(m["Matches"] for m in report.maps), len(self.games))
        self.assertTrue(report.headline.startswith(f"{len(self.games)} meetings with Metro Tech: "))
        eps = [p["EPS"] for p in report.ours]
        self.assertEqual(eps, sorted(eps, reverse=True))
        self.assertIn(self.db.me(), [p["Player"] for p in report.ours])
        self.assertEqual(report.theirs, [])  # the demo season keeps only Lakeside Varsity's players

    def test_a_team_never_played(self):
        report = scouting.scout(self.db, "Nobody FC")
        self.assertEqual((report.meetings, report.maps, report.ours, report.theirs), ([], [], [], []))
        self.assertEqual(report.headline, "You haven't played Nobody FC in the matches in your stats.")
        self.assertEqual(scouting.scout(self.db, " ").meetings, [])

    def test_the_assistant_scouts(self):
        from assistant import answer_anything

        reply = answer_anything("scout Metro Tech", self.db)
        self.assertEqual(reply.text, scouting.scout(self.db, "Metro Tech").headline)
        self.assertEqual(reply.links, [("matchday", "Their full scouting report on Match day")])
        self.assertEqual(answer_anything("how do I scout a team", self.db).kind, "guide")


class TestBothSides(unittest.TestCase):
    def test_their_players_and_an_undated_meeting(self):
        db = StatsDB(":memory:")
        match = copy.deepcopy(SAMPLE_MATCH)
        match.update(recording_player="Fabian", played_at=None)
        db.import_match("x", match)  # every player of both teams
        report = scouting.scout(db, "spacestation gaming")
        theirs = {p["name"] for p in SAMPLE_MATCH["players"] if p["team"] == 1}
        self.assertEqual({p["Player"] for p in report.theirs}, theirs)
        self.assertNotIn("Fabian", {p["Player"] for p in report.theirs})
        self.assertIn("Last: ", report.headline)  # no date to give
        db.close()

    def test_ranked_enemy_team_isnt_a_team(self):
        db = StatsDB(":memory:")
        db.import_match("r", {**SAMPLE_MATCH, "team_names": ["YOUR TEAM", "ENEMY TEAM"], "recording_player": "Fabian"})
        self.assertEqual(scouting.scout(db, "ENEMY TEAM").meetings, [])
        db.close()


if __name__ == "__main__":
    unittest.main()
