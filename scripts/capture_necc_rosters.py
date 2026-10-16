"""
capture_necc_rosters.py
=======================
Make the NECC rosters snapshot each release ships (necc_rosters.json): every team in NECC's
Rainbow Six divisions, with its school, colors, logo, record and players, read from the public
Rosters tab of each division's LeagueOS page (the links in necc_divisions.json).

    pip install playwright && python -m playwright install chromium
    python scripts/capture_necc_rosters.py

LeagueOS has no public API: its pages fill themselves in from an API that only answers its own
site. So this opens each division's Rosters tab in a real browser, as a visitor would, and keeps
what the page was sent: seven page loads, a few seconds apart, naming this project in the
User-Agent. Run it by hand before a release, like `python scripts/events.py`; the app itself never
contacts LeagueOS.

Only what tracking a team needs is kept: each player's league name and the Ubisoft name(s) they
play under (what replays show), whether they're listed as a player or staff, and the team's name,
school, group, colors, logo and win-loss record. Discord and any other linked accounts are dropped.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from app_info import APP_NAME, APP_VERSION, GITHUB_REPO  # noqa: E402
from events import NECC_DIVISIONS, save_catalog  # noqa: E402

OUT = HERE / "necc_rosters.json"
USER_AGENT = f"{APP_NAME.replace(' ', '')}/{APP_VERSION} (https://github.com/{GITHUB_REPO})"
PAUSE = 4.0  # seconds between page loads
PLAYER_ROLE = 1 << 20  # LeagueOS's role flag for a roster's players (others are coaches and managers)
IMAGES = "https://images.leagueos.gg/teams"


def team_from_roster(raw: dict[str, Any], division: dict[str, Any], groups: dict[str, str]) -> dict[str, Any]:
    """One LeagueOS season roster as a snapshot team."""
    stats = raw.get("stats") or {}
    players = []
    for member in sorted((raw.get("members") or {}).values(), key=lambda m: m.get("teamRank") or 99):
        if member.get("active") is False:
            continue
        accounts = ((member.get("connections") or {}).get("ubisoft") or {}).get("accounts") or []
        ubisoft = list(dict.fromkeys(str(a["name"]).strip() for a in accounts if str(a.get("name") or "").strip()))
        name = str(member.get("leagueTag") or "").strip() or (ubisoft[0] if ubisoft else "")
        if name:
            players.append({"name": name, "ubisoft": ubisoft, "player": bool((member.get("roles") or 0) & PLAYER_ROLE)})
    parent = raw.get("parent") or {}
    team_id = raw.get("sourceId") or ""
    return {
        "id": raw.get("id", ""), "team_id": team_id, "name": str(raw.get("name") or "").strip(),
        "school": str(parent.get("name") or "").strip(), "school_short": str(parent.get("nameShort") or "").strip(),
        "division": division.get("division", ""), "event_id": division.get("id", ""),
        "group": groups.get(str(raw.get("name") or "").strip(), ""),
        "color": raw.get("color") or "", "color_alt": raw.get("colorAlt") or "",
        "logo_url": f"{IMAGES}/{team_id}/{raw['avatar']}" if team_id and raw.get("avatar") else "",
        "clan_tag": raw.get("clanTag") or "",
        "record": {"wins": stats.get("wins", 0), "losses": stats.get("losses", 0),
                   "game_wins": stats.get("gameWins", 0), "game_losses": stats.get("gameLosses", 0)},
        "players": players,
    }


def tidy(teams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop what isn't a team: a registration left without a name, and an empty copy of a team
    that's listed again in the same division with its players."""
    filled = {(t["division"], t["name"].casefold()) for t in teams if t["players"]}
    return [t for t in teams if t["name"] and (t["players"] or (t["division"], t["name"].casefold()) not in filled)]


def capture() -> dict[str, Any]:
    from playwright.sync_api import sync_playwright  # only needed here, by whoever makes the snapshot

    divisions = [d for d in json.loads(NECC_DIVISIONS.read_text(encoding="utf-8"))["events"] if d.get("division")]
    teams: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(user_agent=USER_AGENT)
        for number, division in enumerate(divisions):
            if number:
                time.sleep(PAUSE)
            sent: list[dict[str, Any]] = []

            def keep(response, season=division["id"]):
                if f"/league/seasons/{season}/rosters?" in response.url and response.status == 200:
                    sent.extend(response.json()["data"]["results"])

            page.on("response", keep)
            page.goto(f"{division['url']}/rosters", wait_until="networkidle", timeout=90_000)
            page.remove_listener("response", keep)
            groups = {t["name"]: t.get("group", "") for t in division.get("teams", [])}
            found = [team_from_roster(raw, division, groups) for raw in sent]
            print(f"  Division {division['division']}: {len(found)} teams, "
                  f"{sum(len(t['players']) for t in found)} players", flush=True)
            teams += found
        browser.close()
    return {"source_url": "https://necc.v1.leagueos.gg/", "captured_at": date.today().isoformat(),
            "note": "NECC's Rainbow Six teams and their players, from each division's public Rosters tab on "
                    "LeagueOS. A dated snapshot: records are as of that day.",
            "teams": tidy(teams)}


if __name__ == "__main__":
    snapshot = capture()
    if not snapshot["teams"]:
        sys.exit("No rosters were read: the snapshot wasn't changed.")
    save_catalog(snapshot, OUT)
    print(f"{len(snapshot['teams'])} teams, {sum(len(t['players']) for t in snapshot['teams'])} players -> {OUT}")
