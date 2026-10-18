"""The Players tab: one player's profile over every match they're in (headline numbers, recent
form, best maps, operators, attack and defense), or two players side by side: who's ahead on each
stat, and their record together and against each other. The numbers come from player_profile.py."""

from __future__ import annotations

import streamlit as st

from ask_engine import METRICS, fmt
from player_profile import HEADLINE, comparison, form_trend, profile, shared, verdict
from tab import Tab
from ui import md

NOBODY = "\x00nobody"  # "Compare with": no one (a username can't be this)
TREND = {"up": "📈 Trending up: their last few matches beat the ones before.",
         "down": "📉 Trending down: their last few matches are below the ones before.",
         "steady": "➡️ Steady: about the same as the matches before."}


class PlayersTab(Tab):
    """One player's profile, or two players compared."""

    title, name = "Players", "players"

    def render(self) -> None:
        with self.stats() as db:
            names = db.players()
            if not names:
                self.has_matches(db)
                return
            me = db.me()
            keys = sorted(names, key=lambda k: (k != (me or "").casefold(), names[k].casefold()))
            label = lambda k: names[k] + (" (you)" if names[k] == me else "")  # noqa: E731
            left, right = st.columns(2)
            first = left.selectbox("Player", keys, format_func=label, key="players_first")
            others = [NOBODY] + [k for k in keys if k != first]
            second = right.selectbox("Compare with", others, key="players_second",
                                     format_func=lambda k: "Nobody: just this player" if k == NOBODY else label(k))
            a = profile(db, first)
            if second and second != NOBODY:
                b = profile(db, second)
                both = shared(db, first, second)
                self.compare(a, b, both)
            else:
                self.one(a)

    # ------------------------------------------------------------- one player --
    def one(self, p) -> None:
        if not p.matches:
            st.info(md(f"{p.name} isn't in any of your matches."))
            return
        st.caption(md(f"{p.name} over {p.matches} match{'es' if p.matches != 1 else ''} "
                      f"and {p.stats.get('rounds') or 0} rounds."))
        for row in (HEADLINE[:4], HEADLINE[4:]):
            for col, key in zip(st.columns(len(row)), row):
                col.metric(METRICS[key].label, p.show(key))
        st.subheader("Recent form")
        if trend := form_trend(p.form):
            st.caption(TREND[trend])
        st.dataframe([{k: v for k, v in r.items() if k != "match_id"} for r in p.form], hide_index=True,
                     column_config={"K/D": st.column_config.NumberColumn(format="%.2f")})
        maps, ops = st.columns(2)
        with maps:
            st.subheader("Best maps")
            if p.maps:
                st.dataframe(p.maps[:5], hide_index=True, column_config={
                    "Win %": st.column_config.NumberColumn(format="%d%%"),
                    "K/D": st.column_config.NumberColumn(format="%.2f")})
            else:
                st.caption("Play a map twice to see it here.")
        with ops:
            st.subheader("Top operators")
            if p.operators:
                st.dataframe(p.operators, hide_index=True, column_config={
                    "Round win %": st.column_config.NumberColumn(format="%d%%"),
                    "K/D": st.column_config.NumberColumn(format="%.2f")})
            else:
                st.caption("These replays don't include operator picks.")
        if p.sides:
            st.subheader("Attack and defense")
            for col, side, icon in zip(st.columns(2), ("attack", "defense"), ("⚔", "♜")):
                numbers = p.sides.get(side)
                with col:
                    st.markdown(f"**{icon} {side.title()}**")
                    if not numbers:
                        st.caption("No rounds on this side yet.")
                        continue
                    st.caption(f"{numbers['rounds']} rounds · round win {fmt(numbers['win_rate'], 'pct')} · "
                               f"K/D {fmt(numbers['kd'], 'ratio')} · KOST {fmt(numbers['kost'], 'pct')}")
        st.page_link("charts.py", label="Chart any of these over time on Charts", icon=":material/insights:")

    # ------------------------------------------------------------ two players --
    def compare(self, a, b, both) -> None:
        rows = comparison(a, b)
        st.markdown(md(verdict(a, b, rows)))
        mark = lambda r, i: "✅ " if r["leader"] == i else ""  # noqa: E731
        st.dataframe([{"Stat": r["label"], a.name: mark(r, 0) + r["a"], b.name: mark(r, 1) + r["b"]} for r in rows],
                     hide_index=True)
        st.caption("✅ marks who's ahead on each stat. Fewer deaths per round is better.")
        st.subheader("Together and against")
        together, against = st.columns(2)
        won, lost = both.together_record
        together.metric("Matches as teammates", len(both.together))
        if both.together:
            together.caption(f"Record together: {won}-{lost}"
                             + (f" ({fmt(both.together_win_rate, 'pct')} won)" if won + lost else ""))
        against.metric("Matches against each other", len(both.against))
        if both.against:
            a_won, b_won = both.against_record
            against.caption(md(f"{a.name} won {a_won}, {b.name} won {b_won}"))
        if not both.together and not both.against:
            st.caption(md(f"{a.name} and {b.name} haven't been in a match together yet."))
        st.subheader("Best maps")
        for col, p in zip(st.columns(2), (a, b)):
            with col:
                st.markdown(md(f"**{p.name}**"))
                if p.maps:
                    st.dataframe(p.maps[:3], hide_index=True, column_config={
                        "Win %": st.column_config.NumberColumn(format="%d%%"),
                        "K/D": st.column_config.NumberColumn(format="%.2f")})
                else:
                    st.caption("No map played twice yet.")


PlayersTab().run()
