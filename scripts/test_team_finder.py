"""Finding a team's matches in the replays: every match with enough of the roster on one side that
your stats are missing is found, summed up for a person to recognise, and added with the team's players."""

from __future__ import annotations

import copy
import unittest

import team_finder
from sample_data import SAMPLE_MATCH, TEAM0, TEAM1
from stats_db import StatsDB
from sources import ReplaySource


def match(match_id, team_names=("YOUR TEAM", "ENEMY TEAM"), swap=False):
    m = copy.deepcopy(SAMPLE_MATCH)
    m["match_id"] = match_id
    m["team_names"] = list(team_names)
    if swap:  # the team on side 1
        for p in m["players"]:
            p["team"] = 1 - p["team"]
        m["final_score"] = m["final_score"][::-1]
    return m


class FakeSource(ReplaySource):
    def __init__(self, matches):
        self._matches = matches
        self.opened = []

    def matches(self):
        return list(self._matches)

    def open(self, name):
        self.opened.append(name)
        found = self._matches[name]
        return found if isinstance(found, Exception) else (found, {}, [])


class TestFind(unittest.TestCase):
    def setUp(self):
        self.db = StatsDB(":memory:")
        self.addCleanup(self.db.close)
        self.source = FakeSource({
            "Match-2026-09-22_20-00-00-1": match("m3", ("YOUR TEAM", "Ridgeview")),
            "Match-2026-09-21_20-00-00-1": match("m2"),
            "Match-2026-09-20_20-00-00-1": match("m1"),
            "Match-2026-09-19_20-00-00-1": ValueError("unreadable"),
        })

    def test_every_match_with_enough_of_the_team_is_found_newest_first(self):
        found = team_finder.find(self.source, self.db, TEAM0, need=3)
        self.assertEqual([f.source for f in found], ["Match-2026-09-22_20-00-00-1", "Match-2026-09-21_20-00-00-1",
                                                     "Match-2026-09-20_20-00-00-1"])
        self.assertTrue(all(f.new for f in found))
        self.assertEqual(sorted(found[0].keep), sorted(TEAM0))

    def test_a_summary_says_when_where_the_score_and_who_against(self):
        first = team_finder.find(self.source, self.db, TEAM0, need=3)[0]
        own, other = SAMPLE_MATCH["final_score"]
        self.assertEqual(first.score, (own, other))
        self.assertEqual(first.map, "Bank")
        self.assertEqual(first.enemy, "Ridgeview")
        text = first.summary()
        for bit in ("Sep 22", "Bank", f"{own}–{other}", "vs Ridgeview"):
            self.assertIn(bit, text)

    def test_a_generic_enemy_name_is_its_players(self):
        found = team_finder.find(self.source, self.db, TEAM0, need=3)
        self.assertTrue(found[1].enemy.startswith(TEAM1[0]) or any(n in found[1].enemy for n in TEAM1))

    def test_the_score_is_the_teams_own_first_whichever_side_it_was(self):
        source = FakeSource({"Match-2026-09-22_20-00-00-1": match("m9", swap=True)})
        found = team_finder.find(source, self.db, TEAM0, need=3)[0]
        self.assertEqual(found.score, tuple(SAMPLE_MATCH["final_score"]))

    def test_too_few_of_the_team_is_not_their_match(self):
        self.assertEqual(team_finder.find(self.source, self.db, TEAM0[:2] + ["nobody"], need=3), [])
        self.assertEqual(len(team_finder.find(self.source, self.db, TEAM0[:2], need=3)), 3)  # need caps at the roster

    def test_matches_already_in_your_stats_are_left_out(self):
        self.db.import_match("Match-2026-09-21_20-00-00-1", self.source._matches["Match-2026-09-21_20-00-00-1"],
                             players=TEAM0)
        names = [f.source for f in team_finder.find(self.source, self.db, TEAM0, need=3)]
        self.assertNotIn("Match-2026-09-21_20-00-00-1", names)

    def test_a_match_kept_without_some_of_the_team_is_found_to_add_them(self):
        self.db.import_match("Match-2026-09-21_20-00-00-1", self.source._matches["Match-2026-09-21_20-00-00-1"],
                             players=TEAM0[:2])
        again = next(f for f in team_finder.find(self.source, self.db, TEAM0, need=3)
                     if f.source == "Match-2026-09-21_20-00-00-1")
        self.assertFalse(again.new)
        self.assertEqual(sorted(again.keep, key=str.casefold), sorted(TEAM0, key=str.casefold))
        self.assertIn("adds players", again.summary())

    def test_names_are_matched_in_any_case(self):
        self.assertEqual(len(team_finder.find(self.source, self.db, [n.upper() for n in TEAM0], need=5)), 3)

    def test_no_roster_or_no_replays_finds_nothing(self):
        self.assertEqual(team_finder.find(self.source, self.db, [], need=3), [])
        self.assertEqual(team_finder.find(None, self.db, TEAM0, need=3), [])

    def test_progress_is_reported_to_the_end(self):
        seen = []
        team_finder.find(self.source, self.db, TEAM0, need=3, progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(seen[-1], (4, 4))


class TestAdd(unittest.TestCase):
    def setUp(self):
        self.db = StatsDB(":memory:")
        self.addCleanup(self.db.close)
        self.source = FakeSource({"Match-2026-09-22_20-00-00-1": match("m3"),
                                  "Match-2026-09-21_20-00-00-1": match("m2")})

    def test_quick_add_all_puts_every_match_in_with_only_the_teams_players(self):
        found = team_finder.find(self.source, self.db, TEAM0, need=3)
        added, failed = team_finder.add(self.source, self.db, found)
        self.assertEqual((len(added), failed), (2, []))
        self.assertEqual(len(self.db.team_matches(TEAM0, 5)), 2)
        kept = {r["player"] for r in self.db.query("SELECT player FROM match_players")}
        self.assertEqual(kept, set(TEAM0))
        self.assertEqual(team_finder.find(self.source, self.db, TEAM0, need=3), [])  # nothing left to add

    def test_one_picked_match_is_added_alone(self):
        found = team_finder.find(self.source, self.db, TEAM0, need=3)
        team_finder.add(self.source, self.db, found[1:])
        self.assertEqual([r["match_id"] for r in self.db.team_matches(TEAM0, 5)], ["m2"])

    def test_a_replay_gone_since_is_said_not_added(self):
        found = team_finder.find(self.source, self.db, TEAM0, need=3)
        del self.source._matches["Match-2026-09-22_20-00-00-1"]
        added, failed = team_finder.add(self.source, self.db, found)
        self.assertEqual(len(added), 1)
        self.assertIn("isn't here", failed[0][1])


if __name__ == "__main__":
    unittest.main()
