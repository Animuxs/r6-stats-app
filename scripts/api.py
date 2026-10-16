"""Local REST API shared by the React web client and Unity desktop client."""

from __future__ import annotations

import json
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import events
from necc_data import MAX_CATALOG_BYTES, bundled_school_catalog, fetch_school_catalog, normalize_school_catalog
from file_guard import ReplayScanner
from parser import ReplayParseError, collect_rec_files, group_by_match, parse_match, r6_dissect_available, save_uploads
from season_stats import DEFAULT_DB_PATH, GENERIC_TEAM_NAMES, StatsError, StatsManager

PARALLEL_MATCHES = 4  # r6-dissect runs as its own process; each can use a few hundred MB


app = FastAPI(title="R6 Match Intelligence API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


class MatchLogRequest(BaseModel):
    match: dict[str, Any]
    # team name -> the players confirmed for this match: only they are logged
    rosters: dict[str, list[str]] = Field(default_factory=dict)


class RosterRequest(BaseModel):
    team: str
    players: list[str]


class ReplayPathRequest(BaseModel):
    path: str


class _MemoryUpload:
    def __init__(self, name: str, data: bytes):
        self.name = name
        self._data = data

    def getbuffer(self) -> memoryview:
        return memoryview(self._data)


def _season_or_400(season: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9 _.-]{1,40}", season):
        raise HTTPException(status_code=400, detail="Invalid season name.")
    return season.strip()


def _parse_source(collect) -> dict[str, Any]:
    """Parse every match that collect(workdir, scanner) finds (a few at once, in order).
    Blocking: call it from a sync route or through run_in_threadpool."""
    scanner = ReplayScanner()
    try:
        with tempfile.TemporaryDirectory(prefix="r6-api-") as workdir:
            groups = group_by_match(collect(Path(workdir), scanner))
            if not groups:
                raise HTTPException(status_code=400, detail=scanner.summary() or "No Siege replay files found.")
            with ThreadPoolExecutor(max_workers=PARALLEL_MATCHES) as pool:
                parsed = list(pool.map(parse_match, groups.values()))
    except ReplayParseError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=400, detail=f"Cannot read the replays: {error}") from error
    matches = [{"name": name, "match": match, "match_json": json.dumps(match, ensure_ascii=False), "warnings": warnings}
               for name, (match, _raw, warnings) in zip(groups, parsed)]
    return {"matches": matches, "skipped": scanner.summary()}


def _catalog_path() -> Path:
    return Path(DEFAULT_DB_PATH).parent / "necc_r6_catalog.json"


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "r6-match-intelligence"}


@app.get("/api/v1/seasons")
def seasons() -> dict[str, list[str]]:
    with StatsManager() as manager:
        return {"seasons": manager.seasons()}


@app.get("/api/v1/seasons/{season}/summary")
def season_summary(season: str) -> dict[str, Any]:
    with StatsManager(season=_season_or_400(season)) as manager:
        return manager.export_json()


@app.get("/api/v1/seasons/{season}/matches")
def season_matches(season: str) -> dict[str, Any]:
    season = _season_or_400(season)
    with StatsManager(season=season) as manager:
        return {"season": season, "matches": manager.match_history()}


@app.post("/api/v1/seasons/{season}/rosters")
def track_roster(season: str, payload: RosterRequest) -> dict[str, Any]:
    team = payload.team.strip()
    players = list(dict.fromkeys(player.strip() for player in payload.players if player.strip()))
    if not team or not players or len(players) > 100:
        raise HTTPException(status_code=400, detail="Provide a team name and between 1 and 100 player names.")
    with StatsManager(season=_season_or_400(season)) as manager:
        manager.add_players(players, team=team)
        return {"team": team, "tracked_players": manager.tracked_players()}


@app.post("/api/v1/replays/parse")
async def parse_replays(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    if not r6_dissect_available():
        raise HTTPException(status_code=503, detail="The r6-dissect replay parser is not available.")
    uploads = []
    total_bytes = 0
    max_bytes = 512 * 1024 * 1024
    for upload in files:
        data = await upload.read(max_bytes - total_bytes + 1)
        total_bytes += len(data)
        if total_bytes > max_bytes:
            raise HTTPException(status_code=413, detail="Replay uploads are limited to 512 MB per request.")
        uploads.append(_MemoryUpload(upload.filename or "replay.rec", data))
    # parsing takes seconds: run it off the event loop so other requests aren't blocked
    return await run_in_threadpool(_parse_source, lambda workdir, scanner: save_uploads(uploads, workdir, scanner))


@app.post("/api/v1/replays/parse-path")
def parse_replay_path(payload: ReplayPathRequest) -> dict[str, Any]:
    if not r6_dissect_available():
        raise HTTPException(status_code=503, detail="The r6-dissect replay parser is not available.")
    source = Path(payload.path).expanduser()
    return _parse_source(lambda workdir, scanner: collect_rec_files(source, workdir, scanner))


@app.post("/api/v1/seasons/{season}/matches/log")
def log_match(season: str, payload: MatchLogRequest) -> dict[str, Any]:
    # only the players picked for this match are logged, never every tracked player who was in it
    picked = [p.strip() for players in payload.rosters.values() for p in players if p.strip()]
    if not picked:
        raise HTTPException(status_code=400, detail="Pick the players to save from this match.")
    with StatsManager(season=_season_or_400(season)) as manager:
        for team, players in payload.rosters.items():
            # a replay's own labels ("YOUR TEAM", ...) would lump every opponent together
            generic = team.strip().upper() in GENERIC_TEAM_NAMES
            manager.add_players([p for p in players if p.strip()], team=None if generic else team.strip())
        try:
            result = manager.log_match(payload.match, players=picked)
        except StatsError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise HTTPException(status_code=400, detail=f"That isn't a parsed match: {error!r}") from error
        return {
            "match_id": result.match_id,
            "rounds_logged": result.rounds_logged,
            "rounds_skipped_duplicate": result.rounds_skipped_duplicate,
            "players_logged": sorted(result.players_logged),
            "untracked_players": sorted(result.untracked_players),
            "warnings": result.warnings,
        }


@app.get("/api/v1/events")
def list_events(tier: list[str] = Query(default=[]), region: str = "", q: str = "") -> dict[str, Any]:
    """The leagues and tournaments a team can play in: Liquipedia's S- to D-Tier events that are on
    now, coming up or ended in the last six months (filtered by `tier`, `region`, and `q` against
    names, organizers and teams), and NECC's Rainbow Six divisions."""
    catalog = events.current_catalog()
    tiers = {t.strip().upper() for t in tier if t.strip()}
    query = q.strip().casefold()
    found = [e for e in events.current_events(catalog)
             if (not tiers or e["tier"] in tiers) and (not region or e["region"].casefold() == region.strip().casefold())
             and (not query or query in e["name"].casefold() or query in e["organizer"].casefold()
                  or any(query in team.casefold() for team in e["teams"]))]
    return {"events": found, "necc": events.necc_events(), "captured_at": catalog.get("captured_at", ""),
            "necc_captured_at": events.necc_captured_at(), "attribution": events.ATTRIBUTION,
            "license_url": events.LICENSE_URL}


@app.get("/api/v1/schools")
def schools() -> dict[str, Any]:
    if os.environ.get("NECC_R6_DATA_URL"):
        try:
            return {"schools": fetch_school_catalog(), "source": "configured-feed"}
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=503, detail=f"NECC feed unavailable: {error}") from error
    catalog = _catalog_path()
    if catalog.is_file():
        try:
            return {"schools": normalize_school_catalog(json.loads(catalog.read_text(encoding="utf-8"))), "source": "imported"}
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise HTTPException(status_code=500, detail=f"Saved school catalog is invalid: {error}") from error
    return {"schools": bundled_school_catalog(), "source": "NECC LeagueOS · September 26, 2026 snapshot"}


@app.post("/api/v1/schools/catalog")
async def import_school_catalog(request: Request) -> dict[str, Any]:
    body = await request.body()
    if len(body) > MAX_CATALOG_BYTES:
        raise HTTPException(status_code=413, detail="The catalog exceeds the 2 MB limit.")
    try:
        payload = json.loads(body)
        normalized = normalize_school_catalog(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=f"Invalid school catalog: {error}") from error
    path = _catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schools": normalized}, indent=2), encoding="utf-8")
    return {"schools": normalized, "source": "imported"}