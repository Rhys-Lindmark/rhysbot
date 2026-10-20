import test from 'node:test';import assert from 'node:assert/strict';import {DatabaseSync} from 'node:sqlite';import {readFileSync} from 'node:fs';
import {createVoiceStreamHandler,streamConfigured} from './rhysbot-voice-stream.js';
import {standbyBaseMicros} from './rhysbot-chat.js';
const secret='s'.repeat(48),uuid='12345678-1234-4234-8234-123456789abc';
function setup(){const sql=new DatabaseSync(':memory:');sql.exec(readFileSync(new URL('../chat-schema.sql',import.meta.url),'utf8'));const db={prepare(q){let args=[];return {bind(...v){args=v;return this},async first(){return sql.prepare(q).get(...args)},async run(){sql.prepare(q).run(...args)}}}};return {sql,env:{RHYSBOT_DB:db,RHYSBOT_CLIENT_SALT:'a'.repeat(40),RHYSBOT_TRAIN_BRIDGE_SECRET:secret,RHYSBOT_VOICE_STREAM_URL:'https://private.modal.run',MODAL_PROXY_KEY:'test-proxy',MODAL_PROXY_SECRET:'test-proxy-secret'}};}
function req(body,origin='https://ai.rhyslindmark.com'){return new Request('https://ai.rhyslindmark.com/api/rhysbot-voice-stream',{method:'POST',headers:{Origin:origin,'CF-Connecting-IP':'127.0.0.1',Authorization:'Bearer '+secret},body:JSON.stringify(body)});}
test('direct stream exposes first audio before provider finishes, keeps credentials private and reserves once',async()=>{
 const {sql,env}=setup();let provider,reads=0;const first='data: {"type":"audio","data":"AQACAA=="}\n\n';
 const handler=createVoiceStreamHandler({fetcher:async(url,init)=>{reads++;assert.equal(url.pathname,'/stream');assert.equal(init.headers['Modal-Key'],'test-proxy');assert.equal(JSON.parse(init.body).text,'Actual reply');return new Response(new ReadableStream({start(c){provider=c;c.enqueue(new TextEncoder().encode(first));}}),{headers:{'Content-Type':'text/event-stream'}});}});
 const body={text:'Actual reply',request_id:uuid},res=await handler({request:req(body),env});assert.equal(res.status,200);assert.equal(res.headers.get('Modal-Key'),null);const reader=res.body.getReader();assert.equal(new TextDecoder().decode((await reader.read()).value),first);
 assert.equal((await handler({request:req(body),env})).status,409);assert.equal(reads,1);assert.equal(sql.prepare('SELECT micros FROM rb_spend').get().micros,50000);
 provider.enqueue(new TextEncoder().encode('data: {"type":"done"}\n\n'));provider.close();assert.match(new TextDecoder().decode((await reader.read()).value),/done/);assert.equal((await reader.read()).done,true);
});
test('wrong origin, absent config, oversize input and exhausted budget cannot dispatch',async()=>{
 const {sql,env}=setup();let calls=0;const handler=createVoiceStreamHandler({fetcher:async()=>{calls++;throw Error('unexpected');}}),body={text:'Reply',request_id:uuid};
 assert.equal((await handler({request:req(body,'https://evil.test'),env})).status,403);
 assert.equal((await handler({request:req(body),env:{...env,RHYSBOT_VOICE_STREAM_URL:''}})).status,503);
 assert.equal((await handler({request:req({...body,text:'x'.repeat(2001)}),env})).status,400);
 sql.prepare('INSERT INTO rb_spend(period,micros) VALUES(?,?)').run(new Intl.DateTimeFormat('en-CA',{timeZone:'America/Los_Angeles',year:'numeric',month:'2-digit'}).format(new Date()),10000000);
 assert.equal((await handler({request:req(body),env})).status,429);assert.equal(calls,0);
 for(const u of ['http://private.modal.run','https://evil.test','https://user:pass@private.modal.run','https://private.modal.run:444'])assert.equal(streamConfigured({...env,RHYSBOT_VOICE_STREAM_URL:u}),false);
});
test('uncertain dispatch retains charge and refuses another generation with the same request id',async()=>{
 const {sql,env}=setup();let calls=0;const h=createVoiceStreamHandler({fetcher:async()=>{calls++;throw Error('transport after dispatch');}}),body={request_id:uuid,text:'The reply.'};
 assert.equal((await h({request:req(body),env})).status,503);assert.equal((await h({request:req(body),env})).status,409);assert.equal((await h({request:req({...body,text:'Changed'}),env})).status,409);assert.equal(calls,1);assert.equal(sql.prepare('SELECT micros FROM rb_spend').get().micros,50000);
});
test('older Workers without Request.signal can stream; consumer cancellation aborts the provider',async()=>{
 const {env}=setup();let signal,cancelled=false;
 const h=createVoiceStreamHandler({fetcher:async(_,init)=>{signal=init.signal;return new Response(new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('data: {"type":"audio","data":"AQACAA=="}\n\n'));},cancel(){cancelled=true;}}),{headers:{'Content-Type':'text/event-stream'}});}});
 const request=req({text:'Reply',request_id:uuid});Object.defineProperty(request,'signal',{get(){throw Error('Not implemented');}});
 const r=await h({request,env});assert.equal(r.status,200);const reader=r.body.getReader();assert.equal((await reader.read()).done,false);await reader.cancel();assert.equal(signal.aborted,true);assert.equal(cancelled,true);
});

test('ready CPU stream adds only its variable reservation to the shared standby baseline',async()=>{
 const {sql,env}=setup();env.RHYSBOT_CPU_STANDBY_ENABLED='1';env.RHYSBOT_CPU_STANDBY_SINCE=new Date().toISOString();let calls=0;
 const h=createVoiceStreamHandler({fetcher:async()=>{calls++;return new Response('data: {"type":"done"}\n\n',{headers:{'Content-Type':'text/event-stream'}});}});
 const body={text:'A new bounded read.',request_id:uuid};const r=await h({request:req(body),env});assert.equal(r.status,200);await r.text();
 const row=sql.prepare('SELECT micros,fixed_voice_micros FROM rb_spend').get();assert.equal(row.fixed_voice_micros,standbyBaseMicros(env));assert.equal(row.micros,row.fixed_voice_micros+20000);
 assert.equal((await h({request:req(body),env})).status,409);assert.equal(calls,1);assert.equal(sql.prepare('SELECT micros FROM rb_spend').get().micros,row.micros);
});
