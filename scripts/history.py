"""Match History: the matches you've added to your stats, newest first, with your result in each,
the players tracked in it and what you wrote about it (a tag, a note, a video link); and the matches
an earlier version added on its own, to check."""

from __future__ import annotations

import streamlit as st

from ask_engine import named_team
from metrics_engine import eps_from_rating, round_eps, rows_csv
from recap import headline, recap, sittings
from session import Session
from sources import match_label
from stats_db import MAX_NOTE, MAX_VOD, NOTE_TAGS, BadNote, NotAdded, nice_time
from tab import Tab
from ui import md


def _note(kind: str, text: str) -> None:
    Session().leave_note("history", kind, text)


def _keep(match_id: str) -> None:
    players = [p for team in (0, 1) for p in Session().get(f"check:{match_id}:{team}", [])]
    try:
        with Session().stats() as db:
            db.confirm_checked(match_id, players)
    except NotAdded as e:
        _note("error", str(e))
        return
    _note("success", f"Added, with {len(players)} player{'s' if len(players) != 1 else ''}: {', '.join(players)}.")


def _remove(match_id: str, label: str) -> None:
    with Session().stats() as db:
        db.remove_match(match_id)
    _note("success", f"Removed {label} from your stats.")


def _save_note(match_id: str, label: str) -> None:
    session = Session()
    try:
        with session.stats() as db:
            db.save_note(match_id, session.get(f"note_tag_{match_id}") or "", session.get(f"note_text_{match_id}", ""),
                         session.get(f"note_vod_{match_id}", ""))
    except BadNote as e:
        _note("error", str(e))
        return
    _note("success", f"Saved your note on {label}.")


def _opponent(r: dict) -> str:
    """Who a match was against: the other team, when you played and it has a name of its own (not Ranked's
    "ENEMY TEAM"); both teams when you didn't."""
    names = [r.get("team0"), r.get("team1")]
    if r.get("player"):
        other = names[1 - (r.get("team") or 0)]
        return other if named_team(other) else ""
    return " vs ".join(names) if all(named_team(n) for n in names) else ""


def _new_filter() -> None:
    Session().pop("history_rows")  # a row picked in the old list would be another match in the new one


def _keep_recorders() -> None:
    """Every match waiting to be checked, with just the player who recorded it: that's you, and
    your own numbers from your own replay are always right."""
    with Session().stats() as db:
        kept, left = db.keep_recorders()
    _note("success", f"Kept your own stats in {kept} match{'es' if kept != 1 else ''}."
          + (f" {left} didn't have you in them, so they're still waiting." if left else ""))


def _remove_all_waiting() -> None:
    with Session().stats() as db:
        waiting = db.to_check()
        db.remove_match(*[row["match_id"] for row in waiting])
    _note("success", f"Removed {len(waiting)} match{'es' if len(waiting) != 1 else ''}.")


def check_earlier(waiting: list[dict]) -> None:
    """The matches an earlier version added on its own, one at a time: keep the players you track
    in it, or remove it."""
    n = len(waiting)
    st.warning(f"**{n} match{'es' if n != 1 else ''} added by an earlier version {'don' if n != 1 else 'doesn'}'t "
               "count yet.** The app used to add every replay on its own, with everyone in it, so a stranger on "
               "your team in Ranked counted as your teammate. Check each one and keep only the players you track.",
               icon="🔎")
    with st.expander(f"Check earlier matches ({n})", expanded=True):
        by_id = {r["match_id"]: r for r in waiting}

        def label(match_id: str) -> str:
            row = by_id[match_id]
            return " · ".join(x for x in (match_label(row["source"], row), row["match_type"]) if x)

        pick = st.selectbox("Match", list(by_id), format_func=label, key="check_match")
        row = by_id[pick]
        picked = []
        for team, column in enumerate(st.columns(2)):
            names = [p for p, t in row["players"] if t == team]
            if names:
                picked += column.multiselect(row[f"team{team}"] or f"Team {team + 1}", names,
                                             key=f"check:{pick}:{team}", placeholder="Players to track")
        keep, remove = st.columns(2)
        keep.button("Keep these players", type="primary", on_click=_keep, args=(pick,), width="stretch",
                    disabled=not picked)
        remove.button("Remove this match", on_click=_remove, args=(pick, label(pick)), width="stretch")
        st.caption("Or all at once:")
        mine, everything = st.columns(2)
        mine.button(f"Keep just your own stats in all {n}", on_click=_keep_recorders, width="stretch",
                    help="Keeps only the player who recorded each replay: you. Your own numbers are always right.")
        with everything.popover(f"Remove all {n}", width="stretch"):
            st.write(f"Remove all {n} from your stats? Matches whose replays the game has deleted can't be added "
                     "back.")
            st.button(f"Yes, remove all {n}", on_click=_remove_all_waiting, type="primary")


class HistoryTab(Tab):
    """The matches in your stats, newest first, and the ones an earlier version added to check."""

    title, name = "Match History", "history"

    def render(self) -> None:
        with self.stats() as db:
            me = db.me()
            rows = db.match_list(me)
            tracked = db.players_by_match()
            waiting = db.to_check()
            notes = db.notes()
            tags = db.tags()
            if not rows and not waiting:
                self.has_matches(db)
                return
        if waiting:
            check_earlier(waiting)
        if rows:
            self.summary(rows, me)
            self.matches(rows, tracked, notes, tags)
            self.session_recap(me)

    def summary(self, rows: list[dict], me: str | None) -> None:
        mine = [r for r in rows if r["player"]]
        if mine:
            wins, losses = sum(r["won"] == 1 for r in mine), sum(r["won"] == 0 for r in mine)
            kills, deaths = sum(r["kills"] for r in mine), sum(r["deaths"] for r in mine)
            rounds = sum(r["player_rounds"] for r in mine)
            columns = st.columns(4)
            columns[0].metric("Matches", len(mine))
            columns[1].metric("Record", f"{wins}–{losses}", help="Wins–losses, from your team's side")
            columns[2].metric("K/D", f"{kills / deaths if deaths else kills:.2f}")
            eps = round_eps(100 * sum(r["rating"] * r["player_rounds"] for r in mine) / rounds) if rounds else "—"
            columns[3].metric("EPS", eps, help="Your average EPS, weighted by rounds played")
        st.caption((f"Your matches as **{md(me)}**, newest first. " if mine else "")
                   + "**Tracked** is who you picked when you added the match: only their stats are kept. "
                     "Select a match to open it on the Dashboard, tag it (scrim, official...), write a note or "
                     "link its video, or remove it.")

    def session_recap(self, me: str | None) -> None:
        """The matches played in one sitting: the newest session, or an earlier one picked."""
        with self.stats() as db:
            found = sittings(db)
            if not found:
                return
            # picked by position (newest first): two undated matches have the same label
            if self.session.get("history_sitting") not in range(len(found)):
                self.session.set("history_sitting", 0)  # the newest
            with st.expander("Session recap", icon=":material/nights_stay:", expanded=True):
                picked = st.selectbox("Session", range(len(found)), key="history_sitting",
                                      format_func=lambda i: found[i].label, label_visibility="collapsed",
                                      help="Matches with less than 4 hours between them are one session.")
                summary = recap(db, found[picked], me)
                st.markdown(f"**{md(headline(summary))}**")
                games, people = st.columns([2, 3])
                games.dataframe(summary.matches, hide_index=True)
                people.dataframe(summary.players, hide_index=True, column_config={
                    "K/D": st.column_config.NumberColumn(format="%.2f"),
                    "KOST %": st.column_config.NumberColumn(format="%d%%")})

    def matches(self, rows: list[dict], tracked: dict[str, list[str]], notes: dict[str, dict],
                tags: list[str]) -> None:
        if tags:  # once a match is tagged: just the scrims, say
            if self.session.get("history_tag") not in ("All matches", *tags, "Not tagged"):
                self.session.pop("history_tag")  # its last match untagged since
            show = st.segmented_control("Show", ["All matches", *tags, "Not tagged"], default="All matches",
                                        key="history_tag", on_change=_new_filter)
            if show == "Not tagged":
                rows = [r for r in rows if not notes.get(r["match_id"], {}).get("tag")]
            elif show in tags:
                rows = [r for r in rows if notes.get(r["match_id"], {}).get("tag") == show]
        table = []
        for r in rows:
            row = {"When": nice_time(r["played_at"]), "Map": r["map"] or "", "Type": r["match_type"] or "",
                   "Opponent": _opponent(r)}
            if r["player"]:
                own, other = (r["score0"], r["score1"]) if r["team"] == 0 else (r["score1"], r["score0"])
                row |= {"Result": {1: "Win", 0: "Loss"}.get(r["won"], "Draw"), "Score": f"{own}–{other}",
                        "K-D": f"{r['kills']}-{r['deaths']}", "EPS": eps_from_rating(r["rating"])}
            else:
                row |= {"Result": "—", "Score": f"{r['score0']}–{r['score1']}", "K-D": "", "EPS": None}
            row["Tracked"] = ", ".join(tracked.get(r["match_id"], []))
            note = notes.get(r["match_id"], {})
            row |= {"Tag": note.get("tag", ""), "Note": note.get("note", ""), "VOD": note.get("vod") or None}
            table.append(row)
        if not table:
            st.info("No matches here. Pick **All matches** above.")
            return
        picked = st.dataframe(table, hide_index=True, on_select="rerun", selection_mode="single-row",
                              key="history_rows", column_config={
                                  "Note": st.column_config.TextColumn("Note", width="medium"),
                                  "VOD": st.column_config.LinkColumn("VOD", display_text="Watch")})
        chosen = picked.selection.rows if picked else []
        if chosen and chosen[0] < len(rows):
            self.selected(rows[chosen[0]], notes.get(rows[chosen[0]]["match_id"], {}))
        st.download_button("⬇ CSV", rows_csv(table).encode("utf-8"), file_name="match_history.csv", mime="text/csv")

    def selected(self, r: dict, note: dict) -> None:
        """What you can do with the match picked in the table: open it, write about it, or remove it."""
        name, label = r["source"], match_label(r["source"], r)
        source = self.session.replays  # any kind: a folder, uploads, the demo season
        on_hand = bool(source and source.has(name))
        opened, removed = st.columns([1, 1])
        if on_hand:
            if opened.button("Open on Dashboard", type="primary"):
                self.open_on_dashboard(name)
        else:
            opened.caption("This match's replay isn't in your replay folder anymore (the game deletes old ones), "
                           "but its stats are still here and on **Ask**.")
        with removed.popover("Remove from your stats"):
            st.write(f"Remove {md(label)} from your stats?"
                     + (" Its replay is still in your folder, so you can add it again from the Dashboard."
                        if on_hand and source.adds_to_stats else " Its replay is gone, so it can't be added back."
                        if not on_hand else ""))
            st.button("Yes, remove it", on_click=_remove, args=(r["match_id"], label), type="primary")
        self.note_form(r["match_id"], label, note)

    def note_form(self, match_id: str, label: str, note: dict) -> None:
        """A match's tag, note and video link: they stay with it, and Ask understands the tags."""
        with st.form(f"note_form_{match_id}"):
            st.markdown(f"**Your notes on {md(label)}**")
            tag, vod = st.columns([1, 2])
            tag.selectbox("Tag", ["", *NOTE_TAGS], index=([""] + list(NOTE_TAGS)).index(note.get("tag", "")),
                          format_func=lambda t: t or "No tag", key=f"note_tag_{match_id}")
            vod.text_input("Video link", value=note.get("vod", ""), max_chars=MAX_VOD, key=f"note_vod_{match_id}",
                           placeholder="https://youtu.be/...")
            st.text_area("Note", value=note.get("note", ""), max_chars=MAX_NOTE, key=f"note_text_{match_id}",
                         placeholder="What worked, what to fix, who subbed in...")
            st.form_submit_button("Save note", on_click=_save_note, args=(match_id, label))
        st.caption("Tags work on **Ask** too: \"my K/D in scrims\", \"win rate in official matches\".")


HistoryTab().run()
