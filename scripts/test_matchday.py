"""Planning must stay explicit, portable, escaped and isolated from demo data."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from matchday_data import MatchdayWorkspace, demo_workspace, head_to_head, panel_html
from session import Session


class TestMatchday(unittest.TestCase):
    def test_fixture_dates_filter_and_sort_without_inventing_matches(self):
        workspace = demo_workspace(date(2026, 10, 16))
        self.assertEqual(len(workspace.upcoming(date(2026, 10, 19))), 2)
        self.assertEqual(workspace.upcoming(date(2026, 10, 19))[0]["opponent"], "Northgate Esports")
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


class TestHeadToHead(unittest.TestCase):
    """Each upcoming opponent's record against you, from the matches in your stats."""

    @classmethod
    def setUpClass(cls):
        import demo_season
        from stats_db import StatsDB

        cls.db = StatsDB(":memory:")
        demo_season.load(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_record_and_last_meeting(self):
        rows = self.db.query("SELECT mp.won, m.played_at, m.score0, m.score1 FROM match_players mp JOIN matches m "
                             "ON m.match_id = mp.match_id WHERE mp.player_key = ? AND m.team1 = 'Metro Tech' "
                             "ORDER BY m.played_at DESC", [self.db.me().casefold()])
        wins, losses = sum(r["won"] == 1 for r in rows), sum(r["won"] == 0 for r in rows)
        last = rows[0]
        line = head_to_head(self.db, "  metro   TECH ")  # however it was typed in the fixture
        self.assertTrue(line.startswith(f"Your record: {wins}–{losses} · last met "))
        self.assertTrue(line.endswith(f"{'won' if last['won'] else 'lost'} {last['score0']}–{last['score1']}"))
        self.assertEqual(head_to_head(self.db, "A team never played"), "First meeting in your stats")
        self.assertIsNone(head_to_head(self.db, " "))

    def test_every_demo_fixture_has_a_record(self):
        for fixture in demo_workspace(date(2026, 10, 16)).upcoming(date(2026, 10, 16)):
            self.assertTrue(head_to_head(self.db, fixture["opponent"]).startswith("Your record: "))

    def test_the_panel_shows_it_and_the_score_from_your_side(self):
        workspace = demo_workspace(date(2026, 10, 16))
        records = {f["opponent"].casefold(): f"Your record vs {f['opponent']}" for f in workspace.upcoming(date(2026, 10, 16))}
        with mock.patch("matchday_data.MatchdayWorkspace.upcoming", return_value=workspace.upcoming(date(2026, 10, 16))):
            html = panel_html(workspace, [{"won": 1, "map": "Bank", "played_at": "2026-10-01", "team": 1,
                                           "score0": 4, "score1": 7}], [], records=records)
        self.assertIn('<small class="md-record">Your record vs Metro Tech</small>', html)
        self.assertIn("<span>7–4</span>", html)  # won 7-4 from team 1's side, not "4–7"
