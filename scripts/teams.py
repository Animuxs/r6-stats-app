"""Team analytics: build a team from player names and see how it does as a team across every
match it played (from the stats database), with each player's own stats; and the season
tracker's teams, the same way."""

from __future__ import annotations

import json
import re

import streamlit as st

import team_finder
from ask_engine import rate, team_report, team_summary
from map_pool import MIN_SITE_ROUNDS, MIN_VETO_MAPS, map_pool, site_table, veto_hint
from metrics_engine import rows_csv
from team_tables import player_table, season_player_table
from season_stats import StatsError
from session import Session
from sources import match_label
from stats_db import nice_time
from tab import Tab
from ui import md

ROSTER_SIZE = 5   # players on the field
MAX_ROSTER = 10   # a team's whole roster, subs and all
NEW = "New team"
YOU = "\x00you"  # the map pool's "you" (a team can't be named this)


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
    session = Session()
    for i in range(session.get(f"roster_size_{choice}", ROSTER_SIZE)):
        name = session.get(f"roster_{choice}_{i}", "").strip()
        if name and name.casefold() not in seen:
            seen[name.casefold()] = name
    return list(seen.values())


def _save_team(choice: str) -> None:
    session = Session()
    team = session.get(f"team_name_{choice}", "").strip()
    names = _names(choice)
    if not team or not names:
        session.set("team_error", "Enter a team name and at least one player.")
        return
    with session.stats() as db:
        db.save_roster(team, names)
    session.set("team_shown", (team, session.get(f"roster_min_{choice}", 3)))
    session.team_choice = team  # the saved team is what's shown and selected now


def _found(team: str, players: list[str], need: int) -> list[team_finder.Found]:
    """The team's matches in the replays that your stats don't have yet, looked for once per roster
    and replay folder (reading every replay is slow the first time) and kept in the session."""
    session = Session()
    source = session.replays
    if source is None or not source.adds_to_stats:
        return []
    names = source.matches()
    key = (team, tuple(sorted(p.casefold() for p in players)), need, len(names), names[0] if names else "")
    cached = session.get("team_found")
    if cached and cached[0] == key:
        return cached[1]
    bar = st.progress(0.0, text=f"Looking for {md(team)}'s matches in your replays…")
    with session.stats() as db:
        found = team_finder.find(source, db, players, need,
                                 progress=lambda done, total: bar.progress(done / max(total, 1)))
    bar.empty()
    session.set("team_found", (key, found))
    return found


def _add_found(found: list[team_finder.Found]) -> None:
    """Add these found matches to your stats and say how it went on the next run."""
    session = Session()
    with session.stats() as db:
        added, failed = team_finder.add(session.replays, db, found)
    session.pop("team_found")  # look again: what's left to add has changed
    session.drop("team_pick_")
    text = f"Added {len(added)} match{'es' if len(added) != 1 else ''} to your stats." if added else "Nothing was added."
    if failed:
        text += " Not added: " + "; ".join(f"{f.summary()} ({why})" for f, why in failed[:5])
    session.leave_note("teams", "warning" if failed else "success", text)


@st.dialog("Pick matches to add", width="large")
def _pick_matches(team: str, found: list[team_finder.Found]) -> None:
    """Every match found, with when, where, the score and who it was against: tick the ones to add."""
    st.caption(f"{len(found)} of {md(team)}'s matches in your replays aren't in your stats yet. Tick the ones to add; "
               "only the team's players in each are kept, like picking them on the Dashboard.")
    every = st.checkbox("Tick all", value=True, key="team_pick_all")
    box = st.container(height=min(440, 44 * len(found) + 10))
    ticked = [f for i, f in enumerate(found)
              if box.checkbox(md(f.summary()), value=every, key=f"team_pick_{i}_{every}")]
    if st.button(f"Add {len(ticked)} match{'es' if len(ticked) != 1 else ''}", type="primary",
                 disabled=not ticked, key="team_pick_add"):
        _add_found(ticked)
        st.rerun()


def _find_matches(team: str, players: list[str], need: int) -> None:
    """Under a team's stats: how many of its matches your stats are missing, with one button to add
    them all and one to look through them and add one or some."""
    if note := Session().take_note("teams"):
        getattr(st, note[0])(md(note[1]))
    found = _found(team, players, need)
    if not found:
        return
    with st.container(border=True):
        st.markdown(f"**{len(found)} more of {md(team)}'s match{'es' if len(found) != 1 else ''}** in your replays "
                    f"(with {need}+ of the team on one side) aren't in your stats yet.")
        quick, pick, _ = st.columns([1, 1, 2])
        quick.button(f"Quick add all {len(found)}", type="primary", key="team_add_all",
                     on_click=_add_found, args=(found,))
        if pick.button("Review and pick…", key="team_review"):
            _pick_matches(team, found)


def _delete_team(team: str) -> None:
    with Session().stats() as db:
        db.delete_roster(team)
    Session().team_choice = NEW
    Session().pop("team_shown")


def _delete_season_team(season: str, team: str) -> None:
    with Session().tracker(season) as manager:
        manager.delete_team(team)
    Session().drop(f"in_season_{season}_")
    Session().set("season_message", (f"Deleted {team} from {season}. Other seasons and built teams are kept.", []))


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
        # a slider needs two ends: with one player there's nothing to choose (a slider from 1 to 1 crashes)
        need = st.slider("Show matches with at least this many of the team on one side", 1, len(players),
                         min(3, len(players)), key=f"season_need_{season}_{team}") if len(players) > 1 else 1
        with Session().stats() as db:
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
            saved = st.form_submit_button("Save season matches", type="primary", disabled=bool(Session().tracker_off))
        if saved:
            ticked = {m for m in listed | set(gone) if Session().get(_picker_key(season, m))}
            _apply_season_matches(season, [r for r in rows if r["match_id"] in ticked - counted],
                                  sorted((listed | set(gone)) & counted - ticked))


def _apply_season_matches(season: str, add: list[dict], remove: list[str]) -> None:
    """Save the ticked matches to the season (from their replays) and take out the unticked ones.
    A match saves only the players picked for it when it was added on the Dashboard."""
    failed = []
    session = Session()
    source = session.replays  # any kind: the matches are read again from wherever they are
    with session.stats() as db:
        picked = db.players_by_match()
    with session.tracker(season) as manager:
        for match_id in remove:
            manager.remove_match(match_id)
        for r in add:
            label = match_label(r["source"], r)
            parsed = source.open(r["source"]) if source and source.has(r["source"]) else None
            if parsed is None or isinstance(parsed, Exception):
                failed.append(label)
                continue
            try:
                manager.log_match(parsed[0], players=picked.get(r["match_id"], []))
            except StatsError:
                failed.append(label)
    session.drop(f"in_season_{season}_")  # the boxes show what's saved again
    done = " and ".join(d for d in (f"added {len(add) - len(failed)}" if add else "",
                                    f"took out {len(remove)}" if remove else "") if d)
    session.set("season_message", (f"Season matches saved: {done}." if done else "Nothing changed.", failed))
    st.rerun()


class TeamsTab(Tab):
    """Build a team from player names and see how it does as a team; and the season tracker's teams."""

    title, name = "Team Analytics", "teams"

    def render(self) -> None:
        build_tab, season_tab, pool_tab = st.tabs(["Build a team", "Season teams", "Map pool"])
        with build_tab:
            self.build_a_team()
        with season_tab:
            self.season_teams()
        with pool_tab:
            self.map_pool()

    def build_a_team(self) -> None:
        st.caption("Pick your players (up to ten, subs included). Every match you've added with enough of them tracked on the same side is "
                   "found: you see how they do as a team, and each player's own stats. Teams you build are saved, and "
                   "**Ask** understands their names.")
        with self.stats() as db:
            has_matches = self.has_matches(db)  # says where to start when there are none
            me = db.me()
            teams = db.rosters()
            mates = [name for name, _ in db.teammates(me, limit=ROSTER_SIZE - 1)] if me else []

        if has_matches or teams:
            if self.session.team_choice not in [NEW, *teams]:
                # your team (saved on League & Team) to start with, else a new one
                mine = self.session.competition["team"].casefold()
                self.session.team_choice = next((t for t in teams if t.casefold() == mine), NEW)
            choice = st.selectbox("Team", [NEW, *teams], key="team_choice") if teams else NEW
            # a new team starts as you and the players you've played with most; your first one is named after
            # your team on League & Team, so "Show team stats" works in one click
            first_name = "" if teams else (self.session.competition.get("team") or "").strip()
            name, players = (first_name, [me, *mates] if me else []) if choice == NEW else (choice, teams[choice])
            # the whole roster, subs and all, and a spare box for one more
            size = min(MAX_ROSTER, max(ROSTER_SIZE, len(players) + (choice != NEW)))
            self.session.set(f"roster_size_{choice}", size)
            players = (players + [""] * size)[:size]
            # a saved team's stats come first; its roster is a click away. A new team starts with the form.
            editor = st.expander(f"Edit {md(choice)}", icon=":material/edit:") if choice != NEW else st.container()
            with editor, st.form(f"roster_form_{choice}"):
                st.text_input("Team name", value=name, placeholder="e.g. Varsity", key=f"team_name_{choice}")
                for row in range(0, size, ROSTER_SIZE):
                    for i, col in enumerate(st.columns(ROSTER_SIZE), row):
                        if i < size:
                            col.text_input(f"Player {i + 1}", value=players[i], placeholder="Username",
                                           key=f"roster_{choice}_{i}")
                st.slider("Count a match when at least this many of them were on the same side", 1, ROSTER_SIZE, 3,
                          key=f"roster_min_{choice}")
                st.form_submit_button("Show team stats", type="primary", on_click=_save_team, args=(choice,))
            if error := self.session.pop("team_error"):
                st.error(error)
            if choice != NEW:
                editor.button(f"Delete {md(choice)}", on_click=_delete_team, args=(choice,), type="tertiary")

            # the stats of the team picked above (never another one shown before), with the number of its
            # players last asked for
            shown = self.session.get("team_shown")
            need = shown[1] if shown and shown[0] == choice else self.session.get(f"roster_min_{choice}", 3)
            shown = (choice, need) if choice in teams else None
            if shown:
                with self.stats() as db:
                    report = team_report(db, *shown)
                team, need = shown
                _find_matches(team, teams[team], need)
                if not report["matches"]:
                    st.warning(f"No match had {need}+ of {md(team)}'s players on the same side. Check the spelling of "
                               f"the names, or lower the number in **Edit {md(team)}**.")
                else:
                    _team_stats(report["summary"])
                    if report["missing"]:
                        st.warning(f"Not found in {md(team)}'s matches: {md(', '.join(report['missing']))}. "
                                   "Check the spelling.")
                    table = player_table(report["players"])
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
                    if not self.session.tracker_off and track.button(f"Also track {md(team)} in the season tracker"):
                        with self.session.tracker() as manager:
                            manager.add_players(teams[team], team=team)
                        st.success(f"Tracking {len(teams[team])} players as {md(team)}. Save matches from Dashboard to add "
                                   "them to the season.")

    def season_teams(self) -> None:
        st.caption("The season tracker: the players you track and the matches you count toward the season. Pick the "
                   "matches below, or save one from the Dashboard.")
        if self.session.demo:  # the tracker holds real seasons: the demo season's made-up matches stay out of it
            st.info("The season tracker isn't part of the demo season. Leave the demo from the sidebar to see your own "
                    "seasons. **Build a team** shows Lakeside Varsity's demo stats.")
            return
        season = st.text_input("Season", value=self.session.season, key="team_season").strip() or "current"
        self.session.season = season
        with self.session.tracker(season) as manager:
            season_teams = {t.team: t for t in (manager.get_team_stats(n) for n in manager.teams()) if t}
            rosters = manager.tracked_teams()
            for name, t in season_teams.items():  # a team's players: those pinned to it, and any with its matches
                rosters[name] = list(dict.fromkeys(rosters.get(name, []) + t.players))
            counted_by = {name: {m["match_id"] for m in manager.match_history(players)} for name, players in rosters.items()}
            saved = manager.match_history()
            snapshot = manager.export_json()
        if message := self.session.pop("season_message"):
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
            st.button("Delete season team", key="delete_season_team", disabled=bool(self.session.tracker_off),
                      on_click=_delete_season_team, args=(season, selected), type="tertiary")
            st.caption("Deletes this team's counted results from this season. Built teams, match history and other seasons are kept.")
            _season_matches(season, selected, rosters[selected], counted_by[selected])
            season_ids = [m["match_id"] for m in saved]
            # team stats and EPS are worked out again from the stats database, over the season's matches
            with self.stats() as db:
                # each team over the matches counted for it, not every match in the season
                summaries = {name: team_summary(db, t.players, 1, sorted(counted_by[name]))
                             for name, t in season_teams.items()}
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
                with self.stats() as db, self.session.tracker(season) as manager:
                    detail = season_player_table(db, manager, selected)
                st.dataframe(detail, hide_index=True)
                st.download_button("Download season players CSV", rows_csv(detail).encode("utf-8"),
                                   file_name="season_players.csv", mime="text/csv")
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
            with self.stats() as db:  # name the saved matches the way the rest of the app does
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

    def map_pool(self) -> None:
        st.caption("How you, or a team you built, do on each map: the record, the rounds won on attack and defense, "
                   "and the bomb sites won most and least often. Use it to pick and ban maps.")
        with self.stats() as db:
            me = db.me()
            teams = db.rosters()
        options = ([YOU] if me else []) + list(teams)
        if not options:
            st.info("No matches yet. Add some on **Dashboard** and your maps show up here.")
            return
        if self.session.get("pool_whose") not in options:  # a team deleted since, or a new session
            self.session.pop("pool_whose")
        whose = st.selectbox("Whose maps", options, key="pool_whose",
                             format_func=lambda o: f"You ({me})" if o == YOU else o)
        players = [me] if whose == YOU else teams[whose]
        need = 1
        if whose != YOU and len(players) > 1:  # a slider from 1 to 1 crashes
            need = st.slider("Count a match when at least this many of them were on the same side", 1,
                             min(ROSTER_SIZE, len(players)), min(3, len(players)), key=f"pool_need_{whose}")
        with self.stats() as db:
            pool = map_pool(db, players, need)
            if not pool:
                st.info("No matches with that many of the team on one side yet. Lower the number above, or add "
                        "matches on **Dashboard**, picking these players.")
                return
            picked = None
            hint = veto_hint(pool)
            if hint:
                best, worst = hint
                st.markdown(f"**Strongest map:** {md(best['map'])}, {best['round_win']:.0f}% of rounds won over "
                            f"{best['maps']} maps. **Weakest:** {md(worst['map'])}, {worst['round_win']:.0f}% over "
                            f"{worst['maps']} maps.")
            table = [{"Map": p["map"], "Maps": p["maps"], "Map W–L": f"{p['maps_won']}–{p['maps_lost']}",
                      "Round win %": p["round_win"], "Attack win %": p["attack_win"], "Defense win %": p["defense_win"],
                      "Best defense site": p["best_defense"], "Worst defense site": p["worst_defense"],
                      "Best attack site": p["best_attack"], "Worst attack site": p["worst_attack"]} for p in pool]
            percent = {c: st.column_config.ProgressColumn(c, format="%.0f%%", min_value=0, max_value=100)
                       for c in ("Round win %", "Attack win %", "Defense win %")}
            st.dataframe(table, hide_index=True, column_config=percent)
            st.caption(f"Every round counts once for the whole team. A site is where the defenders set up. Strongest "
                       f"and weakest need {MIN_VETO_MAPS}+ maps played, and a best or worst site {MIN_SITE_ROUNDS}+ "
                       "rounds on that side: a few rounds say little, so check how many before a veto.")
            picked = st.selectbox("Sites on", [p["map"] for p in pool], key=f"pool_map_{whose}")
            sites = site_table(db, players, picked, need)
        if sites:
            st.dataframe([{"Site": s["site"], "Attack rounds": s["attack"],
                           "Attack W–L": f"{s['attack_won']}–{s['attack'] - s['attack_won']}",
                           "Attack win %": rate(s["attack_won"], s["attack"]), "Defense rounds": s["defense"],
                           "Defense W–L": f"{s['defense_won']}–{s['defense'] - s['defense_won']}",
                           "Defense win %": rate(s["defense_won"], s["defense"])} for s in sites],
                         hide_index=True, column_config=percent)
        else:
            st.caption(f"The replays of {md(picked)} don't say which sites were played.")
        safe_name = re.sub(r"[^\w\- ]+", "", me if whose == YOU else whose).strip() or "team"
        st.download_button("⬇ CSV", rows_csv(table).encode("utf-8"), file_name=f"{safe_name}_map_pool.csv",
                           mime="text/csv", key="pool_csv")


TeamsTab().run()
