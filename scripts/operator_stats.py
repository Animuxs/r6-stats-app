"""Operator and bomb-site statistics shared by every app edition."""
from collections import Counter, defaultdict
from metrics_engine import compute_match_metrics, side_label

def match_operator_rows(match: dict) -> tuple[list[dict], list[dict]]:
    team_by_player = {player["name"]: player["team"] for player in match.get("players", [])}
    # a replay without each round's picks still shows who played one operator all match (as stats_db does)
    only_operator = {p["name"]: p["operator_history"][0] for p in match.get("players", [])
                     if len(p.get("operator_history") or []) == 1}
    # kills and deaths per (player, round), counted exactly as on the scoreboard (no team kills)
    round_stats = {(name, rb.round_num): rb for name, s in compute_match_metrics(match).items()
                   for rb in s.round_breakdown}
    records: dict[str, dict] = defaultdict(lambda: {
        "picks": 0, "round_wins": 0, "kills": 0, "deaths": 0, "sites": defaultdict(lambda: [0, 0]),
        "sides": Counter(),
    })
    total_picks = 0
    for round_data in match.get("rounds", []):
        in_round = round_data.get("players") or team_by_player
        operators = round_data.get("operators") or {n: op for n, op in only_operator.items() if n in in_round}
        site = round_data.get("site") or "Unknown site"
        for username, operator_name in operators.items():
            if not operator_name:
                continue
            row = records[operator_name]
            row["picks"] += 1
            total_picks += 1
            row["sites"][site][0] += 1
            if round_data.get("winner_team") == team_by_player.get(username):
                row["round_wins"] += 1
                row["sites"][site][1] += 1
            rb = round_stats.get((username, round_data.get("round_num")))
            if rb is not None:
                row["kills"] += rb.kills
                row["deaths"] += rb.deaths
                if rb.side:
                    row["sides"][rb.side] += 1
    rows, site_rows = [], []
    for name, record in sorted(records.items(), key=lambda item: (-item[1]["picks"], item[0])):
        rows.append({
            "Operator": name,
            "Side": side_label(record["sides"].most_common(1)[0][0]) if record["sides"] else "",
            "Picks": record["picks"],
            "Pick rate": f"{record['picks'] / total_picks:.0%}" if total_picks else "0%",
            "Round win rate": f"{record['round_wins'] / record['picks']:.0%}",
            "Kills / Deaths": f"{record['kills']} / {record['deaths']}",
            "K/D": round(record["kills"] / record["deaths"], 2) if record["deaths"] else record["kills"],
        })
        for site, (picks, wins) in sorted(record["sites"].items()):
            site_rows.append({"Operator": name, "Site": site, "Rounds": picks, "Wins": wins,
                              "Win rate": f"{wins / picks:.0%}" if picks else "0%"})
    return rows, site_rows
