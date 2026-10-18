"""
app.py
======
Entry point of the R6 Match Stats website and Windows app (Streamlit).
Pages: report.py (Dashboard: one match's scoreboards), ask.py (plain-English questions about
every match), charts.py (any chart of any stat), history.py (every match), teams.py (build a team; season teams), operators.py,
league.py (your league or tournament: Liquipedia events, NECC divisions, or your own; NECC's school
directory), appearance.py (team colors and logo) and download.py (get the Windows app). The assistant
(assistant_ui.py) floats in the corner of every page. The sidebar's
demo season (or ?demo in the address) fills every page with made-up matches (demo_season.py).

Run with:  streamlit run scripts/app.py   (from the repo root)
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

import rosters
from app_info import APP_NAME, can_dock, is_windows_app, quiet_windows_connection_resets, send_window_command
from branding import render_identity
from session import Session
from sources import demo_active, start_demo, stop_demo

if __name__ == "__main__":
    from streamlit import runtime

    if not runtime.exists():
        # started with `python app.py` (e.g. VS Code's Run button) -- relaunch
        # under `streamlit run` from the repo root, where .streamlit/config.toml lives
        import os

        from streamlit.web import cli as stcli

        quiet_windows_connection_resets()
        os.chdir(Path(__file__).resolve().parent.parent)
        sys.argv = ["streamlit", "run", str(Path(__file__).resolve())]
        sys.exit(stcli.main())

# for `streamlit run app.py`, which starts the server before this script runs
quiet_windows_connection_resets()

st.set_page_config(page_title=APP_NAME, page_icon=str(Path(__file__).with_name("icon.png")), layout="wide", initial_sidebar_state="collapsed")

# ---------------------------------------------------------------- styling --
from presentation import render_styles

render_styles()

from team_runtime import current_team, switch_account
from team_accounts import AccountError
try:
    _signed_team = current_team()
except AccountError:
    switch_account()
    _signed_team = None
    st.warning('Your team session ended. Sign in again from Team hub.')

if st.query_params.get("view") == "dock":
    # the Windows app docked to the edge of the screen (desktop/launcher.py): just the compact page
    page = st.navigation([st.Page("dock.py", title="Docked", icon=":material/dock_to_right:", default=True)],
                         position="hidden")
    render_identity(banner=False)
    page.run()
    st.stop()

page = st.navigation([
    st.Page("report.py", title="Dashboard", icon=":material/dashboard:", default=True),
    st.Page("ask.py", title="Ask", icon=":material/chat:"),
    st.Page("charts.py", title="Charts", icon=":material/insights:"),
    st.Page("history.py", title="Match History", icon=":material/history:"),
    st.Page("teams.py", title="Team Analytics", icon=":material/groups:"),
    st.Page("operators.py", title="Operator Analytics", icon=":material/target:"),
    st.Page("matchday.py", title="Match day", icon=":material/event:"),
    st.Page("team_hub.py", title="Team hub", icon=":material/badge:"),
    st.Page("league.py", title="League & Team", icon=":material/emoji_events:"),
    st.Page("appearance.py", title="Team Theme", icon=":material/palette:"),
    st.Page("download.py", title="Get the Windows app", icon=":material/download:"),
], position="top")
if can_dock():
    with st.sidebar:
        st.caption("Dock the app to the edge of your screen, next to the game or your other apps:")
        left, right = st.columns(2)
        if left.button("⇤ Dock left", width="stretch", key="dock_left"):
            send_window_command("dock", edge="left")
        if right.button("Dock right ⇥", width="stretch", key="dock_right"):
            send_window_command("dock", edge="right")
if is_windows_app():
    rosters.update_on_launch()  # newer team rosters, at most once a day, in the background
# the demo season: a made-up season in a stats database of its own, from the sidebar or ?demo in the
# address (a link to share), so every page has something to show and nobody's real stats are touched
if "demo" in st.query_params and not Session().get("demo_from_link"):
    Session().set("demo_from_link", True)
    if not demo_active():
        start_demo()
from assistant_ui import render_assistant, sidebar_toggle

with st.sidebar:
    sidebar_toggle(Session())
    st.page_link('team_hub.py', label='Team hub · ' + (_signed_team['name'] if _signed_team else 'sign in'), icon=':material/badge:')
    if demo_active():
        st.caption("You're looking at the **demo season**: made-up matches, not your own.")
        if st.button("Leave the demo season", width="stretch", key="demo_leave"):
            stop_demo()
            st.query_params.pop("demo", None)
            st.rerun()
    else:
        st.caption("No replays handy? See every page with a season of made-up matches:")
        if st.button("Try the demo season", width="stretch", key="demo_start"):
            start_demo()
            st.rerun()
_initial_season = Session().season  # the first time: the season named after your league on League & Team, if it has one
render_identity()
if _signed_team and not demo_active():
    from ui import md
    _person = next((p for p in _signed_team['directory'] if p['id'] == Session().team_person), None)
    _view = _person['name'] if _person else 'team overview'
    st.caption(md(f"Team workspace: {_signed_team['name']} · Viewing as {_view} · Shared account access"))
if demo_active():
    st.info("**Demo season**: twelve made-up league matches of Lakeside Varsity, so you can try Ask, Match History, "
            "Team Analytics and the rest without replays. Your own stats aren't touched. Leave it from the sidebar.",
            icon=":material/science:")
session = Session()
# the assistant floats over every page, and keeps its conversation when the page changes
render_assistant(session, page.title)
if session.get("show_matchday", True) and page.title != 'Team hub':
    content, companion = st.columns([4, 1.15], gap="large")
    # Draw the companion first: a page can legitimately stop (e.g. demo season tracker).
    with companion:
        from matchday_hub import render_panel
        render_panel(session)
    with content:
        page.run()
else:
    page.run()
