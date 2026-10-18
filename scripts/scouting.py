"""
scouting.py
===========
A scouting report on a team you've played, for Match day: every meeting, the maps you played them on, who on
your side does best against them and the players of theirs your stats have. Opponents are found by the name
the replays give them (ask_engine.OPPONENT_SQL: trimmed, never Ranked's "ENEMY TEAM"), from your side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ask_engine import METRICS, OPPONENT_SQL, rate
from stats_db import StatsDB, nice_day


@dataclass
class Scout:
    opponent: str
    meetings: list[dict[str, Any]] = field(default_factory=list)  # newest first
    record: tuple[int, int, int] = (0, 0, 0)                    # wins, losses, draws
    maps: list[dict[str, Any]] = field(default_factory=list)      # most played first
    ours: list[dict[str, Any]] = field(default_factory=list)      # your side's players against them, best EPS first
    theirs: list[dict[str, Any]] = field(default_factory=list)    # their players your stats have, best EPS first

    @property
    def headline(self) -> str:
        wins, losses, draws = self.record
        played = wins + losses + draws
        if not played:
            return f"You haven't played {self.opponent} in the matches in your stats."
        result = f"{wins}–{losses}" + (f"–{draws}" if draws else "")
        last = self.meetings[0]
        when = f" on {last['When']}" if last["When"] else ""
        return (f"{played} meeting{'s' if played != 1 else ''} with {self.opponent}: {result}. "
                f"Last{when}: {last['Result'].lower()} {last['Score']} on {last['Map']}.")


def _players(db: StatsDB, ids: list[str], me: str, same_side: bool) -> list[dict[str, Any]]:
    """Each player on your side (or theirs) over the matches `ids`, best EPS first."""
    if not ids:
        return []
    keys = ["matches", "kd", "eps", "kost", "entry"]
    select = ", ".join(f"{METRICS[k].match_sql} AS {k}" for k in keys)
    rows = db.query(
        f"""SELECT MAX(t.player) AS player, {select} FROM match_players t JOIN matches m ON m.match_id = t.match_id
            WHERE t.match_id IN ({', '.join('?' * len(ids))})
              AND {'' if same_side else 'NOT '}EXISTS (SELECT 1 FROM match_players s WHERE s.match_id = t.match_id
                                                        AND s.player_key = ? AND s.team = t.team)
            GROUP BY t.player_key""", [*ids, me])
    rows.sort(key=lambda r: (-(r["eps"] or 0), r["player"].casefold()))
    return [{"Player": r["player"], "Matches": r["matches"],
             "K/D": round(r["kd"], 2) if r["kd"] is not None else None,
             "EPS": round(r["eps"]) if r["eps"] is not None else None,
             "KOST %": round(r["kost"]) if r["kost"] is not None else None, "Entry +/-": r["entry"]} for r in rows]


def scout(db: StatsDB, opponent: str) -> Scout:
    """The scouting report on `opponent` (any spelling), from the matches you played against them."""
    report = Scout(opponent.strip())
    me = db.me()
    name = " ".join(opponent.casefold().split())
    if not me or not name:
        return report
    me = me.casefold()
    games = db.query(
        f"""SELECT m.match_id, m.played_at, m.map, m.score0, m.score1, t.team, t.won, {OPPONENT_SQL} AS name
            FROM match_players t JOIN matches m ON m.match_id = t.match_id
            WHERE t.player_key = ? AND LOWER({OPPONENT_SQL}) = ?
            ORDER BY m.played_at DESC, m.match_id DESC""", [me, name])
    if games:
        report.opponent = games[0]["name"]  # as the replays spell it
    for g in games:
        own, other = (g["score1"], g["score0"]) if g["team"] == 1 else (g["score0"], g["score1"])
        report.meetings.append({"When": nice_day(g["played_at"]), "Map": g["map"] or "—",
                                "Result": {1: "Won", 0: "Lost"}.get(g["won"], "Drew"), "Score": f"{own}–{other}"})
    report.record = (sum(g["won"] == 1 for g in games), sum(g["won"] == 0 for g in games),
                     sum(g["won"] is None for g in games))
    by_map: dict[str, list[int | None]] = {}
    for g in games:
        by_map.setdefault(g["map"] or "—", []).append(g["won"])
    report.maps = [{"Map": m, "Matches": len(r), "Won": sum(x == 1 for x in r), "Lost": sum(x == 0 for x in r),
                    "Win %": round(rate(sum(x == 1 for x in r), sum(x in (0, 1) for x in r)) or 0)}
                   for m, r in sorted(by_map.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
    ids = [g["match_id"] for g in games]
    report.ours = _players(db, ids, me, same_side=True)
    report.theirs = _players(db, ids, me, same_side=False)
    return report
