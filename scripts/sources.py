"""
sources.py
==========
Where every page's replays come from, so the pages don't each load them their own way:

- the replay source picked on the Dashboard (a folder, a zip, uploads): its matches
  are found once, and each is parsed at most once per session; a folder on this PC keeps
  its parsed matches on disk, so each is parsed once per PC (load_source, parse, parse_cache);
- on this PC, the game's MatchReplay folder when nothing was picked yet (current_source);
- the stats database (open_stats_db). Replays never go into it on their own: a match is
  added only when it's confirmed on the Dashboard, with the players picked there;
- the demo season (start_demo, stop_demo): a made-up season in a stats database of its
  own for the session, so every page has something to show and no real stats are touched;
- the cleanup of extracted uploads nobody has used for an hour;
- ReplaySource: one interface over all of them (FolderSource, UploadSource, DemoSource), so the
  pages work with whatever the session's replays are without asking what kind they are.
"""

from __future__ import annotations

import contextlib
import json
from abc import ABC, abstractmethod
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

import streamlit as st

import app_info
import parse_cache
import parser as replay_parser
from file_guard import ReplayScanner
from parser import ReplayParseError, collect_rec_files, group_by_match, parse_match, replay_source_version
from events import save_catalog
from stats_db import StatsDB, default_path, nice_time, played_at_from_folder

WORKDIR_PREFIX = "r6-match-"
WORKDIR_MAX_IDLE = 3600  # seconds; extracted replays are deleted after an hour unused


def sweep_idle_workdirs() -> None:
    """Delete extracted replays nobody has used for an hour, so uploads don't pile
    up on a server (a visitor leaving the page never tells us)."""
    cutoff = time.time() - WORKDIR_MAX_IDLE
    for d in Path(tempfile.gettempdir()).glob(WORKDIR_PREFIX + "*"):
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


@st.cache_resource(show_spinner=False)
def start_workdir_sweeper() -> None:
    """Sweep idle workdirs every 10 minutes for as long as this server runs, even
    when nobody's visiting. Started once per server process."""
    def sweep_forever() -> None:
        while True:
            time.sleep(600)
            sweep_idle_workdirs()

    threading.Thread(target=sweep_forever, name="workdir-sweeper", daemon=True).start()


def can_read_local_files() -> bool:
    """Reading folders on disk only makes sense, and is only safe, when the visitor is on
    the machine running the app -- never on the public website."""
    # looked up on their modules each time, so tests (and nothing else) can stand in for them
    return not app_info.is_public_host() and app_info.is_loopback(st.context.ip_address)


def _touch(path: str | Path) -> None:
    with contextlib.suppress(OSError):
        os.utime(path)  # still in use: keep it from being swept


def load_source(sig: tuple, collect: Callable[[Path, ReplayScanner], list[str]]) -> dict[str, Any]:
    """Find the matches in a source once per source: collect(workdir, scanner) -> .rec
    paths. Zips/uploads are extracted into a workdir that lives as long as the source
    is selected (and is in use), so each match can be parsed only when it's needed."""
    state = st.session_state.get("source")
    if state and state["sig"] == sig:
        _touch(state["workdir"])
        return state
    if state:
        shutil.rmtree(state["workdir"], ignore_errors=True)
    sweep_idle_workdirs()
    workdir = tempfile.mkdtemp(prefix=WORKDIR_PREFIX)
    scanner = ReplayScanner()
    state = {"sig": sig, "workdir": workdir, "parsed": {}}
    try:
        state["groups"] = group_by_match(collect(Path(workdir), scanner))
        if not state["groups"]:
            state["error"] = "No Siege replay files found."
    except ReplayParseError as e:
        state["error"] = str(e)
    state["skipped"] = scanner.summary()
    st.session_state["source"] = state
    return state


def _folder_file() -> Path:
    return default_path().parent / "replay_folder.json"


def saved_replay_folder() -> Path | None:
    """The replay folder picked on the Dashboard, kept on this PC (None if none was picked, or
    on the public website, where folders aren't read)."""
    if not can_read_local_files():
        return None
    try:
        value = json.loads(_folder_file().read_text(encoding="utf-8")).get("path")
    except (OSError, ValueError, AttributeError):
        return None
    return Path(value) if isinstance(value, str) and value.strip() else None


def save_replay_folder(path: Path | None) -> None:
    """Keep the Dashboard's replay folder for next time (None: go back to the one Siege uses)."""
    if not can_read_local_files():
        return
    with contextlib.suppress(OSError):
        if path is None:
            _folder_file().unlink(missing_ok=True)
        else:
            save_catalog({"path": str(path)}, _folder_file())


def preferred_folder() -> Path | None:
    """The folder the pages read replays from: the one picked on the Dashboard while it's still
    there, else the game's own MatchReplay folder."""
    saved = saved_replay_folder()
    if saved and saved.exists():
        return saved
    found = replay_parser.find_replay_folders()
    return found[0] if found else None


def folder_source(path: Path) -> dict[str, Any]:
    """A folder or zip on this PC as the source; found again whenever a replay is added to it."""
    return load_source(("path", str(path), replay_source_version(path)),
                       lambda workdir, scanner: collect_rec_files(path, workdir, scanner))


def current_source() -> dict[str, Any] | None:
    """The replays the pages work with: what the Dashboard loaded (refreshed if it's a folder
    that has gained replays since) or, on this PC, the folder picked there before, or the game's
    MatchReplay folder."""
    state = st.session_state.get("source")
    if state and state["sig"][0] == "path":
        path = Path(state["sig"][1])
        return folder_source(path) if path.exists() else state
    if state:
        _touch(state["workdir"])
        return state
    if can_read_local_files():
        folder = preferred_folder()
        if folder:
            return folder_source(folder)
    return None


def parse(state: dict[str, Any], name: str):
    """(match, raw, warnings) for one match of the source, or its ReplayParseError; parsed once per
    session and, for a folder on this PC, kept on disk so it's parsed once per PC (parse_cache)."""
    if name not in state["parsed"]:
        try:
            if state["sig"][0] == "path" and not app_info.is_public_host():
                state["parsed"][name] = parse_cache.parse(state["groups"][name], parse_match)
            else:
                state["parsed"][name] = parse_match(state["groups"][name])
        except ReplayParseError as e:
            state["parsed"][name] = e
        except Exception as e:  # shown like any unreadable replay, not as a crash
            state["parsed"][name] = ReplayParseError(f"couldn't be read: {e}")
    return state["parsed"][name]


def match_time(name: str) -> str | None:
    """When a match folder's match was played, from its name: "YYYY-MM-DD HH:MM:SS"."""
    return played_at_from_folder(name)


def match_label(name: str, row: dict[str, Any] | None = None) -> str:
    """A match as a person would name it: "Sep 25, 10:55 PM · Bank · Won 4–2" once it's in the
    stats database (StatsDB.match_list's row), else just when it was played."""
    when = (row or {}).get("played_at") or match_time(name)
    bits = [nice_time(when) if when else name]
    if row:
        bits.append(row.get("map") or "")
        if row.get("team") is not None and row.get("score0") is not None:
            own, other = (row["score0"], row["score1"]) if row["team"] == 0 else (row["score1"], row["score0"])
            bits.append(f"{ {1: 'Won', 0: 'Lost'}.get(row.get('won'), 'Draw')} {own}–{other}")
    return " · ".join(b for b in bits if b)


def stats_db_path() -> Path:
    """The stats database: the demo season's while it's on; else the user's own on this PC, and
    on the public website one per visitor that's deleted with their uploads once unused for an hour."""
    if demo_active():
        folder = Path(st.session_state[DEMO_KEY])
        if not (folder / "stats.db").is_file():  # swept after an hour unused: made again
            start_demo()
            folder = Path(st.session_state[DEMO_KEY])
        _touch(folder)
        return folder / "stats.db"
    if not app_info.is_public_host():
        from team_runtime import team_folder
        if folder := team_folder():
            return folder / 'stats.db'
        return default_path()
    from team_runtime import team_folder
    if folder := team_folder():
        return folder / 'stats.db'
    folder = st.session_state.get("stats_db_folder")
    if not folder or not Path(folder).is_dir():
        folder = st.session_state["stats_db_folder"] = tempfile.mkdtemp(prefix=WORKDIR_PREFIX)
    _touch(folder)
    return Path(folder) / "stats.db"


def open_stats_db() -> StatsDB:
    from team_runtime import selected_player
    viewer = selected_player()
    db = StatsDB(stats_db_path())
    db.viewer = viewer
    return db


DEMO_KEY = "demo_season_folder"
_BEFORE_DEMO = "identity_before_demo"  # the league and theme to go back to


def demo_active() -> bool:
    return bool(st.session_state.get(DEMO_KEY))


def start_demo() -> None:
    """Turn the demo season on for this session: its matches go into a stats database of their own
    (in a folder swept like uploads), and the banner shows the demo league, for this session only."""
    import demo_season

    folder = demo_season.database_folder()
    with StatsDB(folder / "stats.db") as db:
        demo_season.load(db)
    old = st.session_state.get(DEMO_KEY)
    if old:
        shutil.rmtree(old, ignore_errors=True)
    if _BEFORE_DEMO not in st.session_state:
        st.session_state[_BEFORE_DEMO] = {k: st.session_state.get(k) for k in ("competition", "appearance")}
    st.session_state[DEMO_KEY] = str(folder)
    # the demo team's league and identity on the banner, for this session only (neither is saved)
    st.session_state["competition"] = dict(demo_season.COMPETITION)
    st.session_state["appearance"] = dict(demo_season.THEME)
    st.session_state["team_choice"] = demo_season.TEAM  # Build a team opens on the demo team


def stop_demo() -> None:
    """Back to the user's own stats and league."""
    folder = st.session_state.pop(DEMO_KEY, None)
    if folder:
        shutil.rmtree(folder, ignore_errors=True)
    st.session_state.pop("team_choice", None)
    before = st.session_state.pop(_BEFORE_DEMO, {})
    for key in ("competition", "appearance"):
        if before.get(key) is None:
            st.session_state.pop(key, None)  # read from this PC again
        else:
            st.session_state[key] = before[key]


def tracker_off() -> str:
    """Why the season tracker can't be saved to here ("" when it can): the demo season is made up,
    and the public website keeps visitors' stats apart."""
    if demo_active():
        return ("Season tracking is off in the demo season: its matches are made up, so nothing goes into a "
                "real season. Leave the demo from the sidebar to track your own.")
    from team_runtime import current_team
    if app_info.is_public_host() and not current_team():
        return ("Season tracking is off on the public website, to keep visitors' stats apart. Use the Windows "
                "app or run the app on your own computer.")
    return ""


def no_matches_yet(db: StatsDB) -> None:
    """What a page that reads the stats database says while nothing's in it."""
    waiting = len(db.to_check())
    st.info("No matches yet. Open a match on **Dashboard**, confirm it and pick the players to track, and "
            "it'll show up here."
            + (f" {waiting} match{'es' if waiting != 1 else ''} added by an earlier version "
               f"{'are' if waiting != 1 else 'is'} waiting to be checked on **Match History**." if waiting else ""))


# ------------------------------------------------------------ replay sources --

class ReplaySource(ABC):
    """Where a session's matches come from: the replays it holds, newest first, each opened
    (parsed) at most once. The Dashboard, Match History, Operator Analytics and Team Analytics
    work with any source the same way; each kind says how it finds and names its matches."""

    @abstractmethod
    def matches(self) -> list[str]:
        """Its matches (replay folder names), newest first."""

    @abstractmethod
    def open(self, name: str) -> tuple[dict, dict, list[str]] | ReplayParseError:
        """(MatchData, raw JSON, warnings) for match `name`, or why it couldn't be read."""

    def has(self, name: str) -> bool:
        return name in self.matches()

    def newest(self) -> str | None:
        found = self.matches()
        return found[0] if found else None

    def round_files(self, name: str) -> int:
        """How many round files match `name` was read from."""
        return 0

    def needs_reading(self, name: str) -> bool:
        """Whether opening match `name` means parsing its replay (slow), not just looking it up."""
        return False

    @property
    def error(self) -> str | None:
        """Why the source couldn't be read at all (None if it could)."""
        return None

    @property
    def skipped(self) -> str | None:
        """The files left out as not replays (None if none were)."""
        return None

    # what the Dashboard shows for this kind of source
    adds_to_stats = True  # its matches can be confirmed into your stats

    def picker_label(self, count: int) -> str:
        return f"{count} matches"

    def describe(self, name: str, row: dict | None) -> str:
        """Match `name` in the Dashboard's picker; `row` is its StatsDB.match_list row, if added."""
        return match_label(name, row) + (" · ✓ added" if row else "")

    def note(self) -> str | None:
        """A line under the picker about where these matches come from."""
        return None


class LoadedSource(ReplaySource):
    """Replays found on disk or uploaded, as load_source keeps them in the session: the state
    dict stays the source of truth, so a rerun or a page reload finds it unchanged."""

    def __init__(self, state: dict[str, Any]):
        self.state = state

    def matches(self) -> list[str]:
        return sorted(self.state.get("groups") or {}, key=lambda n: match_time(n) or n, reverse=True)

    def open(self, name: str):
        return parse(self.state, name)

    def round_files(self, name: str) -> int:
        return len((self.state.get("groups") or {}).get(name, []))

    def needs_reading(self, name: str) -> bool:
        return name not in self.state.get("parsed", {})

    @property
    def error(self) -> str | None:
        return self.state.get("error")

    @property
    def skipped(self) -> str | None:
        return self.state.get("skipped")

    @staticmethod
    def of(state: dict[str, Any] | None) -> "LoadedSource | None":
        """The right kind of source for a session's state dict."""
        if not state:
            return None
        return FolderSource(state) if state["sig"][0] == "path" else UploadSource(state)


class FolderSource(LoadedSource):
    """A folder or zip on this PC: found again whenever the game saves another replay to it."""

    @property
    def path(self) -> Path:
        return Path(self.state["sig"][1])


class UploadSource(LoadedSource):
    """Files uploaded on the Dashboard, extracted to a folder swept an hour after it's last used."""


class DemoSource(ReplaySource):
    """The demo season's made-up matches: already in the demo's stats, so nothing to add."""

    adds_to_stats = False

    def __init__(self):
        import demo_season

        self._season = demo_season.season()

    def matches(self) -> list[str]:
        return sorted(self._season, reverse=True)

    def open(self, name: str):
        return self._season[name], {}, []

    def round_files(self, name: str) -> int:
        return len(self._season[name]["rounds"])

    def picker_label(self, count: int) -> str:
        return f"{count} demo season matches"

    def describe(self, name: str, row: dict | None) -> str:
        return match_label(name, row) + f" · vs {self._season[name]['team_names'][1]}"

    def note(self) -> str:
        return "A made-up match from the demo season. Turn off demo mode in the sidebar to load a real replay."


def replay_source() -> ReplaySource | None:
    """This session's replays: the demo season while it's on, else what the Dashboard loaded or,
    on this PC, the replay folder (None if there's nothing to read)."""
    if demo_active():
        return DemoSource()
    return LoadedSource.of(current_source())
