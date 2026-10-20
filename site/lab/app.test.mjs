import test from 'node:test';
import assert from 'node:assert/strict';
import {parseGuestCorpus,matchGuestExcerpts,validateBackroom,validateImageFile,imageDimensions,prepareImage,validateGeneratedImage,labEvents,transcriptText,safeURL,mountLab,GUEST_LIMIT,IMAGE_LIMIT} from './app.js';
const enc=new TextEncoder(),frame=value=>enc.encode(`data: ${JSON.stringify(value)}\r\n\r\n`);
const jpeg=new Uint8Array([255,216,255,224,0,2,255,217]),dataURL='data:image/jpeg;base64,'+Buffer.from(jpeg).toString('base64');
const streamOf=values=>new ReadableStream({start(c){values.forEach(value=>c.enqueue(value instanceof Uint8Array?value:frame(value)));c.close();}});

test('guest parsing is bounded, Unicode-safe and matches at most four relevant excerpts without unrelated material',()=>{
  const text=Array.from({length:6},(_,i)=>JSON.stringify({title:'Markets '+i,text:'Markets coordination governance. '+('🌱 '.repeat(900))})).join('\n')+'\n'+JSON.stringify({title:'Kitchen',text:'Baking bread in a warm kitchen.'});
  const parsed=parseGuestCorpus(text,'guest.jsonl');assert(parsed.length>6);assert(parsed.every(row=>[...row.text].length<=1500&&[...row.title].length<=160));
  const selected=matchGuestExcerpts(parsed,'Markets coordination');assert.equal(selected.length,4);assert(selected.every(row=>row.text.includes('Markets')));assert(!JSON.stringify(selected).includes('Baking bread'));assert.equal(matchGuestExcerpts(parsed,'Astronomy planets').length,0);
  assert.throws(()=>parseGuestCorpus('x'.repeat(GUEST_LIMIT+1),'large.txt'),/2 MiB/);assert.throws(()=>parseGuestCorpus('not JSON','bad.jsonl'),/line 1/);assert.throws(()=>parseGuestCorpus('{"x":2}','bad.jsonl'),/text or content/);assert.throws(()=>parseGuestCorpus(' ','empty.txt'),/no readable/);assert.throws(()=>parseGuestCorpus('Text','file.pdf'),/\.txt/);
  assert.equal(parseGuestCorpus('Essay body','file.md')[0].text,'Essay body');assert.equal(parseGuestCorpus('"One paragraph"','file.jsonl')[0].text,'One paragraph');
});
test('backroom contract rejects invalid roles, counts and guests; transcripts label simulations and partial completion',()=>{
  const valid={topic:'Coordination',participants:['rhys','qwen'],turns:2};assert.deepEqual(validateBackroom(valid),{action:'backroom',...valid});
  for(const patch of [{participants:['rhys']},{participants:['rhys','rhys']},{participants:['rhys','unknown']},{turns:7},{turns:2.5},{topic:'x'.repeat(2001)},{participants:['rhys','guest']}])assert.throws(()=>validateBackroom({...valid,...patch}));
  const guest={name:'Guest',excerpts:[{title:'Essay',text:'Text'}]};assert.deepEqual(validateBackroom({...valid,participants:['rhys','guest'],guest}).guest,guest);
  assert.match(transcriptText('Question',[{turn:1,role:'guest',text:'<unsafe>'}],{guestName:'Tyler Cowen'}),/partial transcript[\s\S]*Tyler Cowen · prompted guest simulation[\s\S]*<unsafe>/);assert.match(transcriptText('Question',[],{complete:true}),/complete/);
  assert.equal(safeURL('javascript:alert(1)'),null);assert.equal(safeURL('https://user:pw@example.com'),null);assert.equal(safeURL('https://example.com/essay'),'https://example.com/essay');
});
test('image preparation validates JPEG/PNG and raster dimensions, re-encodes metadata-free JPEG and releases bitmap/canvas',async()=>{
  assert.throws(()=>validateImageFile({type:'image/svg+xml',size:100}),/JPEG or PNG/);assert.throws(()=>validateImageFile({type:'image/jpeg',size:10*1024*1024+1}),/10 MiB/);assert.deepEqual(imageDimensions(4000,2000),{width:1024,height:512});assert.deepEqual(imageDimensions(12,24),{width:12,height:24});assert.throws(()=>imageDimensions(10000,10000),/40 megapixels/);
  const file=new File([jpeg,'Exif: do not send original metadata'],'input.jpg',{type:'image/jpeg'}),quality=[];let closed=false,drawn;
  const canvas={getContext(){return{fillRect(){},drawImage(...args){drawn=args;}};},toBlob(callback,type,q){quality.push(q);callback(quality.length===1?new Blob([new Uint8Array(IMAGE_LIMIT+1)],{type}):new Blob([jpeg],{type}));}};
  const result=await prepareImage(file,{createBitmap:async(_,options)=>{assert.equal(options.imageOrientation,'from-image');return{width:4000,height:2000,close(){closed=true;}};},createCanvas:()=>canvas});
  assert.deepEqual(quality,[0.85,0.65]);assert.equal(result.width,1024);assert.equal(result.height,512);assert.equal(result.dataURL,dataURL);assert.equal(closed,true);assert.equal(canvas.width,1);assert.equal(canvas.height,1);assert.deepEqual(drawn.slice(1),[0,0,1024,512]);assert.equal(result.blob.type,'image/jpeg');
  await assert.rejects(()=>prepareImage(new File(['bad image'],'bad.jpg',{type:'image/jpeg'}),{createBitmap:async()=>{throw Error('must not decode');}}),/valid JPEG/);
  assert.equal(validateGeneratedImage(dataURL),dataURL);for(const value of ['javascript:alert(1)','data:image/svg+xml;base64,AAAA','data:image/jpeg;base64,AAAA','data:image/jpeg;base64,!!=='])assert.throws(()=>validateGeneratedImage(value));
});
test('SSE handles split UTF8/CRLF, enforces wire bounds and cancels on malformed/truncated input or abort',async()=>{
  const wire=frame({type:'turn',role:'rhys',turn:1,text:'café 🌱'}),events=[];for await(const row of labEvents(streamOf([...wire].map(byte=>new Uint8Array([byte])))))events.push(row);assert.equal(events[0].text,'café 🌱');
  for(const source of [streamOf([enc.encode('data: broken\n\n')]),streamOf([enc.encode('data: {"type":"done"}')]),streamOf([new Uint8Array([255])])])await assert.rejects(async()=>{for await(const _ of labEvents(source)){};});
  await assert.rejects(async()=>{for await(const _ of labEvents(streamOf([{type:'meta',data:'x'.repeat(40)}]),{maxBytes:20})){};},/size limit/);
  let cancelled=false;const controller=new AbortController(),task=(async()=>{for await(const _ of labEvents(new ReadableStream({cancel(){cancelled=true;}}),{signal:controller.signal})){};})();controller.abort();await assert.rejects(task,{name:'AbortError'});assert.equal(cancelled,true);
});
class Element {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.value='';this.textContent='';this.hidden=false;this.checked=false;this.disabled=false;this.events={};this.classList={toggle(){}};}
  addEventListener(type,fn){(this.events[type]||=[]).push(fn);}async fire(type,values={}){for(const fn of this.events[type]||[])await fn({preventDefault(){},...values});}
  append(...values){this.children.push(...values);}appendChild(value){this.children.push(value);this.onAppend?.(value);return value;}replaceChildren(...values){this.children=values;}set innerHTML(_){throw Error('Unsafe HTML');}
}
function dom(){
  const names=['status','run','analyze','generate','stop','guest-file','image-file','turns','cost','human','role-rhys','role-rhys_critic','role-qwen','role-guest','backroom','topic','guest-name','transcript','sources','transcript-note','download','guest-status','guest-clear','image-preview','image-status','analysis-prompt','generation-prompt','description','analysis-model','generated-image','image-download','generation-model','analysis','generation'];
  const ids=Object.fromEntries(names.map(name=>[name,new Element()]));ids.turns.value='3';for(const role of ['rhys','rhys_critic','qwen'])ids['role-'+role].checked=true;ids['role-guest'].disabled=true;
  const urls=[],revoked=[];let verify;
  const head=new Element('head'),doc={getElementById:name=>ids[name],head,createElement:tag=>tag==='canvas'?{getContext:()=>({fillRect(){},drawImage(){}}),toBlob:callback=>callback(new Blob([jpeg],{type:'image/jpeg'}))}:new Element(tag)};
  const win={URL:{createObjectURL:blob=>{const url='blob:lab-'+urls.length;urls.push({url,blob});return url;},revokeObjectURL:url=>revoked.push(url)},turnstile:{render(_,options){assert.equal(options.action,'rhysbot_chat');verify=options.callback;return 1;},reset(){}},createImageBitmap:async()=>({width:2000,height:1000,close(){}}),addEventListener(){}};
  Object.defineProperty(win,'sessionStorage',{get(){throw Error('Lab must not use session storage');}});
  return{ids,doc,win,urls,revoked,verify:()=>{head.children[0].onload();verify('proof');}};
}
const config={available:true,turnstile_site_key:'public',backroom_available:true,image_available:true,monthly_budget_usd:10};
test('actual lab mount is idle until clicks; defaults run a bounded labeled transcript and keep guest files local except matching excerpts',async(t)=>{
  const d=dom(),calls=[];
  const app=mountLab(d.doc,d.win,async(url,init={})=>{calls.push({url,init});if(!init.method)return Response.json(config);const body=JSON.parse(init.body);return new Response(streamOf([{type:'meta',sources:[{title:'Writing source',url:'https://example.com/essay'}]},...Array.from({length:body.turns},(_,index)=>({type:'turn',turn:index+1,role:body.participants[index%body.participants.length],text:'Turn '+(index+1)+' <safe text>'})),{type:'done'}]),{headers:{'Content-Type':'text/event-stream'}});});t.after(()=>app.dispose());await app.initialized;assert.equal(calls.length,1);d.verify();
  d.ids.topic.value='Markets coordination';await d.ids.backroom.fire('submit');assert.equal(calls.length,2);let body=JSON.parse(calls[1].init.body);assert.deepEqual(body.participants,['rhys','rhys_critic','qwen']);assert.equal(body.turns,3);assert.equal(body.verification_token,'proof');assert.equal(d.ids.transcript.children.length,3);assert.match(d.ids.transcript.children[1].children[0].textContent,/critic.*same sources/);assert.equal(d.ids.transcript.children[0].children[1].textContent,'Turn 1 <safe text>');assert.equal(d.ids['transcript-note'].textContent,'Complete transcript');assert.match(await d.urls.at(-1).blob.text(),/complete transcript[\s\S]*Qwen · no Rhys corpus/);
  const guestText=JSON.stringify({title:'Markets essay',text:'Markets coordination and institutions.'})+'\n'+JSON.stringify({title:'Kitchen',text:'Baking bread in a warm kitchen.'});d.ids['guest-file'].files=[{name:'guest.jsonl',size:enc.encode(guestText).length,text:async()=>guestText}];await d.ids['guest-file'].fire('change');assert.equal(calls.length,2,'choosing a local guest file must not send it');assert.equal(d.ids['role-guest'].disabled,false);d.ids['role-guest'].checked=true;await d.ids['role-guest'].fire('change');assert.equal(d.ids.turns.value,'4');d.ids['guest-name'].value='Tyler Cowen';await d.ids.backroom.fire('submit');body=JSON.parse(calls.at(-1).init.body);assert.equal(body.guest.name,'Tyler Cowen');assert.equal(body.guest.excerpts.length,1);assert.equal(body.guest.excerpts[0].title,'Markets essay');assert(!JSON.stringify(body).includes('Baking bread'));assert.equal(body.verification_token,undefined);assert.equal(calls.length,3);
  await d.ids['guest-clear'].fire('click');assert.equal(d.ids['role-guest'].checked,false);assert.equal(d.ids['role-guest'].disabled,true);
});
test('Stop cancels a live backroom, preserves a clearly partial downloadable transcript, and sends no automatic retry',async(t)=>{
  const d=dom(),calls=[];let signal,cancelled=false,firstTurn;
  const rendered=new Promise(resolve=>{firstTurn=resolve;});d.ids.transcript.onAppend=firstTurn;
  const app=mountLab(d.doc,d.win,async(url,init={})=>{calls.push({url,init});if(!init.method)return Response.json(config);signal=init.signal;return new Response(new ReadableStream({start(c){c.enqueue(frame({type:'turn',role:'rhys',turn:1,text:'A partial response'}));},cancel(){cancelled=true;}}),{headers:{'Content-Type':'text/event-stream'}});});t.after(()=>app.dispose());await app.initialized;d.verify();d.ids.topic.value='Coordination';const pending=d.ids.backroom.fire('submit');await rendered;await d.ids.stop.fire('click');await pending;assert.equal(signal.aborted,true);assert.equal(cancelled,true);assert.equal(calls.length,2);assert.match(d.ids['transcript-note'].textContent,/Partial transcript/);assert.match(d.ids.status.textContent,/Stopped.*new reservation/);assert.match(await d.urls.at(-1).blob.text(),/partial transcript/);assert.equal(d.ids.download.hidden,false);
});
test('budget refusals and incomplete or invalid transcripts remain visible with no automatic dispatch',async(t)=>{
  for(const response of [()=>Response.json({error:'monthly_budget_reached'},{status:429}),()=>Response.json({error:'monthly_budget_reached'},{status:403}),()=>new Response(streamOf([{type:'turn',role:'rhys',turn:2,text:'Out of order'}]),{headers:{'Content-Type':'text/event-stream'}}),()=>new Response(streamOf([{type:'turn',role:'rhys',turn:1,text:'Only one turn'},{type:'done'}]),{headers:{'Content-Type':'text/event-stream'}})]){
    const d=dom();let count=0;const app=mountLab(d.doc,d.win,async(_,init={})=>{count++;return init.method?response():Response.json(config);});t.after(()=>app.dispose());await app.initialized;d.verify();d.ids.topic.value='Topic';await d.ids.backroom.fire('submit');assert.equal(count,2);assert.match(d.ids.status.textContent,/allowance|invalid turn|before all turns/);assert.match(d.ids['transcript-note'].textContent,/Partial/);
  }
});
test('image input is prepared locally; analyze and generate are separate explicit requests with model attribution and cleanup',async(t)=>{
  const d=dom(),calls=[];
  const app=mountLab(d.doc,d.win,async(url,init={})=>{calls.push({url,init});if(!init.method)return Response.json(config);assert.equal(url,'/api/rhysbot-image');const body=JSON.parse(init.body);return Response.json(body.action==='analyze'?{description:'<literal image description>',model:'Moondream'}:{image:dataURL,model:'FLUX'});});t.after(()=>app.dispose());await app.initialized;d.verify();d.ids['image-file'].files=[new File([jpeg],'input.jpg',{type:'image/jpeg'})];await d.ids['image-file'].fire('change');assert.equal(calls.length,1);assert.equal(d.ids['image-preview'].hidden,false);assert.match(d.ids['image-status'].textContent,/1024 × 512.*No image sent yet/);
  d.ids['analysis-prompt'].value='What is pictured?';await d.ids.analysis.fire('submit');assert.equal(calls.length,2);let body=JSON.parse(calls[1].init.body);assert.equal(body.action,'analyze');assert.equal(body.image,dataURL);assert.equal(d.ids.description.textContent,'<literal image description>');assert.match(d.ids['analysis-model'].textContent,/Moondream/);
  d.ids['generation-prompt'].value='Draw a green library';await d.ids.generation.fire('submit');assert.equal(calls.length,3);body=JSON.parse(calls[2].init.body);assert.equal(body.action,'generate');assert.equal(body.prompt,'Draw a green library');assert.equal(body.image,undefined);assert.equal(body.guest,undefined);assert.equal(d.ids['generated-image'].src,dataURL);assert.equal(d.ids['image-download'].href,dataURL);assert.match(d.ids['generation-model'].textContent,/AI-generated.*FLUX/);app.dispose();assert(d.revoked.includes('blob:lab-0'));
});
