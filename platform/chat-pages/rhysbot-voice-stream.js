import {authorize,boundedJSON,charge,ChatFailure,reply,sha,serviceAuthorized} from './rhysbot-chat.js';

// A single direct PCM/SSE stream. No GPU, background job, polling or whole-file wait.
export function streamConfigured(env){
 try{const u=new URL(env.RHYSBOT_VOICE_STREAM_URL);return u.protocol==='https:'&&u.hostname.endsWith('.modal.run')&&!u.username&&!u.password&&!u.port&&!!env.MODAL_PROXY_KEY&&!!env.MODAL_PROXY_SECRET&&typeof env.RHYSBOT_TRAIN_BRIDGE_SECRET==='string'&&env.RHYSBOT_TRAIN_BRIDGE_SECRET.length>=32;}catch{return false;}
}
export function createVoiceStreamHandler({fetcher=fetch}={}){return async({request,env})=>{
 let stage='validate';try{
  if(request.method!=='POST')return reply({error:'method_not_allowed',bridge:'cpu-stream-v4'},405);
  const body=await boundedJSON(request,16000),id=body.request_id;
  if(!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(id||'')||typeof body.text!=='string'||!body.text.trim()||[...body.text].length>2000||new TextEncoder().encode(body.text).length>8000)throw new ChatFailure(400,'invalid_voice_request');
  stage='authorize';const auth=await authorize(request,env,body,fetcher);
  if(!streamConfigured(env))throw new ChatFailure(503,'streaming_voice_not_configured');
  stage='reservation';const db=env.RHYSBOT_DB,key=await sha(auth.client+':pcm-stream:'+id),hash=await sha(body.text),now=Math.floor(Date.now()/1000);
  await db.prepare('DELETE FROM rb_voice WHERE expires<?').bind(now).run();
  const inserted=await db.prepare("INSERT INTO rb_voice(id,text_sha,state,expires) VALUES(?,?,'reserving',?) ON CONFLICT(id) DO NOTHING RETURNING id").bind(key,hash,now+86400).first();
  if(inserted){
   // Standby baseline is reserved by charge(); $0.02 additionally covers two
   // bounded 90s inputs plus 60s startup each at <=4 cores/768MiB (<$0.017).
   // Without standby, $0.05 also covers bounded scale-to-zero idle time.
   try{await charge(env,auth.client,env.RHYSBOT_CPU_STANDBY_ENABLED==='1'?20000:50000);}catch(e){await db.prepare("UPDATE rb_voice SET state='blocked' WHERE id=?").bind(key).run();throw e;}
   await db.prepare("UPDATE rb_voice SET state='stream_charged' WHERE id=?").bind(key).run();
  }
  stage='dispatch_claim';const row=await db.prepare('SELECT text_sha,state,expires FROM rb_voice WHERE id=?').bind(key).first();
  if(!row||row.expires<=now)throw new ChatFailure(404,'voice_not_found');
  if(row.text_sha!==hash)throw new ChatFailure(409,'voice_request_conflict');
  const claim=await db.prepare("UPDATE rb_voice SET state='stream_dispatched' WHERE id=? AND state='stream_charged' RETURNING id").bind(key).first();
  if(!claim)throw new ChatFailure(409,'voice_already_dispatched');
  const endpoint=new URL(env.RHYSBOT_VOICE_STREAM_URL);endpoint.pathname='/stream';endpoint.search='';endpoint.hash='';
  const nonce=[key.slice(0,8),key.slice(8,12),key.slice(12,16),key.slice(16,20),key.slice(20,32)].join('-');
  stage='cancellation';const abort=new AbortController();let incoming;
  try{incoming=request.signal;}catch{} // Older Workers require enable_request_signal.
  const disconnected=()=>abort.abort();incoming?.addEventListener('abort',disconnected,{once:true});
  const timer=setTimeout(()=>abort.abort(),90000);const clean=()=>{clearTimeout(timer);incoming?.removeEventListener('abort',disconnected);};
  const signal=abort.signal;if(incoming?.aborted)abort.abort();
  stage='provider_fetch';let upstream;try{upstream=await fetcher(endpoint,{method:'POST',redirect:'manual',signal,headers:{'Content-Type':'application/json',Authorization:'Bearer '+env.RHYSBOT_TRAIN_BRIDGE_SECRET,'Modal-Key':env.MODAL_PROXY_KEY,'Modal-Secret':env.MODAL_PROXY_SECRET},body:JSON.stringify({request_id:nonce,text:body.text})});}catch(e){clean();throw e;}
  stage='provider_response';if(!upstream.ok||!upstream.body||!upstream.headers.get('Content-Type')?.startsWith('text/event-stream')){abort.abort();clean();throw new ChatFailure(upstream.status===429?429:503,upstream.status===429?'voice_busy':'voice_stream_upstream_'+upstream.status);}
  let bytes=0;const reader=upstream.body.getReader();
  const bounded=new ReadableStream({async pull(c){try{const {value,done}=await reader.read();if(done){clean();c.close();return;}bytes+=value.byteLength;if(bytes>12*1024*1024)throw Error('voice_stream_too_large');c.enqueue(value);}catch(e){abort.abort();clean();c.error(e);}},async cancel(){abort.abort();clean();try{await reader.cancel();}catch{}}});
  return new Response(bounded,{headers:{'Content-Type':'text/event-stream','Cache-Control':'no-store, no-transform','X-Content-Type-Options':'nosniff','X-Rhysbot-Voice':'AI-generated authorized Rhys voice','X-Rhysbot-Sample-Rate':'24000',...(auth.cookie?{'Set-Cookie':auth.cookie}:{})}});
 }catch(e){let debug;
  if(!(e instanceof ChatFailure)&&await serviceAuthorized(request,env)){
   let message=String(e.message||'').slice(0,400);for(const [k,v] of Object.entries(env))if(typeof v==='string'&&v.length>=8&&/SECRET|KEY|SALT/.test(k))message=message.replaceAll(v,'[redacted]');
   debug={stage,kind:e.name,message};
  }
  return reply({error:e instanceof ChatFailure?e.code:'voice_stream_unavailable',stage,...(debug?{debug}:{})},e.status||503,{'X-Rhysbot-Voice-Bridge':'cpu-stream-v4'});}
};}
export const onRequest=createVoiceStreamHandler();
