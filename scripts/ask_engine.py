"""
ask_engine.py
=============
Answers plain-English questions about the stats database (stats_db.py). Nobody
writes a query: the question is read into a Query -- what to measure, for whom,
grouped how, filtered how -- which becomes SQL, and the answer comes back as a
sentence, a table and, for trends, a chart. It all runs on this PC: no AI service,
nothing sent anywhere.

    answer = ask("How has my K/D changed over my last 10 matches?", db)
    answer.headline  # "Your K/D went from 0.71 to 0.92: improving."

What it understands:
    whose stats   you ("my", "I"), a player by name (or the part before the clan tag:
                  "Paltry"), a team built on the Team page, "we"/"our" (your side in
                  your matches), "teammates", "opponents", "everyone"
    what          K/D, kills, deaths, assists, KPR, headshot %, KOST, survival, entry,
                  clutches, multikills, trades, plants, defuses, EPS, win rate, wins,
                  losses, matches, rounds -- and everyday words for them
    grouped by    player ("who", "which teammate"), map, operator, side, site, match
                  type, match ("over time", "trend", "improving"), day, week, month
    filtered by   a map, an operator, attack/defense, ranked/unranked, wins/losses,
                  "last 10 matches", "last week", "since Sep 20", "in September",
                  "more than 10 kills" (matches where that happened), "at least 5 matches"
    and gives     a value, a ranking, a trend, a comparison ("X vs Y"), a count
                  ("how many"), a record, a summary ("how is X doing") or a match list

Only words the engine knows become SQL (metrics, groupings, orderings); every name,
number and date from a question is passed as a parameter.
"""

from __future__ import annotations

import difflib
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from metrics_engine import round_eps
from stats_db import StatsDB, nice_day, nice_time

# ------------------------------------------------------------------ metrics --


@dataclass(frozen=True)
class Metric:
    key: str
    label: str              # column header and chart axis
    phrase: str             # in a sentence: "K/D", "win rate"
    match_sql: str | None   # aggregate over match_players rows, alias t
    round_sql: str | None   # aggregate over round_players rows, alias t
    kind: str = "ratio"     # ratio, pct, count, eps, signed: how it's rounded and shown
    higher_is_better: bool = True


_KD = "CASE WHEN SUM(t.{d}) = 0 THEN SUM(t.kills) * 1.0 ELSE SUM(t.kills) * 1.0 / SUM(t.{d}) END"
_WON = "COUNT(DISTINCT CASE WHEN t.won = {v} THEN {unit} END)"
_M_UNIT, _R_UNIT = "t.match_id", "t.match_id || ':' || t.round"
_WINS_M, _LOSS_M = _WON.format(v=1, unit=_M_UNIT), _WON.format(v=0, unit=_M_UNIT)
_WINS_R, _LOSS_R = _WON.format(v=1, unit=_R_UNIT), _WON.format(v=0, unit=_R_UNIT)
_HS = "100.0 * SUM(t.headshots) / NULLIF(SUM(t.kills), 0)"

METRICS: dict[str, Metric] = {m.key: m for m in [
    # not rounded here: SQLite rounds halves up, and the scoreboard's round() would show 112.5 as 112
    Metric("eps", "EPS", "EPS", "100.0 * SUM(t.rating * t.rounds) / NULLIF(SUM(t.rounds), 0)", None, "eps"),
    Metric("kd", "K/D", "K/D", _KD.format(d="deaths"), _KD.format(d="died")),
    Metric("kills", "Kills", "kills", "SUM(t.kills)", "SUM(t.kills)", "count"),
    Metric("deaths", "Deaths", "deaths", "SUM(t.deaths)", "SUM(t.died)", "count", False),
    Metric("assists", "Assists", "assists", "SUM(t.assists)", "SUM(t.assists)", "count"),
    Metric("kpr", "Kills per round", "kills per round", "SUM(t.kills) * 1.0 / NULLIF(SUM(t.rounds), 0)",
           "SUM(t.kills) * 1.0 / COUNT(*)"),
    Metric("dpr", "Deaths per round", "deaths per round", "SUM(t.deaths) * 1.0 / NULLIF(SUM(t.rounds), 0)",
           "SUM(t.died) * 1.0 / COUNT(*)", "ratio", False),
    Metric("hs", "Headshot %", "headshot rate", _HS, _HS, "pct"),
    Metric("kost", "KOST %", "KOST", "100.0 * SUM(t.kost_rounds) / NULLIF(SUM(t.rounds), 0)",
           "100.0 * SUM(t.kost) / COUNT(*)", "pct"),
    Metric("survival", "Survival %", "survival rate", "100.0 * SUM(t.rounds_survived) / NULLIF(SUM(t.rounds), 0)",
           "100.0 * (COUNT(*) - SUM(t.died)) / COUNT(*)", "pct"),
    Metric("entry", "Entry +/-", "entry differential", "SUM(t.entry_kills) - SUM(t.entry_deaths)",
           "SUM(t.entry_kill) - SUM(t.entry_death)", "signed"),
    Metric("entry_kills", "Entry kills", "entry kills", "SUM(t.entry_kills)", "SUM(t.entry_kill)", "count"),
    Metric("entry_deaths", "Entry deaths", "entry deaths", "SUM(t.entry_deaths)", "SUM(t.entry_death)", "count", False),
    Metric("clutches", "Clutches", "clutches", "SUM(t.clutches)", "SUM(t.clutch IS NOT NULL)", "count"),
    Metric("multikills", "Multikill rounds", "multikill rounds", "SUM(t.multikills)", "SUM(t.kills >= 2)", "count"),
    Metric("aces", "Aces", "aces", None, "SUM(t.kills >= 5)", "count"),  # only the rounds know
    Metric("trade_kills", "Trade kills", "trade kills", "SUM(t.trade_kills)", "SUM(t.trade_kills)", "count"),
    Metric("traded", "Deaths traded", "deaths traded", "SUM(t.traded)", "SUM(t.traded)", "count"),
    Metric("plants", "Plants", "plants", "SUM(t.plants)", "SUM(t.planted)", "count"),
    Metric("defuses", "Defuses", "defuser disables", "SUM(t.defuses)", "SUM(t.defused)", "count"),
    Metric("objectives", "Objectives", "objectives", "SUM(t.plants) + SUM(t.defuses)",
           "SUM(t.planted) + SUM(t.defused)", "count"),
    Metric("win_rate", "Win %", "win rate", f"100.0 * {_WINS_M} / NULLIF({_WINS_M} + {_LOSS_M}, 0)",
           f"100.0 * {_WINS_R} / NULLIF({_WINS_R} + {_LOSS_R}, 0)", "pct"),
    Metric("wins", "Wins", "wins", _WINS_M, _WINS_R, "count"),
    Metric("losses", "Losses", "losses", _LOSS_M, _LOSS_R, "count", False),
    Metric("matches", "Matches", "matches", "COUNT(DISTINCT t.match_id)", "COUNT(DISTINCT t.match_id)", "count"),
    Metric("rounds", "Rounds", "rounds", "SUM(t.rounds)", f"COUNT(DISTINCT {_R_UNIT})", "count"),
]}
_ROUND_WORDS = {"win_rate": ("Round win %", "round win rate"), "wins": ("Rounds won", "rounds won"),
                "losses": ("Rounds lost", "rounds lost")}
RESULT_METRICS = {"win_rate", "wins", "losses"}
SAMPLE_METRICS = {"matches", "rounds"}
RECORD = ["wins", "losses", "win_rate"]
SUMMARY_MATCH = ["matches", "wins", "losses", "win_rate", "kd", "kpr", "hs", "kost", "survival", "entry", "clutches",
                 "eps"]
SUMMARY_ROUND = ["rounds", "win_rate", "kd", "kpr", "hs", "kost", "survival", "entry", "clutches"]

# everyday words for each metric; the longest wording in a question wins
METRIC_WORDS: dict[str, list[str]] = {
    "eps": ["eps", "rating", "performance score", "performance", "impact", "overall rating"],
    "kd": ["kd ratio", "kdr", "kd", "kill death ratio", "kill death", "kill to death", "kills to deaths",
           "kills per death"],
    "kpr": ["kpr", "kills per round", "kills a round", "kills each round"],
    "dpr": ["dpr", "deaths per round", "deaths a round"],
    "hs": ["headshot percentage", "headshot percent", "headshot rate", "headshots", "headshot", "head shots",
           "head shot", "hs percent", "hs"],
    "kost": ["kost"],
    "survival": ["survival rate", "survival", "survive", "surviving", "srv", "stay alive"],
    "entry_kills": ["entry kills", "entry kill", "opening kills", "opening kill", "first kills", "first bloods",
                    "first blood"],
    "entry_deaths": ["entry deaths", "entry death", "opening deaths", "opening death", "first deaths", "die first",
                     "died first"],
    "entry": ["entry differential", "entry diff", "entries", "entry", "entrying", "opening duels", "opening duel"],
    "clutches": ["clutches", "clutch", "clutched", "1vx"],
    "multikills": ["multikill rounds", "multikills", "multikill", "multi kills", "multi kill"],
    "aces": ["aces", "5ks", "5k", "five kill rounds", "5 kill rounds"],
    "traded": ["dead for trade kill", "dead for trade", "deaths traded", "traded deaths", "got traded", "was traded"],
    "trade_kills": ["trade kills", "trade kill", "trades", "trading"],
    "plants": ["plants", "plant", "planted", "planting"],
    "defuses": ["defuses", "defuse", "defused", "defusing", "disables", "disable", "disabled"],
    "objectives": ["objectives", "objective"],
    "win_rate": ["win rate", "winrate", "win percentage", "win percent", "win ratio", "winning percentage", "win",
                 "winning"],
    "wins": ["wins", "won", "victories"],
    "losses": ["losses", "loss", "lost", "lose", "losing", "defeats"],
    "kills": ["kills", "kill", "frags", "fragged", "fraggers", "fragger", "fragging"],
    "deaths": ["deaths", "death", "died", "dies", "die", "dying"],
    "assists": ["assists", "assist"],
    "rounds": ["rounds played", "rounds"],
    "matches": ["matches played", "games played", "matches", "games"],
}
_PHRASES = sorted(((p, k) for k, ps in METRIC_WORDS.items() for p in ps), key=lambda x: -len(x[0]))
_KEY_OF = {p: k for k, ps in METRIC_WORDS.items() for p in ps}

# ------------------------------------------------------------- dimensions --


@dataclass(frozen=True)
class Dim:
    key: str
    label: str
    sql: str


DIMS: dict[str, Dim] = {d.key: d for d in [
    Dim("player", "Player", "t.player_key"),
    Dim("map", "Map", "m.map"),
    Dim("operator", "Operator", "t.operator"),
    Dim("side", "Side", "t.side"),
    Dim("site", "Site", "t.site"),
    Dim("match_type", "Match type", "m.match_type"),
    Dim("match", "Match", "t.match_id"),
    Dim("player_match", "Player · match", "t.player_key || ' ' || t.match_id"),  # one player's one match
    Dim("day", "Day", "substr(m.played_at, 1, 10)"),
    Dim("week", "Week of", "date(m.played_at, '-6 days', 'weekday 1')"),
    Dim("month", "Month", "substr(m.played_at, 1, 7)"),
]}
ROUND_DIMS = {"operator", "side", "site"}
TIME_DIMS = {"match", "day", "week", "month"}

# ------------------------------------------------------------------- words --

_NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())}
_NUM = r"(\d+|" + "|".join(_NUMBER_WORDS) + r")"
_MONTHS = {m: i for i, names in enumerate([
    (), ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
    ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
    ("dec", "december")]) for m in names}
_MONTH = "(?:" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + ")"
_DATE = (r"(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?|" + _MONTH + r" \d{1,2}(?:st|nd|rd|th)?(?: \d{4})?|"
         r"\d{1,2}(?:st|nd|rd|th)? " + _MONTH + r"(?: \d{4})?)")
_UNIT_DAYS = {"day": 1, "days": 1, "week": 7, "weeks": 7, "month": 30, "months": 30}

# words that are never a player's name (in a question, or as the part of a name before a clan tag)
_COMMON = set("""
a about above after against all am an and any anyone are as at average avg be been best better between biggest
both bottom by can compare compared count data day days did do does doing done each every ever everyone everybody
fewest first for from game games get give good great had has have highest how i if im in is it its last least
list lobby lowest many match matches max me mine min month months more most much my myself number of on one only
or our ours over overall past per played player players playing plays recent recently rank ranking record round
rounds show since so stats statistics than that the their them then there these they this those time times to
today top total trend trending up us versus vs was we week weeks were what when where which who whos why will with
worst year yesterday you your team teams teammate teammates opponent opponents enemy enemies side sides map maps
operator operators op ops attack attacking defense defending defence defend ranked unranked mode summary overview
profile improving improve improved changed change progress history lately chart graph most played favorite
favourite main play better worse doing official officials scrim scrims scrimmage scrimmages practice practices
practicing casual casuals tag tagged
""".split())

_WORDS = {  # each idea's wordings, matched as whole words in the cleaned question
    "me": r"\b(i|me|my|mine|myself|im|ive|id)\b",
    "we": r"\b(we|us|our|ours|ourselves|my team|our team|my squad|the team|the teams|the squad)\b",
    "teammates": r"\b(teammates?|team mates?|who i play with|played with|play with)\b",
    "opponents": r"\b(opponents?|enem(y|ies)|against me|against us|the other team)\b",
    "everyone": r"\b(everyone|everyones|everybody|everybodys|all players|anyone|any player|the lobby|in the lobby)\b",
    "trend": r"\b(over time|trend|trends|trending|progress|progression|improv(e|ed|es|ing|ement)|getting (better|worse)"
             r"|declin(e|ed|ing)|change[ds]?|changing|timeline|graph|chart|plot|per match|each match|every match|"
             r"match by match|per game|each game|by match|by game|over my|over our|over the)\b(?! types?\b)",
    "day": r"\b(per day|by day|each day|daily|day by day)\b",
    "week": r"\b(per week|by week|each week|weekly|week by week)\b",
    "month": r"\b(per month|by month|each month|monthly|month by month)\b",
    "compare": r"\b(compare|compared|comparison|versus|vs|or|between|side by side)\b",
    "count": r"\b(how many|number of|how often|count of|total number)\b",
    "record": r"\b(record|win loss|wl|wins and losses|win and loss)\b",
    "summary": r"\b(how (am i|are we|is|was|were|are|did|do|does|has|have)\b.*\b(doing|done|playing|performing|going)"
               r"|stats|statistics|summary|overview|profile|report card|tell me about|numbers|how good)\b",
    "list": r"\b(show|list|which matches|what matches|my matches|our matches|match history|games where|matches where|"
            r"recent matches|recent games|every match|all matches|all games|see matches|matches did|games did)\b",
    "most_played": r"\b(most played|played most|play most|play the most|played the most|most often|favou?rite|main|"
                   r"mains|pick most|picked most|use most|used most|most used|most picked|play with most|"
                   r"played with most|play with the most|played with the most)\b",
}
_GROUP_WORDS = {  # the first that matches decides the grouping
    "side": r"\b(sides|by side|per side|each side|which side|what side)\b",
    "site": r"\b(sites|by site|per site|each site|which site|what site|bomb sites?|best site|worst site)\b",
    "match_type": r"\b(ranked (vs|or|and|versus) unranked|unranked (vs|or|and|versus) ranked|match types?|"
                  r"game types?|by playlist|by mode|playlists?|queues)\b",
    "operator": r"\b(operators|ops|by operator|per operator|each operator|which operators?|what operators?|"
                r"which ops?|best operators?|worst operators?|operator|op)\b",
    "rank": r"\b(rank|ranking|rankings|leaderboard|standings?|what place|which place)\b",
    "map": r"\b(maps|by map|per map|each map|which map|what map|best map|worst map|on what map|"
           r"where (do|did) (i|we) (do|play|perform|win)|where (am|are) (i|we) (best|worst|strongest|weakest))\b",
    "player": r"\b(who|whos|whom|which players?|what players?|players|by player|per player|each player|"
              r"teammates?|best player|worst player|top players?|mvp|fraggers?)\b",
}
# the match types a question can name (parser.py's names), to say when there are none of one
_MATCH_TYPE_WORDS = {"ranked": ("Ranked", "Ranked matches"), "unranked": ("Unranked", "Unranked matches"),
                     "quick": ("Quick Match", "Quick Matches"), "custom": ("Custom game", "custom games")}
# the tags a match can have on Match History (stats_db.NOTE_TAGS), as a question names them: "in scrims"
_TAG_WORDS = {"Official": r"\bofficials?\b", "Scrim": r"\bscrim(s|mage|mages)?\b",
              "Practice": r"\bpractic(e|es|ing)\b", "Casual": r"\bcasuals?\b"}
# words before a name that make it an operator: "as Rook", "playing Rook"
_AS_OPERATOR = {"as", "play", "plays", "playing", "played", "pick", "picks", "picking", "picked", "main", "mains",
                "maining", "using"}
# "me and my teammates", "where do I rank among my teammates": a comparison that includes me
_WITH_ME = (r"\b(compare|compared|comparison|vs|versus|against|rank|ranking|place|stack up|stacks up|measure up|"
            r"me and|and me|i and|and i)\b")
_TEAMMATES_PHRASE = (r"\b(my|our) team ?mates?\b|\b(who|which players?)? ?(do|did|have)? ?i (usually |most |often )?"
                     r"(play|played|queue|queued) with\b")
_BEST = r"\b(best|top|strongest|greatest|better|good|great|leading|mvp)\b"
_WORST = r"\b(worst|bottom|weakest|worse|bad|poor|poorest|struggl\w*)\b"
_HIGH = r"\b(most|highest|max|maximum|more|largest|biggest|higher)\b"
_LOW = r"\b(least|lowest|min|minimum|fewest|fewer|less|smallest|lower)\b"

# ------------------------------------------------------------------ helpers --


def fold(text: str) -> str:
    """Lowercase, accents off: "Jäger" -> "jager", "Nøkk" -> "nokk"."""
    text = text.replace("ø", "o").replace("Ø", "O").replace("æ", "ae").replace("ß", "ss")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()


def loose(text: str) -> str:
    """Only letters and digits: "Paltry.FBRD" -> "paltryfbrd"."""
    return re.sub(r"[^0-9a-z]", "", fold(text))


MAX_NUMBER = 10_000  # "last 999999999999 matches" is every match, not a number too big for SQLite or a date


def _number(word: str) -> int:
    return min(int(word), MAX_NUMBER) if word.isdigit() else _NUMBER_WORDS[word]


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text) is not None


_KNOWN_WORDS = set(_COMMON) | {loose(p) for p, _ in _PHRASES}


@dataclass
class Entity:
    kind: str       # player, team, map, operator
    value: str      # player key, team name, map, operator
    display: str
    start: int      # character span in the question
    end: int


@dataclass
class Vocab:
    """What the database knows: the names a question can mention."""
    players: dict[str, str]              # player key -> name as last seen
    me: str | None                       # the user's player key
    rosters: dict[str, list[str]]        # team -> player keys
    maps: list[str]
    operators: list[str]
    match_types: list[str]
    aliases: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # loose text -> [(kind, value)]
    tags: list[str] = field(default_factory=list)  # the tags given to matches on Match History

    @classmethod
    def from_db(cls, db: StatsDB) -> "Vocab":
        me = db.me()
        vocab = cls(players=db.players(), me=me.casefold() if me else None,
                    rosters={t: [p.casefold() for p in ps] for t, ps in db.rosters().items()},
                    maps=db.distinct("map"), operators=db.distinct("operator"),
                    match_types=db.distinct("match_type"), tags=db.tags())
        vocab.build_aliases()
        return vocab

    def build_aliases(self) -> None:
        aliases: dict[str, list[tuple[str, str]]] = {}

        def add(alias: str, kind: str, value: str) -> None:
            if alias and (kind, value) not in aliases.setdefault(alias, []):
                aliases[alias].append((kind, value))

        for team in self.rosters:
            add(loose(team), "team", team)
        firsts = [loose(m.split()[0]) for m in self.maps]
        for m, first in zip(self.maps, firsts):
            add(loose(m), "map", m)
            if " " in m and len(first) >= 4 and first not in _KNOWN_WORDS and firsts.count(first) == 1:
                add(first, "map", m)  # "kafe" for Kafe Dostoyevsky
        for op in self.operators:
            add(loose(op), "operator", op)
        taken = set(aliases) | _KNOWN_WORDS
        for key, name in self.players.items():
            add(loose(name), "player", key)
        # the part of a name either side of its clan tag ("Paltry" for Paltry.FBRD) when only one player has it
        parts: dict[str, set[str]] = {}
        for key, name in self.players.items():
            for part in re.split(r"[^0-9a-z]+", fold(name)):
                if len(part) >= 3 and part not in taken:
                    parts.setdefault(part, set()).add(key)
        for part, keys in parts.items():
            if len(keys) == 1 and part not in aliases:
                add(part, "player", next(iter(keys)))
        self.aliases = aliases

    def display(self, kind: str, value: str) -> str:
        return self.players.get(value, value) if kind == "player" else value

    def find(self, question: str) -> tuple[list[Entity], list[str]]:
        """The names in a question (longest match first), and notes about any guesses."""
        tokens = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"[^\s,;:!?()\"]+", question)]
        used = [False] * len(tokens)
        found: list[Entity] = []
        notes: list[str] = []
        for size in (4, 3, 2, 1):
            for i in range(len(tokens) - size + 1):
                if any(used[i:i + size]):
                    continue
                start, end = tokens[i][1], tokens[i + size - 1][2]
                typed = question[start:end].rstrip(".?!")
                text = re.sub(r"(?:'s|’s|'|’)$", "", typed)
                candidates = self.aliases.get(loose(text), [])
                if candidates:
                    before = re.findall(r"[a-z]+", fold(question[:start]))[-1:]
                    kind, value, note = self._pick(candidates, text, possessive=text != typed,
                                                   as_operator=bool(before) and before[0] in _AS_OPERATOR)
                    found.append(Entity(kind, value, self.display(kind, value), start, end))
                    used[i:i + size] = [True] * size
                    if note:
                        notes.append(note)
        # a name typed slightly wrong: "Paltri" -> Paltry.FBRD; one that looks like a gamertag but matches
        # nobody is pointed out rather than quietly ignored
        for i, (token, start, end) in enumerate(tokens):
            word = loose(re.sub(r"(?:'s|’s)$", "", token))
            if used[i] or len(word) < 4 or word in _KNOWN_WORDS or re.fullmatch(r"[\d/\-.:+%]+", token):
                continue
            close = difflib.get_close_matches(word, list(self.aliases), n=1, cutoff=0.8) if len(word) >= 5 else []
            if close:
                kind, value, _ = self._pick(self.aliases[close[0]], token)
                found.append(Entity(kind, value, self.display(kind, value), start, end))
                used[i] = True
                notes.append(f"I took “{token}” to mean {self.display(kind, value)}.")
            elif re.search(r"\d|[._\-]|[a-z][A-Z]", token.strip(".?!,")):
                notes.append(f"I don't know anyone called “{token.strip('.?!,')}” in your matches.")
        found.sort(key=lambda e: e.start)
        return found, notes

    def _pick(self, candidates: list[tuple[str, str]], text: str, possessive: bool = False,
              as_operator: bool = False) -> tuple[str, str, str | None]:
        """Which of the things a name matches is meant, and a note when it could be a player too."""
        # a player typed exactly as their name ("Rook-_-") beats the operator Rook
        for kind, value in candidates:
            if kind == "player" and fold(self.players.get(value, "")) == fold(text):
                return kind, value, None
        order = {"team": 0, "map": 1, "operator": 2, "player": 3}
        first = min(candidates, key=lambda c: order[c[0]])
        player = next((c for c in candidates if c[0] == "player"), None)
        if player is None or player == first or (as_operator and first[0] == "operator"):  # "as Rook"
            return first[0], first[1], None
        # "Rook's K/D" is the player Rook-_-; "my K/D as Rook" is the operator
        chosen, other = (player, first) if possessive and first[0] == "operator" else (first, player)
        return chosen[0], chosen[1], (f"“{text}” could be {self._describe(*chosen)} or {self._describe(*other)}: "
                                       f"I went with {self._describe(*chosen)}.")

    def _describe(self, kind: str, value: str) -> str:
        return f"the {kind} {self.display(kind, value)}"


def _clean(question: str, entities: list[Entity]) -> str:
    """The question for keyword matching: names blanked out, lowercase, accents off, "K/D" -> "kd",
    and punctuation dropped apart from what numbers and dates need."""
    text = question
    for e in sorted(entities, key=lambda e: e.start, reverse=True):
        text = text[:e.start] + " @ " + text[e.end:]
    text = fold(text)
    text = re.sub(r"\bk\s*[/\-]\s*d\b", "kd", text)
    text = re.sub(r"\bw\s*/\s*l\b", "wl", text)
    text = re.sub(r"(\d)\s*%", r"\1 percent", text).replace("%", " percent ")
    text = re.sub(r"[’']", "", text)
    text = re.sub(r"[^0-9a-z+<>=./@ -]", " ", text)
    text = re.sub(r"(?<!\d)[/-]|[/-](?!\d)", " ", text)   # "/" and "-" only inside dates
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)       # "." only inside numbers
    return re.sub(r"\s+", " ", text).strip()


def _find_metrics(text: str) -> list[str]:
    """Metric keys in the order they're mentioned; the longest wording wins ("kills per round", not "kills")."""
    spans: list[tuple[int, int, str]] = []
    for phrase, key in _PHRASES:
        for m in re.finditer(r"\b" + re.escape(phrase) + r"\b", text):
            if not any(s < m.end() and m.start() < e for s, e, _ in spans):
                spans.append((m.start(), m.end(), key))
    ordered: list[str] = []
    for _, _, key in sorted(spans):
        if key not in ordered:
            ordered.append(key)
    return ordered


def _date(text: str, now: datetime) -> datetime | None:
    """A date written in a question: 2026-09-20, 9/20, Sep 20, 20 September 2026."""
    try:
        if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text):
            return datetime(*map(int, m.groups()))
        explicit_year = re.search(r"\d{4}|\d/\d{1,2}/\d{2,4}", text) is not None
        if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", text):
            year = int(m.group(3)) if m.group(3) else now.year
            found = datetime(year + 2000 if year < 100 else year, int(m.group(1)), int(m.group(2)))
        elif m := re.fullmatch(r"(" + _MONTH + r") (\d{1,2})(?:st|nd|rd|th)?(?: (\d{4}))?", text):
            found = datetime(int(m.group(3) or now.year), _MONTHS[m.group(1)], int(m.group(2)))
        elif m := re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)? (" + _MONTH + r")(?: (\d{4}))?", text):
            found = datetime(int(m.group(3) or now.year), _MONTHS[m.group(2)], int(m.group(1)))
        else:
            return None
    except ValueError:  # "Feb 30"
        return None
    if found > now and not explicit_year:
        found = found.replace(year=found.year - 1)  # "Dec 30" asked in January means last year
    return found


def _stamp(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S")


# -------------------------------------------------------------------- query --


@dataclass
class Query:
    """A question, understood."""
    kind: str = "value"          # value, rank, trend, compare, count, summary, matches, unknown
    metrics: list[str] = field(default_factory=list)
    group: str | None = None     # a DIMS key
    who: str = "me"              # me, players, team, my_team, teammates, opponents, everyone
    players: list[str] = field(default_factory=list)   # player keys
    team: str | None = None
    team_min: int | None = None  # a team played a match when this many of it were on one side (default 3)
    teams: list[str] = field(default_factory=list)     # every team named (for a comparison)
    compare: str | None = None   # what a comparison is between: player, team, map, operator, side, match_type
    maps: list[str] = field(default_factory=list)
    operators: list[str] = field(default_factory=list)
    side: str | None = None
    match_type: str | None = None
    match_types: list[str] = field(default_factory=list)  # the two compared in "ranked vs custom"
    tag: str | None = None       # only matches tagged this on Match History ("in scrims")
    won: int | None = None       # only matches won (1) or lost (0)
    since: str | None = None     # "YYYY-MM-DD HH:MM:SS", inclusive
    until: str | None = None     # exclusive
    last_matches: int | None = None
    first_matches: int | None = None
    periods: list[tuple[str, str, str | None]] = field(default_factory=list)  # compared: (label, since, until)
    order: str | None = None     # best, worst, high, low
    limit: int | None = None
    conditions: list[tuple[str, str, float]] = field(default_factory=list)  # (metric, op, value)
    min_matches: int | None = None
    min_rounds: int | None = None
    highlight_me: bool = False
    most_played: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def level(self) -> str:
        """Which table answers it: per round for operators/sides/sites and aces, else per match."""
        if self.group in ROUND_DIMS or self.operators or self.side or \
                any(METRICS[m].match_sql is None for m in self.metrics):
            return "round"
        if self.metrics == ["rounds"] and self.who not in ("me", "players"):
            return "round"  # a team's rounds are distinct rounds, not player-rounds
        return "match"


def _read_time(text: str, q: Query, now: datetime) -> str:
    """Time filters -> q.since / until / last_matches / first_matches; returns the text without them."""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def take(pattern: str) -> re.Match | None:
        nonlocal text
        m = re.search(pattern, text)
        if m:
            text = text[:m.start()] + " " + text[m.end():]
        return m

    if m := take(r"\b(last|past|previous|recent|latest|most recent) " + _NUM + r" (matches|match|games|game)\b"):
        q.last_matches = _number(m.group(2))
    if m := take(r"\bfirst " + _NUM + r" (matches|match|games|game)\b"):
        q.first_matches = _number(m.group(1))
    if take(r"\b(my |our |the )?(last|latest|previous|most recent) (match|game)\b"):
        q.last_matches = 1
    if m := take(r"\b(last|past) " + _NUM + r" (days|day|weeks|week|months|month)\b"):
        q.since = _stamp(today - timedelta(days=_number(m.group(2)) * _UNIT_DAYS[m.group(3)] - 1))
    original = text
    periods: list[tuple[int, str, datetime, datetime | None]] = []  # (where in the question, label, start, end)
    ranges = [
        ("yesterday", today - timedelta(days=1), today),
        ("today", today, None),
        ("last week", today - timedelta(days=today.weekday() + 7), today - timedelta(days=today.weekday())),
        ("this week", today - timedelta(days=today.weekday()), None),
        ("last month", (today.replace(day=1) - timedelta(days=1)).replace(day=1), today.replace(day=1)),
        ("this month", today.replace(day=1), None),
        ("this year", today.replace(month=1, day=1), None),
    ]
    for words, start, end in ranges:
        if take(r"\b" + words + r"\b"):
            periods.append((original.find(words), words, start, end))
    if m := take(r"\bbetween (" + _DATE + r") and (" + _DATE + r")"):
        a, b = _date(m.group(1), now), _date(m.group(2), now)
        if a and b:
            q.since, q.until = _stamp(min(a, b)), _stamp(max(a, b) + timedelta(days=1))
    for word, attr, shift in (("since", "since", 0), ("from", "since", 0), ("after", "since", 1),
                              ("before", "until", 0), ("until", "until", 1), ("on", "on", 0)):
        m = re.search(r"\b" + word + r" (" + _DATE + r")", text)
        if m and (d := _date(m.group(1), now)):
            text = text[:m.start()] + " " + text[m.end():]
            if attr == "on":
                q.since, q.until = _stamp(d), _stamp(d + timedelta(days=1))
            else:
                setattr(q, attr, _stamp(d + timedelta(days=shift)))
    # "in September"; and when comparing, bare month names too ("September vs August")
    months = r"\b(?:in |during )" if not _has(_WORDS["compare"], text) else r"\b(?:in |during )?"
    while m := take(months + r"(" + _MONTH + r")(?: (\d{4}))?\b"):
        month = _MONTHS[m.group(1)]
        year = int(m.group(2)) if m.group(2) else (now.year if month <= now.month else now.year - 1)
        start = datetime(year, month, 1)
        label = start.strftime("%B") + (f" {year}" if year != now.year else "")
        periods.append((original.find(m.group(1)), label, start,
                        datetime(year + (month == 12), month % 12 + 1, 1)))
    if len(periods) >= 2:  # "this week vs last week": a comparison of periods, in the order asked
        q.periods = [(label, _stamp(s), _stamp(e) if e else None) for _, label, s, e in sorted(periods)]
    elif periods:
        _, _, start, end = periods[0]
        q.since, q.until = _stamp(start), (_stamp(end) if end else None)
    if q.last_matches is None and q.since is None and take(r"\b(recently|lately|recent|these days|nowadays)\b"):
        q.last_matches = 10
        q.notes.append("“Recently” means the last 10 matches.")
    return re.sub(r"\s+", " ", text).strip()


_OPS = {"at least": ">=", "atleast": ">=", "minimum of": ">=", "more than": ">", "greater than": ">", "over": ">",
        "above": ">", "less than": "<", "fewer than": "<", "under": "<", "below": "<", "at most": "<=",
        "no more than": "<=", ">=": ">=", "<=": "<=", ">": ">", "<": "<"}
_OP_RE = "|".join(re.escape(w) for w in sorted(_OPS, key=len, reverse=True))
_METRIC_RE = "|".join(re.escape(p) for p, _ in _PHRASES)


def _read_conditions(text: str, q: Query) -> str:
    """"more than 10 kills", "10+ kills", "K/D above 1.5", "at least 5 matches" -> q.conditions / min sample."""

    def add(word: str, op: str, value: str) -> None:
        key = _KEY_OF[word]
        if key in SAMPLE_METRICS and op in (">=", ">"):  # "at least 5 matches": a minimum sample
            number = int(float(value)) + (op == ">")
            if key == "matches":
                q.min_matches = number
            else:
                q.min_rounds = number
        else:
            q.conditions.append((key, op, float(value)))

    patterns = [
        (r"\b(" + _OP_RE + r") ?(\d+(?:\.\d+)?) ?(" + _METRIC_RE + r")\b", lambda m: add(m[3], _OPS[m[1]], m[2])),
        (r"\b(\d+) ?\+ ?(" + _METRIC_RE + r")\b", lambda m: add(m[2], ">=", m[1])),
        (r"\b(" + _METRIC_RE + r") (?:of )?(" + _OP_RE + r") ?(\d+(?:\.\d+)?)\b",
         lambda m: add(m[1], _OPS[m[2]], m[3])),
    ]
    for pattern, handle in patterns:
        while m := re.search(pattern, text):
            handle(m)
            text = text[:m.start()] + " " + text[m.end():]
    return re.sub(r"\s+", " ", text).strip()


def interpret(question: str, vocab: Vocab, now: datetime | None = None) -> Query:
    """Read a plain-English question into a Query."""
    now = now or datetime.now()
    q = Query()
    entities, q.notes = vocab.find(question)
    text = _clean(question, entities)
    text = _read_time(text, q, now)
    text = _read_conditions(text, q)

    players = list(dict.fromkeys(e.value for e in entities if e.kind == "player"))
    q.teams = list(dict.fromkeys(e.value for e in entities if e.kind == "team"))
    q.team = q.teams[0] if q.teams else None
    q.maps = list(dict.fromkeys(e.value for e in entities if e.kind == "map"))
    q.operators = list(dict.fromkeys(e.value for e in entities if e.kind == "operator"))
    wants_we = _has(_WORDS["we"], text)
    wants_me = _has(_WORDS["me"], re.sub(_WORDS["we"], " ", text))
    if wants_me and vocab.me and vocab.me not in players:
        players.insert(0, vocab.me)

    text = re.sub(r"\branked (by|on|in terms of)\b", "rank by", text)  # "ranked by K/D" orders; it isn't Ranked
    types = text.replace("quick play", "quick").replace("quickplay", "quick")
    named_types = [mt for mt in vocab.match_types  # "ranked", "unranked", "quick match", "custom games", "customs"
                   if (word := re.sub(r" ?(match|game)$", "", loose(mt))) and _has(r"\b" + word + r"s?\b", types)]
    # one named that isn't in the stats ("my kd in unranked" with no Unranked matches) finds nothing, and says why
    missing = [(match_type, phrase) for word, (match_type, phrase) in _MATCH_TYPE_WORDS.items()
               if match_type not in vocab.match_types and _has(r"\b" + word + r"s?\b", types)]
    q.match_type = named_types[-1] if named_types else missing[-1][0] if missing else None
    q.notes += [f"There are no {phrase} in your stats." for _, phrase in missing]
    tags = [tag for tag, pattern in _TAG_WORDS.items() if _has(pattern, text)]
    q.tag = tags[0] if len(tags) == 1 else None
    if len(tags) > 1:
        q.notes.append(f"Ask looks at one tag at a time, so this is every match: ask about {' and '.join(tags)} "
                       "matches separately.")
    if q.tag and q.tag not in vocab.tags:  # finds nothing, and says why
        q.notes.append(f"No match is tagged {q.tag} yet: tag your matches on Match History.")
    # only wins / losses: the words go once read, so "wins" isn't also taken as the thing to count
    for won, pattern in ((1, r"\b(in|from|during|only) (my |our |the )?(wins|victories)\b|\bwhen (i|we) (won|win)\b|"
                             r"\bmatches (i|we) won\b"),
                         (0, r"\b(in|from|during|only) (my |our |the )?losses\b|\bwhen (i|we) (lost|lose)\b|"
                             r"\bmatches (i|we) lost\b")):
        if m := re.search(pattern, text):
            q.won = won
            text = text[:m.start()] + " " + text[m.end():]
            break

    attack = _has(r"\b(attack|attacking|attacker|attackers|atk|offense|offence)\b", text)
    defense = _has(r"\b(defen[cs]e|defending|defender|defenders|def|defend)\b", text)
    q.most_played = _has(_WORDS["most_played"], text)

    group = next((g for g, pattern in _GROUP_WORDS.items() if _has(pattern, text)), None)
    if group == "rank":  # "where do I rank", "leaderboard": players, unless it says what else to rank
        group = next((g for g in ("map", "operator", "side", "site") if _has(_GROUP_WORDS[g], text)), "player")
    if q.most_played and group is None:
        group = "operator" if q.operators or _has(r"\bcharacters?\b", text) else "map"
    if attack and defense:
        group = "side"
    elif attack or defense:
        q.side = "attack" if attack else "defense"
    for g in ("day", "week", "month"):
        if _has(_WORDS[g], text):
            group = g
    if _has(_WORDS["trend"], text) and group not in ("day", "week", "month"):
        group = "match"
    # "most kills in a match", "my best games": single matches ranked, not totals or a trend
    single_match = _has(r"\bin (?:a|one|1|a single|one single|any) (?:match|game)\b|\bsingle (?:match|game)\b|"
                        r"\b(?:best|worst|top|highest|lowest) (?:match|game)(?:es|s)?\b(?! type)", text)
    if single_match and group in (None, "match", "player"):
        group = "player_match" if group == "player" else "match"
    if group is None and len(named_types) >= 2:
        group = "match_type"  # "ranked vs custom"
    if group == "match_type":
        q.match_type = None  # "ranked vs unranked" compares them rather than keeping one
    # "most defuses", "best K/D", "opponents with the best K/D": with nobody named, that's a ranking of players
    if group is None and _has("|".join((_BEST, _WORST, _HIGH, _LOW)), text) and not (
            wants_me or wants_we or players or q.team or q.maps or q.operators or q.side
            or _has(_WORDS["record"], text) or _has(_WORDS["count"], text)):
        group = "player"

    # whose stats
    ranking_players = group in ("player", "player_match")
    if _has(_WORDS["teammates"], text) and ranking_players:
        if vocab.me and _has(_WITH_ME, text) and _has(_WORDS["me"], re.sub(_TEAMMATES_PHRASE, " ", text)):
            q.who, q.highlight_me = "my_team", True  # my whole side, me included
        else:
            q.who = "teammates"
    elif _has(_WORDS["opponents"], text):
        q.who = "opponents"
    elif _has(_WORDS["everyone"], text):
        q.who = "everyone"
    elif q.team:
        q.who = "team"
    elif players and not (ranking_players and players == [vocab.me]):
        q.who, q.players = "players", players
    elif wants_we:
        q.who = "my_team"
    elif ranking_players:
        q.who = "everyone"
        q.highlight_me = wants_me and vocab.me is not None
    else:
        q.who = "me"

    # what to measure: "matches"/"rounds" are only the thing asked about in counts and "most played"
    found = _find_metrics(text)
    counting = _has(_WORDS["count"], text)
    q.metrics = [m for m in found if m not in SAMPLE_METRICS or counting or q.most_played]
    if counting and any(m not in SAMPLE_METRICS for m in q.metrics):
        q.metrics = [m for m in q.metrics if m not in SAMPLE_METRICS]  # "how many matches have we won": wins
    if not q.metrics and counting:
        q.metrics = [m for m in found if m in SAMPLE_METRICS][:1]
    if _has(_WORDS["record"], text):
        q.metrics = RECORD + [m for m in q.metrics if m not in RESULT_METRICS]

    if _has(_BEST, text):
        q.order = "best"
    elif _has(_WORST, text):
        q.order = "worst"
    elif _has(_HIGH, text):
        q.order = "high"
    elif _has(_LOW, text):
        q.order = "low"
    if q.most_played:
        q.order = "high"
    if m := re.search(r"\b(?:top|best|worst|bottom|highest|lowest) " + _NUM + r"\b", text) or \
            re.search(r"\b" + _NUM + r" (?:best|worst|top|highest|lowest|most|least)\b", text):
        q.limit = _number(m.group(1))

    # what kind of answer
    compare = [k for k, n in (("player", len(players)), ("team", len(q.teams)), ("map", len(q.maps)),
                              ("operator", len(q.operators))) if n >= 2]
    if len(q.periods) >= 2:
        q.kind, q.compare, q.group = "compare", "period", None
    elif compare and (_has(_WORDS["compare"], text) or group in (None, compare[0])):
        q.kind, q.compare = "compare", compare[0]
        q.group = {"player": "player", "map": "map", "operator": "operator"}.get(q.compare)
        if q.compare == "player":
            q.who, q.players = "players", players
        elif q.compare == "team":
            q.who = "team"
    elif group == "side" and attack and defense:
        q.kind, q.compare, q.group = "compare", "side", "side"
    elif single_match and group in ("match", "player_match"):
        q.kind, q.group = "rank", group
    elif group == "match_type" and len(named_types) == 2:
        q.kind, q.compare, q.group, q.match_types = "compare", "match_type", "match_type", named_types
    elif group in TIME_DIMS:
        q.kind, q.group = "trend", group
    elif group:
        q.kind, q.group = "rank", group
    elif counting:
        # "how many teabags": something it can't count is said so, not answered with a count of matches
        noun = re.search(r"\b(?:how many|number of|count of) ([a-z]+)", text)
        if not q.metrics and noun and noun.group(1) not in _KNOWN_WORDS:
            q.kind = "unknown"
            q.notes.append(f"I don't know how to count “{noun.group(1)}” yet.")
        else:
            q.kind = "count"
    elif (_has(_WORDS["list"], text) or (q.conditions and not q.metrics)) and \
            (_has(r"\b(matches|match|games|game)\b", text) or q.last_matches or q.conditions):
        q.kind = "matches"
    elif not q.metrics and _has(_WORDS["summary"], text):
        q.kind = "summary"
    elif not q.metrics and q.last_matches:
        q.kind = "matches"
    elif not q.metrics and (entities or wants_me or wants_we or q.side or q.match_type or q.tag):
        q.kind = "summary"
    elif q.metrics:
        q.kind = "value"
    else:
        q.kind = "unknown"

    _fill_defaults(q, vocab)
    return q


def _fill_defaults(q: Query, vocab: Vocab) -> None:
    round_level = q.group in ROUND_DIMS or bool(q.operators) or bool(q.side) or \
        any(METRICS[m].match_sql is None for m in q.metrics)
    if not q.metrics:
        if q.kind == "rank" and q.group == "player":
            q.metrics = ["matches"] if q.most_played else ["eps"]
        elif q.kind == "rank" and q.group in ("match", "player_match"):
            q.metrics = ["eps"]  # "my best game"
        elif q.kind == "rank" and q.most_played:
            q.metrics = ["rounds" if round_level else "matches"]
        elif q.kind in ("rank", "compare") and q.group in ("map", "operator", "side", "site", "match_type"):
            q.metrics = ["win_rate", "kd"]
        elif q.kind == "trend":
            q.metrics = ["kd"]
        elif q.kind == "count":
            q.metrics = ["matches"]
        elif q.kind == "compare" and q.compare == "team":
            q.metrics = ["win_rate", "wins", "losses", "matches"]  # teams compared by their results
        elif q.kind == "compare" and q.compare in ("player", "period") and not round_level:
            q.metrics = ["kd", "eps", "win_rate", "kpr", "hs", "kost", "survival", "entry", "clutches", "matches"]
        elif q.kind in ("summary", "compare"):
            q.metrics = list(SUMMARY_ROUND if round_level else SUMMARY_MATCH)
    if round_level and "eps" in q.metrics:  # EPS belongs to a whole match
        q.metrics = [m for m in q.metrics if m != "eps"] or ["kd"]
        if q.kind not in ("summary", "compare"):
            q.notes.append("EPS is worked out per match, so it can't be split by round: showing K/D instead.")
    if q.who in ("everyone", "opponents") and q.group != "player" and any(m in RESULT_METRICS for m in q.metrics) \
            and vocab.me:
        q.who = "me"  # every match has a winner and a loser: everyone together wins exactly half
    if q.who in ("me", "my_team", "teammates", "opponents") and not vocab.me:
        q.notes.append("I can't tell which player you are, so this covers everyone. Name a player to narrow it down.")
        q.who = "everyone"
    if q.kind == "rank" and q.group == "player" and q.min_matches is None:
        q.min_matches = {"everyone": 3, "opponents": 2, "teammates": 2, "my_team": 2}.get(q.who, 1)
        if q.last_matches:  # "top fraggers in my last 5 matches": in half of them, not 3 of 5
            q.min_matches = min(q.min_matches, max(1, q.last_matches // 2))
    if q.kind == "rank" and q.group in ("operator", "site") and q.min_rounds is None and not q.most_played:
        q.min_rounds = 5
    if q.kind == "rank" and q.group == "map" and q.min_matches is None and not q.most_played:
        q.min_matches = 2
    if q.kind == "rank" and q.group in ("player", "match", "player_match") and q.limit is None:
        q.limit = 10
    if q.kind == "matches" and q.limit is None:
        q.limit = q.last_matches or q.first_matches or 10


# ---------------------------------------------------------------------- SQL --


class _SQL:
    """FROM/WHERE for a query, with its parameters in order."""

    def __init__(self, q: Query, vocab: Vocab, level: str):
        self.q, self.vocab, self.level = q, vocab, level
        table = "round_players" if level == "round" else "match_players"
        self.from_ = f"{table} t JOIN matches m ON m.match_id = t.match_id"
        self.where: list[str] = []
        self.params: list[Any] = []
        self._scope()
        self._filters()

    def _in(self, column: str, values: list[Any]) -> None:
        self.where.append(f"{column} IN ({', '.join('?' * len(values)) or 'NULL'})")
        self.params.extend(values)

    def _scope(self) -> None:
        q, me = self.q, self.vocab.me
        mine = "SELECT 1 FROM match_players s WHERE s.match_id = t.match_id AND s.player_key = ? AND s.team {op} t.team"
        if q.who == "players":
            self._in("t.player_key", q.players)
        elif q.who == "me":
            self._in("t.player_key", [me])
        elif q.who == "my_team":
            self.where.append(f"EXISTS ({mine.format(op='=')})")
            self.params.append(me)
        elif q.who == "teammates":
            self.where.append(f"EXISTS ({mine.format(op='=')}) AND t.player_key != ?")
            self.params += [me, me]
        elif q.who == "opponents":
            self.where.append(f"EXISTS ({mine.format(op='!=')})")
            self.params.append(me)
        elif q.who == "team":
            # a team played a match when enough of it was on one side; like Build a team, the side with
            # the most of its players counts, and only its own players' numbers do
            keys = self.vocab.rosters.get(q.team, [])
            marks = ", ".join("?" * len(keys)) or "NULL"
            self.where.append(
                f"t.player_key IN ({marks}) AND EXISTS (SELECT 1 FROM (SELECT match_id, team, COUNT(*) AS n,"
                f" ROW_NUMBER() OVER (PARTITION BY match_id ORDER BY COUNT(*) DESC, team) AS pick"
                f" FROM match_players WHERE player_key IN ({marks}) GROUP BY match_id, team) r"
                f" WHERE r.pick = 1 AND r.n >= ? AND r.match_id = t.match_id AND r.team = t.team)")
            self.params += keys + keys + [max(1, min(q.team_min or 3, len(keys)))]

    def _filters(self) -> None:
        q = self.q
        if q.maps:
            self._in("m.map", q.maps)
        if q.operators and self.level == "round":
            self._in("t.operator", q.operators)
        if q.side and self.level == "round":
            self.where.append("t.side = ?")
            self.params.append(q.side)
        if q.match_type:
            self.where.append("m.match_type = ?")
            self.params.append(q.match_type)
        if q.tag:
            self.where.append("m.match_id IN (SELECT match_id FROM match_notes WHERE tag = ?)")
            self.params.append(q.tag)
        if q.won is not None:
            if self.level == "round":  # the match's result, not the round's
                self.where.append("EXISTS (SELECT 1 FROM match_players w WHERE w.match_id = t.match_id"
                                  " AND w.player_key = t.player_key AND w.won = ?)")
            else:
                self.where.append("t.won = ?")
            self.params.append(q.won)
        if q.since:
            self.where.append("m.played_at >= ?")
            self.params.append(q.since)
        if q.until:
            self.where.append("m.played_at < ?")
            self.params.append(q.until)
        if q.group in ROUND_DIMS and self.level == "round":
            self.where.append(f"{DIMS[q.group].sql} IS NOT NULL")

    def only(self, match_ids: list[str] | None) -> "_SQL":
        if match_ids is not None:
            self._in("t.match_id", match_ids)
        return self

    def where_sql(self) -> str:
        return (" WHERE " + " AND ".join(self.where)) if self.where else ""


def _metric_sql(key: str, level: str) -> str:
    sql = METRICS[key].round_sql if level == "round" else METRICS[key].match_sql
    if sql is None:  # never a stand-in number: interpret keeps EPS off rounds and aces off matches
        raise ValueError(f"{key} can't be worked out per {level}")
    return sql


def _label(key: str, level: str) -> str:
    return _ROUND_WORDS[key][0] if level == "round" and key in _ROUND_WORDS else METRICS[key].label


def _phrase(key: str, level: str) -> str:
    return _ROUND_WORDS[key][1] if level == "round" and key in _ROUND_WORDS else METRICS[key].phrase


def _match_ids(db: StatsDB, q: Query, vocab: Vocab, level: str) -> list[str] | None:
    """The matches a query is limited to, in play order: those where its conditions held
    ("more than 10 kills") and/or the last/first N; None when it isn't limited."""
    per_match = [c for c in q.conditions] if not (q.kind == "rank" and q.group == "player") else []
    n = q.last_matches or q.first_matches
    if not per_match and not n:
        return None
    sql = _SQL(q, vocab, level)
    params = list(sql.params)
    text = f"SELECT t.match_id FROM {sql.from_}{sql.where_sql()} GROUP BY t.match_id"
    if per_match:
        text += " HAVING " + " AND ".join(f"({_metric_sql(k, level)}) {op} ?" for k, op, _ in per_match)
        params += [v for _, _, v in per_match]
    text += f" ORDER BY MAX(m.played_at) {'ASC' if q.first_matches else 'DESC'}"
    if n:
        text += " LIMIT ?"
        params.append(n)
    ids = [r["match_id"] for r in db.query(text, params)]
    return ids if q.first_matches else ids[::-1]


def _descending(q: Query, metric: str) -> bool:
    if q.most_played or q.order == "high":
        return True
    if q.order == "low":
        return False
    better_high = METRICS[metric].higher_is_better
    return not better_high if q.order == "worst" else better_high


def _aggregate(db: StatsDB, q: Query, vocab: Vocab, metrics: list[str], group: str | None, *,
               ids: list[str] | None = None, level: str | None = None, minimums: bool = True,
               limit: int | None = None) -> list[dict[str, Any]]:
    """One aggregate query: a row per group (or one row), with "key" and a column per metric,
    plus "matches" and "rounds"."""
    level = level or q.level
    sql = _SQL(q, vocab, level).only(ids)
    cols = list(dict.fromkeys(metrics + ["matches", "rounds"]))
    select = [f"{_metric_sql(k, level)} AS {k}" for k in cols]
    params = list(sql.params)
    text = ""
    if group:
        select.insert(0, f"{DIMS[group].sql} AS key")
        if group in ("match", "player_match"):
            select += ["MAX(m.played_at) AS played_at", "MAX(m.map) AS map", "MAX(t.player_key) AS player_key"]
        text = f" GROUP BY {DIMS[group].sql}"
        having = []
        if q.kind == "rank" and group == "player":  # "players with more than 50 kills": their totals
            for k, op, v in q.conditions:
                having.append(f"({_metric_sql(k, level)}) {op} ?")
                params.append(v)
        if minimums and q.min_matches:
            having.append(f"({_metric_sql('matches', level)}) >= ?")
            params.append(q.min_matches)
        if minimums and q.min_rounds and level == "round":
            having.append(f"({_metric_sql('rounds', level)}) >= ?")
            params.append(q.min_rounds)
        if having:
            text += " HAVING " + " AND ".join(having)
        if group in TIME_DIMS and q.kind != "rank":  # a trend, oldest first; "my best matches" rank
            text += " ORDER BY MIN(m.played_at)"
        elif metrics:
            text += f" ORDER BY {metrics[0]} {'DESC' if _descending(q, metrics[0]) else 'ASC'}, matches DESC, key"
        if limit:
            text += " LIMIT ?"
            params.append(limit)
    rows = db.query(f"SELECT {', '.join(select)} FROM {sql.from_}{sql.where_sql()}{text}", params)
    return [r for r in rows if not group or r["key"] is not None]


# ----------------------------------------------------------------- answers --


@dataclass
class Answer:
    ok: bool
    understood: str               # what was looked up, in plain English
    headline: str                 # the answer, in one sentence
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    chart: dict[str, Any] | None = None   # {"kind": "line" | "bar", "x": column, "y": column}
    notes: list[str] = field(default_factory=list)
    formats: dict[str, str] = field(default_factory=dict)  # column -> ratio | pct | count | eps | signed
    query: Query | None = None


def fmt(value: Any, kind: str) -> str:
    """A number as shown to the user: K/D "1.05", "67%", EPS "112", entry "+3"."""
    if value is None:
        return "—"
    if kind == "ratio":
        return f"{value:.2f}"
    if kind == "pct":
        return f"{value:.0f}%"
    if kind == "signed":
        return f"{round_eps(value):+d}"
    return f"{round_eps(value)}"


def _value(value: Any, kind: str) -> Any:
    """A number for a table: rounded the way fmt shows it, still sortable."""
    if value is None:
        return None
    if kind == "ratio":
        return round(value, 2)
    if kind == "pct":
        return round(value, 1)
    return round_eps(value)  # whole numbers, the way the scoreboard rounds EPS


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else ('es' if word.endswith('ch') else 's')}"


_SINGULAR = {"kills": "kill", "deaths": "death", "assists": "assist", "clutches": "clutch", "plants": "plant",
             "aces": "ace",
             "defuser disables": "defuser disable", "wins": "win", "losses": "loss", "trade kills": "trade kill",
             "deaths traded": "death traded", "multikill rounds": "multikill round", "objectives": "objective",
             "entry kills": "entry kill", "entry deaths": "entry death", "rounds won": "round won",
             "rounds lost": "round lost"}


def _count(value: str, phrase: str) -> str:
    """"1 entry kill", "3 entry kills"."""
    return f"{value} {_SINGULAR.get(phrase, phrase) if value == '1' else phrase}"


def _cap(text: str) -> str:
    """The first letter capitalized, and only it: "typhoon.FBRD's" -> "Typhoon.FBRD's"."""
    return text[:1].upper() + text[1:]


def _subject(q: Query, vocab: Vocab) -> tuple[str, str]:
    """(who, whose) for sentences: ("you", "your"), ("Paltry.FBRD", "Paltry.FBRD's"), ..."""
    if q.who == "me" or (q.who == "players" and q.players == [vocab.me]):
        return "you", "your"
    if q.who == "players" and len(q.players) == 1:
        name = vocab.players.get(q.players[0], q.players[0])
        return name, f"{name}'s"
    if q.who == "team":
        return q.team or "the team", f"{q.team}'s"
    return {"my_team": ("your team", "your team's"), "teammates": ("your teammates", "your teammates'"),
            "opponents": ("your opponents", "your opponents'"), "everyone": ("everyone", "everyone's"),
            "players": ("these players", "these players'")}[q.who]


def _filters_text(q: Query, *, conditions: bool = True) -> str:
    bits = []
    if q.maps:
        bits.append("on " + " / ".join(q.maps))
    if q.operators:
        bits.append("as " + " / ".join(q.operators))
    if q.side:
        bits.append(f"on {q.side}")
    if q.match_type:  # "in Ranked", "in Quick Match", "in custom games"
        bits.append(f"in {q.match_type.lower()}s" if q.match_type.endswith(" game") else f"in {q.match_type}")
    if q.tag:  # "in scrims", "in official matches", "in practice"
        bits.append({"Scrim": "in scrims", "Practice": "in practice"}.get(q.tag, f"in {q.tag.lower()} matches"))
    if q.won is not None:
        bits.append("in wins" if q.won else "in losses")
    if q.last_matches:
        bits.append("in the last match" if q.last_matches == 1 else f"in the last {q.last_matches} matches")
    if q.first_matches:
        bits.append(f"in the first {_plural(q.first_matches, 'match')}")
    # q.until is where the time stops (midnight after the last day): say the last day itself
    last = nice_day(_stamp(datetime.strptime(q.until, "%Y-%m-%d %H:%M:%S") - timedelta(seconds=1))) if q.until else None
    if q.since and q.until:
        first = nice_day(q.since)
        bits.append(f"on {first}" if first == last else f"from {first} to {last}")
    elif q.since:
        bits.append(f"since {nice_day(q.since)}")
    elif q.until:
        bits.append(f"through {last}")
    if conditions:
        for key, op, value in q.conditions:
            words = {">": "more than", ">=": "at least", "<": "fewer than" if METRICS[key].kind == "count"
                     else "under", "<=": "at most"}[op]
            where = "" if q.kind == "rank" and q.group == "player" else " in the match"
            bits.append(f"with {words} {value:g} {METRICS[key].phrase}{where}")
    return (" " + ", ".join(bits)) if bits else ""


def _name_rows(rows: list[dict[str, Any]], group: str | None, vocab: Vocab) -> None:
    for r in rows:
        if group == "player":
            r["name"] = vocab.players.get(r["key"], r["key"])
        elif group == "side":
            r["name"] = (r["key"] or "").capitalize()
        elif group == "match":
            r["name"] = f"{nice_time(r.get('played_at'))} · {r.get('map') or ''}".strip(" ·")
        elif group == "player_match":
            r["name"] = " · ".join(bit for bit in (vocab.players.get(r["player_key"], r["player_key"]),
                                                   nice_time(r.get("played_at")), r.get("map")) if bit)
        elif group in ("day", "week", "month"):
            r["name"] = nice_day(r["key"])
        else:
            r["name"] = r["key"]


def _table(rows: list[dict[str, Any]], group: str | None, metrics: list[str], level: str,
           extra: tuple[str, ...] = ("matches",), group_label: str | None = None
           ) -> tuple[list[str], list[dict[str, Any]], dict[str, str]]:
    extra = tuple(k for k in extra if k not in metrics)
    head = [group_label or DIMS[group].label] if group or group_label else []
    columns = head + [_label(k, level) for k in metrics] + [_label(k, level) for k in extra]
    formats = {_label(k, level): METRICS[k].kind for k in metrics + list(extra)}
    table = []
    for r in rows:
        row: dict[str, Any] = {head[0]: r["name"]} if head else {}
        for k in metrics + list(extra):
            row[_label(k, level)] = _value(r.get(k), METRICS[k].kind)
        table.append(row)
    return columns, table, formats


def ask(question: str, db: StatsDB, now: datetime | None = None, vocab: Vocab | None = None) -> Answer:
    """Answer a plain-English question from the stats database."""
    if not question.strip():
        return Answer(False, "", "Type a question about your matches.")
    if not db.summary()["matches"]:
        return Answer(False, "", "There are no matches in your stats yet: load your replays on the Dashboard first.")
    vocab = vocab or Vocab.from_db(db)
    q = interpret(question, vocab, now)
    if q.kind == "unknown":
        return Answer(False, "", "I didn't catch what to look up. Try one of the examples, like “my K/D over time”, "
                                 "“best map” or “top 5 players by EPS”.", notes=q.notes, query=q)
    handler = {"trend": _answer_trend, "rank": _answer_rank, "compare": _answer_compare,
               "matches": _answer_matches, "summary": _answer_summary}.get(q.kind, _answer_value)
    try:
        answer = handler(db, q, vocab)
    except (ValueError, sqlite3.Error):  # a question read in a way that can't be looked up
        return Answer(False, "", "Sorry, I couldn't work that one out. Try asking it another way.",
                      notes=q.notes, query=q)
    answer.headline = answer.headline[:1].upper() + answer.headline[1:]
    answer.notes = q.notes + answer.notes
    answer.query = q
    return answer


def _no_matches(q: Query, vocab: Vocab, understood: str) -> Answer:
    who = _subject(q, vocab)[0]
    return Answer(False, understood, f"No matches found for {who}{_filters_text(q)}.")


def _answer_value(db: StatsDB, q: Query, vocab: Vocab) -> Answer:
    level = q.level
    ids = _match_ids(db, q, vocab, level)
    rows = _aggregate(db, q, vocab, q.metrics, None, ids=ids, level=level)
    who, whose = _subject(q, vocab)
    understood = f"{_cap(whose)} {', '.join(_phrase(k, level) for k in q.metrics)}{_filters_text(q)}"
    row = rows[0] if rows else {}
    if not row.get("matches"):
        return _no_matches(q, vocab, understood)
    row["name"] = ""
    sample = f"over {_plural(row['matches'], 'match')}" if level == "match" else \
        f"over {_plural(row['rounds'], 'round')}"
    first = q.metrics[0]
    value = fmt(_value(row.get(first), METRICS[first].kind), METRICS[first].kind)
    if q.metrics[:3] == RECORD:
        verb = "are" if who == "you" or q.who in ("teammates", "opponents") else "is"
        headline = f"{_cap(who)} {verb} {row['wins']}–{row['losses']} " \
                   f"({fmt(row['win_rate'], 'pct')} wins) {sample}{_filters_text(q)}."
    elif METRICS[first].kind == "count":
        verb = "have" if who in ("you", "your teammates", "your opponents") else "has"
        if first in SAMPLE_METRICS:
            played = _plural(row[first], "match" if first == "matches" else "round")
            headline = f"{_cap(who)} {verb} played {played}{_filters_text(q)}."
        else:
            headline = f"{_cap(who)} {verb} {_count(value, _phrase(first, level))} {sample}{_filters_text(q)}."
    else:
        headline = f"{_cap(whose)} {_phrase(first, level)} is {value} {sample}{_filters_text(q)}."
    columns, table, formats = _table([row], None, q.metrics, level,
                                     ("matches",) if level == "match" else ("rounds", "matches"))
    return Answer(True, understood, headline, columns, table, None, [], formats)


def _answer_team_summary(db: StatsDB, q: Query, vocab: Vocab) -> Answer:
    """How a team is doing, as a team: maps, rounds, and the situations that decide rounds."""
    level = q.level
    sql = _SQL(q, vocab, level)  # the team's side of each match, within the question's filters
    ids = [r["match_id"] for r in db.query(f"SELECT DISTINCT t.match_id FROM {sql.from_}{sql.where_sql()}",
                                           sql.params)]
    understood = f"How {q.team} is doing as a team{_filters_text(q)}"
    if not ids:
        return _no_matches(q, vocab, understood)
    s = team_summary(db, vocab.rosters.get(q.team, []), q.team_min or 3, ids, side=q.side)
    row = {"Maps": s["maps"], "Maps won": s["maps_won"], "Maps lost": s["maps_lost"],
           "Rounds won": s["rounds_won"], "Rounds lost": s["rounds_lost"],
           "Round win %": rate(s["rounds_won"], s["rounds_won"] + s["rounds_lost"]),
           "Man-down rounds": s["man_down"],
           "Man-down back to even %": rate(s["man_down_even"], s["man_down"]),
           "Man-down win %": rate(s["man_down_won"], s["man_down"]),
           "Plant %": rate(s["plants"], s["attack_rounds"]),
           "Plant stopped %": rate(s["defense_rounds"] - s["enemy_plants"], s["defense_rounds"]),
           "Post-plant win %": rate(s["post_plant_won"], s["post_plant"]),
           "Retake win %": rate(s["retakes_won"], s["retakes"])}
    row = {k: _value(v, "pct") if k.endswith("%") else v for k, v in row.items()}
    headline = (f"{q.team}: {_plural(s['maps'], 'map')}, {s['maps_won']}–{s['maps_lost']}; rounds "
                f"{s['rounds_won']}–{s['rounds_lost']} ({fmt(row['Round win %'], 'pct')} won); of the "
                f"{_plural(s['man_down'], 'round')} it went down 2+ players, it got back to even in "
                f"{s['man_down_even']} and won {s['man_down_won']}{_filters_text(q)}.")
    formats = {k: "pct" if k.endswith("%") else "count" for k in row}
    return Answer(True, understood, headline, list(row), [row], None, [], formats)


def _answer_summary(db: StatsDB, q: Query, vocab: Vocab) -> Answer:
    if q.who == "team" and q.team:  # a team's numbers are its own, never its players' added up
        answer = _answer_team_summary(db, q, vocab)
        if answer.ok and not q.maps and q.level == "match":
            maps = _aggregate(db, replace(q, kind="rank", min_matches=2, order="best", group="map", notes=[]),
                              vocab, ["win_rate"], "map", limit=1)
            if maps and maps[0].get("win_rate") is not None:
                answer.notes.append(f"Best map: {maps[0]['key']} ({fmt(maps[0]['win_rate'], 'pct')} wins over "
                                    f"{_plural(maps[0]['matches'], 'match')}).")
        return answer
    answer = _answer_value(db, q, vocab)
    if not answer.ok:
        return answer
    row = answer.rows[0]
    who, whose = _subject(q, vocab)
    bits = []
    if "Wins" in row:
        bits.append(f"{row['Wins']}–{row['Losses']} ({fmt(row['Win %'], 'pct')} wins)")
    for label, kind in (("K/D", "ratio"), ("EPS", "eps"), ("Round win %", "pct"), ("KOST %", "pct")):
        if row.get(label) is not None:
            bits.append(f"{'KOST' if label == 'KOST %' else label} {fmt(row[label], kind)}")
    sample = f"{_plural(row['Matches'], 'match')}" if "Matches" in row else f"{_plural(row.get('Rounds', 0), 'round')}"
    answer.headline = f"{'You' if who == 'you' else who}: {sample}, {', '.join(bits)}{_filters_text(q)}."
    answer.understood = f"A summary of {whose} stats{_filters_text(q)}"
    # where they do best and what they play most, unless that's what was asked about
    if q.who in ("me", "players", "team", "my_team") and not q.maps and q.level == "match":
        maps = _aggregate(db, replace(q, kind="rank", min_matches=2, order="best", group="map", notes=[]), vocab,
                          ["win_rate"], "map", ids=_match_ids(db, q, vocab, "match"), limit=1)
        if maps and maps[0].get("win_rate") is not None:
            answer.notes.append(f"Best map: {maps[0]['key']} ({fmt(maps[0]['win_rate'], 'pct')} wins over "
                                f"{_plural(maps[0]['matches'], 'match')}).")
    if q.who in ("me", "players") and not q.operators:
        ops_q = replace(q, kind="rank", most_played=True, group="operator", notes=[])
        ops = _aggregate(db, ops_q, vocab, ["rounds"], "operator", level="round", limit=1,
                         ids=_match_ids(db, ops_q, vocab, "round"))
        if ops:
            answer.notes.append(f"Most played operator: {ops[0]['key']} ({_plural(ops[0]['rounds'], 'round')}).")
    return answer


def _answer_rank(db: StatsDB, q: Query, vocab: Vocab) -> Answer:
    level = q.level
    ids = _match_ids(db, q, vocab, level)
    rows = _aggregate(db, q, vocab, q.metrics, q.group, ids=ids, level=level)
    relaxed = False
    if not rows and (q.min_matches or q.min_rounds):
        rows = _aggregate(db, q, vocab, q.metrics, q.group, ids=ids, level=level, minimums=False)
        relaxed = bool(rows)
    _name_rows(rows, q.group, vocab)
    shown = rows[:q.limit] if q.limit else rows
    first = q.metrics[0]
    dim = DIMS[q.group]
    who, whose = _subject(q, vocab)
    among = {"teammates": " among your teammates", "opponents": " among your opponents", "team": f" on {q.team}",
             "my_team": " on your team"}.get(q.who, "") if q.group in ("player", "player_match") else f" for {who}"
    word = {"best": "best", "worst": "worst", "high": "most", "low": "least"}.get(q.order or "", "best")
    if METRICS[first].kind == "count" and word in ("best", "worst"):  # "the most kills", "the fewest deaths"
        word = "most" if (word == "best") == METRICS[first].higher_is_better else "fewest"
    elif METRICS[first].kind != "count" and word in ("most", "least"):
        word = "highest" if word == "most" else "lowest"
    ranked_by = "most played" if q.most_played else f"{word} {_phrase(first, level)}"
    understood = f"{dim.label}s ranked by {ranked_by}{among}{_filters_text(q)}"
    if q.group in ("match", "player_match"):
        understood = f"Single matches ranked by {ranked_by}{among}{_filters_text(q)}"
    if relaxed:
        notes = ["Nothing had enough matches for a fair ranking, so everything is shown."]
    else:
        notes = []
        if q.min_matches and q.group not in ("match", "player_match"):
            understood += f" (at least {_plural(q.min_matches, 'match')})"
        if q.min_rounds and level == "round":
            understood += f" (at least {q.min_rounds} rounds)"
    if not shown:
        return Answer(False, understood, f"No {dim.label.lower()}s found{among}{_filters_text(q)}.", notes=notes)
    top = shown[0]
    kind = METRICS[first].kind
    if kind == "count" and METRICS[first].higher_is_better and not q.most_played and q.order != "low"             and not any(r.get(first) for r in shown):  # "most clutches": nobody has any
        nobody = "nobody" if q.group in ("player", "player_match") else f"no {dim.label.lower()}"
        return Answer(True, understood, f"{_cap(nobody)} has any {_phrase(first, level)} yet{among}{_filters_text(q)}.",
                      *_table(shown, q.group, q.metrics, level, ())[:2], None, notes,
                      _table(shown, q.group, q.metrics, level, ())[2])
    value = fmt(_value(top.get(first), kind), kind)
    sample = _plural(top["matches"], "match") if level == "match" else _plural(top["rounds"], "round")
    if q.group in ("match", "player_match"):  # "Your most kills in a match: 11 (Sep 23, 9:30 PM · Villa)."
        where = top["name"]
        if q.group == "match":
            headline = f"{_cap(whose)} {word} {_phrase(first, level)} in a match: {value} ({where})."
        else:
            name = "You" if top["player_key"] == vocab.me else vocab.players.get(top["player_key"], "")
            where = where.split(" · ", 1)[1] if " · " in where else where
            headline = f"{name} had the {word} {_phrase(first, level)} in a match{among}: {value} ({where})."
    elif q.group == "player":
        name = "You" if top["key"] == vocab.me else top["name"]
        verb = "have" if name == "You" else "has"
        if q.most_played:
            headline = f"{name} played the most matches{among}: {top['matches']}."
        else:
            headline = f"{name} {verb} the {word} {_phrase(first, level)}{among}: {value} ({sample})."
        if q.highlight_me and vocab.me:
            place = next((i for i, r in enumerate(rows, 1) if r["key"] == vocab.me), None)
            if place is None and q.min_matches:
                notes.append(f"You're not in this ranking: it needs at least {_plural(q.min_matches, 'match')}.")
            elif place and place > 1:  # at #1 the headline already says so
                headline += f" You're #{place} of {len(rows)}."
    else:
        what = dim.label.lower()
        if q.most_played:
            headline = f"{_cap(whose)} most played {what} is {top['name']}: {value} {_phrase(first, level)}."
        else:
            # "Your best map by K/D is Bank", "Your map with the most kills is Border"
            by = f"{word} {what} by {_phrase(first, level)}" if word not in ("most", "fewest", "least") else \
                f"{what} with the {word} {_phrase(first, level)}"
            headline = f"{_cap(whose)} {by} is {top['name']}: {value} ({sample})."
    if q.group in ("match", "player_match"):
        extra: tuple[str, ...] = ()  # each row is one match
    else:
        extra = ("matches",) if level == "match" else ("rounds", "matches")
    columns, table, formats = _table(shown, q.group, q.metrics, level, extra)
    chart = {"kind": "bar", "x": dim.label, "y": _label(first, level)} if len(table) > 1 else None
    return Answer(True, understood, headline, columns, table, chart, notes, formats)


def _answer_trend(db: StatsDB, q: Query, vocab: Vocab) -> Answer:
    level = q.level
    ids = _match_ids(db, q, vocab, level)
    rows = _aggregate(db, q, vocab, q.metrics, q.group, ids=ids, level=level)
    _name_rows(rows, q.group, vocab)
    first = q.metrics[0]
    who, whose = _subject(q, vocab)
    unit = {"match": "match", "day": "day", "week": "week", "month": "month"}[q.group]
    understood = f"{_cap(whose)} {', '.join(_phrase(k, level) for k in q.metrics)} per {unit}{_filters_text(q)}"
    if not rows:
        return _no_matches(q, vocab, understood)
    columns, table, formats = _table(rows, q.group, q.metrics, level, () if q.group == "match" else ("matches",))
    chart = {"kind": "line", "x": DIMS[q.group].label, "y": _label(first, level)}
    kind = METRICS[first].kind
    overall = _aggregate(db, q, vocab, [first], None, ids=ids, level=level)[0].get(first)
    if len(rows) < 4:
        headline = f"Only {_plural(len(rows), unit)} so far, too few for a trend: {whose} " \
                   f"{_phrase(first, level)} is {fmt(_value(overall, kind), kind)}."
        return Answer(True, understood, headline, columns, table, chart, [], formats)
    # the earlier half against the later half, each from its own totals (not an average of averages)
    half = len(rows) // 2
    if q.group == "match":
        early_ids, late_ids = [r["key"] for r in rows[:half]], [r["key"] for r in rows[-half:]]
    else:
        early_ids = _period_ids(db, q, vocab, level, ids, rows[:half])
        late_ids = _period_ids(db, q, vocab, level, ids, rows[-half:])
    early = _aggregate(db, q, vocab, [first], None, ids=early_ids, level=level)[0].get(first)
    late = _aggregate(db, q, vocab, [first], None, ids=late_ids, level=level)[0].get(first)
    headline = f"{_cap(whose)} {_phrase(first, level)} went from {fmt(_value(early, kind), kind)} to " \
               f"{fmt(_value(late, kind), kind)}: {_direction(early, late, METRICS[first])}."
    notes = [f"That's the first {_plural(half, unit)} against the last {half}. Overall: "
             f"{fmt(_value(overall, kind), kind)}."]
    return Answer(True, understood, headline, columns, table, chart, notes, formats)


def _period_ids(db: StatsDB, q: Query, vocab: Vocab, level: str, ids: list[str] | None,
                periods: list[dict[str, Any]]) -> list[str]:
    """The match ids in some days/weeks/months of a trend."""
    sql = _SQL(q, vocab, level).only(ids)
    keys = [p["key"] for p in periods]
    rows = db.query(f"SELECT DISTINCT t.match_id FROM {sql.from_}{sql.where_sql()}"
                    f"{' AND ' if sql.where else ' WHERE '}{DIMS[q.group].sql} IN ({', '.join('?' * len(keys))})",
                    sql.params + keys)
    return [r["match_id"] for r in rows]


def _direction(early: float | None, late: float | None, metric: Metric) -> str:
    if early is None or late is None:
        return "not enough data to tell"
    change = (late - early) / abs(early) if early else (1.0 if late else 0.0)
    if abs(change) < 0.05:
        return "holding steady"
    return "improving" if (change > 0) == metric.higher_is_better else "slipping"


def _answer_compare(db: StatsDB, q: Query, vocab: Vocab) -> Answer:
    level = q.level
    if q.compare in ("team", "period"):
        rows = []
        runs = [(team, replace(q, who="team", team=team, notes=[])) for team in q.teams] if q.compare == "team" else \
            [(label, replace(q, since=since, until=until, periods=[], notes=[])) for label, since, until in q.periods]
        for name, sub in runs:
            got = _aggregate(db, sub, vocab, q.metrics, None, ids=_match_ids(db, sub, vocab, level), level=level)
            rows.append(dict(got[0] if got else {}, key=name, name=name))
        label = "Team" if q.compare == "team" else "Period"
        if q.compare == "period":
            return _answer_periods(q, vocab, rows, level)
        rows = [r for r in rows if r.get("matches")]
    else:
        rows = _aggregate(db, q, vocab, q.metrics, q.group, ids=_match_ids(db, q, vocab, level), level=level,
                          minimums=False)
        if q.compare == "match_type":
            rows = [r for r in rows if r["key"] in q.match_types]
        _name_rows(rows, q.group, vocab)
        label = DIMS[q.group].label
    understood = f"{label}s side by side{_filters_text(q)}"
    if len(rows) < 2:
        have = ", ".join(r["name"] for r in rows) or "none of them"
        return Answer(False, understood, f"I need two with matches to compare; only {have} had any.")
    first = next((k for k in q.metrics if k not in SAMPLE_METRICS | {"wins", "losses"}), q.metrics[0])
    metric = METRICS[first]
    ranked = sorted(rows, key=lambda r: (r.get(first) is None,
                                         -(r.get(first) or 0) if metric.higher_is_better else (r.get(first) or 0)))
    lead, other = ranked[0], ranked[1]
    a, b = (fmt(_value(r.get(first), metric.kind), metric.kind) for r in (lead, other))
    if q.compare in ("side", "match_type"):
        better = "stronger side" if q.compare == "side" else "better match type"
        headline = f"{lead['name']} is {_subject(q, vocab)[1]} {better} by {_phrase(first, level)}: {a} vs {b}."
    else:
        name = "You" if lead.get("key") == vocab.me else lead["name"]
        higher = "higher" if metric.higher_is_better else "lower"
        headline = f"{name} {'have' if name == 'You' else 'has'} the {higher} {_phrase(first, level)}: {a} vs {b}" \
                   f" ({other['name'] if other.get('key') != vocab.me else 'you'})."
    extra = ("matches",) if level == "match" else ("rounds",)
    columns, table, formats = _table(rows, q.group if q.compare != "team" else None, q.metrics, level, extra,
                                     group_label=label)
    return Answer(True, understood, headline, columns, table, None, [], formats)


def _answer_periods(q: Query, vocab: Vocab, rows: list[dict[str, Any]], level: str) -> Answer:
    """"This week vs last week": the same numbers for each period, in the order asked."""
    who, whose = _subject(q, vocab)
    understood = f"{_cap(whose)} stats, {' vs '.join(r['name'] for r in rows)}{_filters_text(q)}"
    played = [r for r in rows if r.get("matches")]
    if not played:
        return Answer(False, understood, f"No matches found for {who} in {' or '.join(r['name'] for r in rows)}.")
    first = q.metrics[0]
    kind = METRICS[first].kind
    parts = [f"{fmt(_value(r.get(first), kind), kind) if r.get('matches') else 'no matches'} {r['name']}" for r in rows]
    headline = f"{_cap(whose)} {_phrase(first, level)}: {' vs '.join(parts)}."
    if len(played) == len(rows) == 2 and all(r.get(first) is not None for r in rows):
        starts = {label: since for label, since, _ in q.periods}
        early, late = sorted(rows, key=lambda r: starts[r["name"]])  # whichever order they were asked in
        change = {"improving": "better than", "slipping": "worse than", "holding steady": "about the same as"}[
            _direction(early[first], late[first], METRICS[first])]
        headline += f" {_cap(late['name'])} is {change} {early['name']}."
    for r in rows:
        r["name"] = _cap(r["name"])
    columns, table, formats = _table(rows, None, q.metrics, level, ("matches",), group_label="Period")
    return Answer(True, understood, headline, columns, table, None, [], formats)


def _answer_matches(db: StatsDB, q: Query, vocab: Vocab) -> Answer:
    sql = _SQL(q, vocab, "match").only(_match_ids(db, replace(q, last_matches=None, first_matches=None,
                                                                   notes=[]), vocab, "match"))
    cols = {k: _metric_sql(k, "match") for k in ("kills", "deaths", "kd", "eps")}
    text = ("SELECT t.match_id, MAX(m.played_at) AS played_at, MAX(m.map) AS map, MAX(m.match_type) AS match_type,"
            " MAX(m.score0) AS score0, MAX(m.score1) AS score1, MAX(t.team) AS team, MAX(t.won) AS won, "
            + ", ".join(f"{s} AS {k}" for k, s in cols.items())
            + f" FROM {sql.from_}{sql.where_sql()} GROUP BY t.match_id"
            f" ORDER BY MAX(m.played_at) {'ASC' if q.first_matches else 'DESC'} LIMIT ?")
    rows = db.query(text, sql.params + [q.limit or 10])
    who, whose = _subject(q, vocab)
    # "your last 3 matches", not "your matches in the last 3 matches"
    edge = "first" if q.first_matches else "last" if q.last_matches else None
    filters = _filters_text(replace(q, last_matches=None, first_matches=None))
    asked = f"{edge} {_plural(q.first_matches or q.last_matches, 'match')}" if edge else "matches"
    understood = f"{_cap(whose)} {asked}{filters}, {'oldest' if q.first_matches else 'newest'} first"
    if not rows:
        return _no_matches(q, vocab, understood)
    table = []
    for r in rows:
        own, other = (r["score0"], r["score1"]) if r["team"] == 0 else (r["score1"], r["score0"])
        table.append({"When": nice_time(r["played_at"]), "Map": r["map"], "Type": r["match_type"],
                      "Result": {1: "Win", 0: "Loss"}.get(r["won"], "Draw"), "Score": f"{own}–{other}",
                      "Kills": r["kills"], "Deaths": r["deaths"], "K/D": _value(r["kd"], "ratio"),
                      "EPS": _value(r["eps"], "eps")})
    wins = sum(t["Result"] == "Win" for t in table)
    losses = sum(t["Result"] == "Loss" for t in table)
    if edge and len(table) == 1:
        only = table[0]
        result = {"Win": "a win", "Loss": "a loss"}.get(only["Result"], "a draw")
        headline = f"{_cap(whose)} {edge} match{filters}: {result} on {only['Map']}, {only['Score']}."
    elif edge:
        headline = f"{_cap(whose)} {edge} {len(table)} matches{filters}: {wins} won, {losses} lost."
    else:
        headline = f"{_plural(len(table), 'match').capitalize()}{filters}: {wins} won, {losses} lost."
    return Answer(True, understood, headline, list(table[0]), table, None, [],
                  {"Kills": "count", "Deaths": "count", "K/D": "ratio", "EPS": "eps"})


TEAM_PLAYER_METRICS = ["eps", "kills", "deaths", "assists", "entry_kills", "entry_deaths", "kost", "kpr", "hs", "survival",
                       "clutches", "multikills", "objectives", "traded", "trade_kills"]


def team_sides(keys: list[str], min_players: int = 3, match_ids: list[str] | None = None) -> tuple[str, list]:
    """The SQL (and its parameters) for a team's side in each of its matches: (match_id, team) where
    at least min_players of the player keys `keys` were on one side, the side with the most of them.
    match_ids limits it to those matches. Build a team and the map pool both count matches this way."""
    only = f" AND match_id IN ({', '.join('?' * len(match_ids))})" if match_ids is not None else ""
    sql = (f"SELECT match_id, team FROM (SELECT match_id, team, COUNT(*) AS n, ROW_NUMBER() OVER (PARTITION BY "
           f"match_id ORDER BY COUNT(*) DESC, team) AS pick FROM match_players WHERE player_key IN "
           f"({', '.join('?' * len(keys))}){only} GROUP BY match_id, team) WHERE pick = 1 AND n >= ?")
    return sql, keys + list(match_ids or []) + [max(1, min(min_players, len(keys)))]


def team_summary(db: StatsDB, players: list[str], min_players: int = 3, match_ids: list[str] | None = None,
                 side: str | None = None) -> dict[str, Any]:
    """A team's stats as a team, never added up from its players: the maps it played (at least
    min_players of `players` on one side, the side with the most of them) and its record, and from
    those maps' rounds, each counted once for the whole team:
    - rounds won and lost;
    - man down: rounds where it was two or more players down at some point, and of those, how many
      it brought back to even numbers (won or not) and how many it won;
    - on attack, how often it planted the defuser, and won once it was down (post-plant);
    - on defense, how often it stopped the plant, and won once the defuser was down (retake).
    match_ids limits it to those matches; side to attack or defense rounds."""
    keys = list(dict.fromkeys(p.strip().casefold() for p in players if p.strip()))
    empty = dict.fromkeys(("maps", "maps_won", "maps_lost", "rounds", "rounds_won", "rounds_lost", "man_down",
                           "man_down_even", "man_down_won", "attack_rounds", "plants", "post_plant", "post_plant_won",
                           "defense_rounds", "enemy_plants", "retakes", "retakes_won", "maps_without_rounds"), 0)
    if not keys or match_ids == []:
        return empty
    sides, params = team_sides(keys, min_players, match_ids)
    maps = db.query(
        f"WITH sides AS ({sides}) SELECT COUNT(*) AS maps,"
        " SUM(CASE WHEN s.team = 0 THEN m.score0 > m.score1 ELSE m.score1 > m.score0 END) AS maps_won,"
        " SUM(CASE WHEN s.team = 0 THEN m.score0 < m.score1 ELSE m.score1 < m.score0 END) AS maps_lost,"
        " SUM(NOT EXISTS (SELECT 1 FROM team_rounds r WHERE r.match_id = s.match_id)) AS maps_without_rounds"
        " FROM sides s JOIN matches m ON m.match_id = s.match_id", params)[0]
    rounds = db.query(
        f"WITH sides AS ({sides}) SELECT COUNT(*) AS rounds, SUM(r.won = 1) AS rounds_won,"
        " SUM(r.won = 0) AS rounds_lost,"
        " SUM(r.man_down AND r.won IS NOT NULL) AS man_down, SUM(r.man_down AND r.won = 1) AS man_down_won,"
        " SUM(r.man_down AND r.back_to_even AND r.won IS NOT NULL) AS man_down_even,"
        " SUM(r.side = 'attack') AS attack_rounds, SUM(r.side = 'attack' AND r.planted) AS plants,"
        " SUM(r.side = 'attack' AND r.planted AND r.won IS NOT NULL) AS post_plant,"
        " SUM(r.side = 'attack' AND r.planted AND r.won = 1) AS post_plant_won,"
        " SUM(r.side = 'defense') AS defense_rounds, SUM(r.side = 'defense' AND r.planted) AS enemy_plants,"
        " SUM(r.side = 'defense' AND r.planted AND r.won IS NOT NULL) AS retakes,"
        " SUM(r.side = 'defense' AND r.planted AND r.won = 1) AS retakes_won"
        " FROM sides s JOIN team_rounds r ON r.match_id = s.match_id AND r.team = s.team"
        + (" WHERE r.side = ?" if side else ""), params + ([side] if side else []))[0]
    return {k: int((maps | rounds).get(k) or 0) for k in empty}


def rate(part: int, whole: int) -> float | None:
    """part of whole as a percentage, or None when there's nothing to go on."""
    return 100 * part / whole if whole else None


def eps_by_player(db: StatsDB, players: list[str], match_ids: list[str] | None = None) -> dict[str, int | None]:
    """Each player's EPS, rounds-weighted like EPS everywhere, by player key: over every match
    they played (their all-time EPS), or only over match_ids."""
    keys = list(dict.fromkeys(p.strip().casefold() for p in players if p.strip()))
    if not keys or match_ids == []:
        return dict.fromkeys(keys)
    only = f" AND match_id IN ({', '.join('?' * len(match_ids))})" if match_ids is not None else ""
    rows = db.query(f"SELECT player_key, {METRICS['eps'].match_sql} AS eps FROM match_players t "
                    f"WHERE player_key IN ({', '.join('?' * len(keys))}){only} GROUP BY player_key",
                    keys + list(match_ids or []))
    found = {r["player_key"]: _value(r["eps"], "eps") for r in rows}
    return {k: found.get(k) for k in keys}


def player_report(db: StatsDB, player: str, match_ids: list[str]) -> dict[str, Any] | None:
    """All scoreboard metrics for one player's exact counted season matches."""
    vocab = Vocab.from_db(db)
    q = Query(kind="rank", who="players", players=[player.casefold()], group="player")
    rows = _aggregate(db, q, vocab, TEAM_PLAYER_METRICS, "player", ids=match_ids, minimums=False)
    if not rows:
        return None
    row = rows[0]
    row["name"] = vocab.players.get(row["key"], player)
    row["all_time_eps"] = eps_by_player(db, [player]).get(player.casefold())
    return row


def team_report(db: StatsDB, team: str, min_players: int = 3) -> dict[str, Any]:
    """Build a team: a saved team's matches (at least min_players of it on one side, the side with
    the most of them), its stats as a team (team_summary), and each player's own stats from
    playing for it, with their all-time EPS."""
    vocab = Vocab.from_db(db)
    q = Query(kind="rank", who="team", team=team, team_min=min_players, group="player")
    players = _aggregate(db, q, vocab, TEAM_PLAYER_METRICS, "player", minimums=False)
    roster = db.rosters().get(team, [])
    career = eps_by_player(db, roster)
    for r in players:
        r["name"] = vocab.players.get(r["key"], r["key"])
        r["all_time_eps"] = career.get(r["key"])
    sql = _SQL(q, vocab, "match")
    matches = db.query(
        "SELECT t.match_id, MAX(m.source) AS source, MAX(m.played_at) AS played_at, MAX(m.map) AS map,"
        " MAX(m.score0) AS score0, MAX(m.score1) AS score1, MAX(t.team) AS team, MAX(t.won) AS won,"
        f" GROUP_CONCAT(t.player, ', ') AS players FROM {sql.from_}{sql.where_sql()}"
        " GROUP BY t.match_id ORDER BY MAX(m.played_at) DESC", sql.params)
    found = {r["key"] for r in players}
    return {"team": team, "players": players, "matches": matches,
            "summary": team_summary(db, roster, min_players),
            "missing": [p for p in roster if p.casefold() not in found]}


def operator_table(db: StatsDB, player: str) -> list[dict[str, Any]]:
    """Every operator a player has played, most played first, over all their matches."""
    vocab = Vocab.from_db(db)
    q = Query(kind="rank", group="operator", who="players", players=[player.casefold()], most_played=True)
    rows = _aggregate(db, q, vocab, ["rounds", "win_rate", "kd", "kpr", "hs"], "operator", level="round")
    return [{"Operator": r["key"], "Rounds": r["rounds"], "Round win %": _value(r["win_rate"], "pct"),
             "K/D": _value(r["kd"], "ratio"), "KPR": _value(r["kpr"], "ratio"), "Headshot %": _value(r["hs"], "pct")}
            for r in rows]


def suggestions(vocab: Vocab, db: StatsDB, count: int = 8) -> list[str]:
    """Example questions that work with this database: the ones about the user's own teammate and
    team come before the general ones that are cut first."""
    mine = []
    if vocab.me:
        mates = db.teammates(vocab.players.get(vocab.me, vocab.me), limit=1)
        if mates:
            mine.append(f"Compare me and {mates[0][0]}")
    if vocab.rosters:
        mine.append(f"How is {next(iter(vocab.rosters))} doing?")
    out = ["How has my K/D changed over my last 10 matches?", "What's my best map?", "My most kills in a match",
           "Which operators do I play most?", *mine, "Attack vs defense win rate", "My record this week",
           "Top 5 players by EPS"]
    return out[:count]
