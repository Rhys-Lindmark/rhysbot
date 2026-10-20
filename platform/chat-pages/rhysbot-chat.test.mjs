import test from 'node:test';import assert from 'node:assert/strict';import {DatabaseSync} from 'node:sqlite';import {readFileSync} from 'node:fs';import {createChatHandler,history,prompt,charge,standbyBaseMicros,serviceAuthorized,readSSE} from './rhysbot-chat.js';
import {createVoiceHandler} from './rhysbot-voice.js';
function db(){const sql=new DatabaseSync(':memory:');sql.exec("CREATE TABLE rb_rates(client TEXT,hour TEXT,uses INTEGER,PRIMARY KEY(client,hour));CREATE TABLE rb_spend(period TEXT PRIMARY KEY,micros INTEGER CHECK(micros BETWEEN 0 AND 10000000));CREATE TABLE rb_sessions(token_hash TEXT,client TEXT,expires INTEGER);CREATE VIRTUAL TABLE rb_sources USING fts5(id UNINDEXED,title,kind UNINDEXED,url UNINDEXED,attribution UNINDEXED,text);INSERT INTO rb_sources VALUES('source','Rhys Roote','substack','https://rhyslindmark.substack.com/p/example','author','SOURCE_ONLY_MARKER Rhys founded Roote.');");return {prepare(query){let args=[];return {bind(...v){args=v;return this},async first(){return sql.prepare(query).get(...args)},async all(){return {results:sql.prepare(query).all(...args)}},async run(){sql.prepare(query).run(...args);return {success:true}}}}}}
const secret='s'.repeat(48), envBase={RHYSBOT_TRAIN_BRIDGE_SECRET:secret,RHYSBOT_CLIENT_SALT:'a'.repeat(40)};
test('history is bounded, roles fixed and special delimiters defanged',()=>{assert.throws(()=>history({messages:[{role:'system',content:'evil'}]}));assert.throws(()=>history({messages:[{role:'user',content:'x'.repeat(12001)}]}));assert.ok(!history({messages:[{role:'user',content:'<|system|>'}]})[0].content.includes('<|'))});
test('baseline prompt receives no retrieved sources',()=>{const source=[{text:'SOURCE_ONLY_MARKER'}],messages=[{role:'user',content:'Who is Rhys?'}];assert.ok(prompt(messages,source,true).includes('SOURCE_ONLY_MARKER'));assert.ok(!prompt(messages,source,false).includes('SOURCE_ONLY_MARKER'))});
test('durable monthly cap refuses rather than overspending',async()=>{const env={RHYSBOT_DB:db()};await charge(env,'one',9995000);await charge(env,'two',5000);await assert.rejects(charge(env,'three',5000),/monthly_budget/)});
test('private service authorization is exact; empty/malformed keys never authorize',async()=>{assert.equal(await serviceAuthorized(new Request('https://a',{headers:{Authorization:'Bearer '+secret}}),envBase),true);assert.equal(await serviceAuthorized(new Request('https://a',{headers:{Authorization:'Bearer bad'}}),envBase),false);assert.equal(await serviceAuthorized(new Request('https://a'),{}),false)});
test('side-by-side streams preserve independent history and only Rhys gets corpus',async()=>{const captured=[],work=[];const env={...envBase,RHYSBOT_DB:db(),AI:{async run(model,body){captured.push(body.prompt);return new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('data: {"choices":[{"delta":{"content":"Hello"}}]}\n\ndata: [DONE]\n\n'));c.close()}})}}};const messages=[{role:'user',content:'Who is Rhys?'},{role:'assistant',content:'LEFT_HISTORY'},{role:'user',content:'Explain Roote.'}],qwen_messages=[messages[0],{role:'assistant',content:'RIGHT_HISTORY'},messages[2]];const request=new Request('https://ai.rhyslindmark.com/api/rhysbot-chat',{method:'POST',headers:{Origin:'https://ai.rhyslindmark.com','CF-Connecting-IP':'127.0.0.1',Authorization:'Bearer '+secret},body:JSON.stringify({mode:'compare',messages,qwen_messages})});const response=await createChatHandler()({request,env,waitUntil:p=>work.push(p)});const text=await response.text();await Promise.all(work);assert.equal(response.status,200);assert.ok(text.includes('"channel":"rhys"'));assert.ok(text.includes('"channel":"qwen"'));assert.equal(captured.length,2);assert.ok(captured[0].includes('LEFT_HISTORY'));assert.ok(!captured[0].includes('RIGHT_HISTORY'));assert.ok(captured[0].includes('SOURCE_ONLY_MARKER'));assert.ok(captured[1].includes('RIGHT_HISTORY'));assert.ok(!captured[1].includes('LEFT_HISTORY'));assert.ok(!captured[1].includes('SOURCE_ONLY_MARKER'))});
test('wrong origin and different comparison questions never call AI',async()=>{let calls=0;const env={...envBase,RHYSBOT_DB:db(),AI:{run(){calls++}}};const handler=createChatHandler();const r=await handler({request:new Request('https://ai.rhyslindmark.com/api/rhysbot-chat',{method:'POST',headers:{Origin:'https://evil.test','CF-Connecting-IP':'1.2.3.4'},body:JSON.stringify({messages:[{role:'user',content:'Hello'}],mode:'compare'})}),env});assert.equal(r.status,403);const mismatch=await handler({request:new Request('https://ai.rhyslindmark.com/api/rhysbot-chat',{method:'POST',body:JSON.stringify({messages:[{role:'user',content:'One'}],qwen_messages:[{role:'user',content:'Two'}],mode:'compare'})}),env});assert.equal(mismatch.status,400);assert.equal(calls,0)});

test('provider numeric and boolean response tokens survive streaming',async()=>{const frames=[{response:2},{response:0},{response:1},{response:9},{response:' ['},{response:1},{response:'] and '},{response:4},{response:2},{response:'. '},{response:false},{response:null},{response:{ignored:true}}];const stream=new ReadableStream({start(c){const data=frames.map(x=>'data: '+JSON.stringify(x)+'\n\n').join('')+'data: [DONE]\n\n';c.enqueue(new TextEncoder().encode(data));c.close()}});let text='';for await(const chunk of readSSE(stream))text+=chunk;assert.equal(text,'2019 [1] and 42. false')});

function voiceDB(){const sql=new DatabaseSync(':memory:');sql.exec(readFileSync(new URL('../chat-schema.sql',import.meta.url),'utf8'));return {sql,prepare(query){let args=[];return {bind(...v){args=v;return this},async first(){return sql.prepare(query).get(...args)},async run(){sql.prepare(query).run(...args);return {success:true}}}}};}
function voiceRequest(body,ip='127.0.0.1'){return new Request('https://ai.rhyslindmark.com/api/rhysbot-voice',{method:'POST',headers:{Origin:'https://ai.rhyslindmark.com','CF-Connecting-IP':ip,Authorization:'Bearer '+secret},body:JSON.stringify(body)});}
const voiceId='12345678-1234-4234-8234-123456789abc';
test('voice start and resume reserve once; status and recovered audio never charge again',async()=>{
  const database=voiceDB(),calls=[],audio=new Uint8Array(100),sum=Buffer.from(await crypto.subtle.digest('SHA-256',audio)).toString('hex');
  const env={...envBase,RHYSBOT_DB:database,RHYSBOT_PLATFORM_URL:'https://example.modal.run'};
  const handler=createVoiceHandler({fetcher:async(url,init)=>{calls.push({path:url.pathname,body:JSON.parse(init.body)});return url.pathname==='/voice'?Response.json({state:'queued'},{status:202}):new Response(audio,{headers:{'Content-Type':'audio/wav','X-Rhysbot-Audio-Bytes':'100','X-Rhysbot-Audio-SHA256':sum}});}});
  const start={text:'A complete paragraph. '.repeat(40),request_id:voiceId};
  for(let i=0;i<2;i++)assert.equal((await handler({request:voiceRequest(start),env})).status,202);
  for(let i=0;i<2;i++){const r=await handler({request:voiceRequest({action:'status',request_id:voiceId}),env});assert.equal(r.status,200);assert.equal((await r.arrayBuffer()).byteLength,100);assert.equal(r.headers.get('X-Rhysbot-Audio-SHA256'),sum);}
  assert.equal(database.sql.prepare('SELECT micros FROM rb_spend').get().micros,500000);assert.equal(calls[0].body.request_id,calls[3].body.request_id);
  const before=calls.length;assert.equal((await handler({request:voiceRequest({...start,text:'Changed'}),env})).status,409);assert.equal((await handler({request:voiceRequest({action:'status',request_id:voiceId},'1.2.3.4'),env})).status,404);assert.equal(calls.length,before);
});
test('voice validation, missing authorization and exhausted budget never dispatch',async()=>{
  const database=voiceDB(),env={...envBase,RHYSBOT_DB:database,RHYSBOT_PLATFORM_URL:'https://example.modal.run'};let dispatched=0;
  const handler=createVoiceHandler({fetcher:async()=>{dispatched++;throw Error('Must not dispatch');}});
  for(const body of [{text:'x'.repeat(2001),request_id:voiceId},{text:'fine',request_id:'-'.repeat(36)},{request_id:voiceId,action:'bad'}])assert.equal((await handler({request:voiceRequest(body),env})).status,400);
  assert.equal((await handler({request:voiceRequest({action:'status',request_id:voiceId}),env})).status,404);
  const unauthorized=voiceRequest({text:'Valid',request_id:voiceId});unauthorized.headers.delete('Authorization');assert.equal((await handler({request:unauthorized,env})).status,403);
  await charge(env,'operator',10000000);assert.equal((await handler({request:voiceRequest({text:'Valid',request_id:voiceId}),env})).status,429);
  assert.equal(dispatched,0);assert.equal(database.sql.prepare('SELECT state FROM rb_voice').get().state,'blocked');
});
test('voice download rejects truncated provider audio and resume reuses reservation after transport loss',async()=>{
  const database=voiceDB(),env={...envBase,RHYSBOT_DB:database,RHYSBOT_PLATFORM_URL:'https://example.modal.run'};let calls=0;
  const handler=createVoiceHandler({fetcher:async(url)=>{calls++;if(calls===1)throw Error('Connection lost after dispatch');return url.pathname==='/voice'?Response.json({state:'queued'},{status:202}):new Response(new Uint8Array(50),{headers:{'Content-Type':'audio/wav','X-Rhysbot-Audio-Bytes':'100','X-Rhysbot-Audio-SHA256':'a'.repeat(64)}});}});
  const body={text:'Valid',request_id:voiceId};assert.equal((await handler({request:voiceRequest(body),env})).status,503);assert.equal((await handler({request:voiceRequest(body),env})).status,202);
  const r=await handler({request:voiceRequest({action:'status',request_id:voiceId}),env});await assert.rejects(()=>r.arrayBuffer(),/incomplete_voice/);assert.equal(database.sql.prepare('SELECT micros FROM rb_spend').get().micros,500000);
});

test('voice acknowledgement sets the browser session cookie and status reuses it without CAPTCHA',async()=>{
  const database=voiceDB(),env={...envBase,RHYSBOT_DB:database,RHYSBOT_PLATFORM_URL:'https://example.modal.run',RHYSBOT_TURNSTILE_SECRET:'test-only'};let challenges=0,providers=0;
  const handler=createVoiceHandler({fetcher:async(url)=>{if(String(url).includes('siteverify')){challenges++;return Response.json({success:true,hostname:'ai.rhyslindmark.com',action:'rhysbot_chat'});}providers++;return Response.json({state:providers===1?'queued':'speaking'},{status:202});}});
  const request=voiceRequest({text:'A browser reply.',request_id:voiceId,verification_token:'test-verification'});request.headers.delete('Authorization');
  const started=await handler({request,env});assert.equal(started.status,202);const cookie=started.headers.get('Set-Cookie');assert.match(cookie,/HttpOnly/);assert.match(cookie,/SameSite=Strict/);
  const poll=voiceRequest({action:'status',request_id:voiceId});poll.headers.delete('Authorization');poll.headers.set('Cookie',cookie.split(';')[0]);const response=await handler({request:poll,env});assert.equal(response.status,202);assert.equal((await response.json()).state,'speaking');assert.equal(challenges,1);assert.equal(database.sql.prepare('SELECT micros FROM rb_spend').get().micros,500000);
});

test('retrieval keeps an elliptical follow-up on topic and explicit medium requests are honored',async()=>{
  const {retrievalPlan,retrieve}=await import('./rhysbot-retrieval.js');const database=db();
  const fixtures=[['hee','Why Democracy Is Dying','hee_transcript','https://example.com/hee','Rhys narration','Democracy and political governance need new institutions.'],['pod','New power','podcast_transcript','https://example.com/podcast','Rhys voice match','I think democracy benefits from governance experiments and collective coordination.'],['tweet','Governance','x','https://x.com/RhysLindmark/status/1','Rhys public post','Thinking about platform governance versus democratic processes.'],['card','Political beliefs','anki','','study note','Political beliefs can be classified by many criteria.']];
  for(const row of fixtures)await database.prepare('INSERT INTO rb_sources VALUES(?,?,?,?,?,?)').bind(...row).run();
  const messages=[{role:'user',content:'What are your political beliefs?'},{role:'assistant',content:'Earlier answer'},{role:'user',content:'Come on you got it'}];
  assert.match(retrievalPlan(messages).fts,/politic/);const sources=await retrieve({RHYSBOT_DB:database},messages);assert(sources.some(s=>s.kind==='hee_transcript'));assert(sources.some(s=>s.kind==='podcast_transcript'));assert(sources.some(s=>s.kind==='x'));assert(!sources.some(s=>s.kind==='anki'));
  const hee=await retrieve({RHYSBOT_DB:database},[...messages,{role:'assistant',content:'Here are sources.'},{role:'user',content:'What about HEE on democracy?'}]);assert(hee.length>0);assert(hee.every(s=>s.kind==='hee_transcript'));
});
test('transcript evidence is excerpted near the matching passage, not cut off at the start',async()=>{
 const {retrieve}=await import('./rhysbot-retrieval.js');const database=db();await database.prepare('INSERT INTO rb_sources VALUES(?,?,?,?,?,?)').bind('long','A long episode','hee_transcript','https://example.com/long','Rhys narration',('Unrelated opening words. '.repeat(300))+' The Digicene is the digital chapter of evolution.').run();
 const rows=await retrieve({RHYSBOT_DB:database},'What does HEE say about Digicene?');assert(rows.length);assert(rows[0].text.includes('Digicene'));assert(rows[0].text.length<1500);
});

test('voice status forwards only a bounded ordered part and preserves integrity metadata',async()=>{
 const database=voiceDB(),env={...envBase,RHYSBOT_DB:database,RHYSBOT_PLATFORM_URL:'https://example.modal.run'};const calls=[];let ready=false;
 const handler=createVoiceHandler({fetcher:async(url,init)=>{const body=JSON.parse(init.body);calls.push(body);if(url.pathname==='/voice')return Response.json({state:'queued'},{status:202});if(!ready)return Response.json({state:'speaking',parts_ready:0,parts_total:3},{status:202});return new Response(new Uint8Array(100),{headers:{'Content-Type':'audio/wav','X-Rhysbot-Audio-Bytes':'100','X-Rhysbot-Audio-SHA256':'a'.repeat(64),'X-Rhysbot-Audio-Part':'0','X-Rhysbot-Audio-Parts':'3'}});}});
 assert.equal((await handler({request:voiceRequest({text:'Valid words.',request_id:voiceId}),env})).status,202);
 const request=()=>voiceRequest({action:'status',request_id:voiceId,part:0});let r=await handler({request:request(),env});assert.deepEqual(await r.json(),{state:'speaking',parts_ready:0,parts_total:3});ready=true;r=await handler({request:request(),env});assert.equal(r.headers.get('X-Rhysbot-Audio-Part'),'0');assert.equal(calls.at(-1).part,0);assert.equal((await r.arrayBuffer()).byteLength,100);
 assert.equal((await handler({request:voiceRequest({action:'status',request_id:voiceId,part:32}),env})).status,400);assert.equal(database.sql.prepare('SELECT micros FROM rb_spend').get().micros,500000);
});

test('sourced chat removes only redundant AI-belief handoffs and preserves uncertainty and identity',async()=>{
 const {conversationalOpening}=await import('./rhysbot-chat.js');
 async function clean(text){async function* chunks(){for(const c of text)yield c;}let out='';for await(const c of conversationalOpening(chunks()))out+=c;return out;}
 assert.equal(await clean("I don't have personal political beliefs, but I can share perspectives from Rhys Lindmark's work. His work discusses governance [1]."),'His work discusses governance [1].');
 for(const text of ['I am an AI simulation of Rhys, not the human.','I cannot infer party affiliation from these sources.','Anki notes do not establish his beliefs.','I do not have personal beliefs.'])assert.equal(await clean(text),text);
});

test('CPU standby is reserved once atomically alongside existing usage, with the same total cap',async()=>{
 const database=voiceDB(),now=new Date(),env={RHYSBOT_DB:database,RHYSBOT_CPU_STANDBY_ENABLED:'1',RHYSBOT_CPU_STANDBY_SINCE:new Date(now.getTime()-86400000).toISOString()};
 const period=new Intl.DateTimeFormat('en-CA',{timeZone:'America/Los_Angeles',year:'numeric',month:'2-digit'}).format(now),base=standbyBaseMicros(env,now);
 database.sql.prepare('INSERT INTO rb_spend(period,micros) VALUES(?,?)').run(period,100000);
 await Promise.all(Array.from({length:5},(_,i)=>charge(env,'operator'+i,5000)));
 const r=database.sql.prepare('SELECT micros,fixed_voice_micros FROM rb_spend').get();assert.equal(r.micros,100000+25000+base);assert.equal(r.fixed_voice_micros,base);
 await assert.rejects(charge(env,'over',10000000),/monthly_budget/);assert.equal(database.sql.prepare('SELECT micros FROM rb_spend').get().micros,r.micros);
 assert.equal(standbyBaseMicros({RHYSBOT_CPU_STANDBY_ENABLED:'1',RHYSBOT_CPU_STANDBY_SINCE:'2026-10-20T03:00:00Z'},new Date('2026-10-21T03:00:00Z')),Math.ceil(8900000*13/31));
 assert.equal(standbyBaseMicros({RHYSBOT_CPU_STANDBY_ENABLED:'1',RHYSBOT_CPU_STANDBY_SINCE:'2026-10-20T03:00:00Z'},new Date('2026-11-21T03:00:00Z')),8900000);
 assert.throws(()=>standbyBaseMicros({RHYSBOT_CPU_STANDBY_ENABLED:'1',RHYSBOT_CPU_STANDBY_SINCE:'invalid'}),/standby_budget/);
});
