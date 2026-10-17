"""
map_pool.py
===========
The map pool: how you, or a team you built, do on each map, from the stats database. For each
map: the maps played and their record, the rounds won overall and on attack and defense, and the
bomb sites the team does best and worst on. Team Analytics shows it, to help pick and ban maps.

A team's matches are counted the way Build a team counts them (ask_engine.team_sides): at least so
many of its players on one side. Every round counts once for the whole team, never added up from
its players. A round's site is the one the defenders set up on (attackers attack it).
"""

from __future__ import annotations

from typing import Any

from ask_engine import rate, team_sides
from stats_db import StatsDB

UNKNOWN_MAP = "Unknown map"
MIN_SITE_ROUNDS = 3  # a site needs this many rounds on a side before it can be a map's best or worst
MIN_VETO_MAPS = 2    # and a map this many maps played before it's called strongest or weakest

# each round's site, from any tracked player's row for it (every row of a round has the same site)
_ROUND_SITE = ("SELECT match_id, round, MAX(site) AS site FROM round_players "
               "WHERE site IS NOT NULL AND site != '' GROUP BY match_id, round")


def _keys(players: list[str]) -> list[str]:
    return list(dict.fromkeys(p.strip().casefold() for p in players if p and p.strip()))


def _site_rows(db: StatsDB, players: list[str], min_players: int, map_name: str | None = None) -> list[dict]:
    """(map, site, side, won, rounds) for the team's rounds with a known site and result."""
    keys = _keys(players)
    if not keys:
        return []
    sides, params = team_sides(keys, min_players)
    only = ""
    if map_name is not None:
        only, params = " AND COALESCE(m.map, ?) = ?", params + [UNKNOWN_MAP, map_name]
    return db.query(
        f"WITH sides AS ({sides}), round_site AS ({_ROUND_SITE})"
        f" SELECT COALESCE(m.map, '{UNKNOWN_MAP}') AS map, rs.site, r.side, SUM(r.won = 1) AS won,"
        " COUNT(*) AS rounds FROM sides s JOIN team_rounds r ON r.match_id = s.match_id AND r.team = s.team"
        " JOIN matches m ON m.match_id = s.match_id"
        " JOIN round_site rs ON rs.match_id = r.match_id AND rs.round = r.round"
        f" WHERE r.won IS NOT NULL AND r.side IN ('attack', 'defense'){only}"
        " GROUP BY 1, rs.site, r.side", params)


def _best_and_worst(sites: list[dict]) -> tuple[dict | None, dict | None]:
    """The site won most and least often (by share of rounds, then rounds played), among those
    with enough rounds to say; (None, None) when fewer than two sites have, or they're all won as
    often as each other."""
    known = [s for s in sites if s["rounds"] >= MIN_SITE_ROUNDS]
    ranked = sorted(known, key=lambda s: (s["won"] / s["rounds"], s["rounds"]), reverse=True)
    if len(ranked) < 2 or ranked[0]["won"] * ranked[-1]["rounds"] == ranked[-1]["won"] * ranked[0]["rounds"]:
        return None, None
    return ranked[0], ranked[-1]


def _site_text(site: dict | None) -> str:
    return "—" if site is None else f"{site['site']} ({site['won']}–{site['rounds'] - site['won']})"


def map_pool(db: StatsDB, players: list[str], min_players: int = 1) -> list[dict[str, Any]]:
    """Every map the team played, most played first: maps and record, rounds won overall and on
    each side (as percentages, None with nothing to go on), and its best and worst defense and
    attack sites. `players` is one player (min_players 1) or a team's roster."""
    keys = _keys(players)
    if not keys:
        return []
    sides, params = team_sides(keys, min_players)
    maps = db.query(
        f"WITH sides AS ({sides}) SELECT COALESCE(m.map, '{UNKNOWN_MAP}') AS map, COUNT(*) AS maps,"
        " SUM(CASE WHEN s.team = 0 THEN m.score0 > m.score1 ELSE m.score1 > m.score0 END) AS maps_won,"
        " SUM(CASE WHEN s.team = 0 THEN m.score0 < m.score1 ELSE m.score1 < m.score0 END) AS maps_lost"
        " FROM sides s JOIN matches m ON m.match_id = s.match_id GROUP BY 1", params)
    rounds = {r["map"]: r for r in db.query(
        f"WITH sides AS ({sides}) SELECT COALESCE(m.map, '{UNKNOWN_MAP}') AS map,"
        " SUM(r.won = 1) AS won, SUM(r.won IS NOT NULL) AS rounds,"
        " SUM(r.side = 'attack' AND r.won = 1) AS attack_won, SUM(r.side = 'attack' AND r.won IS NOT NULL) AS attack,"
        " SUM(r.side = 'defense' AND r.won = 1) AS defense_won,"
        " SUM(r.side = 'defense' AND r.won IS NOT NULL) AS defense"
        " FROM sides s JOIN team_rounds r ON r.match_id = s.match_id AND r.team = s.team"
        " JOIN matches m ON m.match_id = s.match_id GROUP BY 1", params)}
    sites: dict[tuple[str, str], list[dict]] = {}
    for row in _site_rows(db, players, min_players):
        sites.setdefault((row["map"], row["side"]), []).append(row)
    pool = []
    for m in maps:
        r = rounds.get(m["map"]) or {}
        won, played = int(r.get("won") or 0), int(r.get("rounds") or 0)
        best_defense, worst_defense = _best_and_worst(sites.get((m["map"], "defense"), []))
        best_attack, worst_attack = _best_and_worst(sites.get((m["map"], "attack"), []))
        pool.append({
            "map": m["map"], "maps": m["maps"], "maps_won": int(m["maps_won"] or 0),
            "maps_lost": int(m["maps_lost"] or 0), "rounds_won": won, "rounds_lost": played - won,
            "round_win": rate(won, played),
            "attack_win": rate(int(r.get("attack_won") or 0), int(r.get("attack") or 0)),
            "defense_win": rate(int(r.get("defense_won") or 0), int(r.get("defense") or 0)),
            "best_defense": _site_text(best_defense), "worst_defense": _site_text(worst_defense),
            "best_attack": _site_text(best_attack), "worst_attack": _site_text(worst_attack),
        })
    return sorted(pool, key=lambda p: (-p["maps"], -(p["round_win"] or 0), p["map"]))


def veto_hint(pool: list[dict]) -> tuple[dict, dict] | None:
    """The strongest and weakest maps by share of rounds won, among maps played at least
    MIN_VETO_MAPS times; None unless two maps qualify and aren't won as often as each other."""
    known = [p for p in pool if p["maps"] >= MIN_VETO_MAPS and p["round_win"] is not None]
    ranked = sorted(known, key=lambda p: (p["round_win"], p["maps"]), reverse=True)
    if len(ranked) < 2 or ranked[0]["round_win"] == ranked[-1]["round_win"]:
        return None
    return ranked[0], ranked[-1]


def site_table(db: StatsDB, players: list[str], map_name: str, min_players: int = 1) -> list[dict[str, Any]]:
    """Each site of one map: the team's rounds and rounds won there on attack and on defense, the
    sites played most first."""
    by_site: dict[str, dict[str, Any]] = {}
    for row in _site_rows(db, players, min_players, map_name):
        site = by_site.setdefault(row["site"], {"site": row["site"], "attack": 0, "attack_won": 0,
                                                 "defense": 0, "defense_won": 0})
        site[row["side"]] += row["rounds"]
        site[f"{row['side']}_won"] += int(row["won"] or 0)
    return sorted(by_site.values(), key=lambda s: (-(s["attack"] + s["defense"]), s["site"]))
