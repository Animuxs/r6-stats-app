"""Tests for the Streamlit pages (app.py, report.py, download.py) and app_info.py.
Run from the repo root:  python -m unittest discover -s scripts"""

from __future__ import annotations

import copy
import io
import json
import os
import shutil
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

import streamlit as st
from streamlit.testing.v1 import AppTest

import app_info
import necc_data
import parser as replay_parser
import season_stats

APP = str(Path(__file__).with_name("app.py"))
_DB_FOLDER = tempfile.mkdtemp()
_PATCHES = [
    # the pages' stats database: a throwaway one, never this PC's real one
    mock.patch.object(season_stats, "DEFAULT_DB_PATH", Path(_DB_FOLDER) / "stats.db"),
    # and no replay folder is found on this PC, so nothing's parsed unless a test asks for it
    mock.patch.object(replay_parser, "find_replay_folders", return_value=[]),
    # nor are newer rosters looked for online
    mock.patch("rosters.update_on_launch"),
]


def setUpModule():
    os.makedirs(_DB_FOLDER, exist_ok=True)  # again, if an earlier tearDownModule removed it
    for patch in _PATCHES:
        patch.start()


def tearDownModule():
    for patch in _PATCHES:
        patch.stop()
    shutil.rmtree(_DB_FOLDER, ignore_errors=True)


def demo_toggle(at):
    """The Dashboard's "Use demo match" switch (the sidebar has a switch of its own, for the assistant)."""
    return next(t for t in at.toggle if t.label.startswith("Use demo match"))


def run_app(local_visitor: bool = True, **env: str) -> AppTest:
    # AppTest has no real visitor IP; env and the visitor apply to this first run only
    with mock.patch.dict(os.environ, env), \
            mock.patch.object(app_info, "is_loopback", return_value=local_visitor):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
    return at


class TestReportPage(unittest.TestCase):
    def test_local_visitor_can_read_folders(self):
        at = run_app()
        self.assertFalse(at.exception)
        self.assertEqual(at.radio[0].options, ["Folder or zip on this computer", "Upload", "From the replays/ folder"])

    def test_public_site_only_takes_uploads(self):
        at = run_app(R6_HOSTED="1")
        self.assertFalse(at.exception)
        self.assertEqual(len(at.radio), 0)  # no folder paths on a public server
        self.assertEqual(len(at.text_input), 0)

    def test_remote_visitor_only_uploads_even_off_a_known_host(self):
        at = run_app(local_visitor=False)
        self.assertEqual(len(at.radio), 0)
        self.assertEqual(len(at.text_input), 0)

    def test_with_nothing_loaded_it_says_how_to_start_and_offers_the_demo(self):
        at = run_app(R6_HOSTED="1")
        self.assertFalse(at.exception)
        self.assertTrue(any(m.value == "#### Getting started" for m in at.markdown))
        at.button(key="start_demo_here").click().run()
        self.assertFalse(at.exception)
        self.assertTrue(at.session_state["r6_demo_db"] if "r6_demo_db" in at.session_state else
                        any("Demo season" in i.value for i in at.info))

    def test_windows_app_has_no_replays_folder_option(self):
        at = run_app(R6_DESKTOP="1")
        self.assertEqual(at.radio[0].options, ["Folder or zip on this computer", "Upload"])

    def test_demo_match_can_be_downloaded_as_csv_json_txt_and_a_picture(self):
        at = run_app(R6_HOSTED="1")
        demo_toggle(at).set_value(True).run()
        buttons = at.get("download_button")
        self.assertEqual([b.proto.label for b in buttons],
                         ["⬇ CSV", "⬇ JSON", "⬇ TXT", "⬇ Rounds CSV", "⬇ Scoreboard image"])
        self.assertEqual([Path(b.proto.url).suffix for b in buttons], [".csv", ".json", ".txt", ".csv", ".png"])
        at = at.run()  # drawn once per scoreboard and color, then kept
        self.assertFalse(at.exception)
        self.assertEqual(at.get("download_button")[-1].proto.url, buttons[-1].proto.url)

    def test_the_demo_match_is_never_added_to_your_stats(self):
        at = run_app(R6_HOSTED="1")
        demo_toggle(at).set_value(True).run()
        self.assertFalse(at.exception)
        self.assertTrue(any("can't be added to your stats" in c.value for c in at.caption))
        self.assertFalse([b for b in at.button if "Confirm" in b.label])

    def test_demo_match_renders_both_scoreboards(self):
        at = run_app(R6_HOSTED="1")
        demo_toggle(at).set_value(True).run()
        self.assertFalse(at.exception)
        tables = [m.value for m in at.markdown if 'class="pl"' in m.value]
        self.assertEqual(len(tables), 2)
        self.assertIn("Team Liquid", tables[0])


    def test_round_by_round_shows_sides_and_operators(self):
        at = run_app(R6_HOSTED="1")
        demo_toggle(at).set_value(True).run()
        self.assertFalse(at.exception)
        tables = [m.value for m in at.markdown if 'class="pl rounds"' in m.value]
        self.assertEqual(len(tables), 2)  # the match's rounds, then the picked player's
        overview, player = tables
        self.assertEqual(overview.count("<tr>"), 1 + 9)  # header + 9 rounds
        self.assertIn('class="side attack"', overview)
        self.assertIn('class="side defense"', overview)
        self.assertIn("Elimination", overview)
        from sample_data import ATTACK_OPS, DEFENSE_OPS

        picked = at.selectbox(key="r6_round_player").value
        self.assertIn(ATTACK_OPS[picked], player)
        self.assertIn(DEFENSE_OPS[picked], player)
        self.assertIn("⚔\ufe0e", player)
        self.assertIn("♜", player)
        self.assertTrue(any('class="side-split"' in m.value for m in at.markdown))
        self.assertIn('<td class="score">6<span>–</span>3</td>', overview)  # the score after the last round
        self.assertIn('class="chip pos">Entry kill<', player)
        self.assertEqual(at.selectbox(key="r6_round_player").format_func(picked), f"{picked} · Team Liquid")

    def test_a_round_the_player_missed_is_listed(self):
        from sample_data import SAMPLE_MATCH, TEAM0, TEAM1

        match = copy.deepcopy(SAMPLE_MATCH)
        match["rounds"][1]["players"] = [n for n in TEAM0 + TEAM1 if n != "Fabian"]  # left for a round
        with mock.patch.object(replay_parser, "load_demo_match", return_value=match):
            at = run_app(R6_HOSTED="1")
            demo_toggle(at).set_value(True).run()
            at.selectbox(key="r6_round_player").set_value("Fabian").run()
        self.assertFalse(at.exception)
        player = [m.value for m in at.markdown if 'class="pl rounds"' in m.value][1]
        self.assertEqual(player.count("<tr"), 1 + 9)  # every round is still listed...
        self.assertIn('<tr class="missed"><td>2</td>', player)  # ...with round 2 as missed


class TestOperatorNormalization(unittest.TestCase):
    def test_operator_data_is_preserved_per_round(self):
        raw = {"rounds": [
            {"roundNumber": 1, "players": [{"username": "Player", "teamIndex": 0,
                                               "operator": {"name": "Ash"}}]},
            {"roundNumber": 2, "players": [{"username": "Player", "teamIndex": 0,
                                               "operator": {"name": "Sledge"}}]},
        ]}
        match = replay_parser.normalize_from_r6_dissect(raw)
        self.assertEqual(match["players"][0]["operator_history"], ["Ash", "Sledge"])
        self.assertEqual(match["rounds"][0]["operators"], {"Player": "Ash"})


class TestSchoolCatalog(unittest.TestCase):
    def test_normalizes_school_roster_and_filters_other_games(self):
        schools = necc_data.normalize_school_catalog({"schools": [{
            "name": "North University",
            "teams": [
                {"name": "Varsity", "game": "Rainbow Six Siege", "roster": ["Ash", {"username": "Thermite"}]},
                {"name": "Overwatch", "game": "Overwatch", "roster": ["Tracer"]},
            ],
        }]})
        self.assertEqual(schools[0]["teams"][0]["roster"], ["Ash", "Thermite"])
        self.assertEqual([team["name"] for team in schools[0]["teams"]], ["Varsity"])

    def test_the_configured_feed(self):
        """The school feed a deployment can set (NECC_R6_DATA_URL): HTTPS only, at most 2 MB, JSON."""
        catalog = {"schools": [{"name": "North University", "teams": [{"name": "Varsity", "roster": ["Ash"]}]}]}

        def served(body: bytes):
            response = mock.MagicMock()
            response.__enter__.return_value.read.side_effect = lambda n: body[:n]
            return mock.patch("necc_data.urlopen", return_value=response)

        for url in ("", "http://example.org/feed.json", "ftp://example.org/x", "file:///C:/feed.json"):
            with self.subTest(url=url), served(b"{}") as fetch, self.assertRaises(ValueError):
                necc_data.fetch_school_catalog(url)
            fetch.assert_not_called()  # nothing is fetched from a link that isn't HTTPS
        with served(json.dumps(catalog).encode()):
            self.assertEqual(necc_data.fetch_school_catalog("https://example.org/feed.json")[0]["name"], "North University")
        for body in (b"not json", b"\xff\xfe", b" " * (necc_data.MAX_CATALOG_BYTES + 1), b'{"schools": []}'):
            with self.subTest(body=body[:12]), served(body), self.assertRaises(ValueError):
                necc_data.fetch_school_catalog("https://example.org/feed.json")

    def test_rejects_catalog_without_schools(self):
        with self.assertRaises(ValueError):
            necc_data.normalize_school_catalog({"schools": []})


def demo_matches(count: int = 2) -> dict[str, dict]:
    """`count` copies of the demo match, as found in a replay folder: {match folder: MatchData}.
    Fabian recorded them."""
    from sample_data import SAMPLE_MATCH

    found = {}
    for i in range(count):
        match = copy.deepcopy(SAMPLE_MATCH)
        match.update(match_id=f"demo-{i}", played_at=f"2026-09-2{i} 20:00:00", recording_player="Fabian",
                     match_type="Ranked")
        found[f"Match-2026-09-2{i}_20-00-00-1"] = match
    return found


def with_matches(at: AppTest, count: int = 2, added: bool = True) -> AppTest:
    """Give the session a replay source of `count` demo matches, already parsed, the way Dashboard
    leaves it after loading a folder; and, if `added`, put them in the stats database with every
    player, as if each had been confirmed on the Dashboard."""
    from stats_db import StatsDB

    parsed = {name: (match, {}, []) for name, match in demo_matches(count).items()}
    at.session_state["source"] = {"sig": ("upload", "test"), "workdir": tempfile.mkdtemp(), "parsed": parsed,
                                  "groups": {n: [f"{n}-R01.rec"] for n in parsed}, "skipped": None}
    if added:
        with StatsDB() as db:
            for name, (match, _raw, _warnings) in parsed.items():
                db.import_match(name, match, files=1)
    return at


class TestDemoSeason(unittest.TestCase):
    """The demo season: a made-up season in a stats database of its own, so every page has something
    to show without replays, and nobody's real stats are touched."""

    def setUp(self):
        self.db = Path(tempfile.mkdtemp(dir=_DB_FOLDER)) / "stats.db"  # the user's real database
        patch = mock.patch.object(season_stats, "DEFAULT_DB_PATH", self.db)
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_season_is_the_same_every_time_and_holds_together(self):
        import demo_season
        from metrics_engine import compute_match_metrics

        first, again = demo_season.season(), demo_season.season()
        self.assertEqual(first, again)
        self.assertEqual(len(first), 12)
        for folder, match in first.items():
            with self.subTest(folder=folder):
                self.assertEqual(sum(match["final_score"]), len(match["rounds"]))
                self.assertIn(max(match["final_score"]), (7, 8))  # first to 7, or 8 after 6-6
                names = {p["name"] for p in match["players"]}
                for rnd in match["rounds"]:
                    for event in rnd["events"]:
                        self.assertTrue({event.get("actor"), event.get("target", event.get("actor"))} <= names)
                stats = compute_match_metrics(match)
                self.assertEqual(len(stats), 10)
                self.assertTrue(all(s.rounds_played == len(match["rounds"]) for s in stats.values()))

    def test_every_page_has_the_season_and_real_stats_are_untouched(self):
        at = run_app()
        at = next(b for b in at.button if b.label == "Try the demo season").click().run()
        self.assertFalse(at.exception)
        self.assertTrue(any("Demo season" in i.value for i in at.info))
        banner = "".join(m.value for m in at.markdown if "identity-banner" in m.value)
        self.assertIn("R6 MATCH STATS DEMO LEAGUE", banner)
        self.assertIn("Lakeside Varsity", banner)  # the demo team's identity, for this session only
        self.assertIn("12 demo season matches", [s.label for s in at.selectbox])  # Dashboard opens any of them
        for page in ("ask.py", "history.py", "operators.py", "teams.py", "league.py"):
            with self.subTest(page=page):
                at.switch_page(page).run()
                self.assertFalse(at.exception)
                self.assertFalse(any("No matches yet" in i.value for i in at.info), page)
        at.switch_page("history.py").run()
        self.assertEqual(len(at.dataframe[0].value), 12)
        at.switch_page("league.py").run()
        with mock.patch("season_stats.StatsManager") as tracker:
            at.button(key="league_save").click().run()
            self.assertFalse(at.exception)
            tracker.assert_not_called()  # demo saves never touch the real season tracker
        self.assertFalse(self.db.exists() and _matches_in(self.db))  # the user's own stats have none of it
        at = next(b for b in at.button if b.label == "Leave the demo season").click().run()
        at.switch_page("ask.py").run()
        self.assertTrue(any("No matches yet" in i.value for i in at.info))
        self.assertNotIn("Lakeside Varsity", "".join(m.value for m in at.markdown if "identity-banner" in m.value))

    def test_a_link_opens_the_demo(self):
        with mock.patch.object(app_info, "is_loopback", return_value=True):
            at = AppTest.from_file(APP, default_timeout=60)
            at.query_params["demo"] = "1"
            at.run()
        self.assertFalse(at.exception)
        self.assertTrue(any("Demo season" in i.value for i in at.info))


def _matches_in(path: Path) -> int:
    from stats_db import StatsDB

    with StatsDB(path) as db:
        return db.summary()["matches"]


class TestAnalyticsPages(unittest.TestCase):
    """The pages that read the stats database, run as in the Windows app (on this PC)."""

    def setUp(self):
        patch = mock.patch.object(season_stats, "DEFAULT_DB_PATH", Path(tempfile.mkdtemp(dir=_DB_FOLDER)) / "stats.db")
        patch.start()
        self.addCleanup(patch.stop)  # every test starts with an empty database

    def test_pages_without_matches_say_where_to_start(self):
        at = run_app()
        for page in ("ask.py", "history.py", "operators.py", "teams.py", "league.py", "appearance.py"):
            with self.subTest(page=page):
                at.switch_page(page).run()
                self.assertFalse(at.exception)
                if page not in ("league.py", "appearance.py"):
                    self.assertTrue(any("No matches yet" in i.value for i in at.info), page)

    def test_ask_answers_about_the_loaded_matches(self):
        from metrics_engine import compute_match_metrics
        from sample_data import SAMPLE_MATCH

        at = with_matches(run_app())
        at.switch_page("ask.py").run()
        at.text_input(key="ask_question").set_value("who has the most kills?")
        at = next(b for b in at.button if b.label == "Ask").click().run()
        self.assertFalse(at.exception)
        best = max(compute_match_metrics(SAMPLE_MATCH).values(), key=lambda s: s.kills)
        self.assertEqual(best.name, "Fabian")  # who recorded the demo matches, so "you"
        headline = next(m.value for m in at.markdown if m.value.startswith("#### "))
        # the same match twice: twice the kills
        self.assertEqual(headline, f"#### You have the most kills: {2 * best.kills} (2 matches).")
        self.assertEqual(len(at.dataframe), 1)

    def test_ask_example_buttons(self):
        at = with_matches(run_app())
        at.switch_page("ask.py").run()
        at = next(b for b in at.button if "best map" in b.label.lower()).click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.text_input(key="ask_question").value, "What's my best map?")
        self.assertTrue(any(m.value.startswith("#### ") for m in at.markdown))

    def test_match_history_lists_every_match(self):
        at = with_matches(run_app(), count=3)
        at.switch_page("history.py").run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.dataframe[0].value), 3)
        self.assertEqual({m.label: m.value for m in at.metric}["Matches"], "3")

    def test_match_notes(self):
        from stats_db import StatsDB

        at = with_matches(run_app(), count=3)
        at.switch_page("history.py").run()
        self.assertFalse(at.segmented_control)  # nothing tagged yet: no filter
        self.assertEqual(list(at.dataframe[0].value.columns[-3:]), ["Tag", "Note", "VOD"])
        newest = "demo-2"  # the table is newest first

        def pick(at: AppTest, row: int = 0) -> AppTest:
            at.session_state["history_rows"] = {"selection": {"rows": [row], "columns": [], "cells": []}}
            return at.run()

        at = pick(at)
        self.assertTrue(any(m.value.startswith("**Your notes on ") for m in at.markdown))  # bold, not escaped
        at.selectbox(key=f"note_tag_{newest}").set_value("Scrim")
        at.text_input(key=f"note_vod_{newest}").set_value("javascript:alert(1)")
        at.text_area(key=f"note_text_{newest}").set_value("Lost the retakes on Kids")
        at = next(b for b in at.button if b.label == "Save note").click().run()
        self.assertFalse(at.exception)
        self.assertIn("web address", at.error[0].value)  # never a link that runs something
        with StatsDB() as db:
            self.assertEqual(db.notes(), {})
        at = pick(at)
        at.text_input(key=f"note_vod_{newest}").set_value("youtu.be/abc")
        at = next(b for b in at.button if b.label == "Save note").click().run()
        self.assertTrue(any("Saved your note" in s.value for s in at.success))
        with StatsDB() as db:
            self.assertEqual(db.notes()[newest], {"tag": "Scrim", "note": "Lost the retakes on Kids",
                                                  "vod": "https://youtu.be/abc"})
        table = at.dataframe[0].value
        self.assertEqual((table["Tag"][0], table["VOD"][0]), ("Scrim", "https://youtu.be/abc"))
        # now there's a tag to show just its matches, and picking it forgets the row picked before
        at = pick(at, 2)
        at = at.segmented_control(key="history_tag").set_value("Scrim").run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.dataframe[0].value), 1)
        self.assertFalse(any(b.label == "Save note" for b in at.button))
        at = pick(at, 0)
        self.assertTrue(any(b.label == "Save note" for b in at.button))
        at.session_state["history_rows"] = {"selection": {"rows": [1], "columns": [], "cells": []}}
        at = at.segmented_control(key="history_tag").set_value("Not tagged").run()
        self.assertEqual(len(at.dataframe[0].value), 2)
        # row 1 of the old list would be another match in this one: nothing's picked
        self.assertFalse(any(b.label == "Save note" for b in at.button))
        with StatsDB() as db:
            db.save_note(newest)  # its only tag cleared, from another window, say
        at = at.segmented_control(key="history_tag").set_value("Scrim").run()
        self.assertFalse(at.exception)
        self.assertFalse(at.segmented_control)  # no tags left: no filter, every match
        self.assertEqual(len(at.dataframe[0].value), 3)

    def test_build_a_team_starts_with_you_and_your_teammates(self):
        from sample_data import TEAM0

        at = with_matches(run_app())
        at.switch_page("teams.py").run()
        players = [at.text_input(key=f"roster_New team_{i}").value for i in range(5)]
        self.assertEqual(players[0], "Fabian")
        self.assertEqual(sorted(players), sorted(TEAM0))  # Fabian's four teammates fill the rest

    def test_build_a_team(self):
        from metrics_engine import compute_match_metrics
        from sample_data import SAMPLE_MATCH

        at = with_matches(run_app())
        at.switch_page("teams.py").run()
        players = [at.text_input(key=f"roster_New team_{i}").value for i in range(4)]
        at.text_input(key="team_name_New team").set_value("Liquid")
        at.text_input(key="roster_New team_4").set_value("Nobody")  # four of the team, and one stranger
        at = next(b for b in at.button if b.label == "Show team stats").click().run()
        self.assertFalse(at.exception)
        metrics = {m.label: m.value for m in at.metric}
        self.assertEqual(metrics["Maps"], "2")
        self.assertNotIn("Team EPS", metrics)  # a team's numbers are its own, never its players' added up
        rounds = len(SAMPLE_MATCH["rounds"])
        won = sum(r["winner_team"] == 0 for r in SAMPLE_MATCH["rounds"])  # Fabian's team is team 0
        self.assertEqual(metrics["Round W–L"], f"{2 * won}–{2 * (rounds - won)}")  # each round once
        situations = at.dataframe[0].value
        self.assertEqual(list(situations.columns), ["Situation", "%", "Rounds"])
        self.assertEqual(len(situations), 7)
        # the man-down rows after the first are out of the man-down rounds only
        went_down = int(situations["Rounds"][0].split(" of ")[0])
        self.assertTrue(situations["Rounds"][1].endswith(f" of {went_down}"))
        self.assertTrue(situations["Rounds"][2].endswith(f" of {went_down}"))
        table = at.dataframe[1].value
        self.assertEqual(sorted(table["Player"]), sorted(players))
        self.assertEqual(set(table["Matches"]), {2})
        eps = {p: s.eps for p, s in compute_match_metrics(SAMPLE_MATCH).items()}  # as the scoreboard shows it
        self.assertEqual(dict(zip(table["Player"], table["All-time EPS"])), {p: str(eps[p]) for p in players})
        self.assertIn("Nobody", at.warning[0].value)
        self.assertEqual(at.selectbox(key="team_choice").value, "Liquid")  # saved, and Ask knows it now

    def test_map_pool(self):
        from stats_db import StatsDB

        at = with_matches(run_app())
        at.switch_page("teams.py").run()
        self.assertFalse(at.exception)
        self.assertEqual(at.selectbox(key="pool_whose").value, "\x00you")  # you, to start with

        def pool(at: AppTest):
            return next(d.value for d in at.dataframe if "Best defense site" in d.value.columns)

        table = pool(at)
        self.assertEqual(list(table["Map"]), ["Bank"])
        self.assertEqual((table["Maps"][0], table["Map W–L"][0]), (2, "2–0"))  # see test_map_pool for the numbers
        self.assertEqual(table["Best attack site"][0], "CCTV/Archives (6–0)")
        sites = next(d.value for d in at.dataframe if "Site" in d.value.columns)
        self.assertEqual(set(sites["Site"]), {"Kids/Rooms", "CCTV/Archives"})
        self.assertEqual(at.selectbox(key="pool_map_\x00you").value, "Bank")
        # a built team, with how many of it must have played
        with StatsDB() as db:
            db.save_roster("Liquid", ["Fabian", "Kanto", "Sens"])
            db.save_roster("Solo", ["Bosco"])
        at = at.run()
        at = at.selectbox(key="pool_whose").set_value("Liquid").run()
        self.assertFalse(at.exception)
        self.assertEqual(at.slider(key="pool_need_Liquid").value, 3)
        self.assertEqual(pool(at)["Map W–L"][0], "2–0")
        at = at.selectbox(key="pool_whose").set_value("Solo").run()  # one player: nothing to slide
        self.assertFalse(at.exception)
        self.assertFalse(any(s.key == "pool_need_Solo" for s in at.slider))
        self.assertEqual(pool(at)["Map W–L"][0], "0–2")  # Bosco was on the other side
        with StatsDB() as db:
            db.delete_roster("Solo")
        at = at.run()  # the team picked is gone: back to you
        self.assertFalse(at.exception)
        self.assertEqual(at.selectbox(key="pool_whose").value, "\x00you")
        at = at.selectbox(key="pool_whose").set_value("Liquid").run()
        at = at.slider(key="pool_need_Liquid").set_value(3).run()
        with StatsDB() as db:
            db.save_roster("Liquid", ["Fabian", "Nobody", "Else"])  # now only one of them played
        at = at.run()
        self.assertFalse(at.exception)
        self.assertTrue(any("No matches with that many" in i.value for i in at.info))

    def test_map_pool_strongest_and_weakest(self):
        from stats_db import StatsDB

        at = with_matches(run_app())  # two wins on Bank
        lost = demo_matches(4)
        with StatsDB() as db:
            for name in list(lost)[2:]:  # and two losses on Oregon_ (a name with Markdown in it)
                match = lost[name]
                match.update(map="Oregon_", final_score=list(reversed(match["final_score"])))
                for rnd in match["rounds"]:
                    rnd["winner_team"] = 1 - rnd["winner_team"]
                db.import_match(name, match, files=1)
        at.switch_page("teams.py").run()
        line = next(m.value for m in at.markdown if "Strongest map" in m.value)
        self.assertTrue(line.startswith("**Strongest map:** Bank, 67% of rounds won over 2 maps."), line)
        self.assertIn("**Weakest:** Oregon\\_, 33% over 2 maps.", line)  # bold kept, the name escaped

    def test_operators_over_all_matches(self):
        from metrics_engine import compute_match_metrics
        from sample_data import SAMPLE_MATCH

        at = with_matches(run_app())
        at.switch_page("operators.py").run()
        self.assertFalse(at.exception)
        self.assertEqual(at.selectbox[0].value, "fabian")  # you come first
        table = at.dataframe[0].value
        picked = Counter(rb.operator for rb in compute_match_metrics(SAMPLE_MATCH)["Fabian"].round_breakdown)
        self.assertEqual(dict(zip(table["Operator"], table["Rounds"])), {op: 2 * n for op, n in picked.items()})
        self.assertEqual(set(picked), {"Ash", "Jäger"})  # Fabian's attack and defense picks
        self.assertEqual(list(table.columns[:2]), ["Operator", "Side"])
        self.assertEqual(dict(zip(table["Operator"], table["Side"])), {"Ash": "⚔\ufe0e Attack", "Jäger": "♜ Defense"})

    def test_operators_in_the_open_match(self):
        from sample_data import SAMPLE_MATCH, TEAM0, TEAM1

        at = run_app()
        at.switch_page("operators.py").run()
        at = at.segmented_control[0].set_value("This match").run()
        self.assertIn("No matches yet", at.info[0].value)
        # before a match is opened on the Dashboard: the newest one
        at = with_matches(at).run()
        self.assertFalse(at.exception)
        self.assertIn("newest match", at.caption[0].value)
        picks = {r["Operator"]: r["Picks"] for r in at.dataframe[0].value.to_dict("records")}
        self.assertEqual(picks, Counter(op for rnd in SAMPLE_MATCH["rounds"] for op in rnd["operators"].values()))
        self.assertEqual(sum(picks.values()), len(SAMPLE_MATCH["rounds"]) * len(TEAM0 + TEAM1))
        from sample_data import ATTACK_OPS, DEFENSE_OPS

        sides = {r["Operator"]: r["Side"] for r in at.dataframe[0].value.to_dict("records")}
        self.assertEqual(sides, {**{op: "⚔\ufe0e Attack" for op in ATTACK_OPS.values()},
                                 **{op: "♜ Defense" for op in DEFENSE_OPS.values()}})
        at.session_state["r6_last_match"] = SAMPLE_MATCH  # opened on the Dashboard
        at = at.run()
        self.assertFalse(any("newest match" in c.value for c in at.caption))

    def test_track_a_school_roster(self):
        from necc_data import normalize_school_catalog
        from season_stats import StatsManager

        at = run_app()
        at.session_state["necc_schools"] = normalize_school_catalog({"schools": [{"name": "Test U", "teams": [
            {"name": "Varsity", "game": "Rainbow Six Siege", "roster": ["_Sniper_", "Bravo"]}]}]})
        at.switch_page("league.py").run()
        at = next(r for r in at.radio if r.label == "Where do you compete?").set_value("NECC").run()
        at = next(b for b in at.button if b.label == "Track this roster").click().run()
        self.assertFalse(at.exception)
        self.assertIn("Added 2 players to Varsity", at.success[0].value)
        with StatsManager() as manager:  # this test's own database, not a real one
            self.assertEqual(sorted(manager.tracked_players()), ["Bravo", "_Sniper_"])

    def test_season_teams_are_team_stats_and_eps_is_worked_out_again(self):
        from metrics_engine import compute_match_metrics
        from sample_data import SAMPLE_MATCH, TEAM0
        from season_stats import StatsManager

        at = with_matches(run_app())
        with StatsManager(season="current") as tracker:
            tracker.add_players(TEAM0, team="Squad")
            for match, _raw, _warnings in at.session_state["source"]["parsed"].values():
                tracker.log_match(match)
            tracker._conn.execute("DELETE FROM match_ratings")  # saved before the tracker kept EPS
            tracker._conn.commit()
        at.switch_page("teams.py").run()
        self.assertFalse(at.exception)
        self.assertEqual({m.label: m.value for m in at.metric}["Maps"], "2")
        roster = at.dataframe[1].value
        eps = {p: str(s.eps) for p, s in compute_match_metrics(SAMPLE_MATCH).items()}  # as the scoreboard shows it
        self.assertEqual(dict(zip(roster["Player"], roster["EPS"])), {p: eps[p] for p in TEAM0})  # not "—"
        self.assertEqual(dict(zip(roster["Player"], roster["All-time EPS"])), {p: eps[p] for p in TEAM0})
        teams = at.dataframe[2].value
        self.assertIn("Back to even %", teams.columns)
        self.assertNotIn("K/D", teams.columns)

    def test_pick_the_matches_that_count_for_the_season(self):
        from sample_data import SAMPLE_MATCH, TEAM0
        from season_stats import StatsManager

        at = with_matches(run_app(), count=3)
        parsed = at.session_state["source"]["parsed"]
        ids = [match["match_id"] for match, _raw, _warnings in parsed.values()]
        with StatsManager(season="current") as tracker:
            tracker.add_players(TEAM0, team="Squad")
            for match, _raw, _warnings in list(parsed.values())[:2]:  # two Game Day matches saved
                tracker.log_match(match)
        at.switch_page("teams.py").run()
        self.assertFalse(at.exception)
        boxes = {i: at.checkbox(key=f"in_season_current_{i}") for i in ids}
        self.assertEqual([boxes[i].value for i in ids], [True, True, False])
        self.assertEqual({m.label: m.value for m in at.metric}["Maps"], "2")
        # the second was practice after all, and the third was Game Day
        boxes[ids[1]].uncheck()
        boxes[ids[2]].check()
        at = next(b for b in at.button if b.label == "Save season matches").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.success[0].value, "Season matches saved: added 1 and took out 1.")
        with StatsManager(season="current") as tracker:
            self.assertEqual({m["match_id"] for m in tracker.match_history()}, {ids[0], ids[2]})
            rounds = {p.username: p.totals["rounds_played"] for p in tracker.all_player_stats()}
        self.assertEqual(rounds, {p: 2 * len(SAMPLE_MATCH["rounds"]) for p in TEAM0})  # two matches' worth
        self.assertEqual([at.checkbox(key=f"in_season_current_{i}").value for i in ids], [True, False, True])
        self.assertEqual({m.label: m.value for m in at.metric}["Maps"], "2")

    def test_a_season_match_saves_only_the_players_picked_for_it(self):
        from sample_data import TEAM0
        from season_stats import StatsManager
        from stats_db import StatsDB

        at = with_matches(run_app(), count=1, added=False)
        name, (match, _raw, _warnings) = next(iter(at.session_state["source"]["parsed"].items()))
        with StatsDB() as db:  # added on the Dashboard with three of the team: the other two were subs today
            db.import_match(name, match, files=1, players=TEAM0[:3])
        with StatsManager(season="current") as tracker:
            tracker.add_players(TEAM0, team="Squad")
        at.switch_page("teams.py").run()
        at.slider(key="season_need_current_Squad").set_value(1).run()
        at.checkbox(key=f"in_season_current_{match['match_id']}").check()
        at = next(b for b in at.button if b.label == "Save season matches").click().run()
        self.assertFalse(at.exception)
        with StatsManager(season="current") as tracker:
            self.assertEqual(sorted(p.username for p in tracker.all_player_stats()), sorted(TEAM0[:3]))

    def test_each_season_team_counts_only_its_own_matches(self):
        from metrics_engine import compute_match_metrics
        from sample_data import TEAM0, TEAM1
        from season_stats import StatsManager

        at = with_matches(run_app())
        source = at.session_state["source"]
        (name, (first, raw, warnings)), (_, (second, _, _)) = source["parsed"].items()
        first["rounds"] = first["rounds"][:-3]  # a different match, so its EPS differs too
        source["parsed"][name] = (first, raw, warnings)
        at.session_state["source"] = source
        with StatsManager(season="current") as tracker:
            tracker.add_players(TEAM0, team="Squad")
            tracker.log_match(first)  # only Squad was tracked then
            tracker.add_players(TEAM1, team="Rivals")
            tracker.log_match(second)  # both teams count this one
        at.switch_page("teams.py").run()
        self.assertFalse(at.exception)
        teams = next(d.value for d in at.dataframe if "Team" in d.value.columns)  # All season teams
        self.assertEqual(dict(zip(teams["Team"], teams["Maps"])), {"Rivals": 1, "Squad": 2})
        # a Rivals player's season EPS is from the one match counted for Rivals, like their other numbers
        at = next(s for s in at.selectbox if s.label == "Season team").set_value("Rivals").run()
        roster = next(d.value for d in at.dataframe if "K / D / A" in d.value.columns)
        eps = {p: str(s.eps) for p, s in compute_match_metrics(second).items()}  # as the scoreboard shows it
        self.assertEqual(dict(zip(roster["Player"], roster["EPS"])), {p: eps[p] for p in TEAM1})

    def test_a_counted_match_shows_whatever_the_filter(self):
        from sample_data import TEAM0, TEAM1
        from season_stats import StatsManager

        at = with_matches(run_app())
        counted, other = [m for m, _raw, _warnings in at.session_state["source"]["parsed"].values()]
        squad = TEAM0[:3] + TEAM1[:2]  # at most 3 of them on one side in any match
        with StatsManager(season="current") as tracker:
            tracker.add_players(squad, team="Squad")
            tracker.log_match(counted)
        at.switch_page("teams.py").run()
        at = at.slider(key="season_need_current_Squad").set_value(5).run()  # more than ever played together
        box = at.checkbox(key=f"in_season_current_{counted['match_id']}")
        self.assertTrue(box.value)
        self.assertIn("3 of the team", box.label)  # listed as itself, not as missing from the database
        self.assertNotIn("not in your stats database", box.label)
        keys = [c.key for c in at.checkbox if str(c.key).startswith("in_season_current_")]
        self.assertEqual(keys, [f"in_season_current_{counted['match_id']}"])  # the uncounted one is filtered out

    def test_a_match_whose_replay_is_gone_cant_be_added(self):
        from sample_data import TEAM0
        from season_stats import StatsManager

        at = with_matches(run_app())
        with StatsManager(season="current") as tracker:
            tracker.add_players(TEAM0, team="Squad")
        at.switch_page("teams.py").run()  # both matches are in the stats database now
        names = list(at.session_state["source"]["groups"])
        ids = [match["match_id"] for match, _raw, _warnings in at.session_state["source"]["parsed"].values()]
        source = at.session_state["source"]
        del source["groups"][names[1]], source["parsed"][names[1]]  # the game deleted the second replay
        at.session_state["source"] = source
        for i in ids:
            at.checkbox(key=f"in_season_current_{i}").check()
        at = next(b for b in at.button if b.label == "Save season matches").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.success[0].value, "Season matches saved: added 1.")
        self.assertIn("aren't on this PC anymore", at.warning[0].value)
        with StatsManager(season="current") as tracker:
            self.assertEqual([m["match_id"] for m in tracker.match_history()], [ids[0]])

    def test_a_tracked_team_without_matches_can_pick_them(self):
        from sample_data import TEAM0
        from season_stats import StatsManager

        at = with_matches(run_app())
        with StatsManager(season="current") as tracker:
            tracker.add_players(TEAM0, team="Squad")  # tracked, nothing saved yet
        at.switch_page("teams.py").run()
        self.assertFalse(at.exception)
        self.assertEqual(next(s for s in at.selectbox if s.label == "Season team").value, "Squad")
        self.assertTrue(any("No matches count for Squad" in i.value for i in at.info))
        self.assertEqual(len([c for c in at.checkbox if str(c.key).startswith("in_season_current_")]), 2)

    def test_season_teams_tab(self):
        at = run_app()
        at.switch_page("teams.py").run()
        self.assertFalse(at.exception)
        self.assertEqual([t.label for t in at.tabs], ["Build a team", "Season teams", "Map pool"])

    def test_a_season_team_of_one_player(self):
        """A one-player season team has no "at least this many of the team" to choose: the page
        shows its matches instead of crashing on a slider from 1 to 1."""
        from season_stats import StatsManager

        with StatsManager(season="current") as tracker:
            tracker.add_players(["Solo"], team="Solo Squad")
        at = run_app()
        at.switch_page("teams.py").run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertFalse([s for s in at.slider if s.label.startswith("Show matches with at least")])


class TestAddingMatches(unittest.TestCase):
    """Nothing goes into the stats database on its own: a match is added only once it's confirmed on
    the Dashboard, with just the players picked for it."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(dir=_DB_FOLDER))  # the game's replay folder, found on this PC
        self.matches = demo_matches(2)
        for patch in (
            mock.patch.object(season_stats, "DEFAULT_DB_PATH", Path(tempfile.mkdtemp(dir=_DB_FOLDER)) / "stats.db"),
            mock.patch.object(replay_parser, "find_replay_folders", return_value=[self.folder]),
            mock.patch.object(app_info, "is_loopback", return_value=True),  # on this PC for every run, not just the first
            mock.patch("branding.is_loopback", return_value=True),  # (branding keeps its own reference)
            mock.patch("sources.collect_rec_files", side_effect=lambda path, workdir, scanner: [
                str(path / name / f"{name}-R01.rec") for name in self.matches]),
            mock.patch("sources.parse_match",
                       side_effect=lambda recs: (self.matches[Path(recs[0]).parent.name], {}, [])),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    @staticmethod
    def stats() -> dict[str, list[str]]:
        """{match id: the players kept in it} in the stats database."""
        from stats_db import StatsDB

        with StatsDB() as db:
            return db.players_by_match()

    @staticmethod
    def picker(at: AppTest, team: int):
        return next(m for m in at.multiselect if str(m.key).startswith("confirm_players:")
                    and str(m.key).endswith(f":{team}"))

    @staticmethod
    def confirm(at: AppTest):
        return next(b for b in at.button if b.label in ("Confirm match and players", "Update players"))

    def test_opening_a_match_adds_nothing(self):
        at = run_app()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.get("selectbox")[0].options), 2)  # both matches, the newest open
        self.assertEqual([self.picker(at, team).value for team in (0, 1)], [[], []])  # nobody picked for you
        self.assertTrue(self.confirm(at).disabled)
        for page in ("ask.py", "history.py", "operators.py", "teams.py"):
            with self.subTest(page=page):
                at.switch_page(page).run()
                self.assertFalse(at.exception)
                self.assertTrue(any("No matches yet" in i.value for i in at.info))
        self.assertEqual(self.stats(), {})

    def test_only_the_players_picked_are_added(self):
        from sample_data import TEAM0

        at = run_app()
        self.picker(at, 0).set_value(TEAM0[:3]).run()
        at = self.confirm(at).click().run()
        self.assertFalse(at.exception)
        self.assertIn("Added to your stats, tracking 3 players: Fabian, Kanto, Sens.", at.success[0].value)
        self.assertEqual(self.stats(), {"demo-1": ["Fabian", "Kanto", "Sens"]})  # the newest match, nobody else
        self.assertTrue(any("In your stats, tracking" in c.value for c in at.caption))
        self.assertEqual(self.confirm(at).label, "Update players")
        self.assertTrue(at.get("selectbox")[0].format_func("Match-2026-09-21_20-00-00-1").endswith("✓ added"))
        at.switch_page("history.py").run()
        self.assertEqual(list(at.dataframe[0].value["Tracked"]), ["Fabian, Kanto, Sens"])

    def test_confirming_again_replaces_the_players(self):
        from sample_data import TEAM0

        at = run_app()
        self.picker(at, 0).set_value(TEAM0).run()
        at = self.confirm(at).click().run()
        self.picker(at, 0).set_value(TEAM0[:1]).run()
        at = self.confirm(at).click().run()
        self.assertEqual(self.stats(), {"demo-1": ["Fabian"]})

    def test_a_matchmade_match_warns_that_a_teammate_may_be_a_stranger(self):
        at = run_app()
        self.assertTrue(any("**Ranked** match" in w.value for w in at.warning))
        self.matches = {name: dict(match, match_type="Custom game") for name, match in self.matches.items()}
        at = run_app()
        self.assertFalse(any("Pick only the players you track" in w.value for w in at.warning))

    def test_saving_to_a_season_logs_only_the_picked_players(self):
        from sample_data import TEAM0, TEAM1
        from season_stats import StatsManager

        with StatsManager(season="S1") as tracker:  # tracked earlier; one of them is on the other team today
            tracker.add_players(TEAM0 + TEAM1[:1], team="Varsity")
        at = run_app()
        self.picker(at, 0).set_value(TEAM0[:3]).run()
        at.checkbox(key="confirm_season:demo-1").check().run()
        at.text_input(key="r6_season_input").set_value("S1").run()
        at = self.confirm(at).click().run()
        self.assertFalse(at.exception)
        self.assertIn("player-rounds to S1", at.success[0].value)
        with StatsManager(season="S1") as tracker:
            logged = {r["username"] for r in tracker._conn.execute("SELECT DISTINCT username FROM logged_rounds")}
        self.assertEqual(logged, set(TEAM0[:3]))
        self.assertEqual(self.stats(), {"demo-1": ["Fabian", "Kanto", "Sens"]})

    def test_a_replays_generic_label_is_never_offered_as_the_team(self):
        from sample_data import TEAM0

        self.matches = {name: dict(match, team_names=["YOUR TEAM", "ENEMY TEAM"])
                        for name, match in self.matches.items()}
        at = run_app()
        self.picker(at, 0).set_value(TEAM0[:1]).run()
        at.checkbox(key="confirm_season:demo-1").check().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.text_input(key="confirm_team_name:demo-1:0").value, "")  # asks for the real name
        self.assertNotIn("confirm_team_name:demo-1:1", [t.key for t in at.text_input])  # nobody picked there


    def save_team(self, team: str, players: list[str]) -> None:
        """Your team, as League & Team saves it: the league, and the team's players."""
        import branding
        from stats_db import StatsDB

        patch = mock.patch.object(branding, "DEFAULT_DB_PATH", season_stats.DEFAULT_DB_PATH)
        patch.start()
        self.addCleanup(patch.stop)
        branding.save_competition(dict(branding.DEFAULT_COMPETITION, source="custom", name="Scrim League", team=team))
        with StatsDB() as db:
            db.save_roster(team, players)

    def test_your_teams_players_are_picked_and_its_side_named(self):
        from sample_data import TEAM0

        self.matches = {name: dict(match, team_names=["YOUR TEAM", "ENEMY TEAM"], match_type="Custom game")
                        for name, match in self.matches.items()}
        self.save_team("Lakeside", [p.upper() for p in TEAM0[:4]])  # any case
        at = run_app()
        self.assertFalse(at.exception)
        self.assertEqual(self.picker(at, 0).value, TEAM0[:4])
        self.assertEqual(self.picker(at, 1).value, [])  # never anyone else
        self.assertEqual(self.picker(at, 0).label, "Lakeside")  # not "YOUR TEAM"
        self.assertTrue(any("Picked from **Lakeside**'s roster" in c.value for c in at.caption))
        scorecard = next(m.value for m in at.markdown if 'class="scorecard"' in m.value)
        self.assertIn("Lakeside", scorecard)
        self.assertIn('class="sc-you"', scorecard)
        self.assertTrue(at.checkbox(key="confirm_season:demo-1").value)  # your team's custom game: the season too
        at = self.confirm(at).click().run()
        self.assertFalse(at.exception)
        self.assertEqual(self.stats(), {"demo-1": sorted(TEAM0[:4], key=str.casefold)})
        self.assertIn("player-rounds to current", at.success[0].value)

    def test_a_ranked_match_isnt_saved_to_the_season_for_you(self):
        from sample_data import TEAM0

        self.save_team("Lakeside", TEAM0[:4])
        at = run_app()
        self.assertEqual(self.picker(at, 0).value, TEAM0[:4])
        self.assertFalse(at.checkbox(key="confirm_season:demo-1").value)
        self.assertTrue(any("**Ranked** match" in w.value for w in at.warning))
        self.assertEqual(self.stats(), {})  # and still nothing until it's confirmed

    def test_the_replay_folder_you_pick_stays(self):
        other = Path(tempfile.mkdtemp(dir=_DB_FOLDER))
        at = run_app()
        self.assertEqual(at.text_input(key="replay_folder").value, str(self.folder))  # the game's, found
        at.text_input(key="replay_folder").set_value(str(other)).run()
        at.switch_page("history.py").run()
        at.switch_page("report.py").run()
        self.assertFalse(at.exception)
        self.assertEqual(at.text_input(key="replay_folder").value, str(other))  # not back to the game's folder
        self.assertEqual(run_app().text_input(key="replay_folder").value, str(other))  # nor after a restart
        at = next(b for b in at.button if b.label == "Use the folder Siege saves to").click().run()
        self.assertEqual(at.text_input(key="replay_folder").value, str(self.folder))
        self.assertEqual(run_app().text_input(key="replay_folder").value, str(self.folder))

    def test_a_folder_that_isnt_there_isnt_kept(self):
        at = run_app()
        at.text_input(key="replay_folder").set_value(str(self.folder / "nope")).run()
        self.assertTrue(any("Not found" in e.value for e in at.error))
        self.assertEqual(run_app().text_input(key="replay_folder").value, str(self.folder))


class TestCheckingEarlierMatches(unittest.TestCase):
    """Matches an earlier version added on its own, with every player in them, wait on Match History
    until they're checked."""

    def setUp(self):
        patch = mock.patch.object(season_stats, "DEFAULT_DB_PATH", Path(tempfile.mkdtemp(dir=_DB_FOLDER)) / "stats.db")
        patch.start()
        self.addCleanup(patch.stop)

    @staticmethod
    def added_by_an_earlier_version(at: AppTest) -> AppTest:
        from stats_db import StatsDB

        at = with_matches(at)
        with StatsDB() as db:  # as an earlier version left it: the next open holds its matches back
            db._conn.execute("DELETE FROM stats_meta WHERE key = 'data'")
            db._conn.commit()
        return at

    def test_they_dont_count_until_checked(self):
        from sample_data import TEAM0

        at = self.added_by_an_earlier_version(run_app())
        at.switch_page("ask.py").run()
        self.assertIn("2 matches added by an earlier version are waiting", at.info[0].value)
        at.switch_page("history.py").run()
        self.assertFalse(at.exception)
        self.assertIn("2 matches added by an earlier version don't count yet", at.warning[0].value)
        self.assertEqual(len(at.dataframe), 0)
        self.assertEqual(at.multiselect(key="check:demo-1:0").value, [])
        at.multiselect(key="check:demo-1:0").set_value(TEAM0[:2]).run()
        at = next(b for b in at.button if b.label == "Keep these players").click().run()
        self.assertFalse(at.exception)
        self.assertIn("1 match added by an earlier version doesn't count yet", at.warning[0].value)
        self.assertEqual(list(at.dataframe[0].value["Tracked"]), ["Fabian, Kanto"])

    def test_keep_just_your_own_stats_in_all_of_them(self):
        at = self.added_by_an_earlier_version(run_app())
        at.switch_page("history.py").run()
        at = next(b for b in at.button if b.label == "Keep just your own stats in all 2").click().run()
        self.assertFalse(at.exception)
        self.assertIn("Kept your own stats in 2 matches.", at.success[0].value)
        self.assertEqual(list(at.dataframe[0].value["Tracked"]), ["Fabian", "Fabian"])
        self.assertEqual({m.label: m.value for m in at.metric}["Matches"], "2")

    def test_remove_them(self):
        at = self.added_by_an_earlier_version(run_app())
        at.switch_page("history.py").run()
        at = next(b for b in at.button if b.label == "Remove this match").click().run()
        self.assertIn("1 match added by an earlier version", at.warning[0].value)
        at = next(b for b in at.button if b.label == "Yes, remove all 1").click().run()
        self.assertFalse(at.exception)
        self.assertFalse(at.warning)
        self.assertTrue(any("No matches yet" in i.value for i in at.info))


class TestMatchLabels(unittest.TestCase):
    def test_names_show_as_written_in_markdown(self):
        from ui import md

        self.assertEqual(md("_Sniper_"), r"\_Sniper\_")  # not an italic "Sniper"
        self.assertEqual(md("Rook-_- has 3 kills"), r"Rook-\_- has 3 kills")
        self.assertEqual(md("Paltry.FBRD: 67% (3 matches)."), "Paltry.FBRD: 67% (3 matches).")

    def test_labels(self):
        from sources import match_label
        from stats_db import nice_time

        name = "Match-2026-09-25_22-55-04-8956"  # the Dashboard's picker, before the match is imported
        self.assertEqual(match_label(name), nice_time("2026-09-25 22:55:04"))
        row = {"played_at": "2026-09-25 22:55:04", "map": "Bank", "team": 1, "score0": 2, "score1": 4, "won": 1}
        self.assertEqual(match_label(name, row), f"{nice_time('2026-09-25 22:55:04')} · Bank · Won 4–2")
        self.assertEqual(match_label("my upload"), "my upload")


class TestWindowsAppBundle(unittest.TestCase):
    """The Windows app ships the pages as plain files, listed in desktop/R6MatchStats.spec, and
    PyInstaller can't see what plain files import: anything missing only fails in the installed app."""

    def test_every_file_and_module_the_pages_need_is_bundled(self):
        import ast
        import re
        import sys

        scripts = Path(__file__).resolve().parent
        spec = (scripts.parent / "desktop" / "R6MatchStats.spec").read_text(encoding="utf-8")
        files = set(re.findall(r'"(\w+\.py)"', spec.split("datas")[0]))
        stdlib = set(re.findall(r'"([\w.]+)"', spec.split("standard library")[1].split("]")[0]))
        local = {p.name for p in scripts.glob("*.py")}
        needed, imports, todo = set(), set(), ["app.py"]
        while todo:
            name = todo.pop()
            if name in needed:
                continue
            needed.add(name)
            source = (scripts / name).read_text(encoding="utf-8")
            found = set(re.findall(r'st\.Page\("(\w+\.py)"', source))
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Import):
                    imports |= {a.name for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    imports.add(node.module)
            found |= {m.split(".")[0] + ".py" for m in imports}
            todo += [f for f in found & local if f not in needed]
        self.assertEqual(sorted(needed - files), [])
        used = {m for m in imports if m.split(".")[0] in sys.stdlib_module_names} - {"__future__"}
        self.assertEqual(sorted(used - stdlib), [])


class TestDockedView(unittest.TestCase):
    """The Windows app docked to the edge of the screen: its compact page (dock.py) and the buttons
    that dock and undock the window."""

    def setUp(self):
        self.appdata = tempfile.mkdtemp(dir=_DB_FOLDER)  # where the window's commands are written
        for patch in (mock.patch.object(season_stats, "DEFAULT_DB_PATH",
                                        Path(tempfile.mkdtemp(dir=_DB_FOLDER)) / "stats.db"),
                      mock.patch.dict(os.environ, {"LOCALAPPDATA": self.appdata})):
            patch.start()
            self.addCleanup(patch.stop)

    def commands(self) -> list[dict]:
        folder = Path(self.appdata, "R6MatchStats", app_info.WINDOW_COMMANDS)
        return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(folder.glob("*.json"))]

    @staticmethod
    def docked(at: AppTest, edge: str = "right") -> AppTest:
        at.query_params["view"] = "dock"
        at.query_params["edge"] = edge
        return at.run()

    def test_shows_the_latest_match_round_by_round(self):
        from sample_data import SAMPLE_MATCH

        at = self.docked(with_matches(run_app()))
        self.assertFalse(at.exception)
        page = "".join(m.value for m in at.markdown)
        self.assertIn("Latest match", page)
        self.assertEqual(page.count('class="rt '), len(SAMPLE_MATCH["rounds"]))  # a tile per round...
        self.assertIn('class="rt attack won"', page)  # ...with its side and result
        self.assertIn('class="rt defense lost"', page)
        self.assertIn('<span class="dk-wl win">WIN</span>', page)  # Fabian's team won 6-3
        self.assertIn("6<i>–</i>3", page)
        self.assertIn("Last 2 matches", page)
        self.assertIn("As Fabian", page)
        self.assertNotIn('<div class="identity-banner">', page)  # no school banner in the narrow panel
        self.assertNotIn("Not in your stats yet", page)
        self.assertEqual([b.label for b in at.button], ["⇱ Full window"])  # docking itself needs the Windows app

    def test_a_match_the_game_saved_waits_to_be_added(self):
        at = self.docked(with_matches(run_app(), added=False))
        self.assertFalse(at.exception)
        page = "".join(m.value for m in at.markdown)
        self.assertIn("New match", page)
        self.assertIn("Not in your stats yet", page)
        self.assertNotIn("Latest match", page)  # nothing's in your stats until you add it

    def test_without_matches_says_why(self):
        at = self.docked(run_app())
        self.assertFalse(at.exception)
        self.assertIn("replay folder wasn't found", at.info[0].value)

    def test_full_window_outside_the_windows_app_goes_back_to_the_dashboard(self):
        at = self.docked(run_app())
        at = at.button(key="undock").click().run()
        self.assertFalse(at.exception)
        self.assertNotIn("view", at.query_params)
        self.assertIn("Dashboard", [t.value for t in at.title])
        self.assertEqual(self.commands(), [])  # there's no window to send them to

    def test_dock_buttons_tell_the_window(self):
        with mock.patch.object(app_info, "can_dock", return_value=True):
            at = run_app()
            at.button(key="dock_right").click().run()
            self.assertEqual(self.commands(), [{"command": "dock", "edge": "right"}])
            at = self.docked(at, "right")
            self.assertEqual([b.label for b in at.button], ["⇄ Dock left", "⇱ Full window"])
            at.button(key="dock_other").click().run()
            at.button(key="undock").click().run()
        self.assertEqual(self.commands()[1:], [{"command": "dock", "edge": "left"}, {"command": "undock"}])

    def test_no_dock_buttons_outside_the_windows_app(self):
        at = run_app()
        self.assertFalse(at.exception)
        self.assertEqual([b for b in at.button if str(b.key).startswith("dock_")], [])

RELEASE = {"version": "9.9.0", "url": "https://github.com/o/r/releases/tag/v9.9.0",
           "installer": "https://github.com/o/r/releases/download/v9.9.0/R6MatchStats-Setup.exe",
           "zip": "https://github.com/o/r/releases/download/v9.9.0/R6MatchStats-Windows.zip"}


def download_page(release, **env: str) -> AppTest:
    """The download page, with GitHub's newest release mocked: a dict, None (no
    release yet) or an OSError (GitHub unreachable)."""
    st.cache_data.clear()
    lookup = {"side_effect": release} if isinstance(release, Exception) else {"return_value": release}
    with mock.patch.dict(os.environ, env), \
            mock.patch.object(app_info, "is_loopback", return_value=True), \
            mock.patch.object(app_info, "latest_release", **lookup):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        at.switch_page("download.py").run()
    return at


def links(at: AppTest) -> list[str]:
    return [b.proto.url for b in at.get("link_button")]


class TestDownloadPage(unittest.TestCase):
    def test_links_to_the_newest_installer(self):
        at = download_page(RELEASE, R6_HOSTED="1")
        self.assertFalse(at.exception)
        self.assertEqual(links(at), [RELEASE["installer"]])
        self.assertIn(RELEASE["zip"], " ".join(c.value for c in at.caption))

    def test_unsigned_release_explains_the_windows_warning(self):
        at = download_page(RELEASE, R6_HOSTED="1")
        install = next(m.value for m in at.markdown if "Download **" in m.value)
        self.assertIn("isn't code-signed yet", install)

    def test_signed_release_names_its_publisher_and_thumbprint(self):
        thumbprint = "0123456789ABCDEF0123456789ABCDEF01234567"
        at = download_page({**RELEASE, "signer": {"name": "Jane_Doe", "thumbprint": thumbprint}}, R6_HOSTED="1")
        self.assertFalse(at.exception)
        install = next(m.value for m in at.markdown if "Download **" in m.value)
        self.assertNotIn("isn't code-signed", install)
        self.assertIn("publisher is **Jane\\_Doe**", install)  # shown as written, not as Markdown
        self.assertIn(thumbprint, [c.value for c in at.code])

    def test_no_release_yet_says_so_instead_of_a_broken_link(self):
        at = download_page(None, R6_HOSTED="1")
        self.assertFalse(at.exception)
        self.assertEqual(len(at.warning), 1)
        self.assertNotIn(app_info.WINDOWS_DOWNLOAD_URL, links(at))

    def test_github_unreachable_falls_back_to_the_latest_release_link(self):
        at = download_page(OSError("offline"), R6_HOSTED="1")
        self.assertFalse(at.exception)
        self.assertEqual(links(at), [app_info.WINDOWS_DOWNLOAD_URL, app_info.BROWSER_EDITION_URL])
        self.assertTrue(app_info.WINDOWS_DOWNLOAD_URL.endswith("/releases/latest/download/" + app_info.WINDOWS_INSTALLER))
        self.assertTrue(app_info.BROWSER_EDITION_URL.endswith("/releases/latest/download/" + app_info.BROWSER_EDITION))

    def test_links_the_browser_edition_when_the_release_has_it(self):
        browser = "https://github.com/o/r/releases/download/v9.9.0/R6MatchStats-Browser.html"
        at = download_page({**RELEASE, "browser": browser}, R6_HOSTED="1")
        self.assertFalse(at.exception)
        self.assertEqual(links(at), [RELEASE["installer"], browser])
        self.assertTrue(any(h.value == "Use it in your browser" for h in at.subheader))
        # a release from before 1.6.0 has no browser edition: no link to a file that isn't there
        self.assertNotIn("Use it in your browser", [h.value for h in download_page(RELEASE, R6_HOSTED="1").subheader])

    def test_windows_app_offers_a_newer_version(self):
        at = download_page(RELEASE, R6_DESKTOP="1")
        self.assertFalse(at.exception)
        self.assertEqual(links(at), [RELEASE["installer"]])
        self.assertIn("9.9.0", at.info[0].value)

    def test_windows_app_up_to_date(self):
        at = download_page({**RELEASE, "version": app_info.APP_VERSION}, R6_DESKTOP="1")
        self.assertEqual(links(at), [])
        self.assertEqual(len(at.info), 0)


class TestLatestRelease(unittest.TestCase):
    def fetch(self, releases):
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(json.dumps(releases).encode())
        with mock.patch("urllib.request.urlopen", return_value=response):
            return app_info.latest_release("o/r")

    @staticmethod
    def release(tag, *assets, **flags):
        return {"tag_name": tag, "html_url": f"https://github.com/o/r/releases/tag/{tag}", **flags,
                "assets": [{"name": a, "browser_download_url": f"https://dl/{tag}/{a}"} for a in assets]}

    def test_skips_releases_without_the_app_drafts_and_prereleases(self):
        found = self.fetch([
            self.release("v3.0.0", "R6MatchStats-Setup.exe", prerelease=True),
            self.release("v2.1.0", "r6-dissect-windows-amd64.zip"),
            self.release("v2.0.0", "R6MatchStats-Setup.exe", "R6MatchStats-Windows.zip"),
        ])
        self.assertEqual(found["version"], "2.0.0")
        self.assertEqual(found["installer"], "https://dl/v2.0.0/R6MatchStats-Setup.exe")
        self.assertEqual(found["zip"], "https://dl/v2.0.0/R6MatchStats-Windows.zip")

    def test_no_releases(self):
        self.assertIsNone(self.fetch([]))

    def test_version_order(self):
        self.assertGreater(app_info.version_tuple("1.10.0"), app_info.version_tuple("1.9.2"))
        self.assertEqual(app_info.version_tuple("1.0"), (1, 0))

    def test_any_tag_style_gives_the_version(self):
        for tag in ("v1.2.0", "V1.2.0", "app-v1.2.0", "1.2.0", "release-1.2.0"):
            self.assertEqual(app_info.release_version(tag), "1.2.0", tag)
        self.assertGreater(app_info.version_tuple("app-v1.2.0"), app_info.version_tuple("1.1.9"))

    def test_published_checksum(self):
        digest = "a" * 64
        releases = io.BytesIO(json.dumps([self.release("v2.0.0", "R6MatchStats-Setup.exe", "SHA256SUMS.txt")]).encode())
        sums = io.BytesIO(f"{digest}  R6MatchStats-Setup.exe\n{'b' * 64}  R6MatchStats-Windows.zip\n".encode())
        responses = []
        for body in (releases, sums):
            response = mock.MagicMock()
            response.__enter__.return_value = body
            responses.append(response)
        with mock.patch("urllib.request.urlopen", side_effect=responses):
            self.assertEqual(app_info.latest_release("o/r")["sha256"], digest)

    def signer(self, signature: str) -> dict | None:
        """latest_release's "signer" for a release whose SIGNATURE.txt says `signature`."""
        releases = io.BytesIO(json.dumps([self.release("v2.0.0", "R6MatchStats-Setup.exe", "SIGNATURE.txt")]).encode())
        responses = []
        for body in (releases, io.BytesIO(signature.encode("utf-8-sig"))):  # PowerShell 5 writes a BOM
            response = mock.MagicMock()
            response.__enter__.return_value = body
            responses.append(response)
        with mock.patch("urllib.request.urlopen", side_effect=responses):
            return app_info.latest_release("o/r")["signer"]

    def test_published_signer(self):
        thumbprint = "0123456789abcdef0123456789abcdef01234567"
        self.assertEqual(self.signer(f"Signed by: Jane Doe\r\nThumbprint: {thumbprint}\r\n"),
                         {"name": "Jane Doe", "thumbprint": thumbprint.upper()})
        self.assertIsNone(self.signer("Signed by: Jane Doe\nThumbprint: not-a-thumbprint\n"))
        self.assertIsNone(self.signer("garbage"))

    def test_no_signature_file_means_unsigned(self):
        releases = io.BytesIO(json.dumps([self.release("v2.0.0", "R6MatchStats-Setup.exe")]).encode())
        response = mock.MagicMock()
        response.__enter__.return_value = releases
        with mock.patch("urllib.request.urlopen", return_value=response):
            self.assertIsNone(app_info.latest_release("o/r")["signer"])


class TestAppInfo(unittest.TestCase):
    def test_loopback(self):
        for ip in (None, "127.0.0.1", "::1", "::ffff:127.0.0.1"):
            self.assertTrue(app_info.is_loopback(ip), ip)
        for ip in ("10.0.0.5", "::ffff:34.12.1.9", "2001:db8::1", "not an ip"):
            self.assertFalse(app_info.is_loopback(ip), ip)

    def test_repo_override(self):
        with mock.patch.dict(os.environ, {"R6_GITHUB_REPO": "someone/r6-stats"}):
            self.assertEqual(app_info.github_repo(), "someone/r6-stats")


if __name__ == "__main__":
    unittest.main()
