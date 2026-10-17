"""Planning must stay explicit, portable, escaped and isolated from demo data."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from matchday_data import MatchdayWorkspace, demo_workspace, panel_html
from session import Session


class TestMatchday(unittest.TestCase):
    def test_fixture_dates_filter_and_sort_without_inventing_matches(self):
        workspace = demo_workspace(date(2026, 10, 16))
        self.assertEqual(len(workspace.upcoming(date(2026, 10, 19))), 2)
        self.assertEqual(workspace.upcoming(date(2026, 10, 19))[0]["opponent"], "Northbridge Esports")
        self.assertEqual(MatchdayWorkspace().upcoming(), [])

    def test_backup_round_trip_preserves_every_planning_field(self):
        original = demo_workspace()
        restored = MatchdayWorkspace.from_dict(json.loads(json.dumps(original.to_dict())))
        self.assertEqual(restored.to_dict(), original.to_dict())

    def test_bad_backups_are_rejected(self):
        for value in ({"fixtures": "oops"}, {"notes": 1}, {"availability": {"Player": "guessed"}},
                      {"fixtures": [{"id": "x", "opponent": "Opponent", "when": "invalid"}]}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MatchdayWorkspace.from_dict(value)
        value = demo_workspace().to_dict()
        value["fixtures"].append(value["fixtures"][0])
        with self.assertRaises(ValueError):
            MatchdayWorkspace.from_dict(value)

    def test_panel_escapes_names_and_notes_and_does_not_guess_availability(self):
        workspace = MatchdayWorkspace(notes='<script>alert("bad")</script>')
        html = panel_html(workspace, [], ["<Player>"], demo=False)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;Player&gt;", html)
        self.assertIn("Unconfirmed", html)
        self.assertIn("No upcoming matches entered", html)

    def test_demo_edits_never_touch_desktop_planning_file(self):
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch("app_info.desktop_data_dir", return_value=Path(folder)), \
                mock.patch("app_info.is_windows_app", return_value=True), \
                mock.patch("sources.demo_active", return_value=False) as demo:
            state = {}
            session = Session(state)
            session.save_matchday(MatchdayWorkspace(notes="real notes"))
            demo.return_value = True
            example = session.matchday
            self.assertEqual(len(example.fixtures), 3)
            example.notes = "changed demo notes"
            session.save_matchday(example)
            self.assertEqual(json.loads((Path(folder) / "matchday.json").read_text())["notes"], "real notes")
            demo.return_value = False
            self.assertEqual(session.matchday.notes, "real notes")
            self.assertEqual(Session({}).matchday.notes, "real notes")

    def test_hosted_sessions_do_not_share_planning(self):
        with mock.patch("app_info.is_windows_app", return_value=False), mock.patch("sources.demo_active", return_value=False):
            first, second = Session({}), Session({})
            first.save_matchday(MatchdayWorkspace(notes="private session"))
            self.assertEqual(second.matchday.notes, "")
