"""Portable match-day planning data; no network requests or inferred availability."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import escape

from scouting import scout

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


def head_to_head(db, opponent: str, before: str | None = None, leave_out: str | None = None) -> str | None:
    """Your record against a team, by the name the replays give it, and how the last meeting went:
    "Your record: 1–1 · last met Oct 9, lost 5–7"; with `before` (a played_at), only the matches before
    then, and never the match `leave_out` (its id: the one on screen, whose saved time can be a little
    earlier than the replay's). None when there's no name or it's not known who you are."""
    if not db.me() or not opponent.strip():
        return None
    report = scout(db, opponent, before, leave_out)  # the same meetings as Match day's scouting report
    if not report.meetings:
        return "First meeting in your stats"
    wins, losses, draws = report.record
    last = report.meetings[0]
    when = f" {last['When']}" if last["When"] else ""
    return (f"Your record: {wins}–{losses}" + (f"–{draws}" if draws else "")
            + f" · last met{when}, {last['Result'].lower()} {last['Score']}")


def fixture_when(fixture: dict, zone: bool = True) -> str:
    """When a fixture is, the same everywhere: "Mon, Oct 19 · 19:00 · Central Time"."""
    when = f"{datetime.fromisoformat(fixture['when']):%a, %b %d · %H:%M}"
    return when + (f" · {fixture['timezone']}" if zone and fixture.get("timezone") else "")


def panel_html(workspace: MatchdayWorkspace, recent: list[dict], roster: list[str], demo: bool = False,
               records: dict[str, str] | None = None) -> str:
    """The match-day panel. `records` is each upcoming opponent's head-to-head line (see head_to_head)."""
    esc = lambda value: escape(str(value), quote=True)
    label = "Example schedule · demo only" if demo else "Manually entered schedule"
    cards = []
    for fixture in workspace.upcoming()[:3]:
        when = fixture_when(fixture, zone=False)
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
