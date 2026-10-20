import test from 'node:test';import assert from 'node:assert/strict';import{DatabaseSync}from'node:sqlite';import{readFileSync}from'node:fs';
import{createNanochatHandler}from'./rhysbot-nanochat.js';
const secret='s'.repeat(48);
function setup(){const sql=new DatabaseSync(':memory:');sql.exec(readFileSync(new URL('../chat-schema.sql',import.meta.url),'utf8'));const db={prepare(q){let a=[];return{bind(...v){a=v;return this},async first(){return sql.prepare(q).get(...a)},async run(){sql.prepare(q).run(...a)}}}};return{sql,env:{RHYSBOT_DB:db,RHYSBOT_CLIENT_SALT:'a'.repeat(40),RHYSBOT_TRAIN_BRIDGE_SECRET:secret,RHYSBOT_PLATFORM_URL:'https://example.modal.run',MODAL_PROXY_KEY:'test',MODAL_PROXY_SECRET:'test-secret'}};}
function request(messages=[{role:'user',content:'Hello'}],origin='https://ai.rhyslindmark.com'){return new Request('https://ai.rhyslindmark.com/api/rhysbot-nanochat',{method:'POST',headers:{Origin:origin,'CF-Connecting-IP':'1.2.3.4',Authorization:'Bearer '+secret},body:JSON.stringify({messages})});}
test('nanochat routes retained history only to actual checkpoint, charges CPU rate and validates provenance',async()=>{
 const{env,sql}=setup();const messages=[{role:'user',content:'Hi'},{role:'assistant',content:'Prior nanochat'},{role:'user',content:'More'}];let calls=0;
 const h=createNanochatHandler({fetcher:async(url,init)=>{calls++;assert.equal(url.pathname,'/nanochat');assert.deepEqual(JSON.parse(init.body).messages,messages);return Response.json({text:'Scratch answer',model:'pure_nanochat',checkpoint_run_id:'nanochat-train-20261019a',weights_sha256:'a'.repeat(64),qwen_fallback:false,retrieval:false,input_tokens:30,output_tokens:3,elapsed_seconds:1});}});
 const r=await h({request:request(messages),env});assert.equal(r.status,200);assert.equal((await r.json()).text,'Scratch answer');assert.equal(calls,1);assert.equal(sql.prepare('SELECT micros FROM rb_spend').get().micros,20000);
});
test('nanochat refuses wrong origin, malformed history, exhausted budget and substituted models',async()=>{
 const{env,sql}=setup();let calls=0;const h=createNanochatHandler({fetcher:async()=>{calls++;return Response.json({text:'Qwen pretending',model:'qwen'});}});
 assert.equal((await h({request:request(undefined,'https://evil.test'),env})).status,403);
 assert.equal((await h({request:request([{role:'system',content:'bad'}]),env})).status,400);assert.equal(calls,0);
 assert.equal((await h({request:request(),env})).status,502);assert.equal(calls,1);
 sql.prepare('UPDATE rb_spend SET micros=10000000').run();assert.equal((await h({request:request(),env})).status,429);assert.equal(calls,1);
});
