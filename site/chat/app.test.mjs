import test from 'node:test';
import assert from 'node:assert/strict';
import { clipUTF8, histories, requestPayload, safeSourceURL, sseEvents, newTurn, applyEvent, completeTurn, boundedAudio, renderText, mount } from './app.js';
const enc = new TextEncoder();
const stream = parts => new ReadableStream({ start(c) { for (const part of parts) c.enqueue(typeof part === 'string' ? enc.encode(part) : part); c.close(); } });

test('SSE preserves split UTF8/CRLF, multiple channels and actual error/done events', async () => {
  const wire = ': ping\r\ndata: {"type":"delta","channel":"rhys","text":"café 🪴"}\r\n\r\ndata: {"type":"done","channel":"rhys"}\n\ndata: {"type":"error","channel":"qwen","message":"Unavailable"}\n\n';
  const bytes = enc.encode(wire), events=[];
  for await (const event of sseEvents(stream([...bytes].map(byte => new Uint8Array([byte]))))) events.push(event);
  assert.equal(events.length,3);assert.equal(events[0].text,'café 🪴');assert.equal(events[2].type,'error');
});
test('SSE enforces byte bounds and cancels its source on failure or abort', async () => {
  let cancelled=false;
  const source=new ReadableStream({start(c){c.enqueue(enc.encode('data: '+ 'x'.repeat(100)));},cancel(){cancelled=true;}});
  await assert.rejects(async()=>{for await(const _ of sseEvents(source,{maxBytes:20})){}},/limit/);assert.equal(cancelled,true);
  const controller=new AbortController();const quiet=new ReadableStream({cancel(){cancelled=true;}});const collect=(async()=>{for await(const _ of sseEvents(quiet,{signal:controller.signal})){} })();controller.abort();await assert.rejects(()=>collect,{name:'AbortError'});
});
test('multi-turn comparison keeps independent assistant answers and shared bounded user window', () => {
  const turns=Array.from({length:8},(_,i)=>({user:`question${i}`,rhys:'source grounded '+i,qwen:'base own '+i}));
  const h=histories(turns,'Follow-up?');assert.equal(h.rhys.length,11);assert.equal(h.qwen.length,11);assert.equal(h.rhys[1].content,'source grounded 3');assert.equal(h.qwen[1].content,'base own 3');
  turns[7].rhys='x'.repeat(11980);const trimmed=histories(turns,'Follow-up?');
  assert.deepEqual(trimmed.rhys.filter(x=>x.role==='user'),trimmed.qwen.filter(x=>x.role==='user'));assert.equal(trimmed.rhys.length,3);
  assert.throws(()=>histories([],'🪴'.repeat(1001)),/4,000/);
  const unicode=clipUTF8('🌱éhello',7);assert.equal(unicode,'🌱éh');assert.equal(enc.encode(unicode).length,7);
});
test('escaped JSON requests fit server body limit without diverging comparison user windows',()=>{
  const turns=[{user:'Question',rhys:'"'.repeat(11000),qwen:'\\'.repeat(11000)}];const context=histories(turns,'New question');const body=requestPayload(context,'compare','v'.repeat(2000));assert(enc.encode(JSON.stringify(body)).length<=28000);assert.equal(body.messages.length,1);assert.deepEqual(body.messages,body.qwen_messages);
});
test('incomplete/errored/aborted turns cannot silently become complete history', () => {
  const turn=newTurn('Question','compare');applyEvent(turn,{type:'delta',channel:'rhys',text:'A'});applyEvent(turn,{type:'done',channel:'rhys'});assert.equal(completeTurn(turn),false);
  applyEvent(turn,{type:'error',channel:'qwen'});applyEvent(turn,{type:'delta',channel:'qwen',text:'must be ignored'});applyEvent(turn,{type:'done',channel:'qwen'});assert.equal(turn.qwen,'');assert.equal(completeTurn(turn),false);
  const ok=newTurn('Question','rhys');applyEvent(ok,{type:'delta',channel:'rhys',text:'Actual response'});applyEvent(ok,{type:'done',channel:'rhys'});assert.equal(completeTurn(ok),true);
});
test('source links reject active schemes and reply renderer never uses HTML', () => {
  for(const value of ['javascript:alert(1)','data:text/html,unsafe','https://user:password@example.com','/invented'])assert.equal(safeSourceURL(value),null);
  assert.equal(safeSourceURL('https://example.com/essay'),'https://example.com/essay');
  const target={set innerHTML(_){throw Error('unsafe HTML');}};renderText(target,'<img src=x onerror=alert(1)>');assert.equal(target.textContent,'<img src=x onerror=alert(1)>');
});
test('voice needs successful bounded WAV; size and truncated bodies fail', async()=>{
  assert.equal((await boundedAudio(new Response(enc.encode('RIFFaudio'),{headers:{'Content-Type':'audio/wav','Content-Length':'9'}}))).type,'audio/wav');
  await assert.rejects(()=>boundedAudio(new Response('html',{headers:{'Content-Type':'text/html'}})),/unavailable/);
  await assert.rejects(()=>boundedAudio(new Response('wave',{headers:{'Content-Type':'audio/wav','Content-Length':'999'}}),20),/large/);
  await assert.rejects(()=>boundedAudio(new Response('wave',{headers:{'Content-Type':'audio/wav','Content-Length':'5'}})),/incomplete/);
});

class FakeElement {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.events={};this.attrs={};this.dataset={};this.classList={add(){},remove(){},toggle(){}};}
  addEventListener(name,fn){(this.events[name] ||= []).push(fn);}
  async fire(name,event={}){for(const fn of this.events[name]||[])await fn({preventDefault(){},...event});}
  append(...els){this.children.push(...els);}appendChild(el){this.children.push(el);return el;}replaceChildren(...els){this.children=els;}setAttribute(k,v){this.attrs[k]=v;}removeAttribute(k){delete this.attrs[k];}focus(){}pause(){}load(){}async play(){this.played=true;}
  set innerHTML(_){throw Error('HTML rendering forbidden');}
}
function dom(){
  const ids=Object.fromEntries(['thread','send','message','mode','mic','stop','human','status','empty','composer','new','mode-note'].map(x=>[x,new FakeElement()]));const head=new FakeElement('head');const example=new FakeElement('button');example.dataset.prompt='Example';let verify;
  const doc={getElementById:id=>ids[id],createElement:tag=>new FakeElement(tag),head,querySelectorAll:()=>[example]};
  const win={URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},crypto,turnstile:{render(_,options){verify=options.callback;return 1;},reset(){}},addEventListener(){}};
  return {ids,doc,win,example,verify:()=>verify};
}
test('actual UI loads config only; examples do not send, follow-ups use independent histories', async()=>{
  const d=dom(),calls=[];
  const fetcher=async(url,init={})=>{calls.push({url,init});if(!init.method)return Response.json({available:true,turnstile_site_key:'public',voice_available:false});return new Response(stream(['data: {"type":"delta","channel":"rhys","text":"Rhys source answer"}\n\n','data: {"type":"delta","channel":"qwen","text":"Qwen independent answer"}\n\n','data: {"type":"done","channel":"rhys"}\n\n','data: {"type":"done","channel":"qwen"}\n\n']),{headers:{'Content-Type':'text/event-stream'}});};
  const app=mount(d.doc,d.win,fetcher);await app.initialized;assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/rhysbot-chat');
  await d.example.fire('click');assert.equal(d.ids.message.value,'Example');assert.equal(calls.length,1);
  d.doc.head.children[0].onload();d.verify()('verified');d.ids.message.value='First';await d.ids.composer.fire('submit');
  assert.equal(calls.length,2);assert.equal(JSON.parse(calls[1].init.body).verification_token,'verified');
  d.ids.message.value='Second';await d.ids.composer.fire('submit');const payload=JSON.parse(calls[2].init.body);assert.equal(payload.messages[1].content,'Rhys source answer');assert.equal(payload.qwen_messages[1].content,'Qwen independent answer');assert.equal(payload.verification_token,undefined);assert.equal(d.ids.thread.children.length,2);
  app.reset();assert.equal(d.ids.thread.children.length,0);assert.equal(d.ids.empty.hidden,false);assert.equal(d.ids.message.value,'');
  d.ids.message.value='Fresh';await d.ids.composer.fire('submit');assert.equal(JSON.parse(calls[3].init.body).messages.length,1);
});
test('UI rejects duplicate submit, Stop aborts and leaves partial turn out of next history', async()=>{
  const d=dom(),calls=[];let pending;
  const fetcher=async(url,init={})=>{calls.push({url,init});if(!init.method)return Response.json({available:true,turnstile_site_key:'public',voice_available:false});if(calls.length===2)return new Promise((resolve,reject)=>{pending={resolve,reject};init.signal.addEventListener('abort',()=>reject(new DOMException('Stopped','AbortError')));});return new Response(stream(['data: {"type":"error","channel":"rhys"}\n\n']),{headers:{'Content-Type':'text/event-stream'}});};
  const app=mount(d.doc,d.win,fetcher);await app.initialized;d.doc.head.children[0].onload();d.verify()('proof');d.ids.message.value='First';const first=d.ids.composer.fire('submit');await d.ids.composer.fire('submit');assert.equal(calls.length,2);
  await d.ids.stop.fire('click');await first;assert.equal(calls[1].init.signal.aborted,true);assert.match(d.ids.status.textContent,/Stopped/);
  d.ids.message.value='Next';await d.ids.composer.fire('submit');assert.equal(JSON.parse(calls[2].init.body).messages.length,1);app.reset();
});
test('saved-checkpoint mode is explicit, sends no RAG mode, and keeps independent follow-ups',async()=>{
  const d=dom(),calls=[];
  const fetcher=async(url,init={})=>{calls.push({url,init});if(!init.method)return Response.json({available:true,turnstile_site_key:'public',voice_available:false});return Response.json({rhys:{text:'Saved adapter reply'},qwen:{text:'Actual base reply'},weights_sha256:'a'.repeat(64)});};
  const app=mount(d.doc,d.win,fetcher);await app.initialized;d.doc.head.children[0].onload();d.verify()('proof');d.ids.mode.value='checkpoints';await d.ids.mode.fire('change');assert.equal(calls.length,1);assert.match(d.ids['mode-note'].textContent,/No retrieved corpus/);
  d.ids.message.value='First';await d.ids.composer.fire('submit');assert.equal(calls[1].url,'/api/rhysbot-checkpoints');assert.equal(JSON.parse(calls[1].init.body).mode,undefined);
  d.ids.message.value='Second';await d.ids.composer.fire('submit');const body=JSON.parse(calls[2].init.body);assert.equal(body.messages[1].content,'Saved adapter reply');assert.equal(body.qwen_messages[1].content,'Actual base reply');app.reset();
});
test('AI voice is explicit, bounded to500characters, cached for replay and released by NewChat',async()=>{
  const d=dom(),calls=[];let revoked=0;d.win.URL.revokeObjectURL=()=>revoked++;
  const fetcher=async(url,init={})=>{calls.push({url,init});if(!init.method)return Response.json({available:true,turnstile_site_key:'public',voice_available:true});if(url==='/api/rhysbot-voice')return new Response('RIFFaudio',{headers:{'Content-Type':'audio/wav'}});return new Response(stream([`data: ${JSON.stringify({type:'delta',channel:'rhys',text:'🌱'.repeat(600)})}\n\n`,'data: {"type":"delta","channel":"qwen","text":"Qwen reply"}\n\n','data: {"type":"done","channel":"rhys"}\n\n','data: {"type":"done","channel":"qwen"}\n\n']),{headers:{'Content-Type':'text/event-stream'}});};
  const app=mount(d.doc,d.win,fetcher);await app.initialized;d.doc.head.children[0].onload();d.verify()('proof');d.ids.message.value='Question';await d.ids.composer.fire('submit');assert.equal(calls.length,2);
  const left=d.ids.thread.children[0].children[1].children[0],button=left.children.find(e=>e.tagName==='button');assert.match(button.textContent,/AI-generated/);await button.fire('click');assert.equal(calls.length,3);const body=JSON.parse(calls[2].init.body);assert.equal([...body.text].length,500);assert.match(body.request_id,/^[a-f0-9-]{36}$/);assert.equal(calls[2].init.credentials,'same-origin');await button.fire('click');assert.equal(calls.length,3);app.reset();assert.equal(revoked,1);
});
test('microphone only fills input on explicit click; recognition never sends a message',async()=>{
  const d=dom();let recognition,starts=0,calls=0;
  d.win.SpeechRecognition=class{constructor(){recognition=this;}start(){starts++;this.onstart();}abort(){}stop(){this.onend();}};
  const app=mount(d.doc,d.win,async()=>{calls++;return Response.json({available:true,turnstile_site_key:'public',voice_available:false});});await app.initialized;assert.equal(starts,0);await d.ids.mic.fire('click');assert.equal(starts,1);recognition.onresult({results:[[{transcript:'Spoken draft'}]]});assert.equal(d.ids.message.value,'Spoken draft');assert.equal(calls,1);app.reset();
});
