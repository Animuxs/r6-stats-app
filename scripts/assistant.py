"""
assistant.py
============
The assistant's brain, shared by the chat panel on every page (assistant_ui.py) and the Ask page.
Whatever is asked, it gives an answer: one of these, tried in order.

    small talk      "hi", "thanks", "what can you do?"
    a chart         "chart my K/D over time", "pie of my operators" (chart_engine.py)
    analysis        "analyze my stats", "analyze Paltry", "analyze my team", "analyze my last match",
                    "what should I work on?": strengths, what to work on and why, form, maps, operators
    a stat          "what is KOST?" (assistant_knowledge.GLOSSARY)
    the app         "how do I add my matches?" (assistant_knowledge.GUIDE)
    more stats      win and loss streaks, when you last played, who you win most with
    your stats      anything the Ask engine answers (ask_engine.ask)
    a best guess    what it could work out (a player, map or operator it recognized, a part of the app,
                    a stat it knows), or the closest questions it can answer

A reply says whether the question was really answered; one that wasn't (or that the user says missed
the point) can be reported to the developers (question_reports.py).

A Conversation remembers what was asked, so follow-ups work: "what about on defense?", "and Paltry?",
"how does he do on Bank?", "why?".
It all runs on this PC: no AI service, nothing sent anywhere.
"""

from __future__ import annotations

import ast
import difflib
import operator as op
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import assistant_knowledge as kb
import chart_engine
from ask_engine import (METRIC_WORDS, METRICS, Answer, Query, Vocab, _aggregate, _clean,
                        _find_metrics, ask, fmt, fold, interpret, operator_table, rate, suggestions,
                        team_report, team_summary)
from map_pool import map_pool, veto_hint
from stats_db import StatsDB, nice_time


@dataclass
class Reply:
    """One answer, in Markdown, with what goes with it."""
    text: str
    kind: str = "stats"            # stats, analysis, define, guide, smalltalk, fallback, empty
    answered: bool = True          # False: it couldn't really answer, so it offers to report the question
    answer: Answer | None = None   # the Ask engine's answer, for its table and chart
    rows: list[dict[str, Any]] = field(default_factory=list)  # a table that isn't the Ask engine's
    columns: list[str] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)  # (tab key in tab.PAGES, label)
    suggestions: list[str] = field(default_factory=list)       # follow-up questions to offer
    question: str = ""             # what was asked
    resolved: str = ""             # the question as it was looked up, after reading it as a follow-up
    chart: chart_engine.Chart | None = None  # a chart to draw

    @property
    def table(self) -> tuple[list[str], list[dict[str, Any]], dict[str, str]]:
        """(columns, rows, formats) of its table, if it has one."""
        if self.answer and self.answer.ok and self.answer.rows:
            return self.answer.columns, self.answer.rows, self.answer.formats
        return self.columns, self.rows, {}

    def next_questions(self) -> list[str]:
        """Its suggestions, without the question just asked."""
        asked = {fold(self.question).strip(" ?!."), fold(self.resolved).strip(" ?!.")}
        return [s for s in self.suggestions if fold(s).strip(" ?!.") not in asked]

    def summary(self) -> str:
        """The reply in one line, for a report."""
        return re.sub(r"\s+", " ", re.sub(r"[*_`#>]", "", self.text)).strip()[:300]


# ------------------------------------------------------------- small talk --

_GREETING = re.compile(r"^(hi|hey|hello|yo|sup|hiya|howdy|good (morning|afternoon|evening)|what'?s up|wassup)\b")
_THANKS = re.compile(r"\b(thanks|thank you|thx|ty|cheers|appreciate it|great job|nice one|awesome|perfect)\b")
_BYE = re.compile(r"^(bye|goodbye|see you|see ya|later|cya|good night|gn)\b")
_HOW_ARE_YOU = re.compile(r"\b(how are you|how are u|how r u|hows it going|how is it going|you ok)\b")
_YES_NO = re.compile(r"^(yes|yeah|yep|sure|ok|okay|no|nope|nah|cool|k|lol|lmao|haha|hmm+)\W*$")
_JOKE = re.compile(r"\b(joke|funny|make me laugh)\b")

_ANALYZE = re.compile(r"\b(analy[sz]e|analysis|break ?down|breakdown|strengths?|weakness(es)?|weak spots?|"
                      r"what should (i|we) (work on|improve|practice|focus on)|work on|improve|improvement|"
                      r"get better|tips?|advice|coach(ing)?|scout(ing)?|review|feedback on (my|our)|"
                      r"what am i (doing wrong|bad at|good at)|how can (i|we) (win|do better|be better|improve)|"
                      r"insights?|evaluate|evaluation|assess(ment)?|deep dive)\b")
_MATCH_WORDS = re.compile(r"\b(last|latest|recent|this|that|previous|my last|our last) (match|game)\b|"
                          r"\b(this|the) (match|game)\b")
_TEAM_WORDS = re.compile(r"\b(my team|our team|the team|we|us|our|squad|roster)\b")
_ME = re.compile(r"\b(i|me|my|mine|myself|im)\b")
_PRONOUNS = re.compile(r"\b(he|she|they|him|her|them|his|hers|their|theirs)\b", re.I)
_FOLLOW_UP = re.compile(r"^(and|what about|how about|same for|same but|now|ok and|okay and|also|but|"
                        r"what if|then|for)\b[\s,]*", re.I)
_WHY = re.compile(r"^(why|how come|how did you (get|work out|figure) (that|this)|explain( that| this)?|"
                  r"where did (that|this) come from|how do you know)\W*$", re.I)
_MORE = re.compile(r"^(more|show more|tell me more|details|more details|go on|elaborate|expand)\W*$", re.I)
_CHART = re.compile(r"\b(chart|charts|graph|graphs|plot|plots|visuali[sz]e|visuali[sz]ation|pie|donut|heat ?map|"
                    r"scatter|histogram|box ?plot|bar chart|line chart|diagram)\b")
_BAN = re.compile(r"\b(ban|bans|banning|veto|vetoes|vetoing|avoid|dodge|pick|picks|first pick)\b.*\bmaps?\b|"
                  r"\bmaps?\b.*\b(ban|veto|avoid|dodge|pick)\b")
_TIME_OF_DAY = re.compile(r"\b(morning|afternoon|evening|night|late|what time|time of day|which day|what day|"
                          r"weekdays?|weekends?|mondays?|tuesdays?|wednesdays?|thursdays?|fridays?|saturdays?|"
                          r"sundays?|day of the week)\b")
_CONSISTENT = re.compile(r"\b(consistent|consistency|inconsistent|steady|streaky|up and down|reliable|variance|"
                         r"volatile|swingy)\b")
_IMPROVING = re.compile(r"\b(improv\w* the most|most improved|who\W*s improving|who is improving|who improved|"
                        r"who\W*s getting better|getting worse the most|who\W*s declining|biggest improvement)\b")
_AVERAGE = re.compile(r"\b(average|avg|on average|typical|usually)\b.*\bper (game|match)\b|\bper (game|match) average\b|"
                      r"\b(average|avg) (\w+ ){0,2}(a|each|every) (game|match)\b|\bon average\b")
_STATS_CONTEXT = re.compile(r"\b(our|we|team|teams|when|in my|in our|since|last|this|on|as|vs|versus|by|per|over)\b")
_STREAK = re.compile(r"\bstreaks?\b|\bin a row\b|\bstraight (wins|losses)\b")
_LAST_PLAYED = re.compile(r"\b(when did (i|we) (last )?play|last time (i|we) played|when was my last (match|game)|"
                          r"how long since|last played)\b")
_FIRST_PLAYED = re.compile(r"\b(first (match|game)|when did (i|we) (start|first play)|oldest (match|game))\b")
_DUO = re.compile(r"\b(duo|who do (i|we) win (the )?most with|best teammates?|who should i (play|queue) with|"
                  r"who (do i|i) play best with|win rate with (each|my) teammates?|best partner|"
                  r"who (is|s) my best (teammate|partner|duo))\b")

_GAME_GUIDE = re.compile(r"\bwhat (does|do|is) \w+( \w+)? (do|for|good at|like)\b|\bhow (does|do|to) (i )?(play|use) \w+|"
                         r"\b(gadget|ability|abilities|loadout|callouts?|strats?|strategy|setups?|counters?|"
                         r"how to play)\b")
ANALYSIS_METRICS = ["matches", "rounds", "wins", "losses", "win_rate", "eps", "kd", "kpr", "kost", "survival", "hs",
                    "entry_kills", "entry_deaths", "clutches", "multikills", "trade_kills", "traded"]

# how far from the lobby's average a number has to be to count as a strength or something to work on
_MARGINS = {"kd": ("ratio", 0.12), "kpr": ("ratio", 0.08), "eps": ("abs", 6), "kost": ("abs", 5),
            "survival": ("abs", 6), "hs": ("abs", 7)}
_NAMES = {"kd": "K/D", "kpr": "kills per round", "eps": "EPS", "kost": "KOST", "survival": "survival rate",
          "hs": "headshot rate"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-z' ]", " ", fold(text))).strip()


# ---------------------------------------------------------------- arithmetic --

_OPS = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.USub: op.neg,
        ast.Pow: op.pow, ast.Mod: op.mod}


def _arithmetic(text: str) -> float | None:
    """2 + 2, (12 / 5) * 3: plain arithmetic only, nothing else is evaluated."""
    expr = re.sub(r"^(what is|whats|what's|calculate|compute)\s+", "", text.strip().lower()).rstrip("?= ")
    expr = expr.replace("x", "*").replace("^", "**")
    if not re.fullmatch(r"[\d\s.+\-*/()%]+", expr) or not re.search(r"\d\s*[+\-*/%]", expr):
        return None

    def run(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return run(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            right = run(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise ValueError
            return _OPS[type(node.op)](run(node.left), right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](run(node.operand))
        raise ValueError

    try:
        value = float(run(ast.parse(expr, mode="eval")))
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, TypeError, MemoryError, RecursionError):
        return None
    return value if value == value and abs(value) != float("inf") else None


# ------------------------------------------------------------------ helpers --

def _who(question: str, vocab: Vocab) -> tuple[str, str | None]:
    """Who a question is about: ("player", key), ("team", name), ("my_team", None) or ("me", key)."""
    entities, _ = vocab.find(question)
    for e in entities:
        if e.kind in ("player", "team"):
            return e.kind, e.value
    text = _norm(question)
    if _TEAM_WORDS.search(text) and not _ME.search(re.sub(_TEAM_WORDS, " ", text)):
        return "my_team", None
    return "me", vocab.me


def _name(vocab: Vocab, key: str | None) -> str:
    return vocab.players.get(key, key) if key else "you"


def _bullet(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines)


def _my_team(vocab: Vocab, db: StatsDB) -> str | None:
    """The saved team the user plays on (the one with most matches), if any."""
    if not vocab.me:
        return None
    mine = [t for t, ps in vocab.rosters.items() if vocab.me in ps]
    if not mine:
        return None
    return max(mine, key=lambda t: len(db.team_matches(vocab.rosters[t], min(3, len(vocab.rosters[t])))))


# ----------------------------------------------------------------- analysis --

def analyze_player(db: StatsDB, vocab: Vocab, key: str, focus: bool = False) -> Reply:
    """A player's strengths, what to work on (with tips), form, maps, operators and sides, measured
    against the average of every player in the stats."""
    name = _name(vocab, key)
    you = key == vocab.me
    who = "You" if you else name
    mine = _aggregate(db, Query(kind="summary", who="players", players=[key]), vocab, ANALYSIS_METRICS, None,
                      level="match")
    if not mine or not mine[0].get("matches"):
        return Reply(f"I don't have any matches for **{name}** yet.", "analysis", answered=True,
                     links=[("dashboard", "Add matches on the Dashboard")])
    p = mine[0]
    lobby = _aggregate(db, Query(kind="summary", who="everyone"), vocab, ANALYSIS_METRICS, None, level="match")[0]
    strengths, work = [], []
    for key_, (how, margin) in _MARGINS.items():
        mine_v, avg = p.get(key_), lobby.get(key_)
        if mine_v is None or avg is None:
            continue
        diff = (mine_v - avg) / avg if how == "ratio" and avg else mine_v - avg
        kind = METRICS[key_].kind
        line = f"**{_NAMES[key_]} {fmt(mine_v, kind)}** (average {fmt(avg, kind)})"
        if diff >= margin:
            strengths.append(line)
        elif diff <= -margin:
            work.append((key_, line))
    ek, ed = p.get("entry_kills") or 0, p.get("entry_deaths") or 0
    if ek + ed >= 4:
        if ek >= ed * 1.3:
            strengths.append(f"**Opening duels: {ek} entry kills to {ed} entry deaths**")
        elif ed >= ek * 1.3:
            work.append(("entry", f"**Opening duels: {ed} entry deaths to {ek} entry kills**"))
    if (p.get("clutches") or 0) >= 2:
        strengths.append(f"**{p['clutches']} clutches** won as the last player alive")
    if p["matches"] >= 4 and p.get("win_rate") is not None:
        if p["win_rate"] >= 58:
            strengths.append(f"**Winning: {p['wins']}–{p['losses']}** ({fmt(p['win_rate'], 'pct')})")
        elif p["win_rate"] <= 42:
            work.append(("win_rate", f"**Results: {p['wins']}–{p['losses']}** ({fmt(p['win_rate'], 'pct')})"))

    parts = [f"### {'Your' if you else name + chr(39) + 's'} analysis",
             f"{who}: {p['matches']} match{'es' if p['matches'] != 1 else ''}, {p['wins']}–{p['losses']}, "
             f"K/D {fmt(p.get('kd'), 'ratio')}, EPS {fmt(p.get('eps'), 'eps')}, KOST {fmt(p.get('kost'), 'pct')}."]
    if p["matches"] < 5:
        parts.append(f"_Only {p['matches']} match{'es' if p['matches'] != 1 else ''} so far, so take this as a first "
                     "look: it gets sharper with every match you add._")
    if not focus:
        parts.append("**Strengths**\n" + (_bullet(strengths) if strengths else
                                          "- Nothing stands far above the average yet: a steady, even game."))
    if work:
        tips = [f"{line}. {kb.TIPS.get(k, '')}".strip() for k, line in work]
        parts.append("**What to work on**\n" + _bullet(tips))
    else:
        parts.append("**What to work on**\n- Nothing is far below the average. To climb further, pick your "
                     "weakest map below and practice it.")

    # form: the last five matches against the ones before
    played = [m for m in db.match_list(key) if m.get("player")]
    if len(played) >= 6:
        recent, before = played[:5], played[5:]
        kd = lambda ms: sum(m["kills"] or 0 for m in ms) / max(1, sum(m["deaths"] or 0 for m in ms))  # noqa: E731
        now, then = kd(recent), kd(before)
        trend = "up" if now > then * 1.08 else "down" if now < then * 0.92 else "steady"
        wins = sum(1 for m in recent if m["won"] == 1)
        parts.append(f"**Form**: K/D {fmt(now, 'ratio')} over the last 5 matches ({wins} won) against "
                     f"{fmt(then, 'ratio')} before, **{trend}**.")

    extras = []
    hint = veto_hint(map_pool(db, [name]))
    if hint:
        best, worst = hint
        extras.append(f"Best map **{best['map']}** ({fmt(best['round_win'], 'pct')} of rounds won), weakest "
                      f"**{worst['map']}** ({fmt(worst['round_win'], 'pct')}).")
    ops = [o for o in operator_table(db, name) if (o["Rounds"] or 0) >= 8 and o["K/D"] is not None]
    if len(ops) >= 2:
        best_op = max(ops, key=lambda o: (o["K/D"], o["Rounds"]))
        worst_op = min(ops, key=lambda o: (o["K/D"], -o["Rounds"]))
        extras.append(f"Best operator **{best_op['Operator']}** (K/D {fmt(best_op['K/D'], 'ratio')} over "
                      f"{best_op['Rounds']} rounds), toughest **{worst_op['Operator']}** "
                      f"(K/D {fmt(worst_op['K/D'], 'ratio')}).")
    elif ops:
        extras.append(f"Most played operator **{ops[0]['Operator']}** ({ops[0]['Rounds']} rounds).")
    sides = {r["key"]: r for r in _aggregate(db, Query(kind="rank", who="players", players=[key], group="side"),
                                             vocab, ["win_rate", "kd"], "side", level="round")}
    if "attack" in sides and "defense" in sides:
        a, d = sides["attack"].get("win_rate"), sides["defense"].get("win_rate")
        if a is not None and d is not None:
            better = "attack" if a > d else "defense"
            extras.append(f"Rounds won: **{fmt(a, 'pct')} on attack**, **{fmt(d, 'pct')} on defense**"
                          + (f", so {better} is the stronger side." if abs(a - d) >= 8 else "."))
    if extras:
        parts.append("**Maps, operators and sides**\n" + _bullet(extras))

    rows = [{"Stat": _NAMES.get(k, METRICS[k].label), name: fmt(p.get(k), METRICS[k].kind),
             "Average": fmt(lobby.get(k), METRICS[k].kind)} for k in ("eps", "kd", "kpr", "kost", "survival", "hs")]
    first = "my" if you else name + "'s"
    return Reply("\n\n".join(parts), "analysis", rows=rows, columns=["Stat", name, "Average"],
                 links=[("operators", "Operator Analytics"), ("teams", "Map pool on Team Analytics")],
                 suggestions=[f"How has {first} K/D changed over {'my' if you else 'their'} last 10 matches?",
                              f"{'My' if you else name + chr(39) + 's'} best operators", "Analyze my team"])


def analyze_team(db: StatsDB, vocab: Vocab, team: str) -> Reply:
    """A saved team: its record, the situations that decide its rounds, its players and its maps."""
    roster = vocab.rosters.get(team, [])
    least = min(3, len(roster)) or 1
    s = team_summary(db, roster, least)
    if not s["maps"]:
        return Reply(f"**{team}** hasn't played a match together in your stats yet (at least {least} of them on "
                     "one side).", "analysis", links=[("teams", "Team Analytics")])
    strengths, work = [], []

    def judge(label: str, value: float | None, good: float, bad: float, tip: str, sample: int) -> None:
        if value is None or sample < 4:
            return
        line = f"**{label} {fmt(value, 'pct')}**"
        if value >= good:
            strengths.append(line)
        elif value <= bad:
            work.append(f"{line}. {tip}")

    judge("Plant rate on attack", rate(s["plants"], s["attack_rounds"]), 70, 50,
          "Too many attack rounds end without the defuser down: commit to a site earlier and save utility for "
          "the plant.", s["attack_rounds"])
    judge("Post-plant win rate", rate(s["post_plant_won"], s["post_plant"]), 70, 50,
          "Rounds are lost after the plant: set up crossfires on the defuser and stop chasing kills.", s["post_plant"])
    judge("Retake win rate", rate(s["retakes_won"], s["retakes"]), 40, 20,
          "Retakes fail: retake together, with utility, not one by one.", s["retakes"])
    judge("Man-down comebacks", rate(s["man_down_even"], s["man_down"]), 40, 20,
          "Two players down usually means a lost round: play for trades so one death doesn't become two.",
          s["man_down"])
    round_win = rate(s["rounds_won"], s["rounds_won"] + s["rounds_lost"])
    report = team_report(db, team, least)
    players = sorted((p for p in report["players"] if p.get("eps") is not None), key=lambda p: -p["eps"])
    for p in players:
        p["kd"] = (p.get("kills") or 0) / p["deaths"] if p.get("deaths") else p.get("kills")
    parts = [f"### {team}: team analysis",
             f"{s['maps']} map{'s' if s['maps'] != 1 else ''}, **{s['maps_won']}–{s['maps_lost']}**, rounds "
             f"{s['rounds_won']}–{s['rounds_lost']} ({fmt(round_win, 'pct')} won)."]
    parts.append("**Strengths**\n" + (_bullet(strengths) or "- No situation stands out yet."))
    parts.append("**What to work on**\n" + (_bullet(work) or "- No situation is clearly costing rounds. Look at the "
                                                              "weakest map below."))
    lines = []
    if players:
        top = players[0]
        lines.append(f"Top performer **{top['name']}** (EPS {fmt(top['eps'], 'eps')}, K/D {fmt(top.get('kd'), 'ratio')}).")
        if len(players) >= 3:
            low = players[-1]
            lines.append(f"Lowest EPS **{low['name']}** ({fmt(low['eps'], 'eps')}): worth a look at their role and "
                         "operators.")
    hint = veto_hint(map_pool(db, roster, min_players=least))
    if hint:
        best, worst = hint
        lines.append(f"Pick **{best['map']}** ({fmt(best['round_win'], 'pct')} of rounds won), ban or practice "
                     f"**{worst['map']}** ({fmt(worst['round_win'], 'pct')}).")
    if report["missing"]:
        lines.append(f"No matches yet for {', '.join(report['missing'])}.")
    if lines:
        parts.append("**Players and maps**\n" + _bullet(lines))
    rows = [{"Player": p["name"], "Matches": p.get("matches"), "EPS": fmt(p["eps"], "eps"),
             "K/D": fmt(p.get("kd"), "ratio"), "KOST": fmt(p.get("kost"), "pct")} for p in players]
    return Reply("\n\n".join(parts), "analysis", rows=rows, columns=["Player", "Matches", "EPS", "K/D", "KOST"],
                 links=[("teams", "Team Analytics")],
                 suggestions=[f"How is {team} doing on attack?", f"Compare {team}'s players by EPS"])


def analyze_match(db: StatsDB, vocab: Vocab, player: str | None) -> Reply:
    """The most recent match (the player's, if given): result, the top players and the player's game."""
    games = [m for m in db.match_list(player, limit=20) if not player or m.get("player")][:1]
    if not games:
        return Reply("I don't have a match to look at yet.", "analysis", links=[("dashboard", "Open a match")])
    m = games[0]
    rows = _aggregate(db, Query(kind="rank", who="everyone", group="player"), vocab,
                      ["eps", "kd", "kills", "deaths", "kost", "entry_kills", "entry_deaths"], "player",
                      ids=[m["match_id"]], level="match", minimums=False)
    for r in rows:
        r["name"] = vocab.players.get(r["key"], r["key"])
    rows.sort(key=lambda r: -(r.get("eps") or 0))
    score = f"{m['score0']}–{m['score1']}" if m.get("score0") is not None else ""
    head = f"### {m.get('map') or 'Match'} · {nice_time(m['played_at'])}"
    lines = []
    if m.get("player"):
        mine = next((r for r in rows if r["key"] == (player or "").casefold()), None)
        result = {1: "won", 0: "lost"}.get(m.get("won"), "drew")
        us, them = (m["score0"], m["score1"]) if m.get("team") == 0 else (m["score1"], m["score0"])
        who = "You" if player and player.casefold() == vocab.me else _name(vocab, player.casefold())
        lines.append(f"{who} {result} **{us}–{them}**.")
        if mine:
            place = rows.index(mine) + 1
            lines.append(f"{'Your' if who == 'You' else who + chr(39) + 's'} game: {mine['kills']}–{mine['deaths']}, "
                         f"EPS {fmt(mine['eps'], 'eps')}, KOST {fmt(mine['kost'], 'pct')}, "
                         f"{place}{'st' if place == 1 else 'nd' if place == 2 else 'rd' if place == 3 else 'th'} "
                         f"of the {len(rows)} tracked players.")
            ek, ed = mine.get("entry_kills") or 0, mine.get("entry_deaths") or 0
            if ed >= ek + 2:
                lines.append(f"Opening duels went badly ({ek} won, {ed} lost): {kb.TIPS['entry']}")
            elif (mine.get("kost") or 0) < 50:
                lines.append(f"KOST under 50% means many rounds without an impact: {kb.TIPS['kost']}")
    elif score:
        lines.append(f"Final score **{score}**.")
    if rows:
        lines.append(f"Top performer: **{rows[0]['name']}** (EPS {fmt(rows[0]['eps'], 'eps')}, "
                     f"{rows[0]['kills']}–{rows[0]['deaths']}).")
    table = [{"Player": r["name"], "EPS": fmt(r["eps"], "eps"), "K–D": f"{r['kills']}–{r['deaths']}",
              "KOST": fmt(r["kost"], "pct")} for r in rows]
    return Reply(head + "\n\n" + _bullet(lines), "analysis", rows=table, columns=["Player", "EPS", "K–D", "KOST"],
                 links=[("history", "Open it from Match History")],
                 suggestions=["Analyze my stats", "My most kills in a match"])


def _analysis(question: str, db: StatsDB, vocab: Vocab) -> Reply:
    text = _norm(question)
    kind, value = _who(question, vocab)
    focus = bool(re.search(r"\b(work on|improve|weak|get better|doing wrong|bad at|tips?|advice|practice)\b", text))
    if _MATCH_WORDS.search(text):
        return analyze_match(db, vocab, vocab.players.get(value, value) if kind == "player" else
                             (vocab.players.get(vocab.me) if vocab.me else None))
    if kind == "team":
        return analyze_team(db, vocab, value)
    if kind == "my_team":
        team = _my_team(vocab, db)
        if team:
            return analyze_team(db, vocab, team)
        answer = ask("How are we doing?", db, vocab=vocab)
        return Reply(f"You haven't saved your team yet, so here's your side in your matches. {answer.headline}\n\n"
                     "Build your team on **Team Analytics** (or pick it on League & Team) and I can analyze it "
                     "properly: its plants, retakes, comebacks, players and maps.", "analysis", answer=answer,
                     links=[("teams", "Build a team"), ("league", "League & Team")])
    if not value:
        return Reply("I can't tell which player you are yet. Name one (“analyze Paltry”) or add matches you "
                     "recorded on the Dashboard.", "analysis", answered=False,
                     suggestions=[f"Analyze {n}" for n in list(vocab.players.values())[:3]])
    return analyze_player(db, vocab, value, focus)


# ------------------------------------------------------------- more stats --

def _streaks(question: str, db: StatsDB, vocab: Vocab) -> Reply:
    kind, key = _who(question, vocab)
    if kind in ("team", "my_team"):
        team = key if kind == "team" else _my_team(vocab, db)
        if not team:
            return Reply("Save your team on Team Analytics first, then I can count its streaks.", "stats",
                         links=[("teams", "Build a team")])
        roster = vocab.rosters[team]
        results = [m["won"] for m in db.team_matches(roster, min(3, len(roster)))]
        who = team
    else:
        if not key:
            return Reply("Name a player and I'll count their streaks.", "stats", answered=False)
        results = [m["won"] for m in db.match_list(key) if m.get("player")]
        who = "You" if key == vocab.me else _name(vocab, key)
    if not results:
        return Reply(f"No matches for {who} yet.", "stats")
    current = 0
    for r in results:
        if r != results[0] or r is None:
            break
        current += 1
    best_w = best_l = run = 0
    last = None
    for r in reversed(results):
        run = run + 1 if r == last and r is not None else (1 if r is not None else 0)
        last = r
        if r == 1:
            best_w = max(best_w, run)
        elif r == 0:
            best_l = max(best_l, run)
    state = {1: f"on a **{current}-match win streak**", 0: f"on a **{current}-match losing streak**"}.get(
        results[0], "coming off a draw")
    verb = "are" if who == "You" else "is"
    return Reply(f"{who} {verb} {state}. Longest win streak: **{best_w}**, longest losing streak: **{best_l}** "
                 f"(over {len(results)} matches).", "stats",
                 suggestions=["My record this week", "Analyze my stats"])


def _last_played(question: str, db: StatsDB, vocab: Vocab, first: bool = False) -> Reply:
    kind, key = _who(question, vocab)
    played = [m for m in db.match_list(key) if m.get("player")] if key else db.match_list()
    if not played:
        return Reply("There are no matches for that yet.", "stats")
    m = played[-1] if first else played[0]
    who = "You" if key == vocab.me else _name(vocab, key) if key else "The"
    what = "first" if first else "last"
    result = {1: ", and won", 0: ", and lost"}.get(m.get("won"), "")
    lead = f"{who} {'first' if first else 'last'} played" if key else f"The {what} match in your stats was"
    return Reply(f"{lead} on **{nice_time(m['played_at'])}**, on {m.get('map') or 'an unknown map'}"
                 f"{result} ({m['score0']}–{m['score1']}).", "stats",
                 suggestions=["Analyze my last match", "Show my last 5 matches"])


def _duos(question: str, db: StatsDB, vocab: Vocab) -> Reply:
    kind, key = _who(question, vocab)
    key = key if kind == "player" else vocab.me
    if not key:
        return Reply("Name a player and I'll find who they win most with.", "stats", answered=False)
    rows = db.query(
        """SELECT o.player_key AS key, COUNT(*) AS matches, SUM(me.won = 1) AS wins, SUM(me.won = 0) AS losses
           FROM match_players me JOIN match_players o
             ON o.match_id = me.match_id AND o.team = me.team AND o.player_key != me.player_key
           WHERE me.player_key = ? GROUP BY o.player_key HAVING COUNT(*) >= 2""", [key])
    for r in rows:
        r["rate"] = rate(r["wins"] or 0, (r["wins"] or 0) + (r["losses"] or 0))
    rows = [r for r in rows if r["rate"] is not None]
    rows.sort(key=lambda r: (-r["rate"], -r["matches"]))
    who = "you" if key == vocab.me else _name(vocab, key)
    if not rows:
        return Reply(f"{who.capitalize()} haven't played two matches with the same teammate yet.", "stats")
    best = rows[0]
    table = [{"Teammate": vocab.players.get(r["key"], r["key"]), "Matches": r["matches"],
              "Record": f"{r['wins']}–{r['losses']}", "Win %": fmt(r["rate"], "pct")} for r in rows[:10]]
    return Reply(f"{'You win' if who == 'you' else who + ' wins'} most with **{table[0]['Teammate']}**: "
                 f"{best['wins']}–{best['losses']} ({fmt(best['rate'], 'pct')}) over {best['matches']} matches.",
                 "stats", rows=table, columns=["Teammate", "Matches", "Record", "Win %"],
                 suggestions=[f"Compare me and {table[0]['Teammate']}"])


def _game_guide(entity, db: StatsDB, vocab: Vocab) -> Reply:
    """An operator's or map's guide isn't something it has: say so, and what the stats say about it."""
    lookup = f"How do I do as {entity.display}?" if entity.kind == "operator" else f"How do I do on {entity.display}?"
    answer = ask(lookup, db, vocab=vocab)
    what = "operator guides (gadgets, loadouts, counters)" if entity.kind == "operator" else         "map guides (callouts, strats, setups)"
    text = f"I don't have {what} yet, only what your matches say. "
    text += (f"About **{entity.display}**: {answer.headline}" if answer.ok else
             f"You haven't played {'as' if entity.kind == 'operator' else 'on'} {entity.display} in your stats yet.")
    text += " If a guide would help, press **Report** and the developers can add one."
    return Reply(text, "fallback", answered=False, answer=answer if answer.ok else None, resolved=lookup,
                 suggestions=["Which operators do I play most?" if entity.kind == "operator" else "What's my best map?"])


def _per_match(db: StatsDB, key: str) -> list[dict[str, Any]]:
    """A player's matches, oldest first: EPS, kills, deaths, result and when."""
    return db.query("SELECT m.played_at, m.map, t.kills, t.deaths, t.won, 100.0 * t.rating AS eps FROM match_players t "
                    "JOIN matches m ON m.match_id = t.match_id WHERE t.player_key = ? ORDER BY m.played_at", [key])


def _swing(values: list[float]) -> float:
    """How far values usually are from their average (the standard deviation)."""
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5


def _subject_key(question: str, vocab: Vocab) -> str | None:
    kind, key = _who(question, vocab)
    return key if kind == "player" else vocab.me


def _ban(question: str, db: StatsDB, vocab: Vocab) -> Reply:
    """Which map to ban (the weakest) or pick (the strongest), from the map pool."""
    kind, key = _who(question, vocab)
    team = key if kind == "team" else _my_team(vocab, db) if kind == "my_team" else None
    player = key if kind == "player" else vocab.me
    players = vocab.rosters[team] if team else [_name(vocab, player)] if player else []
    pool = map_pool(db, players, min_players=min(3, len(players)) if team else 1)
    hint = veto_hint(pool)
    who = team or ("you" if player == vocab.me else _name(vocab, player))
    if not hint:
        return Reply(f"There aren't enough maps played twice or more to say which to pick or ban for {who} yet. The map "
                     "pool on Team Analytics shows each map's record so far.", "stats",
                     links=[("teams", "Map pool on Team Analytics")])
    best, worst = hint
    text = _norm(question)
    picking = bool(re.search(r"\b(pick|picks|first pick)\b", text)) and not re.search(r"\bban\b", text)
    chosen = best if picking else worst
    rows = [{"Map": m["map"], "Maps": m["maps"], "Record": f"{m['maps_won']}–{m['maps_lost']}",
             "Rounds won": fmt(m["round_win"], "pct"), "Attack": fmt(m["attack_win"], "pct"),
             "Defense": fmt(m["defense_win"], "pct")} for m in pool]
    return Reply(f"{'Pick' if picking else 'Ban'} **{chosen['map']}**: {fmt(chosen['round_win'], 'pct')} of rounds won "
                 f"there. Strongest map for {who}: {best['map']} ({fmt(best['round_win'], 'pct')}); weakest: "
                 f"{worst['map']} ({fmt(worst['round_win'], 'pct')}). Only maps played at least twice count.",
                 "stats", rows=rows, columns=list(rows[0]) if rows else [],
                 links=[("teams", "Map pool on Team Analytics")], suggestions=["Heatmap of win rate by map and side"])


_PARTS_OF_DAY = [("Morning", 5, 12), ("Afternoon", 12, 17), ("Evening", 17, 22), ("Late night", 22, 29)]
_WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def _when_best(question: str, db: StatsDB, vocab: Vocab) -> Reply:
    """How a player does by time of day, or by day of the week."""
    key = _subject_key(question, vocab)
    if not key:
        return Reply("Name a player and I'll see when they play best.", "stats", answered=False)
    text = _norm(question)
    by_day = bool(re.search(r"\b(which day|what day|weekdays?|weekends?|\w+days|day of the week)\b", text)) and \
        not re.search(r"\b(time of day|morning|afternoon|evening|night)\b", text)
    groups: dict[str, list[dict[str, Any]]] = {}
    for m in _per_match(db, key):
        try:
            when = datetime.strptime(m["played_at"][:19], "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            continue
        if by_day:
            label = _WEEKDAYS[(when.weekday() + 1) % 7]
        else:
            hour = when.hour if when.hour >= 5 else when.hour + 24
            label = next(name for name, start, end in _PARTS_OF_DAY if start <= hour < end)
        groups.setdefault(label, []).append(m)
    rows, scores = [], {}
    for label in (_WEEKDAYS if by_day else [p[0] for p in _PARTS_OF_DAY]):
        ms = groups.get(label)
        if not ms:
            continue
        wins, losses = sum(1 for m in ms if m["won"] == 1), sum(1 for m in ms if m["won"] == 0)
        kd = sum(m["kills"] or 0 for m in ms) / max(1, sum(m["deaths"] or 0 for m in ms))
        scores[label] = sum(m["eps"] or 0 for m in ms) / len(ms)
        rows.append({"When": label, "Matches": len(ms), "Record": f"{wins}–{losses}",
                     "Win %": fmt(rate(wins, wins + losses), "pct"), "K/D": fmt(kd, "ratio"),
                     "EPS": fmt(scores[label], "eps")})
    who = "You" if key == vocab.me else _name(vocab, key)
    if not rows:
        return Reply(f"No matches for {who} yet.", "stats")
    fair = [r for r in rows if r["Matches"] >= 2] or rows
    best = max(fair, key=lambda r: scores[r["When"]])
    text = (f"{who} {'play' if who == 'You' else 'plays'} best {'on' if by_day else 'in the'} "
            f"**{best['When'] + ('s' if by_day else '')}**: EPS {best['EPS']}, K/D {best['K/D']} over {best['Matches']} "
            f"match{'es' if best['Matches'] != 1 else ''}.")
    if len(fair) < 2:
        text += " That's the only time with enough matches to compare: play at other times to see a difference."
    return Reply(text, "stats", rows=rows, columns=list(rows[0]),
                 suggestions=["What time of day do I play best?" if by_day else "Which day do I play best?"])


def _consistency(question: str, db: StatsDB, vocab: Vocab) -> Reply:
    """How much a player's EPS swings from match to match, against their usual teammates'."""
    key = _subject_key(question, vocab)
    if not key:
        return Reply("Name a player and I'll see how consistent they are.", "stats", answered=False)
    keys = [key] + [name.casefold() for name, _ in db.teammates(_name(vocab, key), 6)]
    rows, owner = [], {}
    for k in dict.fromkeys(keys):
        values = [m["eps"] for m in _per_match(db, k) if m["eps"] is not None]
        if len(values) >= 3:
            rows.append({"Player": _name(vocab, k), "Matches": len(values),
                         "Average EPS": round(sum(values) / len(values)), "Swing (±)": round(_swing(values)),
                         "Worst": round(min(values)), "Best": round(max(values))})
            owner[id(rows[-1])] = k
    mine = next((r for r in rows if owner[id(r)] == key), None)
    who = "You" if key == vocab.me else _name(vocab, key)
    if not mine:
        return Reply(f"I need at least 3 matches for {'you' if who == 'You' else who} to say how consistent "
                     f"{'you are' if who == 'You' else 'they are'}.", "stats")
    rows.sort(key=lambda r: r["Swing (±)"])
    swing = mine["Swing (±)"]
    verdict = "very consistent" if swing <= 6 else "fairly consistent" if swing <= 12 else "streaky"
    text = (f"{who} {'are' if who == 'You' else 'is'} **{verdict}**: EPS {mine['Average EPS']} on average, usually "
            f"within ±{swing} (from {mine['Worst']} to {mine['Best']}).")
    if len(rows) > 1:
        text += (f" That's #{rows.index(mine) + 1} of {len(rows)} for steadiness among "
                 f"{'your' if who == 'You' else 'their'} usual teammates.")
    return Reply(text, "stats", rows=rows, columns=list(rows[0]),
                 suggestions=["Rolling average of my EPS", "Analyze my stats"])


def _improving(db: StatsDB, vocab: Vocab) -> Reply:
    """Each player's EPS in their earlier matches against their later ones, most improved first."""
    rows = []
    for key, name in vocab.players.items():
        values = [m["eps"] for m in _per_match(db, key) if m["eps"] is not None]
        if len(values) < 6:
            continue
        half = len(values) // 2
        early, late = sum(values[:half]) / half, sum(values[-half:]) / half
        rows.append({"Player": name, "Matches": len(values), "Earlier EPS": round(early), "Recent EPS": round(late),
                     "Change": round(late - early)})
    if not rows:
        return Reply("Nobody has 6 matches yet, which is what I need to compare earlier and recent games.", "stats")
    rows.sort(key=lambda r: -r["Change"])
    top, bottom = rows[0], rows[-1]
    text = (f"**{top['Player']}** has improved the most: EPS {top['Earlier EPS']} → {top['Recent EPS']} "
            f"({top['Change']:+d}), comparing their earlier half of matches with their recent half.")
    if bottom is not top and bottom["Change"] < 0:
        text += f" {bottom['Player']} has dropped the most ({bottom['Earlier EPS']} → {bottom['Recent EPS']})."
    return Reply(text, "stats", rows=rows, columns=list(rows[0]),
                 suggestions=[f"Chart {top['Player']}'s EPS over time", f"Analyze {top['Player']}"])


def _average(question: str, db: StatsDB, vocab: Vocab, now: datetime | None) -> Reply | None:
    """"My average kills per game": a count divided by the matches it's over."""
    q = interpret(re.sub(r"\b(per|a|each|every) (game|match)\b|\baverage\b|\bavg\b|\bon average\b", " ", question,
                         flags=re.I), vocab, now)
    metric = next((m for m in q.metrics if METRICS[m].kind == "count" and m not in ("matches", "rounds")), None)
    if metric is None or q.group:
        return None
    rows = _aggregate(db, q, vocab, [metric, "matches"], None, level=q.level)
    if not rows or not rows[0].get("matches"):
        return None
    r = rows[0]
    per = (r[metric] or 0) / r["matches"]
    mine = q.who == "me" or q.players == [vocab.me]
    who = "You" if mine else {"my_team": "Your team", "everyone": "Everyone"}.get(q.who) or (
        _name(vocab, q.players[0]) if q.players else "They")
    verb = "average" if who in ("You", "Everyone", "They") else "averages"
    whose = "my" if mine else "our" if q.who == "my_team" else f"{who}'s"
    return Reply(f"{who} {verb} **{per:.1f} {METRICS[metric].phrase}** per match ({r[metric]} over {r['matches']} "
                 f"matches).", "stats", suggestions=[f"Chart {whose} {METRICS[metric].phrase} over time"])


# ---------------------------------------------------------------- fallback --

def example_questions(vocab: Vocab, db: StatsDB) -> list[str]:
    """Questions it can answer, to suggest the closest ones."""
    base = suggestions(vocab, db, count=20) + [
        "Analyze my stats", "What should I work on?", "Analyze my team", "Analyze my last match",
        "My win streak", "Who do I win most with?", "When did I last play?", "What is KOST?", "What is EPS?",
        "How do I add my matches?", "Where are my replays?", "My K/D on attack", "Best operators",
        "Who has the most clutches?", "My record in scrims", "Ranked vs unranked win rate",
        "My headshot rate over time", "How many aces do I have?", "My worst map", "Top fraggers"]
    return list(dict.fromkeys(base))


def _closest(question: str, vocab: Vocab, db: StatsDB, n: int = 3) -> list[str]:
    pool = example_questions(vocab, db)
    folded = {fold(q): q for q in pool}
    close = difflib.get_close_matches(fold(question), list(folded), n=n, cutoff=0.3)
    found = [folded[c] for c in close]
    words = set(re.findall(r"[a-z]{4,}", fold(question)))
    for q in pool:  # questions sharing a word with it
        if len(found) >= n:
            break
        if q not in found and words & set(re.findall(r"[a-z]{4,}", fold(q))):
            found.append(q)
    for q in pool:
        if len(found) >= n:
            break
        if q not in found:
            found.append(q)
    return found


def _best_guess(question: str, db: StatsDB, vocab: Vocab, notes: list[str]) -> Reply:
    """Something useful for a question nothing else could read."""
    entities, _ = vocab.find(question)
    if topic := kb.guide(question, any_wording=True):
        return _topic_reply(topic, question)
    text = _norm(question)
    glossary = max(kb.GLOSSARY, key=lambda t: kb._score(t, kb._norm(question)))
    if kb._score(glossary, kb._norm(question)) >= 3 and len(text.split()) <= 4:
        return _topic_reply(glossary, question, "define")
    if entities:  # it knows a name in it: say what it knows about that
        e = entities[0]
        lookup = {"player": f"How is {e.display} doing?", "team": f"How is {e.display} doing?",
                  "map": f"How do I do on {e.display}?", "operator": f"How do I do as {e.display}?"}[e.kind]
        answer = ask(lookup, db, vocab=vocab)
        if answer.ok:
            extra = (" I don't have guides to operators themselves, but" if e.kind == "operator" else
                     " I don't have guides to maps themselves, but" if e.kind == "map" else "")
            return Reply(f"I'm not sure exactly what you're after.{extra} here's what your stats say about "
                         f"**{e.display}**: {answer.headline}", "fallback", answered=False, answer=answer,
                         resolved=lookup, suggestions=_closest(question, vocab, db))
    lines = ["I couldn't work that one out."]
    if notes:
        lines.append(" ".join(notes))
    lines.append("I answer questions about your matches, analyze players and teams, explain stats and show you "
                 "around the app. Here are questions close to yours. If it's something I should know, press "
                 "**Report** and the developers can add it.")
    return Reply(" ".join(lines), "fallback", answered=False, suggestions=_closest(question, vocab, db))


def _topic_reply(topic: kb.Topic, question: str, kind: str = "guide") -> Reply:
    return Reply(f"**{topic.title}**\n\n{topic.text}", kind, links=[(topic.page, f"Open {topic.title}")]
                 if topic.page else [], suggestions=list(topic.follow_ups), question=question)


def _small_talk(text: str) -> Reply | None:
    if _GREETING.search(text) and len(text.split()) <= 4:
        return Reply("Hey! Ask me about your matches, ask me to analyze a player or your team, or ask how to do "
                     "something in the app.", "smalltalk",
                     suggestions=["Analyze my stats", "What's my best map?", "How do I add my matches?"])
    if _HOW_ARE_YOU.search(text):
        return Reply("Doing well, and ready to dig into your matches. What would you like to know?", "smalltalk",
                     suggestions=["Analyze my stats", "My win streak"])
    if _THANKS.search(text) and len(text.split()) <= 6:
        return Reply("Any time. Anything else?", "smalltalk")
    if _BYE.search(text):
        return Reply("Good luck in your next match! I'm here when you need me.", "smalltalk")
    if _JOKE.search(text):
        return Reply("Why do Mute players never get invited to parties? They jam every conversation. …I'll stick to "
                     "stats.", "smalltalk", suggestions=["Analyze my stats"])
    if _YES_NO.match(text):
        return Reply("Got it. Ask me anything about your matches, or pick one of these.", "smalltalk",
                     suggestions=["Analyze my stats", "What should I work on?", "My record this week"])
    return None


# ---------------------------------------------------------- the one entry --

def answer_anything(question: str, db: StatsDB, *, vocab: Vocab | None = None, now: datetime | None = None,
                    page: str | None = None) -> Reply:
    """An answer to anything: see the top of this file for what's tried, in order."""
    question = (question or "").strip()
    if not question:
        return Reply("Ask me anything about your matches, or how to use the app.", "empty", answered=False)
    text = _norm(question)
    if (talk := _small_talk(text)) is not None:
        talk.question = question
        return talk
    if (value := _arithmetic(question)) is not None:
        return Reply(f"**{value:g}**", "smalltalk", question=question)
    if re.search(r"\b(this page|this tab|here|current page)\b", text) and page and (t := kb.page_topic(page)):
        return _topic_reply(t, question)

    matches = db.summary()["matches"]
    if matches:
        vocab = vocab or Vocab.from_db(db)
    q = interpret(question, vocab, now) if matches else None
    stats_kind = q is not None and q.kind in ("rank", "trend", "compare", "count", "value")
    clean_metrics = _find_metrics(_clean(question, [])) if matches else []

    # a stat explained: "what is KOST?" (not "what is my KOST?")
    context = bool(q and (q.players or q.team or q.won is not None or q.since or q.maps or q.side or q.operators
                          or q.match_type or q.tag or q.last_matches)) or bool(_STATS_CONTEXT.search(text))
    if (topic := kb.define(question)) and not (_ME.search(text) or context):
        reply = _topic_reply(topic, question, "define")
        reply.suggestions = [f"My {topic.title} over time" if topic.key in METRIC_WORDS else "Analyze my stats"]
        return reply
    # the app: "how do I add my matches?"
    if (topic := kb.guide(question)) and not (stats_kind and (clean_metrics or q.kind in ("rank", "compare", "count"))):
        return _topic_reply(topic, question)
    if not matches:
        if _ANALYZE.search(text) or q is None:
            topic = kb.topic("load")
            reply = _topic_reply(topic, question)
            reply.text = "There are no matches in your stats yet, so there's nothing to look at. " + reply.text
            return reply
    # a chart: "chart my K/D over time", "pie of the operators I play"
    if _CHART.search(text) and not kb.guide(question):  # "how do charts work?" is the guide
        return _with_question(chart_reply(question, db, vocab, now), question)
    # analysis: "analyze my team", "what should I work on?"
    if _ANALYZE.search(text) and not (stats_kind and clean_metrics and q.kind in ("trend", "rank")):
        reply = _analysis(question, db, vocab)
        reply.question = question
        return reply
    if _STREAK.search(text):
        return _with_question(_streaks(question, db, vocab), question)
    if _LAST_PLAYED.search(text):
        return _with_question(_last_played(question, db, vocab), question)
    if _FIRST_PLAYED.search(text) and not clean_metrics:
        return _with_question(_last_played(question, db, vocab, first=True), question)
    if _DUO.search(text):
        return _with_question(_duos(question, db, vocab), question)
    if _BAN.search(text):
        return _with_question(_ban(question, db, vocab), question)
    if _IMPROVING.search(text):
        return _with_question(_improving(db, vocab), question)
    if _CONSISTENT.search(text):
        return _with_question(_consistency(question, db, vocab), question)
    if _TIME_OF_DAY.search(text) and not (q and q.since):
        return _with_question(_when_best(question, db, vocab), question)
    if _AVERAGE.search(text) and clean_metrics:
        if (reply := _average(question, db, vocab, now)) is not None:
            return _with_question(reply, question)
    if _GAME_GUIDE.search(text) and (ops := [e for e in vocab.find(question)[0] if e.kind in ("operator", "map")]):
        return _with_question(_game_guide(ops[0], db, vocab), question)

    answer = ask(question, db, now=now, vocab=vocab)
    if answer.ok or (answer.understood and answer.query and answer.query.kind != "unknown"):
        return Reply(answer.headline, "stats", answered=True, answer=answer, question=question,
                     suggestions=_next_questions(answer, vocab))
    reply = _best_guess(question, db, vocab, answer.notes)
    reply.question = question
    return reply


def chart_reply(question: str, db: StatsDB, vocab: Vocab, now: datetime | None = None) -> Reply:
    """A chart of what was asked for."""
    spec = chart_engine.parse(question, vocab, now)
    chart = chart_engine.build(spec, db, vocab)
    if not chart.ok:
        return Reply(f"I couldn't draw that: {' '.join(chart.notes)}", "chart", answered=False,
                     links=[("charts", "Build it on Charts")])
    return Reply(f"**{chart.title}**. {chart_engine.describe(chart)}", "chart", chart=chart,
                 links=[("charts", "Open on Charts to change it")],
                 suggestions=["Pie of the operators I play", "Heatmap of win rate by map and side"])


def _with_question(reply: Reply, question: str) -> Reply:
    reply.question = question
    return reply


def _next_questions(answer: Answer, vocab: Vocab) -> list[str]:
    """What to ask next after a stats answer."""
    q = answer.query
    if q is None:
        return []
    out = []
    if q.kind == "summary" and q.who in ("me", "players"):
        mine = q.who == "me" or q.players == [vocab.me]
        out.append("Analyze my stats" if mine else f"Analyze {vocab.players.get(q.players[0], '')}")
    trendable = [m for m in q.metrics if m not in ("matches", "rounds", "wins", "losses")]
    whose = "my" if q.who == "me" or q.players == [vocab.me] else         f"{vocab.players.get(q.players[0], q.players[0])}'s" if q.who == "players" and len(q.players) == 1 else         f"{q.team}'s" if q.who == "team" and q.team else "our" if q.who == "my_team" else None
    if q.kind != "trend" and trendable and whose:
        out.append(f"How has {whose} {METRICS[trendable[0]].phrase} changed over time?")
    if q.group != "map":
        out.append("What about on defense?" if q.side != "defense" else "What about on attack?")
    return out[:3]


# ------------------------------------------------------------ conversation --

class Conversation:
    """A chat: replies that remember what came before, so follow-ups work. `state` is any dict that
    survives between messages (the Session keeps it)."""

    def __init__(self, state: dict[str, Any] | None = None):
        self.state = state if state is not None else {}

    def reply(self, message: str, db: StatsDB, *, page: str | None = None, now: datetime | None = None) -> Reply:
        message = (message or "").strip()
        last = self.state.get("last_question")
        if last and _WHY.match(message):
            return Reply(self.state.get("last_why") or "That came straight from the matches in your stats.",
                         "smalltalk", question=message)
        if last and _MORE.match(message):
            return Reply("Here's the full answer on the Ask page, with a table and a chart.", "smalltalk",
                         links=[("ask", "Open it on Ask")], question=message, resolved=last)
        vocab = Vocab.from_db(db) if db.summary()["matches"] else None
        resolved = self.resolve(message, vocab) if vocab else message
        reply = answer_anything(resolved, db, vocab=vocab, now=now, page=page)
        if resolved != message:
            reply.question, reply.resolved = message, resolved
        if reply.kind in ("stats", "analysis") and reply.answered:
            self.state["last_question"] = resolved
            if reply.answer and reply.answer.understood:
                notes = " ".join(reply.answer.notes)
                self.state["last_why"] = (f"I looked up {reply.answer.understood[:1].lower()}"
                                          f"{reply.answer.understood[1:]}, from the matches in your stats."
                                          + (f" {notes}" if notes else ""))
            else:
                self.state["last_why"] = "It's worked out from every match in your stats, measured against the " \
                                         "average of every player in your stats."
            if vocab:
                found, _ = vocab.find(resolved)
                person = next((e for e in found if e.kind == "player"), None)
                if person:
                    self.state["last_player"] = person.display
        return reply

    def resolve(self, message: str, vocab: Vocab) -> str:
        """A follow-up read as a whole question: "and Paltry?" after "my K/D on Bank" is "Paltry K/D on Bank"."""
        last = self.state.get("last_question")
        if (name := self.state.get("last_player")) and _PRONOUNS.search(message):
            message = _PRONOUNS.sub(lambda m: f"{name}'s" if m.group(1).lower() in ("his", "her", "their", "hers",
                                                                                     "theirs") else name, message)
        if not last:
            return message
        opener = _FOLLOW_UP.match(message) or re.search(r"\binstead\b", message, re.I)
        rest = message[opener.end():] if opener and opener.start() == 0 else message
        rest = re.sub(r"\s*\binstead\b", "", rest, flags=re.I)
        alone = interpret(rest, vocab)
        short = len(_norm(rest).split()) <= 4
        if not opener and not (short and alone.kind in ("unknown", "summary") and not _ANALYZE.search(_norm(rest))):
            return message
        if opener is None and alone.kind == "summary" and (alone.players or alone.team):
            return message  # "Paltry" on its own: how they're doing
        entities, _ = vocab.find(rest)
        base = last
        if any(e.kind in ("player", "team") for e in entities):  # a new subject replaces "my"
            base = re.sub(r"\b(my|me|i|our|we|us|mine|i'm|im)\b", " ", base, flags=re.I)
            for e in vocab.find(last)[0]:
                if e.kind in ("player", "team"):
                    base = base.replace(last[e.start:e.end], " ")
        if _find_metrics(_clean(rest, entities)):  # a new stat replaces the old one
            for phrase in sorted((p for ps in METRIC_WORDS.values() for p in ps), key=len, reverse=True):
                base = re.sub(r"\b" + re.escape(phrase) + r"\b", " ", base, flags=re.I)
            base = re.sub(r"\bk\s*/\s*d\b", " ", base, flags=re.I)
        rest = rest.rstrip("?!. ")
        return re.sub(r"\s+", " ", f"{rest} {base}" if entities else f"{base.rstrip('?!. ')} {rest}").strip()


# --------------------------------------------------------- page suggestions --

PAGE_STARTERS: dict[str, list[str]] = {
    "dashboard": ["Analyze my last match", "How do I add my matches?", "What is EPS?"],
    "ask": ["Analyze my stats", "What should I work on?", "My win streak"],
    "charts": ["Chart my K/D over time", "Pie of the operators I play", "Heatmap of win rate by map and side"],
    "history": ["Show my last 5 matches", "My record in scrims", "When did I last play?"],
    "teams": ["Analyze my team", "Who do I win most with?", "What's my best map?"],
    "operators": ["My best operators", "Which operators do I play most?", "My K/D on attack"],
    "matchday": ["Analyze my team", "What's my best map?", "How does match day work?"],
    "team": ["How does the team hub work?", "Analyze my team"],
    "league": ["How do I pick my team?", "Analyze my team"],
    "theme": ["How do I change the colors?", "Analyze my stats"],
    "download": ["Where are my replays?", "Is it safe to use?", "How do updates work?"],
}


def starters(page: str | None) -> list[str]:
    """Questions to offer when the chat opens on a page."""
    return PAGE_STARTERS.get(page or "", ["Analyze my stats", "What can you do?", "How do I add my matches?"])
