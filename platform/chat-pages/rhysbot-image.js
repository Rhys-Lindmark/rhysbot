import{authorize,boundedJSON,charge,ChatFailure,reply,serviceAuthorized}from'./rhysbot-chat.js';
export const VISION='@cf/moondream/moondream3.1-9B-A2B',DRAW='@cf/black-forest-labs/flux-1-schnell';
const enc=new TextEncoder();
export function imageInput(uri){
 const m=typeof uri==='string'&&/^data:image\/(png|jpeg);base64,([A-Za-z0-9+/]*={0,2})$/.exec(uri);
 if(!m||!m[2]||m[2].length>1400000||m[2].length%4)throw new ChatFailure(400,'invalid_image');
 let raw;try{raw=atob(m[2]);}catch{throw new ChatFailure(400,'invalid_image');}
 if(raw.length>1048576||btoa(raw)!==m[2])throw new ChatFailure(400,'image_too_large');
 const b=Uint8Array.from(raw,c=>c.charCodeAt(0));let width,height;
 if(m[1]==='png'){
  if(b.length<33||![137,80,78,71,13,10,26,10].every((x,i)=>b[i]===x)||raw.slice(12,16)!=='IHDR')throw new ChatFailure(400,'invalid_image');
  const v=new DataView(b.buffer);width=v.getUint32(16);height=v.getUint32(20);
 }else{
  if(b[0]!==255||b[1]!==216)throw new ChatFailure(400,'invalid_image');let p=2;
  while(p+4<b.length){if(b[p++]!==255)break;let marker=b[p++];while(marker===255&&p<b.length)marker=b[p++];if(marker===217||marker===218)break;const n=(b[p]<<8)|b[p+1];if(n<2||p+n>b.length)break;
   if([192,193,194,195,197,198,199,201,202,203,205,206,207].includes(marker)&&n>=7){height=(b[p+3]<<8)|b[p+4];width=(b[p+5]<<8)|b[p+6];break;}p+=n;
  }
 }
 if(!width||!height||width>2048||height>2048||width*height>4194304)throw new ChatFailure(400,'image_dimensions_too_large');
 return{uri,mime:'image/'+m[1],width,height,bytes:b.length};
}
export async function visionValue(value){
 const stream=value?.getReader?value:value?.body?.getReader?value.body:null;
 if(!stream)return value?.result??value;
 const reader=stream.getReader(),decoder=new TextDecoder('utf-8',{fatal:true});let text='',bytes=0,timer;
 const deadline=new Promise((_,reject)=>{timer=setTimeout(()=>{reader.cancel().catch(()=>{});reject(new ChatFailure(504,'image_response_timeout'));},30000);});
 try{
  for(;;){const row=await Promise.race([reader.read(),deadline]);if(row.done)break;bytes+=row.value.byteLength;if(bytes>131072)throw new ChatFailure(502,'invalid_image_response');text+=decoder.decode(row.value,{stream:true});}
  text+=decoder.decode();const parsed=JSON.parse(text);return parsed?.result??parsed;
 }catch(e){if(e instanceof ChatFailure)throw e;throw new ChatFailure(502,'invalid_image_response');}
 finally{clearTimeout(timer);try{await reader.cancel();}catch{}reader.releaseLock();}
}
export function createImageHandler({fetcher=fetch}={}){return async({request,env})=>{
 try{
  if(request.method!=='POST')return reply({error:'method_not_allowed'},405);
  const body=await boundedJSON(request,1500000);
  if(!['analyze','generate'].includes(body.action)||typeof body.prompt!=='string'||!body.prompt.trim()||body.prompt.length>1500||enc.encode(body.prompt).length>4500)throw new ChatFailure(400,'invalid_image_request');
  const input=body.action==='analyze'?imageInput(body.image):null;
  const auth=await authorize(request,env,body,fetcher);if(!env.AI)throw new ChatFailure(503,'image_not_configured');
  const budget=await charge(env,auth.client,body.action==='analyze'?10000:20000);
  let timer;const call=env.AI.run(input?VISION:DRAW,input?{task:'query',image:input.uri,question:body.prompt,max_tokens:256,reasoning:false,stream:false,temperature:.2}:{prompt:body.prompt,steps:4});
  let value;try{value=await Promise.race([call,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('image_deadline')),45000);})]);}finally{clearTimeout(timer);}
  if(input){value=await visionValue(value);const description=value?.answer;if(typeof description!=='string'||!description.trim()||description.length>12000){if(await serviceAuthorized(request,env))return reply({error:'invalid_image_response',diagnostic:{type:typeof value,constructor:value?.constructor?.name,keys:Object.keys(value||{}).slice(0,20),field_types:Object.fromEntries(Object.entries(value||{}).slice(0,20).map(([k,v])=>[k,typeof v]))}},502);throw new ChatFailure(502,'invalid_image_response');}return reply({description,model:VISION,budget},200,auth.cookie?{'Set-Cookie':auth.cookie}:{});}
  if(typeof value?.image!=='string'||!value.image.length||value.image.length>12*1024*1024||!/^[A-Za-z0-9+/]+={0,2}$/.test(value.image))throw new ChatFailure(502,'invalid_image_response');
  const prefix=atob(value.image.slice(0,32)),mime=prefix.startsWith('\xff\xd8')?'image/jpeg':prefix.startsWith('\x89PNG\r\n\x1a\n')?'image/png':null;if(!mime)throw new ChatFailure(502,'invalid_image_response');
  return reply({image:`data:${mime};base64,${value.image}`,model:DRAW,budget},200,auth.cookie?{'Set-Cookie':auth.cookie}:{});
 }catch(e){return reply({error:e instanceof ChatFailure?e.code:'image_temporarily_unavailable'},e.status||503);}
};}
export const onRequest=createImageHandler();
