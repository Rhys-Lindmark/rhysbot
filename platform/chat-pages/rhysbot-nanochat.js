import {authorize,boundedJSON,charge,ChatFailure,reply,history} from './rhysbot-chat.js';
import {platformCall} from './rhysbot-voice.js';

export function createNanochatHandler({fetcher=fetch}={}) {return async({request,env})=>{
 try {
  if(request.method!=='POST')return reply({error:'method_not_allowed'},405);
  const body=await boundedJSON(request,30000),messages=history(body);
  if(messages.length>11)throw new ChatFailure(400,'invalid_history');
  const auth=await authorize(request,env,body,fetcher);
  if(!env.RHYSBOT_PLATFORM_URL)throw new ChatFailure(503,'nanochat_not_configured');
  await charge(env,auth.client,20000);
  const r=await platformCall(request,env,'/nanochat',{messages,request_id:crypto.randomUUID()},{fetcher,timeout:135000});
  if(!r.ok)throw new ChatFailure(r.status===400?400:503,r.status===400?'nanochat_context_too_long':'nanochat_temporarily_unavailable');
  const value=await r.json();
  if(typeof value.text!=='string'||value.text.length>24000||value.model!=='pure_nanochat'||value.qwen_fallback!==false||value.retrieval!==false||!/^[a-f0-9]{64}$/.test(value.weights_sha256||'')||!/^nanochat-[a-z0-9-]+$/.test(value.checkpoint_run_id||'')||!Number.isInteger(value.input_tokens)||!Number.isInteger(value.output_tokens)||value.input_tokens>384||value.output_tokens>96)throw new ChatFailure(502,'invalid_nanochat_provenance');
  return reply(value,200,auth.cookie?{'Set-Cookie':auth.cookie}:{});
 }catch(e){return reply({error:e instanceof ChatFailure?e.code:'nanochat_temporarily_unavailable'},e.status||503);}
};}
export const onRequest=createNanochatHandler();
