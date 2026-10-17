"""The app's shared structure: the Session every tab reads and writes, the ReplaySource kinds the
tabs use without asking which kind they have, the Tab base class, and the links between tabs."""

import ast
import inspect
import os
import re
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
from tab import PAGES, Tab

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

    # every kind of replay source, made the way the app makes it: TestTheStructureHolds checks none is missing
    KINDS = {
        "UploadSource": lambda state: sources.LoadedSource.of(state),
        "FolderSource": lambda state: sources.LoadedSource.of(dict(state, sig=("path", "C:/replays", 1))),
        "DemoSource": lambda state: sources.DemoSource(),
    }

    def test_every_kind_answers_the_same_questions(self):
        for kind, make in self.KINDS.items():
            source = make(dict(self.STATE, parsed={}))
            with self.subTest(kind=kind):
                self.assertEqual(type(source).__name__, kind)
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
                      mock.patch("parser.find_replay_folders", return_value=[]),  # never this PC's replays
                      mock.patch("rosters.update_on_launch"),  # nor online
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


SCRIPTS = Path(__file__).parent
NAVIGATION = re.findall(r'st\.Page\("(\w+\.py)"', (SCRIPTS / "app.py").read_text(encoding="utf-8"))
# the modules under the Session that keep their own values in st.session_state, and the Session itself
STATE_OWNERS = {"session.py", "sources.py", "branding.py"}
# scripts run by hand, not part of the app
TOOLS = {"capture_necc_rosters.py", "example_season_stats.py", "run_tests.py"}
# parts of this repo the Windows app doesn't include: the REST API (its own server) and the command line scoreboard
NOT_IN_THE_WINDOWS_APP = {"api.py", "match_stats.py"}


def _tree(name: str) -> ast.Module:
    return ast.parse((SCRIPTS / name).read_text(encoding="utf-8"), name)


def _app_modules() -> list[str]:
    return sorted(p.name for p in SCRIPTS.glob("*.py") if not p.name.startswith("test_") and p.name not in TOOLS)


def _concrete_subclasses(base: type) -> set[type]:
    found = set()
    for sub in base.__subclasses__():
        found |= _concrete_subclasses(sub)
        if not inspect.isabstract(sub):
            found.add(sub)
    return found


class TestTheStructureHolds(unittest.TestCase):
    """The rules that keep the app one program as it grows. A new tab, replay source or module that
    doesn't follow them fails here, with what to change."""

    def test_every_page_is_one_tab(self):
        """Each page in app.py's navigation is a Tab subclass with a title and a name, listed in
        tab.PAGES, and the page file ends by running it: nothing else runs when the file is loaded."""
        self.assertTrue(NAVIGATION)
        names = {}
        for page in [*NAVIGATION, "dock.py"]:
            with self.subTest(page=page):
                tree = _tree(page)
                tabs = [n for n in tree.body if isinstance(n, ast.ClassDef)
                        and any(isinstance(b, ast.Name) and b.id == "Tab" for b in n.bases)]
                self.assertEqual(len(tabs), 1, f"{page} must define exactly one Tab subclass")
                tab = tabs[0]
                attrs = {}
                for node in tab.body:  # title, name = "...", "..."
                    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Tuple):
                        attrs.update({t.id: v.value for t, v in zip(node.targets[0].elts, node.value.elts)
                                      if isinstance(v, ast.Constant)})
                    elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                        attrs.update({t.id: node.value.value for t in node.targets if isinstance(t, ast.Name)})
                self.assertTrue(attrs.get("title"), f"{tab.name} needs a title")
                self.assertEqual(PAGES.get(attrs.get("name")), page, f"add {attrs.get('name')!r}: {page!r} to tab.PAGES")
                names[attrs["name"]] = page
                self.assertIn("render", {n.name for n in tab.body if isinstance(n, ast.FunctionDef)})
                last = tree.body[-1]
                self.assertEqual(ast.unparse(last), f"{tab.name}().run()", f"{page} must end with {tab.name}().run()")
                for node in tree.body[:-1]:
                    if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef)):
                        continue
                    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                        continue  # the docstring
                    targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
                    names_set = [n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)]
                    self.assertTrue(isinstance(node, (ast.Assign, ast.AnnAssign)) and names_set
                                    and all(n.lstrip("_").isupper() for n in names_set),
                                    f"{page} line {node.lineno}: only imports, functions, classes and CONSTANTS "
                                    "go at the top of a page; drawing belongs in the tab's render()")
        self.assertEqual(set(PAGES.values()), set(names.values()), "tab.PAGES lists a page that isn't a tab")

    def test_tabs_share_state_through_the_session(self):
        """Only the Session (and the modules under it that own their keys) touch st.session_state:
        tabs ask the Session, so every tab sees the same season, team, replays and demo season."""
        for name in _app_modules():
            if name in STATE_OWNERS:
                continue
            with self.subTest(module=name):
                uses = [n.lineno for n in ast.walk(_tree(name))
                        if isinstance(n, ast.Attribute) and n.attr == "session_state"]
                self.assertEqual(uses, [], f"{name} uses st.session_state on lines {uses}: use Session() "
                                           "(or self.session in a tab) instead")

    def test_tabs_never_import_each_other(self):
        """Loading a page runs it, so a tab reaches another through the Session and Tab.go_to, never
        by importing its file."""
        pages = {p[:-3] for p in [*NAVIGATION, "dock.py"]}
        for name in _app_modules():
            with self.subTest(module=name):
                imported = {a.name.split(".")[0] for n in ast.walk(_tree(name)) if isinstance(n, ast.Import) for a in n.names}
                imported |= {(n.module or "").split(".")[0] for n in ast.walk(_tree(name)) if isinstance(n, ast.ImportFrom)}
                self.assertFalse(imported & pages, f"{name} imports the page(s) {imported & pages}")

    def test_links_between_tabs_go_through_the_tab_base(self):
        """st.switch_page is called in one place, Tab.go_to, with the page from tab.PAGES."""
        for name in _app_modules():
            with self.subTest(module=name):
                calls = [n.lineno for n in ast.walk(_tree(name)) if isinstance(n, ast.Attribute) and n.attr == "switch_page"]
                self.assertEqual(calls, [] if name != "tab.py" else calls, f"{name}: use self.go_to(...)")
                for node in ast.walk(_tree(name)):  # st.page_link("x.py") names a real tab
                    if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "page_link" and node.args:
                        self.assertIn(node.args[0].value, PAGES.values())

    def test_every_replay_source_is_held_to_the_same_contract(self):
        """Each kind of ReplaySource the app makes is in TestReplaySources' contract test: add a new kind there."""
        kinds = {c.__name__ for c in _concrete_subclasses(sources.ReplaySource) if not c.__subclasses__()}
        self.assertEqual(kinds, set(TestReplaySources.KINDS), "a ReplaySource kind isn't in TestReplaySources.KINDS")

    def test_every_app_module_ships_in_the_windows_app(self):
        """Each module of the app is in the Windows app's file list (tools run by hand are in TOOLS)."""
        spec = (SCRIPTS.parent / "desktop" / "R6MatchStats.spec").read_text(encoding="utf-8")
        for name in _app_modules():
            if name not in NOT_IN_THE_WINDOWS_APP:
                with self.subTest(module=name):
                    self.assertTrue(f'"{name}"' in spec, f"add {name} to APP_FILES in desktop/R6MatchStats.spec")


if __name__ == "__main__":
    unittest.main()
