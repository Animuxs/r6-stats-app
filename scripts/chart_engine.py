"""
chart_engine.py
===============
Any chart of any stat, from a plain-English request or picked from menus. A ChartSpec says what to draw:
which stat (or two, for a scatter), along what (matches over time, maps, operators, players, ...),
for whom (you, players, a team, everyone; several make several lines or bars), split by what, and
filtered how; build() turns it into the rows to plot. parse() reads a request into a ChartSpec:

    parse("my K/D over time vs Sacri", vocab)             # a line per player, match by match
    parse("pie chart of the operators I play", vocab)     # each operator's share of rounds
    parse("heatmap of win rate by map and side", vocab)   # map x side
    parse("scatter K/D vs KOST for every player", vocab)  # a dot per player

The numbers come from the Ask engine's queries (ask_engine._aggregate), so a chart always agrees with
what Ask says. Nothing here draws: charts.py (the Charts tab) and assistant_ui.py draw the rows with Altair.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from ask_engine import (METRICS, ROUND_DIMS, Query, Vocab, _aggregate, _match_ids, _name_rows, _value,
                        fold, interpret)
from stats_db import StatsDB, nice_day

# what can be drawn, with a name for the menus
KINDS = {"line": "Line (progression)", "area": "Area", "bar": "Bar (comparison)", "hbar": "Horizontal bar",
         "stacked": "Stacked bar", "pie": "Pie / donut (share)", "scatter": "Scatter (two stats)",
         "heatmap": "Heatmap (two groupings)", "histogram": "Histogram (spread)", "box": "Box plot (spread)"}
# what a chart can go along (or split by), with a name for the menus
AXES = {"match": "Match (over time)", "day": "Day", "week": "Week", "month": "Month", "map": "Map",
        "operator": "Operator", "side": "Side", "site": "Site", "match_type": "Match type", "player": "Player"}
SPLITS = {"none": "Nothing", "who": "Who (each player or team)", "side": "Side", "map": "Map",
          "match_type": "Match type"}
TIME_AXES = {"match", "day", "week", "month"}
# stats a chart can show (aces only exist per round, EPS only per match: build() handles both)
CHART_METRICS = ["kd", "eps", "kills", "deaths", "assists", "kpr", "dpr", "hs", "kost", "survival", "entry",
                 "entry_kills", "entry_deaths", "clutches", "multikills", "aces", "trade_kills", "traded", "plants",
                 "defuses", "objectives", "win_rate", "wins", "losses", "matches", "rounds"]
COUNTS = {"kills", "deaths", "assists", "clutches", "multikills", "aces", "trade_kills", "traded", "plants",
          "defuses", "objectives", "wins", "losses", "matches", "rounds", "entry_kills", "entry_deaths"}

_KIND_WORDS = [  # the first that matches decides the kind
    ("heatmap", r"\b(heat ?maps?|grid|matrix)\b"),
    ("scatter", r"\b(scatter|correlat\w*|relationship|plotted against|against each other)\b"),
    ("pie", r"\b(pie|donut|doughnut|share|shares|proportion|percentage of|split of|makeup)\b"),
    ("histogram", r"\b(histogram|distribution|spread|how often (do|did) i get)\b"),
    ("box", r"\b(box ?plots?|box and whisker|range of)\b"),
    ("stacked", r"\b(stacked)\b"),
    ("hbar", r"\b(horizontal)\b"),
    ("area", r"\b(area)\b"),
    ("line", r"\b(line|over time|trend|trends|progress\w*|improv\w*|history|timeline|each match|every match|"
             r"match by match|per match|per game|per day|per week|per month|daily|weekly|monthly|lately|form|"
             r"rolling|moving average|running)\b"),
    ("bar", r"\b(bars?|column|columns|compare|comparison|versus|vs|ranking|rank|by map|by operator|by player|"
            r"per map|per operator|per player|each map|each operator|each player|best|worst|top)\b"),
]
_CHART_WORDS = (r"\b(show me|show|draw|plot|graph|chart|visuali[sz]e|visual|make|create|give me|generate|display|"
                r"a|an|the|of|please|heat ?maps?|scatter( ?plot)?|pie|donut|doughnut|histogram|box ?plots?|"
                r"stacked|horizontal|line|bars?|area|column)\b")
_AXIS_WORDS = {
    "operator": r"\b(operators?|ops?|characters?)\b", "site": r"\b(sites?|bomb ?sites?)\b",
    "map": r"\b(maps?)\b", "side": r"\b(sides?|attack (and|vs|or) defen[cs]e)\b",
    "match_type": r"\b(match ?types?|playlists?|ranked (and|vs|or) unranked|modes?)\b",
    "player": r"\b(players?|teammates?|everyone|each player|who)\b",
    "month": r"\b(months?|monthly)\b", "week": r"\b(weeks?|weekly)\b", "day": r"\b(days?|daily)\b",
}
_ROLLING = r"\b(rolling|moving|running) (average|avg|mean)(?: (?:of|over) (\d+))?|\b(?:last|over) (\d+) (?:matches|games) average\b"


@dataclass
class ChartSpec:
    kind: str = "line"
    metric: str = "kd"
    metric2: str | None = None        # the scatter's second stat
    x: str = "match"                  # what it goes along (AXES)
    split: str = "none"               # lines or bars by (SPLITS)
    who: str = "me"                   # me, players, team, everyone, teammates, opponents, my_team
    players: list[str] = field(default_factory=list)  # player keys (who == "players": one series each)
    teams: list[str] = field(default_factory=list)    # team names (who == "team": one series each)
    maps: list[str] = field(default_factory=list)
    operators: list[str] = field(default_factory=list)
    side: str | None = None
    match_type: str | None = None
    tag: str | None = None
    since: str | None = None
    until: str | None = None
    last_matches: int | None = None
    rolling: int = 0                  # a moving average over this many points (lines only)
    cumulative: bool = False          # a running total (counts over time)
    limit: int = 15                   # most bars or slices (the rest are left out)
    notes: list[str] = field(default_factory=list)

    def title(self, vocab: Vocab) -> str:
        stat = METRICS[self.metric].label
        if self.kind == "scatter" and self.metric2:
            stat = f"{stat} vs {METRICS[self.metric2].label}"
        whose = ", ".join(_series_names(self, vocab))
        along = AXES[self.x].split(" (")[0].lower()
        prefix = "Running total of " if self.cumulative else ""
        text = f"{prefix}{stat} by {along}" + (f" and {self.split.replace('_', ' ')}" if self.kind == "heatmap" else "")
        bits = []
        if self.maps:
            bits.append("on " + ", ".join(self.maps))
        if self.operators:
            bits.append("as " + ", ".join(self.operators))
        if self.side:
            bits.append(f"on {self.side}")
        if self.match_type:
            bits.append(f"in {self.match_type}")
        if self.tag:
            bits.append(f"tagged {self.tag}")
        if self.last_matches:
            bits.append(f"last {self.last_matches} matches")
        if self.since or self.until:
            bits.append(" to ".join(nice_day(d) for d in (self.since, self.until) if d))
        if self.rolling > 1:
            bits.append(f"{self.rolling}-match average")
        text += f" ({', '.join(bits)})" if bits else ""
        return f"{text} · {whose}" if whose else text


@dataclass
class Chart:
    """What to draw: rows of {x, y, series} (and y2 for a scatter, x2 for a heatmap), and how."""
    spec: ChartSpec
    title: str
    rows: list[dict[str, Any]]
    x_label: str
    y_label: str
    y2_label: str = ""
    series_label: str = ""
    y_format: str = ""                # a d3 format for the stat's numbers
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.rows)

    def table(self) -> list[dict[str, Any]]:
        """The rows with readable column names, for a table or a CSV."""
        names = {"x": self.x_label, "y": self.y_label, "y2": self.y2_label, "series": self.series_label or "Series",
                 "x2": self.series_label or "Group"}
        return [{names[k]: v for k, v in r.items() if k in names and (k != "series" or self.series_label)}
                for r in self.rows]


_FORMATS = {"ratio": ".2f", "pct": ".0f", "count": "d", "eps": "d", "signed": "+d"}


def _series_names(spec: ChartSpec, vocab: Vocab) -> list[str]:
    if spec.who == "players":
        return [vocab.players.get(p, p) for p in spec.players]
    if spec.who == "team":
        return list(spec.teams)
    if spec.who == "me":
        return [vocab.players.get(vocab.me, "You")] if vocab.me else []
    return [{"everyone": "Everyone", "teammates": "Your teammates", "opponents": "Your opponents",
             "my_team": "Your team"}.get(spec.who, spec.who)]


# ------------------------------------------------------------------ parsing --

def parse(request: str, vocab: Vocab, now: datetime | None = None) -> ChartSpec:
    """A chart request in plain English, read into a ChartSpec (always something drawable)."""
    text = fold(request)
    kind = next((k for k, pattern in _KIND_WORDS if re.search(pattern, text)), None)
    # what Ask reads from it, without the chart words (Ask takes "graph" to mean "over time")
    q = interpret(re.sub(_CHART_WORDS, " ", request, flags=re.I), vocab, now)
    spec = ChartSpec(notes=list(q.notes))
    spec.maps, spec.operators, spec.side, spec.match_type, spec.tag = q.maps, q.operators, q.side, q.match_type, q.tag
    spec.since, spec.until, spec.last_matches = q.since, q.until, q.last_matches or q.first_matches
    metrics = [m for m in q.metrics if m in CHART_METRICS]
    axes = [a for a, pattern in _AXIS_WORDS.items() if re.search(pattern, re.sub(r"\bon (attack|defen[cs]e)\b", "", text))]

    # whose: every player and team named is a series of its own; "me" too when mentioned with them
    names = list(dict.fromkeys(q.players))
    if q.who == "team" and q.teams:
        spec.who, spec.teams = "team", list(q.teams)
    elif names and (len(names) > 1 or names != [vocab.me]):
        spec.who, spec.players = "players", names
    elif q.who in ("everyone", "teammates", "opponents", "my_team"):
        spec.who = q.who
    if "player" in axes and spec.who in ("me", "my_team"):
        spec.who = "everyone" if spec.who == "me" else "teammates"

    if kind == "scatter":
        spec.kind = "scatter"
        spec.metric, spec.metric2 = (metrics + ["kd", "kost"])[:2] if len(metrics) != 1 else (metrics[0], "win_rate")
        if spec.metric2 == spec.metric:
            spec.metric2 = "kost" if spec.metric != "kost" else "kd"
        spec.x = next((a for a in axes if a in ("player", "operator", "map")), "player")
        if spec.x == "player" and spec.who in ("me", "my_team"):
            spec.who = "everyone"
        return spec
    spec.metric = metrics[0] if metrics else ("rounds" if kind == "pie" else "kd")
    if kind == "heatmap":
        spec.kind = "heatmap"
        two = [a for a in axes if a in ("map", "side", "operator", "match_type", "site")] + ["map", "side"]
        two = list(dict.fromkeys(two))
        spec.x, spec.split = two[0], next(a for a in two[1:] if a in SPLITS)
        if not metrics:
            spec.metric = "win_rate"
        return spec
    time_axis = next((a for a in ("day", "week", "month") if a in axes), None)
    group_axis = next((a for a in ("operator", "site", "map", "match_type", "side", "player") if a in axes), None)
    if q.group in AXES and q.group not in ("match",) and not group_axis and not time_axis:
        group_axis = q.group if q.group not in TIME_AXES else None
        time_axis = q.group if q.group in TIME_AXES else None
    if kind in ("histogram", "box"):
        spec.kind, spec.x = kind, "match"
        if kind == "box" and group_axis:
            spec.split = group_axis if group_axis in SPLITS else "none"
        if spec.metric in ("win_rate", "matches", "rounds", "wins", "losses"):
            spec.metric = "kills"
    elif kind == "pie":
        spec.kind, spec.x = "pie", group_axis or "operator"
        if spec.metric not in COUNTS:
            spec.metric = "rounds"
            spec.notes.append("A pie shows shares of a total, so this is each one's share of rounds played.")
    elif kind in ("line", "area") or (kind is None and (time_axis or q.kind == "trend")):
        spec.kind, spec.x = kind or "line", time_axis or "match"
        if group_axis in ("side", "map", "match_type"):
            spec.split = group_axis
    elif kind is None and not group_axis:  # "graph my kills": how it's going, match by match
        spec.kind, spec.x = "line", "match"
    else:
        spec.kind = kind if kind in ("bar", "hbar", "stacked") else "bar"
        spec.x = group_axis or time_axis or ("player" if len(spec.players) + len(spec.teams) > 1 else "map")
        if spec.kind == "stacked":
            spec.split = next((a for a in axes if a in SPLITS and a != spec.x), "side" if spec.x != "side" else "map")
    if spec.x == "player" and spec.who == "me":
        spec.who = "everyone"
    if (m := re.search(_ROLLING, text)) and spec.kind in ("line", "area"):
        spec.rolling = int(m.group(3) or m.group(4) or 5)
    if re.search(r"\b(running total|cumulative|total so far|add(ed)? up)\b", text) and spec.kind in ("line", "area"):
        spec.cumulative = spec.metric in COUNTS
    if (m := re.search(r"\b(?:top|best|worst|first) (\d+)\b", text)):
        spec.limit = max(1, min(50, int(m.group(1))))
    if len(spec.players) + len(spec.teams) > 1 and spec.split == "none" and spec.x != "player":
        spec.split = "who"
    elif spec.split == "none" and spec.kind in ("line", "area", "bar", "hbar"):
        spec.split = "who" if len(spec.players) + len(spec.teams) > 1 else "none"
    return spec


# ----------------------------------------------------------------- building --

def _query(spec: ChartSpec, who: str, players: list[str], team: str | None) -> Query:
    return Query(kind="rank", who=who, players=players, team=team, teams=[team] if team else [], maps=spec.maps,
                 operators=spec.operators, side=spec.side, match_type=spec.match_type, tag=spec.tag,
                 since=spec.since, until=spec.until, last_matches=spec.last_matches)


def _subjects(spec: ChartSpec, vocab: Vocab) -> list[tuple[str, Query]]:
    """(name, query) for each series of the chart's subject."""
    if spec.who == "players" and spec.players:
        return [(vocab.players.get(p, p), _query(spec, "players", [p], None)) for p in spec.players]
    if spec.who == "team" and spec.teams:
        return [(t, _query(spec, "team", [], t)) for t in spec.teams if t in vocab.rosters]
    who = spec.who if spec.who != "players" else "me"
    if who in ("me", "my_team", "teammates", "opponents") and not vocab.me:
        who = "everyone"
    return [(_series_names(replace(spec, who=who), vocab)[0] if who != "me" else
             vocab.players.get(vocab.me, "You"), _query(spec, who, [], None))]


def _level(spec: ChartSpec, *metrics: str | None) -> str:
    """Per round when the chart needs rounds (operators, sides, sites, aces), else per match."""
    if spec.x in ROUND_DIMS or spec.split in ROUND_DIMS or spec.operators or spec.side:
        return "round"
    if any(m and METRICS[m].match_sql is None for m in metrics):
        return "round"
    return "match"


def _metric_for(level: str, metric: str, notes: list[str]) -> str:
    if level == "round" and METRICS[metric].round_sql is None:  # EPS is per match
        notes.append("EPS is worked out per match, so it can't be split by operator, side or site: showing K/D.")
        return "kd"
    return metric


def _rows(db: StatsDB, vocab: Vocab, q: Query, metrics: list[str], group: str, level: str,
          limit: int | None = None, order_desc: bool = True) -> list[dict[str, Any]]:
    """Aggregated rows, one per group value, named for showing."""
    q = replace(q, group=group, metrics=metrics)
    ids = _match_ids(db, q, vocab, level)
    if group in TIME_AXES:
        rows = _aggregate(db, replace(q, kind="trend"), vocab, metrics, group, ids=ids, level=level)
    else:
        rows = _aggregate(db, replace(q, order="high" if order_desc else "low"), vocab, metrics, group, ids=ids,
                          level=level, minimums=False)
    _name_rows(rows, group, vocab)
    if limit and group not in TIME_AXES:
        rows = rows[:limit]
    return rows


def _filter_values(db: StatsDB, vocab: Vocab, dim: str, spec: ChartSpec) -> list[str]:
    """The values a split takes, most played first (at most 8, so a chart stays readable)."""
    if dim == "side":
        return ["attack", "defense"]
    if dim == "map":
        rows = db.query("SELECT map, COUNT(*) AS n FROM matches WHERE map IS NOT NULL GROUP BY map ORDER BY n DESC")
        return [r["map"] for r in rows][:8]
    if dim == "match_type":
        return vocab.match_types[:8]
    if dim == "operator":
        return [r["operator"] for r in db.query(
            "SELECT operator, COUNT(*) AS n FROM round_players WHERE operator IS NOT NULL GROUP BY operator "
            "ORDER BY n DESC LIMIT 8")]
    if dim == "site":
        return [r["site"] for r in db.query(
            "SELECT site, COUNT(*) AS n FROM round_players WHERE site IS NOT NULL GROUP BY site "
            "ORDER BY n DESC LIMIT 8")]
    return []


def _narrow(q: Query, dim: str, value: str) -> Query:
    """A query kept to one value of a split."""
    if dim == "side":
        return replace(q, side=value)
    if dim == "map":
        return replace(q, maps=[value])
    if dim == "match_type":
        return replace(q, match_type=value)
    if dim == "operator":
        return replace(q, operators=[value])
    raise ValueError(dim)


def _label_value(dim: str, value: str) -> str:
    return value.capitalize() if dim == "side" else value


def build(spec: ChartSpec, db: StatsDB, vocab: Vocab | None = None) -> Chart:
    """The rows to draw for a spec (an empty chart, with a note, when nothing matches)."""
    vocab = vocab or Vocab.from_db(db)
    spec = replace(spec, notes=list(spec.notes))
    notes = spec.notes
    level = _level(spec, spec.metric, spec.metric2)
    metric = _metric_for(level, spec.metric, notes)
    spec.metric = metric
    kind = METRICS[metric].kind
    title = spec.title(vocab)
    chart = Chart(spec, title, [], AXES[spec.x].split(" (")[0], METRICS[metric].label,
                  y_format=_FORMATS.get(kind, ""), notes=notes)
    subjects = _subjects(spec, vocab)
    if not subjects:
        notes.append("That team isn't saved yet: build it on Team Analytics.")
        return chart

    if spec.kind == "scatter":
        metric2 = _metric_for(level, spec.metric2 or "kost", notes)
        chart.y2_label = METRICS[metric2].label
        chart.x_label = METRICS[metric].label
        chart.y_label = METRICS[metric2].label
        chart.series_label = AXES[spec.x].split(" (")[0]
        name, q = subjects[0]
        if spec.x == "player" and spec.who in ("me", "players") and len(subjects) == 1 and spec.who == "me":
            q = replace(q, who="everyone")
        rows = _rows(db, vocab, q, [metric, metric2, "matches", "rounds"], spec.x, level, limit=60)
        minimum = 2 if spec.x == "player" else 5
        unit = "matches" if level == "match" else "rounds"
        rows = [r for r in rows if (r.get(unit) or 0) >= minimum and r.get(metric) is not None
                and r.get(metric2) is not None]
        chart.rows = [{"x": _value(r[metric], kind), "y": _value(r[metric2], METRICS[metric2].kind),
                       "series": r["name"], "size": r.get(unit)} for r in rows]
        chart.y_format = _FORMATS.get(METRICS[metric2].kind, "")
        notes.append(f"Each dot is a {chart.series_label.lower()} with at least {minimum} {unit}.")
        return _finish(chart)

    if spec.kind == "heatmap":
        dim2 = spec.split if spec.split not in ("none", "who") else ("side" if spec.x != "side" else "map")
        level = _level(replace(spec, split=dim2), metric)
        metric = _metric_for(level, metric, notes)
        name, q = subjects[0]
        chart.series_label = AXES.get(dim2, dim2).split(" (")[0]
        for value in _filter_values(db, vocab, dim2, spec):
            for r in _rows(db, vocab, _narrow(q, dim2, value), [metric], spec.x, level, limit=12):
                if r.get(metric) is not None:
                    chart.rows.append({"x": r["name"], "x2": _label_value(dim2, value), "y": _value(r[metric], kind)})
        return _finish(chart)

    if spec.kind in ("histogram", "box"):
        level = "round" if (spec.operators or spec.side or METRICS[metric].match_sql is None
                            or (spec.kind == "box" and spec.split in ROUND_DIMS)) else "match"
        metric = _metric_for(level, metric, notes)
        group = "player_match"
        for name, q in subjects:
            splits = [(None, q)] if spec.split in ("none", "who") or spec.kind == "histogram" else \
                [(_label_value(spec.split, v), _narrow(q, spec.split, v)) for v in _filter_values(db, vocab, spec.split, spec)]
            for label, sub in splits:
                for r in _rows(db, vocab, sub, [metric], group, level):
                    if r.get(metric) is not None:
                        chart.rows.append({"x": label or name, "y": _value(r[metric], kind), "series": name})
        chart.x_label = METRICS[metric].label if spec.kind == "histogram" else (
            AXES.get(spec.split, "Who").split(" (")[0] if spec.split not in ("none", "who") else "Who")
        chart.series_label = "Who" if len(subjects) > 1 else ""
        notes.append(f"Each value is one player's {METRICS[metric].phrase} in one match.")
        return _finish(chart)

    # line, area, bar, hbar, stacked, pie: one row per x (and series)
    multi = len(subjects) > 1
    split = spec.split if spec.split in ("side", "map", "match_type") and spec.x != spec.split else None
    if split:
        level = _level(replace(spec, split=split), metric)
        metric = _metric_for(level, metric, notes)
    for name, q in subjects:
        parts = [(None, q)] if not split else \
            [(_label_value(split, v), _narrow(q, split, v)) for v in _filter_values(db, vocab, split, spec)]
        for label, sub in parts:
            rows = _rows(db, vocab, sub, [metric], spec.x, level, limit=spec.limit if spec.kind != "pie" else 12)
            series = " · ".join(b for b in ((name if multi else None), label) if b) or name
            values = [r.get(metric) for r in rows]
            if spec.cumulative:
                total = 0.0
                values = [total := total + (v or 0) for v in values]
            if spec.rolling and spec.rolling > 1:
                window: list[float] = []
                smooth = []
                for v in values:
                    if v is not None:
                        window = (window + [v])[-spec.rolling:]
                    smooth.append(sum(window) / len(window) if window else None)
                values = smooth
            for i, (r, v) in enumerate(zip(rows, values)):
                if v is None:
                    continue
                x = r["name"]
                if spec.x == "match":
                    x = r.get("played_at") or r["name"]
                chart.rows.append({"x": x, "y": _value(v, kind), "series": series,
                                   "label": r["name"], "order": i})
    chart.series_label = "Series" if (multi or split) else ""
    if spec.rolling > 1:
        chart.notes.append(f"Smoothed: each point is the average of the last {spec.rolling}.")
    if spec.x == "match" and chart.rows:
        chart.x_label = "Match date"
    return _finish(chart)


def _finish(chart: Chart) -> Chart:
    if not chart.rows:
        chart.notes.append("No matches found for that. Try fewer filters, or another stat or grouping.")
    return chart


def describe(chart: Chart) -> str:
    """One sentence about what the chart shows (for the assistant and the page)."""
    if not chart.rows:
        return "There's nothing to chart for that."
    spec = chart.spec
    if spec.kind in ("line", "area") and spec.x in TIME_AXES:
        by_series: dict[str, list[float]] = {}
        for r in chart.rows:
            by_series.setdefault(r.get("series", ""), []).append(r["y"])
        bits = []
        for name, values in by_series.items():
            if spec.cumulative:
                bits.append(f"{name}: {values[-1]:g} in all")
            elif len(values) >= 4:  # the earlier half's average against the later half's
                half = len(values) // 2
                early, late = sum(values[:half]) / half, sum(values[-half:]) / half
                change = "up" if late > early * 1.03 else "down" if late < early * 0.97 else "steady"
                bits.append(f"{name}: about {early:.3g} early on, {late:.3g} lately ({change})")
            elif values:
                bits.append(f"{name}: {', '.join(f'{v:g}' for v in values)}")
        return "; ".join(bits) + "."
    if spec.kind in ("bar", "hbar", "pie", "stacked", "heatmap"):
        top = max(chart.rows, key=lambda r: r["y"])
        low = min(chart.rows, key=lambda r: r["y"])
        name = lambda r: " · ".join(str(r[k]) for k in ("x", "x2") if r.get(k) is not None)  # noqa: E731
        if spec.kind == "pie":
            total = sum(r["y"] for r in chart.rows) or 1
            return f"Biggest share: {name(top)} ({100 * top['y'] / total:.0f}%)."
        if top is low:
            return f"{name(top)}: {top['y']:g}."
        return f"Highest: {name(top)} ({top['y']:g}). Lowest: {name(low)} ({low['y']:g})."
    if spec.kind == "scatter":
        return f"{len(chart.rows)} dots: {chart.x_label} across, {chart.y_label} up. Top right is best at both."
    values = sorted(r["y"] for r in chart.rows)
    middle = values[len(values) // 2]
    return f"From {values[0]:g} to {values[-1]:g}, usually around {middle:g} ({len(values)} values)."


def nice_x(value: Any, x: str) -> str:
    return nice_day(value) if x in ("day", "week", "month") else str(value)
