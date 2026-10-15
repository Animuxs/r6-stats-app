import { lazy, Suspense, useEffect, useRef, useState, type CSSProperties } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Activity, ArrowDownToLine, ArrowUpRight, Check, ChevronDown, Crosshair,
  Database, FileUp, History, LayoutDashboard, LoaderCircle, RefreshCw,
  School as SchoolIcon, Shield, UsersRound, Wifi, WifiOff,
} from "lucide-react";
import { api, type MatchHistoryItem, type NormalizedMatch, type School, type SeasonSummary } from "./api";
import { useClientStore, type ModuleName } from "./store";

const SceneHeader = lazy(() => import("./TacticalScene"));

const modules: { label: ModuleName; icon: typeof LayoutDashboard }[] = [
  { label: "Dashboard", icon: LayoutDashboard },
  { label: "Match History", icon: History },
  { label: "Operator Analytics", icon: Crosshair },
  { label: "Team Analytics", icon: UsersRound },
  { label: "School Selection", icon: SchoolIcon },
];

function Kpi({ label, value, hint }: { label: string; value: string | number; hint: string }) {
  return <div className="kpi"><span>{label}</span><strong>{value}</strong><small>{hint}</small></div>;
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return <div className="empty-state"><Database size={18} /><strong>{title}</strong><span>{detail}</span></div>;
}

function Overview({ summary, matches, onExport }: { summary: SeasonSummary | null; matches: MatchHistoryItem[]; onExport: () => void }) {
  const leaders = [...(summary?.players || [])].sort((left, right) => (right.kd || 0) - (left.kd || 0)).slice(0, 5);
  const topPlayer = leaders[0];
  const teamRows = [...(summary?.teams || [])].sort((left, right) => (right.totals?.kills || 0) - (left.totals?.kills || 0));
  return (
    <div className="module-view">
      <div className="module-heading">
        <div><p className="eyebrow">Season intelligence</p><h2>Dashboard</h2></div>
        <button className="button button-quiet" onClick={onExport} disabled={!summary}><ArrowDownToLine size={15} /> Export snapshot</button>
      </div>
      <div className="kpi-grid">
        <Kpi label="Rounds logged" value={summary?.rounds_logged ?? 0} hint="DE-DUPLICATED" />
        <Kpi label="Tracked players" value={summary?.players.length ?? 0} hint="ACTIVE ROSTER" />
        <Kpi label="Teams" value={summary?.teams.length ?? 0} hint="SEASON GROUPS" />
        <Kpi label="Recent matches" value={matches.length} hint="SAVED TO LOCAL DB" />
      </div>
      <div className="dashboard-columns">
        <section className="surface-panel overview-panel">
          <div className="panel-heading"><div><p className="eyebrow">Roster pulse</p><h3>Top players</h3></div><span className="panel-index">01 / 05</span></div>
          {leaders.length ? <div className="leader-list">{leaders.map((player, index) => (
            <div className="leader-row" key={player.username}>
              <span className="rank">0{index + 1}</span><span className="leader-name">{player.username}<small>{player.team || "UNASSIGNED"}</small></span>
              <span className="leader-stat">{(player.kd || 0).toFixed(2)}<small>K/D</small></span>
              <span className="leader-stat">{(player.kost_pct || 0).toFixed(0)}%<small>KOST</small></span>
            </div>
          ))}</div> : <EmptyState title="No season stats yet" detail="Parse a replay and log a roster to populate this board." />}
        </section>
        <section className="surface-panel team-pulse">
          <div className="panel-heading"><div><p className="eyebrow">Squad performance</p><h3>Team pulse</h3></div><UsersRound size={17} /></div>
          {teamRows.length ? teamRows.slice(0, 4).map((team, index) => {
            const maxKills = Math.max(...teamRows.map((row) => row.totals?.kills || 0), 1);
            return <div className="team-pulse-row" key={team.team}>
              <div><span className="team-pulse-rank">0{index + 1}</span><strong>{team.team}</strong><small>{team.players.length} PLAYERS · {team.totals?.rounds_played || 0} ROUNDS</small></div>
              <div className="bar-track"><i style={{ width: `${Math.min(100, (team.totals?.kills || 0) / maxKills * 100)}%` }} /></div>
              <b>{team.totals?.kills || 0}<small>K</small></b>
            </div>;
          }) : <EmptyState title="No teams recorded" detail="Team totals appear after you save a match." />}
          {topPlayer && <div className="feature-line"><Activity size={14} /><span>Current K/D leader</span><strong>{topPlayer.username}</strong><ArrowUpRight size={14} /></div>}
        </section>
      </div>
    </div>
  );
}

function ReplayIntake({
  match, selectedPlayers, busy, message, onFiles, onTogglePlayer, onSave,
}: {
  match: NormalizedMatch | null;
  selectedPlayers: string[];
  busy: boolean;
  message: string;
  onFiles: (files: File[]) => void;
  onTogglePlayer: (player: string) => void;
  onSave: () => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const grouped = new Map<number, string[]>();
  match?.players.forEach((player) => {
    if (!selectedPlayers.includes(player.name)) return;
    grouped.set(player.team, [...(grouped.get(player.team) || []), player.name]);
  });
  return (
    <section className="surface-panel replay-intake">
      <div className="panel-heading"><div><p className="eyebrow">Replay ingest</p><h3>Analyze a match</h3></div><span className="ingest-badge"><span className="live-dot" /> SECURE SCAN</span></div>
      <div className="upload-well" onClick={() => fileRef.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); onFiles(Array.from(event.dataTransfer.files)); }}>
        <input ref={fileRef} type="file" accept=".rec,.zip" multiple hidden onChange={(event) => { if (event.currentTarget.files) onFiles(Array.from(event.currentTarget.files)); event.currentTarget.value = ""; }} />
        <div className="upload-icon"><FileUp size={20} /></div>
        <strong>Drop replay files to begin</strong>
        <span>.REC rounds or a zipped match folder</span>
        <button className="button button-outline" type="button"><FileUp size={14} /> Browse files</button>
      </div>
      {busy && <div className="inline-status"><LoaderCircle className="spin" size={15} /> Parsing replay data with r6-dissect…</div>}
      {message && <div className="inline-status">{message}</div>}
      {match && <div className="parsed-match">
        <div className="parsed-title"><div><p className="eyebrow">Parsed match</p><strong>{match.map}</strong></div><span>{match.final_score?.[0] ?? 0} : {match.final_score?.[1] ?? 0}</span></div>
        <div className="parsed-meta">{match.rounds.length} ROUNDS <i /> {match.players.length} PLAYERS <i /> ID {match.match_id}</div>
        <div className="inline-status">Pick the players to track from this match. Only they are saved.</div>
        <div className="roster-pick-list">{match.players.map((player) => (
          <label className="roster-pick" key={player.name}>
            <input type="checkbox" checked={selectedPlayers.includes(player.name)} onChange={() => onTogglePlayer(player.name)} />
            <span>{player.name}</span><small>{match.team_names[player.team] || `TEAM ${player.team + 1}`}</small>
          </label>
        ))}</div>
        <div className="parsed-footer"><span>{selectedPlayers.length} PLAYERS SELECTED</span><button className="button button-primary" onClick={onSave} disabled={busy || selectedPlayers.length === 0}><Database size={14} /> Save to season</button></div>
      </div>}
    </section>
  );
}

function HistoryModule({ matches }: { matches: MatchHistoryItem[] }) {
  return <div className="module-view">
    <div className="module-heading"><div><p className="eyebrow">Season archive</p><h2>Match History</h2></div><span className="count-label">{matches.length.toString().padStart(2, "0")} RECORDS</span></div>
    {matches.length ? <div className="surface-panel table-wrap"><table><thead><tr><th>Match identifier</th><th>Saved (UTC)</th><th>Rounds</th><th>Players tracked</th><th /></tr></thead><tbody>
      {matches.map((match, index) => <tr key={match.match_id}><td><span className="match-row-index">{String(index + 1).padStart(2, "0")}</span>{match.match_id}</td><td>{match.saved_at.replace("T", " ").replace("+00:00", "")}</td><td>{match.rounds}</td><td>{match.players}</td><td><ArrowUpRight size={14} /></td></tr>)}
    </tbody></table></div> : <EmptyState title="Archive is empty" detail="Saved replay sessions appear here, with round and player counts." />}
  </div>;
}

function getOperatorRows(match: NormalizedMatch | null) {
  const byName = new Map<string, { picks: number; wins: number; kills: number; deaths: number; sites: Map<string, { rounds: number; wins: number }> }>();
  if (!match) return { rows: [], sites: [] };
  const teamByPlayer = new Map(match.players.map((player) => [player.name, player.team]));
  for (const round of match.rounds) {
    for (const [player, operator] of Object.entries(round.operators || {})) {
      const row = byName.get(operator) || { picks: 0, wins: 0, kills: 0, deaths: 0, sites: new Map() };
      row.picks += 1;
      if (round.winner_team !== null && round.winner_team === teamByPlayer.get(player)) row.wins += 1;
      const site = round.site || "Unknown site";
      const siteRow = row.sites.get(site) || { rounds: 0, wins: 0 };
      siteRow.rounds += 1;
      if (round.winner_team !== null && round.winner_team === teamByPlayer.get(player)) siteRow.wins += 1;
      row.sites.set(site, siteRow);
      for (const event of round.events || []) {
        const teamKill = event.target !== undefined && teamByPlayer.get(event.target) === teamByPlayer.get(player);
        if (event.type === "kill" && event.actor === player && !teamKill) row.kills += 1;
        if (event.type === "death" && event.actor === player) row.deaths += 1;
      }
      byName.set(operator, row);
    }
  }
  const totalPicks = [...byName.values()].reduce((total, row) => total + row.picks, 0);
  const rows = [...byName.entries()].sort((left, right) => right[1].picks - left[1].picks).map(([name, row]) => ({
    name, picks: row.picks, pickRate: totalPicks ? row.picks / totalPicks : 0,
    winRate: row.picks ? row.wins / row.picks : 0, kills: row.kills, deaths: row.deaths,
  }));
  const sites = [...byName.entries()].flatMap(([operator, row]) => [...row.sites.entries()].map(([site, stats]) => ({
    operator, site, ...stats, winRate: stats.rounds ? stats.wins / stats.rounds : 0,
  })));
  return { rows, sites };
}

function OperatorModule({ match }: { match: NormalizedMatch | null }) {
  const analytics = getOperatorRows(match);
  return <div className="module-view">
    <div className="module-heading"><div><p className="eyebrow">Selection efficiency</p><h2>Operator Analytics</h2></div><span className="count-label">CURRENT REPLAY</span></div>
    {!match ? <EmptyState title="No replay in this session" detail="Load a recent replay from Dashboard to analyze operator picks." /> : analytics.rows.length ? <>
      <div className="operator-grid">{analytics.rows.map((operator, index) => <motion.article className="operator-tile" key={operator.name} initial={{ opacity: 0, rotateY: 12 }} animate={{ opacity: 1, rotateY: 0 }} transition={{ delay: index * 0.035 }} whileHover={{ y: -3, rotateY: -3 }}>
        <div className="operator-topline"><span>OP / {String(index + 1).padStart(2, "0")}</span><Crosshair size={15} /></div><h3>{operator.name}</h3><div className="operator-statline"><b>{Math.round(operator.winRate * 100)}<small>%</small></b><span>ROUND WIN RATE</span></div>
        <div className="operator-foot"><span>{operator.picks} picks</span><span>{operator.kills} / {operator.deaths} K/D</span></div>
        <div className="bar-track"><i style={{ width: `${Math.min(operator.winRate * 100, 100)}%` }} /></div>
      </motion.article>)}</div>
      <section className="surface-panel site-panel"><div className="panel-heading"><div><p className="eyebrow">Map by objective</p><h3>Site performance</h3></div></div><div className="site-table">{analytics.sites.map((site) => <div key={`${site.operator}-${site.site}`}><strong>{site.operator}</strong><span>{site.site}</span><span>{site.wins}/{site.rounds} rounds</span><b>{Math.round(site.winRate * 100)}%</b></div>)}</div></section>
    </> : <EmptyState title="Operator data unavailable" detail="The parsed replay did not include operator selections for these rounds." />}
  </div>;
}

function TeamModule({ summary }: { summary: SeasonSummary | null }) {
  const [selectedTeam, setSelectedTeam] = useState("");
  useEffect(() => { if (summary?.teams.length && !summary.teams.some((team) => team.team === selectedTeam)) setSelectedTeam(summary.teams[0].team); }, [summary, selectedTeam]);
  const team = summary?.teams.find((item) => item.team === selectedTeam);
  return <div className="module-view">
    <div className="module-heading"><div><p className="eyebrow">Squad intelligence</p><h2>Team Analytics</h2></div>{summary?.teams.length ? <label className="select-control"><UsersRound size={14} /><select value={selectedTeam} onChange={(event) => setSelectedTeam(event.target.value)}>{summary.teams.map((item) => <option key={item.team}>{item.team}</option>)}</select><ChevronDown size={14} /></label> : null}</div>
    {!team ? <EmptyState title="No saved team totals" detail="Import a roster and log a match to build team analytics." /> : <>
      <div className="kpi-grid team-kpis"><Kpi label="EPS" value={team.eps ?? "—"} hint="ROUNDS-WEIGHTED" /><Kpi label="K/D ratio" value={team.kd.toFixed(2)} hint="SEASON TOTAL" /><Kpi label="Entry differential" value={team.entry_diff > 0 ? `+${team.entry_diff}` : team.entry_diff} hint="OPENING DUELS" /><Kpi label="KOST average" value={`${team.kost_avg.toFixed(1)}%`} hint="ROSTER AVERAGE" /><Kpi label="Clutch success" value={team.clutch_success_rate === null ? "—" : `${Math.round(team.clutch_success_rate * 100)}%`} hint="1V1 TO 1V5" /></div>
      <section className="surface-panel table-wrap"><div className="panel-heading"><div><p className="eyebrow">Roster metrics</p><h3>{team.team}</h3></div><span className="count-label">{team.players.length} PLAYERS</span></div><table><thead><tr><th>Player</th><th>EPS</th><th>Rounds</th><th>K/D</th><th>Entry +/-</th><th>KOST</th><th>HS</th><th>Clutches</th></tr></thead><tbody>{team.member_stats.map((player) => <tr key={player.username}><td>{player.username}</td><td>{player.eps ?? "—"}</td><td>{player.totals.rounds_played}</td><td>{player.kd.toFixed(2)}</td><td>{player.entry_diff > 0 ? `+${player.entry_diff}` : player.entry_diff}</td><td>{player.kost_pct.toFixed(1)}%</td><td>{player.hs_pct.toFixed(1)}%</td><td>{player.clutches_won}</td></tr>)}</tbody></table></section>
    </>}
  </div>;
}

function SchoolModule({
  schools, source, error, busy, onSync, onImport, onTrack,
}: {
  schools: School[]; source: string; error: string; busy: boolean;
  onSync: () => void; onImport: (file: File) => void; onTrack: (team: string, roster: string[]) => void;
}) {
  const { selectedSchool, setBrand, season } = useClientStore();
  const [search, setSearch] = useState(selectedSchool);
  const [selectedTeamName, setSelectedTeamName] = useState("");
  const selected = schools.find((school) => school.name === selectedSchool) || schools.find((school) => school.name === search);
  const team = selected?.teams.find((item) => item.name === selectedTeamName) || selected?.teams[0];
  const safeAccent = /^#[\da-f]{6}$/i.test(selected?.primary_color || "") ? selected?.primary_color as string : "#d49353";
  return <div className="module-view">
    <div className="module-heading"><div><p className="eyebrow">Collegiate network</p><h2>School Selection</h2></div><div className="school-actions"><button className="button button-outline" onClick={onSync} disabled={busy}><RefreshCw size={14} className={busy ? "spin" : ""} /> Sync catalog</button><label className="button button-quiet file-label"><FileUp size={14} /> Import JSON<input type="file" accept="application/json,.json" hidden onChange={(event) => { if (event.currentTarget.files?.[0]) onImport(event.currentTarget.files[0]); event.currentTarget.value = ""; }} /></label></div></div>
    {error && <div className="notice notice-error">{error}</div>}
    <div className="school-layout">
      <section className="surface-panel school-search-panel">
        <p className="eyebrow">NECC directory</p><label className="search-field"><SchoolIcon size={16} /><input list="school-options" value={search} onChange={(event) => { setSearch(event.target.value); const found = schools.find((school) => school.name === event.target.value); if (found) setBrand(found.name, /^#[\da-f]{6}$/i.test(found.primary_color || "") ? found.primary_color! : "#d49353"); }} placeholder="Search schools" /><datalist id="school-options">{schools.map((school) => <option key={school.name} value={school.name} />)}</datalist></label>
        <span className="source-label">{source || "NO CATALOG SOURCE"}</span>
        {!schools.length && <EmptyState title="No school catalog" detail="Configure NECC_R6_DATA_URL or import an authorized JSON catalog." />}
        {selected && <>
          {selected.logo_url && <img className="school-logo" src={selected.logo_url} alt={`${selected.name} logo`} />}
          <div className="school-brand" style={{ "--school-accent": safeAccent } as CSSProperties}><span>NECC / R6</span><h3>{selected.name}</h3></div>
          <label className="select-control team-select"><span>Team roster</span><select value={team?.name || ""} onChange={(event) => setSelectedTeamName(event.target.value)}>{selected.teams.map((item) => <option key={item.name}>{item.name}</option>)}</select><ChevronDown size={14} /></label>
          {team && <button className="button button-primary full-button" onClick={() => onTrack(team.name, team.roster)} disabled={busy || !team.roster.length}><UsersRound size={14} /> Track {team.roster.length} roster players</button>}
          <small className="season-note">TRACKING INTO {season.toUpperCase()}</small>
        </>}
      </section>
      <div className="school-details">
        {selected && team ? <>
          <section className="surface-panel roster-panel"><div className="panel-heading"><div><p className="eyebrow">Active roster</p><h3>{team.name}</h3></div><span className="count-label">{team.roster.length} MEMBERS</span></div>
            {team.roster.length ? <div className="roster-carousel">{team.roster.map((player, index) => <motion.article className="roster-card" key={`${player}-${index}`} whileHover={{ rotateY: -7, z: 10, y: -4 }}><span>ROSTER / {String(index + 1).padStart(2, "0")}</span><div className="roster-avatar"><UsersRound size={18} /></div><strong>{player}</strong><small>{team.name}</small></motion.article>)}</div> : <EmptyState title="Roster not published" detail="This catalog entry does not include player names." />}
          </section>
          <div className="school-data-grid"><section className="surface-panel data-panel"><div className="panel-heading"><div><p className="eyebrow">League table</p><h3>Standings</h3></div></div>{team.standings ? <pre>{JSON.stringify(team.standings, null, 2)}</pre> : <EmptyState title="No standings data" detail="Standings were not included in this catalog." />}</section>
            <section className="surface-panel data-panel"><div className="panel-heading"><div><p className="eyebrow">Recent fixtures</p><h3>Match history</h3></div></div>{team.matches?.length ? <pre>{JSON.stringify(team.matches, null, 2)}</pre> : <EmptyState title="No fixtures available" detail="Match history was not included in this catalog." />}</section></div>
        </> : <div className="surface-panel school-placeholder"><Shield size={24} /><h3>Select a school</h3><p>School colors, rosters, standings, and fixtures appear here when a catalog is loaded.</p></div>}
      </div>
    </div>
  </div>;
}

function exportJson(summary: SeasonSummary | null) {
  if (!summary) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(summary, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${summary.season}_team_report.json`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000); // revoking at once can cancel the download
}

export default function App() {
  const { activeModule, setModule, season, setSeason, accent, selectedSchool, setBrand } = useClientStore();
  const [summary, setSummary] = useState<SeasonSummary | null>(null);
  const [matches, setMatches] = useState<MatchHistoryItem[]>([]);
  const [schools, setSchools] = useState<School[]>([]);
  const [schoolSource, setSchoolSource] = useState("");
  const [schoolError, setSchoolError] = useState("");
  const [apiOnline, setApiOnline] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [parsedMatch, setParsedMatch] = useState<NormalizedMatch | null>(null);
  const [selectedPlayers, setSelectedPlayers] = useState<string[]>([]);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([api.health(), api.summary(season), api.matches(season)]).then(([health, stats, history]) => {
      if (cancelled) return;
      setApiOnline(health.status === "fulfilled");
      if (stats.status === "fulfilled") { setSummary(stats.value); setError(""); }
      else { setSummary(null); setError(stats.reason instanceof Error ? stats.reason.message : "API unavailable"); }
      if (history.status === "fulfilled") setMatches(history.value.matches);
    });
    return () => { cancelled = true; };
  }, [season, reload]);

  useEffect(() => {
    let cancelled = false;
    api.schools().then((result) => {
      if (cancelled) return;
      setSchools(result.schools);
      setSchoolSource(result.source);
      setSchoolError("");
    }).catch((reason: unknown) => {
      if (!cancelled) setSchoolError(reason instanceof Error ? reason.message : "School catalog unavailable");
    });
    return () => { cancelled = true; };
  }, [reload]);

  async function parseFiles(files: File[]) {
    if (!files.length) return;
    setBusy(true); setNotice(""); setError("");
    try {
      const result = await api.parseReplays(files);
      if (!result.matches.length) throw new Error(result.skipped || "No valid Siege replay found.");
      const nextMatch = result.matches[0].match;
      setParsedMatch(nextMatch);
      setSelectedPlayers([]);  // nobody until picked: someone on your team in Ranked may be a stranger
      setNotice(result.skipped || `${result.matches.length} match group${result.matches.length === 1 ? "" : "s"} parsed.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Replay parse failed");
    } finally {
      setBusy(false);
    }
  }

  async function saveMatch() {
    if (!parsedMatch) return;
    const rosters: Record<string, string[]> = {};
    for (const player of parsedMatch.players) {
      if (!selectedPlayers.includes(player.name)) continue;
      const teamName = parsedMatch.team_names[player.team] || `Team ${player.team + 1}`;
      rosters[teamName] = [...(rosters[teamName] || []), player.name];
    }
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await api.logMatch(season, parsedMatch, rosters);
      setNotice(`${result.rounds_logged} player-rounds saved; ${result.rounds_skipped_duplicate} duplicates skipped.`);
      setReload((value) => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save match");
    } finally {
      setBusy(false);
    }
  }

  async function syncSchools() {
    setBusy(true); setSchoolError("");
    try {
      const result = await api.schools();
      setSchools(result.schools); setSchoolSource(result.source);
    } catch (reason) {
      setSchoolError(reason instanceof Error ? reason.message : "School catalog unavailable");
    } finally { setBusy(false); }
  }

  async function importSchools(file: File) {
    setBusy(true); setSchoolError("");
    try {
      const result = await api.importCatalog(file);
      setSchools(result.schools); setSchoolSource(result.source);
      const first = result.schools[0];
      if (first) setBrand(first.name, /^#[\da-f]{6}$/i.test(first.primary_color || "") ? first.primary_color! : "#d49353");
    } catch (reason) {
      setSchoolError(reason instanceof Error ? reason.message : "Catalog import failed");
    } finally { setBusy(false); }
  }

  async function trackRoster(team: string, roster: string[]) {
    setBusy(true); setSchoolError("");
    try {
      await api.trackRoster(season, team, roster);
      setNotice(`${roster.length} players added to ${team}.`);
      setReload((value) => value + 1);
    } catch (reason) {
      setSchoolError(reason instanceof Error ? reason.message : "Could not track roster");
    } finally { setBusy(false); }
  }

  function togglePlayer(name: string) {
    setSelectedPlayers((current) => current.includes(name) ? current.filter((player) => player !== name) : [...current, name]);
  }

  const rootStyle = { "--accent": accent, "--accent-rgb": accent.match(/[\da-f]{2}/gi)?.map((part) => parseInt(part, 16)).join(",") || "212,147,83" } as CSSProperties;
  return <main className="app-shell" style={rootStyle}>
    <header className="topbar">
      <a className="brand-lockup" href="#dashboard" onClick={(event) => { event.preventDefault(); setModule("Dashboard"); }}><span className="brand-mark"><Shield size={19} /></span><span>R6<span className="brand-light"> / INTEL</span><small>TACTICAL ANALYTICS</small></span></a>
      <nav className="module-nav" aria-label="Main modules">{modules.map(({ label, icon: Icon }, index) => <button className={`nav-tab ${activeModule === label ? "active" : ""}`} key={label} onClick={() => setModule(label)}><Icon size={15} /><span>{label}</span><small>0{index + 1}</small></button>)}</nav>
      <div className={`api-state ${apiOnline ? "online" : "offline"}`}>{apiOnline ? <Wifi size={14} /> : <WifiOff size={14} />}<span>{apiOnline ? "API LINK" : "OFFLINE"}</span></div>
    </header>
    <Suspense fallback={<div className="scene-header scene-fallback"><span>INITIALIZING TACTICAL SCENE</span></div>}><SceneHeader accent={accent} school={selectedSchool} /></Suspense>
    <div className="content-shell">
      <div className="subbar"><div className="breadcrumb"><span>COMMAND CENTER</span><i>/</i><strong>{activeModule.toUpperCase()}</strong></div><label className="season-control"><span>SEASON</span><select value={season} onChange={(event) => setSeason(event.target.value)}><option value="current">CURRENT</option>{(summary?.players.length ? [summary.season] : []).filter((item) => item !== "current").map((item) => <option key={item} value={item}>{item.toUpperCase()}</option>)}</select><ChevronDown size={13} /></label></div>
      {error && !summary && <div className="notice notice-error"><WifiOff size={15} /><span>API unavailable: {error}. Start `uvicorn api:app --app-dir scripts --host 127.0.0.1 --port 8000`.</span></div>}
      {notice && <div className="notice notice-success"><Check size={15} /><span>{notice}</span><button onClick={() => setNotice("")} aria-label="Dismiss notice">×</button></div>}
      <AnimatePresence mode="wait" initial={false}>
        <motion.div key={activeModule} className="module-transition" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.2 }}>
          {activeModule === "Dashboard" && <><Overview summary={summary} matches={matches} onExport={() => exportJson(summary)} /><ReplayIntake match={parsedMatch} selectedPlayers={selectedPlayers} busy={busy} message={notice} onFiles={parseFiles} onTogglePlayer={togglePlayer} onSave={saveMatch} /></>}
          {activeModule === "Match History" && <HistoryModule matches={matches} />}
          {activeModule === "Operator Analytics" && <OperatorModule match={parsedMatch} />}
          {activeModule === "Team Analytics" && <TeamModule summary={summary} />}
          {activeModule === "School Selection" && <SchoolModule schools={schools} source={schoolSource} error={schoolError} busy={busy} onSync={syncSchools} onImport={importSchools} onTrack={trackRoster} />}
        </motion.div>
      </AnimatePresence>
      <footer className="app-footer"><span>R6 MATCH INTELLIGENCE</span><span>REPLAY-DERIVED DATA <i /> PRIVATE BY DEFAULT</span><span>BUILD 01.12</span></footer>
    </div>
  </main>;
}