/* Match-day planning stays in the portable workspace, separate from demo data. */
(() => {
  "use strict";
  const ctx=window.R6Workspace, ui=window.R6UI, $=id=>document.getElementById(id), esc=ui.esc;
  if(!ctx)return;
  let recent=[], revision=0;
  function plan(){return ctx.state().matchday ||= ctx.isDemo()?structuredClone(R6.matchdayDemo):{fixtures:[],availability:{},notes:""};}
  function roster(){return [...new Set(Object.values(ctx.state().teams||{}).flat())];}
  function validate(value){
    if(!value || !Array.isArray(value.fixtures) || value.fixtures.length>200 || !value.availability || typeof value.availability!=="object" || typeof value.notes!=="string" || value.notes.length>10000)throw Error("Invalid match-day backup.");
    const ids=new Set();
    for(const f of value.fixtures){
      if(!f || ["id","opponent","when","timezone","kind","team"].some(k=>typeof f[k]!=="string"||f[k].length>180) || !f.id || ids.has(f.id)||!f.opponent.trim()||!Number.isFinite(Date.parse(f.when)))throw Error("A fixture is incomplete or has an invalid date.");
      ids.add(f.id);
    }
    if(Object.keys(value.availability).length>200 || Object.entries(value.availability).some(([n,s])=>n.length>180||!["Unconfirmed","Confirmed","Unavailable"].includes(s)))throw Error("Invalid availability values.");
    return {version:1,fixtures:value.fixtures,availability:value.availability,notes:value.notes};
  }
  function localDay(){const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;}
  function panel(){
    const p=plan(),demo=ctx.isDemo();
    const fixtures=p.fixtures.filter(f=>f.when.slice(0,10)>=localDay()).sort((a,b)=>a.when.localeCompare(b.when)).slice(0,3);
    const upcoming=fixtures.map(f=>`<article class="md-fixture"><small>${esc(f.when.slice(0,16).replace("T"," · "))} · ${esc(f.timezone)}</small><b>vs ${esc(f.opponent)}</b><small>${esc(f.team)} · ${esc(f.kind)}</small></article>`).join("")||'<p class="md-empty">No upcoming matches entered.</p>';
    const form=recent.slice(0,5).map(m=>`<div class="md-recent"><b class="md-result">${m.won===1?"W":m.won===0?"L":"—"}</b><span>${esc(m.map||"Unknown map")}<small>${esc((m.played_at||"").slice(0,10))}</small></span><span>${m.score0}–${m.score1}</span></div>`).join("")||'<p class="md-empty">Confirm matches to see recent form.</p>';
    const people=roster().slice(0,10).map(n=>`<div class="md-person"><span>${esc(n)}</span><small>${esc(p.availability[n]||"Unconfirmed")}</small></div>`).join("")||'<p class="md-empty">Save a team to see its roster.</p>';
    $("matchday-panel").innerHTML=`<div class="matchday-panel"><section><h3>Upcoming matches</h3><p class="md-label">${demo?"Example schedule · demo only":"Manually entered schedule"}</p>${upcoming}</section><section><h3>Recent form</h3><p class="md-label">${demo?"Made-up demo results":"Confirmed matches"}</p>${form}</section><section><h3>Roster availability</h3><p class="md-label">${demo?"Sample availability":"Manual confirmations · never inferred"}</p>${people}</section><section><h3>Match prep</h3><p class="md-notes">${p.notes?esc(p.notes).replaceAll("\n","<br>"):"No preparation notes saved."}</p></section></div><button class="btn small" id="md-manage">Manage match-day workspace</button>`;
    $("md-manage").onclick=()=>ui.showTab("matchday");
    document.body.classList.toggle("matchday-off",!ui.store.get("r6web.matchdayPanel",true));
  }
  async function refresh(){
    const token=++revision;recent=[];panel();
    if(ctx.state().matches.length){try{const info=await ctx.engine("overview");if(token===revision){recent=info.matches;panel();}}catch(e){ctx.notify(e.message,true);}}
  }
  async function safely(fn){try{await fn();}catch(e){ctx.notify(e.message||String(e),true);}}
  function page(){
    const p=plan();
    $("matchday-content").innerHTML=`<p class="dim">Plan fixtures and record availability explicitly. ${ctx.isDemo()?"Example data stays in the demo workspace.":"Saved on this device; export a backup to move your planning data."}</p><label><input type="checkbox" id="md-visible" ${ui.store.get("r6web.matchdayPanel",true)?"checked":""}> Show match-day panels</label><form id="md-fixture" class="md-form"><label>Opponent<input id="md-opponent" maxlength="180" required></label><label>Your team<input id="md-team" maxlength="180"></label><label>Match date<input id="md-date" type="date" value="${localDay()}" required></label><label>Start time<input id="md-time" type="time" value="19:00" required></label><label>Time zone label<input id="md-zone" value="Central Time" maxlength="180"></label><label>Match type<select id="md-kind"><option>League · BO3</option><option>League · BO1</option><option>Practice · BO3</option><option>Tournament</option><option>Other</option></select></label><div><button class="btn primary">Save fixture</button></div></form><div id="md-fixtures">${p.fixtures.map((f,i)=>`<div class="saved-match"><span>${esc(f.when.slice(0,16).replace("T"," · "))} · ${esc(f.opponent)}</span><button class="btn small" data-remove-fixture="${i}">Remove fixture</button></div>`).join("")}</div><h3>Roster availability & preparation</h3><form id="md-notes" class="md-form">${roster().map((n,i)=>`<label>${esc(n)}<select data-availability="${i}">${["Unconfirmed","Confirmed","Unavailable"].map(s=>`<option ${(p.availability[n]||"Unconfirmed")===s?"selected":""}>${s}</option>`).join("")}</select></label>`).join("")}<label class="md-span">Preparation notes<textarea id="md-prep" maxlength="10000">${esc(p.notes)}</textarea></label><div><button class="btn primary">Save availability and notes</button></div></form><div class="exports"><button class="btn" id="md-export">Export match-day JSON</button><label>Restore match-day JSON<input id="md-import" type="file" accept=".json,application/json"></label></div>`;
    $("md-visible").onchange=()=>{ui.store.set("r6web.matchdayPanel",$("md-visible").checked);panel();};
    $("md-fixture").onsubmit=e=>{e.preventDefault();safely(async()=>{
      const next=structuredClone(plan());next.fixtures.push({id:crypto.randomUUID(),opponent:$("md-opponent").value.trim(),team:$("md-team").value.trim(),when:`${$("md-date").value}T${$("md-time").value}:00`,timezone:$("md-zone").value,kind:$("md-kind").value});
      ctx.state().matchday=validate(next);await ctx.save();page();
    });};
    $("md-fixtures").querySelectorAll("[data-remove-fixture]").forEach(b=>b.onclick=()=>safely(async()=>{plan().fixtures.splice(Number(b.dataset.removeFixture),1);await ctx.save();page();}));
    $("md-notes").onsubmit=e=>{e.preventDefault();safely(async()=>{const names=roster();$("md-notes").querySelectorAll("[data-availability]").forEach(input=>{plan().availability[names[Number(input.dataset.availability)]]=input.value;});plan().notes=$("md-prep").value;await ctx.save();ctx.notify("Availability and preparation notes saved.");});};
    $("md-export").onclick=()=>ctx.download("R6MatchStats-Matchday.json",JSON.stringify(plan(),null,2));
    $("md-import").onchange=()=>safely(async()=>{const file=$("md-import").files[0];if(!file)return;if(file.size>1000000)throw Error("Choose a match-day backup under 1 MB.");const next=validate(JSON.parse(await file.text()));ctx.state().matchday=next;await ctx.save();page();ctx.notify("Match-day planning restored.");});
  }
  document.addEventListener("r6workspace",refresh);
  document.addEventListener("r6tab",e=>{if(e.detail==="matchday")page();});
  refresh();if(ui.store.get("r6web.tab","report")==="matchday")page();
})();
