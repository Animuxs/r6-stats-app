"""Shared visual tokens for desktop, hosted and standalone browser apps."""

TOKENS_CSS = """:root {
  --bg:#080e19; --panel:#101d2c; --row:#152437; --border:#293b51;
  --text:#f2f6fb; --dim:#a8b8ca; --accent:#52d5f2; --secondary:#f6be4f; --gold:var(--secondary);
  --school:#52d5f2; --atk:#e8924a; --def:#6aa4dd; --pos:#86b99f; --neg:#e17e69;
  --display:'Barlow Condensed','Arial Narrow','Segoe UI Semibold',sans-serif;
  --sans:'IBM Plex Sans','Segoe UI',system-ui,sans-serif;
  --mono:'IBM Plex Mono','Cascadia Mono',Consolas,ui-monospace,monospace;
  color-scheme:dark;
}"""

FONT_CSS = "@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap');\n"

COMPONENT_CSS = """.stApp { background:linear-gradient(122deg, #101416 0%, #171d1f 58%, #14191a 100%); color:var(--text); }
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
.sc-side { display:flex; align-items:center; gap:12px; flex:1 1 0; min-width:0; }
.sc-logo { width:46px; height:46px; object-fit:contain; flex:none; }
.sc-you { display:inline-block; margin-top:3px; padding:1px 6px; border:1px solid var(--secondary, var(--accent));
          color:var(--secondary, var(--accent)); font:700 9px var(--mono); letter-spacing:.1em; border-radius:2px; }
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

"""

STREAMLIT_OVERRIDES = """

.stApp {background:radial-gradient(ellipse at 92% 0%,#52d5f221,transparent 42%),var(--bg);}
.stMainBlockContainer {max-width:none;width:100%;padding:1.1rem clamp(16px,2.2vw,48px) 4rem;}
@media(max-width:1100px){[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .matchday-panel){flex-wrap:wrap;}[data-testid="stColumn"]:has(.matchday-panel),[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .matchday-panel)>[data-testid="stColumn"]{width:100%!important;flex:1 1 100%!important;}}
h1,h2,h3,h4 {text-transform:uppercase;letter-spacing:.03em!important;line-height:1.1!important;}
h1 {font-size:2rem!important;}
header[data-testid="stHeader"] {background:#080e19e8;}
[data-testid="stTopNav"] {border-bottom:1px solid var(--border);background:var(--bg);}
[data-testid="stTopNav"] a, [data-testid="stTopNavSection"] p {font-family:var(--display);text-transform:uppercase;letter-spacing:.06em;}
[data-testid="stMetric"] {border-top:3px solid var(--school);background:linear-gradient(125deg,#17283b,#0c1624);}
.scorecard {background:var(--panel);border-left:3px solid var(--school);box-shadow:none;padding:16px 22px;}
.map-pill {background:var(--row);}
table.pl th {background:var(--panel);} table.pl td {border-color:var(--border);}
table.pl tr:nth-child(even) td {background:var(--row);}
button:focus-visible,a:focus-visible {outline:2px solid var(--accent);outline-offset:2px;}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important;}}
"""

MATCHDAY_CSS = '.matchday-panel section {background:var(--panel);border:1px solid var(--border);border-radius:4px;padding:16px;margin-bottom:16px;}\n.matchday-panel h3 {font-size:1.2rem!important;margin:0 0 8px;}\n.md-label,.md-empty,.md-notes {font-size:.82rem!important;color:var(--dim);}\n.md-label {margin-bottom:12px!important;}\n.md-fixture {padding:12px 0;border-bottom:1px solid var(--border);}\n.md-fixture b,.md-fixture small {display:block;}\n.md-fixture small {color:var(--dim);font-size:.75rem;}\n.md-fixture b {margin:5px 0;}\n.md-recent,.md-person {display:flex;align-items:center;justify-content:space-between;gap:8px;padding:10px 0;border-bottom:1px solid var(--border);font-size:.82rem;}\n.md-recent small {display:block;color:var(--dim);font-size:.72rem;}\n.md-result {color:var(--accent);font-family:var(--mono);}\n.md-person small {color:var(--secondary);font-size:.73rem;}\n.md-notes {overflow-wrap:anywhere;}\n'

# the assistant's panel (assistant_ui.py), floating in the bottom-right corner of every page
ASSISTANT_CSS = """
.st-key-r6_assistant {position:fixed;right:max(16px,env(safe-area-inset-right,0px));bottom:max(16px,env(safe-area-inset-bottom,0px));
  z-index:999990;width:min(430px,calc(100vw - 32px));max-height:calc(100vh - 88px);overflow-y:auto;gap:.45rem;
  background:var(--panel);border:1px solid var(--border);border-top:3px solid var(--accent);border-radius:6px;
  padding:10px 12px 12px;box-shadow:0 18px 48px rgba(0,0,0,.5);}
.st-key-r6_assistant:has(.st-key-assistant_launch) {width:auto;padding:0;background:none;border:none;box-shadow:none;overflow:visible;}
.st-key-assistant_launch button {border-radius:999px;padding:.55rem 1.1rem;background:var(--accent);border-color:var(--accent);
  color:#080e19;font-weight:600;box-shadow:0 10px 28px rgba(0,0,0,.45);}
.st-key-assistant_launch button:hover {filter:brightness(1.08);color:#080e19;border-color:var(--accent);}
.st-key-r6_assistant .r6-assistant-sub {color:var(--dim);font-size:.78rem;}
.st-key-r6_assistant [data-testid="stChatMessage"] {padding:.45rem .55rem;background:var(--row);border-radius:6px;}
.st-key-r6_assistant [data-testid="stChatMessage"] p, .st-key-r6_assistant [data-testid="stChatMessage"] li {font-size:.88rem;}
.st-key-r6_assistant [data-testid="stChatMessage"] h3 {font-size:1.05rem!important;margin:0 0 .3rem;}
.st-key-r6_assistant button[kind="secondary"] {font-size:.8rem;min-height:0;padding:.25rem .6rem;text-align:left;}
.st-key-r6_assistant [data-testid="stChatInput"] textarea {font-size:.9rem;}
@media(max-width:640px){.st-key-r6_assistant {max-height:calc(100vh - 72px);}}
@media print {.st-key-r6_assistant {display:none;}}
"""

STREAMLIT_CSS = FONT_CSS + TOKENS_CSS + COMPONENT_CSS + STREAMLIT_OVERRIDES + MATCHDAY_CSS + ASSISTANT_CSS


def render_styles():
    import streamlit as st
    st.markdown("<style>" + STREAMLIT_CSS + "</style>", unsafe_allow_html=True)
