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
