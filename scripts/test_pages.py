"""Every tab, in every situation the app can be in, with every control tried.

The tabs come from app.py's navigation, so a new tab is tested here without anyone adding it. Each
situation is a way the app really runs: a fresh install of the Windows app, the public website, the
demo season, a season with replays and teams (a one-player team, a team without matches, names with
quotes and emoji), the match-day panel hidden, saved planning data that's unreadable. In each, every
tab must open without an exception or an error message; then each choice in every picker, every
checkbox and toggle, and every button is tried in turn, and none may raise.

Run from the repo root:  python -m unittest discover -s scripts"""

from __future__ import annotations

import copy
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamlit.testing.v1 import AppTest

import app_info
import parser as replay_parser
import season_stats

SCRIPTS = Path(__file__).parent
APP = str(SCRIPTS / "app.py")
PAGES = re.findall(r'st\.Page\("(\w+\.py)"', (SCRIPTS / "app.py").read_text(encoding="utf-8"))
TABS = [p for p in PAGES if p != "dock.py"]  # the docked view is its own app (?view=dock), below
# the players and teams of the "season with replays": the awkward names a real season has
ODD_NAMES = ["O'Brien", 'Quote"d', "Ünïcødé", "🔥Blaze", "x" * 40, "Robert'); DROP TABLE players;--"]
CHOICES_PER_WIDGET = 4  # options tried per picker: the first, the last and two between


def _spread(options: list) -> list:
    """Up to CHOICES_PER_WIDGET options, the first and the last among them."""
    if len(options) <= CHOICES_PER_WIDGET:
        return list(options)
    step = (len(options) - 1) / (CHOICES_PER_WIDGET - 1)
    return [options[round(i * step)] for i in range(CHOICES_PER_WIDGET)]


def _season_with_replays(state: dict, folder: Path) -> None:
    """Three matches added to the stats database, the replay folder the Dashboard opened holding
    them (already read), and a season tracker with a full team, a team of one, a team without
    matches and awkward names."""
    from sample_data import SAMPLE_MATCH
    from stats_db import StatsDB

    parsed = {}
    for i in range(3):
        match = copy.deepcopy(SAMPLE_MATCH)
        match.update(match_id=f"page-{i}", played_at=f"2026-09-2{i} 20:00:00", recording_player="Fabian")
        parsed[f"Match-2026-09-2{i}_20-00-00-1"] = (match, {}, [])
    replays = folder / "MatchReplay"
    for name in parsed:
        (replays / name).mkdir(parents=True)
    work = folder / "work"
    work.mkdir()
    state["replay_folder"] = str(replays)
    state["source"] = {"sig": ("path", str(replays), replay_parser.replay_source_version(replays)),
                       "workdir": str(work), "parsed": parsed,
                       "groups": {n: [str(replays / n / f"{n}-R01.rec")] for n in parsed}, "skipped": None}
    players = [p["name"] for p in SAMPLE_MATCH["players"]]
    with StatsDB() as db:
        for name, (match, _raw, _warnings) in parsed.items():
            db.import_match(name, match, files=1)
        db.save_roster("Full Five", players[:5])
        db.save_roster("Odd Names", ODD_NAMES)
    with season_stats.StatsManager(season="current") as tracker:
        tracker.add_players(players[:5], team="Full Five")
        tracker.add_players(players[:1], team="Team of One")
        tracker.add_players(ODD_NAMES, team="Odd 'Names' 🔥")
        for name, (match, _raw, _warnings) in parsed.items():
            tracker.log_match(match, match_id=match["match_id"])


class Situation:
    """A way the app runs: its environment, the visitor, and what's in the session and database."""

    def __init__(self, name: str, env: dict[str, str] | None = None, local: bool = True,
                 demo: bool = False, prepare=None, query: dict[str, str] | None = None, controls_on=None):
        self.name, self.env, self.local, self.demo = name, env or {}, local, demo
        self.prepare, self.query = prepare, query or {}
        # the tabs whose every control is tried (every tab opens either way): all of them, unless the
        # situation only changes some, when the others' controls would repeat another situation's
        self.controls_on = controls_on or TABS

    def start(self, test: unittest.TestCase) -> AppTest:
        """A fresh app in this situation, with a database of its own, ready on the first tab."""
        folder = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, folder, True)
        env = {"LOCALAPPDATA": str(folder), **self.env}
        for patch in (mock.patch.object(season_stats, "DEFAULT_DB_PATH", folder / "stats.db"),
                      mock.patch("branding.DEFAULT_DB_PATH", folder / "stats.db"),
                      mock.patch.object(replay_parser, "find_replay_folders", return_value=[]),
                      mock.patch("rosters.update_on_launch"),  # never online in a test
                      mock.patch.object(app_info, "is_loopback", return_value=self.local),
                      mock.patch("branding.is_loopback", return_value=self.local),
                      mock.patch("app_info.send_window_command"),  # no window to dock
                      mock.patch.dict(os.environ, env)):
            patch.start()
            test.addCleanup(patch.stop)
        at = AppTest.from_file(APP, default_timeout=60)
        if self.demo:
            at.query_params["demo"] = "1"
        for key, value in self.query.items():
            at.query_params[key] = value
        if self.prepare:
            self.prepare(at.session_state, folder)
        return at.run()


def _unreadable_planning(state, folder: Path) -> None:
    (folder / "R6MatchStats").mkdir(exist_ok=True)
    (folder / "R6MatchStats" / "matchday.json").write_text('{"fixtures": "not a list"', encoding="utf-8")


SITUATIONS = [
    Situation("a fresh install of the Windows app", env={"R6_DESKTOP": "1"}),
    Situation("the public website", env={"R6_HOSTED": "1"}, local=False),
    Situation("the demo season", demo=True),
    Situation("a season with replays and teams", env={"R6_DESKTOP": "1"},
              prepare=_season_with_replays),
    Situation("the match-day panel hidden", prepare=lambda state, folder: state.__setitem__("show_matchday", False),
              controls_on=["matchday.py"]),
    Situation("unreadable saved planning data", env={"R6_DESKTOP": "1"}, prepare=_unreadable_planning,
              controls_on=["matchday.py"]),
]


def problems(at: AppTest) -> list[str]:
    return [str(e.value)[:500] for e in at.exception]


def _slug(text: str) -> str:
    return re.sub(r"\W+", "_", text).strip("_")


class EveryTab(unittest.TestCase):
    """Each tab, in each situation: it opens without an exception or an error message, and then every
    choice in every picker, every checkbox and toggle, and every button is tried; none may raise. A
    control that leads to another tab is followed back. (A test class per situation, below, so
    run_tests.py runs the situations side by side.)"""

    def exercise(self, at: AppTest, page: str, situation: str, controls: bool = True) -> None:
        def back() -> AppTest:
            return at.switch_page(page).run()

        def check(what: str) -> None:
            self.assertFalse(problems(at), f"{situation} · {page} · {what}")

        back()
        check("opening it")
        self.assertFalse([e.value for e in at.error], f"{situation} · {page}: an error message on a fresh visit")
        if not controls:
            return
        for kind in ("selectbox", "radio", "segmented_control", "select_slider"):
            for i in range(len(getattr(at, kind))):
                widgets = getattr(at, kind)
                if i >= len(widgets) or widgets[i].disabled:
                    continue
                key, options = widgets[i].key, list(widgets[i].options)
                for option in _spread(options):
                    widget = next((w for w in getattr(at, kind) if w.key == key), None) if key else getattr(at, kind)[i]
                    if widget is None or option not in widget.options:
                        break  # the choice before redrew the tab without it
                    widget.set_value(option).run()
                    check(f"{kind} {widget.label!r} = {option!r}")
                back()
        for kind in ("checkbox", "toggle"):
            for i in range(len(getattr(at, kind))):
                widgets = getattr(at, kind)
                if i >= len(widgets) or widgets[i].disabled:
                    continue
                label = widgets[i].label
                widgets[i].set_value(not widgets[i].value).run()
                check(f"{kind} {label!r}")
                back()
        for key, label in [(b.key, b.label) for b in at.button if not b.disabled]:
            button = next((b for b in at.button if (b.key, b.label) == (key, label)), None)
            if button is None or button.disabled:
                continue
            button.click().run()
            check(f"button {label!r}")
            back()

    def every_tab(self, situation: Situation) -> None:
        at = situation.start(self)
        self.assertFalse(problems(at), situation.name)
        for page in TABS:
            with self.subTest(page=page):
                self.exercise(at, page, situation.name, controls=page in situation.controls_on)
        # and the docked view of the Windows app (?view=dock), a page of its own
        docked = Situation(situation.name, situation.env, situation.local, situation.demo,
                           situation.prepare, {"view": "dock"}).start(self)
        self.assertFalse(problems(docked), f"{situation.name} · docked view")


for _situation in SITUATIONS:
    _name = "TestEveryTab_" + _slug(_situation.name)
    globals()[_name] = type(_name, (EveryTab,), {
        "__doc__": f"Every tab and control in {_situation.name}.",
        "test_every_tab_and_control": lambda self, s=_situation: self.every_tab(s)})


class TestMatchdayTab(unittest.TestCase):
    """Match day: fixtures and planning notes kept per session (and on this PC in the Windows app),
    the demo's apart from yours, and a backup that only restores what's valid."""

    def open(self, situation: Situation) -> AppTest:
        at = situation.start(self)
        return at.switch_page("matchday.py").run()

    def add_fixture(self, at: AppTest, opponent: str) -> AppTest:
        at.text_input(key=next(t.key for t in at.text_input if t.label == "Opponent")).set_value(opponent)
        return next(b for b in at.button if b.label == "Save fixture").click().run()

    def test_a_fixture_needs_an_opponent(self):
        at = self.add_fixture(self.open(SITUATIONS[0]), "   ")
        self.assertFalse(problems(at))
        self.assertTrue(any("Enter an opponent" in e.value for e in at.error))

    def test_fixtures_are_saved_on_this_pc_in_the_windows_app(self):
        at = self.add_fixture(self.open(SITUATIONS[0]), "Metro Tech")
        self.assertFalse(problems(at))
        self.assertTrue(any("Fixture saved" in s.value for s in at.success))
        saved = Path(os.environ["LOCALAPPDATA"]) / "R6MatchStats" / "matchday.json"
        self.assertIn("Metro Tech", saved.read_text(encoding="utf-8"))
        at = next(b for b in at.button if b.label == "Remove fixture").click().run()
        self.assertNotIn("Metro Tech", saved.read_text(encoding="utf-8"))
        self.assertFalse(any(s.label == "Saved fixture" for s in at.selectbox))

    def test_the_website_keeps_them_in_the_session_only(self):
        at = self.add_fixture(self.open(SITUATIONS[1]), "Metro Tech")
        self.assertFalse(problems(at))
        self.assertFalse((Path(os.environ["LOCALAPPDATA"]) / "R6MatchStats" / "matchday.json").exists())
        self.assertIn("Metro Tech", str(at.session_state["matchday_real"]))

    def test_the_demo_has_its_own_fixtures(self):
        at = self.open(SITUATIONS[2])
        self.assertTrue(any("Demo workspace" in i.value for i in at.info))
        fixtures = next(s for s in at.selectbox if s.label == "Saved fixture")
        self.assertEqual(len(fixtures.options), 3)
        self.assertNotIn("matchday_real", at.session_state)

    def test_unreadable_planning_data_is_kept_and_reported(self):
        at = self.open(SITUATIONS[5])
        self.assertFalse(problems(at))
        self.assertTrue(at.warning)
        kept = Path(os.environ["LOCALAPPDATA"]) / "R6MatchStats" / "matchday.json"
        self.assertEqual(kept.read_text(encoding="utf-8"), '{"fixtures": "not a list"')  # never overwritten

    def test_availability_and_notes_are_saved(self):
        at = self.open(SITUATIONS[3])
        roster = [s for s in at.selectbox if str(s.key).startswith("availability:")]
        self.assertTrue(roster)
        roster[0].set_value("Confirmed")
        at.text_area[0].set_value("Retakes on B.")
        at = next(b for b in at.button if b.label == "Save availability and notes").click().run()
        self.assertFalse(problems(at))
        workspace = at.session_state["matchday_real"]
        self.assertEqual(workspace["notes"], "Retakes on B.")
        self.assertIn("Confirmed", workspace["availability"].values())


class TestMatchHistoryActions(unittest.TestCase):
    """What Match History does with the match picked in its table: open it on the Dashboard, or
    remove it from your stats."""

    def pick_first(self) -> AppTest:
        at = SITUATIONS[3].start(self)  # a season with replays and teams
        at.switch_page("history.py").run()
        self.newest = at.dataframe[0].value.iloc[0]
        at.session_state["history_rows"] = {"selection": {"rows": [0], "columns": [], "cells": []}}
        return at.run()

    def test_open_it_on_the_dashboard(self):
        at = next(b for b in self.pick_first().button if b.label == "Open on Dashboard").click().run()
        self.assertFalse(problems(at))
        picker = next(s for s in at.selectbox if s.label.endswith("matches"))
        self.assertEqual(picker.value, "Match-2026-09-22_20-00-00-1")  # the newest, first in the table

    def test_remove_it_from_your_stats(self):
        from stats_db import StatsDB

        at = next(b for b in self.pick_first().button if b.label == "Yes, remove it").click().run()
        self.assertFalse(problems(at))
        self.assertTrue(any(s.value.startswith("Removed") for s in at.success))
        with StatsDB() as db:
            self.assertEqual(db.summary()["matches"], 2)
        self.assertEqual(len(at.dataframe[0].value), 2)


class TestPlayersTab(unittest.TestCase):
    """Players: one player's profile, then a second player under Compare with."""

    def test_profile_then_compare(self):
        at = SITUATIONS[3].start(self)  # a season with replays and teams
        at.switch_page("players.py").run()
        self.assertFalse(problems(at))
        self.assertEqual([m.label for m in at.metric][:2], ["EPS", "K/D"])
        at.selectbox(key="players_second").select_index(1).run()
        self.assertFalse(problems(at))
        table = at.dataframe[0].value
        self.assertEqual(table.columns[0], "Stat")
        self.assertTrue(table.iloc[:, 1:].apply(lambda col: col.str.startswith("✅")).any().any())
        self.assertTrue(any(m.label == "Matches as teammates" for m in at.metric))


if __name__ == "__main__":
    unittest.main()
