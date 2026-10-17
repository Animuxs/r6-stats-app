import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import worker from './worker.mjs';
const sql=new DatabaseSync(':memory:');sql.exec(readFileSync(new URL('./migrations/0001_team.sql',import.meta.url),'utf8'));
const DB={prepare(query){return {values:[],bind(...values){this.values=values;return this;},async first(){return sql.prepare(query).get(...this.values)||null;},async run(){const r=sql.prepare(query).run(...this.values);return {meta:{changes:Number(r.changes)}};}};},async batch(items){sql.exec('BEGIN');try{const result=[];for(const item of items)result.push(await item.run());sql.exec('COMMIT');return result;}catch(e){sql.exec('ROLLBACK');throw e;}}};
let address=0;
async function call(path,method='GET',data=null,token=null){
  const response=await worker.fetch(new Request('https://test.example'+path,{method,headers:{'CF-Connecting-IP':'127.0.0.'+(++address),...(data?{'Content-Type':'application/json'}:{}),...(token?{Authorization:'Bearer '+token}:{})},...(data?{body:JSON.stringify(data)}:{})}),{DB});
  return {status:response.status,...await response.json()};
}
const first=await call('/create','POST',{email:'coach@csp.edu',name:'CSP'});assert.equal(first.status,201);assert.match(first.code,/^[a-f0-9]{24}$/);
const second=await call('/create','POST',{email:'coach@other.edu',name:'Other'});assert.equal(second.status,201);
const a=await call('/login','POST',{email:'coach@csp.edu',code:first.code});assert.equal(a.status,200);
const b=await call('/login','POST',{email:'player@csp.edu',code:first.code});assert.equal(b.status,200);
const other=await call('/login','POST',{email:'coach@other.edu',code:second.code});assert.equal(other.status,200);
assert.equal((await call('/workspace')).status,401);
const value={version:1,matches:[],teams:{Varsity:['Kanto']},directory:[],token:a.token,password:'exclude-me',recovery:first.recovery};
assert.equal((await call('/workspace','PUT',{revision:0,workspace:value},a.token)).status,200);
const shared=await call('/workspace','GET',null,b.token);assert.deepEqual(shared.workspace.teams,value.teams);assert.equal(shared.revision,1);
assert.equal(shared.workspace.token,undefined);assert.equal(shared.workspace.password,undefined);assert.equal(shared.workspace.recovery,undefined);
assert.deepEqual((await call('/workspace','GET',null,other.token)).workspace,{});
assert.equal((await call('/workspace','PUT',{revision:0,workspace:{...value,teams:{}}},b.token)).status,409);
assert.deepEqual((await call('/workspace','GET',null,a.token)).workspace.teams,value.teams);
assert.equal((await call('/logout','POST',{},b.token)).status,200);assert.equal((await call('/workspace','GET',null,b.token)).status,401);
const reset=await call('/recover','POST',{email:'coach@csp.edu',recovery:first.recovery});assert.equal(reset.status,200);
assert.equal((await call('/workspace','GET',null,a.token)).status,401);
assert.equal((await call('/login','POST',{email:'coach@csp.edu',code:first.code})).status,401);
assert.equal((await call('/recover','POST',{email:'coach@csp.edu',recovery:first.recovery})).status,401);
const fresh=await call('/login','POST',{email:'coach@csp.edu',code:reset.code});assert.equal(fresh.status,200);
assert.deepEqual((await call('/workspace','GET',null,fresh.token)).workspace.teams,value.teams);
assert.equal((await call('/workspace','PUT',{revision:1,workspace:{...value,matches:'bad'}},fresh.token)).status,400);
sql.prepare('UPDATE sessions SET expires=0 WHERE team=(SELECT id FROM teams WHERE email=?)').run('@csp.edu');assert.equal((await call('/workspace','GET',null,fresh.token)).status,401);
const dump=JSON.stringify({teams:sql.prepare('SELECT * FROM teams').all(),sessions:sql.prepare('SELECT * FROM sessions').all()});
for(const secret of [first.code,first.recovery,reset.code,reset.recovery,other.token,fresh.token])assert.ok(!dump.includes(secret));
for(let i=0;i<12;i++){const result=await call('/login','POST',{email:'missing@school.edu',code:'bad'});assert.equal(result.status,i<10?401:429);}
assert.equal((await call('/create','POST',{email:'coach@csp.edu.evil.com',name:'No'})).status,400);
console.log('Shared school service: lifecycle, two sessions, isolation, stale saves, recovery, expiry, credential exclusion and rate limits passed.');
