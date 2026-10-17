"""Operator Analytics: how each operator does, over all of a player's matches (from the stats
database) or in the match open on the Dashboard."""

from __future__ import annotations

import streamlit as st

from ask_engine import operator_table
from metrics_engine import side_label
from operator_stats import match_operator_rows
from tab import Tab
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


class OperatorsTab(Tab):
    """How each operator does: over all of a player's matches, or in one match."""

    title, name = "Operator Analytics", "operators"

    def render(self) -> None:
        view = st.segmented_control("Show", [ALL, THIS], default=ALL, label_visibility="collapsed") or ALL
        if view == THIS:
            self.one_match()
        else:
            self.all_matches()

    def open_match(self) -> tuple[dict | None, bool]:
        """The match open on the Dashboard or, before one has been opened, the newest one in the
        session's replays (whatever kind they are); and whether it's that newest one."""
        if self.session.last_match:
            return self.session.last_match, False
        source = self.session.replays
        newest = source.newest() if source and not source.error else None
        if newest is None:
            return None, False
        with st.spinner("Reading the match..."):
            parsed = source.open(newest)
        return (None, False) if isinstance(parsed, Exception) else (parsed[0], True)

    def one_match(self) -> None:
        match, newest = self.open_match()
        if not match:
            st.info("No matches yet. Open your replays on **Dashboard** (or upload them there) first.")
            return
        if newest:
            st.caption("Your newest match. To see another one, pick it on the **Dashboard**.")
        this_match(match)

    def all_matches(self) -> None:
        with self.stats() as db:
            players = db.players()
            me = db.me()
            if not players:
                self.has_matches(db)
                return
            keys = sorted(players, key=lambda k: (k != (me or "").casefold(), players[k].casefold()))
            player = st.selectbox("Player", keys,
                                  format_func=lambda k: players[k] + (" (you)" if players[k] == me else ""))
            sides = operator_sides(db, player)
            table = [{"Operator": r["Operator"], "Side": sides.get(r["Operator"], ""),
                      **{k: v for k, v in r.items() if k != "Operator"}} for r in operator_table(db, player)]
        if not table:
            st.info(f"No operator picks recorded for {md(players[player])}.")
            return
        st.caption(f"{md(players[player])}'s operators over all their matches, most played first.")
        st.dataframe(table, hide_index=True, column_config={
            "Round win %": st.column_config.NumberColumn(format="%.0f%%"),
            "Headshot %": st.column_config.NumberColumn(format="%.0f%%"),
            "K/D": st.column_config.NumberColumn(format="%.2f"), "KPR": st.column_config.NumberColumn(format="%.2f"),
        })


OperatorsTab().run()
