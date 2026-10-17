"""The app's shared structure: the Session every tab reads and writes, the ReplaySource kinds the
tabs use without asking which kind they have, the Tab base class, and the links between tabs."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamlit.testing.v1 import AppTest

import app_info
import branding
import season_stats
import sources
from session import Session
from tab import Tab

APP = str(Path(__file__).with_name("app.py"))


class TestSession(unittest.TestCase):
    def setUp(self):
        self.state = {}
        self.session = Session(self.state)

    def test_the_season_defaults_to_the_one_named_after_your_league(self):
        with mock.patch("session.current_competition", return_value=dict(branding.DEFAULT_COMPETITION, season="Fall Cup")):
            self.assertEqual(self.session.season, "Fall Cup")
        self.assertEqual(self.state["r6_season"], "Fall Cup")  # the same key every page used before
        self.session.season = "  "
        self.assertEqual(self.session.season, "current")

    def test_without_a_named_season_it_is_current(self):
        with mock.patch("session.current_competition", return_value=dict(branding.DEFAULT_COMPETITION)):
            self.assertEqual(self.session.season, "current")

    def test_a_match_to_open_is_handed_over_once(self):
        self.session.open_on_dashboard("Match-2026-10-01_20-00-00-1")
        self.assertEqual(self.session.match_to_open(), "Match-2026-10-01_20-00-00-1")
        self.assertIsNone(self.session.match_to_open())

    def test_notes_are_kept_per_tab_until_shown(self):
        self.session.leave_note("history", "success", "Removed it.")
        self.assertIsNone(self.session.take_note("league"))
        self.assertEqual(self.session.take_note("history"), ("success", "Removed it."))
        self.assertIsNone(self.session.take_note("history"))

    def test_drop_forgets_a_group_of_values(self):
        self.state.update({"in_season_S1_a": True, "in_season_S1_b": False, "other": 1})
        self.session.drop("in_season_S1_")
        self.assertEqual(self.state, {"other": 1})


class TestReplaySources(unittest.TestCase):
    STATE = {"sig": ("upload", ("a.zip", 1, "x")), "workdir": "", "parsed": {},
             "groups": {"Match-2026-09-01_20-00-00-1": ["r1", "r2"], "Match-2026-10-01_20-00-00-1": ["r1"]},
             "skipped": None}

    def test_the_kind_comes_from_the_state(self):
        self.assertIsInstance(sources.LoadedSource.of(dict(self.STATE)), sources.UploadSource)
        folder = sources.LoadedSource.of(dict(self.STATE, sig=("path", "C:/replays", 1)))
        self.assertIsInstance(folder, sources.FolderSource)
        self.assertEqual(folder.path, Path("C:/replays"))
        self.assertIsNone(sources.LoadedSource.of(None))

    def test_every_kind_answers_the_same_questions(self):
        for source in (sources.LoadedSource.of(dict(self.STATE, parsed={})), sources.DemoSource()):
            with self.subTest(kind=type(source).__name__):
                self.assertIsInstance(source, sources.ReplaySource)
                names = source.matches()
                self.assertEqual(names, sorted(names, reverse=True))  # newest first
                self.assertEqual(source.newest(), names[0])
                self.assertTrue(source.has(names[0]))
                self.assertFalse(source.has("Match-1999-01-01_00-00-00-0"))
                self.assertGreater(source.round_files(names[0]), 0)
                self.assertIn(str(len(names)), source.picker_label(len(names)))
                self.assertTrue(source.describe(names[0], None))

    def test_only_real_replays_are_added_to_your_stats(self):
        self.assertTrue(sources.LoadedSource.of(dict(self.STATE)).adds_to_stats)
        demo = sources.DemoSource()
        self.assertFalse(demo.adds_to_stats)
        self.assertFalse(demo.needs_reading(demo.newest()))  # already in memory
        match, raw, warnings = demo.open(demo.newest())
        self.assertEqual((raw, warnings), ({}, []))
        self.assertIn("vs ", demo.describe(demo.newest(), None))

    def test_a_loaded_source_reads_each_match_once(self):
        state = dict(self.STATE, parsed={})
        source = sources.LoadedSource.of(state)
        name = source.newest()
        self.assertTrue(source.needs_reading(name))
        with mock.patch("sources.parse_match", return_value=({"x": 1}, {}, [])) as parse:
            source.open(name)
            source.open(name)
        parse.assert_called_once()
        self.assertFalse(source.needs_reading(name))


class TestTabBase(unittest.TestCase):
    def test_a_tab_must_say_what_is_on_it(self):
        with self.assertRaises(TypeError):
            Tab(Session({}))  # render() is abstract

        class Empty(Tab):
            title, name = "Empty", "empty"

            def render(self):
                self.rendered = True
        tab = Empty(Session({"empty_note": ("info", "hello")}))
        with mock.patch("tab.st") as st:
            tab.run()
        st.title.assert_called_once_with("Empty")
        st.info.assert_called_once()  # the note left for it
        self.assertTrue(tab.rendered)


class TestTabsTogether(unittest.TestCase):
    """The tabs work as one program on the same session: the demo season reaches every one of them."""

    def setUp(self):
        folder = Path(tempfile.mkdtemp())
        for patch in (mock.patch.object(season_stats, "DEFAULT_DB_PATH", folder / "stats.db"),
                      mock.patch.object(app_info, "is_loopback", return_value=True)):
            patch.start()
            self.addCleanup(patch.stop)
        with mock.patch.dict(os.environ, {"R6_DESKTOP": "1"}):
            self.at = AppTest.from_file(APP, default_timeout=60)
            self.at.query_params["demo"] = "1"
            self.at.run()

    def test_a_match_another_tab_opens_shows_on_the_dashboard(self):
        at = self.at.switch_page("history.py").run()
        import demo_season
        oldest = sorted(demo_season.season())[0]
        Session(at.session_state).open_on_dashboard(oldest)  # what Match History's "Open on Dashboard" does
        at = at.switch_page("report.py").run()
        picker = next(s for s in at.selectbox if s.label.endswith("demo season matches"))
        self.assertEqual(picker.value, oldest)

    def test_operator_analytics_shows_a_demo_match(self):
        at = self.at.switch_page("operators.py").run()
        at = at.segmented_control[0].set_value("This match").run()
        self.assertFalse(at.exception)
        self.assertFalse(any("No matches yet" in i.value for i in at.info))
        self.assertTrue(at.dataframe)

    def test_the_demo_is_never_mistaken_for_the_public_website(self):
        at = self.at.switch_page("league.py").run()
        at = next(r for r in at.radio if r.label == "Where do you compete?").set_value("Liquipedia event (S–D Tier)").run()
        self.assertFalse(any("public website" in c.value for c in at.caption))
        # the Windows app can still check Liquipedia during the demo: only the public website can't
        self.assertTrue(any(b.label.startswith("Check Liquipedia") for b in at.button))
        at = at.switch_page("teams.py").run()
        self.assertTrue(any("isn't part of the demo season" in i.value for i in at.info))


if __name__ == "__main__":
    unittest.main()
