"""Read-only companion panels using the refactor's shared Session."""
import streamlit as st

from matchday_data import panel_html


def render_panel(session):
    with session.stats() as db:
        teams = db.rosters()
        team = session.team_choice if session.team_choice in teams else session.competition["team"]
        roster = teams.get(team) or next(iter(teams.values()), [])
        recent = db.team_matches(roster)[:5] if roster else db.match_list(db.me(), limit=5)
    st.markdown(panel_html(session.matchday, recent, roster, session.demo), unsafe_allow_html=True)
    st.page_link("matchday.py", label="Manage match-day workspace", icon=":material/event:")
