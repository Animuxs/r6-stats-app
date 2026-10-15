"""
sources.py
==========
Where every page's replays come from, so the pages don't each load them their own way:

- the replay source picked on the Dashboard (a folder, a zip, uploads): its matches
  are found once, and each is parsed at most once per session (load_source, parse);
- on this PC, the game's MatchReplay folder when nothing was picked yet (current_source);
- the stats database (open_stats_db). Replays never go into it on their own: a match is
  added only when it's confirmed on the Dashboard, with the players picked there;
- the cleanup of extracted uploads nobody has used for an hour.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

import streamlit as st

import app_info
import parser as replay_parser
from file_guard import ReplayScanner
from parser import ReplayParseError, collect_rec_files, group_by_match, parse_match, replay_source_version
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


def folder_source(path: Path) -> dict[str, Any]:
    """A folder or zip on this PC as the source; found again whenever a replay is added to it."""
    return load_source(("path", str(path), replay_source_version(path)),
                       lambda workdir, scanner: collect_rec_files(path, workdir, scanner))


def current_source() -> dict[str, Any] | None:
    """The replays the pages work with: what the Dashboard loaded (refreshed if it's a folder
    that has gained replays since) or, on this PC, the game's MatchReplay folder."""
    state = st.session_state.get("source")
    if state and state["sig"][0] == "path":
        path = Path(state["sig"][1])
        return folder_source(path) if path.exists() else state
    if state:
        _touch(state["workdir"])
        return state
    if can_read_local_files():
        found = replay_parser.find_replay_folders()
        if found:
            return folder_source(found[0])
    return None


def parse(state: dict[str, Any], name: str):
    """(match, raw, warnings) for one match of the source, or its ReplayParseError; parsed once."""
    if name not in state["parsed"]:
        try:
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
    """The stats database: the user's own on this PC; on the public website, one per visitor
    that's deleted with their uploads once unused for an hour."""
    if not app_info.is_public_host():
        return default_path()
    folder = st.session_state.get("stats_db_folder")
    if not folder or not Path(folder).is_dir():
        folder = st.session_state["stats_db_folder"] = tempfile.mkdtemp(prefix=WORKDIR_PREFIX)
    _touch(folder)
    return Path(folder) / "stats.db"


def open_stats_db() -> StatsDB:
    return StatsDB(stats_db_path())


def no_matches_yet(db: StatsDB) -> None:
    """What a page that reads the stats database says while nothing's in it."""
    waiting = len(db.to_check())
    st.info("No matches yet. Open a match on **Dashboard**, confirm it and pick the players to track, and "
            "it'll show up here."
            + (f" {waiting} match{'es' if waiting != 1 else ''} added by an earlier version "
               f"{'are' if waiting != 1 else 'is'} waiting to be checked on **Match History**." if waiting else ""))
