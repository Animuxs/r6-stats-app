"""
app.py
======
Entry point of the R6 Match Stats website and Windows app (Streamlit).
Pages: report.py (Dashboard: one match's scoreboards), ask.py (plain-English questions about
every match), history.py (every match), teams.py (build a team; season teams), operators.py,
league.py (your league or tournament: Liquipedia events, NECC divisions, or your own; NECC's school
directory), appearance.py (team colors and logo) and download.py (get the Windows app). The sidebar's
demo season (or ?demo in the address) fills every page with made-up matches (demo_season.py).

Run with:  streamlit run scripts/app.py   (from the repo root)
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

from app_info import APP_NAME, can_dock, quiet_windows_connection_resets, send_window_command
from branding import current_competition, render_identity
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

st.set_page_config(page_title=APP_NAME, page_icon=str(Path(__file__).with_name("icon.png")), layout="wide")

# ---------------------------------------------------------------- styling --
st.markdown("""
<style>
:root { --sans:'IBM Plex Sans','Segoe UI',system-ui,sans-serif;
        --display:'Barlow Condensed','Segoe UI Semibold','Arial Narrow',sans-serif;
        --mono:'IBM Plex Mono','Cascadia Mono',Consolas,ui-monospace,monospace;
        --bg:#101416; --panel:#181e20; --row:#202729; --border:#333d3d; --accent:#d49353;
        --mint:#83a99c; --dim:#9ba7a4; --text:#e8ece9; --pos:#86b99f; --neg:#e17e69;
        --atk:#e8924a; --def:#6aa4dd; }
.stApp { background:linear-gradient(122deg, #101416 0%, #171d1f 58%, #14191a 100%); color:var(--text); }
.stApp, [data-testid="stMarkdownContainer"] p, [data-testid="stWidgetLabel"] p { font-family:var(--sans); }
.stMainBlockContainer { max-width:1480px; padding-top:1.2rem; padding-bottom:4rem; }
header[data-testid="stHeader"] { background:rgba(16,20,22,.84); }
section[data-testid="stSidebar"] { background:#161c1e; border-right:1px solid var(--border); }
h1, h2, h3, h4 { color:var(--text) !important; font-family:var(--display) !important; font-weight:600 !important; letter-spacing:0 !important; }
h1 { font-size:2.2rem !important; }
[data-testid="stMetric"] { padding:12px 14px; background:rgba(24,30,32,.72); border:1px solid var(--border); border-radius:4px; }
[data-testid="stMetricLabel"] { color:var(--dim) !important; }
[data-testid="stMetricValue"] { font-family:var(--mono); }
[data-testid="stTabs"] button[role="tab"] { font-family:var(--display); font-size:1.05rem; text-transform:uppercase; }
[data-testid="stTabs"] button[aria-selected="true"] { color:var(--accent); }
/* form buttons (Ask, Show team stats) in the school theme's accent, like its primary buttons (branding.py) */
button[kind="primaryFormSubmit"], button[kind="primaryFormSubmit"]:hover { background:var(--accent); border-color:var(--accent); color:#080e19; }
.scorecard { background:linear-gradient(112deg,#202729,#171d1f); border:1px solid var(--border); border-left:3px solid var(--accent);
             border-radius:4px; padding:18px 24px; margin-bottom:18px; display:flex; align-items:center;
             justify-content:space-between; gap:16px; flex-wrap:wrap; box-shadow:0 12px 28px rgba(0,0,0,.18); }
.team-name { font-size:1.05rem; color:var(--text); font-weight:700; }
.score-big { font:600 2.35rem var(--mono); color:var(--accent); }
.map-pill { display:inline-block; background:#252d2e; color:var(--dim); border-radius:2px;
            padding:4px 10px; font:10px var(--mono); text-transform:uppercase; border:1px solid var(--border); }
.pl-wrap { overflow-x:auto; margin-bottom:22px; border:1px solid var(--border); border-radius:4px; }
table.pl { width:100%; border-collapse:collapse; font-size:0.84rem; color:var(--text); font-variant-numeric:tabular-nums; }
table.pl caption { caption-side:top; text-align:left; padding:10px 14px; font-weight:700;
                   font-size:1rem; background:var(--panel); color:var(--text); }
table.pl .won { color:var(--accent); margin-left:8px; font:10px var(--mono); }
table.pl th { background:#1b2224; color:var(--dim); font:500 10px var(--mono); text-align:center;
              padding:9px 8px; white-space:nowrap; border-top:1px solid var(--border); }
table.pl td { padding:9px 8px; text-align:center; white-space:nowrap; border-top:1px solid #303839; }
table.pl tr:nth-child(even) td { background:rgba(32,39,41,.72); }
table.pl th:first-child, table.pl td:first-child { text-align:left; font-weight:600; }
table.pl td.eps { color:var(--accent); font-weight:800; }
.pos { color:var(--pos); } .neg { color:var(--neg); }
table.pl.rounds td.left { text-align:left; }
.side { display:inline-flex; align-items:center; gap:6px; padding:2px 8px; border:1px solid; border-radius:2px;
        font:600 10px var(--mono); text-transform:uppercase; letter-spacing:.06em; white-space:nowrap; }
.side .ico { font-size:17px; line-height:1; margin:-3px 0; }
.side.attack { color:var(--atk); border-color:rgba(232,146,74,.45); background:rgba(232,146,74,.1); }
.side.defense { color:var(--def); border-color:rgba(106,164,221,.45); background:rgba(106,164,221,.1); }
.side.unknown { color:var(--dim); border-color:var(--border); }
.win-tag { margin-left:8px; padding:1px 6px; border:1px solid var(--accent); border-radius:2px; color:var(--accent);
           font:700 9px var(--mono); letter-spacing:.08em; vertical-align:1px; }
table.pl.rounds td.score { font:600 .9rem var(--mono); color:var(--text); }
table.pl.rounds td.score span { color:var(--dim); margin:0 3px; }
table.pl.rounds td.op { font-weight:600; }
table.pl.rounds td.kda { font-family:var(--mono); }
table.pl.rounds tr.missed td { color:var(--dim); font-style:italic; }
table.pl caption .cap-sub { margin-left:10px; color:var(--dim); font:500 11px var(--mono); text-transform:uppercase; letter-spacing:.06em; }
.dot { display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:8px; vertical-align:1px; }
.dot.alive { background:var(--pos); box-shadow:0 0 6px rgba(134,185,159,.55); }
.dot.dead { background:var(--neg); }
.chip { display:inline-block; margin:1px 6px 1px 0; padding:2px 8px; border-radius:10px; border:1px solid var(--border);
        font-size:.76rem; line-height:1.35; color:var(--text); background:rgba(168,184,202,.07); white-space:nowrap; }
.chip.pos { border-color:rgba(134,185,159,.5); color:var(--pos); background:rgba(134,185,159,.1); }
.chip.neg { border-color:rgba(225,126,105,.5); color:var(--neg); background:rgba(225,126,105,.1); }
.none { color:var(--dim); }
.side-split { display:flex; flex-wrap:wrap; gap:10px; margin:6px 0 14px; }
.side-card { flex:1 1 260px; display:flex; flex-wrap:wrap; align-items:center; gap:6px 12px; padding:10px 14px;
             border:1px solid var(--border); border-left:3px solid var(--atk); border-radius:4px;
             background:rgba(24,30,32,.55); color:var(--dim); font-size:.86rem; }
.side-card.defense { border-left-color:var(--def); }
.side-card b { color:var(--text); font-weight:600; }
.side-card .ops { flex-basis:100%; font-size:.8rem; }
@media(max-width:700px) { .stMainBlockContainer { padding-left:1rem; padding-right:1rem; } h1 { font-size:1.8rem !important; } }
</style>
""", unsafe_allow_html=True)

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
    st.Page("history.py", title="Match History", icon=":material/history:"),
    st.Page("teams.py", title="Team Analytics", icon=":material/groups:"),
    st.Page("operators.py", title="Operator Analytics", icon=":material/target:"),
    st.Page("league.py", title="League & Team", icon=":material/emoji_events:"),
    st.Page("appearance.py", title="Team Theme", icon=":material/palette:"),
    st.Page("download.py", title="Get the Windows app", icon=":material/download:"),
], position="sidebar")
if can_dock():
    with st.sidebar:
        st.caption("Dock the app to the edge of your screen, next to the game or your other apps:")
        left, right = st.columns(2)
        if left.button("⇤ Dock left", width="stretch", key="dock_left"):
            send_window_command("dock", edge="left")
        if right.button("Dock right ⇥", width="stretch", key="dock_right"):
            send_window_command("dock", edge="right")
# the demo season: a made-up season in a stats database of its own, from the sidebar or ?demo in the
# address (a link to share), so every page has something to show and nobody's real stats are touched
if "demo" in st.query_params and not st.session_state.get("demo_from_link"):
    st.session_state["demo_from_link"] = True
    if not demo_active():
        start_demo()
with st.sidebar:
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
if "r6_season" not in st.session_state and current_competition()["season"]:
    st.session_state["r6_season"] = current_competition()["season"]  # the season named after your league
render_identity()
if demo_active():
    st.info("**Demo season**: twelve made-up league matches of Lakeside Varsity, so you can try Ask, Match History, "
            "Team Analytics and the rest without replays. Your own stats aren't touched. Leave it from the sidebar.",
            icon=":material/science:")
page.run()
