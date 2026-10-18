"""The Players tab: one player's profile over every match they're in (headline numbers, recent
form, best maps, operators, attack and defense), or two players side by side: who's ahead on each
stat, and their record together and against each other. The numbers come from player_profile.py."""

from __future__ import annotations

import streamlit as st

from ask_engine import METRICS, fmt
from branding import current_theme
from player_profile import GOAL_MATCHES, GOAL_STATS, HEADLINE, comparison, form_trend, goal_progress, profile, shared, verdict
from scoreboard_image import player_card_png
from session import Session
from tab import Tab
from ui import md

NOBODY = "\x00nobody"  # "Compare with": no one (a username can't be this)
TREND = {"up": "📈 Trending up: their last few matches beat the ones before.",
         "down": "📉 Trending down: their last few matches are below the ones before.",
         "steady": "➡️ Steady: about the same as the matches before."}


def _save_goal(player: str) -> None:
    session = Session()
    with session.stats() as db:
        db.save_goal(player, session.get("goal_stat"), session.get("goal_target"))


def _clear_goal(player: str, stat: str) -> None:
    with Session().stats() as db:
        db.save_goal(player, stat, None)


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
            # opened from an answer ("compare me and Sacri"): those two, picked
            if (asked := st.query_params.get("player")) and asked.casefold() in keys:
                self.session.set("players_first", asked.casefold())
                vs = (st.query_params.get("vs") or "").casefold()
                self.session.set("players_second", vs if vs in keys and vs != asked.casefold() else NOBODY)
            for param in ("player", "vs"):
                st.query_params.pop(param, None)
            # a pick from another database (the demo season, before it was left) isn't one of these players
            # (set, not dropped: the picker on screen only follows a value it's given)
            if self.session.get("players_first") not in keys:
                self.session.set("players_first", keys[0])
            left, right = st.columns(2)
            first = left.selectbox("Player", keys, format_func=label, key="players_first")
            others = [NOBODY] + [k for k in keys if k != first]
            if self.session.get("players_second") not in others:  # the player just picked first
                self.session.set("players_second", NOBODY)
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
        card = player_card_png(p.name, f"{p.matches} match{'es' if p.matches != 1 else ''} · "
                                       f"{p.stats.get('rounds') or 0} rounds",
                               [(METRICS[k].label, p.show(k)) for k in HEADLINE],
                               [(b["label"], b["value"]) for b in p.bests], current_theme()["primary"])
        st.download_button("⬇ Player card", card, file_name=f"{p.key}_player_card.png", mime="image/png",
                           help="This profile as a picture, in your Team Theme's color, to post in a team chat.")
        self.bests(p)
        self.goals(p)
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
        if p.opponents:
            st.subheader("Against opponents")
            st.dataframe(p.opponents, hide_index=True, column_config={
                "Win %": st.column_config.NumberColumn(format="%d%%"),
                "K/D": st.column_config.NumberColumn(format="%.2f")})
        st.page_link("charts.py", label="Chart any of these over time on Charts", icon=":material/insights:")

    @staticmethod
    def bests(p) -> None:
        """Personal bests: their best single matches, aces and clutches, and their streaks."""
        st.subheader("Personal bests")
        if run := p.streak:
            now = {1: f"on a **{run.current}-match win streak**", 0: f"on a **{run.current}-match losing streak**"}.get(
                run.current_won, "coming off a draw")
            st.caption(f"{md(p.name)} is {now}. Longest win streak: {run.best_win} · longest losing streak: "
                       f"{run.best_loss}.")
        if not p.bests:
            st.caption("No full-length matches yet (5 rounds or more).")
            return
        for start in range(0, len(p.bests), 4):
            row = p.bests[start:start + 4]
            for col, best in zip(st.columns(4), row):
                where = " · ".join(x for x in (best["when"], best["map"]) if x)
                col.metric(best["label"], best["value"], help=where or None)

    def goals(self, p) -> None:
        """The player's goals, each measured over their newest matches, and a form to set or clear one."""
        st.subheader("Goals")
        with self.stats() as db:
            progress = goal_progress(db, p.key)
        if progress:
            st.caption(f"Measured over {md(p.name)}'s last {GOAL_MATCHES} matches.")
        for goal in progress:
            mark = "✅" if goal["met"] else "🎯"
            text, clear = st.columns([5, 1], vertical_alignment="center")
            text.progress(goal["progress"], text=f"{mark} {goal['label']}: {goal['shown']} "
                                                f"(goal {goal['target_shown']})")
            clear.button("Clear", key=f"goal_clear_{goal['key']}", on_click=_clear_goal, args=(p.key, goal["key"]),
                         type="tertiary")
        with st.form(f"goal_form_{p.key}", border=False):
            stat, target, save = st.columns([2, 1, 1], vertical_alignment="bottom")
            stat.selectbox("Set a goal", GOAL_STATS, format_func=lambda k: METRICS[k].label, key="goal_stat")
            target.number_input("Target", min_value=0.0, max_value=1000.0, value=1.0, step=0.05, key="goal_target")
            save.form_submit_button("Save goal", on_click=_save_goal, args=(p.key,))

    # ------------------------------------------------------------ two players --
    def compare(self, a, b, both) -> None:
        rows = comparison(a, b)
        st.markdown(md(verdict(a, b, rows)))
        mark = lambda r, i: "✅ " if r["leader"] == i else ""  # noqa: E731
        # the columns are named after the players, so one named "Stat" still gets a column of their own
        first, second = (a.name, b.name) if "Stat" not in (a.name, b.name) else (f"{a.name} (1)", f"{b.name} (2)")
        st.dataframe([{"Stat": r["label"], first: mark(r, 0) + r["a"], second: mark(r, 1) + r["b"]} for r in rows],
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
                st.markdown(f"**{md(p.name)}**")
                if p.maps:
                    st.dataframe(p.maps[:3], hide_index=True, column_config={
                        "Win %": st.column_config.NumberColumn(format="%d%%"),
                        "K/D": st.column_config.NumberColumn(format="%.2f")})
                else:
                    st.caption("No map played twice yet.")


PlayersTab().run()
