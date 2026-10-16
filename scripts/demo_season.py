"""
demo_season.py
==============
A made-up season for trying the whole app without any replays: twelve league matches (first to
7) of a college team, Lakeside Varsity, against six opponents across the ranked map pool, in the internal MatchData
schema (see parser.py). The rounds are simulated from a fixed seed, so the season is the same
every time; every kill, plant and defuse is in each round's events, and the stats come from
metrics_engine like any real replay's.

The demo season (app.py's "Demo season" switch, or ?demo in the website's address) loads it into a
stats database of its own for that browser session, never anyone's real stats: Ask, Match
History, Team Analytics and Operator Analytics then have a season to show, and the Dashboard can
open any of its matches.
"""

from __future__ import annotations

import random
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

TEAM = "Lakeside Varsity"
ROSTER = ["Fabian", "Kanto", "Sens", "Sacri", "BC"]  # Fabian recorded every replay: "you"
YOU = ROSTER[0]
OPPONENTS = [
    ("Northgate Esports", ["Rook1e", "Vexa", "Moth", "Calder", "Pip"]),
    ("Harbor State", ["Tidal", "Brine", "Skiff", "Anchor", "Gull"]),
    ("Ridgeview Gold", ["Quarry", "Flint", "Basalt", "Ore", "Shale"]),
    ("Westfield Black", ["Nocturne", "Umbra", "Raven", "Inkwell", "Soot"]),
    ("Pine Valley", ["Sap", "Needle", "Cone", "Bark", "Lichen"]),
    ("Metro Tech", ["Volt", "Diode", "Ohm", "Relay", "Byte"]),
]
MAPS = {
    "Bank": ["CEO/Executive Lounge", "Open Area/Staff Room", "Tellers/Archives", "Lockers/CCTV Room"],
    "Border": ["Ventilation Room/Armory Lockers", "Workshop/Customs Inspection", "Supply Room/Tellers"],
    "Chalet": ["Master Bedroom/Office", "Bar/Gaming Room", "Kitchen/Trophy Room", "Wine Cellar/Snowmobile Garage"],
    "Clubhouse": ["Gym/Bedroom", "Cash Room/CCTV Room", "Bar/Stock Room", "Church/Arsenal Room"],
    "Consulate": ["Consul Office/Meeting Room", "Lobby/Press Room", "Archives/Tellers", "Garage/Cafeteria"],
    "Kafe Dostoyevsky": ["Cocktail Lounge/Bar", "Fireplace Hall/Mining Room", "Reading Room/Fireplace Hall",
                         "Kitchen Service/Kitchen Cooking"],
    "Lair": ["Master Office/R6 Room", "Lab Support/Meeting Room", "Bunks/Briefing Room", "Armory/Weapon Maintenance"],
    "Nighthaven Labs": ["Command Center/Server Room", "Kitchen/Cafeteria", "Animal Pen/Tank", "Storage/Assembly"],
    "Skyscraper": ["Tea Room/Karaoke", "Exhibition/Office", "Kitchen/BBQ", "Bedroom/Bathroom"],
}
ATTACK_POOL = ["Ash", "Thermite", "Hibana", "Sledge", "Thatcher", "Zofia", "Ace", "Iana", "Nomad", "Twitch",
               "Buck", "Capitão", "Flores", "Gridlock", "Maverick", "Dokkaebi", "Lion", "Brava", "Deimos"]
DEFENSE_POOL = ["Jäger", "Kaid", "Mute", "Valkyrie", "Bandit", "Smoke", "Mira", "Echo", "Lesion", "Wamai",
                "Azami", "Thunderbird", "Mozzie", "Melusi", "Aruni", "Solis", "Fenrir", "Thorn", "Castle"]
WEAPONS = ["R4-C", "556XI", "F2", "AK-12", "ARX200", "MP5", "Vector .45 ACP", "T-5 SMG", "MP7", "416-C Carbine"]
SEED = 1606  # 1.6.0
START = datetime(2026, 8, 24, 19, 30)


def _skills(names: list[str], rng: random.Random, base: float) -> dict[str, float]:
    return {name: base * rng.uniform(0.75, 1.3) for name in names}


def _simulate_round(rng: random.Random, number: int, attack: int, winner: int, rosters: list[list[str]],
                    skill: dict[str, float], mains: dict[str, tuple[str, str]], sites: list[str]) -> dict[str, Any]:
    """One round: who died to whom and when, the plant and defuse, and how it was won."""
    defense = 1 - attack
    if winner == attack:
        condition = rng.choices(["KilledOpponents", "DefusedBomb"], [0.72, 0.28])[0]
    else:
        condition = rng.choices(["KilledOpponents", "DisabledDefuser", "Time"], [0.66, 0.2, 0.14])[0]
    loser = 1 - winner
    deaths = [loser] * (5 if condition == "KilledOpponents" else rng.randint(1, 4))
    deaths += [winner] * rng.choices([0, 1, 2, 3, 4], [0.14, 0.28, 0.3, 0.2, 0.08])[0]
    rng.shuffle(deaths)
    if condition == "KilledOpponents":  # the round ends on the last of the losers going down
        deaths.remove(loser)
        deaths.append(loser)
    alive = {0: list(rosters[0]), 1: list(rosters[1])}
    events: list[dict[str, Any]] = []
    plant_after = len(deaths) // 2 if condition in ("DefusedBomb", "DisabledDefuser") else None
    planted, last_killer, t = False, None, rng.uniform(6, 15)
    for index, team in enumerate(deaths):
        if plant_after is not None and index == plant_after and alive[attack]:
            t += rng.uniform(5, 14)
            events.append({"type": "plant", "time": round(t, 1), "actor": rng.choice(alive[attack])})
            planted = True
        other = 1 - team
        if not alive[team] or not alive[other]:
            break
        # a trade: the last killer goes down moments later
        if last_killer in alive[team] and rng.random() < 0.35:
            victim, t = last_killer, t + rng.uniform(1.2, 3.8)
        else:
            victim, t = rng.choice(alive[team]), t + rng.uniform(4, 19)
        killer = rng.choices(alive[other], [skill[n] for n in alive[other]])[0]
        headshot = rng.random() < 0.42 + 0.1 * (skill[killer] - 1)
        weapon = rng.choice(WEAPONS)
        events += [{"type": "kill", "time": round(t, 1), "actor": killer, "target": victim, "headshot": headshot,
                    "weapon": weapon},
                   {"type": "death", "time": round(t, 1), "actor": victim, "killed_by": killer}]
        alive[team].remove(victim)
        last_killer = killer
    if condition in ("DefusedBomb", "DisabledDefuser") and not planted and alive[attack]:
        t += rng.uniform(5, 12)
        events.append({"type": "plant", "time": round(t, 1), "actor": rng.choice(alive[attack])})
    if condition == "DisabledDefuser" and alive[defense]:
        t += rng.uniform(8, 30)
        events.append({"type": "defuse", "time": round(t, 1), "actor": rng.choice(alive[defense])})
    every = rosters[0] + rosters[1]
    attackers = rosters[attack]
    operators = {}
    for name in every:
        main, alt = mains[name][0 if name in attackers else 1]
        operators[name] = main if rng.random() < 0.7 else alt
    return {"round_num": number, "winner_team": winner, "win_condition": condition, "attack_team": attack,
            "site": rng.choice(sites), "operators": operators, "events": events,
            "alive_start": {"team0": rosters[0], "team1": rosters[1]}}


def _match(rng: random.Random, index: int) -> tuple[str, dict[str, Any]]:
    opponent, their_roster = OPPONENTS[index % len(OPPONENTS)]
    map_name = list(MAPS)[(index * 5) % len(MAPS)]
    played = START + timedelta(days=index * 4 + (index % 3), minutes=15 * (index % 4))
    rosters = [ROSTER, their_roster]
    skill = {**_skills(ROSTER, rng, 1.0), **_skills(their_roster, rng, rng.uniform(0.85, 1.12))}
    skill[YOU] = max(skill[YOU], 1.15)
    mains = {name: ((rng.choice(ATTACK_POOL), rng.choice(ATTACK_POOL)),
                    (rng.choice(DEFENSE_POOL), rng.choice(DEFENSE_POOL))) for name in rosters[0] + rosters[1]}
    strength = sum(skill[n] for n in ROSTER) / (sum(skill[n] for n in ROSTER) + sum(skill[n] for n in their_roster))
    # league format: first to 7, sides swap after 6 rounds; at 6-6, overtime to 8, swapping every round
    attack, score, rounds = rng.randint(0, 1), [0, 0], []
    target = 7
    while max(score) < target:
        chance = strength - (0.05 if attack == 0 else -0.05)  # attacking is a little harder
        winner = 0 if rng.random() < chance else 1
        rounds.append(_simulate_round(rng, len(rounds) + 1, attack, winner, rosters, skill, mains, MAPS[map_name]))
        score[winner] += 1
        if score == [6, 6]:
            target = 8
        if len(rounds) == 6 or len(rounds) >= 12:
            attack = 1 - attack
    folder = f"Match-{played:%Y-%m-%d_%H-%M-%S}-{1600 + index}"
    return folder, {
        "match_id": f"demo-season-{index + 1:02d}",
        "map": map_name,
        "match_type": "Custom game",
        "team_names": [TEAM, opponent],
        "final_score": score,
        "recording_player": YOU,
        "played_at": f"{played:%Y-%m-%d %H:%M:%S}",
        "players": [{"name": n, "team": 0 if n in ROSTER else 1,
                     "operator_history": [r["operators"][n] for r in rounds]} for n in rosters[0] + rosters[1]],
        "rounds": rounds,
    }


def season(matches: int = 12) -> dict[str, dict[str, Any]]:
    """{replay folder name: MatchData} for the demo season, oldest first."""
    rng = random.Random(SEED)
    return dict(_match(rng, index) for index in range(matches))


def load(db: Any, matches: int = 12) -> dict[str, dict[str, Any]]:
    """Add the demo season to `db` (a StatsDB) as if each match had been confirmed on the Dashboard
    with Lakeside Varsity's five players, and save the team on Build a team. Returns the season."""
    found = season(matches)
    for folder, match in found.items():
        db.import_match(folder, match, files=len(match["rounds"]), players=ROSTER)
    db.save_roster(TEAM, ROSTER)
    return found


def database_folder() -> Path:
    """A folder for one session's demo stats database."""
    return Path(tempfile.mkdtemp(prefix="r6-match-demo-"))


COMPETITION = {"source": "custom", "id": "", "name": "R6 Match Stats Demo League", "tier": "", "team": TEAM,
               "url": "", "season": ""}
# the banner's identity during the demo: the default colors, with the demo team's name
THEME = {"name": TEAM, "team": "Demo season · made-up matches", "primary": "#52d5f2", "secondary": "#f6be4f",
         "logo": ""}
