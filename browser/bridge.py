"""The browser edition's bridge to the app's own Python: it runs in the page (Pyodide) next to
scripts/parser.py and scripts/metrics_engine.py, so a replay read in the browser gets exactly the
numbers the Windows app shows. browser/build.py also runs it once to bake in the demo match."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

import metrics_engine as me
import parser as replay_parser


def report(match: dict, warnings: list[str] | tuple = ()) -> dict:
    """Everything the page shows for a match: the scorecard, both scoreboards (the 12 Pro League
    columns), every round, and the plain-text and CSV exports."""
    stats = me.compute_match_metrics(match)
    rows = me.pro_league_rows(stats)
    top = max(stats.values(), key=lambda p: (p.eps, p.kills), default=None)
    names = list(match.get("team_names") or ["Team 1", "Team 2"])[:2]
    return {
        "match": {"map": match.get("map") or "", "match_type": match.get("match_type") or "",
                  "match_id": match.get("match_id") or "", "team_names": names,
                  "final_score": list(match.get("final_score") or [0, 0])[:2], "rounds": len(match.get("rounds") or [])},
        "rows": rows,
        "rounds": me.round_rows(match, stats),
        "top": {"name": top.name, "eps": top.eps} if top else None,
        "any_kills": any(s.kills for s in stats.values()),
        "scoreboard_text": me.scoreboard_text(match, rows),
        "rounds_text": me.rounds_text(match),
        "csv": me.rows_csv(rows),
        "warnings": list(warnings),
        "data": match,
        "player_details": {name: {"EPS": s.eps, "rounds": [
            dict(asdict(r), highlights=r.highlights()) for r in s.round_breakdown
        ]} for name, s in stats.items()},
    }


def analyze(raw_json: str, warnings_json: str = "[]") -> str:
    """r6-dissect's JSON for a match (from the WebAssembly parser) -> the page's report, as JSON."""
    match = replay_parser.normalize_from_r6_dissect(json.loads(raw_json))
    return json.dumps(report(match, json.loads(warnings_json)), ensure_ascii=False)


def demo() -> str:
    return json.dumps(report(replay_parser.load_demo_match()), ensure_ascii=False)


def match_operators(match_json: str) -> str:
    from operator_stats import match_operator_rows
    rows, sites = match_operator_rows(json.loads(match_json))
    return json.dumps({"operators": rows, "sites": sites}, ensure_ascii=False)


def normalized(match_json: str) -> str:
    """Reopen a confirmed match without parsing its replay a second time."""
    return json.dumps(report(json.loads(match_json)), ensure_ascii=False)


def workspace(payload: str, action: str = "overview", argument: str = "{}") -> str:
    """Use the desktop's SQLite engines on confirmed browser matches, never browser SQL.

    The page persists its portable workspace in IndexedDB. Rebuilding this isolated database
    makes edits/removals and season inclusion idempotent, and keeps demo data out of real stats.
    """
    from stats_db import StatsDB
    from season_stats import StatsManager
    from ask_engine import ask, team_report, operator_table, team_summary
    from team_tables import player_table, season_player_table

    state, args = json.loads(payload), json.loads(argument)
    entries = state.get("matches", [])
    teams = state.get("teams", {})
    with TemporaryDirectory(prefix="r6-browser-") as folder:
        path = Path(folder) / "stats.db"
        with StatsDB(path) as db:
            for entry in entries:
                # An explicit empty selection never silently turns into "all players".
                if entry.get("players"):
                    db.import_match(entry["key"], entry["data"], players=entry["players"],
                                    files=len(entry["data"].get("rounds", [])))
            for name, players in teams.items():
                db.save_roster(name, players)
            if action == "ask":
                answer = asdict(ask(str(args.get("question", "")), db))
                answer.pop("query", None)
                result = answer
            elif action == "team":
                result = team_report(db, str(args.get("team", "")),
                                     min_players=max(1, min(5, int(args.get("minimum", 3)))))
                result["table"] = player_table(result["players"])
            elif action == "operators":
                result = operator_table(db, str(args.get("player") or db.me() or ""))
            elif action == "season":
                season = str(args.get("season") or "current")[:180]
                with StatsManager(path, season=season) as manager:
                    for name, players in state.get("seasonTeams", teams).items():
                        manager.add_players(players, team=name)
                    if not state.get("demo"):
                        for entry in entries:
                            if season in entry.get("seasons", [entry.get("season")]) and entry.get("players"):
                                manager.log_match(entry["data"], players=entry["players"])
                    for name in state.get("removedSeasonTeams", {}).get(season, []):
                        manager.delete_team(name)
                    result = manager.export_json()
                    result["rosters"] = manager.tracked_teams()
                    result["tables"] = {name: season_player_table(db, manager, name) for name in manager.teams()}
                    result["summaries"] = {name: team_summary(db, players, 1,
                        [m["match_id"] for m in manager.match_history(players)])
                        for name, players in manager.tracked_teams().items()}
            else:
                result = {"summary": db.summary(), "me": db.me(), "players": list(db.players().values()),
                          "matches": db.match_list(db.me()), "teams": db.rosters(),
                          "tracked": db.players_by_match()}
        return json.dumps(result, ensure_ascii=False, allow_nan=False)

