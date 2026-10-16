"""The browser edition's bridge to the app's own Python: it runs in the page (Pyodide) next to
scripts/parser.py and scripts/metrics_engine.py, so a replay read in the browser gets exactly the
numbers the Windows app shows. browser/build.py also runs it once to bake in the demo match."""

from __future__ import annotations

import json

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
    }


def analyze(raw_json: str, warnings_json: str = "[]") -> str:
    """r6-dissect's JSON for a match (from the WebAssembly parser) -> the page's report, as JSON."""
    match = replay_parser.normalize_from_r6_dissect(json.loads(raw_json))
    return json.dumps(report(match, json.loads(warnings_json)), ensure_ascii=False)


def demo() -> str:
    return json.dumps(report(replay_parser.load_demo_match()), ensure_ascii=False)
