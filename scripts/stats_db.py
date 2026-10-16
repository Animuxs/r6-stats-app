"""
stats_db.py
===========
A database of the matches you've confirmed: one row per match, per player per
match, and per player per round (Ask, Match History and Build a team read from
it), and the teams built on the Team page, so questions can name them.

Nothing goes in on its own. A match is only added when you confirm it on the
Dashboard, and only the players you pick in it are kept: in Ranked, or a scrim
with someone filling in, a stranger can be on your team, and their numbers would
otherwise count for your team.

Tables (SQLite, in the same file as the season tracker's tables):

    matches        one per match (the replay's matchID): when, map, match type, teams, score,
                   and who recorded the replay ("you")
    match_players  one per player per match: the scoreboard's numbers, EPS rating, won or lost
    round_players  one per player per round: operator, side, site, kills, died, round won
    team_rounds    one per team per round: side, round won, bomb planted, whether the team was
                   ever two or more players down, and whether it then got back to even numbers.
                   Every round of a match counts for both
                   teams, so a player who disconnected doesn't take rounds away from their team.
    rosters        the teams built on the Team page: team name -> players
    review_*       copies of the four match tables, for the matches an earlier version added on
                   its own with every player in them. They don't count until you check them on
                   Match History: keep the players you track, or remove the match.

Every number comes from metrics_engine.compute_match_metrics, exactly what the
scoreboards show. A player's EPS is still worked out against everyone in the
match, picked or not. Adding a match again replaces it (with the players picked
this time), so nothing is ever counted twice.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import season_stats
from metrics_engine import compute_match_metrics

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_id    TEXT PRIMARY KEY,
    source      TEXT NOT NULL,     -- the replay folder, e.g. Match-2026-09-25_22-55-04-8956
    files       INTEGER NOT NULL,  -- round files it was read from (a match being played gains more)
    played_at   TEXT,              -- local time, "YYYY-MM-DD HH:MM:SS"
    map         TEXT,
    match_type  TEXT,
    team0       TEXT,
    team1       TEXT,
    score0      INTEGER,
    score1      INTEGER,
    rounds      INTEGER NOT NULL,
    recorder    TEXT,              -- who recorded the replay: "you"
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS matches_played_at ON matches (played_at);
CREATE INDEX IF NOT EXISTS matches_source ON matches (source);

CREATE TABLE IF NOT EXISTS match_players (
    match_id        TEXT NOT NULL,
    player_key      TEXT NOT NULL,  -- the username, casefolded
    player          TEXT NOT NULL,
    team            INTEGER NOT NULL,
    won             INTEGER,        -- 1 won, 0 lost, NULL a draw
    rounds          INTEGER NOT NULL,
    kills           INTEGER NOT NULL,
    deaths          INTEGER NOT NULL,
    assists         INTEGER NOT NULL,
    headshots       INTEGER NOT NULL,
    entry_kills     INTEGER NOT NULL,
    entry_deaths    INTEGER NOT NULL,
    kost_rounds     INTEGER NOT NULL,
    rounds_survived INTEGER NOT NULL,
    clutches        INTEGER NOT NULL,
    multikills      INTEGER NOT NULL,
    trade_kills     INTEGER NOT NULL,
    traded          INTEGER NOT NULL,  -- deaths a teammate avenged ("dead for trade kill")
    plants          INTEGER NOT NULL,
    defuses         INTEGER NOT NULL,
    rating          REAL NOT NULL,     -- EPS / 100, relative to this match's players
    PRIMARY KEY (match_id, player_key)
);
CREATE INDEX IF NOT EXISTS match_players_player ON match_players (player_key);

CREATE TABLE IF NOT EXISTS round_players (
    match_id    TEXT NOT NULL,
    round       INTEGER NOT NULL,  -- 1, 2, ... in play order
    player_key  TEXT NOT NULL,
    player      TEXT NOT NULL,
    team        INTEGER NOT NULL,
    side        TEXT,              -- 'attack' or 'defense'
    operator    TEXT,
    site        TEXT,
    won         INTEGER,           -- 1 round won, 0 lost
    kills       INTEGER NOT NULL,
    died        INTEGER NOT NULL,
    headshots   INTEGER NOT NULL,
    assists     INTEGER NOT NULL,
    entry_kill  INTEGER NOT NULL,
    entry_death INTEGER NOT NULL,
    traded      INTEGER NOT NULL,
    trade_kills INTEGER NOT NULL,
    planted     INTEGER NOT NULL,
    defused     INTEGER NOT NULL,
    kost        INTEGER NOT NULL,
    clutch      INTEGER,           -- size of a clutch won (1 = 1v1, ...), NULL if none
    PRIMARY KEY (match_id, round, player_key)
);
CREATE INDEX IF NOT EXISTS round_players_player ON round_players (player_key);

CREATE TABLE IF NOT EXISTS team_rounds (
    match_id TEXT NOT NULL,
    round    INTEGER NOT NULL,  -- 1, 2, ... in play order
    team     INTEGER NOT NULL,
    side     TEXT,              -- 'attack' or 'defense'
    won      INTEGER,           -- 1 round won, 0 lost, NULL if the replay doesn't say
    planted  INTEGER NOT NULL,  -- the defuser was planted this round (by the attackers)
    man_down INTEGER NOT NULL,  -- this team was two or more players down at some point
    back_to_even INTEGER NOT NULL,  -- ...and then got back to the same number alive as the other team
    PRIMARY KEY (match_id, round, team)
);

CREATE TABLE IF NOT EXISTS rosters (
    team       TEXT NOT NULL COLLATE NOCASE,
    player     TEXT NOT NULL,
    player_key TEXT NOT NULL,
    PRIMARY KEY (team, player_key)
);

CREATE TABLE IF NOT EXISTS stats_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _insert(table: str, columns: str) -> str:
    """An INSERT naming its columns, so a database made by an earlier version (with a column
    since dropped) still takes new rows."""
    names = columns.split()
    return f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})"


_INSERT_MATCH = _insert("matches", "match_id source files played_at map match_type team0 team1 score0 score1 "
                                   "rounds recorder imported_at")
_INSERT_PLAYER = _insert("match_players", "match_id player_key player team won rounds kills deaths assists "
                                          "headshots entry_kills entry_deaths kost_rounds rounds_survived clutches "
                                          "multikills trade_kills traded plants defuses rating")
_INSERT_ROUND = _insert("round_players", "match_id round player_key player team side operator site won kills "
                                         "died headshots assists entry_kill entry_death traded trade_kills "
                                         "planted defused kost clutch")
_INSERT_TEAM_ROUND = _insert("team_rounds", "match_id round team side won planted man_down back_to_even")
# 2: team_rounds. 3: only confirmed matches and players; what an earlier version added on its own waits in review_*
DATA_VERSION = 3
MATCH_TABLES = ("matches", "match_players", "round_players", "team_rounds")
_PLAYER_TABLES = {"match_players", "round_players"}
# how a round ends once the defuser is down, even when the kill feed missed the plant itself
_PLANTED_ENDINGS = {"DefusedBomb", "DisabledDefuser", "bomb_detonated"}  # the last: the demo match's name
_MATCH_FOLDER_TIME = re.compile(r"Match-(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})")


class NotAdded(ValueError):
    """A match that can't be added, and why (in words to show as they are)."""


def default_path() -> Path:
    """The database file: next to the season tracker's (read at call time, so tests can move it)."""
    return Path(season_stats.DEFAULT_DB_PATH)


def played_at_from_folder(source: str) -> str | None:
    """"Match-2026-09-25_22-55-04-8956" -> "2026-09-25 22:55:04" (the game names folders by local time)."""
    found = _MATCH_FOLDER_TIME.search(source)
    if not found:
        return None
    y, mo, d, h, mi, s = found.groups()
    return f"{y}-{mo}-{d} {h}:{mi}:{s}"


def nice_time(stamp: str | None) -> str:
    """"2026-09-25 22:55:04" -> "Sep 25, 10:55 PM" (with the year when it isn't this year)."""
    try:
        d = datetime.strptime(stamp or "", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return stamp or ""
    return f"{nice_day(stamp)}, {d.hour % 12 or 12}:{d:%M} {'AM' if d.hour < 12 else 'PM'}"


def nice_day(stamp: str | None) -> str:
    """"2026-09-25..." -> "Sep 25" (with the year when it isn't this year); "2026-09" -> "Sep 2026"."""
    text = stamp or ""
    try:
        if len(text) == 7:
            return f"{datetime.strptime(text, '%Y-%m'):%b %Y}"
        d = datetime.strptime(text[:10], "%Y-%m-%d")
    except ValueError:
        return text
    return f"{d:%b} {d.day}" + (f", {d.year}" if d.year != datetime.now().year else "")


def team_rounds(match: dict[str, Any]) -> list[tuple[int, int, str | None, int | None, int, int, int]]:
    """(round, team, side, won, planted, man_down, back_to_even) for both teams in every round of a
    match. Man down: while both teams still had someone alive, the team had two or more fewer
    players alive than the other one (from the start, if it began a round short). Back to even:
    after that, the two teams had the same number alive again, both still in the round."""
    team_of = {p["name"]: p["team"] for p in match.get("players") or []}
    rows = []
    for index, rnd in enumerate(match.get("rounds") or [], 1):
        present = [n for n in (rnd.get("players") or team_of) if n in team_of]
        alive = {side: {n for n in present if team_of[n] == side} for side in (0, 1)}
        down = {0: False, 1: False}
        even = {0: False, 1: False}
        planted = rnd.get("win_condition") in _PLANTED_ENDINGS

        def check() -> None:
            if alive[0] and alive[1]:  # once a team is wiped out the round is over
                for side in (0, 1):
                    even[side] = even[side] or (down[side] and len(alive[side]) == len(alive[1 - side]))
                    down[side] = down[side] or len(alive[side]) <= len(alive[1 - side]) - 2

        check()
        for event in rnd.get("events") or []:
            if event.get("type") == "death" and event.get("actor") in alive[0] | alive[1]:
                alive[team_of[event["actor"]]].discard(event["actor"])
                check()
            elif event.get("type") == "plant":
                planted = True
        attack, winner = rnd.get("attack_team"), rnd.get("winner_team")
        for team in (0, 1):
            rows.append((index, team, None if attack not in (0, 1) else ("attack" if team == attack else "defense"),
                         None if winner not in (0, 1) else int(winner == team), int(planted), int(down[team]),
                         int(even[team])))
    return rows


class StatsDB:
    """The stats database. Use as a context manager, or call close()."""

    def __init__(self, path: str | Path | None = None):
        path = default_path() if path is None else path
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Streamlit may run a session's reruns on different threads; each page opens its own
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._columns: dict[str, list[str]] = {}
        with self._conn:
            self._conn.executescript(SCHEMA)
            for table in MATCH_TABLES:  # the same columns as the table it holds matches for
                self._conn.execute(f"CREATE TABLE IF NOT EXISTS review_{table} AS SELECT * FROM {table} WHERE 0")
                # CREATE TABLE ... AS copies no keys: without this, every lookup by match scans the table
                self._conn.execute(f"CREATE INDEX IF NOT EXISTS review_{table}_match ON review_{table} (match_id)")
            data = self._conn.execute("SELECT value FROM stats_meta WHERE key = 'data'").fetchone()
            if not data or int(data[0]) < 3:  # filled on its own by an earlier version
                self._hold_unconfirmed()
            if not data or int(data[0]) < DATA_VERSION:  # only then: every page (and the dock) opens the database
                self._conn.execute("INSERT OR REPLACE INTO stats_meta VALUES ('data', ?)", (str(DATA_VERSION),))

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "StatsDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------- importing ----

    def import_match(self, source: str, match: dict[str, Any], files: int = 0,
                     players: Iterable[str] | None = None) -> str:
        """Add (or replace) a parsed match (parser.parse_match's MatchData) read from the replay
        folder `source`, keeping only `players`' stats (usernames, any case; None keeps everyone);
        returns its match id. Raises NotAdded for a recording with no players (an aborted one),
        when none of `players` played in it, or when another folder already added holds more of
        the same match (folders holding the same match keep the fuller one, in either order)."""
        match_id = str(match.get("match_id") or "")
        if not match_id or match_id == "unknown":
            match_id = f"folder:{source}"
        rounds = match.get("rounds") or []
        if not match.get("players"):
            raise NotAdded("This recording has no players in it (the match was left while loading).")
        other = self._conn.execute("SELECT source, files, rounds FROM matches WHERE match_id = ? AND source != ?",
                                   (match_id, source)).fetchone()
        if other and other["rounds"] > len(rounds):
            raise NotAdded(f"This match is already in your stats from {other['source']}, which has more of it.")
        stats = compute_match_metrics(match)  # EPS against everyone in the match, picked or not
        keep = None if players is None else {p.strip().casefold() for p in players if p.strip()}
        if keep is not None and not any(n.casefold() in keep and s.rounds_played for n, s in stats.items()):
            raise NotAdded("None of the players you picked played in this match.")
        score = list(match.get("final_score") or [0, 0]) + [0, 0]
        team_names = list(match.get("team_names") or []) + ["", ""]

        def kept(name: str) -> bool:
            return keep is None or name.casefold() in keep

        player_rows = []
        for name, s in stats.items():
            if not s.rounds_played or not kept(name):
                continue
            won = None if score[0] == score[1] else int(score[s.team] > score[1 - s.team])
            player_rows.append((
                match_id, name.casefold(), name, s.team, won, s.rounds_played, s.kills, s.deaths, s.assists,
                s.headshots, s.entry_kills, s.entry_deaths, s.kost_rounds, s.rounds_survived, s.total_clutches,
                s.multikill_rounds, s.trade_kills, s.trades, s.plants, s.defuses, s.rating,
            ))

        # compute_match_metrics gives each player one breakdown per round they were in, in play
        # order; walk the rounds the same way to line each breakdown up with its round
        team_of = {p["name"]: p["team"] for p in match.get("players", [])}
        # a replay without each round's picks still shows who played one operator all match
        only_operator = {p["name"]: p["operator_history"][0] for p in match.get("players", [])
                         if len(p.get("operator_history") or []) == 1}
        breakdowns = {name: iter(s.round_breakdown) for name, s in stats.items()}
        round_rows = []
        for index, rnd in enumerate(rounds, 1):
            attack, winner = rnd.get("attack_team"), rnd.get("winner_team")
            operators = rnd.get("operators") or only_operator
            for name in [n for n in (rnd.get("players") or team_of) if n in team_of]:
                rb = next(breakdowns[name], None)
                if rb is None or not kept(name):
                    continue
                team = team_of[name]
                round_rows.append((
                    match_id, index, name.casefold(), name, team,
                    None if attack not in (0, 1) else ("attack" if team == attack else "defense"),
                    operators.get(name), rnd.get("site") or None,
                    None if winner not in (0, 1) else int(winner == team),
                    rb.kills, rb.deaths, rb.headshots, rb.assists, int(rb.entry_kill), int(rb.entry_death),
                    int(rb.traded), rb.trade_kills, int(rb.planted), int(rb.defused), int(rb.kost),
                    int(rb.clutch[-1]) if rb.clutch else None,
                ))

        now = datetime.now().isoformat(timespec="seconds")
        with self._conn:  # one transaction: a match is either fully in or not at all
            # replace this match, and anything added from the same folder under another id (or the
            # other folder with less of this match), checked or still waiting to be
            for prefix in ("", "review_"):
                old = {r[0] for r in self._conn.execute(
                    f"SELECT match_id FROM {prefix}matches WHERE match_id = ? OR source = ?", (match_id, source))}
                for mid in old | {match_id}:
                    for table in MATCH_TABLES:
                        self._conn.execute(f"DELETE FROM {prefix}{table} WHERE match_id = ?", (mid,))
            self._conn.execute(_INSERT_MATCH, (
                match_id, source, files, played_at_from_folder(source) or match.get("played_at"), match.get("map"),
                match.get("match_type"), team_names[0], team_names[1], score[0], score[1], len(rounds),
                match.get("recording_player"), now,
            ))
            self._conn.executemany(_INSERT_PLAYER, player_rows)
            self._conn.executemany(_INSERT_ROUND, round_rows)
            self._conn.executemany(_INSERT_TEAM_ROUND, [(match_id, *row) for row in team_rounds(match)])
        return match_id

    def remove_match(self, match_id: str) -> None:
        """Take a match out of your stats (or out of the ones waiting to be checked)."""
        with self._conn:
            for prefix in ("", "review_"):
                for table in MATCH_TABLES:
                    self._conn.execute(f"DELETE FROM {prefix}{table} WHERE match_id = ?", (match_id,))

    def added(self, source: str) -> dict[str, Any] | None:
        """The match added from the replay folder `source` (its matches row, and the players kept
        in it as "players": [usernames]), or None if it isn't in your stats."""
        row = self._conn.execute("SELECT * FROM matches WHERE source = ?", (source,)).fetchone()
        if not row:
            return None
        found = dict(row)
        found["players"] = [r["player"] for r in self._conn.execute(
            "SELECT player FROM match_players WHERE match_id = ? ORDER BY team, player COLLATE NOCASE",
            (row["match_id"],))]
        return found

    def players_by_match(self) -> dict[str, list[str]]:
        """{match id: the players kept in it}, each match's in team order."""
        found: dict[str, list[str]] = {}
        for r in self._conn.execute("SELECT match_id, player FROM match_players "
                                    "ORDER BY match_id, team, player COLLATE NOCASE"):
            found.setdefault(r["match_id"], []).append(r["player"])
        return found

    # ----------------------------------------------- waiting to be checked ----

    def _table_columns(self, table: str) -> list[str]:
        if table not in self._columns:
            self._columns[table] = [r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")]
        return self._columns[table]

    def _move(self, match_id: str, to_review: bool, players: set[str] | None = None) -> None:
        """Move a match between the stats and the matches waiting to be checked, with only the
        rows of `players` (casefolded usernames; None: all of them). Call inside a transaction."""
        for table in MATCH_TABLES:
            source, target = (table, f"review_{table}") if to_review else (f"review_{table}", table)
            columns = ", ".join(c for c in self._table_columns(target) if c in self._table_columns(source))
            where, params = "match_id = ?", [match_id]
            if players is not None and table in _PLAYER_TABLES:
                where += f" AND player_key IN ({', '.join('?' * len(players))})"
                params += sorted(players)
            self._conn.execute(f"DELETE FROM {target} WHERE match_id = ?", (match_id,))
            self._conn.execute(f"INSERT INTO {target} ({columns}) SELECT {columns} FROM {source} WHERE {where}",
                               params)
            self._conn.execute(f"DELETE FROM {source} WHERE match_id = ?", (match_id,))

    def _hold_unconfirmed(self) -> None:
        """Once, on opening a database an earlier version filled on its own: its matches have every
        player in them, so they wait to be checked. A match saved to a season was confirmed there:
        it stays, with just the players saved from it."""
        saved: dict[str, set[str]] = {}
        if self._conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'logged_rounds'").fetchone():
            for r in self._conn.execute("SELECT DISTINCT match_id, username FROM logged_rounds"):
                saved.setdefault(r[0], set()).add(r[1].casefold())
        for (match_id,) in self._conn.execute("SELECT match_id FROM matches").fetchall():
            keep = saved.get(match_id, set())
            found = self._conn.execute(
                f"SELECT COUNT(*) FROM match_players WHERE match_id = ? AND player_key IN ({', '.join('?' * len(keep))})",
                (match_id, *keep)).fetchone()[0] if keep else 0
            if not found:
                self._move(match_id, to_review=True)
                continue
            for table in _PLAYER_TABLES:
                self._conn.execute(f"DELETE FROM {table} WHERE match_id = ? AND player_key NOT IN "
                                   f"({', '.join('?' * len(keep))})", (match_id, *keep))

    def to_check(self) -> list[dict[str, Any]]:
        """The matches an earlier version added on its own, newest first, each with its players as
        "players": [(username, team)]. They don't count until they're confirmed or removed."""
        rows = self.query("SELECT * FROM review_matches ORDER BY played_at DESC, match_id")
        players: dict[str, list[tuple[str, int]]] = {}
        for r in self._conn.execute("SELECT match_id, player, team FROM review_match_players "
                                    "ORDER BY team, player COLLATE NOCASE"):
            players.setdefault(r["match_id"], []).append((r["player"], r["team"]))
        for row in rows:
            row["players"] = players.get(row["match_id"], [])
        return rows

    def confirm_checked(self, match_id: str, players: Iterable[str]) -> None:
        """Count a match that was waiting to be checked, with only `players`' stats kept."""
        keep = {p.strip().casefold() for p in players if p.strip()}
        if not keep:
            raise NotAdded("Pick at least one player to keep.")
        found = self._conn.execute(
            f"SELECT COUNT(*) FROM review_match_players WHERE match_id = ? AND player_key IN "
            f"({', '.join('?' * len(keep))})", (match_id, *sorted(keep))).fetchone()[0]
        if not found:
            raise NotAdded("None of those players played in this match.")
        with self._conn:
            self._move(match_id, to_review=False, players=keep)

    # ------------------------------------------------------------ rosters ----

    def save_roster(self, team: str, players: list[str]) -> None:
        """Keep a team built on the Team page (replacing an earlier one with the same name)."""
        team = team.strip()
        with self._conn:
            self._conn.execute("DELETE FROM rosters WHERE team = ?", (team,))
            self._conn.executemany("INSERT OR IGNORE INTO rosters VALUES (?, ?, ?)",
                                   [(team, p.strip(), p.strip().casefold()) for p in players if p.strip()])

    def delete_roster(self, team: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM rosters WHERE team = ?", (team,))

    def rosters(self) -> dict[str, list[str]]:
        """{team: [players]} in the order they were saved."""
        found: dict[str, list[str]] = {}
        for r in self._conn.execute("SELECT team, player FROM rosters ORDER BY team COLLATE NOCASE, rowid"):
            found.setdefault(r["team"], []).append(r["player"])
        return found

    # ------------------------------------------------------------ reading ----

    def query(self, sql: str, params: tuple | list | dict = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.execute(sql, params)]

    def summary(self) -> dict[str, Any]:
        """How much is in the database: matches, players, and the first and last match."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS matches, MIN(played_at) AS first, MAX(played_at) AS last FROM matches"
        ).fetchone()
        players = self._conn.execute("SELECT COUNT(DISTINCT player_key) FROM match_players").fetchone()[0]
        return {"matches": row["matches"], "players": players, "first": row["first"], "last": row["last"]}

    def me(self) -> str | None:
        """The player who recorded most of the replays: the app's user."""
        row = self._conn.execute(
            "SELECT recorder, COUNT(*) AS n FROM matches WHERE recorder IS NOT NULL "
            "GROUP BY recorder COLLATE NOCASE ORDER BY n DESC, MAX(played_at) DESC LIMIT 1"
        ).fetchone()
        return row["recorder"] if row else None

    def players(self) -> dict[str, str]:
        """{player_key: username as last seen} for everyone in the database."""
        rows = self._conn.execute(
            "SELECT mp.player_key, mp.player FROM match_players mp JOIN matches m USING (match_id) "
            "ORDER BY m.played_at"
        )
        return {r["player_key"]: r["player"] for r in rows}  # later matches overwrite earlier spellings

    def distinct(self, column: str) -> list[str]:
        """Every value of matches.map / matches.match_type / round_players.operator / .site in the data."""
        table = {"map": "matches", "match_type": "matches",
                 "operator": "round_players", "site": "round_players"}[column]
        rows = self._conn.execute(f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL ORDER BY 1")
        return [r[0] for r in rows]

    def teammates(self, player: str, limit: int = 4) -> list[tuple[str, int]]:
        """The players who were on `player`'s team most often: [(username, matches together)]."""
        rows = self._conn.execute(
            """SELECT o.player_key, COUNT(*) AS n FROM match_players me
               JOIN match_players o ON o.match_id = me.match_id AND o.team = me.team AND o.player_key != me.player_key
               WHERE me.player_key = ? GROUP BY o.player_key ORDER BY n DESC, o.player_key LIMIT ?""",
            (player.casefold(), limit),
        )
        names = self.players()
        return [(names.get(r["player_key"], r["player_key"]), r["n"]) for r in rows]

    def match_list(self, player: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        """Matches, newest first, with `player`'s result and numbers (if given and in them)."""
        sql = """SELECT m.match_id, m.source, m.played_at, m.map, m.match_type, m.team0, m.team1, m.score0,
                        m.score1, m.rounds, mp.player, mp.team, mp.won, mp.kills, mp.deaths, mp.assists,
                        mp.rounds AS player_rounds, mp.rating
                 FROM matches m LEFT JOIN match_players mp ON mp.match_id = m.match_id AND mp.player_key = ?
                 ORDER BY m.played_at DESC, m.match_id"""
        params: list[Any] = [(player or "").casefold()]
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return self.query(sql, params)

    def team_matches(self, players: list[str], min_players: int = 1) -> list[dict[str, Any]]:
        """The matches where at least min_players of `players` were on one side (the side with the
        most of them), newest first: the match, that side ("team") and its result ("won": 1, 0, or
        None for a draw), and how many of them played ("n")."""
        keys = list(dict.fromkeys(p.strip().casefold() for p in players if p.strip()))
        if not keys:
            return []
        return self.query(
            f"""SELECT m.match_id, m.source, m.played_at, m.map, m.match_type, m.score0, m.score1, s.team, s.n,
                       CASE WHEN m.score0 = m.score1 THEN NULL
                            WHEN s.team = 0 THEN m.score0 > m.score1 ELSE m.score1 > m.score0 END AS won
                FROM (SELECT match_id, team, COUNT(*) AS n, ROW_NUMBER() OVER (PARTITION BY match_id
                      ORDER BY COUNT(*) DESC, team) AS pick
                      FROM match_players WHERE player_key IN ({', '.join('?' * len(keys))}) GROUP BY match_id, team) s
                JOIN matches m ON m.match_id = s.match_id
                WHERE s.pick = 1 AND s.n >= ? ORDER BY m.played_at DESC, m.match_id""",
            keys + [max(1, min_players)])
