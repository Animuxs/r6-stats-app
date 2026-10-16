"""The League & Team page: say which league or tournament your team plays in, and which team is
yours. Pick a Liquipedia event (S- to D-Tier), a NECC division, or name a league that isn't
listed. It's saved on this PC: the banner on every page shows it, and the season tracker can be
named after it.

Saving your team sets the app up for it, with nothing typed: a NECC team comes with its roster
(rosters.py: every player's Ubisoft name, as replays show them), so its players are tracked in
the season tracker, become a Team Analytics team that Ask knows by name, and the team's colors and
logo go on the app. Any other team's players are found in your custom games. Find a team looks up
any NECC team, yours or an opponent's. NECC's school directory is here too."""

from __future__ import annotations

import html
import json
import os
import re
import sqlite3
from datetime import date

import streamlit as st

import events
import rosters
from team_ui import roster_html, team_card, team_css
from app_info import is_public_host
from branding import DEFAULT_COMPETITION, DEFAULT_THEME, apply_competition, apply_theme, current_competition
from necc_data import MAX_CATALOG_BYTES, bundled_school_catalog, fetch_school_catalog, normalize_school_catalog
from season_stats import StatsError, StatsManager
from sources import current_source, open_stats_db, parse
from sources import tracker_off
from ui import md

LIQUIPEDIA, NECC, CUSTOM = "Liquipedia event (S–D Tier)", "NECC", "My league isn't listed"
SOURCES = {LIQUIPEDIA: "liquipedia", NECC: "necc", CUSTOM: "custom"}
NOT_LISTED = "My team isn't listed…"
FOUND_KEY = "league_players_found"  # the players picked from your replays, for a team without a roster
ALL_REGIONS = "All regions"
TIER_HELP = ("Liquipedia's tiers: S is the top international events (Majors, the Six Invitational), A the "
             "regional leagues, B and C the main third-party and challenger circuits, D community and amateur "
             "events.")


def _default(key: str, index: int) -> int | None:
    """A widget's starting choice, unless "This is my team" has already set it (Streamlit warns
    when a widget gets both)."""
    return None if key in st.session_state else index


def _dates(event: dict) -> str:
    start, end = events.parse_day(event.get("start", "")), events.parse_day(event.get("end", ""), end=True)
    if not start:
        return ""
    unknown = "??" in (event.get("end") or "")
    if not end or end == start:
        return f"{start:%b %d, %Y}"
    if start.year != end.year:
        return f"{start:%b %d, %Y} – {end:%b %d, %Y}"
    return f"{start:%b %d} – {'?' if unknown else f'{end:%b %d}'}, {end.year}"


def _label(event: dict) -> str:
    bits = [event["name"], f"{event['tier']}-Tier" if event.get("tier") else "", _dates(event),
            event.get("location", ""), event.get("status", "")]
    return " · ".join(b for b in bits if b)


def event_card(event: dict) -> None:
    """What's known about an event: tier, kind, dates, where, prize pool, teams, links."""
    tier = f'<span class="lg-tier">{html.escape(event["tier"])}-TIER</span>' if event.get("tier") else ""
    facts = [
        ("When", _dates(event) + (f" · {event['status']}" if event.get("status") else "")),
        ("Where", " · ".join(x for x in (event.get("mode", ""), event.get("location", "")) if x)),
        ("Organizer", event.get("organizer", "")),
        ("Type", event.get("kind", "")),
        ("Prize pool", f"${event['prize_usd']:,}" if event.get("prize_usd") else ""),
        ("Teams", str(event.get("team_count") or len(event.get("teams") or []) or "")),
    ]
    rows = "".join(f'<div class="lg-fact"><span>{k}</span><b>{html.escape(v)}</b></div>' for k, v in facts if v)
    st.markdown(f'<div class="lg-card">{tier}<div class="lg-name">{html.escape(event["name"])}</div>'
                f'<div class="lg-facts">{rows}</div></div>', unsafe_allow_html=True)
    links = [(label, url) for label, url in (("Liquipedia page" if event["source"] == "liquipedia" else "NECC page",
                                              event.get("url")), ("Official site", event.get("website"))) if url]
    for column, (label, url) in zip(st.columns(max(len(links), 1)), links):
        column.link_button(label, url, width="stretch")


def pick_liquipedia(saved: dict) -> dict | None:
    catalog = events.current_catalog()
    today = date.today()
    listed = events.current_events(catalog, today)
    if not listed:
        st.info("No Liquipedia events are loaded. Check Liquipedia for events below, or name your league instead.")
        return None
    tiers_column, region_column, status_column = st.columns([2, 1.3, 1])
    tiers = tiers_column.multiselect("Tiers", list(events.TIERS), default=list(events.TIERS), key="league_tiers",
                                     help=TIER_HELP)
    regions = sorted({e["region"] for e in listed if e["region"]})
    region = region_column.selectbox("Region", [ALL_REGIONS, *regions], key="league_region")
    state = status_column.selectbox("When", ["Any time", "Live", "Upcoming", "Ended"], key="league_status")
    search_column, qualifier_column = st.columns([3, 1])
    query = search_column.text_input("Search events, organizers or teams", key="league_search",
                                     placeholder="e.g. CollegiateR6, Six Invitational, your team").strip().casefold()
    qualifiers = qualifier_column.toggle("Qualifiers", value=True, key="league_qualifiers",
                                         help="Include qualifiers for other events.")
    found = [e for e in listed
             if e["tier"] in tiers and (region == ALL_REGIONS or e["region"] == region)
             and (state == "Any time" or e["status"] == state)
             and (qualifiers or e.get("kind", "").casefold() != "qualifier")
             and (not query or query in e["name"].casefold() or query in e.get("organizer", "").casefold()
                  or any(query in t.casefold() for t in e.get("teams", [])))]
    st.caption(f"{len(found)} of {len(listed)} events · Liquipedia snapshot from "
               f"{catalog.get('captured_at') or 'unknown date'} · [{events.ATTRIBUTION}]({events.LICENSE_URL})")
    if not found:
        st.info("No events match. Try other tiers or regions, or clear the search.")
        return None
    ids = [e["id"] for e in found]
    by_id = {e["id"]: e for e in found}
    event = by_id[st.selectbox("Event", ids, format_func=lambda i: _label(by_id[i]), key="league_event",
                               index=ids.index(saved["id"]) if saved["id"] in ids else 0)]
    event_card(event)
    return event


def pick_necc(saved: dict) -> dict | None:
    listed = events.necc_events()
    today = date.today()
    for e in listed:
        e["status"] = events.status(e, today)
        for team in rosters.teams():  # a team on a division's roster but not in its snapshot
            if f"necc:{team['event_id']}" == e["id"] and team["name"] not in e["teams"]:
                e["teams"].append(team["name"])
                e["schools"][team["name"]] = team["school"]
    ids = [e["id"] for e in listed]
    by_id = {e["id"]: e for e in listed}
    divisions = [i for i in ids if by_id[i]["division"]]
    default = saved["id"] if saved["id"] in ids else (divisions[0] if divisions else ids[0])
    event = by_id[st.selectbox("Division", ids, format_func=lambda i: _label(by_id[i]), key="league_necc_event",
                               index=_default("league_necc_event", ids.index(default)))]
    st.caption(f"NECC's Rainbow Six season as of {events.necc_captured_at()}: Divisions I–VI and the placement "
               "event that sorted teams into them.")
    event_card(event)
    return event


def pick_custom(saved: dict) -> dict | None:
    mine = saved if saved["source"] == "custom" else DEFAULT_COMPETITION
    name = st.text_input("League or tournament", value=mine["name"], max_chars=180, key="league_custom_name",
                         placeholder="e.g. Midwest High School Esports League, Spring 2027").strip()
    url = st.text_input("Its website (optional)", value=mine["url"], key="league_custom_url",
                        placeholder="https://…").strip()
    if url and not url.startswith("https://"):
        st.warning("Only https:// links are kept.")
    if not name:
        st.caption("Name your league or tournament, then pick your team below.")
        return None
    return {"id": "", "name": name, "source": "custom", "tier": "", "teams": [], "url": url, "schools": {}}


def pick_team(event: dict, saved: dict) -> str:
    teams = list(event.get("teams") or [])
    schools = event.get("schools") or {}
    current = saved["team"] if saved["id"] == event.get("id") or event["source"] == "custom" else ""
    if teams:
        options = [*teams, NOT_LISTED]
        index = options.index(current) if current in options else (len(teams) if current else 0)

        def label(team: str) -> str:
            school = schools.get(team)
            return f"{team} · {school}" if school and school.casefold() not in team.casefold() else team
        key = f"league_team:{_team_key(event)}"
        if st.session_state.get(key) not in (None, *options):
            del st.session_state[key]  # a team that isn't in this list (anymore)
        team = st.selectbox(f"Your team ({len(teams)} listed)", options, index=_default(key, index), format_func=label,
                            key=key)
        if team != NOT_LISTED:
            return team
    return st.text_input("Your team's name", value=current if current not in teams else "", max_chars=180,
                         key=f"league_team_name:{_team_key(event)}").strip()


def _team_key(event: dict) -> str:
    return event.get("id") or "custom"


def save(event: dict) -> None:
    """The Save button's callback. It reads the widgets' values now: arguments bound when the
    button was drawn would miss what was typed just before clicking it."""
    state = st.session_state
    if event["source"] == "custom":
        url = (state.get("league_custom_url") or "").strip()
        event = dict(event, name=(state.get("league_custom_name") or event["name"]).strip(),
                     url=url if url.startswith("https://") else "")
    picked = state.get(f"league_team:{_team_key(event)}")
    team = picked if picked and picked != NOT_LISTED else (state.get(f"league_team_name:{_team_key(event)}") or "")
    team, name_season = team.strip(), bool(state.get("league_name_season"))
    value = {"source": event["source"], "id": event.get("id", ""), "name": event["name"],
             "tier": event.get("tier", ""), "team": team, "url": event.get("url", ""),
             "season": event["name"][:120] if name_season else ""}
    try:
        apply_competition(value)
    except OSError as error:
        state["league_note"] = ("error", f"Couldn't save it: {error}")
        return
    notes = [f"Saved: {team + ' in ' if team else ''}{event['name']}."
             + (" The season tracker now uses it as the season." if name_season else "")]
    roster_team = rosters.find(team, event.get("division", "")) if event["source"] == "necc" and team else None
    players = rosters.in_game_names(roster_team) if roster_team else list(state.get(FOUND_KEY) or [])
    players = list(dict.fromkeys(players + _extra_players()))
    if team and players and state.get("league_track", True):
        notes.append(_track(team, players, value["season"] or state.get("r6_season") or "current"))
    if roster_team and state.get("league_theme", True):
        try:
            apply_theme(rosters.theme_of(roster_team))
            notes.append("Your team's colors and logo are on the app.")
        except OSError as error:
            notes.append(f"Couldn't save your team's colors: {error}")
    state["league_note"] = ("success", " ".join(notes))


def _extra_players() -> list[str]:
    """Players typed under "Add players who aren't listed" (a sub, a new signing)."""
    raw = st.session_state.get("league_extra_players") or ""
    return [n.strip() for n in re.split(r"[\n,]", raw) if n.strip()][:20]


def _track(team: str, players: list[str], season: str) -> str:
    """Make `players` your team: a Team Analytics team Ask knows by name and, in the Windows app, the
    season tracker's players for `team`. Returns what was done, in words."""
    with open_stats_db() as db:
        db.save_roster(team, players)
    done = f"{len(players)} players are your team in Team Analytics and Ask"
    if tracker_off():
        return done + " (season tracking is off on the public website)."
    try:
        with StatsManager(season=season) as manager:
            manager.add_players(players, team=team)
    except (OSError, ValueError, StatsError, sqlite3.Error) as error:
        return done + f", but the season tracker couldn't be updated: {error}."
    return done + f", and tracked for {season}: their matches are picked for you on the Dashboard."


def roster_from_replays(team: str) -> None:
    """For a team without a roster here: its players, found in your custom games."""
    st.markdown("**Your players**")
    state = current_source()
    groups = (state or {}).get("groups") or {}
    scan = st.session_state.get("league_replay_scan")
    if not groups:
        st.caption("Open your replay folder on the **Dashboard** and come back: the app finds your players in "
                   "your custom games (your scrims and league matches), so you don't type their names.")
    elif scan is None:
        st.caption("The app reads your newest custom games and lists who played on your side.")
        if st.button("Find my players in my replays", type="primary", key="league_scan"):
            bar = st.progress(0.0, text="Reading your custom games…")
            with open_stats_db() as db:
                me = db.me()
            found = rosters.from_replays(groups, lambda name: parse(state, name), me,
                                         progress=lambda done, total: bar.progress(min(done / max(total, 1), 1.0)))
            bar.empty()
            st.session_state["league_replay_scan"] = found
            st.rerun()
    else:
        if not scan:
            st.info("No custom games with you in them yet. Play a scrim, or add your players below.")
        else:
            looked = scan[0][2]
            regulars = [name for name, n, _ in scan if n >= max(2, looked // 2)]
            st.session_state.setdefault(FOUND_KEY, regulars)
            st.multiselect(f"Played on your side in your last {looked} custom game{'s' if looked != 1 else ''}",
                           [name for name, _, _ in scan], key=FOUND_KEY,
                           format_func=lambda name: f"{name} ({next(n for x, n, _ in scan if x == name)} games)",
                           help=f"Picked: everyone who played in at least half of them. These become {team}'s "
                                "players when you save.")
        if st.button("Look again", type="tertiary", key="league_rescan"):
            st.session_state.pop("league_replay_scan", None)
            st.session_state.pop(FOUND_KEY, None)
            st.rerun()


def _choose(team: dict) -> None:
    """Find a team's "This is my team": pick its division and team above."""
    st.session_state["league_source"] = NECC
    st.session_state["league_necc_event"] = f"necc:{team['event_id']}"
    st.session_state[f"league_team:necc:{team['event_id']}"] = team["name"]
    st.session_state["league_note"] = ("info", f"Picked {team['name']}: check it below and save.")


def find_a_team() -> None:
    st.subheader("Find a team")
    snapshot_day = rosters.captured_at()
    st.caption("Any NECC team, yours or the one you play next: its roster with the in-game names replays show, "
               f"its division and its record (as of {snapshot_day or 'the snapshot'}). The Windows app picks up "
               "newer rosters when it starts.")
    query = st.text_input("Search teams, schools or players", key="team_search",
                          placeholder="e.g. Akron, Knights Gold, Divine.UA")
    if not query.strip():
        return
    found = rosters.search(query)
    if not found:
        st.info("No NECC team matches. Try the school's name, or a player's in-game name.")
        return
    st.caption(f"{len(found)} team{'s' if len(found) != 1 else ''}" + (", the first 8 shown" if len(found) > 8 else ""))
    for team in found[:8]:
        st.markdown(team_card(team), unsafe_allow_html=True)
        with st.expander(f"{team['name']}'s roster ({sum(p['player'] for p in team['players'])} players)"):
            st.markdown(roster_html(team) or "No players listed yet.", unsafe_allow_html=True)
            st.button("This is my team", key=f"choose:{rosters.team_key(team)}", on_click=_choose, args=(team,))


def refresh_liquipedia() -> None:
    catalog = events.current_catalog()
    if tracker_off():
        st.caption("The public website uses the event list that shipped with this version. The Windows app can "
                   "check Liquipedia for newer events.")
        return
    due = events.next_refresh(catalog)
    left, right = st.columns([1, 2])
    clicked = left.button("Check Liquipedia for new events", disabled=due is not None, key="league_refresh")
    right.caption(f"Liquipedia can be checked again after {due:%b %d, %H:%M}: it asks apps to keep what they "
                  "fetch, so the app checks at most once a day." if due else
                  "Reads only new and still-running events, at Liquipedia's pace: it can take a minute or two.")
    if clicked:
        bar = st.progress(0.0, text="Asking Liquipedia which events are listed…")
        try:
            fresh = events.refresh(lambda done, total: bar.progress(done / total, text=f"Reading events… {done} of {total}"))
        except (OSError, ValueError) as error:
            bar.empty()
            st.error(f"Couldn't check Liquipedia: {error}")
            return
        bar.empty()
        st.session_state["league_note"] = ("success", f"Liquipedia checked: {len(fresh['events'])} events listed.")
        st.rerun()


def necc_directory() -> None:
    """NECC's school directory: every Rainbow Six registration in the LeagueOS season, with each
    school's colors and logo, and roster tracking."""
    if "necc_schools" not in st.session_state:
        st.session_state["necc_schools"] = bundled_school_catalog()
        st.session_state["necc_catalog_source"] = "LeagueOS · Rainbow Six Signups · September 26, 2026 snapshot"
    all_schools = st.session_state["necc_schools"]
    a, b = st.columns(2)
    a.metric("Schools", len(all_schools))
    b.metric("Teams", sum(len(school["teams"]) for school in all_schools))
    with st.expander("Update or import a directory"):
        feed_configured = bool(os.environ.get("NECC_R6_DATA_URL"))
        feed_column, upload_column = st.columns([1, 2])
        with feed_column:
            if st.button("Sync NECC feed", disabled=not feed_configured, type="primary"):
                try:
                    st.session_state["necc_schools"] = fetch_school_catalog()
                    st.session_state["necc_catalog_source"] = "Configured NECC feed"
                    st.rerun()
                except (OSError, ValueError) as error:
                    st.error(f"School feed unavailable: {error}")
        with upload_column:
            uploaded = st.file_uploader("Import school catalog (.json)", type=["json"], key="necc_catalog_upload")
            if uploaded:
                try:
                    raw = uploaded.getvalue()
                    if len(raw) > MAX_CATALOG_BYTES:
                        raise ValueError("The school catalog exceeds the 2 MB limit.")
                    st.session_state["necc_schools"] = normalize_school_catalog(json.loads(raw))
                    st.session_state["necc_catalog_source"] = uploaded.name
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                    st.error(f"Could not import catalog: {error}")

    schools = st.session_state.get("necc_schools", [])
    query = st.text_input("Search school or team", placeholder="School, university, or team name").strip().casefold()
    if query:
        schools = [s for s in schools if query in s["name"].casefold()
                   or any(query in t["name"].casefold() for t in s["teams"])]
        if not schools:
            st.info("No teams match your search. Try another school or team name.")
            return
    if not schools:
        st.info("No school catalog is loaded. Import a school JSON export to browse rosters.")
        return
    st.caption(f"Catalog: {st.session_state.get('necc_catalog_source', 'loaded this session')}")
    school = st.selectbox("School", schools, format_func=lambda s: s["name"], key="necc_school")
    teams = school.get("teams", [])
    if not teams:
        st.warning("This school has no Rainbow Six roster in the loaded catalog.")
        return
    team = st.selectbox("Rainbow Six team", teams, format_func=lambda t: t["name"])
    color = team.get("primary_color") or school.get("primary_color") or "#d49353"
    color = color if re.fullmatch(r"#[0-9a-fA-F]{6}", str(color)) else "#d49353"
    if st.button("Use this team's colors and logo", type="primary"):
        try:
            apply_theme(dict(name=school["name"], team=team["name"], primary=color,
                             secondary=DEFAULT_THEME["secondary"],
                             logo=team.get("logo_url") or school.get("logo_url") or ""))
            st.rerun()
        except OSError as error:
            st.error(f"Could not save appearance: {error}")
    st.page_link("appearance.py", label="Customize colors and upload your logo", icon=":material/palette:")
    logo_column, details_column = st.columns([1, 2])
    with logo_column:
        logo = team.get("logo_url") or school.get("logo_url")
        if isinstance(logo, str) and logo.startswith(("https://", "http://")):
            st.image(logo, width=104)
        st.markdown(
            f'<div style="border-left:3px solid {color};padding:8px 14px;background:#1b2224">'
            f'<div style="font:700 11px IBM Plex Mono,monospace;color:{color};text-transform:uppercase">NECC R6</div>'
            f'<div style="font:600 24px Barlow Condensed,sans-serif;color:#e8ece9">{html.escape(school["name"])}</div>'
            f'<div style="color:#9ba7a4;font-size:13px">{html.escape(team["name"])}</div></div>',
            unsafe_allow_html=True)
    with details_column:
        roster = team.get("roster", [])
        st.metric("Roster", len(roster))
        if roster:
            st.dataframe([{"Player": name} for name in roster], hide_index=True)
            if st.button("Track this roster", disabled=bool(tracker_off()), type="primary"):
                season = st.session_state.get("r6_season", "current")
                with StatsManager(season=season) as manager:
                    manager.add_players(roster, team=team["name"])
                st.success(f"Added {len(roster)} players to {md(team['name'])} for {md(season)}.")
        else:
            st.caption("This snapshot has team registrations, not players: add your players' usernames under "
                       "**Track your players** above.")
        if team.get("division"):
            st.caption(f"Placement group: {team['division']}")


st.markdown("""<style>
.lg-card { border:1px solid var(--border); border-left:4px solid var(--school, var(--accent)); border-radius:4px;
           padding:14px 18px; margin:6px 0 10px; background:rgba(24,30,32,.55); }
.lg-tier { display:inline-block; padding:2px 8px; border:1px solid var(--secondary, var(--accent)); border-radius:2px;
           color:var(--secondary, var(--accent)); font:700 10px var(--mono); letter-spacing:.12em; }
.lg-name { font:600 1.5rem var(--display); color:var(--text); margin:6px 0 8px; line-height:1.15; }
.lg-facts { display:flex; flex-wrap:wrap; gap:8px 26px; }
.lg-fact span { display:block; color:var(--dim); font:500 10px var(--mono); text-transform:uppercase; letter-spacing:.08em; }
.lg-fact b { color:var(--text); font-weight:600; font-size:.92rem; }
</style>""", unsafe_allow_html=True)

team_css()
st.caption("LEAGUES & TOURNAMENTS")
st.title("League & Team")
st.write("Tell the app where your team competes: any Liquipedia event from S- to D-Tier, a NECC division, or a "
         "league of your own.")
if note := st.session_state.pop("league_note", None):
    getattr(st, note[0])(md(note[1]))
saved = current_competition()
if saved["name"]:
    st.caption(f"Saved: **{md(saved['team'] + ' · ' if saved['team'] else '')}{md(saved['name'])}**"
               + (f" ({saved['tier']}-Tier)" if saved["tier"] else ""))

source_names = list(SOURCES)
default_source = next((n for n, s in SOURCES.items() if s == saved["source"]), LIQUIPEDIA)
source = st.radio("Where do you compete?", source_names,
                  index=_default("league_source", source_names.index(default_source)), horizontal=True,
                  key="league_source")
picked = {LIQUIPEDIA: pick_liquipedia, NECC: pick_necc, CUSTOM: pick_custom}[source](saved)

team = ""
if picked:
    team = pick_team(picked, saved)
    roster_team = rosters.find(team, picked.get("division", "")) if picked["source"] == "necc" and team else None
    if roster_team:
        st.markdown(team_card(roster_team, mine=True) + roster_html(roster_team), unsafe_allow_html=True)
        unlinked = [p["name"] for p in roster_team["players"] if not p["ubisoft"]]
        st.caption(f"{len(rosters.in_game_names(roster_team))} players from NECC's roster (as of "
                   f"{rosters.captured_at()}): nothing to type."
                   + (f" {', '.join(unlinked)} haven't linked a Ubisoft name, so they're tracked by their NECC name."
                      if unlinked else ""))
    elif team:
        roster_from_replays(team)
    if team:
        with st.expander("Add players who aren't listed"):
            st.text_area("In-game names", key="league_extra_players", placeholder="One per line: a sub, a new player",
                         help="Their exact Siege usernames, as replays show them.")
        st.checkbox("Track my team's players", value=True, key="league_track",
                    help="Your players become a team in Team Analytics and Ask, and the season tracker's players "
                         "for your team; the Dashboard then picks them in each match for you.")
        if roster_team and (roster_team["color"] or roster_team["logo_url"]):
            st.checkbox("Use my team's colors and logo", value=True, key="league_theme",
                        help="Every page, the banner, the docked panel and the launch screen take your team's "
                             "colors and logo. Change them any time on Team Theme.")
    st.checkbox("Name my season after it", value=bool(saved["season"]) and saved["id"] == picked["id"],
                              key="league_name_season",
                              help="The season tracker counts this league's matches under its name instead of "
                                   "\"current\". You can still type another season name on any page.")
    st.button("Save my team" if team else "Save as my league", type="primary", on_click=save, args=(picked,),
              key="league_save")
if source == LIQUIPEDIA:
    refresh_liquipedia()

st.divider()
find_a_team()
if source == NECC:
    st.divider()
    with st.expander("NECC school directory (sign-ups, school colors and logos)"):
        st.caption("Every Rainbow Six registration in NECC's LeagueOS season, with each school's colors and logo.")
        st.link_button("Open NECC on LeagueOS", "https://necc.v1.leagueos.gg/")
        necc_directory()
st.caption("League, tournament and school names and marks belong to their owners. This independent app isn't "
           "endorsed by Liquipedia, NECC or Ubisoft.")
