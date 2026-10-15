"""
season_stats.py
===============
Persistent, season-long stat logging for tracked Rainbow Six Siege players.

Pipeline (see SEASON_STATS.md for the full integration guide):

    .rec / match folder
        -> parser.parse_match()                 (r6-dissect JSON -> MatchData)
        -> metrics_engine.compute_match_metrics  (MatchData -> per-player, per-round results)
        -> StatsManager.log_match()              (results -> SQLite season totals)

Storage is a single SQLite file:

    tracked_players  who to record (only these players' stats are ever saved)
    player_totals    running season totals per (season, username)
    logged_rounds    one row per (season, match_id, round_num, username) --
                     its PRIMARY KEY is what makes logging idempotent: a round
                     that was already counted for a player is skipped, so
                     re-importing a replay (or the same match as a folder after
                     importing single rounds) never double-counts.
    player_season_stats  VIEW adding the derived stats (KD, KOST%, entry diff)

Every log_match() call runs in one transaction: a match is either fully
counted or not counted at all.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

if os.environ.get("R6_STATS_DB"):
    DEFAULT_DB_PATH = Path(os.environ["R6_STATS_DB"]).expanduser()
elif os.environ.get("R6_DESKTOP") == "1":
    _local_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    DEFAULT_DB_PATH = _local_data / "R6MatchStats" / "season_stats.db"
else:
    DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "season_stats.db"
DEFAULT_SEASON = "current"
SCHEMA_VERSION = 1
CLUTCH_SIZES = range(1, 6)

# r6-dissect names teams relative to the replay's recorder ("YOUR TEAM" /
# "ENEMY TEAM"), so across a season these would lump every opponent together.
# They're ignored; pin real team names with add_player(..., team=...).
GENERIC_TEAM_NAMES = frozenset({"YOUR TEAM", "ENEMY TEAM", "TEAM A", "TEAM B", "BLUE", "ORANGE", ""})

# every additive per-round counter kept in player_totals, in column order
COUNTER_FIELDS: tuple[str, ...] = (
    "rounds_played",
    "kills", "deaths", "assists", "headshots",
    "entry_kills", "entry_deaths", "trades",
    "plants", "defuses",
    # KOST: kost_rounds counts rounds with ANY of K/O/S/T; the four below count
    # each ingredient separately, so one round can add to several of them.
    "kost_rounds", "kost_kill", "kost_objective", "kost_survive", "kost_traded",
    *(f"clutch_1v{n}" for n in CLUTCH_SIZES),           # clutches won, by size
    *(f"clutch_attempts_1v{n}" for n in CLUTCH_SIZES),  # clutches attempted, by size
)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tracked_players (
    username TEXT PRIMARY KEY COLLATE NOCASE,
    team     TEXT,              -- NULL: use the team name from the match data
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS player_totals (
    season     TEXT NOT NULL,
    username   TEXT NOT NULL COLLATE NOCASE,
    team       TEXT,
    {", ".join(f"{c} INTEGER NOT NULL DEFAULT 0" for c in COUNTER_FIELDS)},
    updated_at TEXT NOT NULL,
    PRIMARY KEY (season, username)
);

CREATE TABLE IF NOT EXISTS logged_rounds (
    season    TEXT NOT NULL,
    match_id  TEXT NOT NULL,
    round_num INTEGER NOT NULL,
    username  TEXT NOT NULL COLLATE NOCASE,
    team      TEXT,
    result    TEXT NOT NULL,    -- JSON RoundResult, for auditing / rebuild_totals()
    logged_at TEXT NOT NULL,
    PRIMARY KEY (season, match_id, round_num, username)
);

-- each tracked player's EPS rating in each logged match. EPS is relative to the
-- other players in the match, so it can't be added up like the counters; the
-- season EPS is the rounds-weighted average of these.
CREATE TABLE IF NOT EXISTS match_ratings (
    season    TEXT NOT NULL,
    match_id  TEXT NOT NULL,
    username  TEXT NOT NULL COLLATE NOCASE,
    rounds    INTEGER NOT NULL,
    rating    REAL NOT NULL,
    PRIMARY KEY (season, match_id, username)
);

CREATE VIEW IF NOT EXISTS player_season_stats AS
SELECT *,
    CASE WHEN deaths > 0 THEN CAST(kills AS REAL) / deaths ELSE CAST(kills AS REAL) END AS kd,
    CASE WHEN rounds_played > 0 THEN 100.0 * kost_rounds / rounds_played ELSE 0.0 END AS kost_pct,
    entry_kills - entry_deaths AS entry_diff,
    CASE WHEN kills > 0 THEN 100.0 * headshots / kills ELSE 0.0 END AS hs_pct,
    {" + ".join(f"clutch_1v{n}" for n in CLUTCH_SIZES)} AS clutches_won,
    {" + ".join(f"clutch_attempts_1v{n}" for n in CLUTCH_SIZES)} AS clutch_attempts
FROM player_totals;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clutch_size(label: str | None) -> int | None:
    """'1v3' -> 3; None/garbage -> None."""
    if not label:
        return None
    try:
        n = int(str(label).split("v")[-1])
    except ValueError:
        return None
    return n if n in CLUTCH_SIZES else None


# --------------------------------------------------------------- models ----

@dataclass
class RoundResult:
    """One player's contribution in one round -- the unit that gets logged.

    Build it from metrics_engine's RoundBreakdown (what log_match does), or
    fill it yourself from any other source of per-round data."""
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    headshots: int = 0
    entry_kill: bool = False
    entry_death: bool = False
    traded: bool = False
    planted: bool = False
    defused: bool = False
    survived: bool = True
    clutch_won: int | None = None      # clutch size won (1-5), None if no clutch won
    clutch_attempt: int | None = None  # clutch size attempted (1-5), None if never 1vX

    @classmethod
    def from_breakdown(cls, rb: Any) -> "RoundResult":
        """Adapt a metrics_engine.RoundBreakdown."""
        won = _clutch_size(rb.clutch)
        attempt = _clutch_size(getattr(rb, "clutch_attempt", None)) or won
        return cls(
            kills=rb.kills, deaths=rb.deaths, assists=rb.assists, headshots=rb.headshots,
            entry_kill=rb.entry_kill, entry_death=rb.entry_death, traded=rb.traded,
            planted=rb.planted, defused=rb.defused, survived=rb.survived,
            clutch_won=won, clutch_attempt=attempt,
        )

    @property
    def objective(self) -> bool:
        return self.planted or self.defused

    @property
    def kost(self) -> bool:
        return bool(self.kills or self.objective or self.survived or self.traded)

    def counters(self) -> dict[str, int]:
        """The increments this round adds to a player's season totals."""
        c = {
            "rounds_played": 1,
            "kills": self.kills, "deaths": self.deaths, "assists": self.assists,
            "headshots": self.headshots,
            "entry_kills": int(self.entry_kill), "entry_deaths": int(self.entry_death),
            "trades": int(self.traded),
            "plants": int(self.planted), "defuses": int(self.defused),
            "kost_rounds": int(self.kost),
            "kost_kill": int(self.kills > 0), "kost_objective": int(self.objective),
            "kost_survive": int(self.survived), "kost_traded": int(self.traded),
        }
        for n in CLUTCH_SIZES:
            c[f"clutch_1v{n}"] = int(self.clutch_won == n)
            c[f"clutch_attempts_1v{n}"] = int(self.clutch_attempt == n)
        return c


@dataclass
class PlayerSeasonStats:
    season: str
    username: str
    team: str | None
    totals: dict[str, int]
    kd: float
    kost_pct: float
    entry_diff: int
    hs_pct: float
    clutches_won: int
    clutch_attempts: int
    rating: float | None = None  # rounds-weighted EPS rating; None for matches logged before EPS was kept
    rated_rounds: int = 0

    @property
    def clutches(self) -> dict[str, int]:
        return {f"1v{n}": self.totals[f"clutch_1v{n}"] for n in CLUTCH_SIZES}

    @property
    def eps(self) -> int | None:
        # metrics_engine.round_eps's rule, here so this module needs nothing else to load
        return None if self.rating is None else round(round(100 * self.rating, 6))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["clutches"] = self.clutches
        d["eps"] = self.eps
        d["kd"], d["kost_pct"], d["hs_pct"] = round(self.kd, 3), round(self.kost_pct, 1), round(self.hs_pct, 1)
        return d


@dataclass
class TeamSeasonStats:
    season: str
    team: str
    players: list[str]
    totals: dict[str, int]
    kd: float
    entry_diff: int
    clutch_success_rate: float | None  # won / attempted, None if no attempts
    kost_avg: float                    # mean of members' season KOST%
    eps: int | None = None             # rounds-weighted over members with a rating
    member_stats: list[PlayerSeasonStats] = field(repr=False, default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "season": self.season, "team": self.team, "players": self.players,
            "totals": self.totals, "kd": round(self.kd, 3), "entry_diff": self.entry_diff,
            "clutch_success_rate": None if self.clutch_success_rate is None
            else round(self.clutch_success_rate, 3),
            "kost_avg": round(self.kost_avg, 1), "eps": self.eps,
        }


@dataclass
class LogResult:
    """What a log_match()/log_round() call actually did."""
    match_id: str
    rounds_logged: int = 0           # (round, player) rows newly counted
    rounds_skipped_duplicate: int = 0
    players_logged: set[str] = field(default_factory=set)
    untracked_players: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


class StatsError(Exception):
    pass


# -------------------------------------------------------------- manager ----

class StatsManager:
    """All season-stat updates and persistence go through this class.

        with StatsManager(season="Y10S3") as sm:
            sm.add_player("Fabian", team="Team Liquid")
            sm.log_match(match)                 # parser.parse_match(...)[0]
            sm.get_player_stats("Fabian")
    """

    def __init__(self, db_path: str | Path | None = None, season: str = DEFAULT_SEASON):
        if db_path is None:  # read now, not when this module loaded, so tests can move it
            db_path = DEFAULT_DB_PATH
        self.db_path = Path(db_path)
        if str(db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.season = season
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    # context manager / lifecycle
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "StatsManager":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------ tracked players ----

    def add_player(self, username: str, team: str | None = None) -> None:
        """Track a player (idempotent). `team` pins their team name; leave it
        None to take the team name from each match's data."""
        username = username.strip()
        if not username:
            raise StatsError("username must not be empty")
        with self._conn:
            self._conn.execute(
                """INSERT INTO tracked_players(username, team, added_at) VALUES (?, ?, ?)
                   ON CONFLICT(username) DO UPDATE SET team = excluded.team""",
                (username, team, _now()),
            )

    def add_players(self, usernames: Iterable[str], team: str | None = None) -> None:
        for u in usernames:
            self.add_player(u, team)

    def remove_player(self, username: str, delete_stats: bool = False) -> None:
        """Stop tracking a player. Their stats so far are kept unless
        delete_stats=True (which removes them from EVERY season)."""
        with self._conn:
            self._conn.execute("DELETE FROM tracked_players WHERE username = ?", (username,))
            if delete_stats:
                self._conn.execute("DELETE FROM player_totals WHERE username = ?", (username,))
                self._conn.execute("DELETE FROM logged_rounds WHERE username = ?", (username,))
                self._conn.execute("DELETE FROM match_ratings WHERE username = ?", (username,))

    def tracked_players(self) -> dict[str, str | None]:
        """{username: pinned team or None}"""
        rows = self._conn.execute("SELECT username, team FROM tracked_players ORDER BY username")
        return {r["username"]: r["team"] for r in rows}

    # ---------------------------------------------------------- logging ----

    def log_match(self, match: dict[str, Any], match_id: str | None = None,
                  players: Iterable[str] | None = None) -> LogResult:
        """Log every round of a normalized MatchData (parser.parse_match /
        parser.parse_replay output) for the tracked players in it.

        `players` limits it to the players confirmed for this match (any case):
        a tracked player who was only a stranger's teammate in Ranked, or on the
        other side of a scrim, isn't logged. None logs every tracked player.

        `match_id` overrides match["match_id"]; it must identify the match
        uniquely, since (match_id, round_num, player) is the dedupe key."""
        from metrics_engine import compute_match_metrics  # local: keeps this module importable alone

        match_id = match_id or match.get("match_id")
        if not match_id or match_id == "unknown":
            raise StatsError(
                "match has no matchID; pass match_id=... explicitly so rounds can be de-duplicated"
            )
        team_names = list(match.get("team_names") or [])
        stats = compute_match_metrics(match)  # EPS against everyone in the match
        if players is not None:
            picked = {p.strip().casefold() for p in players if p.strip()}
            stats = {name: ps for name, ps in stats.items() if name.casefold() in picked}

        # regroup the engine's per-player breakdowns into per-round dicts
        rounds: dict[int, dict[str, RoundResult]] = {}
        team_of: dict[str, str | None] = {}
        for name, ps in stats.items():
            team = team_names[ps.team] if 0 <= ps.team < len(team_names) else None
            team_of[name] = None if (team or "").strip().upper() in GENERIC_TEAM_NAMES else team
            for rb in ps.round_breakdown:
                rounds.setdefault(rb.round_num, {})[name] = RoundResult.from_breakdown(rb)

        result = LogResult(match_id=str(match_id))
        tracked = {u.casefold(): u for u in self.tracked_players()}
        with self._conn:  # one transaction for the whole match
            for round_num in sorted(rounds):
                self._log_round(str(match_id), round_num, rounds[round_num], team_of, result)
            self._conn.executemany(
                "INSERT OR IGNORE INTO match_ratings (season, match_id, username, rounds, rating) VALUES (?, ?, ?, ?, ?)",
                [(self.season, str(match_id), tracked[name.casefold()], ps.rounds_played, ps.rating)
                 for name, ps in stats.items() if name.casefold() in tracked and ps.rounds_played],
            )
        pinned = self.tracked_players()
        no_team = sorted(u for u in result.players_logged if not pinned.get(u) and not self._stored_team(u))
        if no_team:
            result.warnings.append(
                f"match team names {team_names} are generic, so no team was recorded for "
                f"{', '.join(no_team)}; pin one with add_player(name, team=...)"
            )
        return result

    def _stored_team(self, username: str) -> str | None:
        row = self._conn.execute(
            "SELECT team FROM player_totals WHERE season = ? AND username = ?", (self.season, username)
        ).fetchone()
        return row["team"] if row else None

    def log_round(
        self,
        match_id: str,
        round_num: int,
        players: dict[str, RoundResult],
        teams: dict[str, str] | None = None,
    ) -> LogResult:
        """Low-level entry point: log one round from already-computed
        per-player RoundResults. `teams` maps username -> team name."""
        result = LogResult(match_id=str(match_id))
        with self._conn:
            self._log_round(str(match_id), int(round_num), players, teams or {}, result)
        return result

    def _log_round(
        self,
        match_id: str,
        round_num: int,
        players: dict[str, RoundResult],
        team_of: dict[str, str | None],
        result: LogResult,
    ) -> None:
        tracked = {u.casefold(): (u, t) for u, t in self.tracked_players().items()}
        now = _now()
        for name, rr in players.items():
            hit = tracked.get(name.casefold())
            if hit is None:
                result.untracked_players.add(name)
                continue
            username, pinned_team = hit
            team = pinned_team or team_of.get(name)

            cur = self._conn.execute(
                """INSERT OR IGNORE INTO logged_rounds
                   (season, match_id, round_num, username, team, result, logged_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (self.season, match_id, round_num, username, team, json.dumps(asdict(rr)), now),
            )
            if cur.rowcount == 0:  # already counted -> never double count
                result.rounds_skipped_duplicate += 1
                continue
            self._add_to_totals(username, team, rr.counters(), now)
            result.rounds_logged += 1
            result.players_logged.add(username)

    def _add_to_totals(self, username: str, team: str | None, counters: dict[str, int], now: str) -> None:
        cols = ", ".join(COUNTER_FIELDS)
        marks = ", ".join("?" for _ in COUNTER_FIELDS)
        adds = ", ".join(f"{c} = {c} + excluded.{c}" for c in COUNTER_FIELDS)
        self._conn.execute(
            f"""INSERT INTO player_totals (season, username, team, {cols}, updated_at)
                VALUES (?, ?, ?, {marks}, ?)
                ON CONFLICT(season, username) DO UPDATE SET
                    {adds},
                    team = COALESCE(excluded.team, player_totals.team),
                    updated_at = excluded.updated_at""",
            (self.season, username, team, *(counters[c] for c in COUNTER_FIELDS), now),
        )

    def is_round_logged(self, match_id: str, round_num: int, username: str | None = None) -> bool:
        sql = "SELECT 1 FROM logged_rounds WHERE season = ? AND match_id = ? AND round_num = ?"
        args: list[Any] = [self.season, str(match_id), int(round_num)]
        if username:
            sql += " AND username = ?"
            args.append(username)
        return self._conn.execute(sql + " LIMIT 1", args).fetchone() is not None

    def remove_match(self, match_id: str) -> int:
        """Take a match out of this season (a practice match saved by mistake, say): its logged
        rounds and EPS ratings go, and the season's totals are worked out again without it.
        Returns how many player-rounds were removed."""
        with self._conn:
            removed = self._conn.execute("DELETE FROM logged_rounds WHERE season = ? AND match_id = ?",
                                         (self.season, str(match_id))).rowcount
            self._conn.execute("DELETE FROM match_ratings WHERE season = ? AND match_id = ?",
                               (self.season, str(match_id)))
        if removed:
            self.rebuild_totals()
        return removed

    def rebuild_totals(self) -> None:
        """Recompute this season's player_totals from logged_rounds (the
        per-round audit log). Use after manual DB edits or to verify integrity."""
        rows = self._conn.execute(
            "SELECT username, team, result, logged_at FROM logged_rounds WHERE season = ? ORDER BY logged_at",
            (self.season,),
        ).fetchall()
        with self._conn:
            self._conn.execute("DELETE FROM player_totals WHERE season = ?", (self.season,))
            for r in rows:
                rr = RoundResult(**json.loads(r["result"]))
                self._add_to_totals(r["username"], r["team"], rr.counters(), r["logged_at"])

    # ------------------------------------------------------------ reads ----

    _STATS_SQL = """SELECT s.*, r.rating, COALESCE(r.rounds, 0) AS rated_rounds
        FROM player_season_stats s LEFT JOIN (
            SELECT username, SUM(rating * rounds) / SUM(rounds) AS rating, SUM(rounds) AS rounds
            FROM match_ratings WHERE season = ? GROUP BY username COLLATE NOCASE
        ) r ON r.username = s.username COLLATE NOCASE
        WHERE s.season = ?"""

    def _player_from_row(self, row: sqlite3.Row) -> PlayerSeasonStats:
        return PlayerSeasonStats(
            season=row["season"], username=row["username"], team=row["team"],
            totals={c: row[c] for c in COUNTER_FIELDS},
            kd=row["kd"], kost_pct=row["kost_pct"], entry_diff=row["entry_diff"],
            hs_pct=row["hs_pct"], clutches_won=row["clutches_won"],
            clutch_attempts=row["clutch_attempts"],
            rating=row["rating"], rated_rounds=row["rated_rounds"],
        )

    def get_player_stats(self, username: str) -> PlayerSeasonStats | None:
        row = self._conn.execute(
            self._STATS_SQL + " AND s.username = ?", (self.season, self.season, username),
        ).fetchone()
        return self._player_from_row(row) if row else None

    def all_player_stats(self) -> list[PlayerSeasonStats]:
        rows = self._conn.execute(self._STATS_SQL + " ORDER BY s.team, s.username", (self.season, self.season))
        return [self._player_from_row(r) for r in rows]

    def tracked_teams(self) -> dict[str, list[str]]:
        """{team: players} for every tracked player pinned to a team, whether or not any of
        their matches are in this season yet."""
        teams: dict[str, list[str]] = {}
        for r in self._conn.execute("SELECT username, team FROM tracked_players WHERE team IS NOT NULL "
                                    "ORDER BY team COLLATE NOCASE, added_at, username"):
            teams.setdefault(r["team"], []).append(r["username"])
        return teams

    def teams(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT team FROM player_totals WHERE season = ? AND team IS NOT NULL ORDER BY team",
            (self.season,),
        )
        return [r["team"] for r in rows]

    def get_team_stats(self, team: str) -> TeamSeasonStats | None:
        members = [p for p in self.all_player_stats() if p.team and p.team.casefold() == team.casefold()]
        if not members:
            return None
        totals = {c: sum(p.totals[c] for p in members) for c in COUNTER_FIELDS}
        won = sum(p.clutches_won for p in members)
        attempted = sum(p.clutch_attempts for p in members)
        played = [p for p in members if p.totals["rounds_played"]]
        rated = [p for p in members if p.rating is not None and p.rated_rounds]
        rated_rounds = sum(p.rated_rounds for p in rated)
        return TeamSeasonStats(
            season=self.season,
            team=members[0].team,
            players=[p.username for p in members],
            totals=totals,
            kd=totals["kills"] / totals["deaths"] if totals["deaths"] else float(totals["kills"]),
            entry_diff=totals["entry_kills"] - totals["entry_deaths"],
            clutch_success_rate=won / attempted if attempted else None,
            kost_avg=sum(p.kost_pct for p in played) / len(played) if played else 0.0,
            eps=round(round(100 * sum(p.rating * p.rated_rounds for p in rated) / rated_rounds, 6))
            if rated_rounds else None,
            member_stats=members,
        )

    # ------------------------------------------------- export / reset ----

    def export_json(self, path: str | Path | None = None) -> dict[str, Any]:
        """Season snapshot as a dict; also written to `path` if given."""
        data = {
            "season": self.season,
            "exported_at": _now(),
            "tracked_players": self.tracked_players(),
            "rounds_logged": self._conn.execute(
                "SELECT COUNT(DISTINCT match_id || ':' || round_num) FROM logged_rounds WHERE season = ?",
                (self.season,),
            ).fetchone()[0],
            "players": [p.to_dict() for p in self.all_player_stats()],
            "teams": [t.to_dict() for t in (self.get_team_stats(n) for n in self.teams()) if t],
        }
        if path is not None:
            Path(path).write_text(json.dumps(data, indent=2))
        return data

    def reset_season(self, season: str | None = None) -> None:
        """Delete all stats (totals and round log) for a season -- the current
        one by default. The tracked-player list is kept."""
        season = season or self.season
        with self._conn:
            self._conn.execute("DELETE FROM player_totals WHERE season = ?", (season,))
            self._conn.execute("DELETE FROM logged_rounds WHERE season = ?", (season,))
            self._conn.execute("DELETE FROM match_ratings WHERE season = ?", (season,))

    def seasons(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT season FROM logged_rounds UNION SELECT DISTINCT season FROM player_totals"
        )
        return sorted(r[0] for r in rows)

    def match_history(self, players: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """Recorded matches for the selected season, newest first; only those any of `players`
        has rounds in, when given."""
        names = list(players) if players is not None else None
        only = f" AND username IN ({', '.join('?' * len(names))})" if names else ""
        if names == []:
            return []
        rows = self._conn.execute(
            f"""SELECT match_id, MAX(logged_at) AS saved_at,
                       COUNT(DISTINCT round_num) AS rounds,
                       COUNT(DISTINCT username) AS players
                FROM logged_rounds WHERE season = ?{only}
                GROUP BY match_id ORDER BY saved_at DESC, match_id""",
            (self.season, *(names or [])),
        )
        return [dict(row) for row in rows]
