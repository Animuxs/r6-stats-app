"""Tests for rosters.py. Run from the repo root:  python -m unittest discover -s scripts"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import rosters
import season_stats


def team(name: str, players: list[tuple[str, list[str], bool]], division: str = "I", **extra) -> dict:
    return {"id": f"r-{name}", "team_id": f"t-{name}", "name": name, "school": f"{name} University",
            "school_short": name, "division": division, "event_id": f"e{division}", "group": "", "clan_tag": "",
            "color": "#123456", "color_alt": "#abcdef", "logo_url": f"https://images.leagueos.gg/teams/{name}/x",
            "record": {"wins": 1, "losses": 2, "game_wins": 3, "game_losses": 4},
            "players": [{"name": n, "ubisoft": u, "player": p} for n, u, p in players], **extra}


A = team("Alpha", [("Ace", ["Ace.A"], True), ("Bee", ["Bee.A", "OldBee"], True), ("Cee", [], True),
                   ("Coach", ["Coach.A"], False)])
B = team("Bravo", [("Dee", ["Dee.B"], True), ("Eff", ["Eff.B"], True), ("Gee", ["Gee.B"], True)])


class TestReleaseSnapshot(unittest.TestCase):
    def test_every_team_is_whole(self):
        snapshot = rosters.load(rosters.BUNDLED)
        self.assertIsNotNone(snapshot)
        self.assertGreater(len(snapshot["teams"]), 100)
        self.assertEqual({t["division"] for t in snapshot["teams"]}, {"I", "II", "III", "IV", "V", "VI"})
        for t in snapshot["teams"]:
            self.assertTrue(t["name"] and t["logo_url"].startswith("https://images.leagueos.gg/"), t["name"])
            self.assertRegex(t["color"], r"^#[0-9a-fA-F]{6}$")
        self.assertGreater(sum(len(rosters.in_game_names(t)) for t in snapshot["teams"]), 700)

    def test_only_what_tracking_needs(self):
        raw = rosters.BUNDLED.read_text(encoding="utf-8")
        self.assertNotIn("discord", raw.casefold())  # nor any other linked account
        keys = {k for t in json.loads(raw)["teams"] for p in t["players"] for k in p}
        self.assertEqual(keys, {"name", "ubisoft", "player"})


class TestCleaning(unittest.TestCase):
    def test_a_snapshot_is_only_data(self):
        self.assertIsNone(rosters.clean_snapshot([]))
        self.assertIsNone(rosters.clean_snapshot({"teams": [A]}))  # no date
        self.assertIsNone(rosters.clean_snapshot({"teams": [], "captured_at": "2026-10-16"}))
        bad = dict(copy.deepcopy(A), color="red;}</style>", color_alt="#zzzzzz", logo_url="http://x/logo.png",
                   name="<b>Alpha</b>", players=[{"name": "Ace", "ubisoft": ["Ace.A", 7, ""], "player": True}, "x"])
        cleaned = rosters.clean_snapshot({"teams": [bad, {"name": ""}, "no"], "captured_at": "2026-10-16"})
        t = cleaned["teams"][0]
        self.assertEqual(len(cleaned["teams"]), 1)
        self.assertEqual((t["color"], t["color_alt"], t["logo_url"]), ("", "", ""))  # never in a style or src
        self.assertEqual(t["name"], "<b>Alpha</b>")  # kept as text: the pages escape it
        self.assertEqual(t["players"], [{"name": "Ace", "ubisoft": ["Ace.A"], "player": True}])


class TestTeams(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(rosters, "teams", return_value=[A, B, team("Alpha", [], "II")])
        patch.start()
        self.addCleanup(patch.stop)

    def test_names_to_track_and_every_name_they_go_by(self):
        self.assertEqual(rosters.in_game_names(A), ["Ace.A", "Bee.A", "Cee"])  # staff left out
        self.assertEqual(rosters.in_game_names(A, staff=True), ["Ace.A", "Bee.A", "Cee", "Coach.A"])
        names = rosters.aliases(A)
        self.assertEqual(names["oldbee"], "Bee.A")  # an older account is the same player
        self.assertEqual(names["bee"], "Bee.A")
        self.assertEqual(names["coach.a"], "Coach.A")  # a coach who fills in is still recognized
        self.assertEqual(rosters.on_roster(["ACE.A", "stranger", "oldbee"], names), ["ACE.A", "oldbee"])

    def test_find_and_search(self):
        self.assertEqual(rosters.find("alpha")["division"], "I")
        self.assertEqual(rosters.find("Alpha", "II")["division"], "II")
        self.assertIsNone(rosters.find("Alpha", "VI"))
        self.assertEqual([t["name"] for t in rosters.search("bra")], ["Bravo"])
        self.assertEqual([t["name"] for t in rosters.search("Eff.B")], ["Bravo"])  # by a player
        self.assertEqual(rosters.search("  "), [])
        self.assertEqual(rosters.mine({"source": "necc", "team": "Alpha", "id": "necc:eII"})["division"], "II")
        self.assertIsNone(rosters.mine({"source": "custom", "team": "Alpha"}))

    def test_naming_the_sides_of_a_match(self):
        players = [{"name": n, "team": 0} for n in ("Ace.A", "OldBee", "rando1")]
        players += [{"name": n, "team": 1} for n in ("Dee.B", "Gee.B", "rando2")]
        found = rosters.identify({"players": players})
        self.assertEqual({side: t["name"] for side, t in found.items()}, {0: "Alpha", 1: "Bravo"})
        lone = [{"name": "Ace.A", "team": 0}, {"name": "x", "team": 1}]
        self.assertEqual(rosters.identify({"players": lone}), {})  # one player isn't a team

    def test_a_teams_look(self):
        self.assertEqual(rosters.theme_of(A), {"name": "Alpha University", "team": "Alpha", "primary": "#123456",
                                               "secondary": "#abcdef", "logo": A["logo_url"]})


class TestDailyUpdate(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        patch = mock.patch.object(season_stats, "DEFAULT_DB_PATH", self.folder / "season_stats.db")
        patch.start()
        self.addCleanup(patch.stop)

    @staticmethod
    def published(day: str) -> bytes:
        return json.dumps({"captured_at": day, "teams": [A, B]}).encode()

    def test_a_newer_snapshot_is_kept_and_asked_for_once_a_day(self):
        now = datetime(2026, 11, 1, 12)
        self.assertTrue(rosters.update(fetch=lambda: self.published("2099-01-01"), now=now))
        self.assertEqual(rosters.captured_at(), "2099-01-01")
        self.assertEqual([t["name"] for t in rosters.teams()], ["Alpha", "Bravo"])
        never = mock.Mock(side_effect=AssertionError("asked again within a day"))
        self.assertFalse(rosters.update(fetch=never, now=now + timedelta(hours=23)))
        self.assertFalse(rosters.update(fetch=lambda: self.published("2098-01-01"), now=now + timedelta(days=2)))
        self.assertEqual(rosters.captured_at(), "2099-01-01")  # an older one isn't kept

    def test_offline_or_broken_never_breaks_the_app(self):
        def offline():
            raise OSError("no network")

        now = datetime(2026, 11, 1, 12)
        self.assertFalse(rosters.update(fetch=offline, now=now))
        self.assertFalse(rosters.update(fetch=lambda: b"{not json", now=now + timedelta(days=1)))
        self.assertFalse(rosters.update(fetch=lambda: b" " * (rosters.MAX_BYTES + 1), now=now + timedelta(days=2)))
        self.assertEqual(rosters.captured_at(), rosters.load(rosters.BUNDLED)["captured_at"])
        rosters.cache_path().write_text("{broken", encoding="utf-8")  # a damaged download: the release's is used
        self.assertEqual(rosters.captured_at(), rosters.load(rosters.BUNDLED)["captured_at"])


class TestFromReplays(unittest.TestCase):
    def test_your_custom_games_teammates(self):
        def match(players, kind="Custom game", recorder="Me"):
            return ({"match_type": kind, "recording_player": recorder,
                     "players": [{"name": n, "team": t} for n, t in players]}, {}, [])

        games = {
            "Match-2026-10-05_20-00-00-1": match([("Me", 0), ("Mate1", 0), ("Mate2", 0), ("Foe", 1)]),
            "Match-2026-10-04_20-00-00-1": match([("Me", 1), ("Mate1", 1), ("Sub", 1), ("Foe", 0)]),
            "Match-2026-10-03_20-00-00-1": match([("Me", 0), ("Rando", 0)], kind="Ranked"),  # not your team's
            "Match-2026-10-02_20-00-00-1": Exception("unreadable"),
            "Match-2026-10-01_20-00-00-1": match([("Mate2", 0), ("Other", 1)]),  # you weren't in it
        }
        found = rosters.from_replays({n: [] for n in games}, lambda n: games[n], "me")
        self.assertEqual(found[:2], [("Me", 2, 2), ("Mate1", 2, 2)])
        self.assertEqual(dict((n, c) for n, c, _ in found), {"Me": 2, "Mate1": 2, "Mate2": 1, "Sub": 1})
        self.assertEqual(rosters.from_replays({}, lambda n: None, "me"), [])


if __name__ == "__main__":
    unittest.main()
