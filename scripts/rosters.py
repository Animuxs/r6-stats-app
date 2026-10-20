"""
rosters.py
==========
Team rosters, so nobody types their players' names: once you pick your team on League & Team, the
app knows who's on it and tracks them.

- NECC: every team in Divisions I-VI with its players' Ubisoft names (what replays show), school,
  colors, logo and record (necc_rosters.json, made with `python scripts/capture_necc_rosters.py`).
  The Windows app looks for a newer snapshot once a day when it starts (update_on_launch): the copy
  on this project's GitHub, which the maintainers refresh during a season. LeagueOS itself is never
  contacted by the app.
- Any other team: the players you play alongside most in your custom games (from_replays), and
  any players a Liquipedia event page lists for it.

A team (NECC):
    {"id", "team_id", "name", "school", "school_short", "division": "I".."VI", "event_id", "group",
     "color", "color_alt": "#rrggbb" or "", "logo_url", "clan_tag",
     "record": {"wins", "losses", "game_wins", "game_losses"},
     "players": [{"name": league name, "ubisoft": [in-game names], "player": False for staff}]}
"""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.request
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import season_stats
from app_info import APP_NAME, APP_VERSION, GITHUB_REPO

_HERE = Path(__file__).resolve().parent
BUNDLED = _HERE / "necc_rosters.json"
# the snapshot the maintainers keep current during a season, between releases
PUBLISHED_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/scripts/necc_rosters.json"
CHECK_EVERY = timedelta(days=1)
MAX_BYTES = 4 * 1024 * 1024
MIN_SHARED = 2  # players a side must share with a roster to be named after that team
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


def data_dir() -> Path:
    return Path(season_stats.DEFAULT_DB_PATH).parent


def cache_path() -> Path:
    """The newer snapshot the app last downloaded, next to the stats database."""
    return data_dir() / "necc_rosters.json"


def checked_path() -> Path:
    return data_dir() / "necc_rosters.checked"


# ------------------------------------------------------------- snapshots ----

_loaded: dict[str, tuple[tuple[int, int, int], dict[str, Any] | None]] = {}


def _clean_team(raw: Any) -> dict[str, Any] | None:
    """A team from a snapshot, every field checked: a downloaded snapshot is only data."""
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not raw["name"].strip():
        return None

    def text(key: str, limit: int = 180) -> str:
        value = raw.get(key)
        return value.strip()[:limit] if isinstance(value, str) else ""

    players = []
    for p in raw.get("players") or []:
        if not isinstance(p, dict):
            continue
        ubisoft = [n.strip()[:64] for n in p.get("ubisoft") or [] if isinstance(n, str) and n.strip()]
        name = (p.get("name") if isinstance(p.get("name"), str) else "").strip()[:64] or (ubisoft[0] if ubisoft else "")
        if name:
            players.append({"name": name, "ubisoft": list(dict.fromkeys(ubisoft)), "player": p.get("player") is not False})
    record = raw.get("record") if isinstance(raw.get("record"), dict) else {}
    logo = text("logo_url", 2048)
    return {
        "id": text("id"), "team_id": text("team_id"), "name": text("name"), "school": text("school"),
        "school_short": text("school_short"), "division": text("division", 8), "event_id": text("event_id"),
        "group": text("group"), "clan_tag": text("clan_tag", 12),
        "color": text("color", 7) if _COLOR.fullmatch(text("color", 7)) else "",
        "color_alt": text("color_alt", 7) if _COLOR.fullmatch(text("color_alt", 7)) else "",
        "logo_url": logo if logo.startswith("https://") else "",
        "record": {k: int(record.get(k) or 0) for k in ("wins", "losses", "game_wins", "game_losses")
                   if isinstance(record.get(k, 0), (int, float))},
        "players": players,
    }


def clean_snapshot(raw: Any) -> dict[str, Any] | None:
    """A snapshot with only well-formed teams, or None if it isn't one."""
    if not isinstance(raw, dict) or not isinstance(raw.get("teams"), list):
        return None
    teams = [t for t in (_clean_team(t) for t in raw["teams"]) if t]
    captured = raw.get("captured_at") if isinstance(raw.get("captured_at"), str) else ""
    if not teams or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", captured):
        return None
    return {"source_url": "https://necc.v1.leagueos.gg/", "captured_at": captured,
            "note": raw.get("note") if isinstance(raw.get("note"), str) else "", "teams": teams}


def load(path: Path) -> dict[str, Any] | None:
    """A stored snapshot, read once until the file changes; None if missing or broken."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key, stamp = str(path), (stat.st_mtime_ns, stat.st_size, stat.st_ino)  # a file replaced in the same tick is new
    if key not in _loaded or _loaded[key][0] != stamp:
        try:
            snapshot = clean_snapshot(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            snapshot = None
        _loaded[key] = (stamp, snapshot)
    return _loaded[key][1]


def snapshot() -> dict[str, Any]:
    """The newest snapshot there is: one downloaded on this PC, or the release's."""
    candidates = [s for s in (load(cache_path()), load(BUNDLED)) if s]
    if not candidates:
        return {"captured_at": "", "teams": []}
    return max(candidates, key=lambda s: s["captured_at"])


def teams() -> list[dict[str, Any]]:
    return snapshot()["teams"]


def captured_at() -> str:
    return snapshot()["captured_at"]


# ---------------------------------------------------------- the daily check ----

def due(now: datetime | None = None) -> bool:
    """Whether it's been CHECK_EVERY since the last look for a newer snapshot."""
    try:
        last = datetime.fromisoformat(checked_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return True
    return (now or datetime.now()) - last >= CHECK_EVERY


def update(fetch=None, now: datetime | None = None) -> bool:
    """Look for a newer snapshot on this project's GitHub, at most once a day; keep it if it's
    newer than what's here. Returns whether one was kept. Never raises: the app works offline."""
    if not due(now):
        return False
    try:
        checked_path().parent.mkdir(parents=True, exist_ok=True)
        checked_path().write_text((now or datetime.now()).isoformat(timespec="seconds"), encoding="utf-8")
        if fetch is None:
            request = urllib.request.Request(PUBLISHED_URL, headers={
                "User-Agent": f"{APP_NAME.replace(' ', '')}/{APP_VERSION}", "Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read(MAX_BYTES + 1)
        else:
            body = fetch()
        if len(body) > MAX_BYTES:
            return False
        fresh = clean_snapshot(json.loads(body))
        if not fresh or fresh["captured_at"] <= captured_at():
            return False
        from events import save_catalog  # writes atomically

        save_catalog(fresh, cache_path())
        return True
    except (OSError, ValueError):
        return False


_started = threading.Event()


def update_on_launch() -> None:
    """Check for a newer snapshot in the background, once per run of the app."""
    if _started.is_set():
        return
    _started.set()
    threading.Thread(target=update, name="roster-update", daemon=True).start()


# ----------------------------------------------------------- finding teams ----

def team_key(team: dict[str, Any]) -> str:
    """What identifies a NECC team: the same name can be on two divisions' rosters."""
    return f"{team.get('division', '')}|{team['name']}"


def find(name: str, division: str = "") -> dict[str, Any] | None:
    """The NECC team called `name` (any case), in `division` when given."""
    key = name.strip().casefold()
    found = [t for t in teams() if t["name"].casefold() == key]
    return next((t for t in found if t["division"] == division), None) if division else (found[0] if found else None)


def mine(competition: dict[str, Any]) -> dict[str, Any] | None:
    """The NECC team saved on League & Team (branding.current_competition), or None."""
    if competition.get("source") != "necc" or not competition.get("team"):
        return None
    event = (competition.get("id") or "").removeprefix("necc:")
    name = competition["team"].casefold()
    return next((t for t in teams() if t["name"].casefold() == name and (not event or t["event_id"] == event)),
                None) or find(competition["team"])


def search(query: str, limit: int = 30) -> list[dict[str, Any]]:
    """NECC teams whose name, school, tag or any player matches `query`, best matches first."""
    q = query.strip().casefold()
    if not q:
        return []
    scored = []
    for team in teams():
        names = [team["name"], team["school"], team["school_short"], team["clan_tag"]]
        players = [n for p in team["players"] for n in (p["name"], *p["ubisoft"])]
        if any(q == n.casefold() for n in names):
            score = 0
        elif any(n.casefold().startswith(q) for n in names):
            score = 1
        elif any(q in n.casefold() for n in names):
            score = 2
        elif any(q in n.casefold() for n in players):
            score = 3
        else:
            continue
        scored.append((score, team["division"], team["name"].casefold(), team))
    return [t for *_, t in sorted(scored, key=lambda s: s[:3])][:limit]


# ------------------------------------------------------------- the players ----

def in_game_names(team: dict[str, Any], staff: bool = False) -> list[str]:
    """The names to track for a team's players: each one's first Ubisoft name (what replays show),
    or their league name when they haven't linked one. Staff only when asked."""
    return list(dict.fromkeys(p["ubisoft"][0] if p["ubisoft"] else p["name"]
                              for p in team["players"] if staff or p["player"]))


def aliases(team: dict[str, Any]) -> dict[str, str]:
    """{any name a player goes by, casefolded: the name tracked for them}: every Ubisoft account
    they've linked and their league name, staff included (a coach can fill in)."""
    found: dict[str, str] = {}
    for p in team["players"]:
        tracked = p["ubisoft"][0] if p["ubisoft"] else p["name"]
        for name in (*p["ubisoft"], p["name"]):
            found.setdefault(name.casefold(), tracked)
    return found


def roster_names(names: Iterable[str]) -> dict[str, str]:
    """{name, casefolded: name} for a plain list of player names (a Team Analytics roster)."""
    return {n.strip().casefold(): n.strip() for n in names if n.strip()}


def on_roster(match_players: Iterable[str], roster: dict[str, str]) -> list[str]:
    """The match's players that are on the roster (an aliases() or roster_names() map), as the
    match names them."""
    return [name for name in match_players if name.casefold() in roster]


def default_selection(names: Iterable[str], roster: dict[str, str], saved: Iterable[str] = ()) -> list[str]:
    """Offer current roster members as well as previously kept players, without strangers."""
    wanted = {name.casefold() for name in saved} | set(roster)
    return [name for name in names if name.casefold() in wanted]


def identify(match: dict[str, Any], limit_to: Iterable[dict[str, Any]] | None = None) -> dict[int, dict[str, Any]]:
    """{side: NECC team} for each side of a match whose players are mostly one team's roster (at
    least MIN_SHARED of them, and more than any other team). Replays only say "YOUR TEAM" and
    "ENEMY TEAM": this names them."""
    sides: dict[int, list[str]] = {}
    for p in match.get("players") or []:
        if p.get("team") in (0, 1):
            sides.setdefault(p["team"], []).append(p["name"])
    found: dict[int, dict[str, Any]] = {}
    pool = list(limit_to) if limit_to is not None else teams()
    for side, names in sides.items():
        counts = Counter()
        for team in pool:
            shared = len(on_roster(names, aliases(team)))
            if shared >= MIN_SHARED:
                counts[team_key(team)] = shared
        if counts:
            best = counts.most_common(2)
            if len(best) == 1 or best[0][1] > best[1][1]:
                found[side] = next(t for t in pool if team_key(t) == best[0][0])
    return found


def theme_of(team: dict[str, Any]) -> dict[str, str]:
    """A team's look for branding.apply_theme: its school, name, colors and logo."""
    return {"name": team.get("school") or team["name"], "team": team["name"],
            "primary": team.get("color") or "", "secondary": team.get("color_alt") or "",
            "logo": team.get("logo_url") or ""}


def from_replays(groups: dict[str, list[str]], parse, me: str | None, limit: int = 12,
                 progress=None) -> list[tuple[str, int, int]]:
    """Your teammates in your newest custom games (where a team plays its scrims and league
    matches): [(name, games together, games looked at)], most games first, you included. `parse`
    reads one match (sources.parse). Ranked, Unranked and Quick Match teams are the game's doing,
    so they're left out."""
    from stats_db import played_at_from_folder

    newest = sorted(groups, key=lambda n: played_at_from_folder(n) or n, reverse=True)
    together: Counter = Counter()
    looked = 0
    for number, name in enumerate(newest):
        if looked >= limit or number >= limit * 3:
            break
        if progress:
            progress(number, min(len(newest), limit * 3))
        result = parse(name)
        if isinstance(result, Exception):
            continue
        match = result[0]
        if match.get("match_type") != "Custom game":
            continue
        recorder = (me or match.get("recording_player") or "").casefold()
        side = next((p["team"] for p in match.get("players") or [] if p["name"].casefold() == recorder), None)
        if side is None:
            continue
        looked += 1
        together.update(p["name"] for p in match["players"] if p["team"] == side)
    return [(name, n, looked) for name, n in together.most_common()]
