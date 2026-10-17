/* Free-tier, app-owned team-code service. No external identity or email service.
 * Deploy separately; never put a database, code or session in the public repository.
 */
const encoder = new TextEncoder();
const now = () => Math.floor(Date.now()/1000);
const random = (bytes=24) => Array.from(crypto.getRandomValues(new Uint8Array(bytes)), b=>b.toString(16).padStart(2,'0')).join('');
const digest = async value => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',encoder.encode(value))),b=>b.toString(16).padStart(2,'0')).join('');
class Problem extends Error { constructor(message,status=400){super(message);this.status=status;} }
export function email(value) {
  if(typeof value!=='string')throw new Problem('Enter a school email ending in .edu.');
  value=value.trim().toLowerCase();
  if(value.length>254||! /^[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+edu$/.test(value))throw new Problem('Enter a school email ending in .edu. Format only; ownership is not verified.');
  return "@"+value.split("@")[1];
}
export function workspace(value) {
  if(!value||value.version!==1||!Array.isArray(value.matches)||value.matches.length>3000||!value.teams||Array.isArray(value.teams)||typeof value.teams!=='object')throw new Problem('Invalid portable browser workspace.');
  for(const m of value.matches)if(!m||typeof m.key!=='string'||!m.data||!Array.isArray(m.data.players)||!Array.isArray(m.data.rounds)||!Array.isArray(m.players)||m.players.some(p=>typeof p!=='string'))throw new Problem('Invalid confirmed match.');
  // Allowlist portable data. Credentials and each person's viewing identity never enter a backup.
  const result={};
  for(const key of ['version','matches','teams','seasonTeams','removedSeasonTeams','seasonTeamMatches','matchday','directory','theme','league'])if(value[key]!==undefined)result[key]=value[key];
  const payload=JSON.stringify(result);
  // D1 has a 2 MB row limit; leave room for UTF-8 and account metadata.
  if(encoder.encode(payload).length>1_500_000)throw new Problem('Shared workspace exceeds the 1.5 MB pilot limit. Keep a local backup and fewer confirmed matches.',413);
  return payload;
}
async function body(request){
  if(Number(request.headers.get('content-length')||0)>1_600_000)throw new Problem('Request too large.',413);
  const reader=request.body?.getReader();if(!reader)throw new Problem('JSON body required.');
  let size=0,chunks=[];
  while(true){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>1_600_000){await reader.cancel();throw new Problem('Request too large.',413);}chunks.push(value);}
  const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
  try{return JSON.parse(new TextDecoder().decode(bytes));}catch{throw new Problem('Invalid JSON.');}
}
async function limit(db,key,max=10){
  const timestamp=now();
  const row=await db.prepare('INSERT INTO attempts(key,count,until) VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=CASE WHEN until<=? THEN 1 ELSE count+1 END, until=CASE WHEN until<=? THEN excluded.until ELSE until END RETURNING count').bind(key,timestamp+900,timestamp,timestamp).first();
  if(row.count>max)throw new Problem('Too many attempts. Try again in 15 minutes.',429);
}
async function account(request,db){
  const token=request.headers.get('authorization')?.match(/^Bearer ([a-f0-9]{64})$/)?.[1];
  if(!token)throw new Problem('Sign in to the team workspace.',401);
  const hashed=await digest(token);
  const row=await db.prepare('SELECT t.* FROM teams t JOIN sessions s ON s.team=t.id WHERE s.token=? AND s.expires>?').bind(hashed,now()).first();
  if(!row)throw new Problem('Session expired. Sign in again.',401);
  return {...row,session:hashed};
}
export default {
  async fetch(request,env){
    const url=new URL(request.url), path=url.pathname;
    // Native/standalone file origins are opaque. Only explicit bearer requests can read data;
    // cookies are neither accepted nor issued, so arbitrary origins cannot borrow a login.
    const headers={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'Authorization, Content-Type','Access-Control-Allow-Methods':'GET, POST, PUT, OPTIONS','Cache-Control':'no-store','Content-Type':'application/json','X-Content-Type-Options':'nosniff'};
    const json=(value,status=200)=>new Response(JSON.stringify(value),{status,headers});
    if(url.protocol!=='https:'&&!['localhost','127.0.0.1','[::1]'].includes(url.hostname))return json({error:'HTTPS required.'},400);
    if(request.method==='OPTIONS')return new Response(null,{status:204,headers});
    try{
      if(path==='/health'&&request.method==='GET')return json({service:'r6-team-workspace',version:1,maxWorkspaceBytes:1500000});
      const db=env.DB;
      const client=await digest(request.headers.get('CF-Connecting-IP')||'local');
      if(path==='/create'&&request.method==='POST'){
        await limit(db,'create:'+client,3);const input=await body(request),address=email(input.email);
        const name=typeof input.name==='string'?input.name.trim():'';
        if(!name||name.length>100)throw new Problem('Enter a team name of 1–100 characters.');
        const id=random(16),code=random(12),recovery=random(24);
        const inserted=await db.prepare('INSERT INTO teams(id,email,name,code_hash,recovery_hash) VALUES(?,?,?,?,?) ON CONFLICT(email) DO NOTHING').bind(id,address,name,await digest(id+':'+code),await digest(id+':'+recovery)).run();
        if(!inserted.meta.changes)throw new Problem('This address already has a workspace. Ask its captain for the team code, or use the saved recovery code.',409);
        return json({name,email:address,code,recovery},201);
      }
      if((path==='/login'||path==='/recover')&&request.method==='POST'){
        const input=await body(request),address=email(input.email);
        await limit(db,'ip:'+client);await limit(db,'email:'+await digest(address));
        const row=await db.prepare('SELECT id,code_hash,recovery_hash FROM teams WHERE email=?').bind(address).first();
        const secret=path==='/login'?input.code:input.recovery;
        const hashed=await digest((row?.id||'missing')+':'+(typeof secret==='string'&&secret.length<=256?secret:''));
        if(!row||hashed!==(path==='/login'?row.code_hash:row.recovery_hash))throw new Problem('Invalid email or team credentials.',401);
        if(path==='/recover'){
          const code=random(12),recovery=random(24);
          const changed=await db.batch([
            db.prepare('UPDATE teams SET code_hash=?,recovery_hash=? WHERE id=? AND recovery_hash=?').bind(await digest(row.id+':'+code),await digest(row.id+':'+recovery),row.id,hashed),
            db.prepare('DELETE FROM sessions WHERE team=? AND EXISTS(SELECT 1 FROM teams WHERE id=? AND recovery_hash=?)').bind(row.id,row.id,await digest(row.id+':'+recovery))
          ]);
          if(!changed[0].meta.changes)throw new Problem('Recovery code already used.',409);
          return json({code,recovery});
        }
        const token=random(32);
        const inserted=await db.prepare('INSERT INTO sessions(token,team,expires) SELECT ?,id,? FROM teams WHERE id=? AND code_hash=?').bind(await digest(token),now()+43200,row.id,hashed).run();
        if(!inserted.meta.changes)throw new Problem('Team code changed. Sign in again.',401);
        return json({token,expires:now()+43200});
      }
      const team=await account(request,db);
      if(path==='/logout'&&request.method==='POST'){
        await db.prepare('DELETE FROM sessions WHERE token=?').bind(team.session).run();return json({ok:true});
      }
      if(path==='/workspace'&&request.method==='GET')return json({name:team.name,email:team.email,revision:team.revision,workspace:JSON.parse(team.workspace)});
      if(path==='/workspace'&&request.method==='PUT'){
        const input=await body(request);
        if(!Number.isSafeInteger(input.revision)||input.revision<0)throw new Problem('Workspace revision required.');
        const payload=workspace(input.workspace);
        // Recheck the session inside the write, including logout/recovery racing a request.
        const result=await db.prepare('UPDATE teams SET workspace=?,revision=revision+1 WHERE id=? AND revision=? AND EXISTS(SELECT 1 FROM sessions WHERE token=? AND team=teams.id AND expires>?)').bind(payload,team.id,input.revision,team.session,now()).run();
        if(!result.meta.changes)throw new Problem('Session changed or a teammate saved first. Keep a backup, then refresh before saving.',409);
        return json({revision:input.revision+1});
      }
      return json({error:'Route not found.'},404);
    }catch(error){return json({error:error instanceof Problem?error.message:'Team service unavailable. Try again later.'},error instanceof Problem?error.status:503);}
  }
};
