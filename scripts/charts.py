"""The Charts tab: any chart of any stat. Type what you want to see ("my K/D over time vs Sacri", "pie of the
operators I play", "heatmap of win rate by map and side"), or pick it from the menus: the request fills the
menus in, so it can be adjusted from there. Charts come from chart_engine.py and are drawn by chart_view.py."""

from __future__ import annotations

from dataclasses import fields

import streamlit as st

from ask_engine import METRICS, Vocab
from chart_engine import AXES, CHART_METRICS, KINDS, SPLITS, ChartSpec, build, describe, parse
from chart_view import draw
from metrics_engine import rows_csv
from session import Session
from tab import Tab
from ui import md

EXAMPLES = ["My K/D over time", "Pie of the operators I play", "Heatmap of win rate by map and side",
            "Scatter K/D vs KOST for every player", "Rolling average of my EPS", "Win rate by map",
            "Box plot of kills by side", "Running total of my kills"]
WHO = {"me": "You", "players": "Players I pick", "team": "Teams I pick", "everyone": "Everyone",
       "teammates": "My teammates", "opponents": "My opponents", "my_team": "My side (we)"}
SIDES = {"": "Both sides", "attack": "Attack", "defense": "Defense"}
STATS = {k: METRICS[k].label for k in CHART_METRICS}
DOTS = {k: AXES[k] for k in ("player", "operator", "map")}
EVERY_TYPE = "Every match type"
# each menu: its key's ending, and {value: what the menu shows} (the menus hold what they show)
MENUS = {"kind": KINDS, "metric": STATS, "metric2": STATS, "x": AXES, "split": SPLITS, "who": WHO, "side": SIDES}
_KEY = "chart_"  # the menus' keys: chart_kind, chart_metric, ...
_SAVED = "chart_saved"  # charts kept for this session: [(title, spec fields)]


def _value(session: Session, name: str) -> str:
    """A menu's value (what it stands for, not what it shows)."""
    shown = session.get(_KEY + name)
    return next((v for v, label in MENUS[name].items() if label == shown), next(iter(MENUS[name])))


def _show(session: Session, name: str, value: str | None) -> None:
    menu = MENUS[name]
    session.set(_KEY + name, menu.get(value or "", next(iter(menu.values()))))


def _put(session: Session, spec: ChartSpec) -> None:
    """Set the menus to a spec (in a callback, before they're drawn)."""
    for name in ("kind", "metric", "x", "split", "who", "side"):
        _show(session, name, getattr(spec, name))
    _show(session, "metric2", spec.metric2 or "kost")
    for name in ("players", "teams", "maps", "operators", "rolling", "cumulative"):
        session.set(_KEY + name, getattr(spec, name))
    session.set(_KEY + "match_type", spec.match_type or EVERY_TYPE)
    session.set(_KEY + "last_n", spec.last_matches or 0)
    for name in ("since", "until", "tag"):
        session.set(_KEY + name, getattr(spec, name))
    session.set(_KEY + "notes", list(spec.notes))


def _from_prompt() -> None:
    session = Session()
    text = (session.get("chart_prompt") or "").strip()
    if not text:
        return
    with session.stats() as db:
        if not db.summary()["matches"]:
            return
        spec = parse(text, Vocab.from_db(db))
    _put(session, spec)
    session.set("chart_asked", text)


def _example(text: str) -> None:
    Session().set("chart_prompt", text)
    _from_prompt()


def _restore(index: int) -> None:
    session = Session()
    saved = session.get(_SAVED, [])
    if 0 <= index < len(saved):
        _put(session, ChartSpec(**saved[index][1]))


def _forget(index: int) -> None:
    session = Session()
    saved = list(session.get(_SAVED, []))
    if 0 <= index < len(saved):
        del saved[index]
        session.set(_SAVED, saved)


def _edited() -> None:
    """A menu was changed by hand: it's no longer just the request's chart, and the filters a request set
    without a menu (dates, a tag) go."""
    session = Session()
    for name in ("since", "until", "tag", "notes"):
        session.set(_KEY + name, None if name != "notes" else [])
    session.set("chart_asked", None)


class ChartsTab(Tab):
    """Any chart, from a sentence or the menus."""

    title, name = "Charts", "charts"

    def render(self) -> None:
        st.caption("Draw any stat any way: progress over time, comparisons, shares, spreads, two stats against each "
                   "other. Type what you want to see, or pick it from the menus.")
        with self.stats() as db:
            if not self.has_matches(db):
                return
            vocab = Vocab.from_db(db)
            if asked := self.session.pop("chart_from_elsewhere", None):  # the assistant's or Ask's chart
                self.session.set("chart_prompt", asked)
                _put(self.session, parse(asked, vocab))
                self.session.set("chart_asked", asked)
            with st.form("chart_form", border=False):
                left, right = st.columns([5, 1], vertical_alignment="bottom")
                left.text_input("What do you want to see?", key="chart_prompt",
                                placeholder="e.g. my K/D over time vs Sacri, or a pie of the operators I play")
                right.form_submit_button("Draw", type="primary", on_click=_from_prompt, width="stretch")
            columns = st.columns(4)
            for i, example in enumerate(EXAMPLES):
                columns[i % 4].button(example, key=f"chart_example_{i}", on_click=_example, args=(example,),
                                      width="stretch")
            spec = self.menus(vocab)
            chart = build(spec, db, vocab)
            st.divider()
            draw(chart, height=380)
            if chart.ok:
                st.caption(md(describe(chart)))
                self.actions(chart, spec)
            self.saved()

    def menus(self, vocab: Vocab) -> ChartSpec:
        """The chart's menus (filled in by a request), read into a spec."""
        s = self.session
        if not s.has(_KEY + "kind"):
            _put(s, ChartSpec())
        names = {key: vocab.players[key] for key in sorted(vocab.players, key=lambda k: vocab.players[k].casefold())}
        known = {"players": names, "teams": vocab.rosters, "maps": vocab.maps, "operators": vocab.operators}
        for key, values in known.items():  # drop what this database doesn't have
            s.set(_KEY + key, [v for v in s.get(_KEY + key) or [] if v in values])
        if s.get(_KEY + "match_type") not in [EVERY_TYPE, *vocab.match_types]:
            s.set(_KEY + "match_type", EVERY_TYPE)
        kind = _value(s, "kind")
        if kind == "scatter" and _value(s, "x") not in DOTS:
            _show(s, "x", "player")  # a scatter's dots are players, operators or maps
        with st.expander("Customize the chart", expanded=not s.get("chart_asked"), icon=":material/tune:"):
            a, b, c, d = st.columns(4)
            a.selectbox("Chart", list(KINDS.values()), key=_KEY + "kind", on_change=_edited)
            kind = _value(s, "kind")
            b.selectbox("Stat", list(STATS.values()), key=_KEY + "metric", on_change=_edited)
            if kind == "scatter":
                c.selectbox("Against", list(STATS.values()), key=_KEY + "metric2", on_change=_edited)
                if _value(s, "x") not in DOTS:
                    _show(s, "x", "player")
                d.selectbox("Each dot is a", list(DOTS.values()), key=_KEY + "x", on_change=_edited)
            elif kind == "histogram":
                c.caption("A histogram shows how often each value came up: one per player per match.")
            else:
                c.selectbox("Slices" if kind == "pie" else "Along", list(AXES.values()), key=_KEY + "x",
                            on_change=_edited)
                d.selectbox("Against" if kind == "heatmap" else "Split by", list(SPLITS.values()), key=_KEY + "split",
                            on_change=_edited)
            e, f, g, h = st.columns(4)
            e.selectbox("Whose stats", list(WHO.values()), key=_KEY + "who", on_change=_edited)
            who = _value(s, "who")
            if who == "players":
                f.multiselect("Players", list(names), format_func=names.get, key=_KEY + "players",
                              placeholder="Pick players", on_change=_edited)
            elif who == "team":
                f.multiselect("Teams", list(vocab.rosters), key=_KEY + "teams",
                              placeholder="Build teams on Team Analytics", on_change=_edited)
            g.multiselect("Maps", vocab.maps, key=_KEY + "maps", placeholder="Every map", on_change=_edited)
            h.selectbox("Side", list(SIDES.values()), key=_KEY + "side", on_change=_edited)
            i, j, k, m = st.columns(4)
            i.multiselect("Operators", vocab.operators, key=_KEY + "operators", placeholder="Every operator",
                          on_change=_edited)
            j.selectbox("Match type", [EVERY_TYPE, *vocab.match_types], key=_KEY + "match_type", on_change=_edited)
            k.number_input("Last N matches (0 = all)", min_value=0, max_value=500, step=1, key=_KEY + "last_n",
                           on_change=_edited)
            if kind in ("line", "area"):
                m.slider("Smooth over", 0, 10, key=_KEY + "rolling", help="A moving average over this many points")
                m.checkbox("Running total", key=_KEY + "cumulative", help="Add each point to the ones before (counts)")
        match_type = s.get(_KEY + "match_type")
        spec = ChartSpec(kind=kind, metric=_value(s, "metric"), metric2=_value(s, "metric2"),
                         x=_value(s, "x") if kind != "histogram" else "match", split=_value(s, "split"),
                         who=who, players=s.get(_KEY + "players") or [], teams=s.get(_KEY + "teams") or [],
                         maps=s.get(_KEY + "maps") or [], operators=s.get(_KEY + "operators") or [],
                         side=_value(s, "side") or None, match_type=None if match_type == EVERY_TYPE else match_type,
                         last_matches=s.get(_KEY + "last_n") or None, since=s.get(_KEY + "since"),
                         until=s.get(_KEY + "until"), tag=s.get(_KEY + "tag"),
                         rolling=(s.get(_KEY + "rolling") or 0) if kind in ("line", "area") else 0,
                         cumulative=bool(s.get(_KEY + "cumulative")) and kind in ("line", "area"),
                         notes=list(s.get(_KEY + "notes") or []))
        if who == "players" and not spec.players:
            spec.who = "me"
        if who == "team" and not spec.teams:
            spec.who = "my_team"
        if s.get("chart_asked"):
            st.caption(f"🔎 Drawn from: “{md(s.get('chart_asked'))}”. Change anything above.")
        return spec

    def actions(self, chart, spec: ChartSpec) -> None:
        with st.expander("The numbers", icon=":material/table:"):
            table = chart.table()
            st.dataframe(table, hide_index=True)
            st.download_button("⬇ CSV", rows_csv(table).encode("utf-8"), file_name="r6_chart.csv", mime="text/csv",
                               key="chart_csv")
        if st.button("Keep this chart", icon=":material/bookmark_add:", key="chart_keep",
                     help="Keep it in the list below for this session, to come back to"):
            saved = list(self.session.get(_SAVED, []))
            values = {f.name: getattr(spec, f.name) for f in fields(ChartSpec)}
            if all(v != values for _, v in saved):
                saved.insert(0, (chart.title, values))
                self.session.set(_SAVED, saved[:20])
                st.rerun()

    def saved(self) -> None:
        saved = self.session.get(_SAVED, [])
        if not saved:
            return
        st.subheader("Kept charts")
        for i, (title, _) in enumerate(saved):
            left, right = st.columns([8, 1], vertical_alignment="center")
            left.button(title, key=f"chart_saved_{i}", on_click=_restore, args=(i,), icon=":material/insights:",
                        width="stretch")
            right.button("", key=f"chart_forget_{i}", on_click=_forget, args=(i,), icon=":material/delete:",
                         help="Forget it")


ChartsTab().run()
