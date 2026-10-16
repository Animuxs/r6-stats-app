"""Identical detailed player columns for built teams and season teams in every edition."""
from ask_engine import player_report
from metrics_engine import pro_league_row, round_eps


def player_table(records: list[dict]) -> list[dict]:
    table = []
    for r in sorted(records, key=lambda r: -(r["eps"] or 0)):
        row = pro_league_row(
            team=0, player=r["name"], eps=round_eps(r["eps"] or 0), kills=r["kills"], deaths=r["deaths"],
            entry_kills=r["entry_kills"], entry_deaths=r["entry_deaths"], kost_pct=r["kost"] or 0,
            kpr=r["kpr"] or 0, hs_pct=r["hs"] or 0, srv_pct=r["survival"] or 0, clutches=r["clutches"],
            multikills=r["multikills"], objectives=r["objectives"], traded=r["traded"], trade_kills=r["trade_kills"])
        row.pop("Team")
        player, eps = row.pop("Player"), row.pop("EPS")
        table.append({"Player": player, "Matches": r["matches"], "EPS": eps,
                      "All-time EPS": "—" if r["all_time_eps"] is None else str(r["all_time_eps"]), **row, "Rounds": r["rounds"],
                      "K / D / A": f"{r['kills']} / {r['deaths']} / {r.get('assists', 0)}",
                      "K/D": round(r["kills"] / r["deaths"], 2) if r["deaths"] else r["kills"],
                      "Entry +/-": r["entry_kills"] - r["entry_deaths"]})
    return table


def season_player_table(db, manager, team: str) -> list[dict]:
    stats = manager.get_team_stats(team)
    if not stats:
        return []
    rows = []
    for p in stats.member_stats:
        ids = [r["match_id"] for r in manager.match_history([p.username])]
        row = player_report(db, p.username, ids)
        if row:
            rows.append(row)
        else:
            # Preserve historic totals when old replay data predates the main stats database.
            rounds, t = p.totals["rounds_played"], p.totals
            rows.append(dict(name=p.username, matches=len(ids), eps=p.eps, all_time_eps=None,
                             rounds=rounds, assists=t["assists"], kills=t["kills"], deaths=t["deaths"], entry_kills=t["entry_kills"],
                             entry_deaths=t["entry_deaths"], kost=p.kost_pct, kpr=t["kills"] / rounds if rounds else 0,
                             hs=p.hs_pct, survival=100 * t["kost_survive"] / rounds if rounds else 0,
                             clutches=p.clutches_won, multikills="—", objectives=t["plants"] + t["defuses"],
                             traded=t["kost_traded"], trade_kills="—"))
    table = player_table(rows)
    for row in table:
        row["EPS"] = str(row["EPS"])
    return table
