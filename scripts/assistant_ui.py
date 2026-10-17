"""
assistant_ui.py
===============
The assistant's chat panel, on every page (app.py draws it), and the Report box it shares with the
Ask page. The panel floats in the bottom-right corner: a button while it's closed, a chat while it's
open. It's a Streamlit fragment, so sending a message redraws only the panel, never the page under it,
and the conversation lives in the Session, so changing tabs keeps it (and where it was) as it was.
It can be turned off from the sidebar; the Windows app remembers that choice.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import streamlit as st

from app_info import APP_VERSION, GITHUB_REPO, desktop_data_dir, is_public_host, is_windows_app
from assistant import Conversation, Reply, starters
from chart_engine import Chart, ChartSpec
from chart_view import draw
from desktop_settings import save_setting, setting
from question_reports import delete_report, issue_url, load_reports, new_report, save_report
from session import Session
from tab import PAGES, switch_to
from ui import md

_ON, _OPEN, _CHAT, _MEMORY, _PENDING = "assistant_on", "assistant_open", "assistant_chat", "assistant_memory", \
    "assistant_pending"
_SESSION_REPORTS = "question_reports"  # on the public website, where nothing is kept on disk
MAX_MESSAGES = 60  # the oldest go once a chat is longer
# each page's title (app.py's st.navigation) -> its key in tab.PAGES
PAGE_KEYS = {"Dashboard": "dashboard", "Ask": "ask", "Charts": "charts", "Match History": "history", "Team Analytics": "teams",
             "Operator Analytics": "operators", "Match day": "matchday", "Team hub": "team",
             "League & Team": "league", "Team Theme": "theme", "Get the Windows app": "download"}
_ICONS = {"dashboard": ":material/dashboard:", "ask": ":material/chat:", "charts": ":material/insights:", "history": ":material/history:",
          "teams": ":material/groups:", "operators": ":material/target:", "matchday": ":material/event:",
          "team": ":material/badge:", "league": ":material/emoji_events:", "theme": ":material/palette:",
          "download": ":material/download:"}
_FORMATS = {"ratio": "%.2f", "pct": "%.0f%%", "eps": "%d", "count": "%d", "signed": "%+d"}


# ------------------------------------------------------------- on and off --

def enabled(session: Session) -> bool:
    """Whether the assistant is on: the Windows app's saved choice, else on."""
    if not session.has(_ON):
        session.set(_ON, setting(desktop_data_dir(), "assistant") if is_windows_app() else True)
    return bool(session.get(_ON))


def _toggled() -> None:
    session = Session()
    session.set(_ON, bool(session.get("assistant_toggle")))
    if is_windows_app():
        try:
            save_setting(desktop_data_dir(), "assistant", session.get(_ON))
        except OSError:
            pass  # still off (or on) for now; it just isn't remembered


def sidebar_toggle(session: Session) -> None:
    st.toggle("Assistant", value=enabled(session), key="assistant_toggle", on_change=_toggled,
              help="The chat assistant in the corner of every page: answers questions about your matches, "
                   "analyzes players and teams, and shows you around the app.")


# --------------------------------------------------------------- reports --

def reports_folder() -> Path | None:
    """Where reports are kept: the app's data folder, or nowhere on the public website."""
    return None if is_public_host() else desktop_data_dir()


def saved_reports(session: Session) -> list[dict[str, Any]]:
    folder = reports_folder()
    return load_reports(folder) if folder else list(session.get(_SESSION_REPORTS, []))


def _keep(session: Session, report: dict[str, Any]) -> None:
    folder = reports_folder()
    if folder:
        try:
            save_report(folder, report)
            return
        except OSError:
            pass  # kept for this session instead
    session.set(_SESSION_REPORTS, [report] + [r for r in session.get(_SESSION_REPORTS, [])
                                              if r["question"].casefold() != report["question"].casefold()])


def forget_report(session: Session, report_id: str) -> None:
    folder = reports_folder()
    if folder:
        try:
            delete_report(folder, report_id)
        except OSError:
            pass
    session.set(_SESSION_REPORTS, [r for r in session.get(_SESSION_REPORTS, []) if r.get("id") != report_id])


def report_box(session: Session, key: str, *, question: str, answer: str = "", understood: str = "",
               where: str = "Ask", page: str = "", label: str = "Report to developers") -> None:
    """A Report button, which opens a short form; once sent, a link to send it on as a GitHub issue."""
    sent = session.get(f"{key}_sent")
    if sent and sent.get("question") == question:
        st.success("Reported, thanks! It's saved on this computer. Send it to the developers so they can add it:",
                   icon=":material/flag:")
        st.link_button("Send on GitHub", issue_url(sent, GITHUB_REPO), icon=":material/open_in_new:",
                       help="Opens GitHub's new-issue page with your report filled in. You can edit it before you "
                            "send it, and it's public. Your stats aren't included.")
        return
    if session.get(f"{key}_open") != question:
        st.button(label, key=f"{key}_button", icon=":material/flag:", on_click=session.set,
                  args=(f"{key}_open", question), help="Couldn't answer, or answered the wrong way? Tell the "
                                                       "developers so they can add or fix it.")
        return
    with st.form(f"{key}_form", border=True):
        st.caption(f"Reporting: “{md(question)}”")
        comment = st.text_area("What did you expect? (optional)", key=f"{key}_comment", max_chars=500, height=80)
        left, right = st.columns(2)
        save = left.form_submit_button("Save report", type="primary", width="stretch")
        cancel = right.form_submit_button("Cancel", width="stretch")
    if cancel:
        session.pop(f"{key}_open")
        _rerun()
    if save:
        report = new_report(question, answer=answer, understood=understood, where=where, page=page,
                            comment=comment, version=APP_VERSION)
        _keep(session, report)
        session.set(f"{key}_sent", report)
        session.pop(f"{key}_open")
        _rerun()


def _rerun() -> None:
    """Redraw: just the assistant's panel when this is in it, else the page."""
    if _in_fragment():
        st.rerun(scope="fragment")
    st.rerun()


def _in_fragment() -> bool:
    """Whether this run is the assistant's panel redrawn on its own (not the whole page)."""
    try:
        from streamlit.runtime.scriptrunner_utils.script_run_context import get_script_run_ctx
        ctx = get_script_run_ctx()
        return bool(ctx and ctx.fragment_ids_this_run)
    except (ImportError, AttributeError):
        return False


# ------------------------------------------------------------ the replies --

def as_message(reply: Reply) -> dict[str, Any]:
    """A reply as kept in the chat: plain values only, so the Session can hold it."""
    columns, rows, formats = reply.table
    answer = reply.answer
    return {"role": "assistant", "text": reply.text, "kind": reply.kind, "answered": reply.answered,
            "columns": columns, "rows": rows[:12], "more_rows": max(0, len(rows) - 12), "formats": formats,
            "links": reply.links, "suggestions": reply.next_questions(), "question": reply.question,
            "resolved": reply.resolved, "understood": answer.understood if answer else "",
            "notes": list(answer.notes) if answer else [], "plain": reply.kind == "stats",
            "chart": asdict(reply.chart) if reply.chart else None}


def _chart(message: dict[str, Any]) -> Chart | None:
    """The chart a message keeps, rebuilt for drawing."""
    data = message.get("chart")
    if not data:
        return None
    return Chart(**{**data, "spec": ChartSpec(**data["spec"])})


def show_reply(message: dict[str, Any]) -> None:
    """A reply's text, notes and table (the chat's and the Ask page's own replies alike)."""
    if message.get("understood") and message.get("kind") == "stats":
        st.caption(f"🔎 {md(message['understood'])}")
    text = md(message["text"]) if message.get("plain") else message["text"]
    if message.get("answered", True) or message.get("kind") != "fallback":
        st.markdown(text)
    else:
        st.info(text, icon=":material/lightbulb:")
    for note in message.get("notes", []):
        st.caption(md(note))
    if chart := _chart(message):
        draw(chart, height=260)
    if message.get("rows"):
        config = {c: st.column_config.NumberColumn(format=_FORMATS[k]) for c, k in message.get("formats", {}).items()
                  if k in _FORMATS}
        st.dataframe(message["rows"], column_order=message.get("columns") or None, column_config=config,
                     hide_index=True)
        if message.get("more_rows"):
            st.caption(f"…and {message['more_rows']} more. Open it on Ask for the whole table.")
    for page, label in message.get("links", []):
        if page in PAGES and not (page == "charts" and message.get("kind") == "chart"):
            st.page_link(page=PAGES[page], label=label, icon=_ICONS.get(page, ":material/arrow_forward:"))


# ------------------------------------------------------------- the panel --

def render_assistant(session: Session, page_title: str | None) -> None:
    """The assistant's floating panel, when it's on."""
    if enabled(session):
        _panel(PAGE_KEYS.get(page_title or ""))


def _ask(question: str) -> None:
    Session().set(_PENDING, question)


def _open_on_ask(question: str) -> None:
    session = Session()
    session.set("ask_question", question)
    session.set("ask_last", question)
    session.set("assistant_go_ask", True)


def _open_on_charts(question: str) -> None:
    session = Session()
    session.set("chart_from_elsewhere", question)
    session.set("assistant_go_charts", True)


def _clear() -> None:
    session = Session()
    session.set(_CHAT, [])
    session.set(_MEMORY, {})


@st.fragment
def _panel(page: str | None) -> None:
    session = Session()
    if session.pop("assistant_go_ask", False):
        switch_to("ask")
    if session.pop("assistant_go_charts", False):
        switch_to("charts")
    with st.container(key="r6_assistant"):
        if not session.get(_OPEN, False):
            st.button("Assistant", key="assistant_launch", icon=":material/forum:", on_click=session.set,
                      args=(_OPEN, True), help="Open the assistant")
            return
        title, clear, close = st.columns([6, 1, 1], vertical_alignment="center", gap="small")
        title.markdown("**Assistant** · <span class='r6-assistant-sub'>your stats, your coach, your guide</span>",
                       unsafe_allow_html=True)
        clear.button("", key="assistant_clear", icon=":material/delete_sweep:", on_click=_clear,
                     help="Start a new conversation", type="tertiary")
        close.button("", key="assistant_close", icon=":material/close:", on_click=session.set, args=(_OPEN, False),
                     help="Close (the conversation is kept)", type="tertiary")
        chat: list[dict[str, Any]] = session.setdefault(_CHAT, [])
        log = st.container(height=380, key="r6_assistant_log", border=False)
        with log:
            if not chat:
                with st.chat_message("assistant", avatar=":material/smart_toy:"):
                    st.markdown("Hi! I can answer anything about your matches, analyze a player or your team, "
                                "explain a stat or show you around the app. What would you like to know?")
            for i, message in enumerate(chat):
                _message(session, i, message, page, last=i == len(chat) - 1)
        offered = starters(page) if not chat else []
        if offered:
            for j, question in enumerate(offered):
                st.button(question, key=f"assistant_start_{j}", on_click=_ask, args=(question,), width="stretch",
                          type="secondary")
        typed = st.chat_input("Ask me anything…", key="assistant_input", max_chars=500)
        question = typed or session.pop(_PENDING, None)
        if question:
            chat.append({"role": "user", "text": question})
            with log:
                with st.chat_message("user", avatar=":material/person:"):
                    st.markdown(md(question))
                with st.chat_message("assistant", avatar=":material/smart_toy:"):
                    with st.spinner("Thinking…"):
                        message = _reply(session, question, page)
            chat.append(message)
            del chat[:-MAX_MESSAGES]
            _rerun()


def _reply(session: Session, question: str, page: str | None) -> dict[str, Any]:
    try:
        with session.stats() as db:
            reply = Conversation(session.setdefault(_MEMORY, {})).reply(question, db, page=page)
        return as_message(reply)
    except Exception:  # noqa: BLE001 - the assistant never takes the page down with it
        return {"role": "assistant", "text": "Something went wrong working that out. Try asking another way, or "
                                             "report it so the developers can fix it.",
                "kind": "fallback", "answered": False, "question": question}


def _message(session: Session, i: int, message: dict[str, Any], page: str | None, last: bool) -> None:
    if message["role"] == "user":
        with st.chat_message("user", avatar=":material/person:"):
            st.markdown(md(message["text"]))
        return
    with st.chat_message("assistant", avatar=":material/smart_toy:"):
        show_reply(message)
        question = message.get("question", "")
        if not question:
            return
        if message.get("kind") == "chart":
            st.button("Change it on Charts", key=f"assistant_chart_{i}", icon=":material/tune:", type="tertiary",
                      on_click=_open_on_charts, args=(question,))
        if message.get("kind") == "stats":
            st.button("Open on Ask", key=f"assistant_ask_{i}", icon=":material/open_in_full:", type="tertiary",
                      on_click=_open_on_ask, args=(message.get("resolved") or question,))
        if not message.get("answered", True):
            report_box(session, f"assistant_report_{i}", question=question, answer=message.get("text", ""),
                       understood=message.get("understood", ""), where="Assistant", page=page or "")
        elif last:
            report_box(session, f"assistant_report_{i}", question=question, answer=message.get("text", ""),
                       understood=message.get("understood", ""), where="Assistant", page=page or "",
                       label="Not what I asked")
        if last and message.get("suggestions"):
            for j, follow in enumerate(message["suggestions"][:3]):
                st.button(follow, key=f"assistant_next_{i}_{j}", on_click=_ask, args=(follow,), type="secondary")
