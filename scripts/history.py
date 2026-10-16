"""Match History: the matches you've added to your stats, newest first, with your result in each
and the players tracked in it; and the matches an earlier version added on its own, to check."""

from __future__ import annotations

import streamlit as st

from metrics_engine import eps_from_rating, round_eps, rows_csv
from sources import current_source, demo_active, match_label, no_matches_yet, open_stats_db
from stats_db import NotAdded, nice_time
from ui import md


def _note(kind: str, text: str) -> None:
    st.session_state["history_note"] = (kind, text)


def _keep(match_id: str) -> None:
    players = [p for team in (0, 1) for p in st.session_state.get(f"check:{match_id}:{team}", [])]
    try:
        with open_stats_db() as db:
            db.confirm_checked(match_id, players)
    except NotAdded as e:
        _note("error", str(e))
        return
    _note("success", f"Added, with {len(players)} player{'s' if len(players) != 1 else ''}: {', '.join(players)}.")


def _remove(match_id: str, label: str) -> None:
    with open_stats_db() as db:
        db.remove_match(match_id)
    _note("success", f"Removed {label} from your stats.")


def _keep_recorders() -> None:
    """Every match waiting to be checked, with just the player who recorded it: that's you, and
    your own numbers from your own replay are always right."""
    kept = left = 0
    with open_stats_db() as db:
        for row in db.to_check():
            try:
                db.confirm_checked(row["match_id"], [row["recorder"] or ""])
                kept += 1
            except NotAdded:
                left += 1
    _note("success", f"Kept your own stats in {kept} match{'es' if kept != 1 else ''}."
          + (f" {left} didn't have you in them, so they're still waiting." if left else ""))


def _remove_all_waiting() -> None:
    with open_stats_db() as db:
        waiting = db.to_check()
        for row in waiting:
            db.remove_match(row["match_id"])
    _note("success", f"Removed {len(waiting)} match{'es' if len(waiting) != 1 else ''}.")


def check_earlier(waiting: list[dict]) -> None:
    """The matches an earlier version added on its own, one at a time: keep the players you track
    in it, or remove it."""
    n = len(waiting)
    st.warning(f"**{n} match{'es' if n != 1 else ''} added by an earlier version {'don' if n != 1 else 'doesn'}'t "
               "count yet.** The app used to add every replay on its own, with everyone in it, so a stranger on "
               "your team in Ranked counted as your teammate. Check each one and keep only the players you track.",
               icon="🔎")
    with st.expander(f"Check earlier matches ({n})", expanded=True):
        by_id = {r["match_id"]: r for r in waiting}

        def label(match_id: str) -> str:
            row = by_id[match_id]
            return " · ".join(x for x in (match_label(row["source"], row), row["match_type"]) if x)

        pick = st.selectbox("Match", list(by_id), format_func=label, key="check_match")
        row = by_id[pick]
        picked = []
        for team, column in enumerate(st.columns(2)):
            names = [p for p, t in row["players"] if t == team]
            if names:
                picked += column.multiselect(row[f"team{team}"] or f"Team {team + 1}", names,
                                             key=f"check:{pick}:{team}", placeholder="Players to track")
        keep, remove = st.columns(2)
        keep.button("Keep these players", type="primary", on_click=_keep, args=(pick,), width="stretch",
                    disabled=not picked)
        remove.button("Remove this match", on_click=_remove, args=(pick, label(pick)), width="stretch")
        st.caption("Or all at once:")
        mine, everything = st.columns(2)
        mine.button(f"Keep just your own stats in all {n}", on_click=_keep_recorders, width="stretch",
                    help="Keeps only the player who recorded each replay: you. Your own numbers are always right.")
        with everything.popover(f"Remove all {n}", width="stretch"):
            st.write(f"Remove all {n} from your stats? Matches whose replays the game has deleted can't be added "
                     "back.")
            st.button(f"Yes, remove all {n}", on_click=_remove_all_waiting, type="primary")


st.title("Match History")

if note := st.session_state.pop("history_note", None):
    getattr(st, note[0])(md(note[1]))

with open_stats_db() as db:
    source = current_source()
    me = db.me()
    rows = db.match_list(me)
    tracked = db.players_by_match()
    waiting = db.to_check()
    if not rows and not waiting:
        no_matches_yet(db)
        st.stop()

if waiting:
    check_earlier(waiting)
if not rows:
    st.stop()

mine = [r for r in rows if r["player"]]
if mine:
    wins, losses = sum(r["won"] == 1 for r in mine), sum(r["won"] == 0 for r in mine)
    kills, deaths = sum(r["kills"] for r in mine), sum(r["deaths"] for r in mine)
    rounds = sum(r["player_rounds"] for r in mine)
    columns = st.columns(4)
    columns[0].metric("Matches", len(mine))
    columns[1].metric("Record", f"{wins}–{losses}", help="Wins–losses, from your team's side")
    columns[2].metric("K/D", f"{kills / deaths if deaths else kills:.2f}")
    eps = round_eps(100 * sum(r["rating"] * r["player_rounds"] for r in mine) / rounds) if rounds else "—"
    columns[3].metric("EPS", eps, help="Your average EPS, weighted by rounds played")
st.caption((f"Your matches as **{md(me)}**, newest first. " if mine else "")
           + "**Tracked** is who you picked when you added the match: only their stats are kept. "
             "Select a match to open it on the Dashboard or remove it.")

table = []
for r in rows:
    row = {"When": nice_time(r["played_at"]), "Map": r["map"] or "", "Type": r["match_type"] or ""}
    if r["player"]:
        own, other = (r["score0"], r["score1"]) if r["team"] == 0 else (r["score1"], r["score0"])
        row |= {"Result": {1: "Win", 0: "Loss"}.get(r["won"], "Draw"), "Score": f"{own}–{other}",
                "K-D": f"{r['kills']}-{r['deaths']}", "EPS": eps_from_rating(r["rating"])}
    else:
        row |= {"Result": "—", "Score": f"{r['score0']}–{r['score1']}", "K-D": "", "EPS": None}
    row["Tracked"] = ", ".join(tracked.get(r["match_id"], []))
    table.append(row)

picked = st.dataframe(table, hide_index=True, on_select="rerun", selection_mode="single-row", key="history_rows")
chosen = picked.selection.rows if picked else []
if chosen:
    r = rows[chosen[0]]
    name, label = r["source"], match_label(r["source"], r)
    opened, removed = st.columns([1, 1])
    if demo_active() or (source and name in (source.get("groups") or {})):  # the demo's are on the Dashboard too
        if opened.button("Open on Dashboard", type="primary"):
            st.session_state["open_match"] = name
            st.switch_page("report.py")
    else:
        opened.caption("This match's replay isn't in your replay folder anymore (the game deletes old ones), "
                       "but its stats are still here and on **Ask**.")
    with removed.popover("Remove from your stats"):
        st.write(f"Remove {md(label)} from your stats?"
                 + (" Its replay is still in your folder, so you can add it again from the Dashboard."
                    if source and name in (source.get("groups") or {}) else
                    " Its replay is gone, so it can't be added back."))
        st.button("Yes, remove it", on_click=_remove, args=(r["match_id"], label), type="primary")
st.download_button("⬇ CSV", rows_csv(table).encode("utf-8"), file_name="match_history.csv", mime="text/csv")
