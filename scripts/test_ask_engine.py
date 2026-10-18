"""Tests for ask_engine.py: plain-English questions, checked against numbers worked out
independently from metrics_engine (the scoreboards' own code), not from SQL.
Run from the repo root:  python -m unittest discover -s scripts"""

from __future__ import annotations

import copy
import random
import unittest
from dataclasses import dataclass, replace
from datetime import datetime

from ask_engine import Vocab, ask, interpret, suggestions, team_report
from metrics_engine import compute_match_metrics, round_eps
from stats_db import StatsDB, nice_day, nice_time

NOW = datetime(2026, 9, 26, 12, 0)  # a Saturday
US = ["Alpha.TAG", "Bravo.TAG", "Charlie", "Delta", "Echo"]  # Alpha.TAG recorded every replay: "me"
THEM = [["Foxtrot", "Golf", "Hotel", "India", "Juliet"], ["Kilo", "Lima", "Mike", "November", "Oscar"]]
MAPS = ["Bank", "Kafe Dostoyevsky", "Bank", "Villa", "Kafe Dostoyevsky", "Bank"]
DATES = ["2026-09-12 20:00:00", "2026-09-14 21:00:00", "2026-09-19 19:00:00", "2026-09-21 18:00:00",
         "2026-09-23 22:00:00", "2026-09-25 20:30:00"]
ATTACKERS, DEFENDERS = ["Ash", "Thermite", "Sledge", "Twitch"], ["Jäger", "Bandit", "Mute", "Rook"]


def make_match(n: int, rng: random.Random) -> dict:
    """A random but valid match between US and one of THEM, like parser.normalize_from_r6_dissect makes."""
    them = THEM[n % 2]
    team_of = {p: 0 for p in US} | {p: 1 for p in them}
    rounds, score = [], [0, 0]
    for r in range(rng.randint(6, 9)):
        attack = r % 2 if r < 6 else rng.randint(0, 1)
        alive = {0: list(US), 1: list(them)}
        events, clock = [], 180.0
        while alive[0] and alive[1] and rng.random() < 0.85:
            side = rng.randint(0, 1)
            killer, victim = rng.choice(alive[side]), rng.choice(alive[1 - side])
            clock -= rng.uniform(2, 15)
            events.append({"type": "kill", "time": clock, "elapsed": 180 - clock, "actor": killer, "target": victim,
                           "headshot": rng.random() < 0.4})
            events.append({"type": "death", "time": clock, "elapsed": 180 - clock, "actor": victim,
                           "killed_by": killer})
            alive[1 - side].remove(victim)
        if alive[attack] and rng.random() < 0.3:
            events.append({"type": "plant", "time": clock - 1, "elapsed": 181 - clock, "actor": rng.choice(alive[attack])})
        winner = 0 if not alive[1] else 1 if not alive[0] else rng.randint(0, 1)
        score[winner] += 1
        operators = {p: rng.choice(ATTACKERS if team_of[p] == attack else DEFENDERS) for p in team_of}
        rounds.append({"round_num": r, "players": list(team_of), "operators": operators, "winner_team": winner,
                       "win_condition": "KilledOpponents", "attack_team": attack, "site": rng.choice(["1F Lobby", "2F Office"]),
                       "events": events, "round_stats": None})
    return {"map": MAPS[n], "match_id": f"m{n}", "team_names": ["YOUR TEAM", "ENEMY TEAM"], "final_score": score,
            "players": [{"name": p, "team": t, "operator_history": []} for p, t in team_of.items()],
            "rounds": rounds, "played_at": DATES[n], "match_type": "Ranked" if n % 3 else "Unranked",
            "recording_player": "Alpha.TAG"}


MATCHES = {f"Match-{n}": make_match(n, random.Random(n)) for n in range(len(DATES))}


@dataclass
class Totals:
    """A player's numbers added up over several matches, from metrics_engine's per-match stats."""
    kills: int = 0
    deaths: int = 0
    headshots: int = 0
    rounds: int = 0
    weighted_rating: float = 0.0

    def add(self, s) -> None:
        self.kills += s.kills
        self.deaths += s.deaths
        self.headshots += s.headshots
        self.rounds += s.rounds_played
        self.weighted_rating += s.rating * s.rounds_played

    @property
    def hs_pct(self) -> float:
        return 100 * self.headshots / self.kills if self.kills else 0.0

    @property
    def eps(self) -> int:  # EPS over several matches: the rounds-weighted average of each match's
        return round_eps(100 * self.weighted_rating / self.rounds)


def truth(players=None, matches=None):
    """Each player's stats over the given matches, straight from metrics_engine."""
    per: dict[str, Totals] = {}
    for name in matches or MATCHES:
        for p, s in compute_match_metrics(MATCHES[name]).items():
            if (players is None or p in players) and s.rounds_played:
                per.setdefault(p, Totals()).add(s)
    return per


def team_truth(roster, min_players):
    """Build a team, worked out directly: the matches where at least min_players of the roster were
    on one side (the side with the most of them), and each roster player's numbers from that side."""
    matches, per = [], {}
    for name, m in MATCHES.items():
        sides = {}
        for p in m["players"]:
            if p["name"] in roster:
                sides.setdefault(p["team"], []).append(p["name"])
        if not sides:
            continue
        side = max(sides, key=lambda t: (len(sides[t]), -t))
        if len(sides[side]) >= min_players:
            matches.append((name, side))
            stats = compute_match_metrics(m)
            for p in sides[side]:
                per.setdefault(p, Totals()).add(stats[p])
    return matches, per


def team_rounds_truth(roster, min_players):
    """A team's own stats, worked out here from the matches themselves: every round of every map it
    played counts once for the team; it was man down when, with both sides still alive, it had two
    or more fewer players standing, and got back to even when both later had as many standing."""
    t = dict.fromkeys(("maps", "maps_won", "maps_lost", "rounds", "rounds_won", "rounds_lost", "man_down",
                       "man_down_even", "man_down_won", "attack_rounds", "plants", "post_plant", "post_plant_won",
                       "defense_rounds",
                       "enemy_plants", "retakes", "retakes_won", "maps_without_rounds"), 0)
    for name, team in team_truth(roster, min_players)[0]:
        m = MATCHES[name]
        side_of = {p["name"]: p["team"] for p in m["players"]}
        ours, theirs = m["final_score"][team], m["final_score"][1 - team]
        t["maps"] += 1
        t["maps_won"] += ours > theirs
        t["maps_lost"] += ours < theirs
        for rnd in m["rounds"]:
            standing = [sum(side_of[n] == side for n in rnd["players"]) for side in (0, 1)]
            down = even = False
            for e in rnd["events"]:
                if e["type"] == "death":
                    standing[side_of[e["actor"]]] -= 1
                    if standing[0] and standing[1]:
                        even = even or (down and standing[0] == standing[1])
                        down = down or standing[team] - standing[1 - team] <= -2
            won = rnd["winner_team"] == team
            planted = any(e["type"] == "plant" for e in rnd["events"])
            attacking = rnd["attack_team"] == team
            t["rounds"] += 1
            t["rounds_won"] += won
            t["rounds_lost"] += not won
            t["man_down"] += down
            t["man_down_won"] += down and won
            t["man_down_even"] += even
            key = "attack_rounds" if attacking else "defense_rounds"
            t[key] += 1
            if planted and attacking:
                t["plants"] += 1
                t["post_plant"] += 1
                t["post_plant_won"] += won
            elif planted:
                t["enemy_plants"] += 1
                t["retakes"] += 1
                t["retakes_won"] += won
    return t


def kd(s):
    return s.kills / s.deaths if s.deaths else float(s.kills)


def won(match, player):
    team = next(p["team"] for p in match["players"] if p["name"] == player)
    a, b = match["final_score"][team], match["final_score"][1 - team]
    return None if a == b else a > b


class EngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = StatsDB(":memory:")
        for name, match in MATCHES.items():
            cls.db.import_match(name, match, files=len(match["rounds"]))
        cls.db.save_roster("TAG", ["Alpha.TAG", "Bravo.TAG", "Charlie"])
        cls.vocab = Vocab.from_db(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def ask(self, question):
        answer = ask(question, self.db, now=NOW, vocab=self.vocab)
        self.assertTrue(answer.ok, f"{question!r}: {answer.headline}")
        return answer


class TestValues(EngineTest):
    def test_my_kd(self):
        me = truth(["Alpha.TAG"])["Alpha.TAG"]
        answer = self.ask("What's my K/D?")
        self.assertEqual(answer.rows[0]["K/D"], round(kd(me), 2))
        self.assertIn(f"{kd(me):.2f}", answer.headline)
        self.assertIn(f"{len(MATCHES)} matches", answer.headline)

    def test_filters_by_map_and_match_type(self):
        on_bank = [n for n, m in MATCHES.items() if m["map"] == "Bank"]
        me = truth(["Alpha.TAG"], on_bank)["Alpha.TAG"]
        self.assertEqual(self.ask("my kd on bank").rows[0]["K/D"], round(kd(me), 2))
        ranked = [n for n, m in MATCHES.items() if m["match_type"] == "Ranked"]
        self.assertEqual(self.ask("my kills in ranked").rows[0]["Kills"], truth(["Alpha.TAG"], ranked)["Alpha.TAG"].kills)

    def test_another_player_by_part_of_their_name(self):
        bravo = truth(["Bravo.TAG"])["Bravo.TAG"]
        answer = self.ask("how many kills does bravo have?")
        self.assertEqual(answer.rows[0]["Kills"], bravo.kills)
        self.assertTrue(answer.headline.startswith("Bravo.TAG has"), answer.headline)
        self.assertEqual(self.ask("Bravo.TAG's headshot %").rows[0]["Headshot %"], round(bravo.hs_pct, 1))

    def test_record_and_counts(self):
        results = [won(m, "Alpha.TAG") for m in MATCHES.values()]
        answer = self.ask("what's my record?")
        self.assertEqual((answer.rows[0]["Wins"], answer.rows[0]["Losses"]), (results.count(True), results.count(False)))
        self.assertIn(f"{results.count(True)}–{results.count(False)}", answer.headline)
        self.assertEqual(self.ask("how many matches have I played?").rows[0]["Matches"], len(MATCHES))

    def test_eps_is_rounds_weighted(self):
        me = truth(["Alpha.TAG"])["Alpha.TAG"]
        self.assertEqual(self.ask("my eps").rows[0]["EPS"], me.eps)

    def test_in_wins_only(self):
        wins = [n for n, m in MATCHES.items() if won(m, "Alpha.TAG")]
        answer = self.ask("my kd in wins")
        self.assertEqual(answer.rows[0]["K/D"], round(kd(truth(["Alpha.TAG"], wins)["Alpha.TAG"]), 2))
        self.assertEqual(answer.query.metrics, ["kd"])  # "wins" was the filter, not a second thing to show


class TestRoundLevel(EngineTest):
    def per_round(self, player, keep):
        """kills, deaths and rounds of `player` over rounds where keep(round, team) holds."""
        kills = deaths = rounds = won_rounds = decided = 0
        for match in MATCHES.values():
            stats = compute_match_metrics(match)[player]
            team = next(p["team"] for p in match["players"] if p["name"] == player)
            for rnd, rb in zip(match["rounds"], stats.round_breakdown):
                if keep(rnd, team, player):
                    kills, deaths, rounds = kills + rb.kills, deaths + rb.deaths, rounds + 1
                    won_rounds += rnd["winner_team"] == team
                    decided += 1
        return kills, deaths, rounds, won_rounds, decided

    def test_kd_on_attack(self):
        k, d, r, _, _ = self.per_round("Alpha.TAG", lambda rnd, team, p: rnd["attack_team"] == team)
        answer = self.ask("my K/D on attack")
        self.assertEqual(answer.rows[0]["K/D"], round(k / d if d else k, 2))
        self.assertIn(f"{r} rounds", answer.headline)

    def test_operator_stats(self):
        k, d, r, _, _ = self.per_round("Alpha.TAG", lambda rnd, team, p: rnd["operators"][p] == "Jäger")
        answer = self.ask("my kd as jager")  # no accent typed
        self.assertEqual(answer.rows[0]["K/D"], round(k / d if d else k, 2))
        self.assertEqual(answer.rows[0]["Rounds"], r)

    def test_most_played_operators(self):
        counts = {}
        for match in MATCHES.values():
            for rnd in match["rounds"]:
                counts[rnd["operators"]["Alpha.TAG"]] = counts.get(rnd["operators"]["Alpha.TAG"], 0) + 1
        answer = self.ask("Which operators do I play most?")
        self.assertEqual({r["Operator"]: r["Rounds"] for r in answer.rows}, counts)
        self.assertEqual(answer.rows[0]["Rounds"], max(counts.values()))

    def test_who_plays_an_operator(self):  # players ranked by their rounds on it, not by K/D
        counts = {}
        for match in MATCHES.values():
            for rnd in match["rounds"]:
                for player, op in rnd["operators"].items():
                    if op == "Jäger":
                        counts[player] = counts.get(player, 0) + 1
        answer = self.ask("who plays Jäger?")
        self.assertEqual(answer.query.group, "player")
        self.assertEqual(answer.rows[0]["Rounds"], max(counts.values()))
        self.assertIn("the most rounds as Jäger", answer.headline)

    def test_attack_vs_defense(self):
        answer = self.ask("attack vs defense win rate")
        self.assertEqual(answer.query.kind, "compare")
        for side in ("attack", "defense"):
            _, _, _, won_rounds, decided = self.per_round(
                "Alpha.TAG", lambda rnd, team, p, side=side: (rnd["attack_team"] == team) == (side == "attack"))
            row = next(r for r in answer.rows if r["Side"] == side.capitalize())
            self.assertEqual(row["Round win %"], round(100 * won_rounds / decided, 1))


class TestRankings(EngineTest):
    def test_players_ranked_by_kd(self):
        everyone = truth()
        answer = self.ask("top 5 players by kd")
        expected = sorted((p for p in everyone), key=lambda p: -kd(everyone[p]))[:5]
        self.assertEqual([r["Player"] for r in answer.rows], expected)
        self.assertEqual([r["K/D"] for r in answer.rows], [round(kd(everyone[p]), 2) for p in expected])

    def test_minimum_matches_keeps_one_off_players_out(self):
        answer = self.ask("who has the best kd?")  # opponents played 3 matches each; min is 3
        self.assertTrue(all(r["Matches"] >= 3 for r in answer.rows))
        answer = self.ask("best kd with at least 4 matches")
        self.assertEqual({r["Player"] for r in answer.rows}, set(US))  # only we played more than 3

    def test_where_do_i_rank(self):
        everyone = truth()
        order = sorted(everyone, key=lambda p: -kd(everyone[p]))
        answer = self.ask("where do I rank by K/D?")
        self.assertIn(f"You're #{order.index('Alpha.TAG') + 1} of {len(order)}", answer.headline)

    def test_best_map_is_mine(self):
        per_map = {}
        for m in MATCHES.values():
            per_map.setdefault(m["map"], []).append(won(m, "Alpha.TAG"))
        answer = self.ask("what's my best map?")
        rates = {mp: 100 * r.count(True) / (r.count(True) + r.count(False)) for mp, r in per_map.items()
                 if len(r) >= 2 and (r.count(True) + r.count(False))}
        self.assertEqual(answer.rows[0]["Map"], max(rates, key=lambda mp: (rates[mp], len(per_map[mp]))))
        self.assertEqual({r["Map"]: r["Win %"] for r in answer.rows}, {mp: round(v, 1) for mp, v in rates.items()})

    def test_teammates_only(self):
        answer = self.ask("which teammate has the most kills?")
        self.assertEqual(answer.query.who, "teammates")
        self.assertNotIn("Alpha.TAG", [r["Player"] for r in answer.rows])
        self.assertTrue(set(r["Player"] for r in answer.rows) <= set(US))

    def test_me_and_my_teammates(self):
        side = truth(US)  # everyone who played on my side (all of US played every match)
        order = sorted(side, key=lambda p: -kd(side[p]))
        for question in ("compare my kd with my teammates", "where do I rank by kd among my teammates?"):
            with self.subTest(question=question):
                answer = self.ask(question)
                self.assertEqual(answer.query.who, "my_team")
                self.assertEqual([r["Player"] for r in answer.rows], order)
                self.assertIn(f"You're #{order.index('Alpha.TAG') + 1} of {len(order)}", answer.headline)
        # who I play with is about them alone
        self.assertEqual(self.ask("who do I play with most?").query.who, "teammates")

    def test_single_matches(self):
        mine = {n: compute_match_metrics(m)["Alpha.TAG"].kills for n, m in MATCHES.items()}
        best = max(mine, key=mine.get)
        answer = self.ask("most kills in a match")
        self.assertEqual((answer.query.group, answer.query.who), ("match", "me"))
        self.assertEqual([r["Kills"] for r in answer.rows], sorted(mine.values(), reverse=True))
        where = f"{nice_time(MATCHES[best]['played_at'])} · {MATCHES[best]['map']}"
        self.assertEqual(answer.headline, f"Your most kills in a match: {mine[best]} ({where}).")
        # everyone's best single match
        every = [(s.kills, p) for m in MATCHES.values() for p, s in compute_match_metrics(m).items()]
        answer = self.ask("who had the most kills in a single match?")
        self.assertEqual(answer.query.group, "player_match")
        self.assertEqual(answer.rows[0]["Kills"], max(every)[0])
        self.assertEqual(len(answer.rows), 10)
        self.assertEqual(self.ask("my best game").query.metrics, ["eps"])
        self.assertEqual(self.ask("best match type").query.group, "match_type")  # not a single match

    def test_aces_and_fraggers(self):
        match = copy.deepcopy(MATCHES["Match-0"])  # plus a round where Alpha.TAG kills all five
        events = []
        for i, victim in enumerate(p["name"] for p in match["players"] if p["team"] == 1):
            clock = 170.0 - 10 * i
            events += [{"type": "kill", "time": clock, "elapsed": 180 - clock, "actor": "Alpha.TAG",
                        "target": victim, "headshot": False},
                       {"type": "death", "time": clock, "elapsed": 180 - clock, "actor": victim,
                        "killed_by": "Alpha.TAG"}]
        match["rounds"].append({"round_num": len(match["rounds"]), "players": [p["name"] for p in match["players"]],
                                "operators": {}, "winner_team": 0, "win_condition": "KilledOpponents",
                                "attack_team": 0, "site": "1F Lobby", "events": events, "round_stats": None})
        aces = {p: sum(rb.kills >= 5 for rb in s.round_breakdown) for p, s in compute_match_metrics(match).items()}
        self.assertEqual(aces["Alpha.TAG"], 1)
        with StatsDB(":memory:") as db:
            db.import_match("Match-ace", match)
            self.assertEqual(ask("how many aces have I got?", db, now=NOW).headline,
                             f"You have 1 ace over {len(match['rounds'])} rounds.")
            self.assertEqual(ask("how many aces has everyone got", db, now=NOW).rows[0]["Aces"], sum(aces.values()))
        answer = self.ask("top fraggers")
        self.assertEqual(answer.query.metrics, ["kills"])
        self.assertEqual(answer.rows[0]["Kills"], max(t.kills for t in truth().values()))
        self.assertTrue(answer.headline.split(":")[0].endswith("the most kills"))

    def test_at_the_top_of_a_ranking(self):
        from sample_data import SAMPLE_MATCH  # Fabian has the most kills

        with StatsDB(":memory:") as db:
            db.import_match("demo", dict(SAMPLE_MATCH, recording_player="Fabian"))
            answer = ask("where do I rank by kills?", db, now=NOW)
        self.assertTrue(answer.headline.startswith("You have the most kills: "))
        self.assertNotIn("You're #", answer.headline)
        self.assertFalse(any("not in this ranking" in n for n in answer.notes))

    def test_most_deaths_without_a_subject_ranks_players(self):
        answer = self.ask("most deaths")
        everyone = truth()
        eligible = [p for p in everyone if sum(p in [x["name"] for x in m["players"]] for m in MATCHES.values()) >= 3]
        self.assertEqual(answer.rows[0]["Deaths"], max(everyone[p].deaths for p in eligible))


class TestTrends(EngineTest):
    def test_kd_over_time(self):
        answer = self.ask("how has my K/D changed over time?")
        self.assertEqual(answer.query.kind, "trend")
        self.assertEqual(len(answer.rows), len(MATCHES))
        in_order = sorted(MATCHES, key=lambda n: MATCHES[n]["played_at"])
        per_match = [round(kd(compute_match_metrics(MATCHES[n])["Alpha.TAG"]), 2) for n in in_order]
        self.assertEqual([r["K/D"] for r in answer.rows], per_match)
        early = kd(truth(["Alpha.TAG"], in_order[:3])["Alpha.TAG"])
        late = kd(truth(["Alpha.TAG"], in_order[-3:])["Alpha.TAG"])
        self.assertIn(f"from {early:.2f} to {late:.2f}", answer.headline)
        self.assertEqual(answer.chart["kind"], "line")

    def test_last_n_matches(self):
        last3 = sorted(MATCHES, key=lambda n: MATCHES[n]["played_at"])[-3:]
        answer = self.ask("my kd in my last 3 matches")
        self.assertEqual(answer.rows[0]["K/D"], round(kd(truth(["Alpha.TAG"], last3)["Alpha.TAG"]), 2))

    def test_dates(self):
        since_21 = [n for n, m in MATCHES.items() if m["played_at"] >= "2026-09-21"]
        self.assertEqual(self.ask("my kills since sep 21").rows[0]["Kills"],
                         truth(["Alpha.TAG"], since_21)["Alpha.TAG"].kills)
        this_week = [n for n, m in MATCHES.items() if m["played_at"] >= "2026-09-21"]  # Monday the 21st
        last_week = [n for n, m in MATCHES.items() if "2026-09-14" <= m["played_at"] < "2026-09-21"]
        answer = self.ask("my kills this week vs last week")
        self.assertEqual(answer.query.compare, "period")
        self.assertEqual([r["Kills"] for r in answer.rows],
                         [truth(["Alpha.TAG"], this_week)["Alpha.TAG"].kills,
                          truth(["Alpha.TAG"], last_week)["Alpha.TAG"].kills])
        self.assertEqual(interpret("in september", self.vocab, NOW).since, "2026-09-01 00:00:00")

    def test_dates_read_like_the_rest_of_the_app(self):
        self.assertIn(f"since {nice_day('2026-09-21')}.", self.ask("my kills since sep 21").headline)
        self.assertIn(f"from {nice_day('2026-09-14')} to {nice_day('2026-09-20')}.",
                      self.ask("my kd between sep 14 and sep 20").headline)
        self.assertIn(f"through {nice_day('2026-09-20')}.", self.ask("my kd until sep 20").headline)
        self.assertIn(f"through {nice_day('2026-09-20')}.", self.ask("my kd before sep 21").headline)
        self.assertIn(f"on {nice_day('2026-09-23')}.", self.ask("my kd on sep 23").headline)
        first = min(MATCHES.values(), key=lambda m: m["played_at"])
        trend = self.ask("how has my K/D changed over time?")
        self.assertEqual(trend.rows[0]["Match"], f"{nice_time(first['played_at'])} · {first['map']}")

    def test_my_last_matches(self):
        last3 = sorted(MATCHES, key=lambda n: MATCHES[n]["played_at"])[-3:]
        results = [won(MATCHES[n], "Alpha.TAG") for n in last3]
        answer = self.ask("my last 3 matches")
        self.assertEqual(answer.understood, "Your last 3 matches, newest first")
        self.assertEqual(answer.headline, f"Your last 3 matches: {results.count(True)} won, "
                                          f"{results.count(False)} lost.")
        last = MATCHES[last3[-1]]
        self.assertTrue(self.ask("my last match").headline.startswith(
            f"Your last match: {'a win' if results[-1] else 'a loss'} on {last['map']}"))

    def test_matches_with_a_condition(self):
        answer = self.ask("matches where I had at least 3 kills")
        expected = [n for n, m in MATCHES.items() if compute_match_metrics(m)["Alpha.TAG"].kills >= 3]
        self.assertEqual(len(answer.rows), min(len(expected), 10))
        self.assertTrue(all(r["Kills"] >= 3 for r in answer.rows))


class TestTeamsAndComparisons(EngineTest):
    def test_a_saved_team_in_questions(self):
        matches, per = team_truth(["Alpha.TAG", "Bravo.TAG", "Charlie"], 3)
        kills, deaths = sum(t.kills for t in per.values()), sum(t.deaths for t in per.values())
        answer = self.ask("TAG kd")
        self.assertEqual(answer.rows[0]["K/D"], round(kills / deaths, 2))
        self.assertEqual(answer.rows[0]["Matches"], len(matches))
        results = [won(MATCHES[n], MATCHES[n]["players"][[p["team"] for p in MATCHES[n]["players"]].index(side)]["name"])
                   for n, side in matches]
        record = self.ask("TAG record")
        self.assertEqual((record.rows[0]["Wins"], record.rows[0]["Losses"]), (results.count(True), results.count(False)))
        # how the team is doing is its own numbers, never its players' added up
        team = team_rounds_truth(["Alpha.TAG", "Bravo.TAG", "Charlie"], 3)
        answer = self.ask("how is TAG doing")
        row = answer.rows[0]
        self.assertEqual((row["Maps"], row["Maps won"], row["Maps lost"], row["Rounds won"], row["Rounds lost"]),
                         (team["maps"], team["maps_won"], team["maps_lost"], team["rounds_won"], team["rounds_lost"]))
        self.assertEqual(row["Man-down rounds"], team["man_down"])
        self.assertEqual(row["Man-down back to even %"], round(100 * team["man_down_even"] / team["man_down"], 1))
        self.assertEqual(row["Man-down win %"], round(100 * team["man_down_won"] / team["man_down"], 1))
        self.assertNotIn("EPS", row)
        self.assertNotIn("K/D", row)
        self.assertIn(f"of the {team['man_down']} rounds it went down 2+ players, it got back to even in "
                      f"{team['man_down_even']} and won {team['man_down_won']}", answer.headline)

    def test_two_teams_are_compared_by_their_results(self):
        vocab = replace(self.vocab, rosters={"tag": ["alpha.tag", "bravo.tag"], "rivals": ["foxtrot", "golf"]})
        vocab.build_aliases()
        q = interpret("compare tag and rivals", vocab, NOW)
        self.assertEqual((q.kind, q.compare), ("compare", "team"))
        self.assertEqual(q.metrics, ["win_rate", "wins", "losses", "matches"])  # not the players' K/D or EPS

    def test_build_a_team_page_numbers(self):
        for need in (1, 2, 3):
            with self.subTest(min_players=need):
                matches, per = team_truth(["Alpha.TAG", "Bravo.TAG", "Charlie"], need)
                report = team_report(self.db, "TAG", need)
                self.assertEqual(len(report["matches"]), len(matches))
                got = {r["name"]: r for r in report["players"]}
                self.assertEqual(set(got), set(per))
                for p, t in per.items():
                    self.assertEqual((got[p]["kills"], got[p]["deaths"], round(got[p]["eps"])), (t.kills, t.deaths, t.eps))
                self.assertEqual(report["summary"], team_rounds_truth(["Alpha.TAG", "Bravo.TAG", "Charlie"], need))
                self.assertNotIn("eps", report)  # no team EPS: a team's numbers are its own
                career = truth(["Alpha.TAG", "Bravo.TAG", "Charlie"])  # every match, not just this team's
                self.assertEqual({r["name"]: r["all_time_eps"] for r in report["players"]},
                                 {p: t.eps for p, t in career.items()})
                self.assertEqual(report["missing"], [])

    def test_compare_two_players(self):
        everyone = truth()
        answer = self.ask("compare me and bravo")
        self.assertEqual(answer.query.kind, "compare")
        rows = {r["Player"]: r for r in answer.rows}
        for p in ("Alpha.TAG", "Bravo.TAG"):
            self.assertEqual(rows[p]["K/D"], round(kd(everyone[p]), 2))

    def test_our_win_rate_counts_each_match_once(self):
        results = [won(m, "Alpha.TAG") for m in MATCHES.values()]
        answer = self.ask("our win rate")
        self.assertEqual(answer.rows[0]["Win %"], round(100 * results.count(True) / (len(results) - results.count(None)), 1))


class TestUnderstanding(EngineTest):
    def test_names(self):
        self.assertEqual(interpret("Bravo.TAG's kd", self.vocab, NOW).players, ["bravo.tag"])
        q = interpret("bravoo kd", self.vocab, NOW)  # a typo
        self.assertEqual(q.players, ["bravo.tag"])
        self.assertTrue(any("Bravo.TAG" in n for n in q.notes))
        q = interpret("Zz_Nobody kd", self.vocab, NOW)
        self.assertTrue(any("Zz_Nobody" in n for n in q.notes))
        self.assertEqual(interpret("kafe win rate", self.vocab, NOW).maps, ["Kafe Dostoyevsky"])

    def test_ranked_by_is_not_the_ranked_playlist(self):
        q = interpret("players ranked by kd", self.vocab, NOW)
        self.assertIsNone(q.match_type)
        self.assertEqual(interpret("my kd in ranked", self.vocab, NOW).match_type, "Ranked")

    def test_every_match_type(self):
        vocab = replace(self.vocab, match_types=["Custom game", "Quick Match", "Ranked", "Unranked"])
        for question, match_type in (("my kd in custom games", "Custom game"), ("kd in customs", "Custom game"),
                                     ("my kills in quick matches", "Quick Match"), ("quick play kd", "Quick Match"),
                                     ("unranked kd", "Unranked"), ("kd in ranked", "Ranked")):
            with self.subTest(question=question):
                self.assertEqual(interpret(question, vocab, NOW).match_type, match_type)
        unranked = [n for n, m in MATCHES.items() if m["match_type"] == "Unranked"]
        self.assertIn(f"over {len(unranked)} matches in Unranked.", self.ask("my kd in unranked").headline)
        # one that isn't in the stats finds nothing, rather than quietly answering for every match
        answer = ask("my kd in custom games", self.db, now=NOW, vocab=self.vocab)
        self.assertFalse(answer.ok)
        self.assertIn("There are no custom games in your stats.", answer.notes)

    def test_ranked_vs_unranked(self):
        answer = self.ask("ranked vs unranked win rate")
        self.assertEqual((answer.query.kind, answer.query.compare), ("compare", "match_type"))
        rates = {}
        for match_type in ("Ranked", "Unranked"):
            results = [won(m, "Alpha.TAG") for m in MATCHES.values() if m["match_type"] == match_type]
            rates[match_type] = 100 * results.count(True) / (results.count(True) + results.count(False))
        self.assertEqual({r["Match type"]: r["Win %"] for r in answer.rows},
                         {t: round(v, 1) for t, v in rates.items()})
        better = max(rates, key=rates.get)
        self.assertTrue(answer.headline.startswith(f"{better} is your better match type by win rate"))

    def test_names_that_are_an_operator_too(self):
        vocab = Vocab(players={"rook-_-": "Rook-_-", "alpha.tag": "Alpha.TAG"}, me="alpha.tag", rosters={},
                      maps=[], operators=["Rook"], match_types=[])
        vocab.build_aliases()
        for question, kind, noted in (("Rook's kd", "player", True), ("my kd as rook", "operator", False),
                                      ("rook kd", "operator", True), ("Rook-_- kd", "player", False)):
            with self.subTest(question=question):
                found, notes = vocab.find(question)
                self.assertEqual(found[0].kind, kind)
                self.assertEqual(bool(notes), noted)

    def test_every_example_is_answered(self):
        examples = suggestions(self.vocab, self.db)
        self.assertEqual(len(examples), 8)
        self.assertIn("How is TAG doing?", examples)  # the user's own team is never the one cut
        for question in examples:
            with self.subTest(question=question):
                self.ask(question)

    def test_something_it_cannot_count_is_said_so(self):
        answer = ask("how many teabags", self.db, now=NOW, vocab=self.vocab)
        self.assertFalse(answer.ok)
        self.assertIn("I don't know how to count “teabags” yet.", answer.notes)
        self.assertEqual(self.ask("how many matches have I played").rows[0]["Matches"], len(MATCHES))

    def test_questions_it_cannot_answer(self):
        for question in ("hello", "what's the weather like?"):
            self.assertFalse(ask(question, self.db, now=NOW, vocab=self.vocab).ok)

    def test_a_question_is_never_sql(self):
        answer = ask("'; DROP TABLE matches; -- kd", self.db, now=NOW, vocab=self.vocab)
        self.assertIsNotNone(answer)
        self.assertEqual(self.db.summary()["matches"], len(MATCHES))

    def test_an_empty_database(self):
        with StatsDB(":memory:") as empty:
            self.assertFalse(ask("my kd", empty).ok)


class TestTags(unittest.TestCase):
    """Matches tagged on Match History: "my K/D in scrims"."""

    @classmethod
    def setUpClass(cls):
        cls.db = StatsDB(":memory:")
        for name, match in MATCHES.items():
            cls.db.import_match(name, match, files=len(match["rounds"]))
        cls.scrims = list(MATCHES)[:3]
        for name in cls.scrims:
            cls.db.save_note(MATCHES[name]["match_id"], "Scrim")
        cls.db.save_note(MATCHES[list(MATCHES)[3]]["match_id"], "Official", "a note doesn't change anything")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def ask(self, question):
        return ask(question, self.db, now=NOW)

    def test_in_scrims(self):
        me = truth(["Alpha.TAG"], self.scrims)["Alpha.TAG"]
        for question in ("my kd in scrims", "What's my K/D in scrimmages?", "my k/d scrim"):
            with self.subTest(question=question):
                answer = self.ask(question)
                self.assertEqual(answer.rows[0]["K/D"], round(kd(me), 2))
                self.assertIn("in scrims", answer.understood)
                self.assertFalse(any("to mean" in n for n in answer.notes))  # never a player called Scrim-something

    def test_in_official_matches(self):
        official = list(MATCHES)[3:4]
        answer = self.ask("how many kills do I have in official matches")
        self.assertEqual(answer.rows[0]["Kills"], truth(["Alpha.TAG"], official)["Alpha.TAG"].kills)
        self.assertIn("in official matches", answer.understood)

    def test_a_tag_nothing_has(self):
        answer = self.ask("my kd in practice")
        self.assertFalse(answer.rows)
        self.assertTrue(any("No match is tagged Practice" in n for n in answer.notes))

    def test_two_tags_at_once(self):
        answer = self.ask("my kills in scrims and official matches")
        self.assertEqual(answer.rows[0]["Kills"], truth(["Alpha.TAG"])["Alpha.TAG"].kills)  # every match
        self.assertTrue(any("one tag at a time" in n for n in answer.notes))

    def test_round_level_questions_and_rankings(self):
        answer = self.ask("best map in scrims")
        maps = {MATCHES[n]["map"] for n in self.scrims}
        self.assertTrue({r["Map"] for r in answer.rows} <= maps, answer.rows)
        self.assertIn("in scrims", self.ask("my attack win rate in scrims").understood)


if __name__ == "__main__":
    unittest.main()
