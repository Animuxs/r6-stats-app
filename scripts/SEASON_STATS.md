# Season Stats Logger

`season_stats.py` keeps season-long stats for a list of **tracked players**.
It stores them in a SQLite file, adds each match's rounds to every tracked
player's totals, and never counts the same round twice.

```
match folder / .rec files
   │  parser.parse_match()                     r6-dissect CLI → normalized MatchData
   ▼
MatchData ── metrics_engine.compute_match_metrics()  per-player, per-round results
   │                                                  (kills, entries, trades, clutches, KOST …)
   ▼
StatsManager.log_match()                        adds tracked players' rounds to the season totals
   │
   ▼
data/season_stats.db  (SQLite; Windows app uses LocalAppData)  →  get_player_stats / get_team_stats / export_json
```

Files:

| File | What it is |
|---|---|
| `season_stats.py` | `StatsManager`, the data models, updates and storage |
| `example_season_stats.py` | Example usage that also works as a command-line tool |
| `test_season_stats.py` | Tests (`python -m unittest test_season_stats -v`, run from `scripts/`) |

No extra dependencies: it uses only Python's built-in `sqlite3`.

---

## Quick start

```python
from parser import parse_match
from season_stats import StatsManager

with StatsManager(season="Y10S3") as sm:          # opens/creates data/season_stats.db
    sm.add_player("Typhoon.FBRD", team="FBRD")     # only tracked players are saved
    sm.add_player("Paltry.FBRD",  team="FBRD")

    match, raw, warnings = parse_match(["replays/Match-…/Match-…-R01.rec",
                                        "replays/Match-…/Match-…-R02.rec"])  # every round file
    result = sm.log_match(match)
    print(result.rounds_logged, result.rounds_skipped_duplicate, result.warnings)

    sm.get_player_stats("Typhoon.FBRD").to_dict()
    sm.get_team_stats("FBRD").to_dict()
    sm.export_json("season_Y10S3.json")
```

From the command line (run from the repo root):

```bash
# try it on the bundled demo match (in-memory, writes nothing)
python scripts/example_season_stats.py --demo

# track players and log a match folder into the season DB
python scripts/example_season_stats.py --season Y10S3 \
    --track "Typhoon.FBRD,Paltry.FBRD,Sircat.FRBD" --team FBRD \
    replays/Match-2026-09-23_21-30-30-23660/

# print + export the season
python scripts/example_season_stats.py --season Y10S3 --export season_Y10S3.json
```

`example_season_stats.py` accepts match **folders** or `.rec` files. Unzip
`.zip` downloads first.

---

## Integrating with r6-dissect

### 1. Get normalized match data

Use `parser.py`. Don't read r6-dissect's JSON directly:

| You have | Call | Notes |
|---|---|---|
| A whole match (folder of `R01.rec`, `R02.rec` …) | `parse_match(list_of_rec_paths)` | One r6-dissect run over the folder. If one round file is broken, the other rounds are still parsed and the broken one is listed in `warnings`. |
| A single round file | `parse_replay(path)` | Gives a one-round MatchData. |
| Raw r6-dissect JSON you already have | `normalize_from_r6_dissect(raw)` | Handles both the single-round and the `{"rounds": [...]}` folder shape. |

All three produce the same `MatchData` shape (documented in `parser.py`),
which is what `StatsManager.log_match()` takes.

### 2. Log it

`log_match(match)` runs `metrics_engine.compute_match_metrics()`, so the
season stats use exactly the same definitions as the dashboard. It then
adds up each tracked player's rounds:

| Stat | Source |
|---|---|
| kills, deaths, headshots | the round's kill feed (team kills give the victim a death but no kill to the killer) |
| assists | r6-dissect's per-round `stats` scoreboard (the kill feed has no assists) |
| entry kill / entry death | the round's first death: the victim gets the entry death, their killer the entry kill |
| trade | the player's killer is killed by a teammate within 10 s |
| clutch won / attempted (1v1–1v5) | a player left as their team's last alive against 1+ enemies; won if their team wins the round |
| objective | plant (`DefuserPlantComplete`) or defuse (`DefuserDisableComplete`), credited to the player r6-dissect names (if it can't tell, to the only player on that side still alive) |
| survived | not dead at round end |

### 3. The match ID must be unique

Rounds are de-duplicated on **(season, match_id, round_num, username)**.
`match_id` comes from r6-dissect's `matchID`, which is the same for every
round of a match, so all of these are safe:

- logging the same replay twice → skipped the second time
- logging round files one by one, then the whole folder → only the new rounds count
- adding a tracked player later and re-logging old matches → only that player's rounds are added (backfill)

If a match has no `matchID`, `log_match` raises `StatsError`. Pass a stable
ID yourself with `log_match(match, match_id="…")`.

### 4. Pin team names

r6-dissect names teams **relative to whoever recorded the replay**: usually
`"YOUR TEAM"` / `"ENEMY TEAM"`. Across a season, those names would merge
every opponent into one team, so they are **ignored**. Give players a team
when you track them:

```python
sm.add_player("Typhoon.FBRD", team="FBRD")
```

If a match does carry real team names (e.g. custom matches named in-game),
players with no pinned team take the name from the match. When a logged
player ends up with no team at all, `LogResult.warnings` says so.

### 5. Use the dashboard tracker

After loading a replay, go to **Add to your stats** below the scoreboards. Pick
the players to track from that match, tick **Also save it to the season
tracker**, enter the season and a team or school name for each team, then
confirm. Only the players picked are logged (`log_match(match, players=...)`),
never another tracked player who was in the match. Team Analytics' Season teams
tab shows saved totals; saving a match again skips rounds already recorded for
the season.

---

## API reference

### `StatsManager(db_path=data/season_stats.db, season="current")`
Opens or creates the database. Use `":memory:"` for a temporary database.
Every read, write and reset applies to `season`. Use one manager per
season, or several on the same file.

**Tracked players**
- `add_player(username, team=None)` / `add_players(usernames, team=None)`: tracks a player. Safe to call again; calling it again updates the pinned team. Usernames match case-insensitively.
- `remove_player(username, delete_stats=False)`: stops tracking. Existing stats are kept unless `delete_stats=True`, which deletes them from every season.
- `tracked_players() -> {username: team_or_None}`

**Logging**
- `log_match(match, match_id=None, players=None) -> LogResult`: logs every round of a normalized match in one transaction. `players` limits it to the players confirmed for this match (any case); `None` logs every tracked player in it.
- `log_round(match_id, round_num, {username: RoundResult}, {username: team}=None) -> LogResult`: lower-level entry point for per-round data you've computed yourself.
- `is_round_logged(match_id, round_num, username=None) -> bool`
- `rebuild_totals()`: recomputes this season's totals from the per-round log.

**Reading**
- `get_player_stats(username) -> PlayerSeasonStats | None`
- `all_player_stats() -> list[PlayerSeasonStats]`
- `get_team_stats(team) -> TeamSeasonStats | None`
- `teams() -> list[str]`, `seasons() -> list[str]`

**Export / reset**
- `export_json(path=None) -> dict`: season snapshot: tracked players, per-player and per-team stats. Written to `path` if given.
- `reset_season(season=None)`: deletes a season's totals and round log. The tracked list is kept.

### Models

`RoundResult`: one player in one round, i.e. what gets logged:
`kills, deaths, assists, headshots, entry_kill, entry_death, traded, planted,
defused, survived, clutch_won (1–5|None), clutch_attempt (1–5|None)`.
`RoundResult.from_breakdown(rb)` builds one from a `metrics_engine.RoundBreakdown`.

`PlayerSeasonStats`: `totals` (every counter below), plus these calculated stats:

| Field | Formula |
|---|---|
| `kd` | kills ÷ deaths (= kills when deaths is 0) |
| `kost_pct` | 100 × kost_rounds ÷ rounds_played |
| `entry_diff` | entry_kills − entry_deaths |
| `hs_pct` | 100 × headshots ÷ kills |
| `clutches_won`, `clutch_attempts`, `clutches` | sums, plus a `{"1v1": n, …}` breakdown |

`TeamSeasonStats` (players grouped by team name):

| Field | Formula |
|---|---|
| `kd` | Σkills ÷ Σdeaths |
| `entry_diff` | Σentry_kills − Σentry_deaths |
| `clutch_success_rate` | Σclutches won ÷ Σclutch attempts (`None` if no attempts) |
| `kost_avg` | average of the members' season KOST% |
| `totals`, `players` | summed counters, member usernames |

### Stored counters (`player_totals`)

`rounds_played, kills, deaths, assists, headshots, entry_kills, entry_deaths,
trades, plants, defuses, kost_rounds, kost_kill, kost_objective,
kost_survive, kost_traded, clutch_1v1 … clutch_1v5,
clutch_attempts_1v1 … clutch_attempts_1v5`

`kost_rounds` counts rounds that had **any** of the four KOST events. The
four `kost_*` counters count each event separately, so one round can add
to several of them. For example, a kill and survival adds 1 to
`kost_kill` and 1 to `kost_survive`, but only 1 to `kost_rounds`.

---

## Database layout

| Table / view | Key | Purpose |
|---|---|---|
| `tracked_players` | `username` (case-insensitive) | Who gets recorded, and their optional pinned team |
| `player_totals` | `(season, username)` | Running totals, updated in the same transaction as the round log |
| `logged_rounds` | `(season, match_id, round_num, username)` | One row per counted round. The key is what prevents double counting. The row's `result` JSON can rebuild the totals |
| `player_season_stats` (view) | — | `player_totals` plus `kd`, `kost_pct`, `entry_diff`, `hs_pct`, `clutches_won`, `clutch_attempts` |

Query it directly with any SQLite tool, e.g.:

```bash
sqlite3 data/season_stats.db \
  "SELECT username, team, rounds_played, kills, round(kd,2), round(kost_pct,1)
   FROM player_season_stats WHERE season='Y10S3' ORDER BY kd DESC"
```

`data/` is git-ignored. Back up `data/season_stats.db` to keep a season.
