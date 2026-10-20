// Retrieve evidence for the ongoing topic, with no source-format multiplier.
const STOP=new Set('what who how why when where does do did is are am was were the a an to of and or in you your yours me my tell about please can could would should think thoughts believe beliefs opinion opinions view views like know got get come yes yeah right really more elaborate explain much just have has had not from with for that this these those it its on at as be been but so then also say said everything evolved hee podcast podcasts youtube twitter writing substack source sources show according discuss'.split(' '));
const GROUPS={writing:['substack','linked_writing'],hee:['hee_transcript'],spoken:['podcast_transcript','youtube_transcript'],x:['x'],anki:['anki']};
export const sourceGroup=kind=>Object.keys(GROUPS).find(g=>GROUPS[g].includes(kind))||'other';
function terms(text){return [...new Set((text.toLowerCase().match(/[\p{L}\p{N}]{3,32}/gu)||[]).filter(t=>!STOP.has(t)))];}
export function retrievalPlan(input){
 const rows=typeof input==='string'?[{role:'user',content:input}]:input;
 const user=rows.filter(r=>r.role==='user').map(r=>r.content),latest=user.at(-1)||'';
 let words=terms(latest);const followup=words.length===0||/\b(that|those|it|more|elaborate|why|come on|you got|what about)\b/i.test(latest);
 if(followup)words=[...new Set([...words,...(user.slice(0,-1).reverse().map(terms).find(t=>t.length)||[])])];
 const topic=words.join(' ');
 if(/politic|democra|govern/.test(topic))words.push('politic','democra','govern','liberal','capitalis');
 if(/longterm/.test(topic))words.push('longterm','future','existential');
 const requested=/\b(HEE|how everything evolved)\b/i.test(latest)?'hee':/\b(podcasts?|youtube|rhys show)\b/i.test(latest)?'spoken':/\b(tweets?|twitter|x posts?|on x)\b/i.test(latest)?'x':/\b(substack|writing|essays?)\b/i.test(latest)?'writing':/\banki\b/i.test(latest)?'anki':null;
 if(!words.length)words=['Rhys','Roote','Digicene','evolution'];
 words=[...new Set(words)].slice(0,16);
 const fts=words.map(w=>'"'+w.replaceAll('"','')+'"*').join(' OR ');
 return {words,fts,requested,personal:/\b(your|you|rhys|belief|views?|opinion|politic)\b/i.test(user.slice(-3).join(' '))};
}
export async function retrieve(env,input){
 const plan=retrievalPlan(input),groups=plan.requested?[plan.requested]:Object.keys(GROUPS);
 const results=await Promise.all(groups.map(async group=>{
  const kinds=GROUPS[group];
  const sql=`SELECT id,title,kind,url,attribution,snippet(rb_sources,5,'','',' … ',64) AS text,bm25(rb_sources,0,4,0,0,0,1) AS rank FROM rb_sources WHERE rb_sources MATCH ? AND kind IN (${kinds.map(()=>'?').join(',')}) ORDER BY rank LIMIT 5`;
  return ((await env.RHYSBOT_DB.prepare(sql).bind(plan.fts,...kinds).all()).results||[]).map((r,order)=>({...r,order}));
 }));
 const all=results.flat().map(r=>{const hay=(r.title+' '+r.text).toLowerCase();return {...r,group:sourceGroup(r.kind),score:plan.words.reduce((n,w)=>n+(hay.includes(w.toLowerCase())?1:0),0)+1/(r.order+2)};});
 const candidates=all.filter(r=>!plan.personal||plan.requested==='anki'||r.kind!=='anki'||!all.some(s=>s.kind!=='anki'));
 const chosen=[],documents=new Set();const add=r=>{const key=r.url||r.title||r.id;if(documents.has(key))return;documents.add(key);const {rank,score,group,order,...source}=r;chosen.push(source);};
 const ranked=candidates.sort((a,b)=>b.score-a.score);
 // Give each relevant medium an opportunity, while rejecting weak matches.
 for(const group of groups){const top=ranked.find(r=>r.group===group);if(top&&top.score>=((ranked[0]?.score||0)*.4))add(top);}
 for(const row of ranked){if(chosen.length>=6)break;add(row);}
 return chosen.slice(0,6);
}
