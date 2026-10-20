import test from 'node:test';
import assert from 'node:assert/strict';
import { clipUTF8, histories, requestPayload, safeSourceURL, sseEvents, newTurn, applyEvent, completeTurn, boundedAudio, requestVoice, voiceWait, renderText, mount, CONVERSATION_KEY, restoreConversation, sourceLabel } from './app.js';
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
function dom(storage=new Map()){
  const ids=Object.fromEntries(['thread','send','message','mode','mic','stop','human','status','empty','composer','new','mode-note'].map(x=>[x,new FakeElement()]));const head=new FakeElement('head');const example=new FakeElement('button');example.dataset.prompt='Example';let verify;
  const doc={getElementById:id=>ids[id],createElement:tag=>new FakeElement(tag),head,querySelectorAll:()=>[example]};
  const win={sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},crypto,turnstile:{render(_,options){verify=options.callback;return 1;},reset(){}},addEventListener(){}};
  return {ids,doc,win,example,verify:()=>verify};
}
test('actual UI loads config only; examples do not send, follow-ups use independent histories', async()=>{
  const d=dom(),calls=[];
  const fetcher=async(url,init={})=>{calls.push({url,init});if(!init.method)return Response.json({available:true,turnstile_site_key:'public',voice_available:false});return new Response(stream(['data: {"type":"delta","channel":"rhys","text":"Rhys source answer"}\n\n','data: {"type":"delta","channel":"qwen","text":"Qwen independent answer"}\n\n','data: {"type":"done","channel":"rhys"}\n\n','data: {"type":"done","channel":"qwen"}\n\n']),{headers:{'Content-Type':'text/event-stream'}});};
  const app=mount(d.doc,d.win,fetcher);await app.initialized;assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/rhysbot-chat');
  await d.example.fire('click');assert.equal(d.ids.message.value,'Example');assert.equal(calls.length,1);
  assert.equal(d.ids.mode.value,'rhys');d.ids.mode.value='compare';await d.ids.mode.fire('change');
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
test('AI voice is explicit, supports full replies up to2000characters, cached for replay and released by NewChat',async()=>{
  const d=dom(),calls=[];let revoked=0;d.win.URL.revokeObjectURL=()=>revoked++;
  const fetcher=async(url,init={})=>{calls.push({url,init});if(!init.method)return Response.json({available:true,turnstile_site_key:'public',voice_available:true});if(url==='/api/rhysbot-voice')return new Response('RIFFaudio',{headers:{'Content-Type':'audio/wav'}});return new Response(stream([`data: ${JSON.stringify({type:'delta',channel:'rhys',text:'🌱'.repeat(2100)})}\n\n`,'data: {"type":"delta","channel":"qwen","text":"Qwen reply"}\n\n','data: {"type":"done","channel":"rhys"}\n\n','data: {"type":"done","channel":"qwen"}\n\n']),{headers:{'Content-Type':'text/event-stream'}});};
  const app=mount(d.doc,d.win,fetcher);await app.initialized;d.doc.head.children[0].onload();d.verify()('proof');d.ids.message.value='Question';await d.ids.composer.fire('submit');assert.equal(calls.length,2);
  const left=d.ids.thread.children[0].children[1].children[0],button=left.children.find(e=>e.tagName==='button');assert.match(button.textContent,/AI-generated/);await button.fire('click');assert.equal(calls.length,3);const body=JSON.parse(calls[2].init.body);assert.equal([...body.text].length,2000);assert.match(body.request_id,/^[a-f0-9-]{36}$/);assert.equal(calls[2].init.credentials,'same-origin');await button.fire('click');assert.equal(calls.length,3);app.reset();assert.equal(revoked,1);
});
test('microphone only fills input on explicit click; recognition never sends a message',async()=>{
  const d=dom();let recognition,starts=0,calls=0;
  d.win.SpeechRecognition=class{constructor(){recognition=this;}start(){starts++;this.onstart();}abort(){}stop(){this.onend();}};
  const app=mount(d.doc,d.win,async()=>{calls++;return Response.json({available:true,turnstile_site_key:'public',voice_available:false});});await app.initialized;assert.equal(starts,0);await d.ids.mic.fire('click');assert.equal(starts,1);recognition.onresult({results:[[{transcript:'Spoken draft'}]]});assert.equal(d.ids.message.value,'Spoken draft');assert.equal(calls,1);app.reset();
});

test('voice polls progress then verifies complete audio; polling never submits text again',async()=>{
  const calls=[],phases=[],raw=enc.encode('RIFFaudio'),sum=Buffer.from(await crypto.subtle.digest('SHA-256',raw)).toString('hex');let accepted=0;
  const fetcher=async(url,init)=>{calls.push(JSON.parse(init.body));const phase=['queued','loading_voice','speaking'][calls.length-1];return phase?Response.json({state:phase},{status:202}):new Response(raw,{headers:{'Content-Type':'audio/wav','X-Rhysbot-Audio-Bytes':String(raw.length),'X-Rhysbot-Audio-SHA256':sum}});};
  const blob=await requestVoice(fetcher,{text:'Whole paragraph',requestId:'one',signal:new AbortController().signal,verification:'proof',onProgress:p=>phases.push(p),onAccepted:()=>accepted++,wait:async()=>{}});
  assert.equal(blob.size,raw.length);assert.deepEqual(phases,['queued','loading_voice','speaking']);assert.equal(accepted,4);assert.equal(calls[0].text,'Whole paragraph');for(const row of calls.slice(1))assert.deepEqual(row,{action:'status',request_id:'one'});
  await assert.rejects(()=>boundedAudio(new Response(raw,{headers:{'Content-Type':'audio/wav','X-Rhysbot-Audio-Bytes':String(raw.length),'X-Rhysbot-Audio-SHA256':'0'.repeat(64)}})),/incomplete/);
});
test('voice polling can stop immediately and provider errors finish with an actionable failure',async()=>{
  const controller=new AbortController();let polls=0;
  const task=requestVoice(async()=>{polls++;return Response.json({state:'speaking'},{status:202});},{text:'Reply',requestId:'same',signal:controller.signal,onProgress:()=>controller.abort()});
  await assert.rejects(()=>task,{name:'AbortError'});assert.equal(polls,1);
  await assert.rejects(()=>requestVoice(async()=>Response.json({error:'monthly_budget_reached'},{status:429}),{text:'Reply',requestId:'same'}),/monthly/);
});

test('voice Stop waiting and Resume remain usable after an interrupted connection',async()=>{
  const d=dom(),voiceBodies=[];let pending;
  const fetcher=async(url,init={})=>{
    if(!init.method)return Response.json({available:true,turnstile_site_key:'public',voice_available:true});
    if(url==='/api/rhysbot-voice'){
      voiceBodies.push(JSON.parse(init.body));
      if(voiceBodies.length===1)return new Promise((resolve,reject)=>{pending=true;init.signal.addEventListener('abort',()=>reject(new DOMException('Stopped','AbortError')),{once:true});});
      return new Response('RIFFaudio',{headers:{'Content-Type':'audio/wav'}});
    }
    return new Response(stream(['data: {"type":"delta","channel":"rhys","text":"A complete answer."}\n\n','data: {"type":"delta","channel":"qwen","text":"Base answer."}\n\n','data: {"type":"done","channel":"rhys"}\n\n','data: {"type":"done","channel":"qwen"}\n\n']),{headers:{'Content-Type':'text/event-stream'}});
  };
  const app=mount(d.doc,d.win,fetcher);await app.initialized;d.doc.head.children[0].onload();d.verify()('proof');d.ids.message.value='Question';await d.ids.composer.fire('submit');
  const card=d.ids.thread.children[0].children[1].children[0],button=card.children.find(e=>e.tagName==='button'),audio=card.children.find(e=>e.tagName==='audio');
  const waiting=button.fire('click');assert.equal(pending,true);assert.equal(button.disabled,false);assert.equal(button.textContent,'Stop waiting');await button.fire('click');await waiting;assert.match(button.textContent,/Resume/);
  await button.fire('click');assert.equal(voiceBodies[0].request_id,voiceBodies[1].request_id);assert.equal(audio.hidden,false);assert.equal(audio.played,true);assert.match(button.textContent,/Play/);app.reset();
});


test('default chat uses user messages and restores a verified follow-up after refresh without auto generation',async()=>{
  const storage=new Map(),calls=[];
  const fetcher=async(url,init={})=>{
    calls.push({url,init});
    if(!init.method)return Response.json({available:true,turnstile_site_key:'public',voice_available:false});
    return new Response(stream([
      `data: ${JSON.stringify({type:'meta',sources:[{id:'h1',title:'Evolution and cooperation',kind:'hee_transcript',url:'https://example.com/hee',attribution:'internal_raw_authorship'}]})}\n\n`,
      'data: {"type":"delta","channel":"rhys","text":"Let’s follow that thread."}\n\n',
      'data: {"type":"done","channel":"rhys"}\n\n'
    ]),{headers:{'Content-Type':'text/event-stream'}});
  };
  const first=dom(storage),app=mount(first.doc,first.win,fetcher);await app.initialized;
  assert.equal(first.ids.mode.value,'rhys');first.doc.head.children[0].onload();first.verify()('proof');first.ids.message.value='How does cooperation evolve?';await first.ids.composer.fire('submit');
  const request=JSON.parse(calls[1].init.body);assert.equal(request.mode,'rhys');assert.equal(request.qwen_messages,undefined);
  const turn=first.ids.thread.children[0];assert.equal(turn.children[0].tagName,'article');assert.equal(turn.children[0].attrs['aria-label'],'Your message');assert.equal(turn.children[0].children[0].textContent,'You');
  const sourceDetails=turn.children[1].children[0].children.find(e=>e.tagName==='details');assert.match(sourceDetails.children[0].textContent,/How Everything Evolved/);assert.doesNotMatch(sourceDetails.children[1].children[0].children[1].textContent,/internal_raw_authorship/);
  assert(storage.has(CONVERSATION_KEY));assert(!storage.get(CONVERSATION_KEY).includes('proof'));
  const second=dom(storage),reloaded=mount(second.doc,second.win,fetcher);await reloaded.initialized;assert.equal(calls.length,3);assert.equal(second.ids.thread.children.length,1);assert.equal(second.ids.empty.hidden,true);assert.match(second.ids.status.textContent,/restored/);
  second.doc.head.children[0].onload();second.verify()('new-proof');second.ids.message.value='And what about institutions?';await second.ids.composer.fire('submit');
  const followup=JSON.parse(calls[3].init.body);assert.equal(followup.messages.length,3);assert.equal(followup.messages[1].content,'Let’s follow that thread.');assert.equal(followup.qwen_messages,undefined);assert.equal(second.ids.thread.children.length,2);
  reloaded.reset();assert.equal(storage.has(CONVERSATION_KEY),false);assert.equal(second.ids.thread.children.length,0);app.reset();
});
test('changing modes preserves separate conversations and never sends Rhys answers to the baseline',async()=>{
  const storage=new Map(),d=dom(storage),calls=[];
  const fetcher=async(url,init={})=>{if(!init.method)return Response.json({available:true,turnstile_site_key:'public',voice_available:false});const body=JSON.parse(init.body);calls.push(body);const channel=body.mode==='qwen'?'qwen':'rhys';return new Response(stream([`data: ${JSON.stringify({type:'delta',channel,text:channel==='rhys'?'Private-to-Rhys-context':'Baseline-context'})}\n\n`,`data: ${JSON.stringify({type:'done',channel})}\n\n`]),{headers:{'Content-Type':'text/event-stream'}});};
  const app=mount(d.doc,d.win,fetcher);await app.initialized;d.doc.head.children[0].onload();d.verify()('proof');
  d.ids.message.value='Rhys question';await d.ids.composer.fire('submit');
  d.ids.mode.value='qwen';await d.ids.mode.fire('change');assert.equal(d.ids.thread.children.length,0);d.ids.message.value='Base question';await d.ids.composer.fire('submit');assert.equal(calls[1].messages.length,1);
  d.ids.mode.value='rhys';await d.ids.mode.fire('change');assert.equal(d.ids.thread.children.length,1);d.ids.message.value='Rhys follow-up';await d.ids.composer.fire('submit');assert.equal(calls[2].messages[1].content,'Private-to-Rhys-context');
  d.ids.mode.value='qwen';await d.ids.mode.fire('change');d.ids.message.value='Base follow-up';await d.ids.composer.fire('submit');assert.equal(calls[3].messages[1].content,'Baseline-context');assert(!JSON.stringify(calls[3]).includes('Private-to-Rhys-context'));
  d.ids.mode.value='compare';await d.ids.mode.fire('change');assert.equal(d.ids.thread.children.length,0);app.reset();
});
test('session restoration validates shape and bounds and disabled storage does not block chat',async()=>{
  assert.equal(restoreConversation('not JSON').mode,'rhys');assert.equal(restoreConversation('x'.repeat(256001)).mode,'rhys');
  const restored=restoreConversation(JSON.stringify({version:1,mode:'qwen',conversations:{qwen:[{user:'Question',rhys:'must not cross over',qwen:'Base reply'},{user:'Incomplete'}]}}));assert.equal(restored.conversations.qwen.length,1);assert.equal(restored.conversations.qwen[0].rhys,'');
  assert.equal(sourceLabel('podcast_transcript'),'Podcasts / YouTube');assert.equal(sourceLabel('x'),'X');
  const d=dom();Object.defineProperty(d.win,'sessionStorage',{get(){throw Error('Blocked storage');}});const app=mount(d.doc,d.win,async()=>Response.json({available:true,turnstile_site_key:'public',voice_available:false}));await app.initialized;assert.equal(d.ids.mode.value,'rhys');app.reset();
});

test('incremental voice verifies and exposes the first part before the rest exists, then combines for replay',async()=>{
 const {requestVoiceParts}=await import('./app.js');
 const wav=n=>{const b=new Uint8Array(44+n);b.set(enc.encode('RIFF'),0);b.set(enc.encode('WAVEfmt '),8);b.set(enc.encode('data'),36);const v=new DataView(b.buffer);v.setUint32(4,36+n,true);v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,24000,true);v.setUint32(28,48000,true);v.setUint16(32,2,true);v.setUint16(34,16,true);v.setUint32(40,n,true);return b;};
 const calls=[],delivered=[];let secondReady=false;
 const fetcher=async(_,init)=>{const body=JSON.parse(init.body);calls.push(body);if(!body.action)return Response.json({state:'queued'},{status:202});if(body.part===1&&!secondReady)return Response.json({state:'speaking'},{status:202});const b=wav(body.part===0?100:200);return new Response(b,{headers:{'Content-Type':'audio/wav','X-Rhysbot-Audio-Part':String(body.part),'X-Rhysbot-Audio-Parts':'2','X-Rhysbot-Audio-Bytes':String(b.length),'X-Rhysbot-Audio-SHA256':Buffer.from(await crypto.subtle.digest('SHA-256',b)).toString('hex')}});};
 const result=await requestVoiceParts(fetcher,{text:'Full reply',requestId:'same',onChunk:(b,info)=>{delivered.push(info.index);if(info.index===0)assert.equal(secondReady,false);},wait:async()=>{if(delivered.length)secondReady=true;}});
 assert.deepEqual(delivered,[0,1]);assert.equal(result.size,344);assert.equal(calls.filter(c=>c.text).length,1);assert.equal(calls.at(-1).part,1);
});

test('voice UI plays opening WAV before the second exists, drains queued parts once, and replays the full clip locally', {timeout:5000}, async(t)=>{
  const d=dom(),voiceCalls=[],objectURLs=[],played=[];
  let firstPlayed,secondRequested,releaseSecond;
  const openingPlayed=new Promise(resolve=>{firstPlayed=resolve;});
  const waitingForSecond=new Promise(resolve=>{secondRequested=resolve;});
  const pcmWav=sample=>{
    const raw=new Uint8Array(46),view=new DataView(raw.buffer);
    raw.set(enc.encode('RIFF'),0);raw.set(enc.encode('WAVEfmt '),8);raw.set(enc.encode('data'),36);
    view.setUint32(4,38,true);view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,24000,true);view.setUint32(28,48000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);view.setUint32(40,2,true);view.setInt16(44,sample,true);
    return raw;
  };
  const partResponse=async(index,sample)=>{
    const raw=pcmWav(sample),hash=Buffer.from(await crypto.subtle.digest('SHA-256',raw)).toString('hex');
    return new Response(raw,{headers:{'Content-Type':'audio/wav','X-Rhysbot-Audio-Part':String(index),'X-Rhysbot-Audio-Parts':'2','X-Rhysbot-Audio-Bytes':String(raw.length),'X-Rhysbot-Audio-SHA256':hash}});
  };
  d.win.URL.createObjectURL=blob=>{const url=`blob:voice-${objectURLs.length}`;objectURLs.push({url,blob});return url;};
  const fetcher=async(url,init={})=>{
    if(!init.method)return Response.json({available:true,turnstile_site_key:'public',voice_available:true});
    if(url==='/api/rhysbot-voice'){
      const body=JSON.parse(init.body);voiceCalls.push(body);
      if(!body.action)return partResponse(0,101);
      assert.equal(body.part,1);
      return new Promise((resolve,reject)=>{
        releaseSecond=()=>partResponse(1,202).then(resolve,reject);
        init.signal.addEventListener('abort',()=>reject(new DOMException('Stopped','AbortError')),{once:true});
        secondRequested();
      });
    }
    return new Response(stream(['data: {"type":"delta","channel":"rhys","text":"The opening sentence. Then the rest of the reply."}\n\n','data: {"type":"done","channel":"rhys"}\n\n']),{headers:{'Content-Type':'text/event-stream'}});
  };
  const app=mount(d.doc,d.win,fetcher);t.after(()=>{releaseSecond?.();app.reset();});
  await app.initialized;d.doc.head.children[0].onload();d.verify()('proof');d.ids.message.value='Question';await d.ids.composer.fire('submit');
  const card=d.ids.thread.children[0].children[1].children[0],button=card.children.find(e=>e.tagName==='button'),audio=card.children.find(e=>e.tagName==='audio');
  audio.play=async()=>{played.push(audio.src);if(played.length===1)firstPlayed();};
  const reading=button.fire('click');await openingPlayed;await waitingForSecond;
  assert.equal(audio.hidden,false);assert.deepEqual(played,['blob:voice-0']);assert.equal(objectURLs.length,1,'the second WAV and complete replay do not exist when opening playback begins');assert.equal(button.textContent,'Stop waiting');
  releaseSecond();await reading;
  assert.deepEqual(played,['blob:voice-0'],'finishing synthesis must not interrupt or restart the playing opening');assert.equal(objectURLs.length,3);
  const complete=new DataView(await objectURLs[2].blob.arrayBuffer());assert.equal(complete.byteLength,48);assert.equal(complete.getInt16(44,true),101);assert.equal(complete.getInt16(46,true),202);
  await audio.fire('ended');assert.deepEqual(played,['blob:voice-0','blob:voice-1']);
  await audio.fire('ended');assert.equal(audio.src,'blob:voice-2');assert.equal(played.length,2,'finishing the final part must not replay the full clip automatically');
  await button.fire('click');assert.deepEqual(played,['blob:voice-0','blob:voice-1','blob:voice-2']);
  await audio.fire('ended');assert.equal(audio.src,'blob:voice-2');assert.equal(played.length,3,'the completed replay must not loop back through the parts');
  await button.fire('click');await audio.fire('ended');assert.deepEqual(played,['blob:voice-0','blob:voice-1','blob:voice-2','blob:voice-2']);
  assert.equal(voiceCalls.length,2,'replay must not send any further generation or polling POST');assert.equal(voiceCalls.filter(body=>typeof body.text==='string').length,1);assert.equal(voiceCalls[0].request_id,voiceCalls[1].request_id);
});
