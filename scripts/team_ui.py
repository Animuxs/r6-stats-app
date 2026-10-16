"""How a team looks on the pages: its card (logo, school, name, division, record, in its own colors)
and its roster as chips. League & Team, Team Theme and the docked panel share them."""

from __future__ import annotations

import html

import streamlit as st

TEAM_CSS = """<style>
.tm-card { display:flex; align-items:center; gap:16px; margin:8px 0 6px; padding:14px 18px; border-radius:4px;
           border:1px solid var(--border); border-left:5px solid var(--tm); position:relative; overflow:hidden;
           background:linear-gradient(110deg, color-mix(in srgb, var(--tm) 22%, #0c1624) 0%, #0c1624 62%); }
.tm-card:after { content:''; position:absolute; right:-20px; top:-40px; width:60px; height:200px; transform:rotate(25deg);
                 background:var(--tm2); opacity:.18; }
.tm-mine { box-shadow:0 0 0 1px var(--tm2) inset; }
.tm-logo { width:58px; height:58px; object-fit:contain; flex:none; border-radius:4px; background:#ffffff0d; }
.tm-blank { background:var(--tm); opacity:.5; }
.tm-body { flex:1; min-width:0; }
.tm-school { color:var(--dim); font:600 10px var(--mono); letter-spacing:.14em; text-transform:uppercase; }
.tm-name { font:700 1.55rem/1.1 var(--display); color:var(--text); margin:2px 0 6px; }
.tm-tags { display:flex; flex-wrap:wrap; gap:6px; }
.tm-tags span { border:1px solid color-mix(in srgb, var(--tm2) 70%, transparent); color:var(--text); padding:1px 7px;
                border-radius:2px; font:700 9px var(--mono); letter-spacing:.1em; }
.tm-record { display:grid; grid-template-columns:auto auto; gap:2px 8px; align-items:baseline; text-align:right;
             z-index:1; }
.tm-record b { font:700 1.3rem var(--mono); color:var(--text); }
.tm-record span { color:var(--dim); font:600 9px var(--mono); letter-spacing:.12em; text-transform:uppercase; }
.tm-roster { display:grid; grid-template-columns:repeat(auto-fill, minmax(170px, 1fr)); gap:6px; margin:4px 0 8px; }
.tm-player { border:1px solid var(--border); border-radius:3px; padding:7px 10px; background:rgba(12,22,36,.6); }
.tm-player b { color:var(--text); font-weight:600; font-size:.88rem; }
.tm-player i { margin-left:6px; color:var(--secondary); font:700 8px var(--mono); letter-spacing:.1em; font-style:normal; }
.tm-player span { display:block; color:var(--accent); font:500 11px var(--mono); }
.tm-unlinked span { color:var(--dim); font-style:italic; }
@media(max-width:700px) { .tm-card { flex-wrap:wrap; } .tm-record { text-align:left; } }
</style>"""


def team_css() -> None:
    st.markdown(TEAM_CSS, unsafe_allow_html=True)


def team_card(team: dict, mine: bool = False) -> str:
    """A NECC team as a card: logo, school, name, division, group, tag and record, in its colors."""
    color = team.get("color") or "var(--school)"
    alt = team.get("color_alt") or "var(--secondary)"
    logo = (f'<img class="tm-logo" src="{html.escape(team["logo_url"], quote=True)}" alt="">'
            if team.get("logo_url") else '<div class="tm-logo tm-blank"></div>')
    tags = [f"DIVISION {team['division']}" if team.get("division") else "", team.get("group", "").upper(),
            f"[{team['clan_tag']}]" if team.get("clan_tag") else "", "YOUR TEAM" if mine else ""]
    record = team.get("record") or {}
    played = record.get("wins", 0) + record.get("losses", 0)
    score = (f'<div class="tm-record"><b>{record.get("wins", 0)}–{record.get("losses", 0)}</b><span>Matches</span>'
             f'<b>{record.get("game_wins", 0)}–{record.get("game_losses", 0)}</b><span>Maps</span></div>'
             if played else '<div class="tm-record"><span>No results yet</span></div>')
    return (f'<div class="tm-card{" tm-mine" if mine else ""}" style="--tm:{color};--tm2:{alt}">{logo}'
            f'<div class="tm-body"><div class="tm-school">{html.escape(team.get("school") or "")}</div>'
            f'<div class="tm-name">{html.escape(team["name"])}</div>'
            f'<div class="tm-tags">{"".join(f"<span>{html.escape(t)}</span>" for t in tags if t)}</div></div>'
            f"{score}</div>")


def roster_html(team: dict) -> str:
    """A team's roster as chips: league name, the in-game name(s) replays show, staff marked."""
    chips = []
    for p in team["players"]:
        ingame = " / ".join(p["ubisoft"]) if p["ubisoft"] else "no Ubisoft name linked"
        staff = '<i>STAFF</i>' if not p["player"] else ""
        chips.append(f'<div class="tm-player{"" if p["ubisoft"] else " tm-unlinked"}"><b>{html.escape(p["name"])}</b>'
                     f'{staff}<span>{html.escape(ingame)}</span></div>')
    return f'<div class="tm-roster">{"".join(chips)}</div>' if chips else ""
