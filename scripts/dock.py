"""
dock.py
=======
The docked view. When the Windows app is docked to the edge of the screen (desktop/launcher.py),
its narrow panel shows this page instead of the full app: your latest match round by round
(⚔ attack, ♜ defense, won or lost), your numbers in it, and your recent form, from the matches
you've added to your stats. It checks the replay folder every few seconds, and says when the game
has saved a match you haven't added yet: matches are only added once you confirm them, and the
players to track in them, on the Dashboard.
"""

from __future__ import annotations

import html
import time
from datetime import datetime

import streamlit as st

from app_info import can_dock, send_window_command
from matchday_data import MatchdayWorkspace, fixture_when, head_to_head
from metrics_engine import SIDE_ICONS, SIDE_NAMES, eps_from_rating
from session import Session
from sources import current_source, match_time, open_stats_db
from stats_db import nice_time
from tab import Tab

REFRESH_SECONDS = 15
SCHEDULE_SECONDS = 60  # how often the panel reads Match day's schedule again
FORM_MATCHES = 5

STYLE = """<style>
header[data-testid="stHeader"] { display:none; }
[data-testid="stElementContainer"]:has(style) { display:none; }  /* the pages' <style> rows: no gaps for them */
.stMainBlockContainer { padding:.7rem .9rem 1.2rem !important; }
.dk-kicker { color:var(--dim); font:600 10px var(--mono); letter-spacing:.14em; text-transform:uppercase; margin:14px 0 6px; }
.dk-card { border:1px solid var(--border); border-left:3px solid var(--school, var(--accent)); border-radius:4px;
           background:linear-gradient(135deg, rgba(23,40,59,.9), rgba(12,22,36,.9)); padding:12px 14px; }
.dk-result { display:flex; align-items:baseline; gap:12px; }
.dk-wl { font:700 13px var(--mono); letter-spacing:.12em; padding:2px 8px; border-radius:2px; border:1px solid; }
.dk-wl.win { color:var(--pos); border-color:var(--pos); } .dk-wl.loss { color:var(--neg); border-color:var(--neg); }
.dk-wl.draw { color:var(--dim); border-color:var(--dim); }
.dk-score { font:600 30px/1 var(--mono); color:var(--text); } .dk-score i { color:var(--dim); font-style:normal; margin:0 4px; }
.dk-meta { color:var(--dim); font-size:.8rem; margin-top:4px; }
.dk-rounds { display:flex; flex-wrap:wrap; gap:4px; margin-top:10px; }
.rt { width:32px; height:36px; border-radius:3px; display:flex; flex-direction:column; align-items:center;
      justify-content:center; font:600 9px var(--mono); color:var(--dim); border:1px solid var(--border);
      background:rgba(168,184,202,.05); }
.rt .ico { font-size:14px; line-height:1.1; }
.rt.attack .ico { color:var(--atk); } .rt.defense .ico { color:var(--def); }
.rt.won { border-color:rgba(134,185,159,.65); background:rgba(134,185,159,.14); }
.rt.lost { border-color:rgba(225,126,105,.55); background:rgba(225,126,105,.1); }
.dk-stats { display:grid; grid-template-columns:repeat(3, 1fr); gap:6px; margin-top:12px; }
.dk-stat { border:1px solid var(--border); border-radius:3px; padding:6px 8px; background:rgba(8,14,25,.45); }
.dk-stat b { display:block; font:600 17px var(--mono); color:var(--accent); }
.dk-stat span { color:var(--dim); font-size:.68rem; text-transform:uppercase; letter-spacing:.08em; }
.dk-ops { margin-top:10px; color:var(--dim); font-size:.8rem; line-height:1.7; }
.dk-ops .attack { color:var(--atk); } .dk-ops .defense { color:var(--def); } .dk-ops b { color:var(--text); font-weight:600; }
.dk-form { display:flex; gap:5px; }
.dk-form span { width:26px; height:26px; display:flex; align-items:center; justify-content:center; border-radius:3px;
                font:700 11px var(--mono); border:1px solid var(--border); color:var(--dim); }
.dk-form .w { color:var(--pos); border-color:rgba(134,185,159,.6); } .dk-form .l { color:var(--neg); border-color:rgba(225,126,105,.55); }
.dk-line { color:var(--dim); font-size:.8rem; margin-top:6px; } .dk-line b { color:var(--text); }
.dk-foot { color:#5d6f84; font-size:.7rem; margin-top:14px; }
.dk-new { border:1px dashed var(--school, var(--accent)); border-radius:4px; padding:10px 12px; margin-top:4px;
          color:var(--dim); font-size:.8rem; line-height:1.45; }
.dk-new b { color:var(--text); font-weight:600; }
</style>"""


def _side_icon(side: str | None) -> str:
    return f'<span class="ico" aria-hidden="true">{SIDE_ICONS[side]}</span>' if side in SIDE_ICONS else '<span class="ico">·</span>'


def rounds_strip(rounds: list[dict]) -> str:
    """One tile per round: its number, the side played (⚔ or ♜) and whether it was won."""
    tiles = []
    for r in rounds:
        result = {1: "won", 0: "lost"}.get(r["won"], "")
        title = " · ".join(x for x in (f"Round {r['round']}", SIDE_NAMES.get(r["side"], ""),
                                       result.capitalize()) if x)
        tiles.append(f'<span class="rt {r["side"] or ""} {result}" title="{html.escape(title)}">'
                     f'{_side_icon(r["side"])}{r["round"]}</span>')
    return f'<div class="dk-rounds">{"".join(tiles)}</div>' if tiles else ""


def latest_match_html(db, me: str | None, row: dict) -> str:
    """The newest match: result and score from `me`'s side, where and when, its rounds, and
    `me`'s numbers and operators in it."""
    mine = row["player"] is not None
    team = row["team"] if mine else 0
    own, other_score = (row["score0"], row["score1"]) if team == 0 else (row["score1"], row["score0"])
    if mine:
        result = {1: ("win", "WIN"), 0: ("loss", "LOSS")}.get(row["won"], ("draw", "DRAW"))
    else:
        result = ("draw", html.escape((row["team0"] or "Team A").upper()))
    meta = " · ".join(html.escape(x) for x in (row["map"], row["match_type"], f"{row['rounds']} rounds") if x)

    if mine:
        rounds = db.query("SELECT round, side, won FROM round_players WHERE match_id = ? AND player_key = ? "
                          "ORDER BY round", (row["match_id"], me.casefold()))
    else:
        rounds = db.query("SELECT round, side, won FROM team_rounds WHERE match_id = ? AND team = 0 ORDER BY round",
                          (row["match_id"],))
    out = [f'<div class="dk-kicker">Latest match · {html.escape(nice_time(row["played_at"]))}</div>',
           '<div class="dk-card">',
           f'<div class="dk-result"><span class="dk-wl {result[0]}">{result[1]}</span>'
           f'<span class="dk-score">{own}<i>–</i>{other_score}</span></div>',
           f'<div class="dk-meta">{meta}</div>', rounds_strip(rounds)]
    if mine:
        stats = db.query("SELECT kills, deaths, assists, rounds, kost_rounds, rating FROM match_players "
                         "WHERE match_id = ? AND player_key = ?", (row["match_id"], me.casefold()))[0]
        kost = f"{100 * stats['kost_rounds'] / stats['rounds']:.0f}%" if stats["rounds"] else "—"
        out.append('<div class="dk-stats">'
                   f'<div class="dk-stat"><b>{stats["kills"]}-{stats["deaths"]}-{stats["assists"]}</b><span>K-D-A</span></div>'
                   f'<div class="dk-stat"><b>{eps_from_rating(stats["rating"])}</b><span>EPS</span></div>'
                   f'<div class="dk-stat"><b>{kost}</b><span>KOST</span></div></div>')
        ops = db.query("SELECT operator, side, COUNT(*) AS n FROM round_players WHERE match_id = ? AND player_key = ? "
                       "AND operator IS NOT NULL GROUP BY operator, side ORDER BY n DESC, operator",
                       (row["match_id"], me.casefold()))
        if ops:
            out.append('<div class="dk-ops">' + " · ".join(
                f'<span class="{o["side"] or ""}">{SIDE_ICONS.get(o["side"], "")}</span> {html.escape(o["operator"])} '
                f'<b>×{o["n"]}</b>' for o in ops) + "</div>")
    out.append("</div>")
    return "".join(out)


def form_html(rows: list[dict]) -> str:
    """The last few matches as W / L (newest first), with the record and K/D over them."""
    mine = [r for r in rows if r["player"] is not None]
    if not mine:
        return ""
    chips = "".join(
        f'<span class="{ {1: "w", 0: "l"}.get(r["won"], "") }" '
        f'title="{html.escape(" · ".join(x for x in (nice_time(r["played_at"]), r["map"]) if x))}">'
        f'{ {1: "W", 0: "L"}.get(r["won"], "D") }</span>' for r in mine)
    wins, losses = sum(r["won"] == 1 for r in mine), sum(r["won"] == 0 for r in mine)
    kills, deaths = sum(r["kills"] for r in mine), sum(r["deaths"] for r in mine)
    return (f'<div class="dk-kicker">Last {len(mine)} match{"es" if len(mine) != 1 else ""}</div>'
            f'<div class="dk-form">{chips}</div>'
            f'<div class="dk-line">Record <b>{wins}–{losses}</b> · K/D <b>{kills / deaths if deaths else kills:.2f}</b></div>')


def schedule():
    """Match day's schedule, read at most once a minute (a team's is fetched over the network, and the panel
    refreshes every few seconds); an empty one if it can't be read, so the panel still shows your matches."""
    session = Session()
    cached = session.get("dock_schedule")
    if cached and time.monotonic() - cached[0] < SCHEDULE_SECONDS:
        return MatchdayWorkspace.from_dict(cached[1])
    try:
        workspace = session.matchday
    except Exception:  # unreadable planning data, a team server that's down: never the whole panel
        workspace = MatchdayWorkspace()
    session.set("dock_schedule", (time.monotonic(), workspace.to_dict()))
    return workspace


def next_match_html(db, workspace) -> str:
    """The next match on the Match day schedule, and your record against that opponent."""
    upcoming = workspace.upcoming()[:1]
    if not upcoming:
        return ""
    fixture = upcoming[0]
    when = fixture_when(fixture)
    record = head_to_head(db, fixture["opponent"])
    return (f'<div class="dk-kicker">Next match</div>'
            f'<div class="dk-line"><b>vs {html.escape(fixture["opponent"])}</b> · {html.escape(when)}'
            + (f' · {html.escape(fixture["kind"])}' if fixture.get("kind") else "") + "</div>"
            + (f'<div class="dk-line">{html.escape(record)}</div>' if record else ""))


def new_match_html(name: str) -> str:
    """The newest replay in the folder, when it isn't in your stats: where to add it."""
    when = match_time(name)
    return (f'<div class="dk-kicker">New match{" · " + html.escape(nice_time(when)) if when else ""}</div>'
            '<div class="dk-new"><b>Not in your stats yet.</b> Open the full window to check it and pick the '
            "players to track: nothing is added without you.</div>")


@st.fragment(run_every=REFRESH_SECONDS)
def panel() -> None:
    with open_stats_db() as db:
        source = current_source()
        groups = (source or {}).get("groups") or {}
        newest = max(groups, key=lambda n: match_time(n) or n) if groups else None
        waiting = newest and not db.added(newest) and new_match_html(newest)
        me = db.me()
        rows = db.match_list(me, limit=FORM_MATCHES)
        if not rows:
            if waiting:
                st.markdown(waiting, unsafe_allow_html=True)
            else:
                st.info("No matches yet. Play one, then add it from the full window's Dashboard."
                        if source else "The game's replay folder wasn't found. Open the full window and pick it on "
                                       "the Dashboard.")
            return
        st.markdown((waiting or "") + latest_match_html(db, me, rows[0]) + form_html(rows)
                    + next_match_html(db, schedule()), unsafe_allow_html=True)
    who = f"As {html.escape(me)} · " if me else ""
    st.markdown(f'<div class="dk-foot">{who}updated {datetime.now():%H:%M:%S}, every {REFRESH_SECONDS} s</div>',
                unsafe_allow_html=True)


class DockTab(Tab):
    """The docked view: no heading, just the buttons that move or undock the window, then the panel."""

    title, name = "Docked", "dock"

    def heading(self) -> None:
        st.markdown(STYLE, unsafe_allow_html=True)
        edge = st.query_params.get("edge", "right")
        other = "left" if edge == "right" else "right"
        with st.container(horizontal=True, gap="small"):
            if can_dock() and st.button(f"⇄ Dock {other}", key="dock_other"):
                send_window_command("dock", edge=other)
            if st.button("⇱ Full window", key="undock", type="primary"):
                if can_dock():
                    send_window_command("undock")
                else:  # outside the Windows app there's no window to change: just go back to the full app
                    st.query_params.clear()
                    st.rerun()

    def render(self) -> None:
        panel()


DockTab().run()
