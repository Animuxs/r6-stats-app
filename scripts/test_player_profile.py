"""Tests for player_profile.py: a player's profile and two players compared, on the demo season and
on small made-up databases where the right answer is easy to see."""

from __future__ import annotations

import unittest

import demo_season
import player_profile as pp
from ask_engine import Vocab, ask
from sample_data import SAMPLE_MATCH
from stats_db import StatsDB


class TestDemoSeason(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = StatsDB(":memory:")
        demo_season.load(cls.db)
        cls.me = cls.db.me()
        cls.mate = cls.db.teammates(cls.me, 1)[0][0]

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_profile_matches_ask(self):
        """The Players tab and Ask give the same numbers for the same player."""
        p = pp.profile(self.db, self.me)
        self.assertEqual(p.name, self.me)
        self.assertEqual(p.matches, 12)
        answer = ask("my K/D", self.db, vocab=Vocab.from_db(self.db))
        self.assertIn(p.show("kd"), answer.headline)

    def test_profile_parts(self):
        p = pp.profile(self.db, self.me.upper())  # any spelling
        self.assertEqual(len(p.form), pp.FORM_MATCHES)
        self.assertEqual(set(p.form[0]), {"Played", "Map", "Result", "K-D-A", "K/D", "EPS", "match_id"})
        self.assertTrue(p.maps and all(m["Matches"] >= pp.MIN_MAP_MATCHES for m in p.maps))
        wins = [m["Win %"] for m in p.maps]
        self.assertEqual(wins, sorted(wins, reverse=True))
        self.assertLessEqual(len(p.operators), 5)
        rounds = [o["Rounds"] for o in p.operators]
        self.assertEqual(rounds, sorted(rounds, reverse=True))
        self.assertEqual(set(p.sides), {"attack", "defense"})
        self.assertEqual(sum(s["rounds"] for s in p.sides.values()), p.stats["rounds"])
        self.assertIn(pp.form_trend(p.form), ("up", "down", "steady"))

    def test_compare_teammates(self):
        a, b = pp.profile(self.db, self.me), pp.profile(self.db, self.mate)
        rows = pp.comparison(a, b)
        self.assertEqual([r["key"] for r in rows], pp.COMPARE)
        lead_a, lead_b = pp.score(rows)
        self.assertLessEqual(lead_a + lead_b, len(rows) - 2)  # matches and rounds aren't "ahead"
        self.assertIn(a.name if lead_a > lead_b else b.name, pp.verdict(a, b, rows))
        both = pp.shared(self.db, self.me, self.mate)
        self.assertEqual(len(both.together), 12)
        self.assertEqual(both.against, [])
        self.assertEqual(sum(both.together_record), 12)
        self.assertIsNotNone(both.together_win_rate)

    def test_unknown_player(self):
        p = pp.profile(self.db, "nobody at all")
        self.assertEqual(p.matches, 0)
        self.assertEqual((p.form, p.maps, p.operators, p.sides), ([], [], [], {}))
        self.assertEqual(p.show("kd"), "—")
        rows = pp.comparison(p, pp.profile(self.db, self.me))
        self.assertEqual(pp.score(rows), (0, 0))
        self.assertIn("no matches", pp.verdict(p, pp.profile(self.db, self.me), rows))


class TestRules(unittest.TestCase):
    def test_leader(self):
        self.assertEqual(pp.leader("kd", 1.2, 1.0), 0)
        self.assertEqual(pp.leader("kd", 0.9, 1.0), 1)
        self.assertEqual(pp.leader("dpr", 0.5, 0.7), 0)  # fewer deaths per round is better
        self.assertIsNone(pp.leader("kd", 1.001, 1.004))  # the same as shown
        self.assertIsNone(pp.leader("kd", None, 1.0))

    def test_form_trend(self):
        rows = lambda *kds: [{"K/D": k} for k in kds]  # noqa: E731
        self.assertIsNone(pp.form_trend(rows(1, 2, 3)))
        self.assertEqual(pp.form_trend(rows(2, 2, 1, 1)), "up")
        self.assertEqual(pp.form_trend(rows(1, 1, 2, 2)), "down")
        self.assertEqual(pp.form_trend(rows(1, 1, 1, 1)), "steady")

    def test_verdict_wording(self):
        a = pp.Profile("a", "A", {"matches": 1})
        b = pp.Profile("b", "B", {"matches": 5})
        tie = [{"leader": 0}, {"leader": 1}]
        self.assertIn("Dead even", pp.verdict(a, b, tie))
        ahead = [{"leader": 1}, {"leader": 1}, {"leader": 0}]
        text = pp.verdict(a, b, ahead)
        self.assertTrue(text.startswith("B leads 2"))
        self.assertIn("few matches", text)


class TestAgainst(unittest.TestCase):
    """Two players on opposite teams: their head-to-head record."""

    def test_against(self):
        db = StatsDB(":memory:")
        db.import_match("sample", SAMPLE_MATCH)  # everyone, both teams
        opponents = db.query("SELECT a.player_key AS a, b.player_key AS b FROM match_players a JOIN match_players b "
                             "ON a.match_id = b.match_id AND a.team = 0 AND b.team = 1 LIMIT 1")[0]
        both = pp.shared(db, opponents["a"], opponents["b"])
        self.assertEqual(both.together, [])
        self.assertTrue(both.against)
        self.assertEqual(len(both.against), 1)
        self.assertEqual(sum(both.against_record), 1)  # one of them won it
        db.close()


class TestStreaks(unittest.TestCase):
    def test_streaks(self):
        run = pp.streaks([1, 1, 0, 0, 0, 1])  # newest first
        self.assertEqual((run.current, run.current_won, run.best_win, run.best_loss, run.matches), (2, 1, 2, 3, 6))
        run = pp.streaks([None, 1, 1, 1])  # a draw ends any streak
        self.assertEqual((run.current, run.current_won, run.best_win), (0, None, 3))
        self.assertEqual(pp.streaks([0]).best_loss, 1)


class TestBests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = StatsDB(":memory:")
        demo_season.load(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_most_kills_is_the_most(self):
        me = self.db.me().casefold()
        best = {b["label"]: b for b in pp.bests(self.db, me)}
        top = self.db.query("SELECT MAX(kills) AS k FROM match_players WHERE player_key = ? AND rounds >= 5", (me,))[0]
        self.assertEqual(best["Most kills"]["value"], f"{top['k']} kills")
        self.assertTrue(best["Most kills"]["when"] and best["Most kills"]["map"])
        self.assertIn("Best EPS", best)

    def test_nothing_isnt_a_best(self):
        db = StatsDB(":memory:")
        db.import_match("sample", SAMPLE_MATCH)
        for key in db.players():
            for best in pp.bests(db, key):
                self.assertFalse(best["value"].startswith("0 "), best)
                self.assertNotIn(" 1 kills", " " + best["value"])
        self.assertEqual(pp.bests(db, "nobody"), [])
        db.close()

    def test_profile_has_them(self):
        p = pp.profile(self.db, self.db.me())
        self.assertTrue(p.bests)
        self.assertEqual(p.streak.matches, 12)
        self.assertIsNone(pp.profile(self.db, "nobody").streak)


class TestLinkFromAnswers(unittest.TestCase):
    """Asking to compare two players links to Players with both of them picked."""

    def test_two_players_link_to_players(self):
        import assistant

        db = StatsDB(":memory:")
        demo_season.load(db)
        me, mate = db.me(), db.teammates(db.me(), 1)[0][0]
        reply = assistant.answer_anything(f"compare me and {mate}", db)
        self.assertEqual(reply.links, [("players", "See them side by side on Players",
                                        {"player": me.casefold(), "vs": mate.casefold()})])
        self.assertEqual(assistant.answer_anything("my K/D", db).links, [])
        # a summary of one player links to their profile
        self.assertEqual(assistant.answer_anything(f"tell me about {mate}", db).links,
                         [("players", "Full profile on Players", {"player": mate.casefold()})])
        self.assertEqual(assistant.answer_anything("how is our team doing", db).links, [])
        db.close()


class TestAgainstOpponents(unittest.TestCase):
    def test_each_opponent(self):
        db = StatsDB(":memory:")
        demo_season.load(db)
        rows = pp.against(db, db.me().casefold())
        self.assertEqual({r["Opponent"] for r in rows}, set(Vocab.from_db(db).opponents))
        self.assertEqual(sum(r["Matches"] for r in rows), 12)
        for r in rows:
            wins, losses = map(int, r["Record"].split("–"))
            self.assertEqual(wins + losses, r["Matches"])
        self.assertEqual(pp.profile(db, db.me()).opponents, rows)
        db.close()

    def test_stand_in_names_are_left_out(self):
        db = StatsDB(":memory:")
        db.import_match("sample", {**SAMPLE_MATCH, "team_names": ["Team A", "Team B"]})
        self.assertEqual(pp.against(db, next(iter(db.players()))), [])
        db.close()


class TestGoals(unittest.TestCase):
    def setUp(self):
        self.db = StatsDB(":memory:")
        demo_season.load(self.db)

    def tearDown(self):
        self.db.close()

    def test_set_measure_and_clear(self):
        self.assertEqual(pp.goal_progress(self.db, "Fabian"), [])
        self.db.save_goal("Fabian", "kd", 99)  # out of reach
        self.db.save_goal("FABIAN", "dpr", 5)  # deaths per round: lower is better, so met
        goals = {g["key"]: g for g in pp.goal_progress(self.db, "fabian")}
        recent = pp.stats_for(self.db, "fabian", [r["match_id"] for r in pp.form(self.db, "fabian", pp.GOAL_MATCHES)])
        self.assertAlmostEqual(goals["kd"]["value"], recent["kd"])
        self.assertFalse(goals["kd"]["met"])
        self.assertLess(goals["kd"]["progress"], 0.1)
        self.assertTrue(goals["dpr"]["met"])
        self.assertEqual(goals["dpr"]["progress"], 1.0)
        self.db.save_goal("Fabian", "kd", None)
        self.assertEqual(set(self.db.goals("fabian")), {"dpr"})
        self.db.save_goal("Fabian", "dpr", None)
        self.assertEqual(self.db.goals("fabian"), {})

    def test_a_damaged_setting_is_no_goals(self):
        with self.db._conn:
            self.db._conn.execute("INSERT OR REPLACE INTO stats_meta VALUES ('goals', 'not json')")
        self.assertEqual(self.db.goals("fabian"), {})
        self.db.save_goal("Fabian", "kd", 1.0)  # and saving starts over
        self.assertEqual(self.db.goals("fabian"), {"kd": 1.0})

    def test_the_assistant_reports_them(self):
        import assistant

        self.assertIn("no goals yet", assistant.answer_anything("my goals", self.db).text)
        self.db.save_goal("Fabian", "kd", 99)
        reply = assistant.answer_anything("how am I doing on my goals", self.db)
        self.assertTrue(reply.text.startswith("You have met 0 of 1 goal"))
        self.assertEqual(reply.links, [("players", "Set goals on Players", {"player": "fabian"})])
        self.assertEqual(assistant.answer_anything("how do I set a goal", self.db).kind, "guide")


if __name__ == "__main__":
    unittest.main()
