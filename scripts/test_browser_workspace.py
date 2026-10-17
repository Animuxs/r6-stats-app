"""The portable browser workspace must use the desktop's confirmed-player semantics."""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "browser"))
import bridge
import demo_season


class TestBrowserWorkspace(unittest.TestCase):
    def setUp(self):
        key, match = next(iter(demo_season.season().items()))
        self.entry = {"key": key, "data": copy.deepcopy(match),
                      "players": demo_season.ROSTER[:2], "seasons": ["Fall", "Practice"]}
        self.state = {"matches": [self.entry], "teams": {"Test team": demo_season.ROSTER[:2]}}

    def run_engine(self, action="overview", **args):
        return json.loads(bridge.workspace(json.dumps(self.state), action, json.dumps(args)))

    def test_only_confirmed_players_are_imported(self):
        result = self.run_engine()
        self.assertEqual(result["summary"]["matches"], 1)
        self.assertEqual(set(result["players"]), set(self.entry["players"]))
        self.entry["players"] = []
        self.assertEqual(self.run_engine()["summary"]["matches"], 0)

    def test_duplicate_confirmation_does_not_double_count(self):
        original = self.run_engine("operators", player=self.entry["players"][0])
        self.state["matches"].append(copy.deepcopy(self.entry))
        self.assertEqual(self.run_engine("operators", player=self.entry["players"][0]), original)

    def test_match_can_belong_to_independent_seasons(self):
        fall = self.run_engine("season", season="Fall")
        practice = self.run_engine("season", season="Practice")
        self.assertGreater(fall["rounds_logged"], 0)
        self.assertEqual([{k: v for k, v in p.items() if k != "season"} for p in fall["players"]],
                         [{k: v for k, v in p.items() if k != "season"} for p in practice["players"]])
        self.assertEqual(self.run_engine("season", season="Other")["rounds_logged"], 0)

    def test_demo_never_logs_to_seasons(self):
        self.state["demo"] = True
        self.assertEqual(self.run_engine("season", season="Fall")["rounds_logged"], 0)

    def test_removing_a_match_removes_its_analytics(self):
        self.state["matches"].clear()
        self.assertEqual(self.run_engine()["summary"]["matches"], 0)
        self.assertEqual(self.run_engine("operators", player=self.entry["players"][0]), [])
        self.assertEqual(self.run_engine("season", season="Fall")["rounds_logged"], 0)

    def test_reports_reopen_with_round_deaths(self):
        result = json.loads(bridge.normalized(json.dumps(self.entry["data"])))
        for player in result["player_details"].values():
            self.assertTrue(all("deaths" in r for r in player["rounds"]))

    def test_team_and_ask_use_saved_roster(self):
        result = self.run_engine("team", team="Test team", minimum=1)
        self.assertEqual(result["team"], "Test team")
        self.assertEqual({p["name"] for p in result["players"]}, set(self.entry["players"]))
        self.assertTrue(self.run_engine("ask", question="Top 5 players by EPS")["ok"])

    def test_built_and_season_players_have_identical_detailed_columns(self):
        built = self.run_engine("team", team="Test team", minimum=1)["table"]
        season = self.run_engine("season", season="Fall")["tables"]["Test team"]
        required = ["Player", "Matches", "EPS", "All-time EPS", "KD (+/-)", "Entry", "KOST", "KPR",
                    "HS", "SRV", "Clutches", "Multikills", "Objectives", "Dead for trade kill", "Trade kills"]
        self.assertEqual(list(built[0])[:len(required)], required)
        self.assertEqual(list(built[0]), list(season[0]))
        for row in season:
            counterpart = next(r for r in built if r["Player"] == row["Player"])
            self.assertEqual({k: str(v) for k, v in row.items()}, {k: str(v) for k, v in counterpart.items()})

    def test_deleted_season_team_does_not_delete_built_team_or_other_seasons(self):
        self.state["removedSeasonTeams"] = {"Fall": ["Test team"]}
        self.assertEqual(self.run_engine("season", season="Fall")["tables"], {})
        self.assertTrue(self.run_engine("season", season="Practice")["tables"]["Test team"])
        self.assertTrue(self.run_engine("team", team="Test team", minimum=1)["table"])

    def test_deleted_built_team_keeps_independent_season_roster(self):
        self.state["seasonTeams"] = copy.deepcopy(self.state["teams"])
        self.state["teams"].clear()
        self.assertTrue(self.run_engine("season", season="Fall")["tables"]["Test team"])

    def test_restored_season_team_stays_empty_until_matches_are_picked(self):
        self.state["seasonTeamMatches"] = {"Fall": {"Test team": []}}
        season = self.run_engine("season", season="Fall")
        self.assertIn("Test team", season["rosters"])
        self.assertEqual(season["rounds_logged"], 0)
        self.assertGreater(self.run_engine("season", season="Practice")["rounds_logged"], 0)
        self.state["seasonTeamMatches"]["Fall"]["Test team"] = [self.entry["data"]["match_id"]]
        self.assertGreater(self.run_engine("season", season="Fall")["rounds_logged"], 0)

    def test_profile_perspective_is_not_replay_ownership(self):
        original = self.entry["data"].get("recorder")
        self.state["viewer"] = self.entry["players"][1]
        self.assertEqual(self.run_engine()["me"].casefold(), self.entry["players"][1].casefold())
        self.assertEqual(self.entry["data"].get("recorder"), original)
        self.state["demo"] = True
        self.assertNotEqual(self.run_engine()["me"], self.entry["players"][1])
