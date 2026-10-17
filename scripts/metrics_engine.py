"""
metrics_engine.py
==================
Computes R6 Pro League-style player stats from a normalized MatchData dict
(see parser.py). The per-player scoreboard mirrors the official R6 Esports
match page, one row per player:

    EPS | KD (+/-) | Entry | KOST | KPR | HS | SRV | Clutches | Multikills |
    Objectives | Dead for trade kill | Trade kills

Definitions (public conventions used by Pro League / SiegeGG / stats.cc):

- KD (+/-): kills-deaths and the difference, e.g. "12-8 (+4)".
- Entry: opening kills-opening deaths. The opening duel is the round's
  first death; its killer gets the entry kill, the victim the entry death.
- KOST: % of rounds with a Kill, Objective (plant/defuse), Survival, or
  where the player's death was Traded.
- KPR: kills per round.
- HS: headshot kills / kills.
- SRV: % of rounds survived.
- Clutches: rounds won as the last player alive on the team vs 1+ enemies.
- Multikills: rounds with 2+ kills.
- Objectives: defuser plants + defuser disables.
- Dead for trade kill: deaths that a teammate avenged by killing the
  killer within TRADE_WINDOW_SECONDS.
- Trade kills: kills of an enemy who had just killed a teammate, within
  TRADE_WINDOW_SECONDS of that teammate's death.
- EPS: a performance score centered on 100 for an average player in the
  match. Ubisoft hasn't published its EPS formula, so this one is built
  from the same ingredients (KPR, survival, KOST, entry differential,
  multikills, clutches, objectives, trades), z-scored across the match.

Team kills never count as kills (the victim still gets a death).

Event time: parser.py gives every event an "elapsed" value (seconds since
the round's first event, monotonic), since the in-game clock counts down
and resets when the defuser is planted. The demo match only has "time",
which already counts up.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import dataclass, field

TRADE_WINDOW_SECONDS = 10.0

# pro_league_rows() column order, as shown on the R6 Esports match page
PRO_LEAGUE_COLUMNS = (
    "EPS", "KD (+/-)", "Entry", "KOST", "KPR", "HS", "SRV",
    "Clutches", "Multikills", "Objectives", "Dead for trade kill", "Trade kills",
)

ATTACK, DEFENSE = "attack", "defense"
# crossed swords for attackers, a chess rook (a fortified tower) for defenders.
# U+FE0E asks for the plain text glyph, so both symbols take the page's color.
SIDE_ICONS = {ATTACK: "⚔︎", DEFENSE: "♜"}
SIDE_NAMES = {ATTACK: "Attack", DEFENSE: "Defense"}
SIDE_SHORT = {ATTACK: "ATK", DEFENSE: "DEF"}  # for plain text, where the symbols' widths vary

# r6-dissect's winCondition values (dissect/header.go), and the demo match's
_WIN_CONDITIONS = {
    "KilledOpponents": "Elimination", "kills": "Elimination",
    "DefusedBomb": "Defuser detonated", "bomb_detonated": "Defuser detonated",
    "DisabledDefuser": "Defuser disabled",
    "Time": "Time ran out", "time": "Time ran out",
    "SecuredArea": "Area secured", "ExtractedHostage": "Hostage extracted",
}


def side_label(side: str | None) -> str:
    """"⚔ Attack" / "♜ Defense", or "" when the replay doesn't say which side a team was on."""
    return f"{SIDE_ICONS[side]} {SIDE_NAMES[side]}" if side in SIDE_ICONS else ""


def win_condition_label(condition: str | None) -> str:
    """How a round was won, in words: "KilledOpponents" -> "Elimination"."""
    if not condition or condition == "unknown":
        return ""
    return _WIN_CONDITIONS.get(condition, condition)


def round_eps(eps: float) -> int:
    """An EPS as shown everywhere: the nearest whole number, halves to even (112.5 -> 112,
    103.5 -> 104), once floating-point noise is gone. 100 × 1.035 comes out as 103.49999999999999,
    and the stats database's average of that rating as exactly 103.5: both show 104."""
    return round(round(eps, 6))


def eps_from_rating(rating: float) -> int:
    """EPS is 100 × the rating (1.00 is the match's average player)."""
    return round_eps(100 * rating)


def team_side(team: int, attack_team: int | None) -> str | None:
    """ATTACK or DEFENSE for `team` in a round where `attack_team` attacked; None if unknown."""
    if attack_team not in (0, 1):
        return None
    return ATTACK if team == attack_team else DEFENSE


@dataclass
class RoundBreakdown:
    round_num: int
    side: str | None = None  # ATTACK / DEFENSE; None when the replay doesn't record it
    operator: str | None = None
    won: bool | None = None  # this player's team won the round; None when the replay has no winner
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    survived: bool = True
    entry_kill: bool = False
    entry_death: bool = False
    traded: bool = False  # this player's death was traded ("dead for trade kill")
    trade_kills: int = 0  # kills that traded a teammate's death
    planted: bool = False
    defused: bool = False
    clutch: str | None = None  # e.g. "1v2" -- clutch WON
    clutch_attempt: str | None = None  # e.g. "1v3" -- left alone vs 1+ enemies, won or not
    headshots: int = 0
    kost: bool = False

    def highlights(self) -> list[tuple[str, str]]:
        """What stood out this round, as (label, tone) pairs, tone "pos", "neg" or "" (neutral):
        a multikill ("3K", "ACE"), the opening duel, trades, the defuser and clutches."""
        out = []
        if self.kills >= 2:
            out.append(("ACE" if self.kills >= 5 else f"{self.kills}K", "pos"))
        if self.entry_kill:
            out.append(("Entry kill", "pos"))
        if self.entry_death:
            out.append(("Entry death", "neg"))
        if self.trade_kills:
            out.append(("Trade kill" + (f" ×{self.trade_kills}" if self.trade_kills > 1 else ""), "pos"))
        if self.traded:
            out.append(("Traded", ""))
        if self.planted:
            out.append(("Planted", "pos"))
        if self.defused:
            out.append(("Defused", "pos"))
        if self.clutch:
            out.append((f"Clutch {self.clutch}", "pos"))
        elif self.clutch_attempt:
            out.append((f"Lost {self.clutch_attempt}", ""))
        return out


@dataclass
class PlayerStats:
    name: str
    team: int
    rounds_played: int = 0
    rounds_survived: int = 0
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    headshots: int = 0
    entry_kills: int = 0
    entry_deaths: int = 0
    trades: int = 0  # deaths traded ("dead for trade kill")
    trade_kills: int = 0
    plants: int = 0
    defuses: int = 0
    kost_rounds: int = 0
    clutches: dict = field(default_factory=lambda: {i: 0 for i in range(1, 6)})
    multikill_rounds: int = 0
    round_breakdown: list = field(default_factory=list)
    rating: float = 1.0

    def _per_round(self, n: int) -> float:
        return n / self.rounds_played if self.rounds_played else 0.0

    @property
    def kd(self) -> float:
        return self.kills / self.deaths if self.deaths else float(self.kills)

    @property
    def kost_pct(self) -> float:
        return 100.0 * self._per_round(self.kost_rounds)

    @property
    def srv_pct(self) -> float:
        return 100.0 * self._per_round(self.rounds_survived)

    @property
    def hs_pct(self) -> float:
        return 100.0 * self.headshots / self.kills if self.kills else 0.0

    @property
    def kpr(self) -> float:
        return self._per_round(self.kills)

    @property
    def dpr(self) -> float:
        return self._per_round(self.deaths)

    @property
    def objectives(self) -> int:
        return self.plants + self.defuses

    @property
    def total_clutches(self) -> int:
        return sum(self.clutches.values())

    @property
    def eps(self) -> int:
        return eps_from_rating(self.rating)


def _team_of(players: list[dict]) -> dict[str, int]:
    return {p["name"]: p["team"] for p in players}


def _clutch_label(size: int) -> str:
    return f"1v{min(max(size, 1), 5)}"


def _process_round(rnd: dict, team_of: dict[str, int], stats: dict[str, PlayerStats],
                   only_operator: dict[str, str]) -> None:
    # Events are consumed in feed order, which is chronological (see parser.py).
    round_num = rnd["round_num"]
    winner_team = rnd.get("winner_team")

    # only players present this round (leavers / late joiners in long matches);
    # the demo data has no per-round roster, so it falls back to everyone.
    present = [n for n in (rnd.get("players") or team_of) if n in team_of]
    attackers = rnd.get("attack_team")
    operators = rnd.get("operators") or only_operator
    rb = {
        name: RoundBreakdown(
            round_num=round_num, side=team_side(team_of[name], attackers), operator=operators.get(name),
            won=team_of[name] == winner_team if winner_team in (0, 1) else None,
        )
        for name in present
    }
    alive = {0: set(), 1: set()}
    for name in present:
        alive[team_of[name]].add(name)

    first_death_seen = False
    deaths = []  # (time, victim, killer) in feed order, for trade detection
    kills = []   # (time, killer, victim)
    alone = {}   # side -> (last player alive, enemies alive at that moment)

    for e in rnd["events"]:
        etype, actor = e["type"], e.get("actor")
        t = e.get("elapsed", e.get("time", 0.0))

        if etype == "kill":
            victim = e.get("target")
            if actor not in rb or victim not in rb:
                continue
            if team_of[actor] == team_of[victim]:
                continue  # team kill: no kill credit; the victim's death is its own event
            rb[actor].kills += 1
            rb[actor].headshots += bool(e.get("headshot"))
            kills.append((t, actor, victim))

        elif etype == "death":
            if actor not in rb or rb[actor].deaths:
                continue
            killer = e.get("killed_by")
            rb[actor].deaths = 1
            rb[actor].survived = False
            if not first_death_seen:
                first_death_seen = True
                rb[actor].entry_death = True
                if killer in rb and team_of[killer] != team_of[actor]:
                    rb[killer].entry_kill = True
            deaths.append((t, actor, killer))
            alive[team_of[actor]].discard(actor)
            for side in (0, 1):
                if side not in alone and len(alive[side]) == 1 and alive[1 - side]:
                    alone[side] = (next(iter(alive[side])), len(alive[1 - side]))

        elif etype in ("plant", "defuse"):
            # r6-dissect names the player; if it couldn't, credit the plant/defuse
            # only when exactly one player on the acting side was still alive
            if actor is None and attackers in (0, 1):
                side = attackers if etype == "plant" else 1 - attackers
                if len(alive[side]) == 1:
                    actor = next(iter(alive[side]))
            if actor in rb:
                if etype == "plant":
                    rb[actor].planted = True
                else:
                    rb[actor].defused = True

    # Trades: victim V killed by K; a teammate of V kills K within the window.
    used_kills = set()
    for dt, victim, killer in deaths:
        if killer not in rb or team_of[killer] == team_of[victim]:
            continue
        for i, (kt, avenger, target) in enumerate(kills):
            if target != killer or kt < dt or kt - dt > TRADE_WINDOW_SECONDS:
                continue
            if team_of[avenger] != team_of[victim] or avenger == victim:
                continue
            rb[victim].traded = True
            if i not in used_kills:
                used_kills.add(i)
                rb[avenger].trade_kills += 1
            break

    # Clutch: a side's last player alive vs 1+ enemies; won if that side won the round.
    for side, (name, enemies) in alone.items():
        rb[name].clutch_attempt = _clutch_label(enemies)
        if side == winner_team:
            rb[name].clutch = rb[name].clutch_attempt

    # assists come from r6-dissect's scoreboard read; the kill feed can't give them
    for name, auth in (rnd.get("round_stats") or {}).items():
        if name in rb:
            rb[name].assists = auth["assists"]

    for name, r in rb.items():
        r.kost = bool(r.kills or r.planted or r.defused or r.survived or r.traded)
        s = stats[name]
        s.rounds_played += 1
        s.rounds_survived += r.survived
        s.kills += r.kills
        s.deaths += r.deaths
        s.assists += r.assists
        s.headshots += r.headshots
        s.plants += r.planted
        s.defuses += r.defused
        s.entry_kills += r.entry_kill
        s.entry_deaths += r.entry_death
        s.trades += r.traded
        s.trade_kills += r.trade_kills
        s.kost_rounds += r.kost
        s.multikill_rounds += r.kills >= 2
        if r.clutch:
            s.clutches[int(r.clutch[-1])] += 1
        s.round_breakdown.append(r)


def _compute_ratings(stats: dict[str, PlayerStats]) -> None:
    """z-score each ingredient across the players in this match and
    recenter at 1.00 (EPS = 100 * rating)."""
    players = [p for p in stats.values() if p.rounds_played]
    if not players:
        return

    # (weight, per-player value); a negative weight penalizes
    ingredients = [
        (0.30, lambda p: p.kpr),
        (-0.20, lambda p: p.dpr),
        (0.18, lambda p: p.kost_pct),
        (0.14, lambda p: p._per_round(p.entry_kills - p.entry_deaths)),
        (0.06, lambda p: p._per_round(p.multikill_rounds)),
        (0.05, lambda p: p._per_round(p.total_clutches)),
        (0.04, lambda p: p._per_round(p.objectives)),
        (0.03, lambda p: p._per_round(p.trade_kills)),
    ]
    composite = {p.name: 0.0 for p in players}
    for weight, value in ingredients:
        vals = [value(p) for p in players]
        mean = sum(vals) / len(vals)
        sd = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
        for p, v in zip(players, vals):
            composite[p.name] += weight * (v - mean) / sd
    for p in players:
        # ~0.15 rating points per composite std-dev unit
        p.rating = round(1.00 + 0.15 * composite[p.name], 3)


def compute_match_metrics(match: dict) -> dict[str, PlayerStats]:
    """Main entry point. Returns {player_name: PlayerStats}."""
    team_of = _team_of(match["players"])
    stats = {name: PlayerStats(name=name, team=t) for name, t in team_of.items()}
    # a replay without each round's picks still shows who played one operator all match (as stats_db does)
    only_operator = {p["name"]: p["operator_history"][0] for p in match["players"]
                     if len(p.get("operator_history") or []) == 1}
    for rnd in match["rounds"]:
        _process_round(rnd, team_of, stats, only_operator)
    _compute_ratings(stats)
    return stats


def _diff(a: int, b: int) -> str:
    d = a - b
    return f"{a}-{b} ({'+' if d > 0 else ''}{d})"


def _num(x: float) -> str:
    """0.92 -> "0.92", 1.0 -> "1", 0.5 -> "0.5" (how the Pro League page prints KPR)."""
    return f"{x:.2f}".rstrip("0").rstrip(".")


def pro_league_row(*, team: int, player: str, eps: int, kills: int, deaths: int, entry_kills: int,
                   entry_deaths: int, kost_pct: float, kpr: float, hs_pct: float, srv_pct: float, clutches: int,
                   multikills: int, objectives: int, traded: int, trade_kills: int) -> dict:
    """One player's row in the R6 Esports match-page layout (the 12 columns, formatted)."""
    return {
        "Team": team,
        "Player": player,
        "EPS": eps,
        "KD (+/-)": _diff(kills, deaths),
        "Entry": _diff(entry_kills, entry_deaths),
        "KOST": f"{kost_pct:.0f}%",
        "KPR": _num(kpr),
        "HS": f"{hs_pct:.0f}%",
        "SRV": f"{srv_pct:.0f}%",
        "Clutches": clutches,
        "Multikills": multikills,
        "Objectives": objectives,
        "Dead for trade kill": traded,
        "Trade kills": trade_kills,
    }


def pro_league_rows(stats: dict[str, PlayerStats]) -> list[dict]:
    """One display row per player, in the R6 Esports match-page layout,
    sorted by team then EPS (desc)."""
    return [pro_league_row(
        team=s.team, player=s.name, eps=s.eps, kills=s.kills, deaths=s.deaths, entry_kills=s.entry_kills,
        entry_deaths=s.entry_deaths, kost_pct=s.kost_pct, kpr=s.kpr, hs_pct=s.hs_pct, srv_pct=s.srv_pct,
        clutches=s.total_clutches, multikills=s.multikill_rounds, objectives=s.objectives, traded=s.trades,
        trade_kills=s.trade_kills,
    ) for s in sorted(stats.values(), key=lambda s: (s.team, -s.rating, s.name.lower()))]


def scoreboard_text(match: dict, rows: list[dict]) -> str:
    """Plain-text scoreboard (pro_league_rows), one table per team, like the R6 Esports match page."""
    cols = ("Player",) + PRO_LEAGUE_COLUMNS
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols} if rows else {c: len(c) for c in cols}
    names, score = match["team_names"], match["final_score"]
    out = [f"{match['map']}  |  {names[0]} {score[0]} - {score[1]} {names[1]}  |  {len(match['rounds'])} round(s)"]
    for team in (0, 1):
        out += ["", names[team], "  ".join(c.ljust(widths[c]) for c in cols)]
        out += ["  ".join(str(r[c]).ljust(widths[c]) for c in cols) for r in rows if r["Team"] == team]
    return "\n".join(out)


def leaderboard_rows(stats: dict[str, PlayerStats]) -> list[dict]:
    """Numeric rows (sortable, CSV-friendly), sorted by EPS desc."""
    rows = [{
        "Player": s.name,
        "Team": s.team,
        "EPS": s.eps,
        "Rounds": s.rounds_played,
        "K": s.kills,
        "D": s.deaths,
        "A": s.assists,
        "+/-": s.kills - s.deaths,
        "Entry K": s.entry_kills,
        "Entry D": s.entry_deaths,
        "KOST%": round(s.kost_pct, 1),
        "KPR": round(s.kpr, 2),
        "HS%": round(s.hs_pct, 1),
        "SRV%": round(s.srv_pct, 1),
        "Clutches": s.total_clutches,
        "Multikills": s.multikill_rounds,
        "Plants": s.plants,
        "Defuses": s.defuses,
        "Objectives": s.objectives,
        "Dead for trade kill": s.trades,
        "Trade kills": s.trade_kills,
    } for s in stats.values()]
    rows.sort(key=lambda r: r["EPS"], reverse=True)
    return rows


def side_split(breakdown: list[RoundBreakdown]) -> dict[str, dict]:
    """A player's rounds split by side, attack first: {"attack": {"rounds", "won", "kills",
    "deaths", "operators"}, "defense": {...}}, where "operators" counts the rounds on each
    operator, most played first. Rounds where the replay doesn't record the side are left out."""
    split: dict[str, dict] = {}
    for rb in breakdown:
        if rb.side in SIDE_ICONS:
            s = split.setdefault(rb.side, {"rounds": 0, "won": 0, "kills": 0, "deaths": 0, "operators": Counter()})
            s["rounds"] += 1
            s["won"] += bool(rb.won)
            s["kills"] += rb.kills
            s["deaths"] += rb.deaths
            if rb.operator:
                s["operators"][rb.operator] += 1
    for s in split.values():
        s["operators"] = dict(s["operators"].most_common())
    return {side: split[side] for side in (ATTACK, DEFENSE) if side in split}


def running_scores(match: dict) -> list[tuple[int, int]]:
    """The score after each round, in play order: [(1, 0), (1, 1), ...]."""
    score, out = [0, 0], []
    for rnd in match["rounds"]:
        if rnd.get("winner_team") in (0, 1):
            score[rnd["winner_team"]] += 1
        out.append((score[0], score[1]))
    return out


def round_rows(match: dict, stats: dict[str, PlayerStats]) -> list[dict]:
    """One row per round, numbered from 1 in play order (for the JSON export): which
    team attacked and defended, who won and how, the site, and each player's side,
    operator and result that round."""
    names = match["team_names"]
    scores = running_scores(match)
    played: dict[int, list] = {}
    for s in sorted(stats.values(), key=lambda s: (s.team, s.name.lower())):
        for rb in s.round_breakdown:
            played.setdefault(rb.round_num, []).append((s, rb))
    rows = []
    for number, rnd in enumerate(match["rounds"], 1):
        attackers, winner = rnd.get("attack_team"), rnd.get("winner_team")
        rows.append({
            "round": number,
            "site": rnd.get("site") or "",
            "attack": names[attackers] if attackers in (0, 1) else None,
            "defense": names[1 - attackers] if attackers in (0, 1) else None,
            "winner": names[winner] if winner in (0, 1) else None,
            "win_condition": win_condition_label(rnd.get("win_condition")),
            "score": list(scores[number - 1]),  # after this round, in the order of the match's teams
            "players": [{
                "player": s.name, "team": names[s.team], "side": rb.side, "operator": rb.operator,
                "won": rb.won, "kills": rb.kills, "deaths": rb.deaths, "assists": rb.assists,
                "survived": rb.survived, "highlights": [label for label, _ in rb.highlights()],
            } for s, rb in played.get(rnd["round_num"], [])],
        })
    return rows


def round_player_rows(rounds: list[dict]) -> list[dict]:
    """round_rows flattened to one row per player per round, for the rounds CSV."""
    return [{
        "Round": r["round"], "Site": r["site"], "Player": p["player"], "Team": p["team"],
        "Side": SIDE_NAMES.get(p["side"], ""), "Operator": p["operator"] or "",
        "Result": {True: "Won", False: "Lost"}.get(p["won"], ""),
        "K": p["kills"], "D": p["deaths"], "A": p["assists"], "Survived": "Yes" if p["survived"] else "No",
        "Highlights": "; ".join(p["highlights"]),
    } for r in rounds for p in r["players"]]


def rounds_text(match: dict) -> str:
    """Plain-text list of the match's rounds: each team's side (ATK/DEF) with WIN on the winner,
    the score after the round, how it was won and the site."""
    names = match["team_names"][:2]
    header = ["Round", *names, "Score", "How it was won", "Site"]
    lines = []
    for number, (rnd, score) in enumerate(zip(match["rounds"], running_scores(match)), 1):
        sides = [SIDE_SHORT.get(team_side(team, rnd.get("attack_team")), "-")
                 + (" WIN" if rnd.get("winner_team") == team else "") for team in range(len(names))]
        lines.append([str(number), *sides, f"{score[0]}-{score[1]}",
                      win_condition_label(rnd.get("win_condition")) or "-", rnd.get("site") or "-"])
    widths = [max(len(h), *(len(line[i]) for line in lines)) if lines else len(h) for i, h in enumerate(header)]
    return "\n".join(["Rounds"] + ["  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip()
                                  for row in [header, *lines]])


def rows_csv(rows: list[dict]) -> str:
    """Rows that share their keys (e.g. leaderboard_rows) as CSV text, columns in key order."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]) if rows else [])
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()
