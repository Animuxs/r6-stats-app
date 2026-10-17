"""Tests for map_pool.py: each map's record, side win rates and best and worst sites, worked out
by hand from the demo match. Run from the repo root:  python -m unittest discover -s scripts"""

from __future__ import annotations

import copy
import unittest

from map_pool import UNKNOWN_MAP, map_pool, site_table, veto_hint
from sample_data import SAMPLE_MATCH, TEAM0, TEAM1
from stats_db import StatsDB

# The demo match, from team 0's side (Fabian's): 6-3 on Bank. On attack it won 5 of 6 rounds: on
# Kids/Rooms 2-1, on CCTV/Archives 3-0. On defense it won 1 of 3: CCTV/Archives 0-1, Kids/Rooms 1-1.


def _match(i: int, **changes) -> dict:
    match = copy.deepcopy(SAMPLE_MATCH)
    match.update(match_id=f"pool-{i}", played_at=f"2026-09-2{i} 20:00:00", recording_player="Fabian", **changes)
    return match


class TestMapPool(unittest.TestCase):

    def setUp(self):
        self.db = StatsDB(":memory:")
        self.addCleanup(self.db.close)

    def add(self, i: int, players=None, **changes) -> dict:
        match = _match(i, **changes)
        self.db.import_match(f"Match-{i}", match, files=1, players=players)
        return match

    def test_one_map_twice(self):
        self.add(0)
        self.add(1)
        [bank] = map_pool(self.db, ["Fabian"])
        self.assertEqual((bank["map"], bank["maps"], bank["maps_won"], bank["maps_lost"]), ("Bank", 2, 2, 0))
        self.assertEqual((bank["rounds_won"], bank["rounds_lost"]), (12, 6))
        self.assertAlmostEqual(bank["round_win"], 100 * 12 / 18)
        self.assertAlmostEqual(bank["attack_win"], 100 * 10 / 12)
        self.assertAlmostEqual(bank["defense_win"], 100 * 2 / 6)
        self.assertEqual(bank["best_attack"], "CCTV/Archives (6–0)")
        self.assertEqual(bank["worst_attack"], "Kids/Rooms (4–2)")
        # on defense only Kids/Rooms has enough rounds (CCTV/Archives has 2): no best or worst
        self.assertEqual((bank["best_defense"], bank["worst_defense"]), ("—", "—"))

    def test_the_other_side_sees_the_mirror(self):
        self.add(0)
        self.add(1)
        [bank] = map_pool(self.db, [TEAM1[0]])
        self.assertEqual((bank["maps_won"], bank["maps_lost"], bank["rounds_won"]), (0, 2, 6))
        self.assertAlmostEqual(bank["defense_win"], 100 * 2 / 12)  # team 0's attack is their defense
        self.assertEqual(bank["best_defense"], "Kids/Rooms (2–4)")
        self.assertEqual(bank["worst_defense"], "CCTV/Archives (0–6)")

    def test_sites_of_one_map(self):
        self.add(0)
        sites = {s["site"]: s for s in site_table(self.db, ["fabian"], "Bank")}  # any case
        self.assertEqual(sites["Kids/Rooms"], {"site": "Kids/Rooms", "attack": 3, "attack_won": 2,
                                               "defense": 2, "defense_won": 1})
        self.assertEqual(sites["CCTV/Archives"], {"site": "CCTV/Archives", "attack": 3, "attack_won": 3,
                                                  "defense": 1, "defense_won": 0})
        self.assertEqual(site_table(self.db, ["Fabian"], "Oregon"), [])

    def test_a_team_counts_only_matches_with_enough_of_it(self):
        self.add(0)
        self.add(1, players=TEAM0[:2])  # only two of the team were tracked in this one
        team = list(TEAM0)
        [bank] = map_pool(self.db, team, min_players=3)
        self.assertEqual(bank["maps"], 1)
        [bank] = map_pool(self.db, team, min_players=2)
        self.assertEqual(bank["maps"], 2)
        self.assertEqual(bank["rounds_won"], 12)  # each round once for the whole team, never per player

    def test_unknown_maps_and_rounds_without_a_result(self):
        match = self.add(0, map=None)
        match["rounds"][0]["winner_team"] = None
        self.db.import_match("Match-1", _match(1, map=None, rounds=match["rounds"]), files=1)
        [unknown] = map_pool(self.db, ["Fabian"])
        self.assertEqual(unknown["map"], UNKNOWN_MAP)
        self.assertEqual(unknown["rounds_won"] + unknown["rounds_lost"], 17)  # the round without a winner is left out
        self.assertTrue(site_table(self.db, ["Fabian"], UNKNOWN_MAP))

    def test_nobody(self):
        self.add(0)
        self.assertEqual(map_pool(self.db, []), [])
        self.assertEqual(map_pool(self.db, ["  "]), [])
        self.assertEqual(map_pool(self.db, ["Somebody else"]), [])
        self.assertEqual(site_table(self.db, [], "Bank"), [])

    def test_strongest_and_weakest_maps(self):
        for i in range(4):
            self.add(i, map="Bank" if i < 2 else "Oregon")
        lost = _match(9, map="Chalet")
        for rnd in lost["rounds"]:
            rnd["winner_team"] = 1  # every round lost: one map isn't enough to be called weakest
        self.db.import_match("Match-9", lost, files=1)
        pool = map_pool(self.db, ["Fabian"])
        self.assertEqual([p["map"] for p in pool], ["Bank", "Oregon", "Chalet"])  # most played first
        self.assertIsNone(veto_hint(pool))  # Bank and Oregon are won as often as each other
        pool[1]["round_win"] -= 10
        best, worst = veto_hint(pool)
        self.assertEqual((best["map"], worst["map"]), ("Bank", "Oregon"))
        self.assertIsNone(veto_hint(pool[:1]))


if __name__ == "__main__":
    unittest.main()
