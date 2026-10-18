"""
recap.py
========
Session recaps: the matches played in one sitting (one evening's scrims, a league night), grouped
by the time between them, with the record, each player's numbers over the session and who stood
out. Match History shows the newest session's recap, and any earlier one.

Numbers come from the same SQL as Ask (ask_engine.METRICS), so they agree with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ask_engine import METRICS
from stats_db import StatsDB, nice_day

GAP = timedelta(hours=4)  # a longer break than this between two matches starts a new session


@dataclass
class Sitting:
    """One session: its matches (newest first) and when it started and ended."""

    match_ids: list[str]
    start: datetime
    end: datetime

    @property
    def label(self) -> str:
        n = len(self.match_ids)
        matches = f"{n} match{'es' if n != 1 else ''}"
        if self.start == datetime.min:
            return f"A match without a date · {matches}"
        hours = f"{self.start:%H:%M}" + (f"–{self.end:%H:%M}" if n > 1 else "")
        return f"{nice_day(f'{self.start:%Y-%m-%d}')} · {matches} · {hours}"


def _when(stamp: str | None) -> datetime | None:
    try:
        return datetime.strptime(stamp or "", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def sittings(db: StatsDB, gap: timedelta = GAP) -> list[Sitting]:
    """Every session, newest first. A match without a time is a session of its own."""
    rows = db.query("SELECT match_id, played_at FROM matches ORDER BY played_at, match_id")
    out: list[Sitting] = []
    for r in rows:
        when = _when(r["played_at"])
        last = out[-1] if out else None
        if when and last and last.end and when - last.end <= gap:
            last.match_ids.insert(0, r["match_id"])
            last.end = when
        else:
            moment = when or datetime.min
            out.append(Sitting([r["match_id"]], moment, moment))
    return out[::-1]


@dataclass
class Recap:
    """What happened in one session."""

    sitting: Sitting
    matches: list[dict[str, Any]]  # each match: map, score, result (from `me`'s side, if `me` played)
    record: tuple[int, int, int]   # me: wins, losses, draws
    players: list[dict[str, Any]]  # a row per player, best EPS first
    standout: str | None           # the player with the best EPS over the session (two matches or more, if any)


def recap(db: StatsDB, sitting: Sitting, me: str | None = None) -> Recap:
    """The recap of one session: its matches, `me`'s record in them and each player's numbers."""
    ids = sitting.match_ids
    marks = ", ".join("?" * len(ids))
    me_key = (me or "").casefold()
    matches = db.query(
        f"""SELECT m.match_id, m.played_at, m.map, m.score0, m.score1, mp.team, mp.won
            FROM matches m LEFT JOIN match_players mp ON mp.match_id = m.match_id AND mp.player_key = ?
            WHERE m.match_id IN ({marks}) ORDER BY m.played_at DESC, m.match_id DESC""", [me_key, *ids])
    shown = []
    for r in matches:
        if r["team"] is None:
            result, score = "—", f"{r['score0']}–{r['score1']}"
        else:
            mine, theirs = (r["score0"], r["score1"]) if r["team"] == 0 else (r["score1"], r["score0"])
            result, score = {1: "Won", 0: "Lost"}.get(r["won"], "Draw"), f"{mine}–{theirs}"
        when = _when(r["played_at"])
        shown.append({"Time": f"{when:%H:%M}" if when else "—", "Map": r["map"] or "—", "Result": result,
                      "Score": score})
    played = [r for r in matches if r["team"] is not None]
    record = (sum(r["won"] == 1 for r in played), sum(r["won"] == 0 for r in played),
              sum(r["won"] is None for r in played))
    keys = ["matches", "kills", "deaths", "kd", "eps", "kost", "entry", "clutches"]
    select = ", ".join(f"{METRICS[k].match_sql} AS {k}" for k in keys)
    # your side's players when you played (the other team's best isn't your session's standout), else everyone
    side = ("AND EXISTS (SELECT 1 FROM match_players s WHERE s.match_id = t.match_id AND s.player_key = ? "
            "AND s.team = t.team)") if played else ""
    rows = db.query(
        f"""SELECT MAX(t.player) AS player, {select}
            FROM match_players t JOIN matches m ON m.match_id = t.match_id
            WHERE t.match_id IN ({marks}) {side} GROUP BY t.player_key""", [*ids, *([me_key] if played else [])])
    rows.sort(key=lambda r: (-(r["eps"] or 0), r["player"].casefold()))
    players = [{"Player": r["player"], "Matches": r["matches"], "K-D": f"{r['kills']}-{r['deaths']}",
                "K/D": round(r["kd"], 2) if r["kd"] is not None else None,
                "EPS": round(r["eps"]) if r["eps"] is not None else None,
                "KOST %": round(r["kost"]) if r["kost"] is not None else None,
                "Entry +/-": r["entry"], "Clutches": r["clutches"]} for r in rows]
    regulars = [p for p in players if p["Matches"] >= 2] or players
    standout = regulars[0]["Player"] if regulars and regulars[0]["EPS"] is not None else None
    return Recap(sitting, shown, record, players, standout)


def headline(r: Recap) -> str:
    """One line about the session, in plain words."""
    wins, losses, draws = r.record
    n = len(r.matches)
    top = f" Top EPS: {r.standout}." if r.standout else ""
    if wins + losses + draws == 0:  # you weren't in these matches
        return f"{n} match{'es' if n != 1 else ''}.{top}"
    if n == 1:
        return f"One match, {'won' if wins else 'lost' if losses else 'drawn'}.{top}"
    tone = "a winning session" if wins > losses else "a losing session" if losses > wins else "an even session"
    record = f"{wins}–{losses}" + (f"–{draws}" if draws else "")
    return f"{record}, {tone}.{top}"  # the session's label already says how many matches
