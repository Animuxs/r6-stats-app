"""Operator Analytics: how each operator does, over all of a player's matches (from the stats
database) or in the match open on the Dashboard."""

from __future__ import annotations

import streamlit as st

from ask_engine import operator_table
from metrics_engine import side_label
from operator_stats import match_operator_rows
from sources import current_source, match_time, no_matches_yet, open_stats_db, parse
from ui import md

ALL, THIS = "All matches", "This match"


def this_match(match: dict) -> None:
    """Every operator played in one match: picks, round wins, and K/D as the scoreboard counts it."""
    rows, site_rows = match_operator_rows(match)
    if not rows:
        st.info("This replay doesn't include operator selections. Try a recent match replay.")
        return
    st.caption(f"{match.get('map', 'Current match')} · {len(match.get('rounds', []))} rounds · every player")
    st.dataframe(rows, hide_index=True)
    st.subheader("Site performance")
    st.dataframe(site_rows, hide_index=True)


def operator_sides(db, player_key: str) -> dict[str, str]:
    """Each operator's side ("⚔ Attack" or "♜ Defense"), from the rounds this player picked it in."""
    sides: dict[str, str] = {}
    for r in db.query("SELECT operator, side, COUNT(*) AS n FROM round_players WHERE player_key = ? "
                      "AND operator IS NOT NULL AND side IS NOT NULL GROUP BY operator, side ORDER BY n DESC",
                      (player_key,)):
        sides.setdefault(r["operator"], side_label(r["side"]))  # the side it was picked on most
    return sides


def open_match() -> tuple[dict | None, bool]:
    """The match open on the Dashboard or, before one has been opened, the newest one; and
    whether it's that newest one."""
    match = st.session_state.get("r6_last_match")
    if match:
        return match, False
    source = current_source()
    if not source or "error" in source or not source.get("groups"):
        return None, False
    newest = max(source["groups"], key=lambda n: match_time(n) or n)
    with st.spinner("Reading the match..."):
        parsed = parse(source, newest)
    return (None, False) if isinstance(parsed, Exception) else (parsed[0], True)


st.title("Operator Analytics")
view = st.segmented_control("Show", [ALL, THIS], default=ALL, label_visibility="collapsed") or ALL

if view == THIS:
    match, newest = open_match()
    if not match:
        st.info("No matches yet. Open your replays on **Dashboard** (or upload them there) first.")
    else:
        if newest:
            st.caption("Your newest match. To see another one, pick it on the **Dashboard**.")
        this_match(match)
else:
    with open_stats_db() as db:
        players = db.players()
        me = db.me()
        if not players:
            no_matches_yet(db)
            st.stop()
        keys = sorted(players, key=lambda k: (k != (me or "").casefold(), players[k].casefold()))
        player = st.selectbox("Player", keys, format_func=lambda k: players[k] + (" (you)" if players[k] == me else ""))
        sides = operator_sides(db, player)
        table = [{"Operator": r["Operator"], "Side": sides.get(r["Operator"], ""),
                  **{k: v for k, v in r.items() if k != "Operator"}} for r in operator_table(db, player)]
    if not table:
        st.info(f"No operator picks recorded for {md(players[player])}.")
    else:
        st.caption(f"{md(players[player])}'s operators over all their matches, most played first.")
        st.dataframe(table, hide_index=True, column_config={
            "Round win %": st.column_config.NumberColumn(format="%.0f%%"),
            "Headshot %": st.column_config.NumberColumn(format="%.0f%%"),
            "K/D": st.column_config.NumberColumn(format="%.2f"), "KPR": st.column_config.NumberColumn(format="%.2f"),
        })
