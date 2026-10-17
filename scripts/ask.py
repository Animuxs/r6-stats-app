"""The Ask page: plain-English questions about every match in the stats database (the ones you've
confirmed on the Dashboard, with the players you picked)."""

from __future__ import annotations

import altair as alt
import streamlit as st

from ask_engine import Answer, Vocab, ask, suggestions
from metrics_engine import rows_csv
from stats_db import nice_day
from branding import current_theme, readable_accent
from tab import Tab
from ui import md

_FORMATS = {"ratio": "%.2f", "pct": "%.0f%%", "eps": "%d", "count": "%d", "signed": "%+d"}
_CHART_FORMATS = {"ratio": ".2f", "pct": ".0f", "eps": "d", "count": "d", "signed": "+d"}
MUTED = "#5d7894"  # the other bars: a quiet blue that sits on the school theme's navy


def _set_question(text: str) -> None:
    st.session_state["ask_question"] = text
    st.session_state["ask_now"] = True


def _chart(answer: Answer, me: str | None) -> None:
    """The answer as a chart, in the table's order (matches oldest first, rankings best first), with
    your own bar picked out in a ranking of players."""
    x, y = answer.chart["x"], answer.chart["y"]
    data = [{"x": r[x], "y": r[y]} for r in answer.rows if r.get(y) is not None]
    number = _CHART_FORMATS.get(answer.formats.get(y, ""), "")
    accent = readable_accent(current_theme()["primary"])  # the school theme's, like the rest of the page
    chart = alt.Chart(alt.Data(values=data)).encode(
        x=alt.X("x:N", sort=None, title=None, axis=alt.Axis(labelAngle=-35, labelLimit=180)),
        y=alt.Y("y:Q", title=y, axis=alt.Axis(format=number)),
        tooltip=[alt.Tooltip("x:N", title=x), alt.Tooltip("y:Q", title=y, format=number)],
    )
    if answer.chart["kind"] == "line":
        chart = chart.mark_line(point=True, color=accent)
    else:
        chart = chart.mark_bar().encode(color=alt.condition(alt.datum.x == (me if x == "Player" else None),
                                                            alt.value(accent), alt.value(MUTED)))
    st.altair_chart(chart.properties(height=260), width="stretch")



class AskTab(Tab):
    """Plain-English questions about the matches in the session's stats database."""

    title, name = "Ask", "ask"

    def render(self) -> None:
        st.caption("Ask anything about your matches in plain English: trends, maps, operators, teammates, "
                   "your team. Everything stays on this computer.")
        with self.stats() as db:
            if not self.has_matches(db):
                return
            summary = db.summary()
            vocab = Vocab.from_db(db)
            me = vocab.players.get(vocab.me) if vocab.me else None
            st.caption(f"📚 {summary['matches']} matches, {nice_day(summary['first'])} to {nice_day(summary['last'])}"
                       + (f" · you're **{md(me)}**" if me else ""))
            question, asked = self.question_box(vocab, db)
            if (asked or self.session.pop("ask_now", False)) and question.strip():
                self.session.set("ask_last", question.strip())
            if last := self.session.get("ask_last"):
                self.show_answer(ask(last, db, vocab=vocab), me)

    def question_box(self, vocab: Vocab, db) -> tuple[str, bool]:
        """The question box and example questions; returns what's typed and whether Ask was clicked."""
        with st.form("ask_form", border=False):
            question = st.text_input("Your question", key="ask_question", label_visibility="collapsed",
                                     placeholder="e.g. How has my K/D changed over my last 10 matches?")
            asked = st.form_submit_button("Ask", type="primary")
        columns = st.columns(4)
        for i, example in enumerate(suggestions(vocab, db)):
            columns[i % 4].button(example, key=f"example_{i}", on_click=_set_question, args=(example,),
                                  width="stretch")
        return question, asked

    def show_answer(self, answer: Answer, me: str | None) -> None:
        st.divider()
        if answer.understood:
            st.caption(f"🔎 {md(answer.understood)}")
        if answer.ok:
            st.markdown(f"#### {md(answer.headline)}")
        else:
            st.warning(md(answer.headline), icon="💬")
        for note in answer.notes:
            st.caption(md(note))
        if answer.ok and answer.chart and len(answer.rows) > 1:
            _chart(answer, me)
        if answer.ok and answer.rows:
            config = {c: st.column_config.NumberColumn(format=_FORMATS[k]) for c, k in answer.formats.items()}
            st.dataframe(answer.rows, column_order=answer.columns, column_config=config, hide_index=True)
            st.download_button("⬇ CSV", rows_csv(answer.rows).encode("utf-8"), file_name="r6_answer.csv",
                               mime="text/csv")


AskTab().run()
