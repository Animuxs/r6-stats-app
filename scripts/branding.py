"""Validated team identity (colors, logo) and the league or tournament the team plays in, with
atomic desktop preference persistence."""
from __future__ import annotations

import base64
import html
import io
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image
import streamlit as st
from app_info import is_public_host, is_loopback
from season_stats import DEFAULT_DB_PATH

DEFAULT_THEME = dict(name='R6 Match Stats', team='', primary='#52d5f2', secondary='#f6be4f', logo='')
# the default theme's name before 1.6.0, when the app was NECC's only: a saved theme may still carry it
OLD_DEFAULT_NAMES = {'Siege / NECC'}
# where the team plays: a Liquipedia event, a NECC division, or a league the team named itself
DEFAULT_COMPETITION = dict(source='', id='', name='', tier='', team='', url='', season='')
COMPETITION_SOURCES = {'', 'liquipedia', 'necc', 'custom'}


def clean_theme(value):
    value = value if isinstance(value, dict) else {}
    result = {key: str(value.get(key, default))[:180] for key, default in DEFAULT_THEME.items() if key != 'logo'}
    for key in ('primary', 'secondary'):
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', result[key]):
            result[key] = DEFAULT_THEME[key]
    logo = str(value.get('logo', ''))
    if urlparse(logo).scheme == 'https' and urlparse(logo).netloc:
        result['logo'] = logo[:2048]
    elif logo.startswith('data:image/png;base64,') and len(logo) < 3_000_000:
        try:
            raw = base64.b64decode(logo.split(',', 1)[1], validate=True)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != 'PNG' or max(image.size) > 1024:
                    raise ValueError('Invalid logo')
                image.verify()
            result['logo'] = logo
        except (ValueError, OSError):
            result['logo'] = ''
    else:
        result['logo'] = ''
    return result


def preference_path():
    return Path(DEFAULT_DB_PATH).parent / 'appearance.json'


def load_theme(path=None):
    try:
        theme = clean_theme(json.loads((path or preference_path()).read_text(encoding='utf-8')))
    except (OSError, ValueError):
        return dict(DEFAULT_THEME)
    if theme['name'] in OLD_DEFAULT_NAMES:
        theme['name'] = DEFAULT_THEME['name']
    return theme


def _write_json(value, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(value, file, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_theme(value, path=None):
    _write_json(clean_theme(value), path or preference_path())


def clean_competition(value):
    value = value if isinstance(value, dict) else {}
    result = {key: str(value.get(key) or default).strip()[:180] for key, default in DEFAULT_COMPETITION.items()}
    if result['source'] not in COMPETITION_SOURCES:
        result['source'] = ''
    if result['tier'] not in ('', 'S', 'A', 'B', 'C', 'D'):
        result['tier'] = ''
    url = str(value.get('url') or '')
    result['url'] = url[:2048] if urlparse(url).scheme == 'https' and urlparse(url).netloc else ''
    if not result['name']:
        return dict(DEFAULT_COMPETITION)
    return result


def competition_path():
    return Path(DEFAULT_DB_PATH).parent / 'competition.json'


def load_competition(path=None):
    try:
        return clean_competition(json.loads((path or competition_path()).read_text(encoding='utf-8')))
    except (OSError, ValueError):
        return dict(DEFAULT_COMPETITION)


def save_competition(value, path=None):
    _write_json(clean_competition(value), path or competition_path())


def local_preferences():
    return not is_public_host() and is_loopback(st.context.ip_address)


def current_theme():
    from team_runtime import shared_active, read_document
    if shared_active():
        return clean_theme(read_document('theme'))
    if 'appearance' not in st.session_state:
        st.session_state['appearance'] = load_theme() if local_preferences() else dict(DEFAULT_THEME)
    return clean_theme(st.session_state['appearance'])


def apply_theme(value):
    value = clean_theme(value)
    from team_runtime import shared_active, write_document
    if shared_active():
        write_document('theme', value)
    elif local_preferences():
        save_theme(value)
    st.session_state['appearance'] = value
    st.session_state['selected_school_name'] = value['name']
    st.session_state['selected_school_color'] = value['primary']
    st.session_state['selected_school_team'] = value['team']


def uploaded_logo(raw):
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('Choose an image smaller than 2 MB.')
    with Image.open(io.BytesIO(raw)) as image:
        if image.format not in ('PNG', 'JPEG', 'WEBP') or max(image.size) > 4096:
            raise ValueError('Use a PNG, JPEG or WebP image up to 4096 pixels.')
        image = image.convert('RGBA')
        image.thumbnail((512, 512))
        output = io.BytesIO()
        image.save(output, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode('ascii')


def current_competition():
    """The league or tournament this team plays in: saved on this PC in the Windows app (and a
    local checkout), kept for the browser session on the public website."""
    from team_runtime import shared_active, read_document
    if shared_active():
        return clean_competition(read_document('competition'))
    if 'competition' not in st.session_state:
        st.session_state['competition'] = load_competition() if local_preferences() else dict(DEFAULT_COMPETITION)
    return clean_competition(st.session_state['competition'])


def apply_competition(value):
    value = clean_competition(value)
    from team_runtime import shared_active, write_document
    if shared_active():
        write_document('competition', value)
    elif local_preferences():
        save_competition(value)
    st.session_state['competition'] = value
    if value['season']:
        st.session_state['r6_season'] = value['season']


def competition_badge(competition):
    """The banner's corner badge: the event's tier, NECC, or the app's own."""
    if competition['tier']:
        return f"{competition['tier']}-TIER EVENT"
    if competition['source'] == 'necc':
        return 'NECC / COLLEGIATE'
    return 'LEAGUE' if competition['name'] else 'TEAM OPERATIONS'


def readable_accent(color):
    channels = [int(color[n:n+2], 16) for n in (1, 3, 5)]
    # Keep the exact school color on decoration; lighten only text for dark surfaces.
    return '#' + ''.join(f'{max(c, 145):02x}' for c in channels)


def render_identity(banner=True):
    """The school theme's colors on every page, and (unless banner=False, as in the docked view)
    its banner with the school's name and logo."""
    theme = current_theme()
    accent = readable_accent(theme['primary'])
    st.markdown(f'''<style>
    :root {{ --accent:{accent}; --school:{theme['primary']}; --secondary:{theme['secondary']};
      --bg:#080e19; --panel:#101d2c; --row:#152437; --border:#293b51; --text:#f2f6fb; --dim:#a8b8ca; }}
    .stApp {{background:radial-gradient(ellipse at 95% 0%,{theme['primary']}30,transparent 45%),#080e19;}}
    header[data-testid="stHeader"] {{background:#080e19e8;}}
    section[data-testid="stSidebar"] {{background:#101a2a;}}
    h1,h2,h3 {{text-transform:uppercase;letter-spacing:.035em!important;}}
    h1 {{font-size:2rem!important;}}
    [data-testid="stMetric"] {{background:linear-gradient(125deg,#17283b,#0c1624);border:1px solid #293b51;border-top:3px solid var(--school);border-radius:3px;}}
    [data-testid="stMetricValue"] {{color:var(--accent);font-size:clamp(20px,2.4vw,32px);}}
    [data-testid="stTabs"] button[aria-selected="true"] {{color:var(--accent);}}
    button[kind="primary"],button[kind="primary"]:hover {{background:var(--accent);border-color:var(--accent);color:#080e19;}}
    .scorecard {{background:linear-gradient(115deg,#182a3f,#0d1725);border-left:4px solid var(--school);}}
    table.pl th {{background:#101d2c;color:#b3c4d7;}} table.pl td {{border-color:#293b51;}}
    table.pl tr:nth-child(even) td {{background:#152437;}}
    .identity-banner {{display:flex;align-items:center;gap:20px;border:0;border-bottom:1px solid var(--border);padding:18px 0;background:transparent;margin:0 0 18px;position:relative;overflow:hidden;}}
    .identity-banner:after {{content:none;position:absolute;right:30px;top:-30px;width:70px;height:230px;transform:rotate(25deg);background:var(--school);opacity:.15;}}
    .identity-banner img {{width:64px;height:64px;object-fit:contain;}}
    .identity-kicker {{color:var(--accent);font:700 11px monospace;letter-spacing:.2em;}}
    .identity-name {{font:600 clamp(22px,3vw,30px) 'Barlow Condensed','Arial Narrow',sans-serif;color:#f2f6fb;line-height:1.1;margin:7px 0;}}
    .identity-sub {{color:#a8b8ca;font-size:13px;}}
    .identity-badge {{margin-left:auto;border:1px solid var(--secondary);color:var(--secondary);border-left:3px solid var(--secondary);background:var(--panel);padding:8px 12px;font:700 11px monospace;white-space:nowrap;}}
    @media(max-width:700px) {{.identity-banner {{padding:18px 14px;gap:12px;}}.identity-badge {{display:none;}}h1 {{font-size:2rem!important;}}}}
    </style>''', unsafe_allow_html=True)
    if theme['logo']:
        st.logo(theme['logo'], size='large')  # your team's logo atop the sidebar, on every page
    if not banner:
        return
    competition = current_competition()
    logo = f'<img src="{html.escape(theme["logo"], quote=True)}" alt="Team logo">' if theme['logo'] else ''
    kicker = f"RAINBOW SIX SIEGE / {competition['name'].upper()}" if competition['name'] else 'RAINBOW SIX SIEGE'
    sub = theme['team'] or competition['team'] or 'Match intelligence · Roster operations · Season performance'
    st.markdown(f'''<div class="identity-banner">{logo}<div><div class="identity-kicker">{html.escape(kicker)}</div>
    <div class="identity-name">{html.escape(theme['name'])}</div>
    <div class="identity-sub">{html.escape(sub)}</div></div>
    <div class="identity-badge">{html.escape(competition_badge(competition))}</div></div>''', unsafe_allow_html=True)
