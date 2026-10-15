"""Tests for api.py, the REST API of the web and Unity clients (needs requirements-api.txt).
Run from the repo root:  python -m unittest discover -s scripts"""

from __future__ import annotations

import copy
import functools
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from fastapi.testclient import TestClient

    import api
except ImportError:  # the API's packages aren't installed (they're only in requirements-api.txt)
    api = None

import season_stats
from sample_data import SAMPLE_MATCH, TEAM0


@unittest.skipIf(api is None, "FastAPI isn't installed (pip install -r scripts/requirements-api.txt)")
class TestApi(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp())
        db = folder / "season_stats.db"
        self.addCleanup(lambda: [p.unlink() for p in folder.iterdir()] and folder.rmdir())
        for patch in (mock.patch.object(api, "StatsManager", functools.partial(season_stats.StatsManager, db)),
                      mock.patch.object(api, "DEFAULT_DB_PATH", db)):
            patch.start()
            self.addCleanup(patch.stop)
        self.client = TestClient(api.app)

    def test_bundled_necc_directory_without_configuration(self):
        with mock.patch.dict("os.environ", {"NECC_R6_DATA_URL": ""}):
            response = self.client.get("/api/v1/schools")
        self.assertEqual(response.status_code, 200)
        schools = response.json()["schools"]
        self.assertEqual(sum(len(school["teams"]) for school in schools), 123)

    def test_health_and_bad_season(self):
        self.assertEqual(self.client.get("/api/v1/health").json()["status"], "ok")
        self.assertEqual(self.client.get("/api/v1/seasons/bad!season/summary").status_code, 400)

    def test_logging_never_pins_a_replays_generic_team_label(self):
        body = {"match": SAMPLE_MATCH, "rosters": {"YOUR TEAM": TEAM0[:2], "Team Liquid": TEAM0[2:3]}}
        response = self.client.post("/api/v1/seasons/S1/matches/log", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        tracked = self.client.get("/api/v1/seasons/S1/summary").json()["tracked_players"]
        self.assertEqual(tracked, {TEAM0[0]: None, TEAM0[1]: None, TEAM0[2]: "Team Liquid"})
        players = self.client.get("/api/v1/seasons/S1/summary").json()["players"]
        self.assertTrue(all(p["eps"] is not None for p in players))

    def test_only_the_players_picked_for_the_match_are_logged(self):
        # tracked earlier, but not picked for this match (a stranger's teammate in Ranked, say)
        self.client.post("/api/v1/seasons/S1/rosters", json={"team": "Varsity", "players": TEAM0})
        body = {"match": SAMPLE_MATCH, "rosters": {"Varsity": TEAM0[:2]}}
        response = self.client.post("/api/v1/seasons/S1/matches/log", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["players_logged"], sorted(TEAM0[:2]))
        players = self.client.get("/api/v1/seasons/S1/summary").json()["players"]
        self.assertEqual(sorted(p["username"] for p in players), sorted(TEAM0[:2]))

    def test_a_match_saved_without_picking_players_is_a_400(self):
        self.client.post("/api/v1/seasons/S1/rosters", json={"team": "Varsity", "players": TEAM0})
        for rosters in ({}, {"Varsity": [" "]}):
            response = self.client.post("/api/v1/seasons/S1/matches/log", json={"match": SAMPLE_MATCH, "rosters": rosters})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/v1/seasons/S1/summary").json()["players"], [])

    def test_a_malformed_match_is_a_400(self):
        broken = copy.deepcopy(SAMPLE_MATCH)
        del broken["rounds"]
        response = self.client.post("/api/v1/seasons/S1/matches/log", json={"match": broken, "rosters": {"T": TEAM0}})
        self.assertEqual(response.status_code, 400)

    def test_upload_that_is_not_a_replay(self):
        response = self.client.post("/api/v1/replays/parse", files=[("files", ("notes.txt", b"hello", "text/plain"))])
        self.assertEqual(response.status_code, 503 if not api.r6_dissect_available() else 400)

    def test_uploads_are_parsed_off_the_event_loop_and_in_order(self):
        rec = b"dissect\x00" + bytes(2000)
        parsed = (SAMPLE_MATCH, {}, [])
        with mock.patch.object(api, "r6_dissect_available", return_value=True), \
                mock.patch.object(api, "parse_match", return_value=parsed) as parse:
            response = self.client.post("/api/v1/replays/parse", files=[
                ("files", ("Match-B-R01.rec", rec, "application/octet-stream")),
                ("files", ("Match-A-R01.rec", rec, "application/octet-stream")),
            ])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([m["name"] for m in response.json()["matches"]], ["Match-A", "Match-B"])
        self.assertEqual(parse.call_count, 2)

    def test_school_catalog_keeps_non_ascii_names(self):
        catalog = {"schools": [{"name": "Université Laval", "teams": [{"name": "R6", "roster": ["Émile"]}]}]}
        self.assertEqual(self.client.post("/api/v1/schools/catalog", content=json.dumps(catalog)).status_code, 200)
        schools = self.client.get("/api/v1/schools").json()["schools"]
        self.assertEqual(schools[0]["name"], "Université Laval")
        self.assertEqual(schools[0]["teams"][0]["roster"], ["Émile"])


if __name__ == "__main__":
    unittest.main()
