"""Tests for season_stats.StatsManager.  Run:  python -m unittest test_season_stats -v"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import parser as replay_parser
from metrics_engine import compute_match_metrics, round_eps
from sample_data import SAMPLE_MATCH, TEAM0, TEAM1
from season_stats import COUNTER_FIELDS, RoundResult, StatsError, StatsManager

LIQUID, SSG = SAMPLE_MATCH["team_names"]


class StatsManagerTest(unittest.TestCase):
    def setUp(self):
        self.sm = StatsManager(":memory:", season="Y10S3")
        self.match = copy.deepcopy(SAMPLE_MATCH)

    def tearDown(self):
        self.sm.close()

    def test_only_tracked_players_are_saved(self):
        self.sm.add_players(["Fabian", "Bosco"])
        res = self.sm.log_match(self.match)
        self.assertEqual(res.players_logged, {"Fabian", "Bosco"})
        self.assertEqual(res.untracked_players, set(TEAM0 + TEAM1) - {"Fabian", "Bosco"})
        self.assertEqual({p.username for p in self.sm.all_player_stats()}, {"Fabian", "Bosco"})
        self.assertIsNone(self.sm.get_player_stats("Kanto"))

    def test_deleting_season_team_preserves_other_team_and_season(self):
        self.sm.add_players(TEAM0, team="Squad")
        self.sm.add_players(TEAM1, team="Rivals")
        self.sm.log_match(self.match)
        self.sm.season = "Other"
        self.sm.log_match(self.match)
        self.sm.season = "Y10S3"
        self.sm.delete_team("Squad")
        self.assertNotIn("Squad", self.sm.tracked_teams())
        self.assertIsNone(self.sm.get_team_stats("Squad"))
        self.assertIsNotNone(self.sm.get_team_stats("Rivals"))
        self.sm.log_match(self.match)
        self.assertIsNone(self.sm.get_team_stats("Squad"))
        self.sm.season = "Other"
        self.assertIsNotNone(self.sm.get_team_stats("Squad"))
        self.sm.season = "Y10S3"
        self.sm.add_players(TEAM0, team="Squad")
        self.assertIn("Squad", self.sm.tracked_teams())
        self.assertIsNone(self.sm.get_team_stats("Squad"))
        self.sm.log_match(self.match)
        self.assertIsNotNone(self.sm.get_team_stats("Squad"))

    def test_totals_match_metrics_engine(self):
        self.sm.add_players(TEAM0 + TEAM1)
        self.sm.log_match(self.match)
        engine = compute_match_metrics(self.match)
        for name, ps in engine.items():
            s = self.sm.get_player_stats(name)
            self.assertEqual(s.totals["kills"], ps.kills, name)
            self.assertEqual(s.totals["deaths"], ps.deaths, name)
            self.assertEqual(s.totals["entry_kills"], ps.entry_kills, name)
            self.assertEqual(s.totals["entry_deaths"], ps.entry_deaths, name)
            self.assertEqual(s.totals["trades"], ps.trades, name)
            self.assertEqual(s.totals["rounds_played"], ps.rounds_played, name)
            self.assertEqual(s.totals["kost_rounds"], ps.kost_rounds, name)
            self.assertEqual(s.clutches_won, ps.total_clutches, name)
            self.assertAlmostEqual(s.kost_pct, ps.kost_pct, places=6)
            self.assertAlmostEqual(s.kd, ps.kd, places=6)
            self.assertEqual(s.entry_diff, ps.entry_kills - ps.entry_deaths)

    def test_relogging_never_double_counts(self):
        self.sm.add_players(TEAM0)
        first = self.sm.log_match(self.match)
        before = self.sm.export_json()["players"]
        second = self.sm.log_match(self.match)
        self.assertEqual(second.rounds_logged, 0)
        self.assertEqual(second.rounds_skipped_duplicate, first.rounds_logged)
        self.assertEqual(self.sm.export_json()["players"], before)

    def test_single_round_then_full_match_does_not_double_count(self):
        self.sm.add_player("Fabian")
        one_round = dict(self.match, rounds=self.match["rounds"][:1])
        self.sm.log_match(one_round)
        self.sm.log_match(self.match)
        self.assertEqual(self.sm.get_player_stats("Fabian").totals["rounds_played"], 9)

    def test_player_added_later_is_backfilled_without_double_counting_others(self):
        self.sm.add_player("Fabian")
        self.sm.log_match(self.match)
        fabian = self.sm.get_player_stats("Fabian").totals
        self.sm.add_player("Kanto")
        res = self.sm.log_match(self.match)
        self.assertEqual(res.players_logged, {"Kanto"})
        self.assertEqual(self.sm.get_player_stats("Fabian").totals, fabian)
        self.assertEqual(self.sm.get_player_stats("Kanto").totals["rounds_played"], 9)

    def test_tracking_is_case_insensitive_and_uses_tracked_spelling(self):
        self.sm.add_player("fabian")
        self.sm.log_match(self.match)
        self.assertEqual(self.sm.get_player_stats("FABIAN").username, "fabian")

    def test_team_from_match_or_pinned(self):
        self.sm.add_player("Fabian")
        self.sm.add_player("Bosco", team="SSG Academy")
        self.sm.log_match(self.match)
        self.assertEqual(self.sm.get_player_stats("Fabian").team, LIQUID)
        self.assertEqual(self.sm.get_player_stats("Bosco").team, "SSG Academy")

    def test_generic_team_names_are_not_used(self):
        self.sm.add_player("Fabian")
        self.sm.add_player("Bosco", team="Spacestation")
        res = self.sm.log_match(dict(self.match, team_names=["YOUR TEAM", "ENEMY TEAM"]))
        self.assertIsNone(self.sm.get_player_stats("Fabian").team)
        self.assertEqual(self.sm.get_player_stats("Bosco").team, "Spacestation")
        self.assertEqual(len(res.warnings), 1)
        self.assertIn("Fabian", res.warnings[0])
        self.assertEqual(self.sm.teams(), ["Spacestation"])

    def test_team_aggregates(self):
        self.sm.add_players(TEAM0 + TEAM1)
        self.sm.log_match(self.match)
        team = self.sm.get_team_stats(LIQUID)
        members = [self.sm.get_player_stats(n) for n in TEAM0]
        kills = sum(m.totals["kills"] for m in members)
        deaths = sum(m.totals["deaths"] for m in members)
        self.assertEqual(sorted(team.players), sorted(TEAM0))
        self.assertAlmostEqual(team.kd, kills / deaths)
        self.assertEqual(team.entry_diff, sum(m.entry_diff for m in members))
        self.assertAlmostEqual(team.kost_avg, sum(m.kost_pct for m in members) / 5)
        won = sum(m.clutches_won for m in members)
        tried = sum(m.clutch_attempts for m in members)
        self.assertGreater(tried, 0)
        self.assertLessEqual(won, tried)
        self.assertAlmostEqual(team.clutch_success_rate, won / tried)
        self.assertEqual(sorted(self.sm.teams()), sorted([LIQUID, SSG]))

    def test_eps_is_kept_per_match_and_rounds_weighted(self):
        self.sm.add_players(TEAM0)
        self.sm.log_match(self.match)
        stats = compute_match_metrics(self.match)
        self.assertEqual(self.sm.get_player_stats("fabian").eps, stats["Fabian"].eps)  # any case
        rounds = sum(stats[n].rounds_played for n in TEAM0)
        want = round_eps(100 * sum(stats[n].rating * stats[n].rounds_played for n in TEAM0) / rounds)
        self.assertEqual(self.sm.get_team_stats(LIQUID).eps, want)
        self.sm.log_match(copy.deepcopy(self.match))  # the same match again changes nothing
        self.assertEqual(self.sm.get_player_stats("Fabian").rated_rounds, stats["Fabian"].rounds_played)
        self.assertEqual(self.sm.export_json()["players"][0]["eps"], self.sm.all_player_stats()[0].eps)
        self.sm.reset_season()
        self.sm.log_round("m", 1, {"Fabian": RoundResult(kills=1)})  # rounds without a match EPS
        self.assertIsNone(self.sm.get_player_stats("Fabian").eps)

    def test_accumulates_across_matches(self):
        self.sm.add_player("Fabian")
        self.sm.log_match(self.match)
        one = self.sm.get_player_stats("Fabian").totals
        self.sm.log_match(copy.deepcopy(self.match), match_id="demo-match-0002")
        two = self.sm.get_player_stats("Fabian").totals
        self.assertEqual(two, {k: 2 * v for k, v in one.items()})

    def test_missing_match_id_is_rejected(self):
        self.sm.add_player("Fabian")
        with self.assertRaises(StatsError):
            self.sm.log_match(dict(self.match, match_id="unknown"))

    def test_log_round_low_level(self):
        self.sm.add_player("Solo")
        rr = RoundResult(kills=2, headshots=1, entry_kill=True, planted=True, clutch_won=2, clutch_attempt=2)
        self.assertEqual(self.sm.log_round("m1", 1, {"Solo": rr}, {"Solo": "Team X"}).rounds_logged, 1)
        self.assertEqual(self.sm.log_round("m1", 1, {"Solo": rr}).rounds_skipped_duplicate, 1)
        s = self.sm.get_player_stats("Solo")
        self.assertEqual((s.totals["clutch_1v2"], s.clutch_attempts, s.kost_pct, s.team), (1, 1, 100.0, "Team X"))
        self.assertTrue(self.sm.is_round_logged("m1", 1, "Solo"))

    def test_match_history_summarizes_logged_rounds(self):
        self.sm.add_players(["Fabian", "Kanto"])
        self.sm.log_match(self.match)
        history = self.sm.match_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["match_id"], self.match["match_id"])
        self.assertEqual(history[0]["rounds"], 9)
        self.assertEqual(history[0]["players"], 2)

    def test_rebuild_totals_reproduces_totals(self):
        self.sm.add_players(TEAM0 + TEAM1)
        self.sm.log_match(self.match)
        before = self.sm.export_json()["players"]
        self.sm.rebuild_totals()
        self.assertEqual(self.sm.export_json()["players"], before)

    def test_a_match_taken_out_of_the_season_is_as_if_never_saved(self):
        practice = copy.deepcopy(self.match)
        practice["match_id"] = "practice"
        self.sm.add_players(TEAM0, team="Liquid")
        self.sm.log_match(self.match)  # Game Day
        self.sm.log_match(practice)
        self.assertEqual(self.sm.remove_match("practice"), len(TEAM0) * len(self.match["rounds"]))
        only_game_day = StatsManager(":memory:", season="Y10S3")
        self.addCleanup(only_game_day.close)
        only_game_day.add_players(TEAM0, team="Liquid")
        only_game_day.log_match(self.match)
        self.assertEqual({p.username: (p.totals, p.rating) for p in self.sm.all_player_stats()},
                         {p.username: (p.totals, p.rating) for p in only_game_day.all_player_stats()})
        self.assertEqual([m["match_id"] for m in self.sm.match_history()], [self.match["match_id"]])
        self.assertEqual(self.sm.remove_match("not saved"), 0)

    def test_tracked_teams_and_their_matches(self):
        self.sm.add_players(TEAM0[:2], team="Liquid")
        self.sm.add_players(["Bosco"], team="SSG")
        self.sm.add_player("Nyx")  # no team pinned
        self.assertEqual(self.sm.tracked_teams(), {"Liquid": TEAM0[:2], "SSG": ["Bosco"]})  # before any match
        self.sm.log_match(self.match)
        self.assertEqual(len(self.sm.match_history(["fabian"])), 1)  # any case
        self.assertEqual(self.sm.match_history(["Nobody"]), [])
        self.assertEqual(self.sm.match_history([]), [])

    def test_reset_season_only_affects_that_season(self):
        self.sm.add_player("Fabian")
        self.sm.log_match(self.match)
        other = StatsManager.__new__(StatsManager)  # same connection, different season
        other.__dict__.update(self.sm.__dict__, season="Y10S4")
        other.log_match(self.match)
        self.sm.reset_season()
        self.assertIsNone(self.sm.get_player_stats("Fabian"))
        self.assertIsNotNone(other.get_player_stats("Fabian"))
        self.assertIn("Fabian", self.sm.tracked_players())  # tracked list survives a reset
        self.assertFalse(self.sm.is_round_logged("demo-match-0001", 1))

    def test_remove_player(self):
        self.sm.add_players(["Fabian", "Kanto"])
        self.sm.log_match(self.match)
        self.sm.remove_player("Fabian")  # keeps stats, stops tracking
        self.assertNotIn("Fabian", self.sm.tracked_players())
        self.assertIsNotNone(self.sm.get_player_stats("Fabian"))
        self.sm.remove_player("Kanto", delete_stats=True)
        self.assertIsNone(self.sm.get_player_stats("Kanto"))

    def test_export_json_and_persistence(self):
        with tempfile.TemporaryDirectory() as td:
            db, out = Path(td) / "s.db", Path(td) / "season.json"
            with StatsManager(db, season="Y10S3") as sm:
                sm.add_players(TEAM0)
                sm.log_match(self.match)
                data = sm.export_json(out)
            self.assertEqual(json.loads(out.read_text()), data)
            self.assertEqual(data["rounds_logged"], 9)
            self.assertEqual({p["username"] for p in data["players"]}, set(TEAM0))
            self.assertEqual([t["team"] for t in data["teams"]], [LIQUID])
            with StatsManager(db, season="Y10S3") as reopened:  # survives a restart
                self.assertEqual(reopened.export_json()["players"], data["players"])
                self.assertEqual(reopened.log_match(self.match).rounds_logged, 0)

    def test_counters_cover_every_field(self):
        self.assertEqual(set(RoundResult().counters()), set(COUNTER_FIELDS))


@unittest.skipUnless(replay_parser.r6_dissect_available(), "r6-dissect binary not built")
class RealReplayTest(unittest.TestCase):
    REPLAY = Path(__file__).resolve().parent.parent / "dissect/test/data/replays/valid/Y9S1/custom_1.rec"

    def test_log_real_replay(self):
        match, _raw, _warnings = replay_parser.parse_match([str(self.REPLAY)])
        names = [p["name"] for p in match["players"]]
        with StatsManager(":memory:") as sm:
            sm.add_players(names)
            res = sm.log_match(match)
            self.assertEqual(res.players_logged, set(names))
            self.assertEqual(sm.log_match(match).rounds_logged, 0)
            self.assertEqual(len(sm.teams()), 2)


if __name__ == "__main__":
    unittest.main()
