"""Find a team's matches in the replays: every match where enough of a roster played on one side
that isn't in your stats yet (or is, without some of them), so a team built on Team Analytics can
have all its matches added at once, or picked one by one after a look at each."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import rosters
from season_stats import GENERIC_TEAM_NAMES
from stats_db import NotAdded, StatsDB, nice_time, played_at_from_folder


@dataclass
class Found:
    """One of a team's matches waiting to be added: where its replay is, what a person needs to
    recognise it, and whose stats to keep."""

    source: str                     # the replay folder, as the replay source names it
    played_at: str | None
    map: str
    score: tuple[int, int]          # the team's rounds, then the other team's
    enemy: str
    keep: list[str] = field(default_factory=list)   # the team's players in it (and any kept before)
    new: bool = True                # False: in your stats already, without some of these players

    @property
    def result(self) -> str:
        own, other = self.score
        return "Won" if own > other else "Lost" if own < other else "Draw"

    def summary(self) -> str:
        """"Sep 25, 10:55 PM · Bank · Won 7–4 · vs Ridgeview" (and what adding it changes)."""
        bits = [nice_time(self.played_at) if self.played_at else self.source, self.map,
                f"{self.result} {self.score[0]}–{self.score[1]}", f"vs {self.enemy}"]
        if not self.new:
            bits.append("adds players to a match you have")
        return " · ".join(b for b in bits if b)


def enemy_name(match: dict[str, Any], side: int) -> str:
    """What to call the team on the other side from `side`: the name in the replay, else the
    league team whose roster is on it, else a couple of its players."""
    other = 1 - side
    names = list(match.get("team_names") or []) + ["", ""]
    if names[other].strip().upper() not in GENERIC_TEAM_NAMES:
        return names[other].strip()
    try:
        known = rosters.identify(match).get(other)
    except Exception:  # the league snapshot is a nicety: a match is named without it
        known = None
    if known and known.get("name"):
        return known["name"]
    players = [p["name"] for p in match.get("players") or [] if p.get("team") == other]
    if not players:
        return "the other team"
    return ", ".join(players[:2]) + (f" +{len(players) - 2}" if len(players) > 2 else "")


def team_side(match: dict[str, Any], keys: set[str]) -> tuple[int | None, list[str]]:
    """The side with the most of the team's players (casefolded `keys`) and their names as written
    in the match; (None, []) when none of them played."""
    sides: dict[int, list[str]] = {0: [], 1: []}
    for p in match.get("players") or []:
        if p.get("team") in sides and p["name"].casefold() in keys:
            sides[p["team"]].append(p["name"])
    side = max(sides, key=lambda s: len(sides[s]))
    return (side, sides[side]) if sides[side] else (None, [])


def find(source, db: StatsDB, players: Iterable[str], need: int = 3,
         progress: Callable[[int, int], None] | None = None) -> list[Found]:
    """Every match in `source` (a ReplaySource) with at least `need` of `players` on one side that
    your stats don't have, or have without some of those players; newest first. Replays that can't
    be read are skipped."""
    keys = {p.strip().casefold() for p in players if p and p.strip()}
    if not keys or source is None:
        return []
    need = max(1, min(need, len(keys)))
    names = source.matches()
    found = []
    for number, name in enumerate(names):
        if progress:
            progress(number, len(names))
        parsed = source.open(name)
        if isinstance(parsed, Exception) or not parsed:
            continue
        match = parsed[0]
        side, mine = team_side(match, keys)
        if side is None or len(mine) < need:
            continue
        have = db.added(name)
        kept = have["players"] if have else []
        missing = [n for n in mine if n.casefold() not in {k.casefold() for k in kept}]
        if have and not missing:
            continue
        score = list(match.get("final_score") or [0, 0]) + [0, 0]
        found.append(Found(
            source=name, played_at=played_at_from_folder(name) or match.get("played_at"),
            map=match.get("map") or "", score=(score[side], score[1 - side]), enemy=enemy_name(match, side),
            keep=list(dict.fromkeys(kept + mine)), new=have is None))
    if progress:
        progress(len(names), len(names))
    found.sort(key=lambda f: f.played_at or "", reverse=True)
    return found


def add(source, db: StatsDB, found: Iterable[Found]) -> tuple[list[Found], list[tuple[Found, str]]]:
    """Add each found match to your stats with its players picked: ([added], [(not added, why)])."""
    added, failed = [], []
    for f in found:
        parsed = source.open(f.source) if source is not None and source.has(f.source) else None
        if parsed is None or isinstance(parsed, Exception):
            failed.append((f, "its replay isn't here anymore"))
            continue
        try:
            db.import_match(f.source, parsed[0], files=source.round_files(f.source), players=f.keep)
        except NotAdded as e:
            failed.append((f, str(e)))
        else:
            added.append(f)
    return added, failed
