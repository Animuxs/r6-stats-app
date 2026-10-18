"""
assistant_knowledge.py
======================
What the assistant and the Ask page know besides your stats: how to use each part of the app, what
each stat means, and what to do about a weak one. Each entry has the words that bring it up; the
entry whose words match a question best wins (knowledge_answer). It's all plain text on this PC.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ask_engine import fold


@dataclass(frozen=True)
class Topic:
    key: str
    title: str
    words: tuple[str, ...]          # phrases that bring it up, matched as whole words
    text: str                       # the answer, in Markdown
    page: str | None = None         # the tab it's about (a key of tab.PAGES), for a link
    follow_ups: tuple[str, ...] = field(default_factory=tuple)


# ------------------------------------------------------------------ the app --

GUIDE: list[Topic] = [
    Topic("load", "Adding your matches",
          ("load replays", "load a replay", "load my replays", "add matches", "add a match", "add my matches",
           "import", "upload", "get started", "getting started", "start", "first time", "set up", "setup",
           "no matches", "where do i begin", "how do i begin", "open a match", "open a replay", "confirm"),
          "Matches get into your stats from the **Dashboard**:\n"
          "1. Open a match: the Windows app finds Siege's replay folder by itself; on the website, upload a "
          "match's replay folder (zipped) or its `.rec` files.\n"
          "2. Check the scoreboard, then press **Confirm match and players** and tick the players whose stats "
          "to keep. Strangers from Ranked aren't counted unless you tick them.\n\n"
          "After that the match shows up on Ask, Match History, Team Analytics and the rest. No replays "
          "handy? Try the **demo season** from the sidebar.",
          "dashboard", ("Where are my replays?", "What's the demo season?")),
    Topic("replays", "Where Siege keeps replays",
          ("where are my replays", "replay folder", "matchreplay", "find my replays", "replays folder",
           "where are replays", "rec files", "rec file", "no replays", "match replay"),
          "Siege saves each match in a **MatchReplay** folder inside the game's install folder, one folder per "
          "match:\n"
          "- Steam: `C:\\Program Files (x86)\\Steam\\steamapps\\common\\Tom Clancy's Rainbow Six Siege\\"
          "MatchReplay`\n"
          "- Ubisoft Connect: `C:\\Program Files (x86)\\Ubisoft\\Ubisoft Game Launcher\\games\\"
          "Tom Clancy's Rainbow Six Siege\\MatchReplay`\n\n"
          "None there? Turn on **Match Replay** in the game's options and play a match.",
          "download", ("How do I add my matches?",)),
    Topic("dashboard", "The Dashboard",
          ("dashboard", "scoreboard", "match report", "round by round", "export", "csv", "json",
           "scoreboard image", "share a scoreboard", "download the scoreboard"),
          "The **Dashboard** shows one match: a pro-league style scoreboard for every player, the "
          "round-by-round breakdown, and CSV, JSON and image exports. It's also where you **confirm** a match "
          "so it counts in your stats.",
          "dashboard", ("How do I add my matches?", "What is EPS?")),
    Topic("ask", "The Ask page",
          ("ask page", "ask tab", "questions", "what can i ask", "example questions", "plain english"),
          "**Ask** answers plain-English questions about every match in your stats: trends (“my K/D over my "
          "last 10 matches”), rankings (“top 5 players by EPS”), comparisons (“attack vs defense”), records, "
          "counts and match lists, with a table and a chart. If it can't answer something the way you asked, "
          "press **Report** so the developers can add it.",
          "ask", ("What's my best map?", "Top 5 players by EPS")),
    Topic("charts", "Charts",
          ("charts", "chart", "graph", "graphs", "plot", "visualize", "visualise", "visual", "pie chart",
           "heatmap", "scatter", "histogram"),
          "**Charts** draws any stat any way. Type what you want to see, like “my K/D over time vs Sacri”, "
          "“pie of the operators I play”, “heatmap of win rate by map and side” or “scatter K/D vs KOST for every "
          "player”, or pick the chart, stat, grouping, players and filters from the menus. Lines, areas, bars, "
          "stacked bars, pies, scatters, heatmaps, histograms and box plots, with the numbers as a CSV. You can also "
          "just ask me for a chart.",
          "charts", ("My K/D over time", "Pie of the operators I play")),
    Topic("history", "Match History",
          ("match history", "history", "past matches", "old matches", "tag a match", "tags", "notes", "note",
           "video link", "delete a match", "remove a match", "scrim tag", "edit a match"),
          "**Match History** lists every match in your stats, newest first, with your result. Open a match to "
          "give it a tag (Official, Scrim, Practice, Casual), a note or a video link, change its tracked "
          "players, open it on the Dashboard or remove it. Ask understands tags: “my K/D in scrims”.",
          "history", ("My record in scrims",)),
    Topic("teams", "Team Analytics",
          ("team analytics", "build a team", "make a team", "create a team", "roster", "season teams",
           "map pool", "veto", "team stats"),
          "**Team Analytics** has three parts:\n"
          "- **Build a team**: type your players' names and see how the team does together, with each "
          "player's stats. Saved teams are known to Ask by name.\n"
          "- **Season teams**: the season tracker's teams.\n"
          "- **Map pool**: each map's record, attack and defense win rates and best and worst sites, with a "
          "veto hint.",
          "teams", ("Analyze my team", "What's my best map?")),
    Topic("operators", "Operator Analytics",
          ("operator analytics", "operators page", "operator stats", "site performance"),
          "**Operator Analytics** shows how each operator does over all of a player's matches, or in the match "
          "open on the Dashboard, plus how each bomb site goes.",
          "operators", ("Which operators do I play most?", "My best operators")),
    Topic("matchday", "Match day",
          ("match day", "matchday", "upcoming match", "schedule", "availability", "plan a match", "fixture"),
          "**Match day** is for planning: add upcoming matches, track who's available and keep preparation "
          "notes. The panel on the right of every page shows what's next.",
          "matchday"),
    Topic("team_hub", "Team hub",
          ("team hub", "team login", "sign in", "log in", "login", "team account", "members", "roles",
           "recover login"),
          "**Team hub** gives your team one shared login so everyone sees the same stats and plans, with "
          "member profiles and roles. Nothing goes to an outside account provider.",
          "team"),
    Topic("league", "League & Team",
          ("league", "tournament", "necc", "liquipedia", "my team", "pick my team", "division", "find a team",
           "school directory"),
          "**League & Team** is where you say which league or tournament you play in and which team is yours. "
          "A NECC team comes with its roster, colors and logo, so the app sets itself up with nothing typed.",
          "league"),
    Topic("theme", "Team Theme",
          ("theme", "colors", "colours", "logo", "appearance", "school colors", "branding", "launch chime",
           "dark mode"),
          "**Team Theme** changes the app's colors, names and logo, starting from your team or a NECC school. "
          "In the Windows app it also has desktop options like the launch chime.",
          "theme"),
    Topic("download", "The Windows app",
          ("windows app", "download", "install", "installer", "update", "updates", "new version",
           "browser edition", "portable", "uninstall", "is it safe", "virus", "signed"),
          "**Get the Windows app** has the installer, a portable zip and a browser edition, plus checksums to "
          "check the download. The Windows app checks GitHub for a newer version and offers it.",
          "download"),
    Topic("dock", "Docking the window",
          ("dock", "docked", "side of the screen", "next to the game", "overlay", "second monitor"),
          "In the Windows app, open the sidebar and press **Dock left** or **Dock right**: the app shrinks to a "
          "narrow panel at the edge of your screen, next to the game or your other apps.",
          None),
    Topic("demo", "The demo season",
          ("demo", "demo season", "sample data", "made up matches", "try it", "example data", "fake matches"),
          "The **demo season** fills every page with twelve made-up league matches so you can try everything "
          "without replays. Your own stats aren't touched. Turn it on or off from the sidebar.",
          None, ("How do I add my matches?",)),
    Topic("assistant", "The assistant",
          ("assistant", "chatbot", "chat bot", "turn off", "turn on", "hide the assistant", "disable",
           "what can you do", "help me", "who are you", "what are you", "how do you work", "commands"),
          "I'm the R6 Match Stats assistant. I can:\n"
          "- **Answer questions about your matches**, anything the Ask page answers: “my K/D on Bank”, "
          "“compare me and Paltry”, “who's our best entry?”\n"
          "- **Analyze** a player or a team: “analyze my stats”, “analyze Paltry”, “analyze my team”, "
          "“what should I work on?”\n"
          "- **Guide you around the app**: “how do I add my matches?”, “where are my replays?”\n"
          "- **Explain stats**: “what is KOST?”\n\n"
          "I remember the conversation, so you can follow up with “what about on defense?” or “and Paltry?”. "
          "Turn me off from the sidebar. Everything stays on this computer.",
          None, ("Analyze my stats", "What should I work on?", "How do I add my matches?")),
    Topic("report", "Reporting a question",
          ("report", "report a question", "report a bug", "bug", "feedback", "wrong answer", "suggest",
           "feature request", "developers", "contact"),
          "When I or the Ask page can't answer a question, or answer it the wrong way, press **Report**. "
          "The question is saved on this computer, and you can send it to the developers as a GitHub issue "
          "(you see and can edit it first). Your stats aren't included.",
          "ask"),
    Topic("privacy", "Your data",
          ("privacy", "my data", "data", "online", "offline", "internet", "ubisoft account", "ban", "banned",
           "cheat", "cheating", "anti cheat", "battleye", "safe to use"),
          "R6 Match Stats only reads the replay files Siege has already saved. It never connects to the game, "
          "Ubisoft's servers or your account, never touches the game while it runs, and gives no advantage in a "
          "match. The Windows app keeps everything on your PC and only asks GitHub whether there's a newer "
          "version. It's an unofficial fan project, not made or endorsed by Ubisoft.",
          None),
]

# ------------------------------------------------------------- the stats --

GLOSSARY: list[Topic] = [
    Topic("eps", "EPS", ("eps", "rating", "performance score"),
          "**EPS** is a whole-match performance score: **100 is the average player in that match**, higher is "
          "better. It weighs kills, deaths, entries, KOST, survival, multikills, clutches, objectives and "
          "trades against everyone else in the match, so a player who wins rounds without many kills can still "
          "rate well. Ubisoft hasn't published its formula, so this one is the app's own."),
    Topic("kd", "K/D", ("kd", "kd ratio", "kdr", "kill death ratio", "kill death"),
          "**K/D** is kills divided by deaths. **1.00** means you trade evenly; above 1 you win more fights "
          "than you lose. It doesn't count objectives or trades, so read it next to KOST and EPS."),
    Topic("kost", "KOST", ("kost",),
          "**KOST** is the share of rounds where you got a **K**ill, the **O**bjective (plant or defuse), "
          "**S**urvived, or were **T**raded. It measures how often you're useful in a round: around **60–65%** "
          "is solid, 70%+ is excellent."),
    Topic("kpr", "KPR", ("kpr", "kills per round"),
          "**KPR** is kills per round. Around **0.7–0.8** is solid; 1.0+ is a fragger's number."),
    Topic("survival", "Survival", ("survival", "srv", "survival rate"),
          "**Survival** is the share of rounds you were still alive at the end. High survival with low kills can "
          "mean you're playing too passively; low survival can mean taking fights without support."),
    Topic("entry", "Entry", ("entry", "entries", "entry kill", "entry death", "opening kill", "opening duel",
                             "first blood", "entry differential"),
          "An **entry** is the round's first kill (entry kill) or first death (entry death). **Entry +/-** is "
          "entry kills minus entry deaths. Winning the opening duel wins the round far more often, so a "
          "negative entry number costs your team rounds."),
    Topic("clutch", "Clutch", ("clutch", "clutches", "1vx", "1v2", "1v3"),
          "A **clutch** is a round you win as your team's last player alive against one or more opponents "
          "(1vX)."),
    Topic("trade", "Trades", ("trade", "trades", "trade kill", "traded", "refrag"),
          "A **trade kill** is killing the opponent who just killed your teammate, within a few seconds. "
          "**Deaths traded** counts your deaths a teammate avenged: a high number means you're dying in places "
          "your team can cover."),
    Topic("hs", "Headshot %", ("headshot", "headshots", "hs", "headshot rate", "headshot percentage"),
          "**Headshot %** is the share of your kills that were headshots. 40–50% is typical; higher usually means "
          "good crosshair placement."),
    Topic("multikill", "Multikills", ("multikill", "multikills", "ace", "aces", "4k", "5k"),
          "A **multikill round** is a round with two or more kills; an **ace** is all five."),
    Topic("win_rate", "Win rate", ("win rate", "winrate", "win percentage", "round win rate"),
          "**Win %** is the share of matches won. Split by map, side, site or operator it counts **rounds** "
          "won instead, since a match has many of those."),
]

# ------------------------------------------------------------ coaching tips --

# what to work on when a stat is weak, keyed by the metric
TIPS: dict[str, str] = {
    "kd": "Take fewer 50/50 fights: hold angles your teammates can trade from, and reposition after a kill "
          "instead of re-peeking the same spot.",
    "kost": "Find a way to matter every round: play for the trade, carry the defuser, or stay alive with "
            "utility left. A round with nothing in it is a round your team plays 4v5.",
    "survival": "You're dying a lot. Pre-place your crosshair, use drones and cams before you swing, and fall "
                "back when a fight goes wrong instead of finishing it alone.",
    "entry": "You're losing opening duels. Entry with a teammate behind you so your death can be traded, use a "
             "flash or drone first, or leave first contact to your entry fragger.",
    "hs": "Work on crosshair placement: keep it at head height on the angles you clear, and practice "
          "tap-firing at range.",
    "kpr": "Look for more impact: play closer to the action, and take the fights your team sets up for you.",
    "clutches": "In 1vX, slow down: play the clock, the defuser and sound rather than hunting.",
    "win_rate": "Look at where the rounds go: your map pool and attack vs defense split show which maps and "
                "sides to practice or ban.",
}


# ---------------------------------------------------------------- matching --

def _norm(text: str) -> str:
    text = re.sub(r"\bk\s*[/\-]\s*d\b", "kd", fold(text))
    return " " + re.sub(r"[^0-9a-z]+", " ", text).strip() + " "


def _score(topic: Topic, text: str) -> int:
    """How well a topic matches a question: the length of its longest phrase in it (0 if none)."""
    return max((len(w) for w in topic.words if f" {_norm(w).strip()} " in text), default=0)


_DEFINE = re.compile(r"\b(what (is|are|s|does|do)|whats|define|definition|meaning|mean|means|explain|"
                     r"stand for|stands for|how is .* (worked out|calculated|counted)|how do you (count|calculate))\b")
_HOW_TO = re.compile(r"\b(how (do|can|to|should)|where (do|can|is|are)|can i|help|guide|walk me|show me how|"
                     r"tutorial|what does .* page|what is the .* (page|tab)|whats the .* (page|tab)|"
                     r"turn (on|off)|is it|does it|will i)\b")


def define(question: str) -> Topic | None:
    """The stat a "what is KOST?" question asks about, if it's one of those."""
    text = _norm(question)
    if not _DEFINE.search(text.strip()):
        return None
    best = max(GLOSSARY, key=lambda t: _score(t, text))
    return best if _score(best, text) else None


def guide(question: str, any_wording: bool = False) -> Topic | None:
    """The part of the app a "how do I...?" question asks about. With any_wording, a question that just
    names a part of the app ("match history") counts too."""
    text = _norm(question)
    if not (any_wording or _HOW_TO.search(text.strip())):
        return None
    best = max(GUIDE, key=lambda t: _score(t, text))
    return best if _score(best, text) >= 4 else None


def page_topic(page: str) -> Topic | None:
    """The guide to one tab (a key of tab.PAGES)."""
    return next((t for t in GUIDE if t.page == page), None)


def topic(key: str) -> Topic:
    return next(t for t in GUIDE + GLOSSARY if t.key == key)
