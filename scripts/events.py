"""
events.py
=========
The leagues and tournaments a team can say it plays in, for the League & Team page:

- Liquipedia's Rainbow Six events, S-Tier to D-Tier, that are on now, coming up, or ended in the
  last six months, from Liquipedia's MediaWiki API (https://liquipedia.net/rainbowsix). A snapshot
  ships with each release (liquipedia_events.json, made with `python scripts/events.py`), and the
  Windows app can check for new events at most once a day (refresh).
- NECC's Rainbow Six season: its placement event and Divisions I-VI with the teams in each
  (necc_divisions.json, a dated snapshot of necc.v1.leagueos.gg's public season pages).

Liquipedia's API terms (https://liquipedia.net/api-terms-of-use) are kept: at most one request
every 2 seconds over one connection, gzip, a User-Agent naming this project and where to reach
it, and results kept for as long as they're useful (only new and still-running events are read
again). Its content is CC-BY-SA 3.0, so ATTRIBUTION goes wherever its events are shown.

An event (both sources):
    {"id", "name", "source": "liquipedia" | "necc", "tier": "S".."D" or "", "kind": "Qualifier",
     "Monthly", ... or "", "organizer", "location", "region", "mode": "Online" | "Offline" | "",
     "start", "end": "YYYY-MM-DD" (Liquipedia may leave "??" for a day or month not yet known),
     "prize_usd": int | None, "team_count": int | None, "teams": [team names], "url", "website"}
NECC events add "division", and "schools" / "groups": {team name: school / group}.
"""

from __future__ import annotations

import calendar
import gzip
import http.client
import json
import os
import re
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote, urlencode

import season_stats
from app_info import APP_NAME, APP_VERSION, GITHUB_REPO

_HERE = Path(__file__).resolve().parent
BUNDLED = _HERE / "liquipedia_events.json"
NECC_DIVISIONS = _HERE / "necc_divisions.json"

WIKI = "https://liquipedia.net/rainbowsix"
API_HOST, API_PATH = "liquipedia.net", "/rainbowsix/api.php"
USER_AGENT = f"{APP_NAME.replace(' ', '')}/{APP_VERSION} (https://github.com/{GITHUB_REPO})"
ATTRIBUTION = "Event data from Liquipedia (liquipedia.net/rainbowsix), CC-BY-SA 3.0."
LICENSE_URL = "https://liquipedia.net/rainbowsix/Liquipedia:Copyrights"

TIERS = ("S", "A", "B", "C", "D")  # Liquipedia's liquipediatier 1-5
TIER_CATEGORIES = {tier: f"Category:{tier}-Tier Tournaments" for tier in TIERS}
WINDOW = timedelta(days=183)        # events that ended longer ago than this aren't listed
LOOKBACK = timedelta(days=548)      # pages put in a tier category longer ago than this aren't read
REFRESH_EVERY = timedelta(days=1)   # the app checks Liquipedia at most this often
REQUEST_GAP = 2.1                   # seconds between requests (Liquipedia: no more than 1 per 2 s)
BATCH = 50                          # pages per request, the API's limit
MAX_TEAMS = 256
# points tables, show matches and the like aren't events a team plays in
SKIPPED_KINDS = {"points", "show match", "showmatch", "misc"}

# Liquipedia's "country" holds regions, countries and two-letter codes alike; these group them
_REGIONS = {
    "North America": {"north america", "na", "united states", "usa", "us", "canada", "ca"},
    "Latin America": {"south america", "latin america", "latam", "brazil", "br", "mexico", "mx", "argentina", "ar",
                      "chile", "cl", "colombia", "co", "peru", "pe", "uruguay", "uy", "sa"},
    "Europe & MENA": {"europe", "eu", "emea", "mena", "middle east", "north africa", "saudi arabia",
                      "united arab emirates", "uae", "ae", "turkey", "tr", "united kingdom", "uk", "gb", "england",
                      "france", "fr", "germany", "de", "spain", "es", "italy", "it", "portugal", "pt", "poland",
                      "pl", "hungary", "hu", "nordic", "denmark", "dk", "sweden", "se", "norway", "no", "finland",
                      "fi", "netherlands", "nl", "belgium", "be", "czech republic", "cz", "russia", "ru",
                      "ukraine", "ua", "cis", "dach", "benelux", "baltic", "balkans", "romania", "ro", "greece",
                      "gr", "austria", "at", "switzerland", "ch", "ireland", "ie", "israel", "il", "egypt", "eg",
                      "kuwait", "kw", "qatar", "qa", "slovakia", "sk", "serbia", "rs", "croatia", "hr"},
    "Asia-Pacific": {"asia", "asia pacific", "apac", "japan", "jp", "korea", "south korea", "kr", "china", "cn",
                     "southeast asia", "sea", "oceania", "australia", "au", "new zealand", "nz", "india", "in",
                     "taiwan", "tw", "hong kong", "hk", "singapore", "sg", "philippines", "ph", "thailand", "th",
                     "indonesia", "id", "malaysia", "my", "vietnam", "vn", "mongolia", "mn", "pakistan", "pk"},
    "Global": {"global", "world", "international", "worldwide"},
}
_REGION_OF = {name: region for region, names in _REGIONS.items() for name in names}
_CODES = {"us": "United States", "ca": "Canada", "br": "Brazil", "mx": "Mexico", "fr": "France", "de": "Germany",
          "es": "Spain", "it": "Italy", "pl": "Poland", "uk": "United Kingdom", "gb": "United Kingdom",
          "jp": "Japan", "kr": "South Korea", "cn": "China", "au": "Australia", "eu": "Europe", "sa": "South America",
          "na": "North America", "hu": "Hungary", "pt": "Portugal", "nl": "Netherlands", "tr": "Turkey"}


# ---------------------------------------------------------------- wikitext ----

def template_params(text: str, name: str) -> dict[str, str] | None:
    """The parameters of the first {{name ...}} template in `text` ({param: raw value}, with
    unnamed ones as "1", "2", ...), or None when it isn't there. Nested templates and links stay
    inside the value they belong to."""
    # "Infobox league" is also written "Infobox_league"
    pattern = re.escape(name).replace(r"\ ", "[ _]")
    start = re.search(r"\{\{\s*" + pattern + r"\s*[|}\n]", text, re.I)
    if not start:
        return None
    depth, i, parts, current = 0, start.start(), [], []
    while i < len(text):
        pair = text[i:i + 2]
        if pair in ("{{", "[["):
            depth += 1
            current.append(pair)
            i += 2
        elif pair in ("}}", "]]"):
            depth -= 1
            if depth == 0:
                parts.append("".join(current))
                break
            current.append(pair)
            i += 2
        elif text[i] == "|" and depth == 1:
            parts.append("".join(current))
            current = []
            i += 1
        else:
            current.append(text[i])
            i += 1
    params: dict[str, str] = {}
    unnamed = 0
    for part in parts[1:]:
        key, eq, value = part.partition("=")
        if eq and re.fullmatch(r"\s*[\w ]+\s*", key):
            params[key.strip().lower()] = value.strip()
        else:
            unnamed += 1
            params[str(unnamed)] = part.strip()
    return params


def plain(value: str, page: str = "") -> str:
    """A wikitext value as text: links and templates down to the words they show."""
    value = value.replace("{{!}}", "|").replace("&nbsp;", " ")
    value = re.sub(r"\{\{\s*SUBPAGENAME\s*\}\}", page.rsplit("/", 1)[-1], value, flags=re.I)
    value = re.sub(r"<!--.*?-->|<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", value, flags=re.S)
    value = re.sub(r"\{\{\s*TeamIcon\s*\|[^{}]*\}\}", "", value, flags=re.I)
    for _ in range(3):  # {{Team|Name}}, {{Abbr|X|...}}: the first argument is what shows
        value = re.sub(r"\{\{[^{}|]*\|([^{}|]*)(?:\|[^{}]*)?\}\}", r"\1", value)
        value = re.sub(r"\{\{[^{}]*\}\}", "", value)
    value = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", value)
    value = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", value)
    value = re.sub(r"\[https?://[^\]\s]+\]", "", value)
    value = re.sub(r"<[^>]+>|'''?", "", value)
    return re.sub(r"\s+", " ", value).strip(" ,;")


def _templates(text: str, name: str) -> list[str]:
    """Every whole {{name ...}} template in `text`, nested templates and all."""
    found = []
    for start in re.finditer(r"\{\{\s*" + re.escape(name) + r"\s*[|}\n]", text, re.I):
        depth, i = 0, start.start()
        while i < len(text):
            pair = text[i:i + 2]
            if pair == "{{":
                depth += 1
                i += 2
            elif pair == "}}":
                depth -= 1
                i += 2
                if depth == 0:
                    found.append(text[start.start():i])
                    break
            else:
                i += 1
    return found


def participants(text: str) -> list[str]:
    """The teams in a tournament page's participant lists ({{TeamParticipants}}'s {{Opponent|name}}
    and the older {{TeamCard|team=name}}), in page order, each once. Opponents elsewhere on the
    page, like an MVP award's player, aren't teams."""
    names = [m.group(1) for block in _templates(text, "TeamParticipants")
             for m in re.finditer(r"\{\{\s*(?:Team)?Opponent\s*\|\s*([^|}\n]+)", block, re.I)]
    names += [m.group(1) for m in re.finditer(r"\{\{\s*TeamCard\s*\|[^{}]*?\bteam\s*=\s*([^|}\n]+)", text, re.I)]
    # no participant list, or only "TBD"s (many qualifiers): the teams in its brackets, which only name teams
    return (_team_names(names)
            or _team_names(m.group(1) for m in re.finditer(r"\{\{\s*TeamOpponent\s*\|\s*([^|}\n]+)", text, re.I)))


def _team_names(names: Iterable[str]) -> list[str]:
    """Raw team names cleaned up: placeholders dropped, a team template's lowercase key
    capitalized, each team once in first-seen order."""
    found: dict[str, str] = {}
    for name in names:
        name = plain(name).strip()
        if not name or name.casefold() in {"tbd", "tba", "bye", "solo", "literal"} or "=" in name:
            continue
        if name == name.lower():  # a team template's key, e.g. "fisher college"
            name = " ".join(w[:1].upper() + w[1:] for w in name.split())
        found.setdefault(name.casefold(), name)
    return list(found.values())[:MAX_TEAMS]


def parse_day(value: str, end: bool = False) -> date | None:
    """A Liquipedia date ("2026-10-06", "2026-10-??", "2026-??-??") as a day: an unknown day or
    month is the first one, or the last for an end date."""
    found = re.match(r"\s*(\d{4})-(\d{2}|\?\?)-(\d{2}|\?\?)", value or "")
    if not found:
        return None
    year = int(found.group(1))
    month = int(found.group(2)) if found.group(2) != "??" else (12 if end else 1)
    if not 1 <= month <= 12:
        return None
    last = calendar.monthrange(year, month)[1]
    day = int(found.group(3)) if found.group(3) != "??" else (last if end else 1)
    return date(year, month, min(max(day, 1), last))


def region_of(location: str) -> str:
    """The broad region a Liquipedia location falls in, for filtering ("Other" if unknown)."""
    key = location.strip().casefold()
    return _REGION_OF.get(key) or next((_REGION_OF[part] for part in re.split(r"[,/&]| and ", key)
                                        if part.strip() in _REGION_OF), "Other" if key else "")


def _number(value: str) -> int | None:
    digits = re.sub(r"[^\d.]", "", plain(value))
    try:
        return int(float(digits)) if digits else None
    except ValueError:
        return None


def event_from_page(title: str, text: str, category_tier: str = "") -> dict[str, Any] | None:
    """The event on a Liquipedia tournament page, from its {{Infobox league}}; None for a page
    without one, without dates, or that isn't an event a team plays in (a points table, ...)."""
    box = template_params(text, "Infobox league")
    if box is None:
        return None
    kind = plain(box.get("liquipediatiertype", ""))
    if kind.casefold() in SKIPPED_KINDS:
        return None
    # a one-day event gives just "date"
    start = plain(box.get("sdate", "")) or plain(box.get("date", ""))
    end = plain(box.get("edate", "")) or start
    if not parse_day(start) and not parse_day(end):
        return None
    tier = plain(box.get("liquipediatier", ""))
    tier = TIERS[int(tier) - 1] if tier.isdigit() and 1 <= int(tier) <= 5 else category_tier
    location = plain(box.get("country", "")) or plain(box.get("region", ""))
    location = _CODES.get(location.casefold(), location if location != location.lower() else location.title())
    website = plain(box.get("web", "")) if box.get("web", "").startswith("http") else ""
    return {
        "id": title,
        "name": plain(box.get("name", ""), title) or title.replace("/", " "),
        "source": "liquipedia",
        "tier": tier,
        "kind": kind[:1].upper() + kind[1:] if kind else "",
        "organizer": ", ".join(o for o in (plain(box.get(k, "")) for k in ("organizer", "organizer2")) if o),
        "location": location,
        "region": region_of(location),
        "mode": plain(box.get("type", "")).capitalize(),
        "start": start or end,
        "end": end,
        "prize_usd": _number(box.get("prizepoolusd", "")),
        "team_count": _number(box.get("team_number", "")),
        "teams": participants(text),
        "url": f"{WIKI}/{quote(title.replace(' ', '_'))}",
        "website": website,
    }


def _settled(text: str) -> bool:
    """A page that won't become a listed event: a points table, a show match, a series' overview
    page. (A page with no infobox or no dates yet may still become one, so it's read again.)"""
    box = template_params(text, "Infobox league")
    if box is None:  # a series' overview page, not one of its events
        return template_params(text, "Infobox series") is not None
    return plain(box.get("liquipediatiertype", "")).casefold() in SKIPPED_KINDS


def in_window(event: dict[str, Any], today: date) -> bool:
    """On now, coming up, or ended within WINDOW of `today`."""
    end = parse_day(event.get("end", ""), end=True) or parse_day(event.get("start", ""), end=True)
    return end is not None and end >= today - WINDOW


def status(event: dict[str, Any], today: date) -> str:
    """"Upcoming", "Live" or "Ended" on `today`."""
    start = parse_day(event.get("start", ""))
    end = parse_day(event.get("end", ""), end=True) or start
    if start and start > today:
        return "Upcoming"
    return "Ended" if end and end < today else "Live"


def _disambiguate(events: list[dict[str, Any]]) -> None:
    """Two pages sometimes carry the same infobox name: tell them apart by their page."""
    seen: dict[str, int] = {}
    for event in events:
        seen[event["name"].casefold()] = seen.get(event["name"].casefold(), 0) + 1
    for event in events:
        if seen[event["name"].casefold()] > 1:
            event["raw_name"] = event["name"]
            event["name"] = f'{event["name"]} ({event["id"].replace("/", " / ")})'


# ----------------------------------------------------------------- the API ----

class LiquipediaClient:
    """Liquipedia's MediaWiki API, as its terms ask: one kept-alive connection, gzip, a
    User-Agent naming this project, and at least REQUEST_GAP seconds between requests."""

    def __init__(self, connect: Callable[[], http.client.HTTPConnection] | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self._connect = connect or (lambda: http.client.HTTPSConnection(API_HOST, timeout=30))
        self._conn: http.client.HTTPConnection | None = None
        self._sleep, self._clock = sleep, clock
        self._last = float("-inf")
        self.requests = 0

    def get(self, **params: Any) -> dict[str, Any]:
        wait = self._last + REQUEST_GAP - self._clock()
        if wait > 0:
            self._sleep(wait)
        query = urlencode({"format": "json", "formatversion": "2", **params})
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", "Accept": "application/json"}
        for attempt in (1, 2):  # a kept-alive connection the server has closed is opened again, once
            if self._conn is None:
                self._conn = self._connect()
            try:
                self._conn.request("GET", f"{API_PATH}?{query}", headers=headers)
                response = self._conn.getresponse()
                body = response.read()
                break
            except (http.client.HTTPException, ConnectionError):
                self.close()
                if attempt == 2:
                    raise
        self._last = self._clock()
        self.requests += 1
        if response.status != 200:
            raise OSError(f"Liquipedia answered {response.status} {response.reason}")
        if response.getheader("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        data = json.loads(body)
        if "error" in data:
            raise OSError(f"Liquipedia: {data['error'].get('info') or data['error']}")
        return data

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def tier_pages(client: LiquipediaClient, since: date) -> dict[str, str]:
    """{page title: tier} for every page put in an S-D tier category since `since`, newest first
    (a page in two tiers keeps the higher one)."""
    found: dict[str, str] = {}
    cutoff = f"{since.isoformat()}T00:00:00Z"
    for tier, category in TIER_CATEGORIES.items():
        more: dict[str, str] = {}
        while True:
            reply = client.get(action="query", list="categorymembers", cmtitle=category, cmnamespace=0,
                               cmlimit=500, cmsort="timestamp", cmdir="desc", cmprop="title|timestamp", **more)
            batch = reply.get("query", {}).get("categorymembers", [])
            for page in batch:
                if page["timestamp"] >= cutoff:
                    found.setdefault(page["title"], tier)
            if not batch or batch[-1]["timestamp"] < cutoff or "continue" not in reply:
                break
            more = reply["continue"]
    return found


def page_texts(client: LiquipediaClient, titles: Iterable[str],
               progress: Callable[[int, int], None] | None = None) -> dict[str, str]:
    """{title: wikitext} for `titles`, BATCH pages per request."""
    titles = list(titles)
    texts: dict[str, str] = {}
    for start in range(0, len(titles), BATCH):
        reply = client.get(action="query", prop="revisions", rvprop="content", rvslots="main",
                           titles="|".join(titles[start:start + BATCH]))
        for page in reply.get("query", {}).get("pages", []):
            revisions = page.get("revisions") or []
            if revisions:
                texts[page["title"]] = revisions[0].get("slots", {}).get("main", {}).get("content", "")
        if progress:
            progress(min(start + BATCH, len(titles)), len(titles))
    return texts


def build_catalog(tiers: dict[str, str], texts: dict[str, str], today: date,
                  previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """The catalog from the tier categories' pages (`tiers`) and the ones read now (`texts`);
    events of `previous` that weren't read again are kept while their pages are still listed."""
    previous = previous or {}
    # copies: `previous` may be the cached catalog other pages are reading
    events = {e["id"]: dict(e) for e in previous.get("events", []) if e["id"] in tiers and e["id"] not in texts}
    past = {title for title in previous.get("past_pages", []) if title in tiers and title not in texts}
    for title, text in texts.items():
        event = event_from_page(title, text, tiers.get(title, ""))
        if event and in_window(event, today):
            events[title] = event
        elif event or _settled(text):
            past.add(title)  # ended, or never an event: not read again
    for title in [t for t, e in events.items() if not in_window(e, today)]:
        past.add(title)
        del events[title]
    listed = sorted(events.values(), key=lambda e: (TIERS.index(e["tier"]) if e["tier"] in TIERS else 9,
                                                    e["start"], e["name"].casefold()))
    for event in listed:  # names told apart last time are worked out again
        event["name"] = event.pop("raw_name", event["name"])
    _disambiguate(listed)
    return {"source": "Liquipedia", "url": WIKI, "license": "CC-BY-SA 3.0", "attribution": ATTRIBUTION,
            "captured_at": today.isoformat(), "checked_at": datetime.now().isoformat(timespec="seconds"),
            "events": listed, "past_pages": sorted(past)}


def crawl(client: LiquipediaClient, today: date, previous: dict[str, Any] | None = None,
          progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """A fresh catalog from Liquipedia. With `previous`, only pages it doesn't know and events
    that haven't ended are read again: an ended event, or a page that isn't one, won't change."""
    tiers = tier_pages(client, today - WINDOW - LOOKBACK)
    known = {e["id"]: e for e in (previous or {}).get("events", [])}
    past = set((previous or {}).get("past_pages", []))
    todo = [t for t in tiers if t not in past and (t not in known or status(known[t], today) != "Ended")]
    return build_catalog(tiers, page_texts(client, todo, progress), today, previous)


# ------------------------------------------------------- stored catalogs ----

def cache_path() -> Path:
    """The catalog the app last fetched, next to the season database (read at call time)."""
    return Path(season_stats.DEFAULT_DB_PATH).parent / "liquipedia_events.json"


_loaded: dict[str, tuple[tuple[int, int, int], dict[str, Any] | None]] = {}


def load_catalog(path: Path) -> dict[str, Any] | None:
    """A stored catalog, or None if it's missing or broken. Each file is read once until it
    changes: every page view and API call asks for it."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key, stamp = str(path), (stat.st_mtime_ns, stat.st_size, stat.st_ino)  # a file replaced in the same tick is new
    if key in _loaded and _loaded[key][0] == stamp:
        return _loaded[key][1]
    try:
        catalog = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        catalog = None
    if not (isinstance(catalog, dict) and isinstance(catalog.get("events"), list)):
        catalog = None
    _loaded[key] = (stamp, catalog)
    return catalog


def save_catalog(catalog: dict[str, Any], path: Path) -> None:
    """Write atomically, so a crash mid-write never leaves a broken file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(catalog, file, ensure_ascii=False, indent=1)
            file.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def current_catalog() -> dict[str, Any]:
    """The newest catalog there is: the one fetched on this PC, or the release's snapshot."""
    candidates = [c for c in (load_catalog(cache_path()), load_catalog(BUNDLED)) if c]
    if not candidates:
        return {"events": [], "past_pages": [], "captured_at": "", "attribution": ATTRIBUTION}
    return max(candidates, key=lambda c: (c.get("captured_at", ""), c.get("checked_at", "")))


def current_events(catalog: dict[str, Any] | None = None, today: date | None = None) -> list[dict[str, Any]]:
    """The catalog's events still in the window today, each with its "status"."""
    today = today or date.today()
    catalog = catalog if catalog is not None else current_catalog()
    return [dict(e, status=status(e, today)) for e in catalog.get("events", []) if in_window(e, today)]


def next_refresh(catalog: dict[str, Any]) -> datetime | None:
    """When Liquipedia may be checked again (None: now)."""
    try:
        checked = datetime.fromisoformat(catalog.get("checked_at") or "")
    except ValueError:
        return None
    due = checked + REFRESH_EVERY
    return due if due > datetime.now() else None


def refresh(progress: Callable[[int, int], None] | None = None, client: LiquipediaClient | None = None,
            today: date | None = None) -> dict[str, Any]:
    """Check Liquipedia for new and changed events and keep the result on this PC. Raises
    OSError when it can't be reached, or when it was checked less than REFRESH_EVERY ago."""
    catalog = current_catalog()
    due = next_refresh(catalog)
    if due:
        raise OSError(f"Liquipedia was checked recently. It can be checked again after {due:%b %d, %H:%M}.")
    client = client or LiquipediaClient()
    try:
        fresh = crawl(client, today or date.today(), catalog, progress)
    finally:
        client.close()
    save_catalog(fresh, cache_path())
    return fresh


# ------------------------------------------------------------------ NECC ----

def _necc() -> dict[str, Any]:
    stamp = os.stat(NECC_DIVISIONS).st_mtime_ns
    if _loaded.get("necc", (None,))[0] != stamp:
        _loaded["necc"] = (stamp, json.loads(NECC_DIVISIONS.read_text(encoding="utf-8")))
    return _loaded["necc"][1]


def necc_events() -> list[dict[str, Any]]:
    """NECC's Rainbow Six season: the placement event and Divisions I-VI, in the same shape as
    Liquipedia's events (new dicts each call, so callers may change them)."""
    data = _necc()
    events = []
    for raw in data.get("events", []):
        teams = raw.get("teams") or []
        events.append({
            "id": f"necc:{raw['id']}", "name": f"NECC {raw['name']}", "source": "necc", "tier": "",
            "kind": "Division" if raw.get("division") else "Placement", "division": raw.get("division", ""),
            "organizer": "NECC", "location": "North America", "region": "North America", "mode": "Online",
            "start": raw.get("start", ""), "end": raw.get("end", ""), "prize_usd": None,
            "team_count": len(teams) or None, "teams": [t["name"] for t in teams],
            "schools": {t["name"]: t.get("school", "") for t in teams},
            "groups": {t["name"]: t["group"] for t in teams if t.get("group")},
            "url": raw.get("url", ""), "website": data.get("source_url", ""),
        })
    return events


def necc_captured_at() -> str:
    return _necc().get("captured_at", "")


if __name__ == "__main__":
    # Make the snapshot each release ships: `python scripts/events.py` (about 30 requests, a few
    # minutes at Liquipedia's pace). It starts from the current snapshot, so only new and
    # still-running events are read again; pass --full to read every page.
    import sys

    full = "--full" in sys.argv
    previous = None if full else load_catalog(BUNDLED)
    client = LiquipediaClient()
    try:
        catalog = crawl(client, date.today(), previous,
                        lambda done, total: print(f"  pages {done}/{total}", flush=True))
    finally:
        client.close()
    save_catalog(catalog, BUNDLED)
    counts = {tier: sum(e["tier"] == tier for e in catalog["events"]) for tier in TIERS}
    print(f"{len(catalog['events'])} events ({counts}) in {client.requests} requests -> {BUNDLED}")
