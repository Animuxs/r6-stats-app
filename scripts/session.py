"""
session.py
==========
One browser session's shared state, as one object every tab reads and writes: the season, your
league and team, the demo season, the replays, the match to open, and the notes a tab leaves for
itself across a rerun. It's what makes the tabs one program: Match History asks it to open a match
and the Dashboard finds it there; League & Team names the season and Team Analytics uses it.

It keeps everything in Streamlit's st.session_state under the same keys as before, so a page
reload, the tests and anything else that reads those keys see the same values. Only this class
should spell them out.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from contextlib import AbstractContextManager
from typing import Any

import streamlit as st

import sources
from branding import current_competition
from season_stats import DEFAULT_SEASON, StatsManager
from stats_db import StatsDB

_SEASON = "r6_season"
_LAST_MATCH = "r6_last_match"
_OPEN_MATCH = "open_match"
_TEAM_CHOICE = "team_choice"


class Session:
    """The current browser session (or, in tests, any mapping standing in for it)."""

    def __init__(self, state: MutableMapping[str, Any] | None = None):
        self._state = st.session_state if state is None else state

    # ------------------------------------------------------------ season ----
    @property
    def season(self) -> str:
        """The season the tracker saves to: picked on any page, else the one named after your
        league on League & Team, else "current"."""
        if _SEASON not in self._state:
            named = current_competition()["season"]
            if named:
                self._state[_SEASON] = named
        return self._state.get(_SEASON) or DEFAULT_SEASON

    @season.setter
    def season(self, value: str) -> None:
        self._state[_SEASON] = value.strip() or DEFAULT_SEASON

    def tracker(self, season: str | None = None) -> StatsManager:
        """The season tracker, for `season` or the session's."""
        from team_runtime import current_team
        if current_team() or self.demo:
            return StatsManager(db_path=sources.stats_db_path(), season=season or self.season)
        return StatsManager(season=season or self.season)

    @property
    def tracker_off(self) -> str:
        """Why the season tracker can't be saved to here ("" when it can)."""
        return sources.tracker_off()

    # ------------------------------------------------------------- stats ----
    def stats(self) -> AbstractContextManager[StatsDB]:
        """The stats database this session works with: the demo season's while it's on, a
        visitor's own on the public website, else yours."""
        return sources.open_stats_db()

    @property
    def demo(self) -> bool:
        return sources.demo_active()

    @property
    def replays(self) -> sources.ReplaySource | None:
        """Where this session's matches come from: the demo season while it's on, else what the
        Dashboard loaded or, on this PC, your replay folder (None if there's nothing)."""
        return sources.replay_source()

    # ------------------------------------------------------------ matches ---
    def open_on_dashboard(self, name: str) -> None:
        """Ask the Dashboard to open the match from replay folder `name` next time it's shown."""
        self._state[_OPEN_MATCH] = name

    def match_to_open(self) -> str | None:
        """The match another tab asked the Dashboard to open, once (None if none was)."""
        return self._state.pop(_OPEN_MATCH, None)

    @property
    def last_match(self) -> dict[str, Any] | None:
        """The match last shown on the Dashboard (Operator Analytics' "This match")."""
        return self._state.get(_LAST_MATCH)

    @last_match.setter
    def last_match(self, match: dict[str, Any]) -> None:
        self._state[_LAST_MATCH] = match

    # -------------------------------------------------------------- teams ---
    @property
    def competition(self) -> dict[str, str]:
        """Your league or tournament and team, as saved on League & Team."""
        return current_competition()

    @property
    def team_choice(self) -> str | None:
        """The team picked on Team Analytics' Build a team."""
        return self._state.get(_TEAM_CHOICE)

    @team_choice.setter
    def team_choice(self, team: str) -> None:
        self._state[_TEAM_CHOICE] = team

    # -------------------------------------------------------------- notes ---
    def leave_note(self, tab: str, kind: str, text: str) -> None:
        """A message for `tab` to show at the top of its next run ("success", "error", ...): what a
        button's callback did, shown after the rerun it causes."""
        self._state[f"{tab}_note"] = (kind, text)

    def take_note(self, tab: str) -> tuple[str, str] | None:
        return self._state.pop(f"{tab}_note", None)

    # ------------------------------------------------------- plain values ---
    @property
    def matchday(self):
        from matchday_data import MatchdayWorkspace, demo_workspace
        from app_info import desktop_data_dir, is_windows_app
        import json
        from team_runtime import current_team, store
        if not self.demo and current_team():
            value, revision = store().document(self.get('team_token'), 'planning')
            self.setdefault('team_planning_revision', revision)
            return MatchdayWorkspace.from_dict(value)
        key = "matchday_demo" if self.demo else "matchday_real"
        if key not in self._state:
            value = demo_workspace().to_dict() if self.demo else {}
            if not self.demo and is_windows_app():
                try:
                    value = json.loads((desktop_data_dir() / "matchday.json").read_text(encoding="utf-8"))
                except FileNotFoundError:
                    pass
                except (OSError, ValueError):
                    self.leave_note("matchday", "warning", "Could not read saved planning data. Export a backup before replacing it.")
            try:
                self._state[key] = MatchdayWorkspace.from_dict(value).to_dict()
            except ValueError:
                self._state[key] = MatchdayWorkspace().to_dict()
                self.leave_note("matchday", "warning", "Saved planning data is invalid; the original file has been kept.")
        return MatchdayWorkspace.from_dict(self._state[key])

    def save_matchday(self, workspace) -> None:
        from matchday_data import MatchdayWorkspace
        from app_info import desktop_data_dir, is_windows_app
        import json
        import os
        import tempfile
        value = MatchdayWorkspace.from_dict(workspace.to_dict()).to_dict()
        from team_runtime import current_team, store
        if not self.demo and current_team():
            store().save_document(self.get('team_token'), 'planning', value,
                                  self.get('team_planning_revision', 0))
            self.set('team_planning_revision', self.get('team_planning_revision', 0) + 1)
            return
        if not self.demo and is_windows_app():
            folder = desktop_data_dir()
            folder.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, delete=False) as file:
                    temporary = file.name
                    json.dump(value, file, ensure_ascii=False)
                os.replace(temporary, folder / "matchday.json")
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
        self._state["matchday_demo" if self.demo else "matchday_real"] = value

    def get(self, key: str, default: Any = None) -> Any:
        """A tab's own value (a widget's key, say), for what's only that tab's business."""
        return self._state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._state[key] = value

    def pop(self, key: str, default: Any = None) -> Any:
        return self._state.pop(key, default)

    def setdefault(self, key: str, value: Any) -> Any:
        if key not in self._state:
            self._state[key] = value
        return self._state[key]

    def has(self, key: str) -> bool:
        return key in self._state

    def drop(self, prefix: str) -> None:
        """Forget every value whose key starts with `prefix` (a group of a tab's widgets)."""
        for key in [k for k in self._state if str(k).startswith(prefix)]:
            del self._state[key]
