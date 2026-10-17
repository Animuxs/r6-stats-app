/* Full browser workspace. All statistics come from the desktop's Python engines. */
(() => {
  "use strict";
  const ui = window.R6UI, R6 = window.R6, $ = id => document.getElementById(id), esc = ui.esc;
  const profiles = window.R6Profiles;
  let online = null, personal = null, pendingShared = 0, activeTheme;
  let viewingAs = ""; // Viewing identity is private to this tab and never enters a backup.
  const selectedPlayer = () => sample ? undefined : real.directory?.find(p => p.id === viewingAs)?.player || undefined;
  const empty = () => ({directory: [], version: 1, matches: [], teams: {}, seasonTeams: {}, removedSeasonTeams: {}, seasonTeamMatches: {}});
  let real = empty(), sample = null, activeTab = "report", ready = false, revision = 0;
  let currentReport = ui.current(), currentMeta = window.R6ReportMeta;
  const state = () => sample || real;
  const notify = (text, error = false) => {
    const box = $("workspace-message"); box.hidden = !text; box.className = `notice${error ? " err" : ""}`;
    box.textContent = text;
  };
  function validate(value) {
    if (!value || value.version !== 1 || !Array.isArray(value.matches) || !value.teams || typeof value.teams !== "object")
      throw Error("This is not an R6 Match Stats workspace backup.");
    if (value.matches.length > 3000 || Object.keys(value.teams).length > 500) throw Error("This workspace is too large.");
    for (const m of value.matches) {
      if (typeof m.key !== "string" || !m.data || !Array.isArray(m.data.players) || !Array.isArray(m.data.rounds)
          || !Array.isArray(m.players) || m.players.some(p => typeof p !== "string")) throw Error("A saved match is incomplete.");
    }
    for (const [name, players] of Object.entries(value.teams))
      if (!name.trim() || !Array.isArray(players) || players.some(p => typeof p !== "string")) throw Error("A saved team is incomplete.");
    const seasonTeams = value.seasonTeams ?? structuredClone(value.teams);
    const removedSeasonTeams = value.removedSeasonTeams ?? {};
    if (!seasonTeams || typeof seasonTeams !== "object" || !removedSeasonTeams || typeof removedSeasonTeams !== "object") throw Error("Invalid season teams.");
    for (const players of Object.values(seasonTeams)) if (!Array.isArray(players) || players.some(p=>typeof p!=="string")) throw Error("Invalid season roster.");
    for (const names of Object.values(removedSeasonTeams)) if (!Array.isArray(names) || names.some(n=>typeof n!=="string")) throw Error("Invalid deleted season team list.");
    const seasonTeamMatches=value.seasonTeamMatches ?? {};
    if(!seasonTeamMatches || typeof seasonTeamMatches!=="object")throw Error("Invalid season match choices.");
    for(const teams of Object.values(seasonTeamMatches)){
      if(!teams || typeof teams!=="object")throw Error("Invalid season match choices.");
      for(const ids of Object.values(teams))if(!Array.isArray(ids)||ids.some(id=>typeof id!=="string"))throw Error("Invalid season match list.");
    }
    return {directory: profiles.directory(value.directory), version: 1, matches: value.matches, teams: value.teams, seasonTeams, removedSeasonTeams, seasonTeamMatches, matchday:value.matchday || {fixtures:[],availability:{},notes:""}};
  }
  const storage = new Promise((resolve, reject) => {
    const request = indexedDB.open("r6-match-stats", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("workspace");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(Error("Browser storage is unavailable. Export your workspace before closing this page."));
  });
  async function read() {
    const db = await storage;
    return new Promise((resolve, reject) => {
      const request = db.transaction("workspace").objectStore("workspace").get("current");
      request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
    });
  }
  async function write(value) {
    const db = await storage;
    await new Promise((resolve, reject) => {
      const tx = db.transaction("workspace", "readwrite"); tx.objectStore("workspace").put(value, "current");
      tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error);
    });
  }
  async function save() {
    revision++;
    if (!sample && online) {
      if (pendingShared) {online.blocked=true;throw Error("Another save is in progress. Export your pending changes, then refresh before saving again.");}
      if (online.blocked) throw Error("Shared save is blocked. Export your pending workspace, then refresh or sign in again.");
      pendingShared++;
      try {
        const result = await teamRequest(online.server, "/workspace", "PUT", {revision:online.revision,workspace:portable()}, online.token);
        online.revision=result.revision;
      } catch(error) {online.blocked=true;notify(error.message+" Export your pending workspace before refreshing.",true);throw error;}
      finally {pendingShared--;status();}
    } else if (!sample) {
      try { await write(portable()); }
      catch { notify("Your changes are only in memory: browser storage could not save them. Export a workspace backup now.", true); }
    }
    status();
  }
  function status() {
    document.dispatchEvent(new CustomEvent("r6workspace"));
    $("workspace-status").textContent = sample ? "Demo season · separate from your saved stats" : online ? `${online.name} · ${real.matches.length} confirmed matches · ${online.blocked?"pending changes — export before refresh":"shared server workspace"}` : `${real.matches.length} confirmed matches · saved on this device`;
    $("workspace-demo").textContent = sample ? "Leave the demo season" : "Try the demo season";
  }
  function download(name, data, type = "application/json") {
    const url = URL.createObjectURL(new Blob([data], {type})), a = document.createElement("a");
    a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function csv(rows) {
    if (!rows.length) return "";
    const columns = Object.keys(rows[0]), cell = v => '"' + String(v ?? "").replaceAll('"', '""') + '"';
    return [columns, ...rows.map(r => columns.map(c => r[c]))].map(r => r.map(cell).join(",")).join("\r\n");
  }
  function table(rows, columns = null) {
    if (!rows?.length) return '<p class="dim">No matches meet these selections yet.</p>';
    columns ||= Object.keys(rows[0]);
    const val = v => typeof v === "number" && !Number.isInteger(v) ? Number(v.toFixed(2)) : v ?? "—";
    return `<div class="table-wrap"><table><thead><tr>${columns.map(c => `<th scope="col">${esc(c.includes("_")?c.replaceAll("_"," ").replace(/\b[a-z]/g,l=>l.toUpperCase()):c)}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${columns.map(c => `<td>${esc(val(r[c]))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }
  function chart(answer) {
    if (!answer.chart || answer.rows.length < 2) return "";
    const {x, y, kind} = answer.chart, data = answer.rows.filter(r => Number.isFinite(r[y])).slice(0, 40);
    if (!data.length) return "";
    const low=Math.min(0,...data.map(r=>r[y])), high=Math.max(1,...data.map(r=>r[y])), width=800,dx=740/data.length;
    const position=v=>185-155*(v-low)/(high-low),baseline=position(0);
    const points=data.map((r,i)=>[40+dx*(i+.5),position(r[y])]);
    return `<svg class="workspace-chart" viewBox="0 0 ${width} 235" role="img" aria-label="${esc(y)} by ${esc(x)}"><line x1="30" y1="${baseline}" x2="790" y2="${baseline}"/>${kind === "line" ? `<polyline points="${points.map(p => p.join(",")).join(" ")}"/>` : ""}${data.map((r,i) => `${kind === "line" ? `<circle cx="${points[i][0]}" cy="${points[i][1]}" r="4"/>` : `<rect x="${40+dx*i}" y="${Math.min(baseline,points[i][1])}" width="${Math.max(2,dx-6)}" height="${Math.max(1,Math.abs(baseline-points[i][1]))}"/>`}<text x="${points[i][0]}" y="205" text-anchor="middle">${esc(String(r[x]).slice(0,12))}</text><title>${esc(r[x])}: ${esc(r[y])}</title>`).join("")}</svg>`;
  }
  let pythonQueue = Promise.resolve();
  function engine(action, args = {}) {
    const payload = JSON.stringify({...state(), viewer: selectedPlayer(), demo:!!sample});
    const run = pythonQueue.then(async () => JSON.parse((await ui.bridge()).workspace(payload, action, JSON.stringify(args))));
    pythonQueue = run.catch(() => {}); return run;
  }
  async function safely(fn) {
    try { await fn(); } catch (error) { notify(error.message || String(error), true); }
  }
  function openSaved(entry) {
    safely(async () => {
      const bridge = await ui.bridge();
      const report = JSON.parse(bridge.normalized(JSON.stringify(entry.data)));
      ui.showTab("report"); ui.renderReport(report, {label: entry.key, demo: !!sample});
      $("result").scrollIntoView({block:"start", behavior:"auto"});
    });
  }
  function enhanceReport(report, meta) {
    currentReport = report; currentMeta = meta;
    if (!report?.data) return;
    document.querySelectorAll("#result .confirmation,#result .player-round-detail").forEach(n=>n.remove());
    const old = state().matches.find(m => m.data.match_id === report.data.match_id);
    const panel = document.createElement("div"); panel.className = "confirmation";
    panel.innerHTML = `<h3>${sample || meta.demo ? "Demo match" : old ? "Update tracked players" : "Add to your stats"}</h3>
      <p class="dim">${sample || meta.demo ? "Sample data stays separate from your own stats." : "Pick whose stats to keep. No player is selected for you; confirming again replaces this match without counting it twice."}</p>
      <div class="player-picks">${report.data.players.map((p,i) => `<label><input type="checkbox" data-player="${i}" ${old?.players.includes(p.name) ? "checked" : ""} ${sample||meta.demo ? "disabled" : ""}>${esc(p.name)} · ${esc(report.match.team_names[p.team] || "")}</label>`).join("")}</div>
      ${sample||meta.demo ? "" : `<label><input type="checkbox" id="include-season" ${(old?.seasons?.length || old?.season) ? "checked" : ""}> Also save selected roster players to a season</label><label>Season <input id="confirm-season" maxlength="180" value="${esc(old?.seasons?.[0] || old?.season || "current")}"></label><button class="btn primary" id="confirm-browser-match">Confirm match and players</button>`}`;
    $("result").append(panel);
    panel.querySelector("#confirm-browser-match")?.addEventListener("click", () => safely(async () => {
      const players = [...panel.querySelectorAll("[data-player]:checked")].map(input => report.data.players[Number(input.dataset.player)].name);
      if (!players.length) throw Error("Pick at least one player before confirming.");
      const entry = {key: meta.label || report.data.match_id, data: report.data, players,
        seasons: [...new Set([...(old?.seasons || (old?.season ? [old.season] : [])), ...($("include-season").checked ? [$("confirm-season").value.trim() || "current"] : [])])]};
      // Validate with the real engine before changing the saved workspace.
      const candidate = {...real, matches: [...real.matches.filter(m => m.data.match_id !== report.data.match_id), entry]};
      JSON.parse((await ui.bridge()).workspace(JSON.stringify(candidate)));
      real = candidate; await save(); notify("Match and selected players saved.");
    }));
    const detail = document.createElement("div");detail.className="player-round-detail";
    detail.innerHTML = `<h3>Player rounds</h3><label>Player <select id="round-player">${Object.keys(report.player_details || {}).map(n => `<option>${esc(n)}</option>`).join("")}</select></label><div id="player-round-results"></div>`;
    $("result").append(detail);
    const render = () => {
      const s = report.player_details[$("round-player").value]; if (!s) return;
      const rounds = s.rounds.map(r => ({Round:r.round_num, Side:r.side, Operator:r.operator, Result:r.won ? "Won" : "Lost", Kills:r.kills, Deaths:r.deaths, Assists:r.assists, Highlights:r.highlights.map(h=>h[0]).join(", ")}));
      $("player-round-results").innerHTML = `<p>EPS ${esc(s.EPS)}</p>${table(rounds)}`;
    };
    $("round-player").addEventListener("change", render); render();
  }
  document.addEventListener("r6report", e => enhanceReport(e.detail.report,e.detail.meta));
  async function history() {
    const box = $("history-content");
    if (!state().matches.length) { box.innerHTML = '<p class="notice">Confirm a match on Dashboard, or try the demo season.</p>'; return; }
    const info = await engine("overview");
    box.innerHTML = `<p class="dim">${info.summary.matches} confirmed matches. Only the players you picked count.</p>` + [...state().matches].reverse().map((m,i) => `<article class="saved-match"><div><b>${esc(m.data.map)} · ${esc(m.data.final_score.join("–"))}</b><small>${esc(m.key)} · ${esc(m.players.join(", "))}</small></div><button class="btn small" data-open="${i}">Open match</button>${sample ? "" : `<button class="btn small" data-remove="${i}">Remove from stats</button>`}</article>`).join("");
    const entries = [...state().matches].reverse();
    box.querySelectorAll("[data-open]").forEach(b => b.onclick = () => openSaved(entries[Number(b.dataset.open)]));
    box.querySelectorAll("[data-remove]").forEach(b => b.onclick = () => safely(async () => {
      const entry = entries[Number(b.dataset.remove)];
      if (!confirm(`Remove ${entry.key} from your stats and seasons? You can import its replay again.`)) return;
      real.matches = real.matches.filter(m => m !== entry); await save(); await history();
    }));
  }
  function askPage() {
    $("ask-content").innerHTML = `<p class="dim">Ask about the matches you have confirmed. The same engine as the desktop app runs on this device.</p><form id="ask-form"><label>Your question<input id="question" required placeholder="How has my K/D changed over my last 10 matches?"></label><button class="btn primary">Ask</button></form><div class="exports">${["What's my best map?","Attack vs defense win rate","Who had the most kills in a match?"].map(q=>`<button class="btn small" data-question="${esc(q)}">${esc(q)}</button>`).join("")}</div><div id="answer" aria-live="polite"></div>`;
    const run = () => safely(async () => {
      $("answer").textContent = "Reading your stats…";
      const a = await engine("ask", {question:$("question").value});
      $("answer").innerHTML = `<p class="dim">${esc(a.understood)}</p><h3>${esc(a.headline)}</h3>${a.notes.map(n=>`<p>${esc(n)}</p>`).join("")}${chart(a)}${table(a.rows,a.columns)}<button class="btn small" id="answer-csv">Download CSV</button>`;
      $("answer-csv").onclick = () => download("r6-answer.csv",csv(a.rows),"text/csv");
    });
    $("ask-form").onsubmit = e => {e.preventDefault();run();};
    $("ask-content").querySelectorAll("[data-question]").forEach(b=>b.onclick=()=>{$("question").value=b.dataset.question;run();});
  }
  async function teamsPage() {
    const info = await engine("overview"), names = Object.keys(state().teams);
    $("teams-content").innerHTML = `<p class="dim">Build a team from the players whose stats you track.</p><form id="team-form"><label>Team name<input id="team-name-save" maxlength="180" required></label><label>Players, one per line<textarea id="team-players" required></textarea></label><button class="btn primary">Save team</button></form><div class="workspace-form"><label>Saved team<select id="analytics-team">${names.map(n=>`<option>${esc(n)}</option>`).join("")}</select></label><label>Minimum roster players in a match<input id="team-min" type="number" min="1" max="5" value="3"></label><button class="btn" id="team-stats">Show team stats</button><button class="btn" id="track-team" ${sample?"disabled":""}>Track team in season</button><button class="btn" id="delete-team" ${sample?"disabled":""}>Delete team</button></div><div id="team-results"></div>`;
    $("team-players").value = info.me ? [info.me,...info.players.filter(p=>p!==info.me).slice(0,4)].join("\n") : "";
    $("team-form").onsubmit = e => {e.preventDefault();safely(async()=>{
      const name=$("team-name-save").value.trim(),players=[...new Set($("team-players").value.split(/\r?\n/).map(p=>p.trim()).filter(Boolean))];
      if(!name||!players.length)throw Error("Enter a team and at least one player.");
      Object.defineProperty(state().teams,name,{value:players,writable:true,enumerable:true,configurable:true});
      await save(); await teamsPage();
    });};
    const show=()=>safely(async()=>{
      const name=$("analytics-team").value;if(!name)return;
      const result=await engine("team",{team:name,minimum:Number($("team-min").value)});
      $("team-name-save").value=name;$("team-players").value=state().teams[name].join("\n");
      const summary=Object.entries(result.summary).filter(([,v])=>typeof v!=="object").map(([Metric,Value])=>({Metric:Metric.replaceAll("_"," "),Value}));
      $("team-results").innerHTML=`<h3>${esc(name)}</h3>${table(summary)}<h3>Players</h3>${table(result.table)}<button class="btn small" id="team-csv">Download players CSV</button><h3>Matches</h3>${table(result.matches)}`;
      $("team-csv").onclick=()=>download("r6-team-players.csv",csv(result.table),"text/csv");
    });
    $("track-team").onclick=()=>safely(async()=>{const name=$("analytics-team").value;if(!name)return;real.seasonTeams[name]=[...real.teams[name]];real.removedSeasonTeams[chosenSeason]=(real.removedSeasonTeams[chosenSeason]||[]).filter(n=>n!==name);await save();notify(`Tracking ${name} in Season Tracker.`);});
    $("team-stats").onclick=show;$("analytics-team").onchange=show;
    $("delete-team").onclick=()=>safely(async()=>{const n=$("analytics-team").value;if(n&&confirm(`Delete saved team ${n}? Match stats are kept.`)){delete real.teams[n];await save();await teamsPage();}});
    if(names.length)show();
  }
  async function operatorsPage() {
    const info=await engine("overview");
    $("operators-content").innerHTML=`<div class="workspace-form"><label>Scope<select id="operator-scope"><option>All matches</option><option>This match</option></select></label><label>Player<select id="operator-player">${info.players.map(n=>`<option ${n.toLowerCase()===selectedPlayer()?.toLowerCase()?"selected":""}>${esc(n)}</option>`).join("")}</select></label><button class="btn" id="operator-show">Show operators</button></div><div id="operator-results"></div>`;
    const show=()=>safely(async()=>{if($("operator-scope").value==="This match"){const match=ui.current()?.data;if(!match)throw Error("Open a match on Dashboard first.");const result=JSON.parse((await ui.bridge()).match_operators(JSON.stringify(match)));$("operator-results").innerHTML=table(result.operators)+"<h3>Site performance</h3>"+table(result.sites);return;}const rows=await engine("operators",{player:$("operator-player").value});$("operator-results").innerHTML=table(rows)+`<button class="btn small" id="operator-csv">Download CSV</button>`;$("operator-csv").onclick=()=>download("r6-operators.csv",csv(rows),"text/csv");});
    $("operator-scope").onchange=show;$("operator-show").onclick=show;$("operator-player").onchange=show;show();
  }
  let chosenSeason="current", chosenSeasonTeam="";
  async function seasonPage() {
    if(sample){$("season-content").innerHTML='<p class="notice">Season tracking is off in the demo. Leave the demo to manage your own seasons.</p>';return;}
    const seasons=[...new Set([chosenSeason,"current",...real.matches.flatMap(m=>m.seasons || (m.season?[m.season]:[]))])];
    $("season-content").innerHTML=`<div class="workspace-form"><label>Season<select id="season-name">${seasons.map(n=>`<option ${n===chosenSeason?"selected":""}>${esc(n)}</option>`).join("")}</select></label><button class="btn" id="season-show">Show season</button><button class="btn" id="season-export">Export season JSON</button></div><p class="dim">Only checked matches and the players confirmed for each match count. Built teams and season teams are managed separately.</p><div id="season-team-controls"></div><div id="season-matches"></div><button class="btn primary" id="season-save">Save season matches</button><div id="season-results"></div>`;
    let snapshot=null;
    const renderTeam=()=>{
      chosenSeasonTeam=$("season-team")?.value || "";
      const chosen=real.seasonTeamMatches[chosenSeason]?.[chosenSeasonTeam];
      $("season-matches").innerHTML=real.matches.map((m,i)=>`<label class="saved-match"><input type="checkbox" data-season-match="${i}" ${(chosen ? chosen.includes(m.data.match_id) : (m.seasons || [m.season]).includes(chosenSeason))?"checked":""}>${esc(m.key)} · ${esc(m.data.map)}</label>`).join("");
      const rows=snapshot.tables[chosenSeasonTeam] || [];
      const summary=snapshot.summaries[chosenSeasonTeam];
      const metrics=summary ? Object.entries(summary).map(([Metric,Value])=>({Metric:Metric.replaceAll("_"," "),Value})) : [];
      $("season-results").innerHTML=`<p>${snapshot.rounds_logged} rounds counted in ${esc(chosenSeason)}</p><h3>${esc(chosenSeasonTeam || "No season team")}: players</h3>${table(rows)}<button class="btn small" id="season-player-csv">Download players CSV</button><h3>Team statistics</h3>${table(metrics)}`;
      $("season-player-csv").onclick=()=>download("r6-season-players.csv",csv(rows),"text/csv");
    };
    const show=()=>safely(async()=>{
      chosenSeason=$("season-name").value;
      snapshot=await engine("season",{season:chosenSeason});
      const names=Object.keys(snapshot.rosters);
      $("season-team-controls").innerHTML=`<div class="workspace-form"><label>Season team<select id="season-team">${names.map(n=>`<option ${n===chosenSeasonTeam?"selected":""}>${esc(n)}</option>`).join("")}</select></label><button class="btn" id="season-delete-team" ${names.length?"":"disabled"}>Delete season team</button></div><p class="dim">Deleting a season team removes its counted results only from the selected season. Built teams, saved matches and other seasons are kept. Track the team again from Team Analytics to restore an empty roster.</p>`;
      $("season-team").onchange=renderTeam;
      $("season-delete-team").onclick=()=>safely(async()=>{
        const name=$("season-team").value;if(!name||!confirm(`Delete ${name} and its counted results from ${chosenSeason}? Built teams and other seasons are kept.`))return;
        real.seasonTeamMatches[chosenSeason] ||= {};real.seasonTeamMatches[chosenSeason][name]=[];
        real.removedSeasonTeams[chosenSeason]=[...new Set([...(real.removedSeasonTeams[chosenSeason]||[]),name])];await save();await show();
      });
      $("season-matches").innerHTML=real.matches.map((m,i)=>`<label class="saved-match"><input type="checkbox" data-season-match="${i}" ${(m.seasons || [m.season]).includes(chosenSeason)?"checked":""}>${esc(m.key)} · ${esc(m.data.map)}</label>`).join("");
      renderTeam();
    });
    $("season-show").onclick=show;$("season-name").onchange=show;
    $("season-save").onclick=()=>safely(async()=>{if(!chosenSeasonTeam)throw Error("Track a season team first.");real.seasonTeamMatches[chosenSeason] ||= {};real.seasonTeamMatches[chosenSeason][chosenSeasonTeam]=[];$("season-matches").querySelectorAll("[data-season-match]").forEach(c=>{const m=real.matches[Number(c.dataset.seasonMatch)];m.seasons=m.seasons||(m.season?[m.season]:[]);if(c.checked){if(!m.seasons.includes(chosenSeason))m.seasons.push(chosenSeason);real.seasonTeamMatches[chosenSeason][chosenSeasonTeam].push(m.data.match_id);}delete m.season;});await save();await show();});
    $("season-export").onclick=()=>snapshot&&download("r6-season.json",JSON.stringify(snapshot,null,2));await show();
  }
  const defaultTheme={name:"R6 Match Stats",team:"",primary:"#52d5f2",secondary:"#f6be4f",logo:""};
  function cleanTheme(value) {
    const theme={...defaultTheme};
    for(const k of ["name","team"])theme[k]=String(value?.[k]||theme[k]).slice(0,180);
    for(const k of ["primary","secondary"])if(/^#[0-9a-f]{6}$/i.test(value?.[k]))theme[k]=value[k];
    if(/^https:\/\//.test(value?.logo)||/^data:image\/(png|jpeg|webp);base64,[a-z0-9+/=]+$/i.test(value?.logo))theme.logo=String(value.logo).slice(0,2800000);
    return theme;
  }
  function applyTheme(value) {
    const theme=cleanTheme(value);activeTheme=theme;if(!online&&!sample)ui.store.set("r6web.theme",theme);
    const accent="#"+[1,3,5].map(n=>Math.max(145,parseInt(theme.primary.slice(n,n+2),16)).toString(16)).join("");
    for(const [k,v] of Object.entries({school:theme.primary,accent,secondary:theme.secondary,gold:theme.secondary}))document.documentElement.style.setProperty(`--${k}`,v);
    document.querySelector(".brand h1").textContent=theme.name;
    let logo=document.querySelector(".brand .team-logo");
    if(theme.logo){if(!logo){logo=document.createElement("img");logo.className="team-logo";logo.alt="Team logo";document.querySelector(".brand").prepend(logo);}logo.src=theme.logo;}
    else logo?.remove();
    document.querySelector(".brand svg").toggleAttribute("hidden",!!theme.logo);
    document.body.style.background=`radial-gradient(ellipse at 92% 0%,${theme.primary}30,transparent 42%),var(--bg)`;
    return theme;
  }
  function themePage() {
    const theme=cleanTheme(activeTheme||defaultTheme);
    $("theme-content").innerHTML=`<div class="workspace-form"><label>Team preset<select id="theme-preset">${R6.rosters.map((t,i)=>`<option value="${i}">${esc(t.name)} · ${esc(t.school)}</option>`).join("")}</select></label><button class="btn primary" id="theme-use">Use this team's colors and logo</button></div><form id="theme-form"><label>Organization<input id="theme-name" maxlength="180" value="${esc(theme.name)}"></label><label>Team<input id="theme-team" maxlength="180" value="${esc(theme.team)}"></label><label>Primary color<input type="color" id="theme-primary" value="${theme.primary}"></label><label>Secondary color<input type="color" id="theme-secondary" value="${theme.secondary}"></label><label>Logo<input type="file" id="theme-logo" accept="image/png,image/jpeg,image/webp"></label><button class="btn primary">Save my theme</button></form><button class="btn" id="theme-reset">Restore the default theme</button><p class="dim">Your colors and logo apply throughout this workspace. Dark team colors are lightened for readable text.</p>`;
    $("theme-use").onclick=()=>safely(async()=>{const t=R6.rosters[Number($("theme-preset").value)];applyTheme({name:t.school||t.name,team:t.name,primary:t.color,secondary:t.color_alt,logo:t.logo_url});await save();themePage();});
    $("theme-form").onsubmit=e=>{e.preventDefault();safely(async()=>{
      let logo=theme.logo;const file=$("theme-logo").files[0];
      if(file){if(file.size>2*1024*1024)throw Error("Choose a logo smaller than 2 MB.");logo=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(file);});}
      applyTheme({name:$("theme-name").value,team:$("theme-team").value,primary:$("theme-primary").value,secondary:$("theme-secondary").value,logo});await save();notify("Team theme applied.");
    });};
    $("theme-reset").onclick=()=>safely(async()=>{applyTheme(defaultTheme);await save();themePage();});
  }
  const portable = () => ({...real,theme:activeTheme||defaultTheme,league:ui.currentLeague()});
  async function teamRequest(server,path,method="GET",data=null,token=null) {
    const response=await fetch(server.replace(/\/$/,"")+path,{method,headers:{...(data?{"Content-Type":"application/json"}:{}),...(token?{Authorization:"Bearer "+token}:{})},...(data?{body:JSON.stringify(data)}:{})});
    const value=await response.json();if(!response.ok)throw Error(value.error||"Shared team request failed.");return value;
  }
  function clearReport() {
    viewingAs="";sample=null;chosenSeason="current";chosenSeasonTeam="";revision++;
    ui.renderReport(R6.demo,{label:"Demo match — separate from saved stats",demo:true});
    for(const id of ["answer","operator-results","team-results","season-results","history-content"])if($(id))$(id).replaceChildren();
  }
  async function connect(server,token,remember) {
    if(pendingShared)throw Error("Wait for the shared save to finish.");
    const result=await teamRequest(server,"/workspace","GET",null,token);
    const incoming=result.workspace.version?validate(result.workspace):empty();
    if(!online){personal=portable();await write(personal);}
    real=incoming;online={server,token,revision:result.revision,name:result.name};
    ui.store.set("r6web.teamSession",remember?{server,token}:null);
    ui.store.set("r6web.teamServer",server);
    clearReport();applyTheme(result.workspace.theme||defaultTheme);ui.saveLeague(result.workspace.league||null);status();teamHubPage();
  }
  function teamHubPage() {
    const box = $("team-hub-content");
    if (sample) { box.innerHTML = '<p class="notice">Leave the demo season to manage your own member profiles or open a shared workspace.</p>'; return; }
    const people = real.directory || [];
    if (!people.some(p => p.id === viewingAs)) viewingAs = "";
    const downloadBase = R6.teamServerUrl?.startsWith("https://") ? profiles.serverUrl(R6.teamServerUrl).replace(/\/$/, "") : null;
    box.innerHTML = `<p class="notice">${online?`School workspace: ${esc(online.name)}. Everyone with an email at this school and the team PIN sees the same saved data. Refresh to load teammates' changes.`:"Personal workspace on this device. Sign in below to open shared school data. Personal and demo data stay separate."}</p>
      <p class="dim">School-PIN browser pilot: shared workspaces currently support up to 1.5 MB of data. Keep exported backups. Desktop password-login workspaces are separate.</p>
      ${downloadBase?`<p><a href="${esc(downloadBase)}/R6MatchStats-School-PIN-Pilot.html">Download the portable browser pilot</a> · <a href="${esc(downloadBase)}/SHA256SUMS.txt" target="_blank" rel="noopener">SHA-256 checksum</a></p>`:""}
      <p class="dim">Any address at the same school domain (for example @csp.edu) opens the same school workspace with its PIN. Email format is checked, not ownership or school affiliation. The captain creates the workspace and shares its PIN privately. Knowing an email address alone does not reveal the PIN.</p>
      ${online?'<button class="btn" id="team-refresh">Refresh shared workspace</button><button class="btn" id="team-logout">Sign out and return to personal</button>':`<form id="team-server-form"><label>Free team server URL<input id="team-server-url" type="url" required value="${esc(ui.store.get("r6web.teamServer",R6.teamServerUrl||""))}" placeholder="https://your-team-worker.workers.dev"></label><label>Your school email<input id="team-email" type="email" required placeholder="team@csp.edu" maxlength="254"></label><label>Team PIN<input id="team-code" type="password" autocomplete="off" maxlength="256"></label><label><input id="team-remember" type="checkbox"> Remember this login on this device for up to 12 hours</label><button class="btn primary">Sign in to school workspace</button><label>Team name (for a new workspace)<input id="team-new-name" maxlength="100"></label><button class="btn" type="button" id="team-create">Create school workspace and PIN</button><label>Saved recovery code<input id="team-recovery" type="password" autocomplete="off" maxlength="256"></label><button class="btn" type="button" id="team-recover">Reset PIN with recovery code</button></form><div id="team-credentials"></div><p class="dim">Use the same server URL on every computer. This connects to the free team-code service, separate from the desktop's password login. No data uploads until you sign in and save or explicitly import a backup.</p>`}
      <h3>${online?"Shared school profiles":"Profiles on this device"}</h3><p class="dim">Roles describe responsibilities and give everyone equal access. Choosing a profile changes Ask and all-match Operators, never the replay recorder or scoreboard. With no linked player, analytics use the replay recorder.</p>
      <label>Who are you viewing as?<select id="profile-view"><option value="">Team overview / replay recorder perspective</option>${people.map(p=>`<option value="${p.id}" ${p.id===viewingAs?"selected":""}>${esc(p.name)}</option>`).join("")}</select></label>
      ${table(people.map(p=>({Name:p.name,Player:p.player||"—",Roles:p.roles.map(r=>r==="Other"?p.custom_role:r).join(", ")})))}
      <label>Member to edit<select id="profile-edit"><option value="">Add a member</option>${people.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select></label><div id="profile-editor"></div>`;
    if(online){
      $("team-refresh").onclick=()=>safely(async()=>{
        if(pendingShared)throw Error("Wait for the shared save to finish.");
        if(!confirm("Load the server's latest workspace? Export a backup first if you have pending changes. Unsaved changes will be discarded."))return;
        await connect(online.server,online.token,!!ui.store.get("r6web.teamSession",null));notify("Shared school workspace refreshed.");
      });
      $("team-logout").onclick=()=>safely(async()=>{
        if(pendingShared)throw Error("Wait for the shared save to finish.");
        if(online.blocked&&!confirm("Pending changes have not reached the server. Export them first if needed. Return to personal workspace?"))return;
        await teamRequest(online.server,"/logout","POST",{},online.token).catch(()=>{});
        online=null;ui.store.set("r6web.teamSession",null);const previous=personal||empty();real={...validate(previous),theme:previous.theme,league:previous.league};personal=null;
        clearReport();applyTheme(real.theme||defaultTheme);ui.saveLeague(real.league||null);status();teamHubPage();
      });
    }else{
      const input=()=>({server:profiles.serverUrl($("team-server-url").value.trim()),email:$("team-email").value.trim()});
      $("team-server-form").onsubmit=e=>{e.preventDefault();safely(async()=>{
        const {server,email}=input(),remember=$("team-remember").checked;
        const result=await teamRequest(server,"/login","POST",{email,code:$("team-code").value.trim()});
        await connect(server,result.token,remember);notify("Signed in to shared school workspace.");
      });};
      const credentials=result=>{
        const box=$("team-credentials");box.replaceChildren();
        const text=`Team PIN: ${result.code}\nRecovery code: ${result.recovery}`;
        const guidance=document.createElement("p");guidance.textContent="Save the PIN privately for teammates. Keep the recovery code in a separate private backup: it can replace the PIN and sign out all devices. There is no email recovery.";
        const pre=document.createElement("pre");pre.textContent=text;
        const downloadButton=document.createElement("button");downloadButton.className="btn";downloadButton.textContent="Download private login backup";downloadButton.onclick=()=>download("R6-Team-Login-PRIVATE.txt",text,"text/plain");
        const dismiss=document.createElement("button");dismiss.className="btn";dismiss.textContent="I saved the codes";dismiss.onclick=()=>box.replaceChildren();
        box.append(guidance,pre,downloadButton,dismiss);$("team-code").value=result.code;$("team-recovery").value="";
      };
      $("team-create").onclick=()=>safely(async()=>{const {server,email}=input();credentials(await teamRequest(server,"/create","POST",{email,name:$("team-new-name").value}));});
      $("team-recover").onclick=()=>safely(async()=>{const {server,email}=input();credentials(await teamRequest(server,"/recover","POST",{email,recovery:$("team-recovery").value.trim()}));});
    }
    $("profile-view").onchange = () => {viewingAs=$("profile-view").value;revision++;status();notify("Viewing identity changed for this tab.");};
    const edit = () => {
      const id = $("profile-edit").value, person = people.find(p=>p.id===id) || {name:"",player:"",roles:["Player"],custom_role:""};
      $("profile-editor").innerHTML=`<form id="profile-form"><label>Display name<input id="profile-name" maxlength="100" required value="${esc(person.name)}"></label><label>Player username (optional for staff)<input id="profile-player" maxlength="100" value="${esc(person.player)}"></label><fieldset><legend>Roles</legend>${profiles.roles.map(r=>`<label><input type="checkbox" name="profile-role" value="${r}" ${person.roles.includes(r)?"checked":""}>${r}</label>`).join("")}</fieldset><label>Custom role when Other is selected<input id="profile-custom" maxlength="100" value="${esc(person.custom_role)}"></label><button class="btn primary">Save member</button></form>${id?'<button class="btn" id="profile-delete">Remove member profile</button>':""}`;
      $("profile-form").onsubmit=e=>{e.preventDefault();safely(async()=>{
        const bytes=crypto.getRandomValues(new Uint8Array(16));
        const person={id:id||Array.from(bytes,b=>b.toString(16).padStart(2,"0")).join(""),name:$("profile-name").value,player:$("profile-player").value,custom_role:$("profile-custom").value,roles:[...box.querySelectorAll('[name="profile-role"]:checked')].map(n=>n.value)};
        real.directory=profiles.directory([...people.filter(p=>p.id!==id),person]);await save();teamHubPage();notify(online?"Member profile saved to the school workspace.":"Member profile saved on this device.");
      });};
      if(id)$("profile-delete").onclick=()=>safely(async()=>{
        if(!confirm("Remove this profile? Match stats are kept."))return;
        real.directory=people.filter(p=>p.id!==id);if(viewingAs===id)viewingAs="";await save();teamHubPage();
      });
    };
    $("profile-edit").onchange=edit;edit();
  }
  const pages={"team-hub":teamHubPage,ask:askPage,history,teams:teamsPage,operators:operatorsPage,season:seasonPage,theme:themePage};
  document.addEventListener("r6tab",e=>{activeTab=e.detail;if(ready&&pages[activeTab])safely(pages[activeTab]);});
  document.querySelector(".tabs").addEventListener("keydown",e=>{
    if(!["ArrowLeft","ArrowRight","Home","End"].includes(e.key))return;
    const tabs=[...document.querySelectorAll('.tabs [role="tab"]')];let i=tabs.indexOf(document.activeElement);
    i=e.key==="Home"?0:e.key==="End"?tabs.length-1:(i+(e.key==="ArrowRight"?1:-1)+tabs.length)%tabs.length;
    e.preventDefault();tabs[i].focus();tabs[i].click();
  });
  $("workspace-demo").onclick=()=>safely(async()=>{
    if(sample){sample=null;ui.renderReport(R6.demo,{label:"Demo match",demo:true});}
    else{sample={version:1,matches:structuredClone(R6.demoSeason),teams:structuredClone(R6.demoTeam)};openSaved(sample.matches.at(-1));}
    revision++;status();if(pages[activeTab])await pages[activeTab]();
  });
  $("workspace-export").onclick=()=>download("R6MatchStats-Workspace.json",JSON.stringify(portable(),null,2));
  $("workspace-import").onclick=()=>$("workspace-file").click();
  $("workspace-file").onchange=()=>safely(async()=>{
    if(sample)throw Error("Leave the demo before importing a workspace.");
    const file=$("workspace-file").files[0];if(!file)return;if(file.size>150*1024*1024)throw Error("Choose a workspace backup under 150 MB.");
    const raw=JSON.parse(await file.text()),incoming=validate(raw);
    JSON.parse((await ui.bridge()).workspace(JSON.stringify(incoming)));
    if((real.matches.length||online)&&!confirm(online?"Replace the shared school workspace with this backup? Every teammate will see the replacement. Export the current workspace first if needed.":"Replace this browser's saved workspace with the backup? Export the current workspace first if you need it."))return;
    real=incoming;viewingAs="";sample=null;if(raw.theme)applyTheme(raw.theme);if(raw.league)ui.saveLeague(raw.league);await save();notify("Workspace restored.");if(pages[activeTab])await pages[activeTab]();$("workspace-file").value="";
  });
  window.R6Workspace={state,isShared:()=>!!online,isDemo:()=>!!sample,save,engine,notify,download};
  applyTheme(ui.store.get("r6web.theme",defaultTheme));
  read().then(value=>{if(value){real=validate(value);if(value.theme)applyTheme(value.theme);if(value.league)ui.saveLeague(value.league);}}).catch(e=>notify(e.message,true)).finally(()=>{
    ready=true;status();enhanceReport(currentReport,currentMeta);
    activeTab=ui.store.get("r6web.tab","report");if(pages[activeTab])safely(pages[activeTab]);
    const remembered=ui.store.get("r6web.teamSession",null);
    if(remembered)safely(async()=>{try{await connect(profiles.serverUrl(remembered.server),remembered.token,true);ui.showTab("team-hub");}catch(error){ui.store.set("r6web.teamSession",null);throw error;}});
  });
})();
