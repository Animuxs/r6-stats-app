"""The Ask page: plain-English questions about every match in the stats database (the ones you've
confirmed on the Dashboard, with the players you picked). It answers whatever is asked (assistant.py:
your stats, an analysis, what a stat means, how to use the app, or its best guess), and a question it
couldn't answer, or answered the wrong way, can be reported to the developers."""

from __future__ import annotations

import altair as alt
import streamlit as st

from ask_engine import Answer, Vocab, suggestions
from assistant import Reply, answer_anything
from assistant_ui import as_message, forget_report, report_box, saved_reports
from assistant_ui import show_reply as show_message
from app_info import GITHUB_REPO
from question_reports import issue_url
from metrics_engine import rows_csv
from stats_db import nice_day
from branding import current_theme, readable_accent
from session import Session
from tab import Tab
from ui import md

_FORMATS = {"ratio": "%.2f", "pct": "%.0f%%", "eps": "%d", "count": "%d", "signed": "%+d"}
_CHART_FORMATS = {"ratio": ".2f", "pct": ".0f", "eps": "d", "count": "d", "signed": "+d"}
MUTED = "#5d7894"  # the other bars: a quiet blue that sits on the school theme's navy


def _set_question(text: str) -> None:
    session = Session()
    session.set("ask_question", text)
    session.set("ask_now", True)


def _to_charts(question: str) -> None:
    session = Session()
    session.set("chart_from_elsewhere", question)
    session.set("ask_go_charts", True)


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
        if self.session.pop("ask_go_charts", False):
            self.go_to("charts")
        st.caption("Ask anything about your matches in plain English: trends, maps, operators, teammates, "
                   "your team. Ask for an analysis, what a stat means or how to use the app, too. Everything stays "
                   "on this computer.")
        with self.stats() as db:
            if not self.has_matches(db):
                self.reported()
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
                self.show_reply(answer_anything(last, db, vocab=vocab, page="ask"), me)
        self.reported()

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

    def show_reply(self, reply: Reply, me: str | None) -> None:
        """Any reply: a stats answer with its chart and table, or the assistant's words; then follow-up
        questions, and the Report button (for an answer that missed, too)."""
        if reply.chart is not None:
            st.divider()
            show_message(as_message(reply))
            st.button("Change it on Charts", icon=":material/tune:", key="ask_to_charts", on_click=_to_charts,
                      args=(reply.question,))
        elif reply.kind == "stats" and reply.answer is not None:
            self.show_answer(reply.answer, me)
            if reply.rows and not reply.answer.rows:
                st.dataframe(reply.rows, column_order=reply.columns, hide_index=True)
        else:
            st.divider()
            show_message(as_message(reply))
        if follow_ups := reply.next_questions():
            columns = st.columns(min(3, len(follow_ups)))
            for i, follow in enumerate(follow_ups[:3]):
                columns[i].button(follow, key=f"ask_next_{i}", on_click=_set_question, args=(follow,),
                                  icon=":material/subdirectory_arrow_right:", width="stretch")
        understood = reply.answer.understood if reply.answer else ""
        report_box(self.session, "ask_report", question=reply.question, answer=reply.summary(),
                   understood=understood, where="Ask", page="ask",
                   label="Report to developers" if not reply.answered else "Not what I asked? Report it")

    def reported(self) -> None:
        """The questions reported from here and the assistant, to send on or forget."""
        reports = saved_reports(self.session)
        if not reports:
            return
        with st.expander(f"Reported questions ({len(reports)})", icon=":material/flag:"):
            st.caption("Questions you reported because they weren't answered the way you asked. Send each to the "
                       "developers on GitHub (you see it before it's sent), or forget it.")
            for report in reports[:50]:
                text, send, drop = st.columns([6, 2, 1], vertical_alignment="center")
                text.markdown(f"**{md(report['question'])}**<br><small>{md(report.get('time', ''))} · "
                              f"{md(report.get('where', ''))}</small>", unsafe_allow_html=True)
                send.link_button("Send", issue_url(report, GITHUB_REPO), icon=":material/open_in_new:",
                                 width="stretch")
                drop.button("", key=f"ask_forget_{report['id']}", icon=":material/delete:", help="Forget it",
                            on_click=forget_report, args=(self.session, report["id"]))

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
