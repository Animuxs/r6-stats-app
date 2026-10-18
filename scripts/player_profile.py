"""
player_profile.py
=================
What the Players tab shows, worked out from the stats database: one player's profile (their
numbers over every match, recent form, best maps, operators, attack and defense), and two players
side by side (who's ahead on each stat, and their record playing together and against each other).

Every number comes from the same SQL as Ask (ask_engine.METRICS), so a stat here always matches
the answer to the same question on Ask.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ask_engine import DIMS, METRICS, fmt, named_team, rate
from stats_db import StatsDB, nice_day

# the profile's headline numbers, in the order they're shown
HEADLINE = ["eps", "kd", "kpr", "hs", "kost", "survival", "entry", "win_rate"]
# compared side by side: the headline, then the counts that tell how a player plays
COMPARE = HEADLINE + ["dpr", "clutches", "multikills", "trade_kills", "objectives", "matches", "rounds"]
FORM_MATCHES = 10   # recent form: this many of the newest matches
MIN_MAP_MATCHES = 2  # a map counts as a "best map" with at least this many matches on it
MIN_OP_ROUNDS = 5    # an operator counts in "best operators" with at least this many rounds on it


@dataclass
class Profile:
    """One player's numbers over every match they're in."""

    key: str
    name: str
    stats: dict[str, Any]                       # metric key -> value (None when there's nothing to go on)
    form: list[dict[str, Any]] = field(default_factory=list)       # newest first
    maps: list[dict[str, Any]] = field(default_factory=list)       # best first
    operators: list[dict[str, Any]] = field(default_factory=list)  # most played first
    sides: dict[str, dict[str, Any]] = field(default_factory=dict)  # "attack"/"defense" -> numbers
    bests: list[dict[str, Any]] = field(default_factory=list)      # personal bests, see bests()
    opponents: list[dict[str, Any]] = field(default_factory=list)  # their record against each team, most played first
    streak: Streaks | None = None

    @property
    def matches(self) -> int:
        return self.stats.get("matches") or 0

    def show(self, key: str) -> str:
        """A stat as the app shows it ("1.24", "61%", "+3", "—")."""
        return fmt(self.stats.get(key), METRICS[key].kind)


def _match_stats(db: StatsDB, where: str, params: list[Any], keys: list[str]) -> dict[str, Any]:
    select = ", ".join(f"{METRICS[k].match_sql} AS {k}" for k in keys)
    rows = db.query(f"SELECT {select} FROM match_players t JOIN matches m ON m.match_id = t.match_id WHERE {where}",
                    params)
    return dict(rows[0]) if rows else {k: None for k in keys}


def stats_for(db: StatsDB, key: str, match_ids: list[str] | None = None) -> dict[str, Any]:
    """A player's numbers over every match they're in (or only `match_ids`)."""
    where, params = "t.player_key = ?", [key]
    if match_ids is not None:
        where += f" AND t.match_id IN ({', '.join('?' * len(match_ids)) or 'NULL'})"
        params += match_ids
    return _match_stats(db, where, params, COMPARE)


def form(db: StatsDB, key: str, count: int = FORM_MATCHES) -> list[dict[str, Any]]:
    """The player's newest matches, newest first: when, the map, the result and their numbers."""
    rows = db.query(
        """SELECT m.match_id, m.played_at, m.map, m.score0, m.score1, t.team, t.won, t.kills, t.deaths,
                  t.assists, t.rounds, t.rating
           FROM match_players t JOIN matches m ON m.match_id = t.match_id
           WHERE t.player_key = ? ORDER BY m.played_at DESC, m.match_id DESC LIMIT ?""", (key, count))
    out = []
    for r in rows:
        mine, theirs = (r["score0"], r["score1"]) if r["team"] == 0 else (r["score1"], r["score0"])
        result = {1: "Won", 0: "Lost"}.get(r["won"], "Draw")
        out.append({"Played": nice_day(r["played_at"]), "Map": r["map"] or "—",
                    "Result": f"{result} {mine}-{theirs}" if mine is not None and theirs is not None else result,
                    "K-D-A": f"{r['kills']}-{r['deaths']}-{r['assists']}",
                    "K/D": round(r["kills"] / r["deaths"], 2) if r["deaths"] else float(r["kills"]),
                    "EPS": round(100 * r["rating"]), "match_id": r["match_id"]})
    return out


def form_trend(rows: list[dict[str, Any]]) -> str | None:
    """Whether recent form is up or down: the newer half's K/D against the older half's, or None
    with too few matches to say."""
    if len(rows) < 4:
        return None
    half = len(rows) // 2
    newer = sum(r["K/D"] for r in rows[:half]) / half
    older = sum(r["K/D"] for r in rows[-half:]) / half
    if newer >= older * 1.1:
        return "up"
    if newer <= older * 0.9:
        return "down"
    return "steady"


def maps(db: StatsDB, key: str, min_matches: int = MIN_MAP_MATCHES) -> list[dict[str, Any]]:
    """Each map the player has played at least min_matches times, best win rate first (then K/D)."""
    rows = db.query(
        f"""SELECT m.map AS map, {METRICS['matches'].match_sql} AS matches, {METRICS['win_rate'].match_sql} AS win_rate,
                   {METRICS['kd'].match_sql} AS kd, {METRICS['eps'].match_sql} AS eps
            FROM match_players t JOIN matches m ON m.match_id = t.match_id
            WHERE t.player_key = ? AND m.map IS NOT NULL GROUP BY m.map HAVING COUNT(*) >= ?""", (key, min_matches))
    rows.sort(key=lambda r: (-(r["win_rate"] or 0), -(r["kd"] or 0), r["map"]))
    return [{"Map": r["map"], "Matches": r["matches"], "Win %": _round(r["win_rate"], 0), "K/D": _round(r["kd"], 2),
             "EPS": _round(r["eps"], 0)} for r in rows]


def operators(db: StatsDB, key: str, limit: int = 5) -> list[dict[str, Any]]:
    """The operators the player picks most, with how they do on each."""
    rows = db.query(
        f"""SELECT t.operator AS operator, t.side AS side, COUNT(*) AS rounds,
                   {METRICS['win_rate'].round_sql} AS win_rate, {METRICS['kd'].round_sql} AS kd
            FROM round_players t WHERE t.player_key = ? AND t.operator IS NOT NULL
            GROUP BY t.operator ORDER BY rounds DESC, t.operator LIMIT ?""", (key, limit))
    return [{"Operator": r["operator"], "Side": {"attack": "⚔ Attack", "defense": "♜ Defense"}.get(r["side"], ""),
             "Rounds": r["rounds"], "Round win %": _round(r["win_rate"], 0), "K/D": _round(r["kd"], 2)} for r in rows]


def sides(db: StatsDB, key: str) -> dict[str, dict[str, Any]]:
    """The player's attack and defense: rounds, round win %, K/D and KOST on each."""
    rows = db.query(
        f"""SELECT t.side AS side, COUNT(*) AS rounds, {METRICS['win_rate'].round_sql} AS win_rate,
                   {METRICS['kd'].round_sql} AS kd, {METRICS['kost'].round_sql} AS kost
            FROM round_players t WHERE t.player_key = ? AND t.side IN ('attack', 'defense') GROUP BY t.side""", (key,))
    return {r["side"]: {"rounds": r["rounds"], "win_rate": r["win_rate"], "kd": r["kd"], "kost": r["kost"]}
            for r in rows}


@dataclass
class Streaks:
    """Win and loss streaks over a run of results, newest first (1 won, 0 lost, None a draw)."""

    current: int        # the newest result's run: matches in a row with the same result
    current_won: int | None  # what that run is: 1 wins, 0 losses, None a draw
    best_win: int
    best_loss: int
    matches: int


def streaks(results: list[int | None]) -> Streaks:
    """The streaks in `results`, newest first. A draw ends any streak."""
    current = 0
    for r in results:
        if r != results[0] or r is None:
            break
        current += 1
    best_w = best_l = run = 0
    last = None
    for r in reversed(results):
        run = run + 1 if r == last and r is not None else (1 if r is not None else 0)
        last = r
        if r == 1:
            best_w = max(best_w, run)
        elif r == 0:
            best_l = max(best_l, run)
    return Streaks(current, results[0] if results else None, best_w, best_l, len(results))


# each personal best: its label, the SQL that ranks matches (best first), and how it's shown
_BESTS = [
    ("Most kills", "t.kills", lambda r: _count(r["kills"], "kill")),
    ("Best EPS", "t.rating", lambda r: f"{round(100 * r['rating'])} EPS"),
    ("Best K/D", "CASE WHEN t.deaths = 0 THEN t.kills ELSE t.kills * 1.0 / t.deaths END",
     lambda r: f"{r['best']:.2f} ({r['kills']}–{r['deaths']})"),  # "2.67 (16–6)"
    ("Most entry kills", "t.entry_kills", lambda r: _count(r["entry_kills"], "entry kill")),
    ("Most multikill rounds", "t.multikills", lambda r: _count(r["multikills"], "multikill round")),
]
MIN_BEST_ROUNDS = 5  # a match counts for a best with at least this many rounds played (not one quit early)


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def bests(db: StatsDB, key: str) -> list[dict[str, Any]]:
    """The player's best single matches (most kills, best EPS, ...), and their career totals of
    the rare things (aces, clutches): [{"label", "value", "match_id", "when", "map"}]. A best of
    nothing (no entry kills in any match) isn't one, so it's left out."""
    out = []
    for label, order, shown in _BESTS:
        rows = db.query(
            f"""SELECT t.match_id, t.kills, t.deaths, t.entry_kills, t.multikills, t.rating, m.played_at, m.map,
                       {order} AS best
                FROM match_players t JOIN matches m ON m.match_id = t.match_id
                WHERE t.player_key = ? AND t.rounds >= ? ORDER BY best DESC, m.played_at DESC LIMIT 1""",
            (key, MIN_BEST_ROUNDS))
        if rows and rows[0]["best"]:
            r = rows[0]
            out.append({"label": label, "value": shown(r), "match_id": r["match_id"], "when": nice_day(r["played_at"]),
                        "map": r["map"] or ""})
    # the best single round: most kills, the newest if it's happened more than once
    rounds = db.query("SELECT r.round, r.kills, r.operator, m.played_at, m.map FROM round_players r "
                      "JOIN matches m ON m.match_id = r.match_id WHERE r.player_key = ? AND r.kills >= 2 "
                      "ORDER BY r.kills DESC, m.played_at DESC LIMIT 1", (key,))
    if rounds:
        r = rounds[0]
        as_op = f" as {r['operator']}" if r["operator"] else ""
        out.append({"label": "Most kills in a round", "value": f"{_count(r['kills'], 'kill')}{as_op}",
                    "match_id": None, "when": f"{nice_day(r['played_at'])}, round {r['round']}", "map": r["map"] or ""})
    totals = db.query("SELECT SUM(kills >= 5) AS aces, SUM(clutch IS NOT NULL) AS clutches, MAX(clutch) AS biggest "
                      "FROM round_players WHERE player_key = ?", (key,))[0]
    if totals["aces"]:
        out.append({"label": "Aces", "value": _count(totals["aces"], "ace"),
                    "match_id": None, "when": "", "map": ""})
    if totals["clutches"]:
        out.append({"label": "Clutches won", "value": f"{totals['clutches']} (biggest 1v{totals['biggest']})",
                    "match_id": None, "when": "", "map": ""})
    return out


def against(db: StatsDB, key: str) -> list[dict[str, Any]]:
    """The player's record and K/D against each team they've played, by name (not "Team A" or "Blue"),
    most played first."""
    rows = db.query(
        f"""SELECT {DIMS['opponent'].sql} AS opponent, {METRICS['matches'].match_sql} AS matches,
                   {METRICS['wins'].match_sql} AS wins, {METRICS['losses'].match_sql} AS losses,
                   {METRICS['kd'].match_sql} AS kd, {METRICS['eps'].match_sql} AS eps
            FROM match_players t JOIN matches m ON m.match_id = t.match_id
            WHERE t.player_key = ? GROUP BY 1 ORDER BY matches DESC, opponent""", (key,))
    return [{"Opponent": r["opponent"], "Matches": r["matches"], "Record": f"{r['wins']}–{r['losses']}",
             "Win %": _round(rate(r["wins"], r["wins"] + r["losses"]), 0), "K/D": _round(r["kd"], 2),
             "EPS": _round(r["eps"], 0)}
            for r in rows if named_team(r["opponent"])]


# the stats a goal can be set on: the headline ones and the counts that tell how a player plays
GOAL_STATS = ["kd", "eps", "kpr", "hs", "kost", "survival", "entry", "win_rate", "dpr", "clutches", "trade_kills"]
GOAL_MATCHES = 10  # a goal is measured over the newest this many matches


def goal_progress(db: StatsDB, key: str) -> list[dict[str, Any]]:
    """A player's goals, each with their number over the newest GOAL_MATCHES matches and whether it's met:
    [{"key", "label", "target", "value", "shown", "target_shown", "met", "progress" (0-1)}]."""
    key = key.casefold()
    goals = db.goals(key)
    if not goals:
        return []
    recent = [r["match_id"] for r in form(db, key, GOAL_MATCHES)]
    numbers = stats_for(db, key, recent) if recent else {}
    out = []
    for stat, target in goals.items():
        if stat not in METRICS:
            continue
        metric, value = METRICS[stat], numbers.get(stat)
        if value is None:
            met, progress = False, 0.0
        elif metric.higher_is_better:
            met, progress = value >= target, (min(1.0, value / target) if target > 0 else float(value >= target))
        else:
            met, progress = value <= target, (min(1.0, target / value) if value > 0 else 1.0)
        out.append({"key": stat, "label": metric.label, "target": target, "value": value,
                    "shown": fmt(value, metric.kind), "target_shown": fmt(target, metric.kind), "met": met,
                    "progress": progress})
    return out


def profile(db: StatsDB, key: str) -> Profile:
    """Everything the Players tab shows about one player."""
    key = key.casefold()
    name = db.players().get(key, key)
    results = [m["won"] for m in db.match_list(key) if m.get("player")]
    return Profile(key, name, stats_for(db, key), form=form(db, key), maps=maps(db, key), operators=operators(db, key),
                   sides=sides(db, key), bests=bests(db, key), streak=streaks(results) if results else None,
                   opponents=against(db, key))


def _round(value: float | None, digits: int) -> float | int | None:
    if value is None:
        return None
    return round(value) if digits == 0 else round(value, digits)


# ------------------------------------------------------------- two players --
def leader(key: str, a: Any, b: Any) -> int | None:
    """Which of two values is better for this stat: 0, 1, or None for a tie (or nothing to compare). Values that
    look the same ("61%" and "61%", like Ask's "level") are a tie."""
    if a is None or b is None or fmt(a, METRICS[key].kind) == fmt(b, METRICS[key].kind):
        return None
    better_a = a > b if METRICS[key].higher_is_better else a < b
    return 0 if better_a else 1


def comparison(a: Profile, b: Profile, keys: list[str] = COMPARE) -> list[dict[str, Any]]:
    """A row per stat: the label, each player's value as shown, and who's ahead (0, 1 or None)."""
    return [{"key": k, "label": METRICS[k].label, "a": a.show(k), "b": b.show(k),
             "leader": None if k in ("matches", "rounds") else leader(k, a.stats.get(k), b.stats.get(k))}
            for k in keys]


def score(rows: list[dict[str, Any]]) -> tuple[int, int]:
    """How many of the compared stats each player leads."""
    return sum(r["leader"] == 0 for r in rows), sum(r["leader"] == 1 for r in rows)


@dataclass
class Shared:
    """Two players' matches together (same team) and against each other (opposite teams)."""

    together: list[str]            # match ids, newest first
    against: list[str]
    together_record: tuple[int, int]  # wins, losses
    against_record: tuple[int, int]   # a's wins, b's wins

    @property
    def together_win_rate(self) -> float | None:
        return rate(self.together_record[0], sum(self.together_record))


def shared(db: StatsDB, a: str, b: str) -> Shared:
    """The matches two players were both in, split into teammates and opponents."""
    rows = db.query(
        """SELECT x.match_id, x.team = y.team AS same, x.won AS won
           FROM match_players x JOIN match_players y ON y.match_id = x.match_id AND y.player_key = ?
           JOIN matches m ON m.match_id = x.match_id
           WHERE x.player_key = ? ORDER BY m.played_at DESC, m.match_id DESC""", (b.casefold(), a.casefold()))
    together = [r["match_id"] for r in rows if r["same"]]
    against = [r["match_id"] for r in rows if not r["same"]]
    t_won = sum(1 for r in rows if r["same"] and r["won"] == 1)
    t_lost = sum(1 for r in rows if r["same"] and r["won"] == 0)
    a_won = sum(1 for r in rows if not r["same"] and r["won"] == 1)
    b_won = sum(1 for r in rows if not r["same"] and r["won"] == 0)
    return Shared(together, against, (t_won, t_lost), (a_won, b_won))


def verdict(a: Profile, b: Profile, rows: list[dict[str, Any]]) -> str:
    """One line on who comes out ahead, in plain words."""
    lead_a, lead_b = score(rows)
    if not a.matches or not b.matches:
        return "One of them has no matches yet, so there's nothing to compare."
    if lead_a == lead_b:
        return f"Dead even: {a.name} and {b.name} each lead {lead_a} of the stats."
    first, second, lead, trail = (a, b, lead_a, lead_b) if lead_a > lead_b else (b, a, lead_b, lead_a)
    small = min(a.matches, b.matches) < 3
    note = " (few matches so far, so this can change quickly)" if small else ""
    return f"{first.name} leads {lead} of the stats to {second.name}'s {trail}{note}."
