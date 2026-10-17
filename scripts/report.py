"""
report.py
=========
The "Match report" page: load a match's replay and show an R6 Pro League-style
scoreboard for every player, a round-by-round breakdown, and CSV/JSON exports.

It's also the only way a match gets into your stats: looking at a match adds nothing.
You confirm it, picking the players whose stats to keep (and, if you like, save it to
a season), so a stranger who was on your team in Ranked never counts as your teammate.
"""

from __future__ import annotations

import html
import json
import os
from pathlib import Path

import streamlit as st

import rosters
from app_info import APP_NAME, APP_VERSION, NOTICE, is_windows_app
from metrics_engine import (
    PRO_LEAGUE_COLUMNS, SIDE_ICONS, SIDE_NAMES, compute_match_metrics, leaderboard_rows, pro_league_rows,
    round_player_rows, round_rows, rounds_text, rows_csv, running_scores, scoreboard_text, side_split, team_side,
    win_condition_label,
)
from branding import current_theme
from parser import (
    ReplayParseError, find_replay_folders, load_demo_match, r6_dissect_available, raw_shape_preview, save_uploads,
)
from season_stats import GENERIC_TEAM_NAMES, StatsError
from session import Session
from sources import (
    DemoSource, LoadedSource, ReplaySource, can_read_local_files, folder_source, load_source, save_replay_folder,
    saved_replay_folder, start_demo, start_workdir_sweeper,
)
from scoreboard_image import scoreboard_png
from stats_db import NotAdded, nice_time
from tab import Tab
from ui import md

REPO_ROOT = Path(__file__).resolve().parent.parent
REPLAYS_DIR = REPO_ROOT / "replays"

UPLOAD = "Upload"
FOLDER = "Folder or zip on this computer"
REPLAYS_FOLDER = "From the replays/ folder"
# the game makes these teams: someone on yours may be a stranger, not one of your players
MATCHMADE = {"Ranked", "Unranked", "Quick Match"}


def _signed_cell(text: str) -> str:
    """Color the "(+4)" / "(-2)" part of a KD or Entry cell."""
    text = html.escape(text)
    if "(+" in text:
        return text.replace("(+", '<span class="pos">(+').replace(")", ")</span>")
    if "(-" in text:
        return text.replace("(-", '<span class="neg">(-').replace(")", ")</span>")
    return text


def scoreboard_html(team_name: str, won: bool, rows: list[dict]) -> str:
    head = "".join(f"<th>{html.escape(c)}</th>" for c in ("Player",) + PRO_LEAGUE_COLUMNS)
    body = []
    for r in rows:
        cells = [f"<td>{html.escape(r['Player'])}</td>", f'<td class="eps">{r["EPS"]}</td>']
        for c in PRO_LEAGUE_COLUMNS[1:]:
            v = str(r[c])
            cells.append(f"<td>{_signed_cell(v) if c in ('KD (+/-)', 'Entry') else html.escape(v)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    badge = '<span class="won">WIN</span>' if won else ""
    return (f'<div class="pl-wrap"><table class="pl"><caption>{html.escape(team_name)}{badge}</caption>'
            f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>")


@st.cache_data(max_entries=8, show_spinner=False)
def _scoreboard_image(key: tuple, _match: dict, _rows: list[dict], accent: str) -> bytes:
    """The scoreboard picture, drawn once per scoreboard (key: everything it shows) and theme color."""
    return scoreboard_png(_match, _rows, accent, nice_time(_match.get("played_at")))


def side_badge(side: str | None) -> str:
    """"⚔ ATTACK" in orange or "♜ DEFENSE" in blue, R6 broadcast colors."""
    if side not in SIDE_ICONS:
        return '<span class="side unknown" title="The replay doesn\'t say which side">—</span>'
    return (f'<span class="side {side}" title="{SIDE_NAMES[side]}"><span class="ico" aria-hidden="true">'
            f'{SIDE_ICONS[side]}</span>{SIDE_NAMES[side]}</span>')


def rounds_html(match: dict) -> str:
    """Every round of the match: the side each team played, who won, the score after it, how it
    was won and the site."""
    names = match["team_names"][:2]
    head = "".join(f"<th>{html.escape(c)}</th>" for c in ("Round", *names, "Score", "How it was won", "Site"))
    body = []
    for number, (rnd, score) in enumerate(zip(match["rounds"], running_scores(match)), 1):
        cells = [f"<td>{number}</td>"]
        for team in range(len(names)):
            won = '<span class="win-tag">WIN</span>' if rnd.get("winner_team") == team else ""
            cells.append(f"<td>{side_badge(team_side(team, rnd.get('attack_team')))}{won}</td>")
        cells.append(f'<td class="score">{score[0]}<span>–</span>{score[1]}</td>')
        how = win_condition_label(rnd.get("win_condition")) or "—"
        cells.append(f"<td>{html.escape(how)}</td>")
        cells.append(f'<td class="left">{html.escape(rnd.get("site") or "—")}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (f'<div class="pl-wrap"><table class="pl rounds"><caption>Rounds</caption>'
            f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>")


def _chips(rb) -> str:
    chips = "".join(f'<span class="chip {tone}">{html.escape(label)}</span>' for label, tone in rb.highlights())
    return chips or '<span class="none">—</span>'


def player_rounds_html(player: str, team: str, match: dict, breakdown: list) -> str:
    """One player's rounds, every round of the match in order: side, operator, whether their
    team won, K-D-A (with a dot for survived or died) and what stood out. Rounds the player
    wasn't in (they left or joined late) say so."""
    by_round = {rb.round_num: rb for rb in breakdown}
    head = "".join(f"<th>{c}</th>" for c in ("Round", "Side", "Operator", "Result", "K-D-A", "Highlights"))
    body = []
    for number, rnd in enumerate(match["rounds"], 1):
        rb = by_round.get(rnd["round_num"])
        if rb is None:
            body.append(f'<tr class="missed"><td>{number}</td><td colspan="5" class="left">Didn\'t play this round</td></tr>')
            continue
        result = {True: '<span class="pos">Won</span>', False: '<span class="neg">Lost</span>'}.get(rb.won, "—")
        alive = ('<span class="dot alive" title="Survived"></span>' if rb.survived
                 else '<span class="dot dead" title="Died"></span>')
        body.append(
            f"<tr><td>{number}</td><td>{side_badge(rb.side)}</td>"
            f'<td class="op">{html.escape(rb.operator or "—")}</td><td>{result}</td>'
            f'<td class="kda">{alive}{rb.kills}-{rb.deaths}-{rb.assists}</td>'
            f'<td class="left">{_chips(rb)}</td></tr>'
        )
    return (f'<div class="pl-wrap"><table class="pl rounds"><caption>{html.escape(player)}'
            f'<span class="cap-sub">{html.escape(team)}</span></caption>'
            f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>")


def side_split_html(breakdown: list) -> str:
    """"⚔ ATTACK 6 rounds · 5 won · 9-2 K-D · Ash ×6" for one player, then the same for defense."""
    parts = []
    for side, s in side_split(breakdown).items():
        ops = " · ".join(f"{html.escape(op)} <b>×{n}</b>" for op, n in list(s["operators"].items())[:3])
        parts.append(
            f'<div class="side-card {side}">{side_badge(side)}'
            f'<div><b>{s["rounds"]}</b> round{"s" if s["rounds"] != 1 else ""} · <b>{s["won"]}</b> won · '
            f'<b>{s["kills"]}-{s["deaths"]}</b> K-D</div>'
            + (f'<div class="ops">{ops}</div>' if ops else "") + "</div>"
        )
    return f'<div class="side-split">{"".join(parts)}</div>' if parts else ""


def _picked_key(match: dict, team: int) -> str:
    return f"confirm_players:{match['match_id']}:{team}"


def _team_name_key(match: dict, team: int) -> str:
    return f"confirm_team_name:{match['match_id']}:{team}"


def _season_key(match: dict) -> str:
    return f"confirm_season:{match['match_id']}"


def my_team() -> tuple[str, dict[str, str], dict | None]:
    """Your team as saved on League & Team: its name, its players ({any name they go by,
    casefolded: the name tracked for them}) and its NECC roster entry, if it has one."""
    session = Session()
    competition = session.competition
    team = competition["team"]
    if not team:
        return "", {}, None
    necc = rosters.mine(competition)
    players = rosters.aliases(necc) if necc else {}
    with session.stats() as db:  # the Team Analytics team made when it was saved (and any players added since)
        saved = next((names for name, names in db.rosters().items() if name.casefold() == team.casefold()), [])
    for key, name in rosters.roster_names(saved).items():
        players.setdefault(key, name)
    return team, players, necc


def name_sides(match: dict, team: str, players: dict[str, str], necc: dict | None) -> tuple[dict, dict]:
    """The match with its sides named after the teams playing, where the replay only says "YOUR
    TEAM" and "ENEMY TEAM": your team where your players are, and any NECC team whose roster is on
    a side. Returns it and {"mine": your side or None, "logos": {side: logo URL}}."""
    names = list(match.get("team_names") or ["", ""]) + ["", ""]
    sides: dict[int, list[str]] = {0: [], 1: []}
    for p in match.get("players", []):
        if p.get("team") in sides:
            sides[p["team"]].append(p["name"])
    counts = {side: len(rosters.on_roster(n, players)) for side, n in sides.items()}
    mine = max(counts, key=counts.get) if players and max(counts.values()) >= rosters.MIN_SHARED else None
    if mine is not None and counts[1 - mine] == counts[mine]:
        mine = None  # a scrim among your own players: neither side is "yours"
    found = rosters.identify(match)
    if mine is not None:
        found[mine] = necc or {"name": team, "logo_url": ""}
    logos = {}
    for side in (0, 1):
        if side in found:
            logos[side] = found[side].get("logo_url", "")
            if names[side].strip().upper() in GENERIC_TEAM_NAMES:
                names[side] = found[side]["name"]
    return dict(match, team_names=names[:2]), {"mine": mine, "logos": logos, "found": found}


def _add_match(source: str, match: dict, files: int) -> None:
    """Add the match to the stats database with the players picked for it and, when asked, save
    those same players' rounds to the season. Runs as the confirm button's callback."""
    session = Session()
    picked = {team: list(session.get(_picked_key(match, team), [])) for team in (0, 1)}
    players = picked[0] + picked[1]
    try:
        with session.stats() as db:
            db.import_match(source, match, files=files, players=players)
    except NotAdded as e:
        session.leave_note("dashboard", "error", str(e))
        return
    notes = [f"Added to your stats, tracking {len(players)} player{'s' if len(players) != 1 else ''}: "
             f"{', '.join(players)}."]
    kind = "success"
    if session.get(_season_key(match)) and not session.tracker_off:
        session.season = session.get("r6_season_input") or ""
        season = session.season
        with session.tracker(season) as tracker:
            tracked = {u.casefold() for u in tracker.tracked_players()}
            for team in (0, 1):
                team_name = (session.get(_team_name_key(match, team)) or "").strip()
                if team_name.upper() in GENERIC_TEAM_NAMES:
                    team_name = ""  # never save a replay's generic label as a team: it'd lump opponents together
                for player in picked[team]:
                    if team_name or player.casefold() not in tracked:  # a blank name keeps the team they have
                        tracker.add_player(player, team_name or None)
            try:
                result = tracker.log_match(match, players=players)
            except StatsError as e:
                notes.append(f"It couldn't be saved to {season}: {e}")
                kind = "warning"
            else:
                notes.append(f"Saved {result.rounds_logged} player-rounds to {season}." if result.rounds_logged else
                             f"Its rounds for these players were already saved to {season}.")
                notes += result.warnings
    session.leave_note("dashboard", kind, " ".join(notes))


def confirm_match(source: str, match: dict, files: int, team: str = "", roster: dict[str, str] | None = None,
                  mine: int | None = None) -> None:
    """The step that adds a match to your stats: a person checks the match and picks exactly whose
    stats to keep. Your team's players (saved on League & Team) are picked for you; nobody else is."""
    session = Session()
    team_names = match["team_names"]
    roster = roster or {}
    st.subheader("Add to your stats")
    if note := session.take_note("dashboard"):
        getattr(st, note[0])(md(note[1]))
    with session.stats() as db:
        added = db.added(source)
    if added:
        st.caption(f"✓ In your stats, tracking **{md(', '.join(added['players']))}**. Change who's picked and "
                   "confirm again to update it, or remove it on **Match History**.")
        if added["files"] < files:
            st.info("The replay has more rounds than when you added it: confirm again to add them.")
    else:
        st.caption("Not in your stats yet: matches are never added on their own. Pick exactly whose stats to keep "
                   "from this match, then confirm. Only they count on Ask, Match History and Team Analytics.")
    if match.get("match_type") in MATCHMADE:
        st.warning(f"This is a **{match['match_type']}** match: the game made the teams, so someone on your team may "
                   "not be one of your players. Pick only the players you track.", icon="⚠️")
    picked, from_roster = {}, []
    for side, column in enumerate(st.columns(2)):
        names = [p["name"] for p in match.get("players", []) if p.get("team") == side]
        key = _picked_key(match, side)
        # what's in the database for this match, else your team's players: never a stranger
        on_roster = rosters.on_roster(names, roster)
        if not session.has(key):
            session.set(key, [n for n in (added or {}).get("players", []) if n in names] if added else on_roster)
            from_roster += [] if added else on_roster
        picked[side] = column.multiselect(team_names[side], names, key=key, placeholder="Players to track")
    if from_roster:
        st.caption(f"Picked from **{md(team)}**'s roster: {md(', '.join(from_roster))}. Check them, then confirm.")
    elif not team and not added:
        st.caption("Tip: pick your team on **League & Team** and its players are picked here for you.")

    # a custom game with your team in it (a scrim or a league match) is saved to the season too, unless you say not
    ours = mine is not None and len(rosters.on_roster(
        [p["name"] for p in match.get("players", []) if p.get("team") == mine], roster)) >= 3
    off = session.tracker_off
    session.setdefault(_season_key(match), bool(ours and not added and not off
                                                and match.get("match_type") == "Custom game"))
    season_on = st.checkbox("Also save it to the season tracker", key=_season_key(match), disabled=bool(off),
                            help="Adds these players' rounds to a season's totals. Only the players picked above are "
                                 "saved, even if you track others who played in this match. Ticked for you when "
                                 "your team played a custom game (a scrim or a league match): untick practice.")
    if off:
        st.caption(off)  # why: the demo season, or the public website
    elif season_on:
        st.text_input("Season", value=session.season, key="r6_season_input")
        for side, column in enumerate(st.columns(2)):
            if not picked[side]:
                continue
            generic = team_names[side].strip().upper() in GENERIC_TEAM_NAMES
            column.text_input(
                f"Team or school name for {team_names[side]}", value="" if generic else team_names[side],
                key=_team_name_key(match, side), placeholder="e.g. Varsity" if generic else None,
                help=f'The replay only calls this team "{team_names[side]}", so enter its real name. Left blank, '
                     "players keep the team they already have in the tracker." if generic else None,
            )
    st.button("Update players" if added else "Confirm match and players", type="primary",
              disabled=not (picked[0] or picked[1]), on_click=_add_match, args=(source, match, files))


def _keep_replay_folder() -> None:
    """The folder box's callback: keep a folder that's there for next time, on this PC."""
    typed = (Session().get("replay_folder") or "").strip().strip('"')
    if typed and Path(typed).expanduser().exists():
        save_replay_folder(Path(typed).expanduser())


def _use_game_folder(auto: str) -> None:
    Session().set("replay_folder", auto)
    save_replay_folder(None)


class DashboardTab(Tab):
    """One match's scoreboards, round by round, and the step that adds it to your stats. Its
    matches come from any ReplaySource: your folder, uploads, the replays/ folder or the demo
    season, all picked and opened the same way."""

    title, name = "Dashboard", "dashboard"

    def heading(self) -> None:
        start_workdir_sweeper()
        self.local_files = can_read_local_files()
        self.demo_mode = self.sidebar()
        st.title(self.title)

    def show_note(self) -> None:
        pass  # shown in "Add to your stats", next to what it's about

    def render(self) -> None:
        if self.demo_mode and not self.session.demo:
            match = load_demo_match()
            st.caption("Showing the bundled demo match. Turn off demo mode in the sidebar to load a real replay.")
            self.show_match(match, None, 0, None)
            return
        source = DemoSource() if self.demo_mode else self.choose_source()
        if source is None:
            self.getting_started()
            return
        if source.skipped:
            st.warning(source.skipped, icon="🛡️")
        if source.error:
            st.error(source.error)
            return
        name = self.pick_match(source)
        if source.needs_reading(name):
            with st.spinner("Reading the match... long matches can take a few seconds."):
                source.open(name)
        result = source.open(name)
        if isinstance(result, ReplayParseError):
            st.error(f"{name}: {result}")
            return
        match, raw, warnings = result
        if note := source.note():
            st.caption(note)
        mine = ("", {}, {"mine": None, "logos": {}, "found": {}})
        if source.adds_to_stats:
            my_name, my_players, my_necc = my_team()
            match, sides = name_sides(match, my_name, my_players, my_necc)
            mine = (my_name, my_players, sides)
        for w in warnings:
            st.warning(w, icon="⚠️")
        if raw:
            with st.sidebar:
                with st.expander("🔍 Parser debug info"):
                    st.json(raw_shape_preview(raw))
        self.show_match(match, name, source.round_files(name), source, *mine)

    def getting_started(self) -> None:
        """What to do with nothing loaded yet: the steps, and the demo season a click away."""
        with st.container(border=True):
            st.markdown("#### Getting started")
            st.markdown(
                "1. **Load a match** above" + (": the app finds Siege's replay folder by itself, or paste its path."
                                               if self.local_files else ": upload a match folder's zip.") + "\n"
                "2. Check the scoreboard, then **Confirm match and players** to add it to your stats.\n"
                "3. Ask anything on **Ask**, draw it on **Charts**, or open the **Assistant** in the corner.")
            left, right = st.columns(2)
            left.button("Try the demo season", icon=":material/science:", key="start_demo_here", on_click=start_demo,
                        type="primary", width="stretch", help="Twelve made-up matches on every page. Your own stats "
                                                              "aren't touched; leave it from the sidebar.")
            right.page_link("download.py", label="Where are my replays?", icon=":material/folder_open:")

    # ----------------------------------------------------------- input ----
    def sidebar(self) -> bool:
        """The sidebar's notes and the demo match switch; returns whether it's on."""
        with st.sidebar:
            st.caption("Drop in a match replay for an esports-style scoreboard of every player.")
            if not r6_dissect_available():
                st.warning("r6-dissect not found, so real replays can't be parsed. "
                           "See the README to build it, or use the demo match below.", icon="⚠️")
            demo_mode = st.toggle("Use demo match (no file needed)",
                                  value=self.session.demo or not r6_dissect_available())
            st.divider()
            st.caption("EPS · KD (+/-) · Entry · KOST · KPR · HS · SRV · Clutches · Multikills · "
                       "Objectives · Dead for trade kill · Trade kills")
            st.caption(f"{APP_NAME} {APP_VERSION}")
            st.caption(NOTICE)
        return demo_mode

    def choose_source(self) -> ReplaySource | None:
        """Where to read replays from (a folder, an upload, the replays/ folder), as a ReplaySource."""
        if not self.local_files:
            kinds = [UPLOAD]
        elif is_windows_app():
            kinds = [FOLDER, UPLOAD]
        else:
            kinds = [FOLDER, UPLOAD, REPLAYS_FOLDER]
        kind = kinds[0]
        if len(kinds) > 1:
            kind = st.radio(
                "Replay source", kinds, horizontal=True,
                help="On GitHub Codespaces, browser uploads over ~50 MB fail with HTTP 413; "
                     "put big matches in replays/ instead." if os.environ.get("CODESPACES") else None,
            )
        state = {UPLOAD: self.uploaded, FOLDER: self.from_folder}.get(kind, self.from_replays_dir)()
        return LoadedSource.of(state)

    def uploaded(self) -> dict | None:
        uploaded = st.file_uploader(
            "Drop the match's .zip (or every .rec file from the match folder)",
            type=["zip", "rec"], accept_multiple_files=True,
        )
        if not self.local_files:
            st.caption("Your replays are in the game's `MatchReplay` folder, one folder per match. "
                       "Right-click a match folder, choose **Send to > Compressed (zipped) folder**, "
                       "and upload that zip. Prefer not to upload? Get the Windows app, which "
                       "reads the folder directly.")
        if not uploaded:
            return None
        sig = ("upload",) + tuple((u.name, u.size, u.file_id) for u in uploaded)
        return load_source(sig, lambda td, sc, u=uploaded: save_uploads(u, td, sc))

    def from_folder(self) -> dict | None:
        found = find_replay_folders()
        auto = str(found[0]) if found else ""
        if not self.session.has("replay_folder"):  # first time on this page: the folder kept on this PC
            saved = saved_replay_folder()
            self.session.set("replay_folder", str(saved) if saved else auto)
        current = self.session.get("replay_folder").strip().strip('"')
        # a folder that's set: the box stays out of the way; otherwise it's the first thing to fill in
        box = st.expander("Replay folder", expanded=not current) if current else st.container()
        with box:
            typed = st.text_input(
                "Path to a match folder, a .zip, or your whole MatchReplay folder", key="replay_folder",
                on_change=_keep_replay_folder,
                placeholder=r"C:\Program Files (x86)\Steam\steamapps\common\Tom Clancy's Rainbow Six Siege\MatchReplay",
            ).strip().strip('"')
            if auto and typed == auto:
                st.caption("Found your Siege replays folder automatically.")
            elif typed:
                st.caption("Your replay folder: it stays picked, here and on every page, until you change it.")
                if auto:
                    st.button("Use the folder Siege saves to", on_click=_use_game_folder, args=(auto,),
                              type="tertiary")
        if not typed:
            return None
        path = Path(typed).expanduser()
        if not path.exists():
            st.error(f"Not found: {path}")
            return None
        return folder_source(path)

    def from_replays_dir(self) -> dict | None:
        REPLAYS_DIR.mkdir(exist_ok=True)
        choices = sorted(
            [p for p in REPLAYS_DIR.iterdir()
             if (p.is_dir() and any(p.rglob("*.rec"))) or p.suffix.lower() in (".zip", ".rec")],
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        st.caption(f"Copy a match folder or its `.zip` into `{REPLAYS_DIR}`, then pick it here.")
        if not choices:
            st.info(f"No matches in `{REPLAYS_DIR}` yet.")
            return None
        chosen = st.selectbox("Match", choices, format_func=lambda p: p.name + ("/" if p.is_dir() else ""))
        return folder_source(chosen)

    def pick_match(self, source: ReplaySource) -> str:
        """Which of the source's matches to show: newest first, or the one another tab asked for."""
        names = source.matches()
        with self.stats() as db:
            known = {r["source"]: r for r in db.match_list(db.me())}
        opening = self.session.match_to_open()  # "Open on Dashboard" on Match History
        if len(names) == 1:
            return names[0]
        return st.selectbox(source.picker_label(len(names)), names,
                            index=names.index(opening) if opening in names else 0,
                            format_func=lambda n: source.describe(n, known.get(n)))

    # ----------------------------------------------------------- match ----
    def show_match(self, match: dict, name: str | None, files: int, source: ReplaySource | None,
                   my_name: str = "", my_players: dict | None = None, sides: dict | None = None) -> None:
        sides = sides or {"mine": None, "logos": {}, "found": {}}
        stats = compute_match_metrics(match)
        rows = pro_league_rows(stats)
        team_names = match["team_names"]
        score = match["final_score"]
        self.session.last_match = match
        if not stats:
            st.warning("This replay has no player data (it may be a practice session or a match "
                       "that ended before it started).", icon="⚠️")
            return
        if not any(s.kills for s in stats.values()):
            st.warning("Parsed, but no kills were found. Expand **Parser debug info** in the sidebar "
                       "to see what r6-dissect returned.", icon="⚠️")
        top_player = max(stats.values(), key=lambda player: (player.eps, player.kills))
        snapshot_columns = st.columns(4)
        snapshot_columns[0].metric("Rounds", len(match["rounds"]))
        snapshot_columns[1].metric("Match score", f"{score[0]} : {score[1]}")
        snapshot_columns[2].metric("Top EPS", top_player.eps)
        snapshot_columns[3].metric("Top performer", top_player.name)
        st.markdown(scorecard_html(match, sides, my_name), unsafe_allow_html=True)
        for team_idx, team_name in enumerate(team_names[:2]):
            team_rows = [r for r in rows if r["Team"] == team_idx]
            if team_rows:
                won = score[team_idx] > score[1 - team_idx]
                st.markdown(scoreboard_html(team_name, won, team_rows), unsafe_allow_html=True)
        if source is not None and source.adds_to_stats:
            confirm_match(name, match, files, my_name, my_players, sides["mine"])
        elif source is not None:
            st.caption("This match is part of the demo season, which already has it in its stats.")
        else:
            st.caption("The demo match is only for trying the app, so it can't be added to your stats.")
        self.exports(match, stats, rows)
        self.breakdown(match, stats, rows)

    def exports(self, match: dict, stats: dict, rows: list[dict]) -> None:
        team_names, score = match["team_names"], match["final_score"]
        rounds = round_rows(match, stats)
        c1, c2, c3, c4, c5 = st.columns([1, 1, 1, 1.4, 2])
        c1.download_button("⬇ CSV", rows_csv([{"Player": r["Player"], "Team Name": team_names[r["Team"]], **r}
                                               for r in leaderboard_rows(stats)]).encode("utf-8"),
                           file_name=f"{match['match_id']}_stats.csv", mime="text/csv")
        c2.download_button("⬇ JSON", json.dumps({
            "map": match["map"], "match_id": match["match_id"], "teams": team_names,
            "score": score, "players": rows, "rounds": rounds},
            indent=2, ensure_ascii=False).encode("utf-8"),
            file_name=f"{match['match_id']}_stats.json", mime="application/json")
        c3.download_button("⬇ TXT", (scoreboard_text(match, rows) + "\n\n" + rounds_text(match) + "\n").encode("utf-8"),
                           file_name=f"{match['match_id']}_stats.txt", mime="text/plain")
        c4.download_button("⬇ Rounds CSV", rows_csv(round_player_rows(rounds)).encode("utf-8"),
                           file_name=f"{match['match_id']}_rounds.csv", mime="text/csv",
                           help="One row per player per round: side, operator, result, K-D-A and highlights.")
        accent = current_theme()["primary"]
        # everything the picture shows: the cache is shared by every visitor to the website
        key = (match.get("match_id"), match.get("played_at"), match.get("map"), match.get("match_type"),
               len(match["rounds"]), tuple(match["team_names"][:2]), tuple(match["final_score"]),
               tuple(tuple(r.values()) for r in rows))
        c5.download_button("⬇ Scoreboard image", _scoreboard_image(key, match, rows, accent),
                           file_name=f"{match['match_id']}_scoreboard.png", mime="image/png",
                           help="The scoreboard as a picture, in your Team Theme's color, to post in a team chat.")

    def breakdown(self, match: dict, stats: dict, rows: list[dict]) -> None:
        team_names = match["team_names"]
        st.subheader("Round-by-round")
        st.markdown(rounds_html(match), unsafe_allow_html=True)
        player = st.selectbox("Player", [r["Player"] for r in rows], key="r6_round_player",
                              format_func=lambda name: f"{name} · {team_names[stats[name].team]}")
        s = stats[player]
        for col, (label, value) in zip(st.columns(3) + st.columns(3), (
            ("EPS", s.eps),
            ("K-D-A", f"{s.kills}-{s.deaths}-{s.assists}"),
            ("Entry K-D", f"{s.entry_kills}-{s.entry_deaths}"),
            ("KOST", f"{s.kost_pct:.0f}%"),
            ("Plants-Defuses", f"{s.plants}-{s.defuses}"),
            ("Traded-Trade kills", f"{s.trades}-{s.trade_kills}"),
        )):
            col.metric(label, value)
        st.markdown(side_split_html(s.round_breakdown)
                    + player_rounds_html(player, team_names[s.team], match, s.round_breakdown), unsafe_allow_html=True)
        with st.expander("Stat definitions"):
            st.markdown(STAT_DEFINITIONS)


def scorecard_html(match: dict, sides: dict, my_name: str) -> str:
    """The match's score between its two sides, with each side's logo and which one is yours."""
    team_names, score = match["team_names"], match["final_score"]

    def side_html(side: int, align: str) -> str:
        logo = sides["logos"].get(side)
        img = f'<img class="sc-logo" src="{html.escape(logo, quote=True)}" alt="">' if logo else ""
        you = '<span class="sc-you">YOUR TEAM</span>' if sides["mine"] == side and my_name else ""
        inner = f'{img}<div><div class="team-name">{html.escape(team_names[side])}</div>{you}</div>'
        mirrored = "flex-direction:row-reverse;text-align:right;" if align == "flex-end" else ""
        return f'<div class="sc-side" style="{mirrored}">{inner}</div>'

    rounds = len(match["rounds"])
    return (f'<div class="scorecard">'
            f'{side_html(0, "flex-start")}'
            f'<div style="text-align:center"><span class="score-big">{score[0]}</span>'
            f'<span style="color:#8b949e;font-size:1.6rem"> : </span><span class="score-big">{score[1]}</span><br>'
            f'<span class="map-pill">{html.escape(match["map"])} · {rounds} round{"s" if rounds != 1 else ""}'
            f'{" · " + html.escape(match["match_type"]) if match.get("match_type") else ""}</span></div>'
            f'{side_html(1, "flex-end")}'
            f'</div>')


STAT_DEFINITIONS = (
    "- **⚔︎ Attack / ♜ Defense**: the side the player's team played that round. "
    "**Operator**: who they picked that round. **Score**: the score after that round.\n"
    "- **Highlights**: a multikill (2K to ACE), the round's entry kill or death, trade kills, a death "
    "that was traded, the defuser planted or disabled, and clutches won (or lost) as the last one alive.\n"
    "- **EPS**: performance score centered on 100 (match average). Ubisoft hasn't published "
    "its EPS formula; this one combines KPR, deaths, KOST, entry differential, multikills, "
    "clutches, objectives and trade kills, weighted against everyone in this match.\n"
    "- **KD (+/-)**: kills-deaths (difference). **Entry**: opening kills-opening deaths.\n"
    "- **KOST**: % of rounds with a Kill, Objective, Survival or Traded death. "
    "**KPR**: kills per round. **HS**: headshot kills %. **SRV**: % of rounds survived.\n"
    "- **Clutches**: rounds won as the team's last player alive vs 1+ enemies. "
    "**Multikills**: rounds with 2+ kills.\n"
    "- **Objectives**: defuser plants + disables.\n"
    "- **Dead for trade kill**: deaths a teammate avenged within 10 s. "
    "**Trade kills**: kills that avenged a teammate within 10 s."
)


DashboardTab().run()
