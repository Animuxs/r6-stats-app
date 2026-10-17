"""Tests for launcher.py. Run from the repo root:  python -m unittest discover -s desktop"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import launcher

HERE = Path(__file__).resolve().parent


class TestLaunchChime(unittest.TestCase):
    def test_chime_is_async_and_mute_and_smoke_runs_are_silent(self):
        import desktop_settings

        sound = mock.Mock(SND_ALIAS=1, SND_ASYNC=2, SND_NODEFAULT=4)
        with mock.patch.object(launcher, 'IS_WINDOWS', True), \
                mock.patch.dict(sys.modules, {'winsound': sound}), \
                mock.patch.dict(launcher.os.environ, {}, clear=True), \
                mock.patch.object(desktop_settings, 'launch_chime_enabled', return_value=True) as enabled:
            launcher.play_launch_chime()
            sound.PlaySound.assert_called_once_with('SystemAsterisk', 7)
            enabled.return_value = False
            launcher.play_launch_chime()
            self.assertEqual(sound.PlaySound.call_count, 1)
            enabled.return_value = True
            with mock.patch.dict(launcher.os.environ, {'R6_SMOKE_TEST': '1'}):
                launcher.play_launch_chime()
            self.assertEqual(sound.PlaySound.call_count, 1)
            sound.PlaySound.side_effect = RuntimeError('no sound device')
            launcher.play_launch_chime()  # audio failures never stop the app


@unittest.skipUnless(sys.platform == "win32", "console windows are a Windows thing")
class TestHideConsoleWindows(unittest.TestCase):
    def test_every_program_started_gets_no_console_window(self):
        seen = []

        def record(self, *args, **kwargs):  # stands in for the real Popen.__init__
            self._child_created = False
            seen.append(kwargs.get("creationflags", 0))

        with mock.patch.object(subprocess.Popen, "__init__", record):
            launcher.hide_console_windows()
            launcher.hide_console_windows()  # a second call changes nothing
            subprocess.Popen(["anything"])
            subprocess.Popen(["anything"], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            subprocess.Popen(["anything"], creationflags=subprocess.CREATE_NEW_CONSOLE)  # asked for: kept
        no_window = subprocess.CREATE_NO_WINDOW
        self.assertEqual(seen, [no_window, subprocess.CREATE_NEW_PROCESS_GROUP | no_window,
                                subprocess.CREATE_NEW_CONSOLE])

    def test_a_plain_subprocess_call_from_the_app_opens_no_window(self):
        # Like Streamlit's Git lookup: subprocess.run with no flags, from a process with no
        # console (pythonw.exe, as the app is), which would otherwise give the child a window.
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if not pythonw.is_file():
            self.skipTest("no pythonw.exe next to this Python")
        with tempfile.TemporaryDirectory() as td:
            result, runner, probe = Path(td, "result.json"), Path(td, "runner.py"), Path(td, "probe.py")
            probe.write_text("import ctypes, json, sys\n"
                             "json.dump({'window': bool(ctypes.windll.kernel32.GetConsoleWindow())}, open(sys.argv[1], 'w'))\n")
            runner.write_text("\n".join([
                "import subprocess, sys",
                f"sys.path.insert(0, {str(HERE)!r})",
                "import launcher",
                "launcher.hide_console_windows()",
                f"subprocess.run([{sys.executable!r}, {str(probe)!r}, {str(result)!r}], timeout=60)",
            ]))
            subprocess.run([str(pythonw), str(runner)], timeout=90, check=True)
            self.assertEqual(json.loads(result.read_text()), {"window": False})


class TestSmokeTest(unittest.TestCase):
    def test_dashboard_is_recognized_however_the_theme_capitalizes_it(self):
        # the text the window reported in the failed build for the school themes
        themed = "RAINBOW SIX SIEGE / COLLEGIATE\nNECC / TEAM OPERATIONS\nDASHBOARD\n\nReplay source"
        for text in (themed, "Dashboard", "Match Report"):
            self.assertTrue(launcher.dashboard_is_rendered(text), text)
        self.assertFalse(launcher.dashboard_is_rendered("R6 Match Stats is starting..."))


class TestLaunchScreen(unittest.TestCase):
    def saved(self, content: str | None) -> dict:
        with tempfile.TemporaryDirectory() as td, mock.patch.object(launcher, "DATA_DIR", Path(td)):
            if content is not None:
                Path(td, "appearance.json").write_text(content, encoding="utf-8")
            return launcher.saved_theme()

    def test_uses_the_saved_school_theme(self):
        theme = self.saved(json.dumps({"name": "Test U Varsity", "primary": "#c8102e"}))
        self.assertEqual(theme, {"primary": "#c8102e", "name": "Test U Varsity"})

    def test_falls_back_to_the_default_theme(self):
        default = {"primary": launcher.DEFAULT_ACCENT, "name": ""}
        for content in (None, "not json", "[]", json.dumps({"name": "Siege / NECC", "primary": "red"})):
            self.assertEqual(self.saved(content), default, content)

    def test_page_shows_the_app_its_steps_and_the_school(self):
        page = launcher.splash_page({"primary": "#c8102e", "name": "<b>Test U</b> @VERSION@"})
        self.assertIn(launcher.APP_NAME, page)
        self.assertIn(launcher.readable_accent("#c8102e"), page)
        self.assertIn("&lt;b&gt;Test U&lt;/b&gt; @VERSION@", page)  # escaped, and never filled in as a token
        self.assertNotIn("<b>Test U", page)
        self.assertEqual(re.findall(r"@[A-Z_]+@", page), ["@VERSION@"])  # every token filled in, once
        for step in launcher.LAUNCH_STEPS:
            self.assertIn(json.dumps(step), page)
        self.assertIn("function launch_step(", page)
        self.assertIn("function launch_ready(", page)
        self.assertIn("prefers-reduced-motion", page)
        self.assertFalse(launcher.dashboard_is_rendered(""))  # the smoke test reads the app's page, not this

    def test_readable_accent_lightens_dark_colors(self):
        self.assertEqual(launcher.readable_accent("#002855"), "#919191")
        self.assertEqual(launcher.readable_accent("#52d5f2"), "#91d5f2")

    def test_steps_never_stop_the_launch(self):
        window = mock.Mock()
        window.evaluate_js.side_effect = RuntimeError("page not loaded yet")
        launcher.show_step(window, 1)  # no exception
        launcher.finish_launch(window)
        window.evaluate_js.side_effect = None
        launcher.show_step(window, 2)
        window.evaluate_js.assert_called_with("launch_step(2)")



class TestDocking(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        self.data = Path(folder) / "R6MatchStats"
        for patch in (mock.patch.dict("os.environ", {"LOCALAPPDATA": folder}),
                      mock.patch.object(launcher, "DATA_DIR", self.data)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_dock_argument(self):
        self.assertEqual(launcher.dock_request(["--dock", "right"]), "right")
        self.assertEqual(launcher.dock_request(["--dock", "LEFT"]), "left")
        self.assertEqual(launcher.dock_request(["--dock", "off"]), "off")
        for args in ([], ["--dock"], ["--dock", "top"], ["--serve", "1", "2"]):
            self.assertIsNone(launcher.dock_request(args), args)

    def test_docked_state_is_remembered(self):
        self.assertIsNone(launcher.saved_dock())
        launcher.save_dock("left")
        self.assertEqual(launcher.saved_dock(), "left")
        launcher.save_dock(None)
        self.assertIsNone(launcher.saved_dock())
        (self.data / launcher.DOCK_STATE).write_text("{broken", encoding="utf-8")
        self.assertIsNone(launcher.saved_dock())

    def test_commands_from_the_pages_reach_the_window_once(self):
        launcher.send_window_command("dock", edge="right")
        launcher.send_window_command("undock")
        self.assertEqual(launcher.take_window_commands(), [{"command": "dock", "edge": "right"}, {"command": "undock"}])
        self.assertEqual(launcher.take_window_commands(), [])  # each one is done once
        with self.assertRaises(ValueError):
            launcher.send_window_command("explode")

    def test_commands_sent_at_the_same_time_are_all_kept_in_order(self):
        # Windows' clock often gives two quick calls the same time: neither command may be lost
        with mock.patch("time.time_ns", return_value=1_790_805_194_325_678_000):
            for edge in ("left", "right", "left"):
                launcher.send_window_command("dock", edge=edge)
        self.assertEqual([c["edge"] for c in launcher.take_window_commands()], ["left", "right", "left"])

    def docking(self):
        window = mock.Mock()
        docking = launcher.Docking(window)
        docking.url = "http://127.0.0.1:5000/"
        docking._bar = mock.Mock(edge=None)
        return docking, window

    def test_dock_move_and_undock(self):
        docking, window = self.docking()
        docking.set("right")
        docking._bar.dock.assert_called_once_with("right", launcher.DOCK_WIDTH)
        window.load_url.assert_called_with("http://127.0.0.1:5000/?view=dock&edge=right")
        self.assertEqual(launcher.saved_dock(), "right")
        docking.set("right")  # already there: nothing happens
        self.assertEqual(docking._bar.dock.call_count, 1)
        docking.set("left")
        window.load_url.assert_called_with("http://127.0.0.1:5000/?view=dock&edge=left")
        docking.set(None)
        docking._bar.undock.assert_called_once()
        window.load_url.assert_called_with("http://127.0.0.1:5000/")
        self.assertIsNone(launcher.saved_dock())

    def test_docking_before_the_server_is_up_only_moves_the_window(self):
        docking, window = self.docking()
        docking.url = ""
        docking.set("left", load=False)
        docking._bar.dock.assert_called_once()
        window.load_url.assert_not_called()
        self.assertEqual(docking.page(), "?view=dock&edge=left")  # the server's address goes in front

    def test_a_failed_dock_changes_nothing(self):
        docking, window = self.docking()
        docking._bar.dock.side_effect = OSError("no shell")
        with self.assertRaises(OSError):
            docking.set("right")
        self.assertIsNone(docking.edge)
        self.assertIsNone(launcher.saved_dock())
        window.load_url.assert_not_called()

    def test_closing_gives_the_screen_back_but_remembers_the_dock(self):
        docking, _ = self.docking()
        docking.set("right")
        docking._bar.edge = "right"
        docking.release()
        docking._bar.undock.assert_called_once()
        self.assertEqual(launcher.saved_dock(), "right")  # reopens docked

    def test_the_launch_screen_waits_and_fades(self):
        self.assertGreaterEqual(launcher.MIN_LAUNCH_SECONDS, 3)
        self.assertEqual(launcher.FADE_SECONDS, 0.6)
        self.assertIn("transition:opacity .6s", launcher.SPLASH)  # the fade the launcher waits out

    def test_the_docked_page_is_recognized(self):
        self.assertTrue(launcher.dock_is_rendered("⇄ Dock left\n⇱ Full window\nLatest match"))
        self.assertFalse(launcher.dock_is_rendered("Dashboard"))


class TestWindowsShell(unittest.TestCase):
    def test_structures_have_windows_layout(self):
        import ctypes

        import windows_shell as shell

        if ctypes.sizeof(ctypes.c_void_p) != 8:
            self.skipTest("the sizes below are 64-bit Windows'")
        sizes = {s.__name__: ctypes.sizeof(s) for s in (shell.APPBARDATA, shell.MONITORINFO, shell.RECT, shell.GUID,
                                                        shell.PROPERTYKEY, shell.PROPVARIANT)}
        self.assertEqual(sizes, {"APPBARDATA": 48, "MONITORINFO": 40, "RECT": 16, "GUID": 16, "PROPERTYKEY": 20,
                                 "PROPVARIANT": 24})

    @unittest.skipUnless(sys.platform == "win32", "Windows' shell")
    def test_app_id_and_jump_list_are_accepted(self):
        import windows_shell as shell

        shell.set_app_id("R6MatchStats.Test")
        self.assertEqual(shell.app_id(), "R6MatchStats.Test")
        shell.set_jump_list("R6MatchStats.Test", sys.executable, launcher.JUMP_LIST_TASKS)  # raises if refused

if __name__ == "__main__":
    unittest.main()
