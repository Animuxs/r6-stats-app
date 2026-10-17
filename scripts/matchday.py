"""Match-day planning with explicit manual data and isolated demo storage."""
import json
from datetime import date, datetime, time
from uuid import uuid4

import streamlit as st

from matchday_data import AVAILABILITY, MatchdayWorkspace
from tab import Tab


class MatchdayTab(Tab):
    title, name = "Match-day workspace", "matchday"

    def render(self):
        st.caption("Plan upcoming matches, confirm your roster and keep preparation notes. No schedule or availability is inferred from replays.")
        if self.session.demo:
            st.info("Demo workspace: example fixtures and availability, separate from your real planning data.")
        st.toggle("Show match-day panels", value=self.session.get("show_matchday", True),
                  key="matchday_panel_toggle", on_change=lambda: self.session.set("show_matchday", self.session.get("matchday_panel_toggle")))
        workspace = self.session.matchday
        with st.form("add_fixture"):
            st.subheader("Add an upcoming match")
            opponent = st.text_input("Opponent", max_chars=180)
            team = st.text_input("Your team", value=self.session.competition["team"], max_chars=180)
            left, right = st.columns(2)
            day = left.date_input("Match date", value=date.today())
            start = right.time_input("Start time", value=time(19, 0))
            timezone = st.text_input("Time zone label", value="Central Time", max_chars=180)
            kind = st.selectbox("Match type", ["League · BO3", "League · BO1", "Practice · BO3", "Tournament", "Other"])
            add = st.form_submit_button("Save fixture", type="primary")
        if add:
            if not opponent.strip():
                st.error("Enter an opponent before saving.")
            else:
                workspace.fixtures.append(dict(id=uuid4().hex, opponent=opponent.strip(), team=team.strip(),
                                               when=datetime.combine(day, start).isoformat(), timezone=timezone, kind=kind))
                self.session.save_matchday(workspace)
                self.note("success", "Fixture saved.")
                st.rerun()
        if workspace.fixtures:
            choices = {f["id"]: f for f in workspace.fixtures}
            selected = st.selectbox("Saved fixture", list(choices),
                                    format_func=lambda key: f'{choices[key]["when"][:16]} · {choices[key]["opponent"]}')
            if st.button("Remove fixture"):
                workspace.fixtures = [f for f in workspace.fixtures if f["id"] != selected]
                self.session.save_matchday(workspace)
                st.rerun()
        with self.stats() as db:
            teams = db.rosters()
        st.subheader("Roster availability & preparation")
        with st.form("matchday_notes"):
            roster = list(dict.fromkeys(p for players in teams.values() for p in players))
            selections = {p: st.selectbox(p, AVAILABILITY, index=AVAILABILITY.index(workspace.availability.get(p, "Unconfirmed")),
                                         key=f"availability:{p}") for p in roster}
            notes = st.text_area("Preparation notes", value=workspace.notes, max_chars=10000)
            save = st.form_submit_button("Save availability and notes", type="primary")
        if save:
            workspace.availability.update(selections)
            workspace.notes = notes
            self.session.save_matchday(workspace)
            self.note("success", "Match-day notes and availability saved.")
            st.rerun()
        st.download_button("Export match-day JSON", json.dumps(workspace.to_dict(), indent=2),
                           file_name="R6MatchStats-Matchday.json", mime="application/json")
        uploaded = st.file_uploader("Restore match-day JSON", type=["json"])
        if uploaded and st.button("Restore planning data"):
            try:
                if uploaded.size > 1000000:
                    raise ValueError("Choose a match-day backup under 1 MB.")
                restored = MatchdayWorkspace.from_dict(json.loads(uploaded.getvalue()))
                self.session.save_matchday(restored)
                self.note("success", "Match-day planning data restored.")
                st.rerun()
            except (ValueError, OSError) as exc:
                st.error(str(exc))
        st.caption("The Windows app saves planning data on this computer. The hosted app keeps it in your private browser session; export a backup before leaving.")


MatchdayTab().run()
