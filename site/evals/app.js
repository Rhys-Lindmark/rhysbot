const encoder = new TextEncoder();
export const PAGE_SIZE = 30;
export const REVIEW_LIMIT = 200 * 1024;
export const REVIEW_KEY = 'rhysbot.anki-reviews.v1';
const COHORTS = ['train','validation','test'];
const CHOICES = {rhys_correct:'Rhys correct',qwen_correct:'Qwen correct',both:'Both',neither:'Neither',unsure:'Unsure'};
const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const nonempty = (value,limit) => typeof value === 'string' && !!value.trim() && encoder.encode(value).length <= limit;
const integer = value => Number.isSafeInteger(value) && value >= 0;
const number = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
export function metric(value,digits=3) { return number(value) ? value.toFixed(digits) : '—'; }
export function percentage(value) { return number(value) && value <= 1 ? (value*100).toFixed(1)+'%' : '—'; }
export function measuredSummary(value) {
  return value?.status==='measured' && nonempty(value.checkpoint_run_id,200) && hash(value.weights_sha256) && nonempty(value.base_model_id,300) && nonempty(value.base_revision,200) && integer(value.input_records) && integer(value.scored_records) && number(value.elapsed_seconds) ? value : null;
}
export function validateQA(row) {
  if(!row || !nonempty(row.id,200) || !nonempty(row.question,65536) || !nonempty(row.reference,131072) || !COHORTS.includes(row.frozen_sft_split) || typeof row.source_split!=='string' || !nonempty(row.source_group_id,500) || !hash(row.qa_sha256)) throw new Error('Invalid public QA record.');
  return {id:row.id,question:row.question,reference:row.reference,source_split:row.source_split,frozen_sft_split:row.frozen_sft_split,source_group_id:row.source_group_id,qa_sha256:row.qa_sha256};
}
export function validateScore(row) {
  if(!row || !nonempty(row.id,200) || !COHORTS.includes(row.frozen_sft_split) || !integer(row.target_tokens) || row.target_tokens < 1 || !integer(row.full_target_tokens) || row.full_target_tokens < row.target_tokens || typeof row.truncated!=='boolean' || !number(row.rhys_nll) || !number(row.qwen_base_nll)) throw new Error('Invalid per-card score.');
  return {id:row.id,frozen_sft_split:row.frozen_sft_split,target_tokens:row.target_tokens,full_target_tokens:row.full_target_tokens,truncated:row.truncated,rhys_nll:row.rhys_nll,qwen_base_nll:row.qwen_base_nll};
}
export function validateSample(row) {
  const qa=validateQA(row);
  if(!integer(row.input_tokens)||row.input_tokens<1)throw new Error('Invalid answer sample token count.');
  const channel=name=>{const answer=row[name];if(!answer||!nonempty(answer.text,131072)||typeof answer.normalized_exact_match!=='boolean'||!number(answer.token_f1)||answer.token_f1>1||!number(answer.seconds))throw new Error('Invalid generated answer sample.');return{text:answer.text,normalized_exact_match:answer.normalized_exact_match,token_f1:answer.token_f1,seconds:answer.seconds};};
  return {...qa,input_tokens:row.input_tokens,rhys:channel('rhys'),qwen_base:channel('qwen_base')};
}
export async function readJSONL(response,{validate,maxBytes=40*1024*1024,maxRecords=22000,signal}={}) {
  if(!response.ok||!response.body)throw new Error('This evaluation dataset is not available yet.');
  const size=response.headers.get('Content-Length');if(size!==null&&(!/^\d+$/.test(size)||Number(size)>maxBytes))throw new Error('Evaluation dataset exceeded its download limit.');
  const reader=response.body.getReader(),decoder=new TextDecoder('utf-8',{fatal:true}),rows=[],ids=new Set();let pending='',bytes=0;
  const abort=()=>reader.cancel().catch(()=>{});signal?.addEventListener('abort',abort,{once:true});
  const line=value=>{if(!value.trim())return;if(rows.length>=maxRecords)throw new Error('Evaluation dataset exceeded its record limit.');const row=validate(JSON.parse(value));if(ids.has(row.id))throw new Error('Evaluation dataset contains duplicate card IDs.');ids.add(row.id);rows.push(row);};
  try{for(;;){signal?.throwIfAborted();const{value,done}=await reader.read();signal?.throwIfAborted();if(done){pending+=decoder.decode();if(pending.trim())line(pending);return rows;}bytes+=value.byteLength;if(bytes>maxBytes)throw new Error('Evaluation dataset exceeded its download limit.');pending+=decoder.decode(value,{stream:true});let split;while((split=pending.indexOf('\n'))>=0){line(pending.slice(0,split));pending=pending.slice(split+1);}}}
  finally{signal?.removeEventListener('abort',abort);try{await reader.cancel();}catch{}reader.releaseLock();}
}
export function joinScores(qa,scores) {
  const byId=new Map(scores.map(row=>[row.id,row]));
  return qa.map(row=>{const score=byId.get(row.id);if(score&&score.frozen_sft_split!==row.frozen_sft_split)throw new Error('Score and QA split disagree; data was not combined.');return {...row,score:score||null};});
}
export function pageRows(rows,{query='',cohort='',page=0}={}) {
  const search=query.trim().toLowerCase(),filtered=rows.filter(row=>(!cohort||row.frozen_sft_split===cohort)&&(!search||(row.question+'\n'+row.reference+'\n'+row.id).toLowerCase().includes(search))),pages=Math.max(1,Math.ceil(filtered.length/PAGE_SIZE));
  const current=Number.isInteger(page)?Math.max(0,Math.min(page,pages-1)):0;
  return {rows:filtered.slice(current*PAGE_SIZE,(current+1)*PAGE_SIZE),total:filtered.length,page:current,pages};
}
const reviewId = entry => `${entry.weights_sha256}/${entry.qa_sha256}/${entry.id}`;
function validReview(entry) {return entry && nonempty(entry.id,200)&&hash(entry.qa_sha256)&&hash(entry.weights_sha256)&&nonempty(entry.checkpoint_run_id,200)&&Object.hasOwn(CHOICES,entry.choice)&&typeof entry.correction==='string'&&[...entry.correction].length<=2000&&typeof entry.reviewed_at==='string'&&Number.isFinite(Date.parse(entry.reviewed_at));}
function cleanReview(entry) {return Object.fromEntries(['id','qa_sha256','weights_sha256','checkpoint_run_id','choice','correction','reviewed_at'].map(key=>[key,entry[key]]));}
export function restoreReviews(raw) {
  if(typeof raw!=='string'||encoder.encode(raw).length>REVIEW_LIMIT)return [];
  try{const parsed=JSON.parse(raw);if(parsed?.version!==1||!Array.isArray(parsed.entries))return [];const result=new Map();for(const row of parsed.entries)if(validReview(row))result.set(reviewId(row),cleanReview(row));return [...result.values()];}catch{return [];}
}
export function saveReview(entries,entry) {
  if(!validReview(entry))throw new Error('Choose a valid review and keep corrections within 2,000 characters.');
  const next=entries.filter(row=>reviewId(row)!==reviewId(entry));next.push(cleanReview(entry));
  if(encoder.encode(JSON.stringify({version:1,entries:next})).length>REVIEW_LIMIT)throw new Error('Local reviews reached the 200 KiB limit. Export and clear existing reviews before adding more.');return next;
}
export function exportReviews(entries,samples,summary) {
  const measured=measuredSummary(summary);if(!measured)return '';
  const available=new Map(samples.map(row=>[row.id+'\n'+row.qa_sha256,row]));
  return entries.filter(entry=>entry.weights_sha256===measured.weights_sha256&&entry.checkpoint_run_id===measured.checkpoint_run_id).flatMap(entry=>{const sample=available.get(entry.id+'\n'+entry.qa_sha256);return sample?[JSON.stringify({schema_version:1,...cleanReview(entry),base_model_id:measured.base_model_id,base_revision:measured.base_revision,question:sample.question,reference:sample.reference,rhys_answer:sample.rhys.text,qwen_base_answer:sample.qwen_base.text})]:[];}).join('\n');
}
async function smallJSON(response,maxBytes) {
  if(!response.ok)throw new Error('Evaluation report is not available yet.');const bytes=await readBounded(response,maxBytes);return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
}
async function readBounded(response,maxBytes) {
  if(!response.body)throw new Error('Empty evaluation report.');const reader=response.body.getReader(),parts=[];let bytes=0;
  try{for(;;){const{value,done}=await reader.read();if(done)break;bytes+=value.byteLength;if(bytes>maxBytes)throw new Error('Evaluation report exceeded its size limit.');parts.push(value);}const joined=new Uint8Array(bytes);let offset=0;for(const part of parts){joined.set(part,offset);offset+=part.length;}return joined;}finally{try{await reader.cancel();}catch{}reader.releaseLock();}
}
export function mountEvals(doc=document,win=window,fetcher=fetch) {
  const $=id=>doc.getElementById(id),el=(tag,cls,text)=>{const value=doc.createElement(tag);if(cls)value.className=cls;if(text!==undefined)value.textContent=String(text);return value;};
  const controller=new AbortController();let summary,manifest,qa=[],samples=[],reviews=[],page=0,samplePage=0,qaLoaded=false,samplesLoaded=false,exportURL;
  try{reviews=restoreReviews(win.localStorage?.getItem(REVIEW_KEY));}catch{}
  const status=(message,error=false)=>{$('status').textContent=message;$('status').classList.toggle('error',error);};
  const get=path=>fetcher('./data/'+path,{credentials:'same-origin',cache:'no-store',redirect:'error',signal:controller.signal});
  const displayCount=value=>integer(value)?value.toLocaleString('en-US'):'—';
  function renderSummary(){
    const counts=manifest?.counts;for(const name of ['all_released_records','eligible_qa','missing_reference','missing_question'])$(name).textContent=displayCount(counts?.[name]);
    $('summary-table').replaceChildren();$('generation-table').replaceChildren();const measured=measuredSummary(summary);$('measured').hidden=!measured;$('pending').hidden=!!measured;
    if(!measured){$('pending').textContent='Evaluation in progress. The measured adapter-versus-base report is not available yet; no accuracy or loss scores are shown.';return;}
    $('identity').textContent=`${measured.checkpoint_run_id} · weights SHA-256 ${measured.weights_sha256}`;$('base-identity').textContent=`Base: ${measured.base_model_id} · revision ${measured.base_revision}`;$('coverage').textContent=`${displayCount(measured.scored_records)} scored / ${displayCount(measured.input_records)} input records · ${Array.isArray(measured.skipped)?displayCount(measured.skipped.length):'—'} skipped · ${metric(measured.elapsed_seconds,1)}s measured elapsed time.`;
    for(const cohort of COHORTS){const row=measured.cohorts?.[cohort],tr=el('tr');tr.append(el('th','',cohort),el('td','',displayCount(row?.count)),el('td','',displayCount(row?.target_tokens)));
      const maximum=Math.max(number(row?.rhys_nll)?row.rhys_nll:0,number(row?.qwen_base_nll)?row.qwen_base_nll:0);
      for(const [name,cls] of [['rhys_nll','rhys'],['qwen_base_nll','qwen']]){const td=el('td','loss',metric(row?.[name]));if(number(row?.[name])&&maximum>0){const bar=el('span',`bar ${cls}`);bar.style.width=(row[name]/maximum*100)+'%';bar.setAttribute('aria-hidden','true');td.appendChild(bar);}tr.appendChild(td);}
      tr.append(el('td','',displayCount(row?.rhys_lower_loss)),el('td','',displayCount(row?.base_lower_loss)),el('td','',displayCount(row?.ties)));$('summary-table').appendChild(tr);
    }
    const generation=measured.generation;$('generation-count').textContent=integer(generation?.count)?`${displayCount(generation.count)} generated answer pairs measured. Normalized exact match and token F1 compare each answer to the reference card; they are not human correctness judgments.`:'No generated-answer aggregate measurements are available in this report.';
    for(const [key,label] of [['rhys','Rhys adaptation'],['qwen_base','Qwen base']]){const tr=el('tr');tr.append(el('th','',label),el('td','',percentage(generation?.[key]?.exact_match)),el('td','',percentage(generation?.[key]?.token_f1)));$('generation-table').appendChild(tr);}
  }
  function renderQA(){const result=pageRows(qa,{query:$('query').value,cohort:$('cohort').value,page});page=result.page;$('qa-rows').replaceChildren();$('qa-count').textContent=`${displayCount(result.total)} cards match · page ${page+1} of ${result.pages}. At most ${PAGE_SIZE} cards are rendered.`;$('previous').disabled=!qaLoaded||page===0;$('next').disabled=!qaLoaded||page+1>=result.pages;
    for(const row of result.rows){const tr=el('tr'),question=el('td','card'),details=el('details'),reference=el('p','reference',row.reference);details.append(el('summary','','Reveal reference card'),reference);question.append(el('p','question',row.question),el('p','card-meta',`${row.id} · source split ${row.source_split} · source group ${row.source_group_id}`),details);tr.append(question,el('td','',row.frozen_sft_split),el('td','',metric(row.score?.rhys_nll)),el('td','',metric(row.score?.qwen_base_nll)),el('td','',row.score?`${row.score.target_tokens} / ${row.score.full_target_tokens}`:'—'),el('td','',row.score?(row.score.truncated?'Yes':'No'):'Not scored'));$('qa-rows').appendChild(tr);}
  }
  function reviewStatus(){const text=exportReviews(reviews,samples,summary),matched=text?text.split('\n').length:0;$('review-count').textContent=`${reviews.length} local reviews · ${matched} match the loaded samples and checkpoint. Storage limit: 200 KiB. Unmatched older reviews are kept locally and excluded from this export.`;$('export-reviews').disabled=!matched;$('clear-reviews').disabled=!reviews.length;}
  function persistReviews(){try{win.localStorage?.setItem(REVIEW_KEY,JSON.stringify({version:1,entries:reviews}));return 'Saved locally in this browser.';}catch{return 'Kept in this page only; browser storage is unavailable. Export before closing.';}}
  function renderSamples(){const pages=Math.max(1,Math.ceil(samples.length/4));samplePage=Math.max(0,Math.min(samplePage,pages-1));$('samples').replaceChildren();$('sample-page').textContent=samplesLoaded?`Answer pairs ${Math.min(samplePage*4+1,samples.length)}–${Math.min((samplePage+1)*4,samples.length)} of ${samples.length}`:'Load public answer samples to inspect and review them.';$('sample-previous').disabled=!samplesLoaded||samplePage===0;$('sample-next').disabled=!samplesLoaded||samplePage+1>=pages;
    for(const sample of samples.slice(samplePage*4,samplePage*4+4)){const article=el('article','sample');article.append(el('p','card-meta',`${sample.frozen_sft_split} · ${sample.id} · ${sample.input_tokens} input tokens`),el('h3','',sample.question));const pair=el('div','pair');for(const [name,label]of [['rhys','Rhys adaptation · saved adapter'],['qwen_base','Qwen base · adapter disabled']]){const answer=sample[name],card=el('section',`answer ${name}`);card.append(el('h4','',label),el('p','answer-text',answer.text),el('p','card-meta',`Normalized exact match: ${answer.normalized_exact_match?'yes':'no'} · token F1: ${metric(answer.token_f1)} · ${metric(answer.seconds,2)}s`));pair.appendChild(card);}article.appendChild(pair);const reference=el('details','reference-drawer');reference.append(el('summary','','Reveal reference card'),el('p','reference',sample.reference));article.appendChild(reference);
      const measured=measuredSummary(summary);if(measured){const key={id:sample.id,qa_sha256:sample.qa_sha256,weights_sha256:measured.weights_sha256,checkpoint_run_id:measured.checkpoint_run_id},existing=reviews.find(row=>reviewId(row)===reviewId(key)),controls=el('div','review'),buttons=el('div','review-buttons'),correction=el('textarea');correction.maxLength=2000;correction.rows=2;correction.value=existing?.correction||'';correction.setAttribute('aria-label','Optional correction for '+sample.id);correction.placeholder='Optional correction or reviewer note (saved locally only)';const note=el('p','card-meta',existing?'Your local review: '+CHOICES[existing.choice]:'Choose a human review; these labels are separate from the automatic metrics.');let choice=existing?.choice||'unsure';const choices=[];
        const save=()=>{try{const entry={...key,choice,correction:correction.value,reviewed_at:new Date().toISOString()};reviews=saveReview(reviews,entry);note.textContent=persistReviews();for(const [value,button] of choices)button.setAttribute('aria-pressed',String(value===choice));reviewStatus();}catch(error){note.textContent=error.message;}};
        for(const [value,label]of Object.entries(CHOICES)){const button=el('button','quiet',label);button.type='button';button.setAttribute('aria-pressed',String(value===existing?.choice));button.addEventListener('click',()=>{choice=value;save();});choices.push([value,button]);buttons.appendChild(button);}const saveCorrection=el('button','quiet','Save correction'),remove=el('button','quiet','Delete this review');saveCorrection.type=remove.type='button';saveCorrection.addEventListener('click',save);remove.addEventListener('click',()=>{reviews=reviews.filter(row=>reviewId(row)!==reviewId(key));persistReviews();renderSamples();});controls.append(buttons,correction,saveCorrection,remove,note);article.appendChild(controls);
      }else article.appendChild(el('p','card-meta','Review export is available once a measured checkpoint report is loaded.'));$('samples').appendChild(article);
    }reviewStatus();
  }
  $('load-qa').addEventListener('click',async()=>{if(qaLoaded)return;$('load-qa').disabled=true;status('Loading all public QA and per-card scores…');try{const [cards,scores]=await Promise.all([get('anki.jsonl').then(response=>readJSONL(response,{validate:validateQA,signal:controller.signal})),get('scores.jsonl').then(response=>readJSONL(response,{validate:validateScore,maxBytes:12*1024*1024,signal:controller.signal}))]);qa=joinScores(cards,scores);qaLoaded=true;$('load-qa').textContent='All public QA loaded';$('query').disabled=$('cohort').disabled=false;renderQA();status('QA loaded. Search and filters run locally; no additional requests are sent.');}catch(error){status(error.message,true);$('load-qa').textContent='Try loading all QA again';$('load-qa').disabled=false;}});
  $('load-samples').addEventListener('click',async()=>{if(samplesLoaded)return;$('load-samples').disabled=true;status('Loading public generated-answer samples…');try{samples=await get('samples.jsonl').then(response=>readJSONL(response,{validate:validateSample,maxBytes:4*1024*1024,maxRecords:500,signal:controller.signal}));samplesLoaded=true;$('load-samples').textContent='Answer samples loaded';renderSamples();status('Samples loaded. Human reviews stay in this browser until you export them.');}catch(error){status(error.message,true);$('load-samples').disabled=false;$('load-samples').textContent='Try loading samples again';}});
  $('query').addEventListener('input',()=>{page=0;renderQA();});$('cohort').addEventListener('change',()=>{page=0;renderQA();});$('previous').addEventListener('click',()=>{page--;renderQA();});$('next').addEventListener('click',()=>{page++;renderQA();});$('sample-previous').addEventListener('click',()=>{samplePage--;renderSamples();});$('sample-next').addEventListener('click',()=>{samplePage++;renderSamples();});
  $('clear-reviews').addEventListener('click',()=>{reviews=[];try{win.localStorage?.removeItem(REVIEW_KEY);}catch{}renderSamples();status('All local reviewer labels and corrections cleared. No server data was changed.');});
  $('export-reviews').addEventListener('click',()=>{const text=exportReviews(reviews,samples,summary);if(!text){status('No reviews match the loaded public samples and checkpoint.',true);return;}if(exportURL)win.URL.revokeObjectURL(exportURL);exportURL=win.URL.createObjectURL(new Blob([text+'\n'],{type:'application/x-ndjson'}));$('review-download').href=exportURL;$('review-download').hidden=false;$('review-download').click?.();status('Review JSONL prepared locally. Nothing was sent or published.');});
  const initialized=(async()=>{const results=await Promise.allSettled([get('manifest.json').then(response=>smallJSON(response,256*1024)),get('summary.json').then(response=>smallJSON(response,2*1024*1024))]);if(results[0].status==='fulfilled')manifest=results[0].value;if(results[1].status==='fulfilled')summary=results[1].value;renderSummary();reviewStatus();status(measuredSummary(summary)?'Measured report loaded. Full QA and answer samples are loaded only when you choose.':'Evaluation in progress. You can inspect the corpus and training methods while measurements are prepared.');})();
  const dispose=()=>{controller.abort();if(exportURL)win.URL.revokeObjectURL(exportURL);};win.addEventListener('pagehide',dispose);reviewStatus();return {initialized,dispose};
}
if(typeof document!=='undefined'&&typeof window!=='undefined')mountEvals();
