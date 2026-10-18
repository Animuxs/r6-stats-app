"""Portable match-day planning data; no network requests or inferred availability."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import escape

from stats_db import nice_day

AVAILABILITY = ("Unconfirmed", "Confirmed", "Unavailable")


@dataclass
class MatchdayWorkspace:
    fixtures: list[dict] = field(default_factory=list)
    availability: dict[str, str] = field(default_factory=dict)
    notes: str = ""

    @classmethod
    def from_dict(cls, value: dict) -> "MatchdayWorkspace":
        if not isinstance(value, dict):
            raise ValueError("Choose a match-day workspace JSON object.")
        fixtures, availability = value.get("fixtures", []), value.get("availability", {})
        notes = value.get("notes", "")
        if not isinstance(fixtures, list) or len(fixtures) > 200 or not isinstance(availability, dict):
            raise ValueError("Invalid match-day fixtures or availability.")
        clean, seen = [], set()
        for fixture in fixtures:
            if not isinstance(fixture, dict):
                raise ValueError("A fixture is incomplete.")
            fields = {key: fixture.get(key, "") for key in ("id", "opponent", "when", "timezone", "kind", "team")}
            if any(not isinstance(v, str) or len(v) > 180 for v in fields.values()):
                raise ValueError("Fixture fields must be text of at most 180 characters.")
            if not fields["id"] or fields["id"] in seen or not fields["opponent"].strip():
                raise ValueError("Every fixture needs a unique ID and an opponent.")
            try:
                datetime.fromisoformat(fields["when"])
            except ValueError as exc:
                raise ValueError("A fixture has an invalid date or time.") from exc
            seen.add(fields["id"])
            clean.append(fields)
        if len(availability) > 200 or any(not isinstance(n, str) or len(n) > 180 or s not in AVAILABILITY
                                         for n, s in availability.items()):
            raise ValueError("Invalid roster availability.")
        if not isinstance(notes, str) or len(notes) > 10000:
            raise ValueError("Keep preparation notes under 10,000 characters.")
        return cls(clean, dict(availability), notes)

    def to_dict(self) -> dict:
        return {"version": 1, "fixtures": self.fixtures, "availability": self.availability, "notes": self.notes}

    def upcoming(self, today: date | None = None) -> list[dict]:
        today = today or date.today()
        return sorted((f for f in self.fixtures if datetime.fromisoformat(f["when"]).date() >= today),
                      key=lambda f: f["when"][:19])


def demo_workspace(today: date | None = None) -> MatchdayWorkspace:
    today = today or date.today()
    return MatchdayWorkspace([
        {"id": f"demo-{i}", "opponent": opponent, "team": "Lakeside Varsity", "kind": kind,
         "when": f"{today + timedelta(days=days)}T19:00:00", "timezone": "Central Time"}
        for i, (days, opponent, kind) in enumerate(((2, "Metro Tech", "League · BO3"),
                                                  (4, "Northgate Esports", "Practice · BO3"),
                                                  (7, "Harbor State", "League · BO3")))
    ], {"Fabian": "Confirmed", "Kanto": "Confirmed", "Sens": "Unconfirmed", "Sacri": "Confirmed", "BC": "Confirmed"},
       "Example preparation notes: review retakes and confirm the map veto before match time.")


def head_to_head(db, opponent: str) -> str | None:
    """Your record against a team, by the name the replays give it, and how the last meeting went:
    "Your record: 1–1 · last met Oct 9, lost 5–7". None when you haven't played it (or don't know who you are)."""
    me = db.me()
    if not me or not opponent.strip():
        return None
    rows = db.query(
        """SELECT m.played_at, m.score0, m.score1, mp.team, mp.won FROM match_players mp
           JOIN matches m ON m.match_id = mp.match_id
           WHERE mp.player_key = ? AND lower(trim(CASE mp.team WHEN 0 THEN m.team1 ELSE m.team0 END)) = ?
           ORDER BY m.played_at DESC""", [me.casefold(), " ".join(opponent.casefold().split())])
    if not rows:
        return "First meeting in your stats"
    wins, losses = sum(r["won"] == 1 for r in rows), sum(r["won"] == 0 for r in rows)
    last = rows[0]
    own, other = (last["score1"], last["score0"]) if last["team"] == 1 else (last["score0"], last["score1"])
    result = {1: "won", 0: "lost"}.get(last["won"], "drew")
    return f"Your record: {wins}–{losses} · last met {nice_day(last['played_at'])}, {result} {own}–{other}"


def panel_html(workspace: MatchdayWorkspace, recent: list[dict], roster: list[str], demo: bool = False,
               records: dict[str, str] | None = None) -> str:
    """The match-day panel. `records` is each upcoming opponent's head-to-head line (see head_to_head)."""
    esc = lambda value: escape(str(value), quote=True)
    label = "Example schedule · demo only" if demo else "Manually entered schedule"
    cards = []
    for fixture in workspace.upcoming()[:3]:
        when = datetime.fromisoformat(fixture["when"]).strftime("%a, %b %d · %H:%M")
        record = (records or {}).get(fixture["opponent"].casefold())
        record_html = f'<small class="md-record">{esc(record)}</small>' if record else ""
        cards.append(f'<article class="md-fixture"><small>{esc(when)} · {esc(fixture["timezone"])}</small>'
                     f'<b>vs {esc(fixture["opponent"])}</b><small>{esc(fixture["team"])} · {esc(fixture["kind"])}</small>'
                     f'{record_html}</article>')
    upcoming = ''.join(cards) or '<p class="md-empty">No upcoming matches entered.</p>'
    form = []
    for match in recent[:5]:
        result = {1: "W", 0: "L"}.get(match.get("won"), "—")
        own, other = ((match.get("score1"), match.get("score0")) if match.get("team") == 1
                      else (match.get("score0"), match.get("score1")))
        form.append(f'<div class="md-recent"><b class="md-result">{result}</b><span>{esc(match.get("map") or "Unknown map")}'
                    f'<small>{esc((match.get("played_at") or "")[:10])}</small></span>'
                    f'<span>{esc("—" if own is None else own)}–{esc("—" if other is None else other)}</span></div>')
    recent_html = ''.join(form) or '<p class="md-empty">Confirm matches to see recent form.</p>'
    people = ''.join(f'<div class="md-person"><span>{esc(name)}</span><small>{esc(workspace.availability.get(name, "Unconfirmed"))}</small></div>'
                     for name in roster[:10]) or '<p class="md-empty">Save a team to see its roster.</p>'
    notes = esc(workspace.notes).replace('\n', '<br>') if workspace.notes else 'No preparation notes saved.'
    return (f'<aside class="matchday-panel"><section><h3>Upcoming matches</h3><p class="md-label">{label}</p>{upcoming}</section>'
            f'<section><h3>Recent form</h3><p class="md-label">{"Made-up demo results" if demo else "Confirmed matches"}</p>{recent_html}</section>'
            f'<section><h3>Roster availability</h3><p class="md-label">{"Sample availability" if demo else "Manual confirmations · never inferred"}</p>{people}</section>'
            f'<section><h3>Match prep</h3><p class="md-notes">{notes}</p></section></aside>')
