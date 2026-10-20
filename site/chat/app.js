import {createVoiceStream} from './voice-stream.js';
const encoder = new TextEncoder();
export const MAX_HISTORY_BYTES = 12000;
export const MAX_PROMPT_BYTES = 4000;
export function clipUTF8(text, maxBytes) {
  let size = 0, result = '';
  for (const char of String(text)) { const bytes = encoder.encode(char).length; if (size + bytes > maxBytes) break; result += char; size += bytes; }
  return result;
}
export function histories(turns, prompt, mode = 'compare') {
  if (!prompt.trim() || encoder.encode(prompt).length > MAX_PROMPT_BYTES) throw new Error('Please keep your message within 4,000 UTF-8 bytes.');
  const rows = turns.slice(-5); // Five complete pairs + current user = eleven messages.
  const build = channel => [...rows.flatMap(t => [{ role: 'user', content: t.user }, { role: 'assistant', content: t[channel] }]), { role: 'user', content: prompt }];
  const channels = ['rhys','nanochat'].includes(mode) ? ['rhys'] : mode === 'qwen' ? ['qwen'] : ['rhys','qwen'];
  let result;
  for (;;) {
    result = Object.fromEntries(channels.map(c => [c, build(c)]));
    if (Object.values(result).every(messages => messages.every(m => typeof m.content === 'string' && m.content.trim()) && messages.reduce((n,m) => n + encoder.encode(m.content).length, 0) <= MAX_HISTORY_BYTES)) break;
    if (!rows.length) throw new Error('The conversation is too long. Start a new chat.');
    rows.shift(); // Shared window preserves the same user turns on both sides.
  }
  return result;
}
export function safeSourceURL(value) {
  try { const url = new URL(value); return ['https:','http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch { return null; }
}
export function requestPayload(context, mode, verification = '') {
  let messages = context.rhys || context.qwen, qwen = context.rhys && context.qwen ? context.qwen : null;
  let payload;
  for (;;) {
    payload = { messages, ...(qwen ? { qwen_messages: qwen } : {}), ...(verification ? { verification_token: verification } : {}), ...(['checkpoints','nanochat'].includes(mode) ? {} : { mode }) };
    // JSON escaping can expand otherwise valid history; stay below server's 30KiB body.
    if (encoder.encode(JSON.stringify(payload)).length <= 28000) return payload;
    if (messages.length === 1) throw new Error('The encoded request is too long. Shorten your message.');
    messages = messages.slice(2); if (qwen) qwen = qwen.slice(2);
  }
}
export async function* sseEvents(stream, { signal, maxBytes = 512000 } = {}) {
  const reader = stream.getReader(), decoder = new TextDecoder('utf-8', { fatal: true });
  let pending = '', size = 0;
  const abort = () => reader.cancel().catch(() => {});
  signal?.addEventListener('abort', abort, { once: true });
  const parse = frame => {
    const raw = frame.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).replace(/^ /, '')).join('\n');
    if (!raw || raw === '[DONE]') return null;
    const event = JSON.parse(raw); if (!event || typeof event !== 'object' || typeof event.type !== 'string') throw new Error('Invalid stream event.'); return event;
  };
  try {
    while (true) {
      if (signal?.aborted) throw new DOMException('Stopped', 'AbortError');
      const { value, done } = await reader.read();
      if (signal?.aborted) throw new DOMException('Stopped', 'AbortError');
      if (done) { pending += decoder.decode(); if (pending.trim()) { const event = parse(pending); if (event) yield event; } break; }
      size += value.byteLength; if (size > maxBytes) throw new Error('The response exceeded its stream limit.');
      pending += decoder.decode(value, { stream: true });
      let boundary;
      while ((boundary = /\r?\n\r?\n/.exec(pending))) { const frame = pending.slice(0, boundary.index); pending = pending.slice(boundary.index + boundary[0].length); const event = parse(frame); if (event) yield event; }
    }
  } finally { signal?.removeEventListener('abort', abort); try { await reader.cancel(); } catch {} reader.releaseLock(); }
}
export function newTurn(prompt, mode) {
  const channels = ['rhys','nanochat'].includes(mode) ? ['rhys'] : mode === 'qwen' ? ['qwen'] : ['rhys','qwen'];
  return { user: prompt, channels, rhys: '', qwen: '', completed: new Set(), failed: new Set() };
}
export function applyEvent(turn, event) {
  if (!turn.channels.includes(event.channel)) return;
  if (event.type === 'delta' && typeof event.text === 'string') {
    if (turn.completed.has(event.channel) || turn.failed.has(event.channel)) return;
    if (encoder.encode(turn[event.channel] + event.text).length > 128000) throw new Error('Reply exceeded its text limit.');
    turn[event.channel] += event.text;
  } else if (event.type === 'done') turn.completed.add(event.channel);
  else if (event.type === 'error') turn.failed.add(event.channel);
}
export function completeTurn(turn) { return turn.channels.every(c => turn.completed.has(c) && !turn.failed.has(c) && turn[c].trim()); }
export async function boundedAudio(response, limit = 8 * 1024 * 1024) {
  if (!response.ok || response.headers.get('Content-Type')?.split(';')[0] !== 'audio/wav' || !response.body) throw new Error('AI voice is unavailable.');
  const size = response.headers.get('X-Rhysbot-Audio-Bytes') || response.headers.get('Content-Length');
  if (size && (!/^\d+$/.test(size) || Number(size) > limit)) throw new Error('AI voice response is too large.');
  const reader = response.body.getReader(); let total = 0; const chunks = [];
  try { for (;;) { const { done, value } = await reader.read(); if (done) break; total += value.byteLength; if (total > limit) throw new Error('AI voice response is too large.'); chunks.push(value); } }
  finally { try { await reader.cancel(); } catch {} reader.releaseLock(); }
  if (!total || (size && total !== Number(size))) throw new Error('AI voice response was incomplete.');
  const blob = new Blob(chunks, { type: 'audio/wav' });
  const expected = response.headers.get('X-Rhysbot-Audio-SHA256');
  if (expected) {
    const actual = [...new Uint8Array(await crypto.subtle.digest('SHA-256',await blob.arrayBuffer()))].map(x=>x.toString(16).padStart(2,'0')).join('');
    if (actual !== expected) throw new Error('AI voice download was incomplete. Choose Resume to download it again.');
  }
  return blob;
}
export function voiceWait(ms,signal) {
  return new Promise((resolve,reject)=>{
    const abort=()=>{clearTimeout(timer);signal?.removeEventListener('abort',abort);reject(new DOMException('Stopped','AbortError'));};
    const timer=setTimeout(()=>{signal?.removeEventListener('abort',abort);resolve();},ms);
    signal?.addEventListener('abort',abort,{once:true});if(signal?.aborted)abort();
  });
}
export async function requestVoice(fetcher,{text,requestId,verification,signal,onProgress=()=>{},onAccepted=()=>{},wait=voiceWait}) {
  let payload={text,request_id:requestId,...(verification?{verification_token:verification}:{})};
  const deadline=Date.now()+480000;
  for (;;) {
    signal?.throwIfAborted();
    const response=await fetcher('/api/rhysbot-voice',{method:'POST',credentials:'same-origin',redirect:'error',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:AbortSignal.any([signal||new AbortController().signal,AbortSignal.timeout(30000)])});
    if(!response.ok) {let code;try{code=(await response.json()).error;}catch{}const error=new Error(friendly(code));error.code=code;throw error;}
    onAccepted();
    if(response.status!==202)return boundedAudio(response);
    const {state}=await response.json();
    if(!['queued','loading_voice','speaking'].includes(state))throw new Error('Voice preparation could not finish. Choose Resume to check the same request.');
    onProgress(state);
    if(Date.now()>deadline)throw new Error('Voice is taking too long. Choose Resume to check the same request without generating again.');
    await wait(2500,signal);payload={action:'status',request_id:requestId};
  }
}
// Ordered independently verified WAV parts; the complete clip remains available
// for replay and seeking after synthesis finishes.
export async function joinVoiceParts(parts) {
  let bytes=0;const pcm=[];
  for(const blob of parts){const raw=new Uint8Array(await blob.arrayBuffer()),v=new DataView(raw.buffer);if(raw.length<44||new TextDecoder().decode(raw.slice(0,4))!=='RIFF'||new TextDecoder().decode(raw.slice(8,12))!=='WAVE'||v.getUint16(20,true)!==1||v.getUint16(22,true)!==1||v.getUint32(24,true)!==24000||v.getUint16(34,true)!==16||new TextDecoder().decode(raw.slice(36,40))!=='data'||v.getUint32(40,true)!==raw.length-44)throw new Error('Unsupported voice part.');pcm.push(raw.slice(44));bytes+=raw.length-44;if(bytes+44>8*1024*1024)throw new Error('Voice response is too large.');}
  if(!pcm.length)throw new Error('No voice audio returned.');
  const header=new Uint8Array(await parts[0].slice(0,44).arrayBuffer()),view=new DataView(header.buffer);view.setUint32(4,36+bytes,true);view.setUint32(40,bytes,true);return new Blob([header,...pcm],{type:'audio/wav'});
}
export async function requestVoiceParts(fetcher,{text,requestId,verification,signal,onProgress=()=>{},onAccepted=()=>{},onChunk=()=>{},parts=[],wait=voiceWait}) {
  let payload={text,request_id:requestId,...(verification?{verification_token:verification}:{})},total,bytes=parts.reduce((n,p)=>n+p.size,0);const deadline=Date.now()+480000;
  for(;;){
    signal?.throwIfAborted();
    const response=await fetcher('/api/rhysbot-voice',{method:'POST',credentials:'same-origin',redirect:'error',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:AbortSignal.any([signal||new AbortController().signal,AbortSignal.timeout(30000)])});
    if(!response.ok){let code;try{code=(await response.json()).error;}catch{}const error=new Error(friendly(code));error.code=code;throw error;}
    onAccepted();
    if(response.status===202){const state=await response.json();if(!['queued','loading_voice','speaking'].includes(state.state))throw new Error('Voice progress is unavailable.');onProgress(state.state,state);if(Date.now()>deadline)throw new Error('Voice is taking too long. Resume checks the same job.');await wait(1000,signal);}
    else {
      const index=response.headers.get('X-Rhysbot-Audio-Part'),count=response.headers.get('X-Rhysbot-Audio-Parts');
      const blob=await boundedAudio(response);signal?.throwIfAborted();
      if(index===null){onChunk(blob,{index:0,total:1});return blob;} // Earlier backend compatibility.
      if(!/^\d+$/.test(index)||!/^\d+$/.test(count||'')||Number(index)!==parts.length||Number(count)<1||Number(count)>32||Number(index)>=Number(count)||(total!==undefined&&total!==Number(count)))throw new Error('Voice parts arrived out of order.');
      total=Number(count);bytes+=blob.size;if(bytes>8*1024*1024)throw new Error('Voice response is too large.');parts.push(blob);onChunk(blob,{index:Number(index),total});
      if(parts.length===total)return joinVoiceParts(parts);
    }
    payload={action:'status',request_id:requestId,part:parts.length};
  }
}
export function renderText(element, value) { element.textContent = String(value ?? ''); }

const MODE_NOTES = {
  compare: 'The same unmodified Qwen3-30B-A3B model on both sides; only the left receives Rhys source excerpts. Each side keeps its own answers. This is not the trained checkpoint.',
  rhys: 'Qwen3-30B-A3B with retrieved Rhys source excerpts. This is a source-grounded conversation, not the trained 4B checkpoint.',
  qwen: 'Unmodified Qwen3-30B-A3B with no Rhys corpus supplied. This conversation does not share Rhysbot’s previous answers.',
  nanochat: 'A tiny 4-layer model with a 512-token context, trained from random weights and followed by short supervised training. Limited quality; no Qwen, retrieved sources or hidden fallback. Each reply includes its saved checkpoint run and weights hash.',
  checkpoints: 'Actual saved Qwen3.5-4B adaptation versus the same 4B base with the adapter disabled. No retrieved corpus context is added. Cold starts can be slow; experimental training does not guarantee reliable answers.',
};
function friendly(code) {
  return ({ verification_required: 'Please complete verification to continue.', verification_failed: 'Verification expired or failed. Complete it again.', monthly_budget_reached: 'The shared $10 monthly chat and voice budget has been reached.', hourly_limit: 'This client’s hourly request limit has been reached.', model_busy: 'The model is busy. No automatic retry was sent.', recovery_required: 'The model requires operator reconciliation. No retry was sent.', chat_not_configured: 'Chat is not configured yet.', chat_temporarily_unavailable: 'Chat is temporarily unavailable.', nanochat_unavailable: 'Pure nanochat Rhys is unavailable. No fallback was used.', nanochat_temporarily_unavailable: 'Pure nanochat Rhys could not finish. No fallback or automatic retry was used.', nanochat_context_too_long: 'This conversation exceeds nanochat’s small context window. Start a new chat or shorten your message; no fallback was used.' })[code] || 'The request could not finish. No automatic retry was sent.';
}

export const CONVERSATION_KEY = 'rhysbot.conversations.v1';
const MODES = ['rhys','compare','qwen','checkpoints','nanochat'];
const emptyConversations = () => Object.fromEntries(MODES.map(mode => [mode, []]));
export function sourceLabel(kind) {
  return ({ hee_transcript: 'How Everything Evolved', podcast_transcript: 'Podcasts / YouTube', youtube_transcript: 'Podcasts / YouTube', x: 'X', substack: 'Writing', linked_writing: 'Writing', anki: 'Anki' })[kind] || 'Public source';
}
function sourceProvenance(kind) {
  if (kind === 'anki') return 'Curated study note; not necessarily Rhys’s views';
  if (kind === 'hee_transcript') return 'Rhys’s narration';
  if (['podcast_transcript','youtube_transcript'].includes(kind)) return 'Rhys’s speech · automatic transcript';
  return 'Published by Rhys · may quote others';
}
function sourceMetadata(sources) {
  return (Array.isArray(sources) ? sources : []).slice(0,8).filter(s => s && typeof s === 'object').map(s => Object.fromEntries(['id','title','url','kind','attribution'].filter(key => typeof s[key] === 'string').map(key => [key,s[key].slice(0,key === 'url' ? 2000 : 600)])));
}
export function nanochatProvenance(value) {
  if (!value || typeof value !== 'object' || typeof value.weights_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(value.weights_sha256) || typeof value.checkpoint_run_id !== 'string' || !value.checkpoint_run_id.trim() || value.checkpoint_run_id.length > 200 || !Number.isSafeInteger(value.input_tokens) || value.input_tokens < 1 || !Number.isSafeInteger(value.output_tokens) || value.output_tokens < 1 || !Number.isFinite(value.elapsed_seconds) || value.elapsed_seconds < 0) return null;
  return Object.fromEntries(['weights_sha256','checkpoint_run_id','input_tokens','output_tokens','elapsed_seconds'].map(key => [key,value[key]]));
}
export function restoreConversation(raw) {
  const fallback = { mode: 'rhys', conversations: emptyConversations() };
  if (typeof raw !== 'string' || encoder.encode(raw).length > 256000) return fallback;
  try {
    const value = JSON.parse(raw);
    if (value?.version !== 1 || !MODES.includes(value.mode) || !value.conversations || typeof value.conversations !== 'object') return fallback;
    const conversations = emptyConversations();
    for (const mode of MODES) {
      const channels = ['rhys','nanochat'].includes(mode) ? ['rhys'] : mode === 'qwen' ? ['qwen'] : ['rhys','qwen'];
      if (!Array.isArray(value.conversations[mode])) continue;
      for (const row of value.conversations[mode].slice(-30)) {
        if (!row || typeof row.user !== 'string' || !row.user.trim() || encoder.encode(row.user).length > MAX_PROMPT_BYTES || channels.some(c => typeof row[c] !== 'string' || !row[c].trim() || encoder.encode(row[c]).length > 128000)) continue;
        const provenance = mode === 'nanochat' ? nanochatProvenance(row.provenance) : null;
        if (mode === 'nanochat' && !provenance) continue;
        conversations[mode].push({ user: row.user, rhys: channels.includes('rhys') ? row.rhys : '', qwen: channels.includes('qwen') ? row.qwen : '', sources: mode === 'nanochat' ? [] : sourceMetadata(row.sources), ...(provenance ? {provenance} : {}) });
      }
    }
    return { mode: value.mode, conversations };
  } catch { return fallback; }
}

export function mount(doc = document, win = window, fetcher = fetch) {
  const $ = id => doc.getElementById(id); const thread = $('thread');
  let saved; try { saved = win.sessionStorage?.getItem(CONVERSATION_KEY); } catch {}
  const restored = restoreConversation(saved);
  let conversations = restored.conversations;
  let config, mode = restored.mode, turns = conversations[mode], active, voiceActive, timer, verification = '', sessionKnown = false, widget, turnstileLoaded = false, recognition, listening = false, generation = 0;
  const audioURLs = new Set(), audios = new Set();
  $('mode').value = mode;
  function persist() {
    conversations[mode] = turns;
    try {
      let value = JSON.stringify({ version: 1, mode, conversations });
      while (encoder.encode(value).length > 240000) {
        const longest = MODES.reduce((a,b) => conversations[a].length >= conversations[b].length ? a : b);
        if (!conversations[longest].length) break;
        conversations[longest].shift();
        value = JSON.stringify({ version: 1, mode, conversations });
      }
      win.sessionStorage?.setItem(CONVERSATION_KEY,value);
    } catch {} // Storage may be disabled or full; the live conversation still works.
  }
  const element = (tag, cls, text) => { const el = doc.createElement(tag); if (cls) el.className = cls; if (text !== undefined) renderText(el, text); return el; };
  const status = (text, error = false) => { renderText($('status'), text); $('status').classList.toggle('error', error); };
  const modeAvailable = () => !!config?.available && (mode !== 'nanochat' || config.nanochat_available === true);
  const modeNote = () => MODE_NOTES[mode] + (mode === 'nanochat' && config?.nanochat_available !== true ? ' Currently unavailable; no substitute model will be used.' : '');
  function ready() { $('send').disabled = !modeAvailable() || !!active || (!sessionKnown && !verification) || !$('message').value.trim(); $('mode').disabled = !!active; $('mic').disabled = !!active || !recognition; $('stop').hidden = !active; }
  function verifiedSession() { sessionKnown = true; verification = ''; $('human').hidden = true; ready(); }
  async function needVerification() {
    sessionKnown = false; verification = ''; $('human').hidden = false; ready();
    if (win.turnstile && widget !== undefined) { win.turnstile.reset(widget); return; }
    if (turnstileLoaded || !config?.turnstile_site_key) return;
    turnstileLoaded = true;
    const script = doc.createElement('script'); script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'; script.async = true;
    script.onload = () => { widget = win.turnstile.render('#human', { sitekey: config.turnstile_site_key, action: 'rhysbot_chat', callback: token => { verification = token; status('Ready.'); ready(); }, 'expired-callback': () => { verification = ''; ready(); }, 'error-callback': () => { verification = ''; status('Verification unavailable. You can try again later.', true); ready(); } }); };
    script.onerror = () => { turnstileLoaded = false; status('Verification could not load.', true); };
    doc.head.appendChild(script);
  }
  async function checked(response) {
    if (response.ok) return response;
    let code; try { code = (await response.json()).error; } catch {}
    if (['verification_required','verification_failed'].includes(code)) await needVerification();
    throw new Error(friendly(code));
  }
  const streamPlayers=new Set(), audioContexts=new Set();
  function clearAudio() {
    voiceActive?.abort(); voiceActive = null;
    for(const player of streamPlayers)player.stop();streamPlayers.clear();
    for(const ctx of audioContexts)ctx.close().catch(()=>{});audioContexts.clear();
    for (const audio of audios) { audio.pause(); audio.removeAttribute('src'); audio.load?.(); } audios.clear();
    for (const url of audioURLs) win.URL.revokeObjectURL(url); audioURLs.clear();
  }
  function reset() {
    generation++; active?.abort(); active = null; clearInterval(timer); recognition?.abort(); clearAudio(); conversations = emptyConversations(); turns = conversations[mode]; thread.replaceChildren(); $('empty').hidden = false; $('message').value = ''; try { win.sessionStorage?.removeItem(CONVERSATION_KEY); } catch {} status(config?.available ? 'New conversation. No request sent.' : 'Chat is unavailable.'); ready(); $('message').focus();
  }
  function sourcesFor(card, sources) {
    if (!Array.isArray(sources) || !sources.length) return;
    const kinds = [...new Set(sources.slice(0,8).map(source => sourceLabel(source?.kind)))];
    const details = element('details','sources'), summary = element('summary','',`Sources · ${kinds.join(' · ')} (${Math.min(sources.length,8)})`), list = element('ol');
    for (const source of sources.slice(0,8).filter(s => s && typeof s === 'object')) {
      const li = element('li'), href = safeSourceURL(source.url); const title = String(source.title || source.id || 'Source').slice(0,240);
      if (href) { const a = element('a','',title); a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer'; li.appendChild(a); } else li.appendChild(element('span','',title));
      li.appendChild(element('span','source-meta',`${sourceLabel(source.kind)} · ${sourceProvenance(source.kind)}`)); list.appendChild(li);
    }
    details.append(summary,list); card.appendChild(details);
  }
  function addStreamingVoice(card,reply) {
    const button=element('button','voice','Listen · AI-generated Rhys voice');button.type='button';
    const progress=element('span','answer-status');progress.setAttribute('role','status');
    const audio=element('audio','audio');audio.controls=true;audio.hidden=true;audio.preload='none';audio.setAttribute('aria-label','AI-generated Rhys voice');audios.add(audio);
    const spoken=[...reply].slice(0,2000).join('');let url,ownController;
    if([...reply].length>2000)renderText(progress,'Reads the first 2,000 characters.');
    button.addEventListener('click',async()=>{
      if(ownController&&voiceActive===ownController){ownController.abort();audio.pause();return;}
      if(url){audio.src=url;audio.hidden=false;await audio.play().catch(()=>status('Use the player to listen.'));return;}
      if(voiceActive){status('One voice request is already in progress.');return;}
      if(!sessionKnown&&!verification){await needVerification();status('Complete verification, then choose Listen again.');return;}
      // Resume in the click gesture, before the first asynchronous network wait.
      const ctx=new (win.AudioContext||win.webkitAudioContext)();audioContexts.add(ctx);const resumed=ctx.resume();
      const controller=new AbortController(),epoch=generation;ownController=controller;voiceActive=controller;
      let player;renderText(button,'Stop voice');renderText(progress,'Starting Rhys voice…');
      try{
        await resumed;
        player=createVoiceStream({audioContext:ctx,text:spoken,requestId:win.crypto.randomUUID(),verification,fetcher,signal:controller.signal,onFirstPlayback:timing=>{
          if(epoch!==generation)return;verifiedSession();renderText(progress,`Streaming Rhys voice · ${((timing.firstScheduledAtMs-timing.startedAtMs)/1000).toFixed(2)}s to first audio`);
        }});streamPlayers.add(player);
        const result=await player.finished;if(epoch!==generation||controller.signal.aborted)return;
        verifiedSession();url=win.URL.createObjectURL(result.wav);audioURLs.add(url);audio.src=url;audio.hidden=false;
        await player.playbackFinished;
        if(controller.signal.aborted)throw new DOMException('Stopped','AbortError');
        if(epoch===generation&&!controller.signal.aborted){renderText(button,'Play again · AI-generated Rhys voice');renderText(progress,`Rhys voice ready · ${((result.firstScheduledAtMs-result.startedAtMs)/1000).toFixed(2)}s to first audio · replay is local`);}
      }catch(error){
        if(epoch===generation){if(['verification_required','verification_failed'].includes(error.code))await needVerification();renderText(progress,controller.signal.aborted?(url?'Voice stopped. Play again uses the saved audio.':'Voice stopped. Another try starts a new request.'):error.message);renderText(button,url?'Play again · AI-generated Rhys voice':'Try voice again');}
      }finally{
        if(player){player.stop();streamPlayers.delete(player);}audioContexts.delete(ctx);await ctx.close().catch(()=>{});
        if(voiceActive===controller)voiceActive=null;ownController=null;
      }
    });
    card.append(button,progress,audio);
  }
  function addVoice(card, reply) {
    if (!config?.voice_available || !reply.trim()) return;
    if(config.voice_stream_available&&(win.AudioContext||win.webkitAudioContext)){addStreamingVoice(card,reply);return;}
    const button = element('button','voice','Listen · AI-generated Rhys voice'); button.type = 'button';
    const audio = element('audio','audio'); audio.controls = true; audio.preload = 'none'; audio.hidden = true; audio.setAttribute('aria-label','AI-generated Rhys voice'); audios.add(audio);
    const progress=element('span','answer-status');progress.setAttribute('role','status');
    const spoken=[...reply].slice(0,2000).join('');
    if([...reply].length>2000)renderText(progress,'Reads the first 2,000 characters.');
    let requestId, url, ownController, playingIndex=-1, waitingForPart=true, fullPlayback=false;const parts=[],partURLs=[];
    const playPart=index=>{if(!partURLs[index]){waitingForPart=true;return;}playingIndex=index;waitingForPart=false;audio.src=partURLs[index];audio.hidden=false;audio.play().catch(()=>status('Audio is ready. Use the player to listen.'));};
    audio.addEventListener('ended',()=>{if(fullPlayback)return;if(playingIndex+1<partURLs.length)playPart(playingIndex+1);else if(url){audio.src=url;playingIndex=-1;waitingForPart=false;fullPlayback=true;}else waitingForPart=true;});
    button.addEventListener('click', async () => {
      if (url) { fullPlayback=true;audio.src=url;audio.hidden = false; await audio.play().catch(() => status('Use the audio player to listen.')); return; }
      if (ownController && voiceActive===ownController) {ownController.abort();audio.pause();return;}
      if (voiceActive) { status('One voice request is already in progress.'); return; }
      if (!sessionKnown && !verification) { await needVerification(); status('Complete verification, then choose Listen again.'); return; }
      if(playingIndex>=0&&!waitingForPart)audio.play().catch(()=>{});requestId ||= win.crypto.randomUUID();voiceActive=new AbortController();const controller=voiceActive,epoch=generation;ownController=controller;
      const started=Date.now();let phase='queued';
      const update=()=>{renderText(button,'Stop waiting');renderText(progress,`${({queued:'Starting Rhys voice',loading_voice:'Loading saved Rhys voice',speaking:'Reading the reply in Rhys voice'})[phase]} · ${Math.round((Date.now()-started)/1000)}s. Audio starts as soon as the opening is ready.`);};
      update();const ticker=setInterval(update,1000);
      try {
        const blob=await requestVoiceParts(fetcher,{text:spoken,requestId,verification,parts,signal:controller.signal,onAccepted:verifiedSession,onProgress:value=>{phase=value;update();},onChunk:(blob,info)=>{if(epoch!==generation||controller.signal.aborted)return;const partURL=win.URL.createObjectURL(blob);audioURLs.add(partURL);partURLs[info.index]=partURL;renderText(progress,`Listening while the rest generates · part ${info.index+1} of ${info.total}`);if(waitingForPart)playPart(info.index);}});if(controller.signal.aborted||epoch!==generation)return;
        verifiedSession(); url = win.URL.createObjectURL(blob); audioURLs.add(url); if(playingIndex<0){fullPlayback=true;audio.src=url;}audio.hidden=false;renderText(button,'Play again · AI-generated Rhys voice');
        renderText(progress,`AI audio ready · ${Math.round((Date.now()-started)/1000)}s${[...reply].length>2000?' · first 2,000 characters':''}`);
        if(playingIndex<0)await audio.play().catch(() => status('AI audio is ready. Use the player to listen.'));
      } catch (error) {
        if(epoch===generation){if(['verification_required','verification_failed'].includes(error.code))await needVerification();renderText(progress,controller.signal.aborted?'Waiting stopped. Resume checks the same audio request; generation may continue.':error.message);renderText(button,'Resume · AI-generated Rhys voice');}
      } finally {clearInterval(ticker);if(voiceActive===controller)voiceActive=null;ownController=null;}
    });
    card.append(button,progress,audio);
  }
  function cardsFor(turn) {
    const block = element('section','turn'), question = element('article','user-message'), answers = element('div',`answers${turn.channels.length === 1 ? ' single' : ''}`); question.setAttribute('aria-label','Your message'); question.append(element('span','message-author','You'),element('p','question',turn.user)); block.append(question,answers); const cards = {};
    for (const channel of turn.channels) {
      const label = mode === 'nanochat' ? 'Pure nanochat Rhys · tiny from-scratch experiment' : mode === 'checkpoints' ? (channel === 'rhys' ? 'Rhysbot · trained 4B adapter' : 'Qwen 4B base · adapter disabled') : (channel === 'rhys' ? 'Rhysbot · Qwen + Rhys sources' : 'Qwen · no corpus');
      const card = element('article',`answer ${channel}`), heading = element('h3','',label), text = element('div','answer-text pending','Waiting for reply…'), timing = element('div','answer-status'); card.append(heading,text,timing); answers.appendChild(card); cards[channel] = { card,text,timing };
    }
    thread.appendChild(block); $('empty').hidden = true; return cards;
  }
  function provenanceFor(card,value) {
    const details = element('details','sources provenance'), summary = element('summary','',`Scratch checkpoint · ${value.checkpoint_run_id}`);
    details.append(summary,element('p','',`Weights SHA-256: ${value.weights_sha256}`),element('p','',`${value.input_tokens} input tokens · ${value.output_tokens} output tokens · ${value.elapsed_seconds.toFixed(2)}s inference. No retrieved sources or Qwen fallback.`));
    card.appendChild(details);
  }
  function renderConversation() {
    thread.replaceChildren(); $('empty').hidden = !turns.length;
    for (const saved of turns) {
      const turn = newTurn(saved.user,mode), cards = cardsFor(turn);
      for (const channel of turn.channels) {
        renderText(cards[channel].text,saved[channel]); cards[channel].text.classList.remove('pending');
        if (channel === 'rhys') { if (mode === 'nanochat' && saved.provenance) provenanceFor(cards[channel].card,saved.provenance); else sourcesFor(cards[channel].card,saved.sources); addVoice(cards[channel].card,saved[channel]); }
      }
    }
  }
  function followLatest() {
    $('composer').scrollIntoView?.({block:'end',behavior:win.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
  }
  async function send(event) {
    event?.preventDefault(); if (active || !config?.available) return;
    if (!modeAvailable()) { status('Pure nanochat Rhys is currently unavailable. No fallback was used.',true); return; }
    const prompt = $('message').value.trim(); if (!prompt) return;
    if (!sessionKnown && !verification) { await needVerification(); status('Complete verification, then send your message.'); return; }
    let context; try { context = histories(turns,prompt,mode); } catch (error) { status(error.message,true); return; }
    recognition?.abort(); const turn = newTurn(prompt,mode), cards = cardsFor(turn); const controller = new AbortController(), epoch = generation; active = controller; ready(); $('message').value = ''; followLatest(); const started = Date.now();
    const elapsed = () => status(`${mode === 'checkpoints' ? 'Loading saved checkpoints and comparing' : 'Generating'}… ${((Date.now()-started)/1000).toFixed(1)}s`);
    elapsed(); const ticker = setInterval(elapsed,250); timer = ticker; const deadline = setTimeout(() => controller.abort('deadline'),mode === 'checkpoints' ? 270000 : mode === 'nanochat' ? 150000 : 90000);
    try {
      const request = requestPayload(context,mode,verification);
      const response = await checked(await fetcher(mode === 'nanochat' ? '/api/rhysbot-nanochat' : mode === 'checkpoints' ? '/api/rhysbot-checkpoints' : '/api/rhysbot-chat', { method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request), signal: controller.signal }));
      if (controller.signal.aborted || epoch !== generation) throw new DOMException('Stopped','AbortError');
      verifiedSession();
      if (mode === 'nanochat') {
        if (response.headers.get('Content-Type')?.split(';')[0] !== 'application/json') throw new Error('Pure nanochat returned an invalid response. No fallback was used.');
        const result = await response.json(), provenance = nanochatProvenance(result);
        if (controller.signal.aborted || epoch !== generation) throw new DOMException('Stopped','AbortError');
        if (typeof result?.text !== 'string' || !result.text.trim() || !provenance) throw new Error('Pure nanochat returned no valid reply with checkpoint provenance. This turn was not saved; no fallback was used.');
        applyEvent(turn,{type:'delta',channel:'rhys',text:result.text}); applyEvent(turn,{type:'done',channel:'rhys'}); turn.provenance = provenance;
        renderText(cards.rhys.text,turn.rhys); cards.rhys.text.classList.remove('pending'); renderText(cards.rhys.timing,`Scratch checkpoint · ${provenance.elapsed_seconds.toFixed(2)}s · ${provenance.output_tokens} output tokens`); provenanceFor(cards.rhys.card,provenance);
      } else if (mode === 'checkpoints') {
        const result = await response.json();
        if (controller.signal.aborted || epoch !== generation) throw new DOMException('Stopped','AbortError');
        for (const channel of turn.channels) { const row = result[channel]; if (typeof row?.text !== 'string' || !row.text.trim()) { turn.failed.add(channel); renderText(cards[channel].text,'The saved checkpoint returned no usable reply.'); continue; } applyEvent(turn,{type:'delta',channel,text:row.text}); applyEvent(turn,{type:'done',channel}); renderText(cards[channel].text,row.text); cards[channel].text.classList.remove('pending'); renderText(cards[channel].timing,`Actual saved weights · ${((Date.now()-started)/1000).toFixed(1)}s elapsed`); }
      } else {
        if (!response.headers.get('Content-Type')?.includes('text/event-stream') || !response.body) throw new Error('The service did not return a response stream.');
        for await (const item of sseEvents(response.body,{signal:controller.signal})) {
          if (item.type === 'meta' && cards.rhys) { turn.sources = sourceMetadata(item.sources); sourcesFor(cards.rhys.card,turn.sources); }
          if (!cards[item.channel]) continue;
          applyEvent(turn,item); const target = cards[item.channel];
          if (item.type === 'delta') { renderText(target.text,turn[item.channel]); target.text.classList.remove('pending'); }
          if (item.type === 'timing' && Number.isFinite(item.first_token_ms)) renderText(target.timing,`First token ${(item.first_token_ms/1000).toFixed(2)}s`);
          if (item.type === 'done') renderText(target.timing,`${target.timing.textContent ? target.timing.textContent+' · ' : ''}finished ${(Number(item.elapsed_ms || Date.now()-started)/1000).toFixed(1)}s`);
          if (item.type === 'error') target.card.appendChild(element('p','answer-error',typeof item.message === 'string' ? item.message : 'This reply could not finish.'));
        }
      }
      if (controller.signal.aborted || epoch !== generation) throw new DOMException('Stopped','AbortError');
      if (completeTurn(turn)) { turns.push({user:prompt,rhys:turn.rhys,qwen:turn.qwen,sources:turn.sources || [],...(turn.provenance ? {provenance:turn.provenance} : {})}); if (turns.length > 30) turns.splice(0,turns.length-30); persist(); for (const channel of turn.channels) if (channel === 'rhys') addVoice(cards[channel].card,turn[channel]); status('Your turn. Ask a follow-up or take the conversation somewhere new.'); }
      else status('A reply was incomplete. This turn was not added to either model’s follow-up history.',true);
    } catch (error) {
      if (epoch === generation) {
        let message = error.name === 'AbortError' || controller.signal.aborted ? 'Stopped. Partial replies are shown; this turn was not saved to follow-up history.' : error.message;
        if (mode === 'nanochat') {
          if (controller.signal.reason === 'deadline') message = 'Pure nanochat timed out after 150 seconds. This turn was not saved; no fallback or automatic retry was used.';
          else if (!message.toLowerCase().includes('no fallback')) message += ' This nanochat turn was not saved; no fallback was used.';
        }
        status(message,true);
        for (const channel of turn.channels) if (!turn[channel]) { renderText(cards[channel].text,'No completed reply.'); cards[channel].text.classList.remove('pending'); }
      }
    } finally { clearInterval(ticker); clearTimeout(deadline); if (active === controller) { active = null; ready(); $('message').focus(); } }
  }
  $('composer').addEventListener('submit',send);
  $('message').addEventListener('input',ready);
  $('message').addEventListener('keydown',event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); } });
  $('stop').addEventListener('click',() => active?.abort()); $('new').addEventListener('click',reset);
  $('mode').addEventListener('change',() => { if (active || !MODES.includes($('mode').value)) return; generation++; recognition?.abort(); clearAudio(); conversations[mode] = turns; mode = $('mode').value; turns = conversations[mode]; renderConversation(); persist(); renderText($('mode-note'),modeNote()); status(turns.length ? 'Conversation restored. Keep going.' : 'A separate conversation. No request sent.'); ready(); $('message').focus(); });
  for (const button of doc.querySelectorAll('[data-prompt]')) button.addEventListener('click',() => { $('message').value = button.dataset.prompt; $('message').focus(); ready(); });
  const Recognition = win.SpeechRecognition || win.webkitSpeechRecognition;
  if (Recognition) {
    recognition = new Recognition(); recognition.lang = 'en-US'; recognition.interimResults = true; recognition.continuous = false; let prefix = '';
    recognition.onresult = event => { const transcript = [...event.results].map(r => r[0].transcript).join(' '); $('message').value = clipUTF8(`${prefix}${transcript}`,MAX_PROMPT_BYTES); ready(); };
    recognition.onstart = () => { listening = true; $('mic').classList.add('active'); $('mic').setAttribute('aria-pressed','true'); renderText($('mic'),'Stop listening'); status('Listening. Speech fills the input; it will not send automatically.'); };
    recognition.onend = () => { listening = false; $('mic').classList.remove('active'); $('mic').setAttribute('aria-pressed','false'); renderText($('mic'),'Microphone'); ready(); };
    recognition.onerror = () => status('Microphone permission or recognition is unavailable. You can keep typing.',true);
    $('mic').addEventListener('click',() => { if (listening) recognition.stop(); else { prefix = $('message').value ? $('message').value+' ' : ''; try { recognition.start(); } catch { status('Microphone could not start. You can type instead.',true); } } });
  } else { renderText($('mic'),'Microphone unavailable'); $('mic').title = 'Speech recognition is unavailable in this browser. Type your message instead.'; }
  const initialized = (async () => {
    try { const response = await fetcher('/api/rhysbot-chat',{credentials:'same-origin',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(10000)}); if (!response.ok) throw new Error('Chat is unavailable.'); config = await response.json(); if ($('nanochat-option')) $('nanochat-option').disabled = config.nanochat_available !== true; if (!config.available) throw new Error('Chat is not configured yet. No generation request was sent.'); renderText($('mode-note'),modeNote()); renderConversation(); await needVerification(); status(turns.length ? 'Your conversation is restored. Complete verification to continue.' : 'Complete verification to start. Later turns use a one-hour server session.'); }
    catch(error) { status(error.message,true); }
    ready();
  })();
  win.addEventListener('pagehide',() => { active?.abort(); recognition?.abort(); clearInterval(timer); clearAudio(); });
  return { initialized, reset };
}
if (typeof document !== 'undefined' && typeof window !== 'undefined') mount();
