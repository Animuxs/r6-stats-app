"""Leagues and tournaments: Liquipedia's events (parsing, the polite API client, the catalog and
its daily refresh), NECC's divisions, the saved league, and the League & Team page."""

import gzip
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from streamlit.testing.v1 import AppTest

import branding
import events
import season_stats

TODAY = date(2026, 10, 16)
PAGE = """{{Infobox league
|liquipediatier=4|liquipediatiertype=
|name=Packrunners League - Season 2
|organizer={{Team|Packrunners}}
|organizer2=[[Slim]]
|type=Online
|country=us
|sdate=2026-10-01
|edate=2026-11-??
|web=https://example.org/league
|prizepoolusd=1,500
|team_number=8
}}
==Awards==
{{AwardPrizePool|{{Slot|award=MVP|{{Opponent|njr|flag=us|team=DarkZero}}}}}}
==Participants==
{{TeamParticipants
|{{Opponent|fisher college|players={{Persons|{{Person|One}}}}}}
|{{Opponent|Team Liquid}}
|{{Opponent|TBD}}
|{{Opponent|team liquid}}
}}
"""


def page(name="Some Cup", tier=3, kind="", start="2026-10-01", end="2026-10-20", extra=""):
    return (f"{{{{Infobox league\n|liquipediatier={tier}\n|liquipediatiertype={kind}\n|name={name}\n"
            f"|sdate={start}\n|edate={end}\n}}}}\n{extra}")


class TestWikitext(unittest.TestCase):
    def test_an_event_from_its_infobox(self):
        event = events.event_from_page("Packrunners League/Season 2", PAGE, "D")
        self.assertEqual(event["name"], "Packrunners League - Season 2")
        self.assertEqual(event["tier"], "C")  # the infobox's own tier beats the category's
        self.assertEqual(event["organizer"], "Packrunners, Slim")
        self.assertEqual((event["location"], event["region"], event["mode"]), ("United States", "North America", "Online"))
        self.assertEqual((event["start"], event["end"]), ("2026-10-01", "2026-11-??"))
        self.assertEqual((event["prize_usd"], event["team_count"]), (1500, 8))
        # participant lists only: the MVP award's player isn't a team, TBD isn't either, and a
        # team template's lowercase key is shown capitalized, once
        self.assertEqual(event["teams"], ["Fisher College", "Team Liquid"])
        self.assertEqual(event["url"], "https://liquipedia.net/rainbowsix/Packrunners_League/Season_2")
        self.assertEqual(event["website"], "https://example.org/league")

    def test_pages_that_arent_events_a_team_plays_in(self):
        self.assertIsNone(events.event_from_page("X", "no infobox here"))
        self.assertIsNone(events.event_from_page("X", page(kind="Points")))
        self.assertIsNone(events.event_from_page("X", page(kind="Show Match")))
        self.assertIsNone(events.event_from_page("X", page(start="", end="")))

    def test_plain_text_from_markup(self):
        self.assertEqual(events.plain("{{TeamIcon|Elevate}} [[Elevate|Elevate Esports]]"), "Elevate Esports")
        self.assertEqual(events.plain("[https://atchall.com/ ATC Hall]"), "ATC Hall")
        self.assertEqual(events.plain("Rei dos Mapas 2026 - {{SUBPAGENAME}}", "Rei dos Mapas/2026/Final"),
                         "Rei dos Mapas 2026 - Final")
        self.assertEqual(events.plain("'''Bold'''<br/>text<!-- note -->"), "Boldtext")

    def test_dates_with_unknown_days(self):
        self.assertEqual(events.parse_day("2026-10-??"), date(2026, 10, 1))
        self.assertEqual(events.parse_day("2026-11-??", end=True), date(2026, 11, 30))
        self.assertEqual(events.parse_day("2027-??-??", end=True), date(2027, 12, 31))
        self.assertIsNone(events.parse_day("TBA"))

    def test_regions(self):
        self.assertEqual(events.region_of("Brazil"), "Latin America")
        self.assertEqual(events.region_of("EMEA"), "Europe & MENA")
        self.assertEqual(events.region_of("Japan"), "Asia-Pacific")
        self.assertEqual(events.region_of("United States, Canada"), "North America")
        self.assertEqual(events.region_of("Atlantis"), "Other")

    def test_one_day_events_and_the_other_infobox_spelling(self):
        event = events.event_from_page("Weekly/12", "{{Infobox_league\n|liquipediatier=5\n|name=Weekly #12\n|date=2026-10-20\n}}")
        self.assertEqual((event["name"], event["start"], event["end"], event["tier"]), ("Weekly #12", "2026-10-20", "2026-10-20", "D"))

    def test_teams_from_brackets_when_the_participant_list_is_only_placeholders(self):
        text = page(extra="{{TeamParticipants|{{Opponent|TBD}}}}\n{{Match|opponent1={{TeamOpponent|Rusted and Busted}}"
                          "|opponent2={{TeamOpponent|RNDM|score=2}}}}")
        self.assertEqual(events.event_from_page("X", text)["teams"], ["Rusted and Busted", "RNDM"])

    def test_wikitext_that_isnt_well_formed_doesnt_break_parsing(self):
        for text in ("{{Infobox league|name=Open", "}}}}{{{{|||", "{{Infobox league|sdate=2026-13-45|edate=x}}",
                     "{{Infobox league|name=[[Broken|sdate=2026-10-01", "{{TeamParticipants|{{Opponent|", "x" * 200_000):
            events.event_from_page("X", text)  # no exception, whatever it returns
            events.participants(text)
        self.assertIsNone(events.parse_day("2026-13-01"))

    def test_window_and_status(self):
        live = {"start": "2026-10-01", "end": "2026-11-??"}
        self.assertEqual(events.status(live, TODAY), "Live")
        self.assertEqual(events.status({"start": "2026-12-01", "end": "2026-12-05"}, TODAY), "Upcoming")
        ended = {"start": "2026-03-01", "end": "2026-05-01"}
        self.assertEqual(events.status(ended, TODAY), "Ended")
        self.assertTrue(events.in_window(ended, TODAY))  # ended less than six months ago
        self.assertFalse(events.in_window({"start": "2025-12-01", "end": "2026-01-10"}, TODAY))


class FakeConnection:
    """Answers the API's requests from `pages` and `members`, gzipped like Liquipedia."""

    def __init__(self, members, pages, log):
        self.members, self.pages, self.log = members, pages, log

    def request(self, method, url, headers):
        from urllib.parse import parse_qs, urlparse
        self.headers = headers
        params = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        self.log.append(params)
        if params.get("list") == "categorymembers":
            tier = params["cmtitle"].split(":")[1][0]
            body = {"query": {"categorymembers": [{"title": t, "timestamp": "2026-09-01T00:00:00Z"}
                                                  for t, tr in self.members.items() if tr == tier]}}
        else:
            body = {"query": {"pages": [{"title": t, "revisions": [{"slots": {"main": {"content": self.pages[t]}}}]}
                                        for t in params.get("titles", "").split("|") if t in self.pages]}}
        self.body = gzip.compress(json.dumps(body).encode())

    def getresponse(self):
        response = mock.Mock(status=200, reason="OK")
        response.read.return_value = self.body
        response.getheader.side_effect = lambda name: "gzip" if name == "Content-Encoding" else None
        return response

    def close(self):
        pass


class TestLiquipediaApi(unittest.TestCase):
    def setUp(self):
        self.log, self.slept, self.now = [], [], [0.0]
        self.members = {"Big Cup/2026": "S", "Old Cup/2025": "B", "Amateur League/S3": "D", "Points/2026": "C"}
        self.pages = {
            "Big Cup/2026": page("Big Cup 2026", tier=1, start="2026-11-01", end="2026-11-10",
                                 extra="{{TeamParticipants|{{Opponent|G2 Esports}}}}"),
            "Old Cup/2025": page("Old Cup 2025", tier=3, start="2025-05-01", end="2025-05-03"),
            "Amateur League/S3": page("Amateur League S3", tier=5, start="2026-03-01", end="2026-06-01"),
            "Points/2026": page("Points", tier=4, kind="Points"),
        }
        self.client = self.make_client()

    def make_client(self):
        def sleep(seconds):
            self.slept.append(seconds)
            self.now[0] += seconds
        return events.LiquipediaClient(connect=lambda: FakeConnection(self.members, self.pages, self.log),
                                       sleep=sleep, clock=lambda: self.now[0])

    def test_requests_are_polite(self):
        self.client.get(action="query")
        self.client.get(action="query")
        self.assertEqual(self.slept, [events.REQUEST_GAP])  # never two requests within 2 seconds
        connection = self.client._conn
        self.assertIn("github.com/", connection.headers["User-Agent"])  # names the project and where to reach it
        self.assertEqual(connection.headers["Accept-Encoding"], "gzip")

    def test_a_full_crawl(self):
        catalog = events.crawl(self.client, TODAY)
        self.assertEqual([e["name"] for e in catalog["events"]], ["Big Cup 2026", "Amateur League S3"])  # by tier
        self.assertEqual(catalog["events"][0]["teams"], ["G2 Esports"])
        self.assertEqual(catalog["past_pages"], ["Old Cup/2025", "Points/2026"])
        self.assertEqual(catalog["captured_at"], TODAY.isoformat())

    def test_a_refresh_reads_only_new_and_running_events(self):
        first = events.crawl(self.client, TODAY)
        self.members["New Open/2026"] = "C"
        self.pages["New Open/2026"] = page("New Open", tier=4, start="2026-10-20", end="2026-10-21")
        self.log.clear()
        second = events.crawl(self.make_client(), TODAY, first)
        read = [p["titles"].split("|") for p in self.log if "titles" in p]
        # the ended league, the old cup and the points table aren't read again
        self.assertEqual(sorted(t for batch in read for t in batch), ["Big Cup/2026", "New Open/2026"])
        self.assertEqual([e["name"] for e in second["events"]], ["Big Cup 2026", "New Open", "Amateur League S3"])

    def test_events_dropped_from_liquipedia_go(self):
        first = events.crawl(self.client, TODAY)
        del self.members["Big Cup/2026"]
        second = events.crawl(self.make_client(), TODAY, first)
        self.assertEqual([e["name"] for e in second["events"]], ["Amateur League S3"])

    def test_the_same_name_on_two_pages(self):
        self.pages["Amateur League/S3"] = page("Big Cup 2026", tier=5, start="2026-03-01", end="2026-06-01")
        first = events.crawl(self.client, TODAY)
        names = [e["name"] for e in first["events"]]
        self.assertEqual(names, ["Big Cup 2026 (Big Cup / 2026)", "Big Cup 2026 (Amateur League / S3)"])
        # once one is renamed on Liquipedia, the other goes back to its own name
        self.pages["Big Cup/2026"] = page("Big Cup 2026 Finals", tier=1, start="2026-11-01", end="2026-11-10")
        second = events.crawl(self.make_client(), TODAY, first)
        self.assertEqual([e["name"] for e in second["events"]], ["Big Cup 2026 Finals", "Big Cup 2026"])

    def test_a_name_that_looks_told_apart_is_kept(self):
        self.pages["Big Cup/2026"] = page("Six Cup (EU/NA)", tier=1, start="2026-11-01", end="2026-11-10")
        first = events.crawl(self.client, TODAY)
        second = events.crawl(self.make_client(), TODAY, first)
        self.assertEqual(second["events"][0]["name"], "Six Cup (EU/NA)")

    def test_a_page_without_dates_yet_is_read_again(self):
        self.members["Future Cup/2027"] = "B"
        self.pages["Future Cup/2027"] = "{{Infobox league\n|liquipediatier=3\n|name=Future Cup 2027\n|sdate=TBA\n}}"
        first = events.crawl(self.client, TODAY)
        self.assertNotIn("Future Cup/2027", first["past_pages"])
        self.pages["Future Cup/2027"] = page("Future Cup 2027", tier=3, start="2027-02-01", end="2027-02-03")
        second = events.crawl(self.make_client(), TODAY, first)
        self.assertIn("Future Cup 2027", [e["name"] for e in second["events"]])

    def test_a_refresh_leaves_the_previous_catalog_as_it_was(self):
        self.pages["Amateur League/S3"] = page("Big Cup 2026", tier=5, start="2026-03-01", end="2026-06-01")
        first = events.crawl(self.client, TODAY)
        before = json.dumps(first, sort_keys=True)
        events.crawl(self.make_client(), TODAY, first)
        self.assertEqual(json.dumps(first, sort_keys=True), before)  # it may be the cached copy pages are reading

    def test_failures_come_back_as_os_errors(self):
        class Refusing(FakeConnection):
            def request(self, *args, **kwargs):
                raise ConnectionResetError("reset")
        client = events.LiquipediaClient(connect=lambda: Refusing({}, {}, []), sleep=lambda s: None)
        with self.assertRaises(OSError):
            client.get(action="query")

        class Limited(FakeConnection):
            def getresponse(self):
                response = super().getresponse()
                response.status, response.reason = 429, "Too Many Requests"
                return response
        client = events.LiquipediaClient(connect=lambda: Limited({}, {}, []), sleep=lambda s: None)
        with self.assertRaisesRegex(OSError, "429"):
            client.get(action="query", list="categorymembers", cmtitle="Category:S-Tier Tournaments")


class TestStoredCatalog(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp())
        patch = mock.patch.object(season_stats, "DEFAULT_DB_PATH", folder / "season_stats.db")
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_release_snapshot(self):
        catalog = events.load_catalog(events.BUNDLED)
        self.assertGreater(len(catalog["events"]), 50)
        self.assertEqual({e["tier"] for e in catalog["events"]}, set(events.TIERS))
        self.assertEqual(catalog["attribution"], events.ATTRIBUTION)
        for event in catalog["events"]:
            self.assertTrue(event["url"].startswith(events.WIKI + "/"), event["id"])
            self.assertTrue(events.parse_day(event["start"]) or events.parse_day(event["end"]), event["id"])

    def test_a_newer_fetch_wins_and_refresh_waits_a_day(self):
        self.assertEqual(events.current_catalog()["captured_at"], events.load_catalog(events.BUNDLED)["captured_at"])
        fresh = {"events": [], "past_pages": [], "captured_at": "2099-01-01",
                 "checked_at": datetime.now().isoformat(timespec="seconds")}
        events.save_catalog(fresh, events.cache_path())
        self.assertEqual(events.current_catalog()["captured_at"], "2099-01-01")
        self.assertIsNotNone(events.next_refresh(events.current_catalog()))
        with self.assertRaises(OSError):  # checked less than a day ago: Liquipedia isn't asked again
            events.refresh(client=mock.Mock(side_effect=AssertionError("no request")))
        fresh["checked_at"] = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
        events.save_catalog(fresh, events.cache_path())
        self.assertIsNone(events.next_refresh(events.current_catalog()))

    def test_a_catalog_is_read_again_once_it_changes(self):
        path = events.cache_path()
        events.save_catalog({"events": [], "captured_at": "2099-01-01"}, path)
        self.assertEqual(events.load_catalog(path)["captured_at"], "2099-01-01")
        events.save_catalog({"events": [], "captured_at": "2099-01-02", "note": "a longer file"}, path)
        self.assertEqual(events.load_catalog(path)["captured_at"], "2099-01-02")
        path.unlink()
        self.assertIsNone(events.load_catalog(path))

    def test_a_broken_cache_falls_back_to_the_snapshot(self):
        events.cache_path().parent.mkdir(parents=True, exist_ok=True)
        events.cache_path().write_text("{broken", encoding="utf-8")
        self.assertGreater(len(events.current_catalog()["events"]), 50)

    def test_necc_divisions(self):
        necc = events.necc_events()
        divisions = [e for e in necc if e["division"]]
        self.assertEqual([e["division"] for e in divisions], ["I", "II", "III", "IV", "V", "VI"])
        self.assertEqual(sum(len(e["teams"]) for e in divisions), 128)
        first = divisions[0]
        self.assertEqual(first["schools"]["UdeS Esports"], "Université de Sherbrooke")
        self.assertEqual(first["groups"]["Akron White"], "Group A")
        self.assertTrue(all(e["url"].startswith("https://necc.v1.leagueos.gg/league/seasons/") for e in necc))


class TestSavedLeague(unittest.TestCase):
    def test_roundtrip_and_cleaning(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "competition.json"
            value = dict(branding.DEFAULT_COMPETITION, source="liquipedia", id="Big Cup/2026", name="Big Cup",
                         tier="S", team="G2 Esports", url="https://liquipedia.net/rainbowsix/Big_Cup")
            branding.save_competition(value, path)
            self.assertEqual(branding.load_competition(path), value)
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(branding.load_competition(path), branding.DEFAULT_COMPETITION)
        cleaned = branding.clean_competition(dict(name="X", source="evil", tier="Z", url="javascript:alert(1)"))
        self.assertEqual((cleaned["source"], cleaned["tier"], cleaned["url"]), ("", "", ""))
        self.assertEqual(branding.clean_competition(dict(name="", team="Nobody")), branding.DEFAULT_COMPETITION)

    def test_old_default_theme_name(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "appearance.json"
            path.write_text(json.dumps(dict(branding.DEFAULT_THEME, name="Siege / NECC")), encoding="utf-8")
            self.assertEqual(branding.load_theme(path)["name"], branding.DEFAULT_THEME["name"])


class TestLeaguePage(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp())
        for patch in (mock.patch.object(season_stats, "DEFAULT_DB_PATH", folder / "season_stats.db"),
                      mock.patch.object(branding, "DEFAULT_DB_PATH", folder / "season_stats.db"),
                      mock.patch.object(branding, "local_preferences", return_value=True)):
            patch.start()
            self.addCleanup(patch.stop)
        self.folder = folder
        with mock.patch.dict(os.environ, {"R6_DESKTOP": "1"}):
            self.at = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=60).run()
        self.at.switch_page("league.py").run()
        self.assertFalse(self.at.exception)

    def widget(self, kind, label):
        return next(w for w in getattr(self.at, kind) if w.label == label)

    def test_pick_a_liquipedia_event_and_team(self):
        at = self.at
        self.widget("text_input", "Search events, organizers or teams").set_value("CollegiateR6 Season 12").run()
        event = self.widget("selectbox", "Event")
        self.assertTrue(all("CollegiateR6" in option for option in event.options), event.options)
        team = next(s for s in at.selectbox if s.label.startswith("Your team"))
        team.set_value("Fisher College").run()
        self.widget("checkbox", "Name my season after it").check().run()
        at = self.widget("button", "Save as my league").click().run()
        self.assertFalse(at.exception)
        saved = branding.load_competition(self.folder / "competition.json")
        self.assertEqual((saved["source"], saved["tier"], saved["team"]), ("liquipedia", "C", "Fisher College"))
        self.assertTrue(saved["name"].startswith("CollegiateR6 Season 12"))
        self.assertEqual(at.session_state["r6_season"], saved["name"])
        banner = "".join(m.value for m in at.markdown if "identity-banner" in m.value)
        self.assertIn("C-TIER EVENT", banner)
        self.assertIn(saved["name"].upper(), banner.upper())

    def test_filters_narrow_the_list(self):
        self.widget("multiselect", "Tiers").set_value(["S"]).run()
        self.assertTrue(all("S-Tier" in option for option in self.widget("selectbox", "Event").options))

    def test_name_a_league_that_isnt_listed(self):
        at = self.widget("radio", "Where do you compete?").set_value("My league isn't listed").run()
        self.widget("text_input", "League or tournament").set_value("Midwest HS League").run()
        self.widget("text_input", "Your team's name").set_value("Lakeside Varsity").run()
        at = self.widget("button", "Save as my league").click().run()
        saved = branding.load_competition(self.folder / "competition.json")
        self.assertEqual((saved["source"], saved["name"], saved["team"]), ("custom", "Midwest HS League", "Lakeside Varsity"))
        self.assertTrue(any("Saved: Lakeside Varsity in Midwest HS League" in s.value for s in at.success))

    def test_pick_a_necc_division_team(self):
        at = self.widget("radio", "Where do you compete?").set_value("NECC").run()
        division = self.widget("selectbox", "Division")
        self.assertEqual(division.value, "necc:aa437rpne256ozzb6f5lv012y")  # Division I to start with
        self.assertTrue(division.options[1].startswith("NECC R6 | Division I ·"))
        next(s for s in at.selectbox if s.label.startswith("Your team")).set_value("UdeS Esports").run()
        self.widget("button", "Save as my league").click().run()
        saved = branding.load_competition(self.folder / "competition.json")
        self.assertEqual((saved["source"], saved["team"]), ("necc", "UdeS Esports"))
        self.assertIn("School", [s.label for s in self.at.selectbox])  # the NECC school directory is there too


if __name__ == "__main__":
    unittest.main()
