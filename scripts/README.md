# R6 Match Stats

Give it a Rainbow Six Siege match replay (a `.zip` of the match folder, the
folder itself, or its round `.rec` files) and it builds an **R6 Pro
League-style scoreboard**: one row per player, split by team, with the same
12 columns as the official R6 Esports match page.

| Player | EPS | KD (+/-) | Entry | KOST | KPR | HS | SRV | Clutches | Multikills | Objectives | Dead for trade kill | Trade kills |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Player1 | 116 | 8-2 (+6) | 4-0 (+4) | 100% | 1.14 | 62% | 71% | 0 | 1 | 0 | 1 | 0 |

It comes three ways, all with CSV, JSON and TXT export:

- **a website** (`app.py`, a Streamlit app you can host for free),
- **a Windows app** (`R6MatchStats-Setup.exe`, built from `desktop/`) with its
  own window, Start menu and desktop shortcuts, which reads your replays
  straight from the game's folder,
- **a command-line tool** (`match_stats.py`).

The website and the Windows app also keep the matches you confirm in your own
stats database, with just the players you pick in each, so you can follow your
trends over weeks, build a team out of the players you queue with, and **ask
questions in plain English**: "How has
my K/D changed over my last 10 matches?", "What's my best map?", "Who had the
most kills in a single match?". No SQL needed, and nothing leaves your computer.

> R6 Match Stats is an unofficial fan project, free to use. It isn't made,
> endorsed or supported by Ubisoft. Rainbow Six and Ubisoft are trademarks of
> Ubisoft Entertainment. See [Ubisoft's terms](#ubisofts-terms).

## Quick start (Windows)

From the repo root, in PowerShell:

```powershell
# 1. build the replay parser (needs Go 1.23+: https://go.dev/dl/)
go build -o r6-dissect.exe .

# 2. install the Python dependencies (Python 3.10+)
python -m venv .venv
.venv\Scripts\python -m pip install -r scripts\requirements.txt

# 3a. open the dashboard (http://localhost:8501)
.venv\Scripts\python scripts\app.py

# 3b. ...or print the scoreboard in the terminal
.venv\Scripts\python scripts\match_stats.py "C:\Users\you\Downloads\Match-2026-09-23_19-19-11-23660.zip"
```

On macOS/Linux, use `go build` (it produces `r6-dissect`), `source
.venv/bin/activate`, and forward slashes.

## Using the dashboard

The top navigation has **Dashboard**, **Ask** and **Charts**, then three menus:
**Stats** (Match History, Team Analytics, Operator Analytics), **Team** (Match day,
Team hub, League & Team, Team Theme) and **App** (Get the Windows app). Dashboard
takes a **Replay source**:

- **Folder or zip on this computer**: a path to one match folder, a `.zip`, or
  your whole `MatchReplay` folder. It's filled in automatically when Siege is
  installed through Steam (any library) or Ubisoft Connect, and the latest
  match opens right away. With several matches you get a picker, and each
  match is parsed only when you pick it. Matches already in your stats say
  **✓ added**.
- **Upload**: drop the match's `.zip`, or every `.rec` file from the match folder.
- **From the replays/ folder**: copy matches into `replays/` at the repo
  root. Use this on GitHub Codespaces, where browser uploads over ~50 MB fail.

On the public website only **Upload** is offered: folder paths would read the
web server's disk, so they're only available to someone on the same computer
as the app.

Below the scoreboards you'll find the downloads and a round-by-round section:

- **Rounds**: every round, with the side each team played (⚔ attack, ♜ defense),
  who won, the score after the round, how it was won and the site.
- **Each player's rounds**: their side, operator, whether their team won, K-D-A
  (a green dot if they survived, red if they died) and highlights: multikills
  (2K to ACE), entry kills and deaths, trade kills, traded deaths, plants,
  disables and clutches. Rounds they weren't in say so. Above it, their attack
  and defense split: rounds, rounds won, K-D and the operators they played.
- **Downloads**: CSV (the scoreboard's numbers), JSON (the scoreboard plus every
  round), TXT (the scoreboard and the round list as plain text), Rounds CSV
  (one row per player per round, for spreadsheets) and **Scoreboard image** (a
  PNG of the scoreboard in your Team Theme's color, to post in a team chat).

Operator Analytics also shows which side each operator is played on.

Opening a match adds nothing to your stats. Below the scoreboards, **Add to your
stats** is the only way in: pick the players whose stats to keep from that
match, one list per team, then **Confirm match and players**. Nobody is picked
for you, and a Ranked, Unranked or Quick Match match warns that the game made
the teams, so someone on yours may not be one of your players. Tick **Also save
it to the season tracker** to add the same players' rounds to a season, with a
team or school name for each team. Confirm again with different players to
update a match. The demo match can't be added.

In the Windows app you can **dock** the app to the left or right edge of your
screen, next to the game or your other apps. Use the buttons in the sidebar, or
right-click the app's taskbar icon. The docked panel (`dock.py`) shows your
latest match round by round (⚔ attack, ♜ defense, won or lost), your K-D-A, EPS,
KOST and operators, and your last five results, from the matches you've added.
About 15 seconds after the game saves a match it says **New match: not in your
stats yet**, so you can add it from the full window. See [desktop/README.md](../desktop/README.md).

The **Use demo match** toggle loads a built-in
sample match, so you can try the dashboard without a replay. The **Get the
Windows app** page has the download button and install steps.

The other pages work from every match you've added, not just the one on the
Dashboard:

- **Ask**: type a question, or tap one of the examples (see below).
- **Charts**: any chart of any stat (see below).
- **The assistant**: the **Assistant** button in the bottom-right corner of every
  page opens a chat (see below). Turn it off with the **Assistant** switch in the
  sidebar.
- **Match History**: every match you've added, newest first, with your record,
  K/D and EPS, and who's **Tracked** in each. Select a match to open its
  scoreboards on the Dashboard, to remove it from your stats, or to write about
  it: a **tag** (Official, Scrim, Practice or Casual), a note and a link to its
  video. Once a match is tagged, **Show** lists just one tag's matches, and Ask
  understands the tags ("my K/D in scrims"). Notes stay when a match is added
  again and go when it's removed.
- **Team Analytics > Build a team**: up to five players, filled in with you and
  the four teammates you play with most. Every match where enough of them (you
  choose how many) were tracked on the same side is found. Teams are saved: pick one
  again from the **Team** list, and Ask knows its name ("How is Varsity
  doing?"). You see:
  - **the team's own stats**, never its players' numbers added together:
    - maps played, map record, and rounds won and lost. A round counts once for
      the whole team, even if a player disconnected during it;
    - **man down**: how often the team went two or more players down, and of
      those rounds, how often it got back to even numbers (win or lose) and
      how often it won;
    - on **attack**, how often it plants the defuser and wins once it's down;
    - on **defense**, how often it stops the plant and wins after a plant;
  - **each player's own stats** from playing for this team, with their EPS for
    this team and their **all-time EPS** over every match they've played.
- **Team Analytics > Season teams**: the season tracker's teams, from the
  matches saved to a season, either with the Dashboard's **Also save it to the
  season tracker** or picked here. Only the players picked for each match when
  it was added are saved, never another tracked player who happened to be in
  it, and duplicate rounds are ignored. Each season
  team gets the same team stats, from the matches saved to the season, and
  each player's EPS is recalculated from those matches, alongside their
  all-time EPS. Under **Choose … matches**, tick the matches that count toward
  the season (your Game Day matches) and untick practice. A ticked match is
  read from its replay and saved; an unticked one is taken out and the
  season's totals are worked out again without it. Teams you've tracked show
  up here before any of their matches are saved.
- **Team Analytics > Map pool**: how you, or a team you built, do on each map:
  maps played and record, round win % overall and on attack and defense, and
  the bomb sites won most and least often on each side (a site needs 3+ rounds
  on a side to be called best or worst). Its strongest and weakest maps (2+ maps
  played) help with a veto, and **Sites on** breaks one map down site by site.
- **Operator Analytics**: each operator's rounds, round win rate, K/D, KPR and
  headshot rate over **All matches** (for any player), or picks and site win
  rates for every player in **This match** (the Dashboard's match, or your
  newest one).

### Your stats database and Ask

Your stats are a local SQLite database of the matches you've confirmed on the
Dashboard, with only the players you picked in each. Nothing is added on its
own, so a stranger who was on your team in Ranked, or a sub in a scrim, never
counts toward your team. Each player's EPS is still worked out against everyone
in the match. Matches stay in the database after the game deletes old replays,
and confirming a match again replaces it, so nothing is counted twice. Every
number comes from the same code as the scoreboards. A recording with no players
(a match you left during loading) can't be added, and when two folders hold the
same match the fuller one is kept.

Earlier versions added every replay on their own, with every player in it.
After updating, those matches don't count until you check them: **Match
History > Check earlier matches** lets you keep just the players you track in
each one, keep only your own stats in all of them at once, or remove them.
Matches you'd saved to a season stay, with the players saved from them.

**Ask** turns a plain-English question into a database query and answers with
a sentence, a table (downloadable as CSV) and, for trends and rankings, a chart.
It understands:

- **stats**: K/D, kills, deaths, assists, EPS, KPR, headshot %, KOST, survival,
  entry kills and deaths, clutches, multikills, aces, trades, plants, defuses,
  win rate, wins and losses;
- **who**: you (the player who recorded the replays), any player by name or
  part of it ("Paltry" for Paltry.FBRD; typos are forgiven), your teammates,
  your opponents, everyone, or a team you built;
- **filters**: a map, an operator ("as Ash"), attack or defense, Ranked,
  Unranked, Quick Match or custom games, a Match History tag ("in scrims",
  "in official matches"), wins or losses, dates ("this week",
  "since Sep 20", "in September", "yesterday"), your last or first N matches,
  and conditions ("matches where I had at least 3 kills");
- **answers**: a single number, rankings ("top 5 players by EPS", "best map",
  "where do I rank"), trends ("over time", "per week"), comparisons ("me vs
  Talus", "attack vs defense", "ranked vs custom", "this week vs last week"),
  your best single matches ("most kills in a match"), summaries ("how am I
  doing") and match lists ("my last 5 matches").

It always says how it read the question, and when it guesses (a misspelled
name, or a name that's both a player and an operator) it says so. It works with
its own word lists and rules: no AI model, and no internet connection.

Ask also answers what isn't a stats query: an **analysis** ("analyze my stats",
"analyze Paltry", "analyze my team", "analyze my last match", "what should I
work on?"), what a stat means ("what is KOST?"), how to use the app ("how do I
add my matches?"), streaks, when you last played and who you win most with.
Anything it can't read still gets an answer: what it could work out, and the
closest questions it can answer.

**Report**: when Ask or the assistant can't answer a question, or answers it the
wrong way, press **Report** (or **Not what I asked**) and say what you expected.
The report is saved on this computer (`question-reports.jsonl` in the app's data
folder; only for the session on the public website) and listed under **Reported
questions** on Ask. **Send** opens a GitHub issue filled in with it, labeled
`ask-report`, which you see and can edit before sending; no stats are included.
Developers can list a PC's reports with `python scripts/question_reports.py`.

**Charts** draws any stat any way. Type what you want to see, or pick it from the
menus (a request fills them in, so it can be adjusted):

- **line** and **area** for progress: "my K/D over time vs Sacri", "headshot rate
  per week", "rolling average of my EPS", "running total of my kills";
- **bar**, **horizontal bar** and **stacked bar** for comparisons: "win rate by map",
  "top 5 players by EPS", "stacked bar of rounds won by map and side";
- **pie** for shares: "pie of the operators I play";
- **scatter** for two stats: "scatter K/D vs KOST for every player" (or operators, maps);
- **heatmap** for two groupings: "heatmap of win rate by map and side";
- **histogram** and **box plot** for how a stat is spread: "kills histogram", "box
  plot of kills by side".

Every stat Ask knows, for you, any players, saved teams, everyone, your teammates or
your opponents (each player or team its own line or bar), filtered by map, side,
operator, match type, tag, dates or your last N matches. **The numbers** shows the
table (and a CSV); **Keep this chart** keeps it in a list for the session. Ask and
the assistant draw charts too ("chart my K/D over time"), with a button to change
it on Charts. The numbers come from the same queries as Ask (`chart_engine.py`),
drawn with Altair (`chart_view.py`).

**The assistant** is a chat in the corner of every page that answers everything
Ask does, plus small talk and follow-ups: it remembers the conversation, so
"what about on defense?", "and Sacri?", "how does he do on Bank?", "why?" and
"kills instead" work after a question. It suggests what to ask next, starts with
questions about the page you're on, and links to the right tab. It's a Streamlit
fragment, so chatting redraws only the panel, and the conversation stays when you
change tabs. The Windows app remembers whether it's switched on. Like Ask, it runs
on your computer with no AI model or online service (`assistant.py`,
`assistant_knowledge.py`, `assistant_ui.py`).

### Leagues and tournaments

**League & Team** is where you say where your team competes, from three places:

- **Liquipedia events, S- to D-Tier**: every Rainbow Six event on
  [Liquipedia](https://liquipedia.net/rainbowsix) that's on now, coming up, or
  ended in the last six months, from the Majors down to community leagues. Filter
  by tier, region and when, search by event, organizer or team, then pick your
  team from the event's participants (or type it if it isn't listed).
- **NECC**: the Rainbow Six season's Divisions I–VI and the placement event, with
  every team in each, plus NECC's school directory (school colors and logos).
- **My league isn't listed**: name any league or tournament, and your team.

Saving it puts the league on the banner of every page (and, if you tick **Name my
season after it**, makes it the season tracker's season), on this PC in the
Windows app, for the browser session on the public website. **Track your
players** adds your players' usernames to the season tracker under your team.

Event data comes from Liquipedia's MediaWiki API, following its
[terms](https://liquipedia.net/api-terms-of-use): at most one request every
2 seconds over one connection, gzip, a User-Agent naming this project, and
results kept rather than fetched again. Each release ships a snapshot,
`liquipedia_events.json`; refresh it before a release with

```bash
.venv\Scripts\python scripts\events.py         # only new and still-running events (about 10 requests)
.venv\Scripts\python scripts\events.py --full  # every page again (about 30 requests, a few minutes)
```

The Windows app's **Check Liquipedia for new events** does the same at most once
a day and keeps the result next to the season database; the public website only
uses the snapshot, so a shared server never asks Liquipedia on visitors' behalf.
Liquipedia's content is CC-BY-SA 3.0, credited wherever events are shown. The
REST API serves the same list at `GET /api/v1/events` (`tier`, `region`, `q`).

NECC's divisions are `necc_divisions.json`, a dated snapshot of NECC's public
[LeagueOS](https://necc.v1.leagueos.gg/) season pages (LeagueOS has no public
API); update it when a new season's divisions are set.

### NECC school data

League & Team's NECC school directory accepts a JSON catalog upload. A deployment can instead set
`NECC_R6_DATA_URL` to an HTTPS JSON endpoint that returns the same shape. The
catalog uses a top-level `schools` array; each school may contain `logo_url`,
`primary_color`, and `teams`. R6 team entries may contain `name`, `game`,
`roster` (strings or `{ "name": "..." }` objects), `standings`, and `matches`.
Other explicitly named games are filtered out. Selecting a school updates the
dashboard accent and lets you add its roster to the season tracker.

No NECC endpoint or API credentials are bundled: the official sites did not
provide a usable public feed from this environment. Until a supported endpoint
is configured, import a catalog exported from an authorized NECC source.

## Command line

```
python match_stats.py SOURCE [--csv stats.csv] [--json stats.json] [--txt stats.txt]
```

`SOURCE` is a `.zip`, a match folder, a single `.rec`, or a folder of many
matches (each one gets its own scoreboard; up to four are parsed at once).
`--csv` writes the numeric stats (one row per player per match), `--json`
writes the scoreboards, and `--txt` writes the scoreboards as plain text,
exactly as printed. Use any of them together, or none to just print.

## Stat definitions

| Column | Meaning |
|---|---|
| EPS | Performance score, where 100 is the average player in this match. Ubisoft hasn't published its EPS formula, so this one is built from the same kind of inputs: kills and deaths per round, KOST, entry differential, multikills, clutches, objectives and trade kills, each compared against the other players in the match. |
| KD (+/-) | kills-deaths (difference) |
| Entry | opening kills-opening deaths. The opening duel is the round's first death. |
| KOST | % of rounds with a **K**ill, **O**bjective, **S**urvival or **T**raded death |
| KPR | kills per round |
| HS | headshot kills / kills |
| SRV | % of rounds survived |
| Clutches | rounds won as the team's last player alive against 1+ enemies |
| Multikills | rounds with 2+ kills |
| Objectives | defuser plants + defuser disables |
| Dead for trade kill | this player's deaths that a teammate avenged within 10 s |
| Trade kills | kills on an enemy who had killed a teammate within the previous 10 s |

Team kills never count as kills, but the victim still gets a death.

### Known limitations

- **Objectives:** replays from the current game version (Y11S3) don't say
  *who* planted or disabled the defuser. The parser works it out from which
  player put their weapon away when the plant or disable started (see
  `dissect/defuse.go`). Across 22 test matches this named a player on the
  correct side for every plant and disable. If it can't tell, a plant is
  credited only when a single player on that side was alive.
- **EPS** is a close stand-in, not Ubisoft's exact number.
## Publishing

### The website (Streamlit Community Cloud, free)

Streamlit Community Cloud only deploys from a GitHub repo you're an admin of.
If you aren't one, fork this repo first: the fork is yours, and the website's
download button automatically points at the fork's releases.

1. On GitHub, fork this repository to your account.
2. Go to <https://share.streamlit.io>, sign in with GitHub, and choose
   **Create app** > **Deploy a public app from GitHub**.
3. Fill in: **Repository** `<you>/r6-dissect`, **Branch** `main`, **Main file
   path** `scripts/app.py`. Optionally pick a custom **App URL**.
4. Under **Advanced settings**, choose Python 3.12 (what the tests run on), then
   click **Deploy**.

It uses the Linux `r6-dissect` binary committed at the repo root, so rebuild
and commit it (`GOOS=linux GOARCH=amd64 go build -trimpath -o r6-dissect .`;
`-trimpath` keeps your PC's folder paths out of it) after changing the Go
parser. Every push to the branch redeploys the site.

### A demo of the whole app to share

**Try the demo season** in the sidebar (or `?demo` at the end of the app's
address) fills every page with a made-up season: twelve league matches of a
college team, Lakeside Varsity, across the map pool (`demo_season.py`, the same
every time). Ask, Match History, Team Analytics and Operator Analytics then have
a season to show, and the Dashboard opens any of its matches. It goes into a
stats database of its own for that browser session, so nobody's real stats are
touched, and the season tracker stays off while it's on. **Leave the demo
season** goes back to your own.

To share the whole app, deploy the website as above and send its address with
`/?demo` at the end, for example `https://<your-app>.streamlit.app/?demo`:
each visitor gets their own copy of the demo season. (Link to the root address:
the first visit after the site wakes up always opens the Dashboard.) The browser
edition ([browser/](../browser/README.md)) is the lighter option: the scoreboards
of any replay, with no server at all.

### The Windows app (GitHub Releases)

The **Windows app** workflow (`.github/workflows/windows-app.yaml`) builds the
installer (`R6MatchStats-Setup.exe`) and a portable zip, runs the tests,
scans the build with Microsoft Defender, installs and opens the app on a
Windows machine to check it works, and attaches both files plus their
checksums (`SHA256SUMS.txt`) to every published release. The website's download button points at the newest
release that has the installer. Until there is one, the page says the app
hasn't been published yet.

1. On a fork, open the **Actions** tab once and enable workflows.
2. On GitHub, go to **Releases** > **Draft a new release**, create a tag like
   `v1.1.0` (the app takes its version from the tag), and click **Publish
   release**. About ten minutes later the installer appears on the release.

The installed app's **Get the Windows app** page tells users when a newer
release is out, and running the new installer updates the app in place.

To build it on your own PC instead, run
`powershell -ExecutionPolicy Bypass -File desktop\build.ps1`, then drag
`dist\R6MatchStats-Setup.exe`, `dist\R6MatchStats-Windows.zip` and
`dist\SHA256SUMS.txt` onto a release. See [desktop/README.md](../desktop/README.md).

## Safety

- **Only replays get through.** `file_guard.py`'s `ReplayScanner` checks every
  file before the parser sees it, whether it's uploaded, in a zip or in a
  folder. A file must be named `.rec`, be a plausible size for one round, and
  start with the bytes every Siege replay starts with. Anything else, like a
  renamed program, is skipped and listed on the page, and never even written
  to disk. Zips and uploads are also capped in file count and total size, and
  a zip's folder names can't write outside its temporary folder.
- **The Windows app checks itself.** The build records every file's SHA-256
  (`desktop/integrity.py`), and each time the app opens it refuses to run if
  any file was changed, added (say, a planted DLL) or removed. It can't catch
  someone replacing `R6MatchStats.exe` itself or a DLL Windows loads before
  the app's code starts. For that, compare the download with the published
  checksum (the download page shows it) and only download from the official
  release.
- **Builds are scanned.** The release workflow scans each build with Microsoft
  Defender and won't publish one it flags. A release is only published once
  the scan has finished clean: if Defender isn't running or the scan doesn't
  finish, the release stops there (other builds just warn).
- **Releases are code-signed** once the repository has a signing certificate
  (see [desktop/README.md](../desktop/README.md#code-signing)). The download
  page then names the publisher and gives the certificate's thumbprint.
  Unsigned releases make Windows SmartScreen warn about the app.
- **Replays are temporary; stats stay on your computer.** The Windows app reads
  replays in place and never writes to the game's folders. Uploads are deleted
  after an hour unused. The stats database (the numbers and names of the
  players you picked in each match you added, and your saved teams) and the season tracker's totals are stored in
  SQLite: `%LOCALAPPDATA%\R6MatchStats\season_stats.db` in the Windows app, or
  `data/season_stats.db` in a source checkout. Delete that file to remove
  everything the app has saved. On the public website each visitor gets their
  own stats database, deleted with their uploads after an hour unused, and
  season tracking is turned off so visitors' stats are never mixed.

## Ubisoft's terms

This tool is built to stay clear of the Rainbow Six Siege EULA and Ubisoft's
Terms of Use, but it isn't approved by Ubisoft, and only Ubisoft can say for
certain what it allows:

- **Not a cheat or a game tool** (EULA 1.2(iii), EULA 4, Terms 7.3.4): it never
  touches the game while it runs. It doesn't read the game's memory, inject
  anything, automate input or change game files. It only reads replay files the
  game already saved, after the match, so it gives no advantage in a match.
- **Nothing online** (Terms 7.3.5, 7.3.6): it never connects to the game,
  Ubisoft's servers or anyone's Ubisoft account, and it doesn't scrape or
  collect data. Each person looks at their own replays.
- **Non-commercial** (EULA 1.1, 1.2(i), Terms 1.3): it's free, with no ads or paid
  features. Keep it that way.
- **No implied endorsement** (EULA 1.3.j) and **Ubisoft's IP** (EULA 2, Terms 9):
  the app, the installer and this README say it's unofficial and not endorsed.
  It uses no Ubisoft logos or artwork (the icon is original). It uses
  Siege's names, such as maps, operators and "R6", only to describe the game's
  data.
- **The grey area: reverse engineering** (EULA 1.2(ii)). Reading the `.rec`
  replay format relies on community reverse-engineering of that format
  ([r6-dissect](https://github.com/redraskal/r6-dissect)), which the EULA's
  no-reverse-engineering clause could be read to cover. That the format has
  been publicly documented for years isn't permission. If you want certainty,
  ask Ubisoft before publishing widely.
- **Don't feed replays to AI tools** (Terms 1.3): the Terms forbid using game
  content as input to AI tools. The app doesn't: **Ask** and the assistant read
  questions with their own rules and word lists, on your computer, with no AI
  model or online service. Keep that in mind when asking an assistant for help with replay data.

## Architecture

```
.zip / folder / .rec
   │  file_guard.ReplayScanner                    (only real replays get through)
   │  parser.collect_rec_files + group_by_match   (unzip, split into matches)
   ▼
r6-dissect (Go CLI, repo root)  →  JSON per round
   │  parser.normalize_from_r6_dissect             (stable internal schema)
   ▼
metrics_engine.compute_match_metrics  →  PlayerStats per player
   │  metrics_engine.pro_league_rows               (the 12 display columns)
   ├──────────►  stats_db.StatsDB  (SQLite: every match, player and round)
   │                │  ask_engine.ask  (plain English → SQL → answer)
   ▼                ▼
app.py → report.py (Dashboard), ask.py, history.py, teams.py, operators.py,
  (Streamlit)  league.py, download.py      /  match_stats.py (CLI)
```

`app.py` is the entry point: it sets up the page's styling, header and
navigation. `sources.py` is where every page gets its replays: the source
picked on the Dashboard (or, on your PC, the game's `MatchReplay` folder), each
match parsed at most once per session. `stats_db.py` is the database, which
only takes a match when it's confirmed on the Dashboard.

The tabs are one program on one shared session:

- **`session.Session`** owns what they share: the season, your league and
  team, the demo season, the stats database, the match another tab asked the
  Dashboard to open, and the notes a button leaves for its tab's next run. It
  keeps them in `st.session_state` under the same keys, and only it spells
  them out.
- **`sources.ReplaySource`** is where matches come from: `FolderSource`,
  `UploadSource` and `DemoSource` answer the same questions (`matches()`,
  `open()`, `has()`, how the Dashboard names them, whether they can be added
  to your stats). The Dashboard, Match History, Operator Analytics, Team
  Analytics and League & Team use any of them the same way.
- **`tab.Tab`** is each tab's base class. A tab overrides `render()` (and
  `heading()` if it needs to) and gets the session, the stats database, the
  "no matches yet" state, the season tracker's on/off note, notes across a
  rerun, and links to the other tabs. Each page file ends with
  `SomeTab().run()`, so `st.navigation` runs the files as before.
`ask_engine.py` reads a question into a query (whose stats, which ones, which
filters, what kind of answer), runs it with every value passed as a query
parameter, and writes the answer; it also works out Build a team and the
all-matches operator table. `app_info.py` holds the app name, version and
download links, and tells whether it's running as the public website, the
Windows app (`desktop/launcher.py`), or from a source checkout.

The React and Unity clients use the shared FastAPI service in `api.py`. Start it
from the repository root:

```bash
pip install -r scripts/requirements-api.txt   # FastAPI, Uvicorn; the Streamlit app doesn't need them
PYTHONPATH=scripts uvicorn api:app --app-dir scripts --host 127.0.0.1 --port 8000
```

The service exposes `GET /api/v1/health`, season summaries and match history,
roster tracking, replay upload/path parsing, match logging, and school catalog
read/import routes. The API and existing Streamlit tracker use the same SQLite
database. Set `R6_STATS_DB` to choose its path; on Windows set `R6_DESKTOP=1` to
use `%LOCALAPPDATA%\R6MatchStats\season_stats.db`. It binds to loopback by
default and has no authentication, so do not expose it to an untrusted network.

`parser.py` finds the r6-dissect binary via `$R6_DISSECT_BIN`, then `PATH`,
then `r6-dissect.exe` (Windows) or `r6-dissect` at the repo root, then
`~/go/bin`. If a future r6-dissect release renames JSON fields, only
`normalize_from_r6_dissect` needs updating.

**Season-long stats:** `season_stats.py` (`StatsManager`) logs tracked
players' rounds into SQLite across a season without double-counting. The
Season tracker, Team Analytics' Season teams tab and League & Team use this
data. See
[SEASON_STATS.md](SEASON_STATS.md) and `example_season_stats.py`.

## Tests

```bash
python scripts/run_tests.py              # every app test, on all your cores (the same tests as unittest discover)
python scripts/run_tests.py --lint --coverage   # what every pull request runs (pip install -r scripts/requirements-dev.txt)
python scripts/run_tests.py desktop      # the Windows app's launcher and integrity check
go test ./...                            # the Go replay parser
```

What they cover:

- **Every tab in every situation** (`test_pages.py`): a fresh install of the Windows app, the public
  website, the demo season, a season with replays and teams (a one-player team, a team without matches,
  names with quotes and emoji), the match-day panel hidden, and unreadable planning data. Every tab must
  open without an error, then every choice in every picker, every checkbox, toggle and button is tried.
  The tabs come from `app.py`'s navigation, so a new tab is tested without adding it here.
- **Odd inputs** (`test_robustness.py`): replays with unusual shapes (one round, no kills, a player who
  left, suicides, overtime, a 1v1, missing fields, 150 random matches) through the scoreboard, the stats
  database, the season tracker, Ask and the docked view; hundreds of questions to Ask; damaged settings,
  catalogs, logos and match-day backups.
- **The structure** (`test_structure.py`): every page is one `Tab` subclass listed in `tab.PAGES` and ends
  by running it; only `Session` (and `sources.py` and `branding.py` under it) touches `st.session_state`;
  tabs never import each other and link through `Tab.go_to`; every `ReplaySource` kind passes the same
  contract test; every module ships in the Windows app. Each failure says what to change.
- **Coverage**: `--coverage` fails if the tests cover less than 93% of the app or 75% of any one module.

## Troubleshooting

- **"r6-dissect not found"**: build it at the repo root (step 1), or set
  `R6_DISSECT_BIN` to its full path.
- **Every stat is 0 / "no player data"**: expand **Parser debug info** in the
  sidebar to see what r6-dissect returned. Practice sessions and matches that
  ended during prep have no kill feed.
- **A map shows as `Map(123...)`**: the map is newer than this r6-dissect
  build; add its ID to `dissect/header.go` and re-run `stringer -type=Map`.


### Full-width match-day workspace (1.6.3)

The app uses the available window width. Its companion rail shows manually entered upcoming fixtures, recent confirmed results, roster availability and preparation notes. Open **Match day** to manage fixtures and time-zone labels, record availability, edit notes, export or restore planning JSON, or turn the rail off for a full-width statistics-only view. Smaller windows stack the panels below the main content.

No match schedule or availability is inferred from replay files. The demo season has clearly labeled example fixtures and availability, isolated from real planning data. The Windows app saves planning data in `%LOCALAPPDATA%/R6MatchStats/matchday.json`; the hosted app keeps it in the current private session, so export before leaving. Existing tabs, detailed team tables, selected-player imports, seasons, independent team deletion, replay sources and docking retain the shared Session/Tab structure.
