"""Team analytics: build a team from player names and see how it does as a team across every
match it played (from the stats database), with each player's own stats; and the season
tracker's teams, the same way."""

from __future__ import annotations

import json
import re

import streamlit as st

from ask_engine import eps_by_player, rate, team_report, team_summary
from metrics_engine import pro_league_row, round_eps, rows_csv
from season_stats import StatsError, StatsManager
from sources import current_source, demo_active, match_label, no_matches_yet, open_stats_db, parse, tracker_off
from stats_db import nice_time
from ui import md

ROSTER_SIZE = 5
NEW = "New team"


def _fmt_eps(eps: int | None) -> str:
    return "—" if eps is None else str(eps)


def _pct(part: int, whole: int) -> str:
    value = rate(part, whole)
    return "—" if value is None else f"{value:.0f}%"


def _team_stats(summary: dict) -> None:
    """A team's stats as a team: its maps and rounds, and how it does in the situations that
    decide rounds. Never its players' numbers added together."""
    s = summary
    played = s["rounds_won"] + s["rounds_lost"]
    m = st.columns(4)
    m[0].metric("Maps", s["maps"])
    m[1].metric("Map W–L", f"{s['maps_won']}–{s['maps_lost']}", help="Maps won–lost")
    m[2].metric("Round W–L", f"{s['rounds_won']}–{s['rounds_lost']}")
    m[3].metric("Round win %", _pct(s["rounds_won"], played))
    stopped = s["defense_rounds"] - s["enemy_plants"]
    # (situation, how many, out of how many): the man-down rows after the first count only the
    # rounds where the team went down 2+ players
    rows = [
        ("Man down: went down 2+ players", s["man_down"], played),
        ("Man down: got back to even numbers", s["man_down_even"], s["man_down"]),
        ("Man down: won the round anyway", s["man_down_won"], s["man_down"]),
        ("Attack: planted the defuser", s["plants"], s["attack_rounds"]),
        ("Attack: won after planting", s["post_plant_won"], s["post_plant"]),
        ("Defense: stopped the plant", stopped, s["defense_rounds"]),
        ("Defense: won after their plant", s["retakes_won"], s["retakes"]),
    ]
    st.dataframe([{"Situation": name, "%": rate(part, whole), "Rounds": f"{part} of {whole}"}
                  for name, part, whole in rows], hide_index=True,
                 column_config={"%": st.column_config.ProgressColumn("%", format="%.0f%%", min_value=0, max_value=100)})
    st.caption(f"{s['rounds']} rounds, each counted once for the whole team, even if a player disconnected. "
               "Man down: the team had two or more fewer players alive than the other team. "
               "**Got back to even** counts those rounds where both teams later had the same number alive, won or "
               "not; **won the round anyway** counts those it won.")
    if s["maps_without_rounds"]:
        st.caption(f"{s['maps_without_rounds']} of these maps were read by an earlier version and their replays are "
                   "gone, so their rounds aren't in the round numbers.")


def _names(choice: str) -> list[str]:
    """The player names typed into the form for `choice`, without blanks or repeats (any case)."""
    seen: dict[str, str] = {}
    for i in range(ROSTER_SIZE):
        name = st.session_state.get(f"roster_{choice}_{i}", "").strip()
        if name and name.casefold() not in seen:
            seen[name.casefold()] = name
    return list(seen.values())


def _save_team(choice: str) -> None:
    team = st.session_state.get(f"team_name_{choice}", "").strip()
    names = _names(choice)
    if not team or not names:
        st.session_state["team_error"] = "Enter a team name and at least one player."
        return
    with open_stats_db() as db:
        db.save_roster(team, names)
    st.session_state["team_shown"] = (team, st.session_state.get(f"roster_min_{choice}", 3))
    st.session_state["team_choice"] = team  # the saved team is what's shown and selected now


def _delete_team(team: str) -> None:
    with open_stats_db() as db:
        db.delete_roster(team)
    st.session_state["team_choice"] = NEW
    st.session_state.pop("team_shown", None)


def _picker_key(season: str, match_id: str) -> str:
    return f"in_season_{season}_{match_id}"


def _season_matches(season: str, team: str, players: list[str], counted: set[str]) -> None:
    """Pick which of a team's matches count toward the season: tick Game Day, untick practice.
    Ticking a match reads its replay again and saves it to the season; unticking takes it out."""
    with st.expander(f"Choose {md(team)}'s matches for {md(season)} ({len(counted)} counted)",
                     expanded=not counted):
        st.caption("Tick the matches that count toward this season, like your Game Day matches, and untick "
                   "practice. Only ticked matches go into this season's stats, with just the players you picked "
                   "when you added each one on the Dashboard. Adding a match needs its replay on this PC; taking "
                   "one out doesn't. A match is in the season or not, so one between two of your tracked teams "
                   "counts for both.")
        need = st.slider("Show matches with at least this many of the team on one side", 1, max(1, len(players)),
                         min(3, max(1, len(players))), key=f"season_need_{season}_{team}")
        with open_stats_db() as db:
            every = db.team_matches(players)
        # a counted match always shows, even with fewer of the team than asked for
        rows = [r for r in every if r["n"] >= need or r["match_id"] in counted]
        listed = {r["match_id"] for r in rows}
        gone = sorted(counted - {r["match_id"] for r in every})  # counted, but not in the stats database
        if not rows and not gone:
            st.info("No matches with that many of the team yet. Add them on **Dashboard**, picking these players, "
                    "or lower the number above.")
            return
        with st.form(f"season_matches_{season}_{team}", border=False):
            box = st.container(height=min(420, 44 * (len(rows) + len(gone)) + 10))
            for r in rows:
                label = " · ".join(bit for bit in (match_label(r["source"], r), r["match_type"],
                                                   f"{r['n']} of the team") if bit)
                box.checkbox(md(label), value=r["match_id"] in counted, key=_picker_key(season, r["match_id"]))
            for match_id in gone:
                box.checkbox(f"{md(match_id)} · saved earlier, not in your stats database",
                             value=True, key=_picker_key(season, match_id))
            saved = st.form_submit_button("Save season matches", type="primary", disabled=bool(tracker_off()))
        if saved:
            ticked = {m for m in listed | set(gone) if st.session_state.get(_picker_key(season, m))}
            _apply_season_matches(season, [r for r in rows if r["match_id"] in ticked - counted],
                                  sorted((listed | set(gone)) & counted - ticked))


def _apply_season_matches(season: str, add: list[dict], remove: list[str]) -> None:
    """Save the ticked matches to the season (from their replays) and take out the unticked ones.
    A match saves only the players picked for it when it was added on the Dashboard."""
    failed = []
    state = current_source()
    groups = (state or {}).get("groups") or {}
    with open_stats_db() as db:
        picked = db.players_by_match()
    with StatsManager(season=season) as manager:
        for match_id in remove:
            manager.remove_match(match_id)
        for r in add:
            label = match_label(r["source"], r)
            parsed = parse(state, r["source"]) if r["source"] in groups else None
            if parsed is None or isinstance(parsed, Exception):
                failed.append(label)
                continue
            try:
                manager.log_match(parsed[0], players=picked.get(r["match_id"], []))
            except StatsError:
                failed.append(label)
    for key in [k for k in st.session_state if str(k).startswith(f"in_season_{season}_")]:
        del st.session_state[key]  # the boxes show what's saved again
    done = " and ".join(d for d in (f"added {len(add) - len(failed)}" if add else "",
                                    f"took out {len(remove)}" if remove else "") if d)
    st.session_state["season_message"] = (f"Season matches saved: {done}." if done else "Nothing changed.", failed)
    st.rerun()


st.title("Team Analytics")
build_tab, season_tab = st.tabs(["Build a team", "Season teams"])

# ------------------------------------------------------------ build a team --
with build_tab:
    st.caption("Pick up to five players. Every match you've added with enough of them tracked on the same side is "
               "found: you see how they do as a team, and each player's own stats. Teams you build are saved, and "
               "**Ask** understands their names.")
    with open_stats_db() as db:
        has_matches = db.summary()["matches"] > 0
        if not has_matches:
            no_matches_yet(db)
        me = db.me()
        teams = db.rosters()
        mates = [name for name, _ in db.teammates(me, limit=ROSTER_SIZE - 1)] if me else []

    if has_matches:
        if st.session_state.get("team_choice") not in [NEW, *teams]:
            st.session_state["team_choice"] = NEW
        choice = st.selectbox("Team", [NEW, *teams], key="team_choice") if teams else NEW
        # a new team starts as you and the players you've played with most
        name, players = ("", [me, *mates] if me else []) if choice == NEW else (choice, teams[choice])
        players = (players + [""] * ROSTER_SIZE)[:ROSTER_SIZE]
        with st.form(f"roster_form_{choice}"):
            st.text_input("Team name", value=name, placeholder="e.g. Varsity", key=f"team_name_{choice}")
            columns = st.columns(ROSTER_SIZE)
            for i, col in enumerate(columns):
                col.text_input(f"Player {i + 1}", value=players[i], placeholder="Username", key=f"roster_{choice}_{i}")
            st.slider("Count a match when at least this many of them were on the same side", 1, ROSTER_SIZE, 3,
                      key=f"roster_min_{choice}")
            st.form_submit_button("Show team stats", type="primary", on_click=_save_team, args=(choice,))
        if error := st.session_state.pop("team_error", None):
            st.error(error)
        if choice != NEW:
            st.button(f"Delete {md(choice)}", on_click=_delete_team, args=(choice,), type="tertiary")

        shown = st.session_state.get("team_shown")
        if shown and shown[0] in teams:
            with open_stats_db() as db:
                report = team_report(db, *shown)
            team, need = shown
            if not report["matches"]:
                st.warning(f"No match had {need}+ of {md(team)}'s players on the same side. Check the spelling of "
                           "the names, or lower the number above.")
            else:
                _team_stats(report["summary"])
                if report["missing"]:
                    st.warning(f"Not found in {md(team)}'s matches: {md(', '.join(report['missing']))}. "
                               "Check the spelling.")
                table = []
                for r in sorted(report["players"], key=lambda r: -(r["eps"] or 0)):
                    row = pro_league_row(
                        team=0, player=r["name"], eps=round_eps(r["eps"] or 0), kills=r["kills"], deaths=r["deaths"],
                        entry_kills=r["entry_kills"], entry_deaths=r["entry_deaths"], kost_pct=r["kost"] or 0,
                        kpr=r["kpr"] or 0, hs_pct=r["hs"] or 0, srv_pct=r["survival"] or 0, clutches=r["clutches"],
                        multikills=r["multikills"], objectives=r["objectives"], traded=r["traded"],
                        trade_kills=r["trade_kills"])
                    row.pop("Team")
                    player, eps = row.pop("Player"), row.pop("EPS")
                    table.append({"Player": player, "Matches": r["matches"], "EPS": eps,
                                  "All-time EPS": _fmt_eps(r["all_time_eps"]), **row})
                st.subheader(f"{md(team)}: players")
                st.dataframe(table, hide_index=True)
                st.caption("**EPS** here is from the matches they played for this team; a match where a player was on "
                           "the other side isn't included for them. **All-time EPS** is from every match they've "
                           "played. Both are rounds-weighted averages of their per-match EPS.")
                st.subheader("Matches")
                matches = []
                for r in report["matches"]:
                    own, other = (r["score0"], r["score1"]) if r["team"] == 0 else (r["score1"], r["score0"])
                    matches.append({"When": nice_time(r["played_at"]), "Map": r["map"], "Score": f"{own}–{other}",
                                    "Result": {1: "Win", 0: "Loss"}.get(r["won"], "Draw"), "Players": r["players"]})
                st.dataframe(matches, hide_index=True)
                dl, track = st.columns([1, 2])
                safe_name = re.sub(r"[^\w\- ]+", "", team).strip() or "team"  # a usable file name
                dl.download_button("⬇ CSV", rows_csv(table).encode("utf-8"), file_name=f"{safe_name}_team_stats.csv",
                                   mime="text/csv")
                if not tracker_off() and track.button(f"Also track {md(team)} in the season tracker"):
                    with StatsManager(season=st.session_state.get("r6_season", "current")) as manager:
                        manager.add_players(teams[team], team=team)
                    st.success(f"Tracking {len(teams[team])} players as {md(team)}. Save matches from Dashboard to add "
                               "them to the season.")

# ------------------------------------------------------------ season teams --
with season_tab:
    st.caption("The season tracker: the players you track and the matches you count toward the season. Pick the "
               "matches below, or save one from the Dashboard.")
    if demo_active():  # the tracker holds real seasons: the demo season's made-up matches stay out of it
        st.info("The season tracker isn't part of the demo season. Leave the demo from the sidebar to see your own "
                "seasons. **Build a team** shows Lakeside Varsity's demo stats.")
        st.stop()  # the last thing on the page
    season = st.text_input("Season", value=st.session_state.get("r6_season", "current"), key="team_season").strip() or "current"
    st.session_state["r6_season"] = season
    with StatsManager(season=season) as manager:
        season_teams = {t.team: t for t in (manager.get_team_stats(n) for n in manager.teams()) if t}
        rosters = manager.tracked_teams()
        for name, t in season_teams.items():  # a team's players: those pinned to it, and any with its matches
            rosters[name] = list(dict.fromkeys(rosters.get(name, []) + t.players))
        counted_by = {name: {m["match_id"] for m in manager.match_history(players)} for name, players in rosters.items()}
        saved = manager.match_history()
        snapshot = manager.export_json()
    if message := st.session_state.pop("season_message", None):
        st.success(message[0])
        if message[1]:
            st.warning("These couldn't be added, because their replays aren't on this PC anymore: "
                       + md(", ".join(message[1])))

    if not rosters:
        st.info("No season teams yet. Track a roster first: **Also track … in the season tracker** under Build a "
                "team, the Dashboard's **Also save it to the season tracker**, or **League & Team**'s NECC school directory. Then pick "
                "its matches here.")
    else:
        selected = st.selectbox("Season team", sorted(rosters, key=str.casefold))
        _season_matches(season, selected, rosters[selected], counted_by[selected])
        season_ids = [m["match_id"] for m in saved]
        # team stats and EPS are worked out again from the stats database, over the season's matches
        with open_stats_db() as db:
            # each team over the matches counted for it, not every match in the season
            summaries = {name: team_summary(db, t.players, 1, sorted(counted_by[name]))
                         for name, t in season_teams.items()}
            season_eps = {}  # like the rest of a player's season numbers, from their team's counted matches
            for name, t in season_teams.items():
                season_eps.update(eps_by_player(db, t.players, sorted(counted_by[name])))
            career = eps_by_player(db, [p for t in season_teams.values() for p in t.players])
            in_db = db.query(f"SELECT COUNT(*) AS n FROM matches WHERE match_id IN ({', '.join('?' * len(season_ids))})",
                             season_ids)[0]["n"] if season_ids else 0
        team = season_teams.get(selected)
        if team is None:
            st.info(f"No matches count for {md(selected)} in {md(season)} yet: tick them above.")
        else:
            _team_stats(summaries[team.team])
            if in_db < len(season_ids):
                st.caption(f"{len(season_ids) - in_db} of the {len(season_ids)} matches saved to {md(season)} aren't "
                           "in your stats database (their replays are gone), so these numbers leave them out.")
            st.subheader("Roster performance")
            st.dataframe([{
                "Player": player.username,
                # recalculated from the season's matches; the tracker's own number only if they're gone
                "EPS": _fmt_eps(season_eps.get(player.username.casefold()) or player.eps),
                "All-time EPS": _fmt_eps(career.get(player.username.casefold())),
                "Rounds": player.totals["rounds_played"],
                "K / D / A": f"{player.totals['kills']} / {player.totals['deaths']} / {player.totals['assists']}",
                "K/D": round(player.kd, 2),
                "Entry +/-": player.entry_diff,
                "KOST": f"{player.kost_pct:.1f}%",
                "HS": f"{player.hs_pct:.1f}%",
                "Clutches": player.clutches_won,
            } for player in team.member_stats], hide_index=True)
        if season_teams:
            st.subheader("All season teams")
            st.dataframe([{
                "Team": t.team,
                "Players": len(t.players),
                "Maps": (s := summaries[t.team])["maps"],
                "Map W–L": f"{s['maps_won']}–{s['maps_lost']}",
                "Round W–L": f"{s['rounds_won']}–{s['rounds_lost']}",
                "Round win %": _pct(s["rounds_won"], s["rounds_won"] + s["rounds_lost"]),
                "Man-down rounds": s["man_down"],
                "Back to even %": _pct(s["man_down_even"], s["man_down"]),
                "Man-down win %": _pct(s["man_down_won"], s["man_down"]),
                "Plant %": _pct(s["plants"], s["attack_rounds"]),
                "Plant stopped %": _pct(s["defense_rounds"] - s["enemy_plants"], s["defense_rounds"]),
                "Post-plant win %": _pct(s["post_plant_won"], s["post_plant"]),
                "Retake win %": _pct(s["retakes_won"], s["retakes"]),
            } for t in season_teams.values()], hide_index=True)
            st.caption("**EPS** is each player's over the matches counted in this season, and **All-time EPS** "
                       "over every match they've played: both rounds-weighted averages of their per-match EPS.")
    if saved:
        with open_stats_db() as db:  # name the saved matches the way the rest of the app does
            known = {r["match_id"]: r for r in db.match_list()}
        with st.expander(f"Matches saved to {season} ({len(saved)})"):
            st.dataframe([{
                "Match": match_label(known[m["match_id"]]["source"], known[m["match_id"]])
                if m["match_id"] in known else m["match_id"],
                "Saved": nice_time(m["saved_at"][:19].replace("T", " ")),
                "Rounds": m["rounds"],
                "Tracked players": m["players"],
            } for m in saved], hide_index=True)
    st.download_button("Export season report", json.dumps(snapshot, indent=2).encode("utf-8"),
                       file_name=f"{season}_team_report.json", mime="application/json")
