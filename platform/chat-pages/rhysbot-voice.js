import {authorize,boundedJSON,charge,ChatFailure,reply,sha} from './rhysbot-chat.js';

// Checkpoint comparison retains its longer timeout.
export async function platformCall(request,env,path,body,{fetcher=fetch,timeout=220000}={}) {
  const endpoint=new URL(env.RHYSBOT_PLATFORM_URL);
  if(endpoint.protocol!=='https:'||!endpoint.hostname.endsWith('.modal.run'))throw new ChatFailure(503,'platform_not_configured');
  endpoint.pathname=path;
  return fetcher(endpoint,{method:'POST',signal:AbortSignal.timeout(timeout),headers:{'Content-Type':'application/json',Authorization:'Bearer '+env.RHYSBOT_TRAIN_BRIDGE_SECRET,'Modal-Key':env.MODAL_PROXY_KEY,'Modal-Secret':env.MODAL_PROXY_SECRET},body:JSON.stringify(body)});
}
export function createVoiceHandler({fetcher=fetch}={}) {return async ({request,env})=>{
  try {
    if(request.method!=='POST')return reply({error:'method_not_allowed'},405);
    const body=await boundedJSON(request,16000),id=body.request_id;
    if(!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(id||'')||![undefined,'status'].includes(body.action))throw new ChatFailure(400,'invalid_voice_request');
    const poll=body.action==='status';
    if(!poll&&(typeof body.text!=='string'||!body.text.trim()||[...body.text].length>2000||new TextEncoder().encode(body.text).length>8000))throw new ChatFailure(400,'invalid_voice_request');
    const auth=await authorize(request,env,body,fetcher),key=await sha(auth.client+':'+id),now=Math.floor(Date.now()/1000);
    const nonce=[key.slice(0,8),key.slice(8,12),key.slice(12,16),key.slice(16,20),key.slice(20,32)].join('-');
    const db=env.RHYSBOT_DB,hash=poll?null:await sha(body.text);
    if(!poll) {
      await db.prepare('DELETE FROM rb_voice WHERE expires<?').bind(now).run();
      const inserted=await db.prepare("INSERT INTO rb_voice(id,text_sha,state,expires) VALUES(?,?,'reserving',?) ON CONFLICT(id) DO NOTHING RETURNING id").bind(key,hash,now+86400).first();
      if(inserted) {
        try {await charge(env,auth.client,500000);}
        catch(error) {await db.prepare("UPDATE rb_voice SET state='blocked' WHERE id=?").bind(key).run();throw error;}
        await db.prepare("UPDATE rb_voice SET state='charged' WHERE id=?").bind(key).run();
      }
    }
    const row=await db.prepare('SELECT text_sha,state,expires FROM rb_voice WHERE id=?').bind(key).first();
    if(!row||row.expires<=now)throw new ChatFailure(404,'voice_not_found');
    if(!poll&&row.text_sha!==hash)throw new ChatFailure(409,'voice_request_conflict');
    if(row.state!=='charged')throw new ChatFailure(409,'voice_reservation_incomplete');
    const response=await platformCall(request,env,poll?'/voice-status':'/voice',poll?{request_id:nonce}:{text:body.text,request_id:nonce},{fetcher,timeout:20000});
    const headers=auth.cookie?{'Set-Cookie':auth.cookie}:{};
    if(response.status===202) {
      const result=await response.json();
      if(!['queued','loading_voice','speaking'].includes(result.state))throw new ChatFailure(503,'voice_recovery_required');
      return reply({state:result.state},202,headers);
    }
    if(!response.ok)throw new ChatFailure(503,'voice_temporarily_unavailable');
    const bytes=response.headers.get('X-Rhysbot-Audio-Bytes'),checksum=response.headers.get('X-Rhysbot-Audio-SHA256');
    if(!poll||!response.headers.get('Content-Type')?.startsWith('audio/wav')||!/^\d+$/.test(bytes||'')||Number(bytes)>8*1024*1024||Number(bytes)<44||!/^[a-f0-9]{64}$/.test(checksum||''))throw new ChatFailure(502,'invalid_voice_response');
    let size=0;
    const stream=response.body.pipeThrough(new TransformStream({transform(chunk,controller){size+=chunk.byteLength;if(size>Number(bytes))throw Error('voice_too_large');controller.enqueue(chunk)},flush(){if(size!==Number(bytes))throw Error('incomplete_voice')}}));
    return new Response(stream,{headers:{'Content-Type':'audio/wav','Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Rhysbot-Voice':'AI-generated authorized Rhys voice','X-Rhysbot-Audio-Bytes':bytes,'X-Rhysbot-Audio-SHA256':checksum,...headers}});
  } catch(error) {return reply({error:error instanceof ChatFailure?error.code:'voice_temporarily_unavailable'},error.status||503);}
};}
export const onRequest=createVoiceHandler();
