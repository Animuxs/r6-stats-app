"""Odd but real inputs, everywhere the app takes them in: replays with a strange shape, every kind
of question to Ask, awkward player and team names, and damaged settings, catalogs and backups.

Each test runs the same pipeline the app does, so a replay that parses must also make it through the
scoreboard, the round by round view, the stats database, the season tracker, Ask and the docked
view. Anything that's turned away must be turned away with the error the app shows (ValueError,
ReplayParseError, StatsError, ...), never a crash. The random cases are seeded: every run tries the
same ones, so a failure can be run again.

Run from the repo root:  python -m unittest discover -s scripts"""

from __future__ import annotations

import copy
import json
import random
import shutil
import struct
import tempfile
import os
import unittest
import zlib
from datetime import datetime
from pathlib import Path
from unittest import mock

import branding
import dock
import events
import metrics_engine as me
import necc_data
import rosters
from ask_engine import METRIC_WORDS, Vocab, ask, fmt, suggestions
from matchday_data import MatchdayWorkspace, panel_html
import parser as replay_parser
from parser import ReplayParseError, normalize_from_r6_dissect
from season_stats import StatsManager
from stats_db import NotAdded, StatsDB

ODD_NAMES = ["O'Brien", 'Quote"d', "Ünïcødé", "🔥Blaze", "x" * 40, "Robert'); DROP TABLE players;--",
             "<script>alert(1)</script>", "  spaced  ", "fabian"]  # (and "Fabian": names that differ in case)
NOW = datetime(2026, 10, 16, 12, 0)


# ------------------------------------------------------------ replays ----
def raw_round(n: int, team0: list[str], team1: list[str], feed: list[dict], winner: int | None = 0,
              attack: int = 0, **extra) -> dict:
    """One round as r6-dissect writes it."""
    teams = [{"name": "Alpha", "score": 0, "won": winner == 0, "role": "Attack" if attack == 0 else "Defense",
              "winCondition": "KilledOpponents"},
             {"name": "Bravo", "score": 0, "won": winner == 1, "role": "Attack" if attack == 1 else "Defense",
              "winCondition": "Time"}]
    players = [{"username": u, "teamIndex": 0, "operator": {"name": "Ash"}} for u in team0] + \
              [{"username": u, "teamIndex": 1, "operator": {"name": "Jäger"}} for u in team1]
    return {"roundNumber": n, "teams": teams, "players": players, "matchFeedback": feed,
            "map": {"name": "BorderY10", "id": 1}, "matchID": "m-odd", "timestamp": "2026-10-01T20:00:00Z",
            "matchType": {"name": "Ranked"}, "stats": [{"username": u, "assists": 1} for u in team0], **extra}


def kill(clock: float, killer: str | None, target: str | None, headshot: bool = False) -> dict:
    event = {"type": {"name": "Kill", "id": 0}, "timeInSeconds": clock, "headshot": headshot}
    if killer is not None:
        event["username"] = killer
    if target is not None:
        event["target"] = target
    return event


def other(name: str, clock: float, username: str | None = None) -> dict:
    event = {"type": {"name": name}, "timeInSeconds": clock}
    if username:
        event["username"] = username
    return event


A, B = ["a1", "a2", "a3", "a4", "a5"], ["b1", "b2", "b3", "b4", "b5"]

# replays with the shapes real matches have: each is r6-dissect's output for a whole match
ODD_REPLAYS = {
    "one round": {"rounds": [raw_round(1, A, B, [kill(150, "a1", "b1")])]},
    "no kills at all (every round to time)": {"rounds": [raw_round(i, A, B, [], winner=i % 2) for i in range(1, 7)]},
    "a round nobody won": {"rounds": [raw_round(1, A, B, [kill(100, "b2", "a2")], winner=None),
                                      raw_round(2, A, B, [], winner=1)]},
    "a player who left after round 1": {"rounds": [raw_round(1, A, B, [kill(120, "a5", "b5")]),
                                                   *[raw_round(i, A[:4], B, [kill(90, "b1", "a1")], winner=1)
                                                     for i in range(2, 5)]]},
    "suicides, unknown killers and unknown victims": {"rounds": [raw_round(1, A, B, [
        other("Death", 170, "a3"), kill(160, "a1", "a1"), kill(150, None, "b4"), kill(140, "b1", None),
        kill(130, "ghost", "a2"), kill(120, "a4", "nobody")])]},
    "events the app doesn't use": {"rounds": [raw_round(1, A, B, [
        other("OperatorSwap", 179, "a1"), other("Battleye", 178), other("PlayerLeave", 177, "b5"),
        other("LocateObjective", 176, "a2"), other("SomethingNew", 175), other("Other", 174)])]},
    "a plant and a defuse resetting the clock": {"rounds": [raw_round(1, A, B, [
        kill(100, "a1", "b1"), other("DefuserPlantComplete", 40, "a2"), kill(30, "b2", "a2"),
        other("DefuserDisableComplete", 5, "b3")], winner=1)]},
    "a plant nobody is named for": {"rounds": [raw_round(1, A, B, [other("DefuserPlantComplete", 40)])]},
    "overtime, 8-7": {"rounds": [raw_round(i, A, B, [kill(100, "a1" if i % 2 else "b1", "b2" if i % 2 else "a2")],
                                           winner=0 if i % 2 else 1, attack=(i - 1) // 6 % 2) for i in range(1, 16)]},
    "a 1v1": {"rounds": [raw_round(i, ["solo"], ["duel"], [kill(60, "duel", "solo")], winner=1) for i in range(1, 4)]},
    "no players list (names only in the kill feed)": {"rounds": [dict(raw_round(1, [], [], [kill(90, "a1", "b1")]), players=[])]},
    "players without a team or operator": {"rounds": [dict(raw_round(1, A, B, [kill(90, "a1", "b1")]),
                                                           players=[{"username": u} for u in A + B])]},
    "missing teams, map, stats and feed": {"rounds": [{"roundNumber": 1, "players": [{"username": u, "teamIndex": 0} for u in A]},
                                                      {"roundNumber": 2, "teams": None, "map": None, "stats": None,
                                                       "matchFeedback": None, "players": None}]},
    "one round file, not a match folder": raw_round(1, A, B, [kill(90, "b1", "a1")], winner=1),
    # (Ubisoft names are unique ignoring case, so "Fabian" and "fabian" are never in one match)
    "awkward names": {"rounds": [raw_round(i, ODD_NAMES[:5], ODD_NAMES[5:],
                                           [kill(100, ODD_NAMES[i % 5], ODD_NAMES[5 + i % 4], headshot=True)])
                                 for i in range(1, 8)]},
    "a duplicated round number": {"rounds": [raw_round(1, A, B, [kill(90, "a1", "b1")]),
                                             raw_round(1, A, B, [kill(90, "b1", "a1")], winner=1)]},
    "no timestamp, match type or score": {"rounds": [{k: v for k, v in raw_round(1, A, B, []).items()
                                                      if k not in ("timestamp", "matchType")}]},
    "the whole team wiped by one player": {"rounds": [raw_round(1, A, B, [kill(170 - 5 * i, "b1", A[i], headshot=True)
                                                                         for i in range(5)], winner=1)]},
}


def random_replay(rng: random.Random) -> dict:
    """A match with random rounds, players coming and going, and random events among them."""
    team0 = rng.sample(A + ODD_NAMES[:3], rng.randint(1, 5))
    team1 = rng.sample(B + ODD_NAMES[3:6], rng.randint(1, 5))
    rounds = []
    for n in range(1, rng.randint(1, 15) + 1):
        here0 = [p for p in team0 if rng.random() > 0.1] or team0[:1]
        here1 = [p for p in team1 if rng.random() > 0.1] or team1[:1]
        everyone = here0 + here1 + ["stranger", None]
        feed, clock = [], 180.0
        for _ in range(rng.randint(0, 12)):
            clock = max(0.0, clock - rng.uniform(0, 30)) if rng.random() > 0.1 else rng.uniform(0, 45)  # a plant resets it
            pick = rng.random()
            if pick < 0.6:
                feed.append(kill(clock, rng.choice(everyone), rng.choice(everyone), rng.random() < 0.4))
            elif pick < 0.7:
                feed.append(other("Death", clock, rng.choice(here0 + here1)))
            elif pick < 0.8:
                feed.append(other("DefuserPlantComplete", clock, rng.choice(everyone)))
            elif pick < 0.9:
                feed.append(other("DefuserDisableComplete", clock, rng.choice(everyone)))
            else:
                feed.append(other(rng.choice(["OperatorSwap", "Battleye", "PlayerLeave", "Unknown"]), clock))
        rounds.append(raw_round(n, here0, here1, feed, winner=rng.choice([0, 1, 1, 0, None]), attack=rng.randint(0, 1)))
    return {"rounds": rounds}


class _Database(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.db = StatsDB(self.folder / "stats.db")
        self.addCleanup(self.db.close)


class TestOddReplays(_Database):
    """A replay that parses goes through everything the app does with a match, without a crash."""

    def pipeline(self, raw: dict, label: str) -> dict:
        match = normalize_from_r6_dissect(copy.deepcopy(raw))
        match["match_id"] = f"{label}-{len(self.db.match_list())}"
        stats = me.compute_match_metrics(match)
        rows = me.pro_league_rows(stats)
        me.scoreboard_text(match, rows)
        me.leaderboard_rows(stats)
        me.round_player_rows(me.round_rows(match, stats))
        me.rounds_text(match)
        me.running_scores(match)
        for player in stats.values():
            me.side_split(player.round_breakdown)
        me.rows_csv(rows)
        try:
            self.db.import_match(match["match_id"], match, files=len(match["rounds"]))
        except NotAdded:  # shown on the Dashboard as why it can't be added: a recording without players
            self.assertFalse(match["players"])
            return match
        with StatsManager(self.folder / "season.db", season="odd") as tracker:
            tracker.add_players([p["name"] for p in match["players"]][:3], team="Odd Squad")
            tracker.log_match(match, match_id=match["match_id"])
            tracker.get_team_stats("Odd Squad")
            tracker.export_json()
        latest = self.db.match_list(self.db.me(), limit=5)
        if latest:
            dock.latest_match_html(self.db, self.db.me(), latest[0])
            dock.form_html(latest)
        return match

    def test_every_odd_replay(self):
        for label, raw in ODD_REPLAYS.items():
            with self.subTest(replay=label):
                match = self.pipeline(raw, label)
                self.assertEqual(len(match["rounds"]), len(raw.get("rounds") or [raw]))

    def test_random_replays(self):
        rng = random.Random(1606)
        for i in range(150):
            raw = random_replay(rng)
            with self.subTest(case=i):
                self.pipeline(raw, f"random{i}")

    def test_ask_about_odd_replays(self):
        for label, raw in ODD_REPLAYS.items():
            self.pipeline(raw, label)
        vocab = Vocab.from_db(self.db)
        for question in QUESTIONS(vocab)[:150]:
            with self.subTest(question=question):
                self.assertTrue(ask(question, self.db, now=NOW, vocab=vocab).headline)

    def test_names_come_back_as_they_were(self):
        self.pipeline(ODD_REPLAYS["awkward names"], "names")
        stored = set(self.db.players().values())
        for name in ODD_NAMES[:8]:
            self.assertIn(name, stored)
        self.db.save_roster("Odd 'Names' 🔥", ODD_NAMES[:5])
        self.assertEqual(self.db.rosters()["Odd 'Names' 🔥"], ODD_NAMES[:5])
        self.assertTrue(self.db.team_matches(ODD_NAMES[:5], min_players=2))
        self.db.delete_roster("Odd 'Names' 🔥")
        self.assertNotIn("Odd 'Names' 🔥", self.db.rosters())


    def test_season_teams_from_before_the_stats_database(self):
        """A season team whose matches were saved to the tracker by an earlier version, before the
        stats database, still gets a row for each player, from the tracker's own totals."""
        from team_tables import season_player_table

        with StatsManager(self.folder / "season.db", season="old") as tracker:
            tracker.add_players(A[:3], team="Old Guard")
            for i in range(2):
                tracker.log_match(normalize_from_r6_dissect(ODD_REPLAYS["overtime, 8-7"]), match_id=f"old-{i}")
            table = season_player_table(self.db, tracker, "Old Guard")  # the stats database has none of them
            self.assertEqual(len(table), 3)
            self.assertTrue(all(isinstance(row["EPS"], str) for row in table))
            self.assertEqual(season_player_table(self.db, tracker, "No Such Team"), [])


class TestReadingReplays(unittest.TestCase):
    """Reading a match's rounds when some won't read, and finding Siege's replay folders."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)

    def rounds(self, count: int) -> list[str]:
        match = self.folder / "Match-2026-10-01_20-00-00-1"
        match.mkdir(exist_ok=True)
        paths = []
        for i in range(1, count + 1):
            (match / f"Match-2026-10-01_20-00-00-1-R{i:02d}.rec").write_bytes(b"round")
            paths.append(str(match / f"Match-2026-10-01_20-00-00-1-R{i:02d}.rec"))
        return paths

    def test_a_round_that_wont_read_is_skipped_not_the_match(self):
        def dissect(path, num_rounds=None):
            if num_rounds:  # the whole match folder at once
                raise ReplayParseError("unsupported round")
            if path.name.endswith("R02.rec"):
                raise ReplayParseError("corrupt")
            return raw_round(int(path.stem[-2:]), A, B, [kill(90, "a1", "b1")])
        with mock.patch.object(replay_parser, "_run_r6_dissect", side_effect=dissect):
            match, raw, warnings = replay_parser.parse_match(self.rounds(3))
        self.assertEqual(len(match["rounds"]), 2)
        self.assertIn("Whole-match parse failed", warnings[0])
        self.assertTrue(any("R02" in w for w in warnings))

    def test_a_match_with_no_round_that_reads(self):
        with mock.patch.object(replay_parser, "_run_r6_dissect", side_effect=ReplayParseError("corrupt")):
            with self.assertRaisesRegex(ReplayParseError, "Every round failed"):
                replay_parser.parse_match(self.rounds(2))
        with self.assertRaises(ReplayParseError):
            replay_parser.parse_match([])
        with self.assertRaises(ReplayParseError):
            replay_parser.parse_match([str(self.folder / "gone-R01.rec")])

    def test_one_round_file(self):
        with mock.patch.object(replay_parser, "_run_r6_dissect", return_value=raw_round(1, A, B, [])):
            match, _raw, warnings = replay_parser.parse_match(self.rounds(1))
        self.assertEqual((len(match["rounds"]), warnings), (1, []))

    def test_finding_siege_in_every_steam_library_and_ubisoft_connect(self):
        programs, library = self.folder / "Program Files (x86)", self.folder / "D" / "SteamLibrary"
        steam = programs / "Steam" / "steamapps"
        steam.mkdir(parents=True)
        (steam / "libraryfolders.vdf").write_text(
            '"libraryfolders" { "0" { "path" "%s" } "1" { "path" "%s" } }'
            % (str(programs / "Steam").replace("\\", "\\\\"), str(library).replace("\\", "\\\\")), encoding="utf-8")
        found = []
        for root, played in ((library / "steamapps" / "common", 100), (programs / "Ubisoft" / "Ubisoft Game Launcher" / "games", 200)):
            replays = root / replay_parser._SIEGE_REPLAYS
            (replays / "Match-1").mkdir(parents=True)
            os.utime(replays / "Match-1", (played, played))
            found.append(replays)
        env = {"ProgramFiles(x86)": str(programs), "ProgramFiles": str(programs)}  # the same folder twice
        with mock.patch.object(replay_parser.sys, "platform", "win32"), mock.patch.dict(os.environ, env):
            self.assertEqual(replay_parser.find_replay_folders(), found[::-1])  # most recently played first
        with mock.patch.object(replay_parser.sys, "platform", "linux"):
            self.assertEqual(replay_parser.find_replay_folders(), [])  # Siege's replays are a Windows thing
        with mock.patch.object(replay_parser.sys, "platform", "win32"),                 mock.patch.dict(os.environ, {"ProgramFiles(x86)": str(self.folder / "nothing"), "ProgramFiles": ""}):
            self.assertEqual(replay_parser.find_replay_folders(), [])


# ------------------------------------------------------------- Ask ----
def QUESTIONS(vocab: Vocab) -> list[str]:
    """Every metric asked the ways people ask, about the players, maps, operators and teams the
    database knows; then the questions nobody should ask but someone will."""
    player = next(iter(vocab.players.values()), "Fabian")
    team = next(iter(vocab.rosters), "Varsity")
    map_ = (vocab.maps or ["Bank"])[0]
    operator = (vocab.operators or ["Ash"])[0]
    templates = ["my {m}", "what's my {m}?", "{m} by map", "{m} by operator", "{m} by week", "{m} per match",
                 "top 5 players by {m}", "worst {m}", "{m} on " + map_, f"{player}'s {{m}}", "{m} as " + operator,
                 "compare me and " + player + " {m}", "{m} over my last 3 matches", "{m} since 2026-09-01",
                 "{m} in ranked", f"{team} {{m}}", "{m} attack vs defense", "{m} this month", "best {m} with " + player,
                 "{m} between september 1 and october 1"]
    words = [ws[0] for ws in METRIC_WORDS.values()]
    out = [t.format(m=w) for i, w in enumerate(words) for t in templates[i % 4::4]]  # each metric 5 ways
    out += [t.format(m=words[i % len(words)]) for i, t in enumerate(templates)]  # each template once more
    out += ["", "   ", "?", "!!!", "my", "and or not", "a" * 3000, "🔥🔥🔥", "Robert'); DROP TABLE matches;--",
            "' OR 1=1 --", "%", "_", "\\", "\x00", "kd " * 200, "top 0 players", "top 999999999999 players",
            "last -5 matches", "since 2099-01-01", "between 2026-13-45 and yesterday", "my kd on a map that doesn't exist",
            "compare me and me", "compare nobody and nothing", f"{player} vs {player}", "who is the best", "help",
            "SELECT * FROM matches", "my k/d/a/b/c", "show me everything", "ÜNÏCØDÉ'S K/D", "first blood?"]
    return out


class TestAsk(_Database):
    """Ask answers every question, from an empty database, the demo season and awkward names, with
    an answer or a "didn't understand", never a crash."""

    def check_all(self, label: str) -> None:
        vocab = Vocab.from_db(self.db)
        questions = QUESTIONS(vocab) + suggestions(vocab, self.db, count=20)
        for question in questions:
            with self.subTest(database=label, question=question[:60]):
                answer = ask(question, self.db, now=NOW, vocab=vocab)
                self.assertTrue(answer.headline)
                for row in answer.rows:
                    for column, kind in answer.formats.items():
                        fmt(row.get(column), kind)  # every value shows

    def test_an_empty_database(self):
        self.check_all("empty")

    def test_the_demo_season(self):
        import demo_season
        for folder, match in demo_season.season().items():
            self.db.import_match(folder, match, files=len(match["rounds"]))
        self.db.save_roster(demo_season.TEAM, demo_season.ROSTER)
        self.check_all("demo season")

    def test_values_of_every_kind_show(self):
        """What SQLite can hand back for a column: NULL (it has no NaN or infinity), ints and floats."""
        for kind in ("ratio", "pct", "count", "eps", "signed"):
            for value in (None, 0, -1, -0.5, 1.5, 0.004, 10 ** 12, -(10 ** 12)):
                with self.subTest(kind=kind, value=value):
                    self.assertIsInstance(fmt(value, kind), str)


# --------------------------------------------- settings, catalogs, backups ----
JUNK = [None, 0, -1, 1.5, True, "", "text", "#zzz", [], [1, 2], {}, {"name": None}, {"name": 5}, "x" * 100_000,
        {"primary": "javascript:alert(1)", "name": "<b>", "team": ["list"], "logo": 7},
        {"source": "liquipedia", "id": None, "name": ["no"], "tier": {}, "season": 12, "url": "javascript:x"}]


class TestDamagedSettingsAndFiles(unittest.TestCase):
    """Whatever's in a settings file, a downloaded catalog or a backup, the app either uses a clean
    version of it or says it can't, with the error it shows."""

    def test_theme_and_competition_are_always_cleaned(self):
        for junk in JUNK:
            with self.subTest(value=repr(junk)[:60]):
                theme, competition = branding.clean_theme(junk), branding.clean_competition(junk)
                self.assertEqual(set(theme), set(branding.DEFAULT_THEME))
                self.assertEqual(set(competition), set(branding.DEFAULT_COMPETITION))
                self.assertTrue(all(isinstance(v, str) for v in competition.values()))
                self.assertTrue(theme["primary"].startswith("#"))
                branding.competition_badge(competition)
                branding.readable_accent(theme["primary"])

    def test_settings_files_that_are_damaged(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        for content in (b"", b"{", b"\xff\xfe\x00", b"null", b"[]", b'"text"', json.dumps({"name": 5}).encode(), b"{}" * 50000):
            with self.subTest(content=content[:20]):
                (folder / "theme.json").write_bytes(content)
                (folder / "competition.json").write_bytes(content)
                self.assertEqual(set(branding.load_theme(folder / "theme.json")), set(branding.DEFAULT_THEME))
                self.assertEqual(set(branding.load_competition(folder / "competition.json")), set(branding.DEFAULT_COMPETITION))
        self.assertEqual(set(branding.load_theme(folder / "missing.json")), set(branding.DEFAULT_THEME))

    def test_a_logo_that_isnt_an_image(self):
        """Team Theme shows a ValueError or OSError as "Could not save theme: ..."; nothing else."""
        def png(width: int, height: int) -> bytes:  # a PNG's header, saying how big it is, and no picture
            def chunk(kind: bytes, data: bytes) -> bytes:
                return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
            return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                    + chunk(b"IDAT", zlib.compress(b"\x00" * 10)) + chunk(b"IEND", b""))
        for raw in (b"", b"not an image", b"\x89PNG\r\n\x1a\n" + b"\x00" * 10, b"GIF89a", b"<svg onload=alert(1)>",
                    b"\xff" * 3_000_000, png(64, 64), png(5000, 10), png(30_000, 30_000)):
            with self.subTest(raw=raw[:12], size=len(raw)):
                with self.assertRaises((ValueError, OSError)):
                    branding.uploaded_logo(raw)

    def test_school_catalogs(self):
        for junk in [*JUNK, {"schools": []}, {"schools": [None]}, {"schools": [{"name": ""}]},
                     {"schools": [{"name": "A", "teams": "no"}]}, {"schools": [{"name": "A", "teams": [{"name": None}]}]}]:
            with self.subTest(value=repr(junk)[:60]):
                try:
                    schools = necc_data.normalize_school_catalog(junk)
                except ValueError:
                    continue
                self.assertTrue(all(s["name"] for s in schools))

    def test_roster_snapshots(self):
        for junk in [*JUNK, {"teams": None}, {"teams": [None]}, {"teams": [{"name": "A", "players": "x"}]},
                     {"captured_at": 5, "teams": []}]:
            with self.subTest(value=repr(junk)[:60]):
                cleaned = rosters.clean_snapshot(junk)
                self.assertTrue(cleaned is None or isinstance(cleaned.get("teams"), list))

    def test_liquipedia_pages(self):
        rng = random.Random(7)
        texts = ["", "{{", "}}", "{{Infobox league", "{{Infobox league|name=|liquipediatier=}}",
                 "{{Infobox league|name=X|liquipediatier=99|sdate=2026-02-31|edate=nope}}",
                 "{{Infobox league|name={{Team|{{Team|nested}}}}|liquipediatier=1|sdate=2026-10-01}}",
                 "{{TeamParticipants|{{Opponent|}}|{{Opponent}}}}", "[[" * 500, "{{" * 2000 + "}}" * 1999]
        alphabet = "{}|=[]<>!\n abcXYZ0123-"
        texts += ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 400))) for _ in range(100)]
        for i, text in enumerate(texts):
            with self.subTest(case=i):
                event = events.event_from_page(f"Page {i}", text)
                if event:
                    events.status(event, NOW.date())
                    events.in_window(event, NOW.date())
                events.participants(text)
                events.template_params(text, "Infobox league")

    def test_liquipedia_catalogs_on_disk(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        for content in (b"", b"{", b"null", b"[]", b'{"events": "x"}', b'{"events": [null, 5, {}]}', b"\xff"):
            with self.subTest(content=content):
                (folder / "events.json").write_bytes(content)
                catalog = events.load_catalog(folder / "events.json")
                if catalog is not None:
                    events.current_events(catalog, NOW.date())

    def test_match_day_backups(self):
        valid = {"fixtures": [{"id": "1", "opponent": "Metro", "when": "2026-10-20T19:00:00", "timezone": "CT",
                               "kind": "League", "team": "Us"}], "availability": {"Fabian": "Confirmed"}, "notes": "n"}
        damaged = [*JUNK, {"fixtures": [None]}, {"fixtures": [{"id": "", "opponent": "x", "when": "2026-01-01"}]},
                   {"fixtures": [{"id": "1", "opponent": "x", "when": "not a date"}]},
                   {"fixtures": [dict(valid["fixtures"][0])] * 2},  # the same fixture twice
                   {"fixtures": [dict(valid["fixtures"][0], id=str(i)) for i in range(201)]},
                   {"availability": {"Fabian": "Maybe"}}, {"availability": {"x" * 181: "Confirmed"}},
                   {"notes": "x" * 10_001}, {"notes": 5}, dict(valid, fixtures=[dict(valid["fixtures"][0], opponent="x" * 181)])]
        for junk in damaged:
            with self.subTest(value=repr(junk)[:60]):
                try:
                    MatchdayWorkspace.from_dict(junk)
                except ValueError:
                    continue
        workspace = MatchdayWorkspace.from_dict(valid)
        self.assertEqual(MatchdayWorkspace.from_dict(workspace.to_dict()), workspace)  # a backup restores itself
        # with a time zone, names that need escaping, and results the database may not have
        workspace.fixtures.append(dict(valid["fixtures"][0], id="2", opponent="<b>x</b>", when="2099-01-01T19:00:00+02:00"))
        html = panel_html(workspace, [{"won": None, "map": None, "played_at": None}, {"won": 1}], ["<i>", *ODD_NAMES])
        self.assertNotIn("<b>x</b>", html)
        self.assertNotIn("<span><i></span>", html)
        self.assertIn("&lt;i&gt;", html)


if __name__ == "__main__":
    unittest.main()
