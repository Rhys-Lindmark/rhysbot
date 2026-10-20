const encode = new TextEncoder();
export const GUEST_LIMIT = 2 * 1024 * 1024;
export const IMAGE_LIMIT = 1024 * 1024;
const ROLES = ['rhys','rhys_critic','qwen','guest'];
const LABELS = { rhys: 'AI Rhys · Qwen + Rhys sources', rhys_critic: 'AI Rhys critic · same sources, different prompt', qwen: 'Qwen · no Rhys corpus', guest: 'Guest · prompted simulation from supplied text' };
const STOP_WORDS = new Set(['the','and','for','with','this','that','what','how','why','about','are','you','your','does','should','could','would','from','have','into']);
const terms = text => [...new Set((String(text).toLowerCase().match(/[\p{L}\p{N}]{3,40}/gu) || []).filter(word => !STOP_WORDS.has(word)))];
function chunks(text, title) {
  const characters = [...text.replace(/\r\n?/g,'\n').trim()], result = []; let offset = 0;
  while (offset < characters.length) {
    let end = Math.min(offset+1500,characters.length);
    if (end < characters.length) { const tail = characters.slice(offset+800,end), boundary = Math.max(tail.lastIndexOf('\n'),tail.lastIndexOf(' ')); if (boundary >= 0) end = offset+800+boundary; }
    const value = characters.slice(offset,end).join('').trim(); if (value) result.push({title:[...title].slice(0,160).join(''),text:value}); offset = end;
  }
  return result;
}
export function parseGuestCorpus(text, filename = 'Guest corpus') {
  if (typeof text !== 'string' || encode.encode(text).length > GUEST_LIMIT) throw new Error('Guest corpus must be a UTF-8 text file no larger than 2 MiB.');
  if (!/\.(txt|md|jsonl)$/i.test(filename)) throw new Error('Choose a .txt, .md or .jsonl guest corpus.');
  const documents = [];
  if (/\.jsonl$/i.test(filename)) {
    for (const [index,line] of text.replace(/^\uFEFF/,'').split(/\r?\n/).entries()) {
      if (!line.trim()) continue;
      let row; try { row = JSON.parse(line); } catch { throw new Error(`Guest JSONL line ${index+1} is invalid JSON.`); }
      const content = typeof row === 'string' ? row : row?.text ?? row?.content;
      if (typeof content !== 'string') throw new Error(`Guest JSONL line ${index+1} needs a text or content string.`);
      documents.push(...chunks(content,typeof row?.title === 'string' ? row.title : `${filename} · ${index+1}`));
    }
  } else documents.push(...chunks(text,filename));
  if (!documents.length) throw new Error('The guest corpus contains no readable text.');
  return documents;
}
export function matchGuestExcerpts(documents, topic) {
  const query = terms(topic); if (!query.length) return [];
  return documents.map((document,index) => {
    const words = new Set(terms(document.text+' '+document.title));
    return {document,index,score:query.reduce((sum,word) => sum+(words.has(word)?1:0),0)};
  }).filter(row => row.score > 0).sort((a,b) => b.score-a.score || a.index-b.index).slice(0,4).map(row => ({title:row.document.title.slice(0,160),text:[...row.document.text].slice(0,1500).join('')}));
}
export function validateBackroom({topic,participants,turns,guest}) {
  if (typeof topic !== 'string' || !topic.trim() || [...topic].length > 2000) throw new Error('Enter a topic of 1–2,000 characters.');
  if (!Array.isArray(participants) || participants.length < 2 || participants.length > 4 || new Set(participants).size !== participants.length || participants.some(role=>!ROLES.includes(role))) throw new Error('Choose two to four different participants.');
  if (!Number.isInteger(turns) || turns < 2 || turns > 6) throw new Error('Choose two to six total turns.');
  if (turns < participants.length) throw new Error('Choose at least one turn for each invited participant.');
  if (participants.includes('guest') && (!guest || typeof guest.name !== 'string' || !guest.name.trim() || [...guest.name].length > 80 || !Array.isArray(guest.excerpts) || !guest.excerpts.length || guest.excerpts.length > 4 || guest.excerpts.some(row=>typeof row?.title!=='string'||[...row.title].length>160||typeof row.text!=='string'||!row.text.trim()||[...row.text].length>2000))) throw new Error('Add a guest name and a corpus with excerpts matching this topic.');
  return {action:'backroom',topic:topic.trim(),participants:[...participants],turns,...(participants.includes('guest')?{guest}:{})};
}
export function validateImageFile(file) {
  if (!file || !['image/jpeg','image/png'].includes(file.type) || !Number.isFinite(file.size) || file.size <= 0 || file.size > 10 * 1024 * 1024) throw new Error('Choose a JPEG or PNG image no larger than 10 MiB.');
  return file.type;
}
export function imageDimensions(width,height) {
  if (!Number.isInteger(width)||!Number.isInteger(height)||width<1||height<1||width>20000||height>20000||width*height>40000000) throw new Error('The image dimensions are unsupported (maximum 40 megapixels).');
  const scale = Math.min(1,1024/Math.max(width,height)); return {width:Math.max(1,Math.round(width*scale)),height:Math.max(1,Math.round(height*scale))};
}
function toBase64(bytes) { let value='';for(let offset=0;offset<bytes.length;offset+=8192)value+=String.fromCharCode(...bytes.subarray(offset,offset+8192));return btoa(value); }
export async function prepareImage(file,{createBitmap=globalThis.createImageBitmap,createCanvas=()=>document.createElement('canvas')}={}) {
  validateImageFile(file); if(typeof createBitmap!=='function')throw new Error('Image preparation is unavailable in this browser.');
  const signature=new Uint8Array(await file.slice(0,12).arrayBuffer());
  if(file.type==='image/jpeg'&&!(signature[0]===255&&signature[1]===216&&signature[2]===255)||file.type==='image/png'&&![137,80,78,71,13,10,26,10].every((byte,index)=>signature[index]===byte))throw new Error('The file does not contain a valid JPEG or PNG image.');
  let bitmap,canvas;
  try {
    bitmap=await createBitmap(file,{imageOrientation:'from-image'});const dimensions=imageDimensions(bitmap.width,bitmap.height);canvas=createCanvas();canvas.width=dimensions.width;canvas.height=dimensions.height;
    const context=canvas.getContext('2d');if(!context)throw new Error('Image preparation is unavailable.');context.fillStyle='#fff';context.fillRect(0,0,canvas.width,canvas.height);context.drawImage(bitmap,0,0,canvas.width,canvas.height);
    let blob;for(const quality of [0.85,0.65,0.45,0.25]){blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',quality));if(blob&&blob.size<=IMAGE_LIMIT)break;}
    if(!blob||blob.type!=='image/jpeg'||blob.size>IMAGE_LIMIT)throw new Error('Could not prepare an image below 1 MiB.');
    const dataURL='data:image/jpeg;base64,'+toBase64(new Uint8Array(await blob.arrayBuffer()));return {blob,dataURL,...dimensions};
  } finally {bitmap?.close?.();if(canvas){canvas.width=1;canvas.height=1;}}
}
export function validateGeneratedImage(value) {
  const prefix='data:image/jpeg;base64,';if(typeof value!=='string'||!value.startsWith(prefix)||value.length>prefix.length+Math.ceil(IMAGE_LIMIT/3)*4)throw new Error('The image service returned an invalid image.');
  const encoded=value.slice(prefix.length);if(!encoded||encoded.length%4||!/^[A-Za-z0-9+/]*={0,2}$/.test(encoded))throw new Error('The image service returned invalid image encoding.');
  let raw;try{raw=atob(encoded);}catch{throw new Error('The image service returned invalid image encoding.');}
  if(btoa(raw)!==encoded||raw.length>IMAGE_LIMIT||raw.charCodeAt(0)!==255||raw.charCodeAt(1)!==216||raw.charCodeAt(2)!==255)throw new Error('The image service returned an invalid JPEG.');return value;
}
export async function* labEvents(stream,{signal,maxBytes=512*1024}={}) {
  const reader=stream.getReader(),decoder=new TextDecoder('utf-8',{fatal:true});let pending='',bytes=0;
  const abort=()=>reader.cancel().catch(()=>{});signal?.addEventListener('abort',abort,{once:true});
  try {for(;;){signal?.throwIfAborted();const{value,done}=await reader.read();signal?.throwIfAborted();if(done){pending+=decoder.decode();if(pending.trim())throw new Error('The backroom stream ended with an incomplete event.');break;}bytes+=value.byteLength;if(bytes>maxBytes)throw new Error('The backroom transcript exceeded its size limit.');pending+=decoder.decode(value,{stream:true});let separator;while((separator=/\r?\n\r?\n/.exec(pending))){const frame=pending.slice(0,separator.index);pending=pending.slice(separator.index+separator[0].length);const data=frame.split(/\r?\n/).filter(line=>line.startsWith('data:')).map(line=>line.slice(5).replace(/^ /,'')).join('\n');if(!data)continue;const event=JSON.parse(data);if(!event||typeof event.type!=='string')throw new Error('Invalid backroom stream event.');yield event;}}}
  finally{signal?.removeEventListener('abort',abort);try{await reader.cancel();}catch{}reader.releaseLock();}
}
export function safeURL(value) {try{const url=new URL(value);return ['https:','http:'].includes(url.protocol)&&!url.username&&!url.password?url.href:null;}catch{return null;}}
export function transcriptText(topic,rows,{complete=false,guestName='Guest'}={}) {
  return [`Rhysbot backroom — ${complete?'complete transcript':'partial transcript'}`,`Topic: ${topic}`,'AI simulations; statements may be incorrect.',...rows.map(row=>`\n${row.turn}. ${row.role==='guest'?guestName+' · prompted guest simulation':LABELS[row.role]}\n${row.text}`)].join('\n');
}
const friendly=code=>({monthly_budget_reached:'The shared $10 monthly allowance has been reached.',hourly_limit:'This client’s hourly request limit has been reached.',verification_required:'Complete verification, then try again.',verification_failed:'Verification expired. Complete it again.',backroom_unavailable:'The backroom is currently unavailable.',image_unavailable:'Image tools are currently unavailable.'})[code]||'The request could not finish. No automatic retry was sent.';

export function mountLab(doc=document,win=window,fetcher=fetch) {
  const $=id=>doc.getElementById(id),el=(tag,cls,text)=>{const node=doc.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=String(text);return node;};
  let config,verification='',sessionKnown=false,widget,scriptLoaded=false,active,guest=[],prepared,previewURL,downloadURL,loadEpoch=0,imageEpoch=0;
  const say=(message,error=false)=>{$('status').textContent=message;$('status').classList.toggle('error',error);};
  const selected=()=>ROLES.filter(role=>$('role-'+role).checked);
  function ready(){const authorized=sessionKnown||!!verification,busy=!!active;$('run').disabled=!config?.available||!config.backroom_available||!authorized||busy;$('analyze').disabled=!config?.available||!config.image_available||!authorized||busy||!prepared;$('generate').disabled=!config?.available||!config.image_available||!authorized||busy;$('stop').hidden=!busy;$('guest-file').disabled=busy;$('image-file').disabled=busy;$('cost').textContent=`Reserves $${(Number($('turns').value)*0.005).toFixed(3)} for ${$('turns').value} total messages. Another run starts a new reservation.`;}
  function verified(){sessionKnown=true;verification='';$('human').hidden=true;ready();}
  async function needVerification(){sessionKnown=false;verification='';$('human').hidden=false;ready();if(win.turnstile&&widget!==undefined){win.turnstile.reset(widget);return;}if(scriptLoaded||!config?.turnstile_site_key)return;scriptLoaded=true;const script=el('script');script.src='https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';script.async=true;script.onload=()=>{widget=win.turnstile.render('#human',{sitekey:config.turnstile_site_key,action:'rhysbot_chat',callback:token=>{verification=token;say('Ready. Choose an experiment.');ready();},'expired-callback':()=>{verification='';ready();},'error-callback':()=>{verification='';say('Verification is unavailable.',true);ready();}});};script.onerror=()=>{scriptLoaded=false;say('Verification could not load.',true);};doc.head.appendChild(script);}
  async function checked(response){if(response.ok){verified();return response;}let code;try{code=(await response.json()).error;}catch{}if(['verification_required','verification_failed'].includes(code))await needVerification();throw new Error(friendly(code));}
  async function request(path,payload,controller){return checked(await fetcher(path,{method:'POST',credentials:'same-origin',redirect:'error',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,...(verification?{verification_token:verification}:{})}),signal:controller.signal}));}
  async function operation(work,timeoutMs){if(active)return;const controller=new AbortController();active=controller;ready();const timeout=setTimeout(()=>controller.abort(),timeoutMs);try{await work(controller);}catch(error){say(controller.signal.aborted?'Stopped. Results may be partial. Provider work may continue; trying again starts a new reservation.':error.message,true);}finally{clearTimeout(timeout);if(active===controller)active=null;ready();}}
  function renderSources(sources){if(!Array.isArray(sources))return;const details=el('details','sources'),list=el('ol');details.appendChild(el('summary','',`${Math.min(8,sources.length)} Rhys source excerpts`));for(const source of sources.slice(0,8)){if(!source||typeof source!=='object')continue;const li=el('li'),href=safeURL(source.url),title=String(source.title||source.id||'Source').slice(0,240);if(href){const link=el('a','',title);link.href=href;link.target='_blank';link.rel='noopener noreferrer';li.appendChild(link);}else li.textContent=title;list.appendChild(li);}details.appendChild(list);$('sources').replaceChildren(details);}
  $('backroom').addEventListener('submit',async event=>{event.preventDefault();if(active)return;if(!config?.backroom_available||!config.available){say('The backroom is currently unavailable.',true);return;}if(!sessionKnown&&!verification){await needVerification();return;}let payload;try{const participants=selected(),topic=$('topic').value.trim(),excerpts=participants.includes('guest')?matchGuestExcerpts(guest,topic):[];payload=validateBackroom({topic,participants,turns:Number($('turns').value),guest:{name:$('guest-name').value.trim(),excerpts}});}catch(error){say(error.message,true);return;}
    await operation(async controller=>{const rows=[];let complete=false;$('transcript').replaceChildren();$('sources').replaceChildren();$('transcript-note').textContent='Partial transcript · running';$('download').hidden=true;if(downloadURL){win.URL.revokeObjectURL(downloadURL);downloadURL=undefined;}
      const downloadable=()=>{if(downloadURL)win.URL.revokeObjectURL(downloadURL);downloadURL=win.URL.createObjectURL(new Blob([transcriptText(payload.topic,rows,{complete,guestName:payload.guest?.name})],{type:'text/plain;charset=utf-8'}));$('download').href=downloadURL;$('download').hidden=!rows.length;};
      say('Starting the backroom…');try{const response=await request('/api/rhysbot-lab',payload,controller);if(!response.headers.get('Content-Type')?.includes('text/event-stream')||!response.body)throw new Error('The backroom did not return a transcript stream.');for await(const item of labEvents(response.body,{signal:controller.signal})){if(item.type==='meta'){renderSources(item.sources);continue;}if(item.type==='error')throw new Error(typeof item.message==='string'?item.message.slice(0,400):'The backroom could not finish.');if(item.type==='done'){if(rows.length!==payload.turns)throw new Error('The backroom ended before all turns completed.');complete=true;break;}if(item.type!=='turn')continue;if(!payload.participants.includes(item.role)||!Number.isInteger(item.turn)||item.turn!==rows.length+1||item.turn>payload.turns||typeof item.text!=='string'||!item.text.trim()||encode.encode(item.text).length>40000)throw new Error('The backroom returned an invalid turn.');rows.push({role:item.role,turn:item.turn,text:item.text});const article=el('article',`turn ${item.role}`);article.append(el('h3','',`${item.turn} · ${item.role==='guest'?payload.guest.name+' · prompted guest simulation':LABELS[item.role]}`),el('p','turn-text',item.text));$('transcript').appendChild(article);say(`Turn ${rows.length} of ${payload.turns}.`);downloadable();}if(!complete)throw new Error('The backroom stream ended before completion.');say('Backroom complete. No background conversation is running.');}finally{$('transcript-note').textContent=complete?'Complete transcript':'Partial transcript · stopped or incomplete';downloadable();}
    },180000);
  });
  for(const role of ROLES)$('role-'+role).addEventListener('change',()=>{if(selected().length>Number($('turns').value))$('turns').value=String(selected().length);ready();});
  $('turns').addEventListener('change',ready);$('stop').addEventListener('click',()=>active?.abort());
  $('guest-file').addEventListener('change',async()=>{const epoch=++loadEpoch;guest=[];$('role-guest').checked=false;$('role-guest').disabled=true;try{const file=$('guest-file').files?.[0];if(!file)return;if(file.size>GUEST_LIMIT)throw new Error('Guest corpus must be no larger than 2 MiB.');const raw=await file.text();const parsed=parseGuestCorpus(raw,file.name);if(epoch!==loadEpoch)return;guest=parsed;$('role-guest').disabled=false;$('guest-status').textContent=`${guest.length} local text chunks ready. Only up to four topic-matched excerpts are sent when you run with the guest selected.`;}catch(error){if(epoch===loadEpoch)$('guest-status').textContent=error.message;}});
  $('guest-clear').addEventListener('click',()=>{loadEpoch++;guest=[];$('guest-file').value='';$('role-guest').checked=false;$('role-guest').disabled=true;$('guest-status').textContent='No guest corpus loaded.';});
  $('image-file').addEventListener('change',async()=>{const epoch=++imageEpoch;prepared=undefined;if(previewURL){win.URL.revokeObjectURL(previewURL);previewURL=undefined;}$('image-preview').hidden=true;ready();try{const file=$('image-file').files?.[0];if(!file)return;$('image-status').textContent='Preparing image locally…';const result=await prepareImage(file,{createBitmap:win.createImageBitmap?.bind(win),createCanvas:()=>doc.createElement('canvas')});if(epoch!==imageEpoch)return;prepared=result;previewURL=win.URL.createObjectURL(result.blob);$('image-preview').src=previewURL;$('image-preview').hidden=false;$('image-status').textContent=`Prepared ${result.width} × ${result.height} JPEG · ${Math.ceil(result.blob.size/1024)} KiB. No image sent yet.`;}catch(error){if(epoch===imageEpoch)$('image-status').textContent=error.message;}finally{ready();}});
  async function imageAction(action){if(active)return;if(!config?.available||!config.image_available){say('Image tools are currently unavailable.',true);return;}if(!sessionKnown&&!verification){await needVerification();return;}const prompt=$(action==='analyze'?'analysis-prompt':'generation-prompt').value.trim();if(!prompt||[...prompt].length>1500){say('Enter an image prompt of 1–1,500 characters.',true);return;}if(action==='analyze'&&!prepared){say('Choose and prepare an image first.',true);return;}
    await operation(async controller=>{say(action==='analyze'?'Analyzing with Moondream…':'Generating with FLUX…');const response=await request('/api/rhysbot-image',{action,prompt,...(action==='analyze'?{image:prepared.dataURL}:{})},controller);const result=await response.json();controller.signal.throwIfAborted();if(typeof result?.model!=='string'||!result.model.trim())throw new Error('Image result has no model attribution.');if(action==='analyze'){if(typeof result.description!=='string'||!result.description.trim()||result.description.length>30000)throw new Error('The image service returned no usable description.');$('description').textContent=result.description;$('analysis-model').textContent=`AI image analysis · ${result.model.slice(0,200)}`;}else{const image=validateGeneratedImage(result.image);$('generated-image').src=image;$('generated-image').hidden=false;$('image-download').href=image;$('image-download').hidden=false;$('generation-model').textContent=`AI-generated image · ${result.model.slice(0,200)}`;}say('Image experiment complete.');},180000);
  }
  $('analysis').addEventListener('submit',event=>{event.preventDefault();return imageAction('analyze');});$('generation').addEventListener('submit',event=>{event.preventDefault();return imageAction('generate');});
  const dispose=()=>{active?.abort();loadEpoch++;imageEpoch++;guest=[];prepared=undefined;if(previewURL)win.URL.revokeObjectURL(previewURL);if(downloadURL)win.URL.revokeObjectURL(downloadURL);previewURL=downloadURL=undefined;};win.addEventListener('pagehide',dispose);
  const initialized=(async()=>{try{const response=await fetcher('/api/rhysbot-lab',{credentials:'same-origin',redirect:'error',cache:'no-store',signal:AbortSignal.timeout(10000)});if(!response.ok)throw new Error('The lab is unavailable.');config=await response.json();if(!config.available)throw new Error('The lab is currently unavailable.');await needVerification();say('Complete verification, then choose an experiment.');}catch(error){say(error.message,true);}ready();})();
  ready();return {initialized,dispose};
}
if(typeof document!=='undefined'&&typeof window!=='undefined')mountLab();
