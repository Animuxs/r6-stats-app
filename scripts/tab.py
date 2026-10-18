"""
tab.py
======
What every tab of the app has in common. Each page file (report.py, ask.py, ...) is a Tab subclass
that says what's on it by overriding render(), and ends with SomeTab().run(): st.navigation runs
the page files as before, so the navigation, the tests and the Windows app's file list don't change.

A Tab gets the session (session.Session: the season, your team, the replays, the demo season) and
the helpers the tabs share: the stats database, the "no matches yet" state, notes left across a rerun,
and links to the other tabs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import streamlit as st

from session import Session
from sources import no_matches_yet
from stats_db import StatsDB
from ui import md
from team_accounts import AccountError

# each tab's page file, for links between tabs
PAGES = {"dashboard": "report.py", "ask": "ask.py", "charts": "charts.py", "history": "history.py", "teams": "teams.py",
         "operators": "operators.py", "matchday": "matchday.py", "team": "team_hub.py", "league": "league.py", "theme": "appearance.py", "download": "download.py",
         "dock": "dock.py"}


def switch_to(tab: str) -> None:
    """Show another tab (a key of PAGES): the one place the app changes page."""
    st.switch_page(PAGES[tab])


class Tab(ABC):
    """One tab of the app."""

    title: str = ""
    name: str = ""  # its key in PAGES, and where its notes are kept

    def __init__(self, session: Session | None = None):
        self.session = session or Session()

    def run(self) -> None:
        """Draw the tab: its heading, any note a callback left, then what render() puts on it."""
        self.heading()
        self.show_note()
        try:
            self.render()
        except AccountError as error:
            st.error(md(str(error)))
            st.page_link('team_hub.py', label='Open Team hub to refresh or sign in')

    def heading(self) -> None:
        st.title(self.title)

    @abstractmethod
    def render(self) -> None:
        """What's on the tab."""

    # ---------------------------------------------------------- helpers ----
    def stats(self):
        """The session's stats database (a context manager)."""
        return self.session.stats()

    def has_matches(self, db: StatsDB) -> bool:
        """Whether there's anything in the stats database; says where to start when there isn't."""
        if db.summary()["matches"]:
            return True
        no_matches_yet(db)
        return False

    def note(self, kind: str, text: str) -> None:
        """Leave a message for this tab's next run: what a button's callback did."""
        self.session.leave_note(self.name, kind, text)

    def show_note(self) -> None:
        if note := self.session.take_note(self.name):
            getattr(st, note[0])(md(note[1]))

    def go_to(self, tab: str) -> None:
        switch_to(tab)

    def open_on_dashboard(self, match_name: str) -> None:
        self.session.open_on_dashboard(match_name)
        self.go_to("dashboard")
