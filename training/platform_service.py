"""Private corpus training, explicit checkpoint comparison and authorized Rhys TTS.
All GPU inputs need durable authorization before dispatch. No anonymous GPU entry point.
"""
import hashlib,hmac,io,json,os,re,sys,time,uuid,zipfile
from pathlib import Path
import modal
HERE=Path('/rhysbot/training') if Path('/rhysbot/training/runtime.py').is_file() else Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from modal_app import core_image,volume,CONFIG,PINS
from runtime import read_json,write_json
from budget import reserve,finish,committed
APP=modal.App('rhysbot-platform')
app=APP  # Modal CLI discovers this conventional module attribute.
TARGETS=json.loads(os.environ.get('RHYSBOT_SERVING_TARGETS','{}'))
SECRET=modal.Secret.from_name('rhysbot-platform')
CPU=modal.Image.debian_slim(python_version='3.12').pip_install('fastapi==0.115.12').add_local_dir(str(HERE),remote_path='/rhysbot/training',copy=True).env({'PYTHONPATH':'/rhysbot/training'})
GPU=core_image.add_local_dir(str(HERE),remote_path='/rhysbot/training',copy=True).env({'PYTHONPATH':'/rhysbot/training','HF_HOME':'/artifacts/rhysbot-hf-cache','HF_HUB_CACHE':'/artifacts/rhysbot-hf-cache/hub','RHYSBOT_SERVING_TARGETS':json.dumps(TARGETS)})
VOICE=(modal.Image.debian_slim(python_version='3.12').apt_install('libsndfile1','sox').pip_install('torch==2.9.1','torchaudio==2.9.1','qwen-tts==0.1.1','numpy==2.2.6','soundfile==0.13.1').add_local_dir(str(HERE),remote_path='/rhysbot/training',copy=True).env({'PYTHONPATH':'/rhysbot/training','HF_HOME':'/artifacts/rhysbot-voice-hf','HF_HUB_CACHE':'/artifacts/rhysbot-voice-hf'}))
if os.environ.get('RHYSBOT_VOICE_REFERENCE'):
 VOICE=VOICE.add_local_file(os.environ['RHYSBOT_VOICE_REFERENCE'],'/voice/rhys.wav',copy=True)
TTS_MODEL='Qwen/Qwen3-TTS-12Hz-0.6B-Base'
TTS_REV='5d83992436eae1d760afd27aff78a71d676296fc'
_VOICE_MODEL=None
_VOICE_PROMPT=None
COMPARISON_SYSTEM='You are an AI language model in a research comparison, not the human Rhys Lindmark. Answer the user directly. Do not invent private facts or claim to be a person.'

def jobs():
 # Resolve only during remote execution: never serialize an unhydrated Dict.
 return modal.Dict.from_name('rhysbot-platform-jobs',create_if_missing=True)

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()

def job_state(store,key):
 entry=store.get(key)
 if not entry:return None
 # Metadata is immutable; call ID and worker state have independent keys so a
 # slow post-spawn write cannot overwrite running/terminal state.
 return {**entry,**store.get('state:'+key,{}),'call_id':store.get('call:'+key)}

def customer_path(key,root='/artifacts'):
 job_id(key);base=Path(root).resolve();path=base
 for component in ('customer-jobs',key):
  path=path/component
  if path.is_symlink():raise ValueError('Symlink in customer artifact path')
 return path

def job_id(value):
 if not isinstance(value,str) or not re.fullmatch(r'job_[a-f0-9]{32}',value):raise ValueError('Invalid job ID')
 return value

def nonce_id(value):
 if not isinstance(value,str) or str(uuid.UUID(value))!=value:raise ValueError('Canonical UUID required')
 return value

def validated_dispatch(body):
 if not isinstance(body,dict) or set(body)!={'job_id','idempotency_key','recipe','authorization_mode','corpus','limits'}:raise ValueError('Invalid dispatch fields')
 key=job_id(body.get('job_id'))
 if body.get('idempotency_key')!=key or body.get('recipe')!='qwen-lora-v1' or body.get('authorization_mode') not in {'stripe_test','sandbox'}:raise ValueError('Fixed test recipe required')
 c=body.get('corpus',{})
 if not isinstance(c,dict) or set(c)!={'url','sha256','bytes','format'}:raise ValueError('Invalid corpus fields')
 if c.get('url')!='https://ai.rhyslindmark.com/api/rhysbot/train-data?job='+key or not re.fullmatch('[a-f0-9]{64}',str(c.get('sha256'))) or type(c.get('bytes')) is not int or not 1<=c['bytes']<=5242880 or c.get('format') not in {'txt','jsonl'}:raise ValueError('Fixed private corpus required')
 limits=body.get('limits')
 if limits!={'timeout_seconds':900,'max_cost_usd':3.6} or type(limits['timeout_seconds']) is not int or type(limits['max_cost_usd']) not in {int,float}:raise ValueError('Fixed execution ceiling required')
 return key

def corpus_text(raw,kind):
 if not 1<=len(raw)<=5242880:raise ValueError('Corpus size bound')
 text=raw.decode('utf-8',errors='strict')
 controls=r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]'
 if not text.strip() or re.search(controls,text):raise ValueError('Binary or empty corpus')
 if kind=='txt':return text
 if kind!='jsonl':raise ValueError('Unsupported corpus format')
 result=[]
 for line in text.split('\n'):
  if not line.strip():continue
  if len(line.encode())>32768:raise ValueError('JSONL line too large')
  row=json.loads(line)
  if not isinstance(row,dict) or set(row)!={'text'} or not isinstance(row['text'],str) or not row['text'].strip() or re.search(controls,row['text']):raise ValueError('JSONL text-only schema required')
  result.append(row['text'])
 if not result:raise ValueError('Empty JSONL corpus')
 return '\n\n'.join(result)

def fetch_corpus(body,secret,opener=None,clock=time.monotonic):
 """Fixed authenticated URL; 45s wall deadline plus at most 10s blocked read."""
 from urllib.request import Request,build_opener,HTTPRedirectHandler
 validated_dispatch(body)
 class NoRedirect(HTTPRedirectHandler):
  def redirect_request(self,*args,**kwargs):return None
 opener=opener or build_opener(NoRedirect());expected=body['corpus'];start=clock();raw=bytearray()
 request=Request(expected['url'],headers={'Authorization':'Bearer '+secret,'Accept-Encoding':'identity','User-Agent':'RhysbotTraining/1.0 (+https://ai.rhyslindmark.com/rhysbot/)'})
 with opener.open(request,timeout=10) as response:
  if response.status!=200 or response.headers.get('Content-Encoding','identity')!='identity':raise ValueError('Private corpus unavailable')
  length=response.headers.get('Content-Length')
  if length is not None and (not length.isdigit() or int(length)!=expected['bytes']):raise ValueError('Corpus length mismatch')
  while True:
   if clock()-start>45:raise TimeoutError('Corpus fetch deadline')
   chunk=response.read1(min(262144,expected['bytes']-len(raw)+1))
   if clock()-start>45:raise TimeoutError('Corpus fetch deadline')
   if not chunk:break
   raw.extend(chunk)
   if len(raw)>expected['bytes']:raise ValueError('Corpus size overflow')
 if len(raw)!=expected['bytes'] or hashlib.sha256(raw).hexdigest()!=expected['sha256']:raise ValueError('Corpus fetch/hash failure')
 return raw

def token_blocks(tokens,sequence_length=256):
 limit=min(len(tokens),100000)
 return [tokens[i:i+sequence_length] for i in range(0,limit-sequence_length+1,sequence_length)]

def provider_timeout_report(key,error,timeout_class):
 # Plain poll timeouts/expired output are uncertainty, not provider termination.
 if type(error) is timeout_class:return {'status':'failed','job_id':key,'error_type':'FunctionTimeoutError','provider_terminal':True}
 return None

def pending_poll_timeout(error,modal_timeout_class):
 # Modal 1.6.2 poll_function raises builtin TimeoutError for unfinished calls.
 # Keep both exact poll types; subclasses such as FunctionTimeoutError are NOT
 # pending and must go through the explicit provider-terminal classifier.
 return type(error) in {TimeoutError,modal_timeout_class}

def terminal_training(key,report,store,guard,artifacts,root='/artifacts'):
 """Commit full retained hold BEFORE terminal publication/unlock. Idempotent."""
 if report.get('job_id')!=key or report.get('status') not in {'completed','failed'}:raise ValueError('Unverified terminal report')
 artifacts.reload();ledger=read_json(Path(root)/'spend-ledger.json');entry=ledger['jobs'].get(key)
 if not entry or entry.get('family')!='customer_training':raise ValueError('Missing matching training reservation')
 expected='completed_pending_billing' if report['status']=='completed' else 'failed_pending_billing'
 if entry.get('status') not in {'active','running_reserved',expected}:raise ValueError('Reservation terminal mismatch')
 if entry.get('status')!=expected:
  if guard.get('active')!=key:raise ValueError('Training lock owner mismatch')
  finish(ledger,key,report['status']=='completed',{'report':report});write_json(Path(root)/'spend-ledger.json',ledger)
  artifacts.commit()
 elif guard.get('active')==key:
  # A prior commit might have raised after writing. Confirm durable state again
  # before publishing/unlocking rather than treating a local file as a receipt.
  artifacts.commit()
 store.put('state:'+key,{'state':'completed' if report['status']=='completed' else 'failed','artifact_ready':report['status']=='completed','report':report})
 if guard.get('active')==key:guard.pop('active')
 return report

def comparison_histories(messages,qwen_messages):
 def check(rows):
  if not isinstance(rows,list) or not 1<=len(rows)<=12:raise ValueError('History length')
  total=0
  for i,row in enumerate(rows):
   if not isinstance(row,dict) or set(row)!={'role','content'} or row['role']!=('user' if i%2==0 else 'assistant') or not isinstance(row['content'],str) or not row['content'].strip() or '<|' in row['content'] or '|>' in row['content']:raise ValueError('Invalid history')
   total+=len(row['content'].encode())
  if rows[-1]['role']!='user' or total>12000:raise ValueError('History bound')
  return rows
 left=check(messages);right=check(qwen_messages if qwen_messages is not None else messages)
 if [r['content'] for r in left if r['role']=='user']!=[r['content'] for r in right if r['role']=='user']:raise ValueError('Comparison questions must match')
 return left,right

@APP.function(image=GPU,gpu='L4',cpu=(2,4),memory=(12288,24576),timeout=900,retries=0,min_containers=0,max_containers=1,scaledown_window=2,secrets=[SECRET],volumes={'/artifacts':volume})
def train_job(key,body):
 # HF constants freeze at import: establish mounted cache before torch/peft/HF.
 os.environ.update(HF_HOME='/artifacts/rhysbot-hf-cache',HF_HUB_CACHE='/artifacts/rhysbot-hf-cache/hub',TOKENIZERS_PARALLELISM='false',OMP_NUM_THREADS='2',WANDB_MODE='disabled')
 import math,torch
 from peft import LoraConfig,get_peft_model
 from qwen_run import load_model
 validated_dispatch(body);store=jobs();state=job_state(store,key)
 if not state or state.get('state')!='queued' or state.get('body_sha256')!=digest(body):raise ValueError('No persisted job authorization')
 d=modal.Dict.from_name('rhysbot-training-singleton')
 if d.get('active')!=key:raise ValueError('Shared budget guard required')
 if not store.put('execution:'+key,{'started_at':time.time()},skip_if_exists=True):raise ValueError('Job already executed; no retry')
 store.put('state:'+key,{'state':'running'});start=time.monotonic();report={'status':'failed','job_id':key}
 try:
  out=customer_path(key);raw=fetch_corpus(body,os.environ['RHYSBOT_TRAIN_BRIDGE_SECRET'])
  text=corpus_text(raw,body['corpus']['format']);del raw
  if len(text.strip())<200:raise ValueError('At least 200 characters required')
  torch.set_num_threads(2);torch.manual_seed(42)
  model_id='Qwen/Qwen3.5-4B-Base';model,tokenizer,loading=load_model(model_id,PINS['models'][model_id]['revision'])
  targets=CONFIG['qwen']['target_modules']
  model=get_peft_model(model,LoraConfig(r=8,lora_alpha=16,lora_dropout=0,bias='none',task_type='CAUSAL_LM',target_modules=targets))
  model.gradient_checkpointing_enable();model.enable_input_require_grads();model.config.use_cache=False
  tokens=tokenizer(text[:250000],add_special_tokens=False)['input_ids'];del text
  blocks=token_blocks(tokens)
  if len(blocks)<2:raise ValueError('At least 512 tokenized input tokens required')
  validation=blocks[-max(1,len(blocks)//10):];training=blocks[:-len(validation)]
  def eval_loss():
   model.eval()
   with torch.inference_mode():value=sum(float(model(input_ids=(x:=torch.tensor([b],device='cuda')),labels=x).loss) for b in validation[:4])/min(4,len(validation))
   model.train()
   if not math.isfinite(value):raise ValueError('Nonfinite heldout loss')
   return value
  before=eval_loss();optimizer=torch.optim.AdamW((p for p in model.parameters() if p.requires_grad),lr=1e-4);losses=[]
  for step in range(32):
   batch=torch.tensor([training[step%len(training)]],device='cuda');loss=model(input_ids=batch,labels=batch).loss
   if not torch.isfinite(loss):raise ValueError('Nonfinite training loss')
   loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1);optimizer.step();optimizer.zero_grad(set_to_none=True);losses.append(float(loss.detach()))
  after=eval_loss();out.mkdir(parents=True,exist_ok=False);model.save_pretrained(out/'adapter',safe_serialization=True);tokenizer.save_pretrained(out/'adapter')
  report={'status':'completed','job_id':key,'recipe':'qwen-lora-v1','model':model_id,'revision':PINS['models'][model_id]['revision'],'loading':loading,'optimizer_steps':32,'sequence_length':256,'training_tokens':sum(map(len,training)),'optimizer_target_tokens':32*255,'heldout_tokens':sum(map(len,validation)),'loss':losses,'heldout_loss_before':before,'heldout_loss_after':after,'elapsed_seconds':time.monotonic()-start,'corpus_sha256':body['corpus']['sha256'],'lora_target_modules':targets,'trainable_parameters':sum(p.numel() for p in model.parameters() if p.requires_grad),'limitations':['Bounded 32-step adaptation pilot, not a full production model.','Small held-out text sample; loss is not factual accuracy.','Input capped to first 250000 characters / 100000 tokens; corpus remains private.']}
  write_json(out/'training.json',report);volume.commit()
  with zipfile.ZipFile(out/'adapter.zip','w',zipfile.ZIP_DEFLATED) as archive:
   for file in (out/'adapter').rglob('*'):
    if file.is_symlink():raise ValueError('Unexpected adapter symlink')
    if file.is_file():archive.write(file,'adapter/'+str(file.relative_to(out/'adapter')))
   archive.write(out/'training.json','training.json')
  if not 1<= (out/'adapter.zip').stat().st_size<=100*1024*1024:raise ValueError('Adapter artifact size bound')
  report.update(artifact_sha256=hashlib.sha256((out/'adapter.zip').read_bytes()).hexdigest(),artifact_bytes=(out/'adapter.zip').stat().st_size)
  volume.commit()
 except Exception as error:report.update(status='failed',error_type=type(error).__name__,elapsed_seconds=time.monotonic()-start)
 # CPU /status finalizes this exact returned report. HF/Xet may keep cache
 # descriptors open in this GPU process, so it must never volume.reload(),
 # account/publish a terminal state, or unlock here. Until positive CPU-side
 # completion, state remains running and the full reservation/guard is retained.
 return report

def claim_voice_attempt(store,nonce):
 # Modal restarts preempted inputs even with retries=0. Two 180s attempts fit
 # the same $0.15 reservation; public resubmission only replays the call handle.
 for attempt in range(2):
  if store.put(f'execution:voice:{nonce}:{attempt}',True,skip_if_exists=True):return attempt+1
 raise ValueError('Voice recovery allowance exhausted')

@APP.function(image=VOICE,gpu='L4',cpu=(2,4),memory=(8192,16384),timeout=180,retries=0,min_containers=0,max_containers=1,scaledown_window=15,volumes={'/artifacts':volume})
def voice_job(nonce,text):
 os.environ.update(HF_HOME='/artifacts/rhysbot-voice-hf',HF_HUB_CACHE='/artifacts/rhysbot-voice-hf',TOKENIZERS_PARALLELISM='false',OMP_NUM_THREADS='2')
 global _VOICE_MODEL,_VOICE_PROMPT
 # Ongoing voice/compare are separately authorized by the CF $10/month ledger,
 # charged BEFORE this private service. They never consume the $200 training
 # ledger. Only the authenticated CF bridge can issue single-use nonce grants.
 store=jobs();state=store.get('voice:'+nonce)
 if state!={'text_sha256':hashlib.sha256(text.encode()).hexdigest()}:raise ValueError('No voice grant')
 attempt=claim_voice_attempt(store,nonce)
 store.put('voice-state:'+nonce,{'state':'loading_voice'})
 import torch,soundfile as sf
 from huggingface_hub import snapshot_download
 from qwen_tts import Qwen3TTSModel
 torch.set_num_threads(2)
 start=time.monotonic()
 if hashlib.sha256(Path('/voice/rhys.wav').read_bytes()).hexdigest()!='392b8c0ce6e2b70f1ed7bb4e00ce964d08190e892a3e9f5f274c4d5c644d1622':raise ValueError('Unapproved voice reference')
 if _VOICE_MODEL is None:
  try:model_path=snapshot_download(TTS_MODEL,revision=TTS_REV,cache_dir='/artifacts/rhysbot-voice-hf',local_files_only=True)
  except FileNotFoundError:model_path=snapshot_download(TTS_MODEL,revision=TTS_REV,cache_dir='/artifacts/rhysbot-voice-hf')
  # This pinned repository includes the matching speech_tokenizer subdirectory.
  if not (Path(model_path)/'speech_tokenizer').is_dir():raise ValueError('Pinned repository lacks its speech tokenizer')
  _VOICE_MODEL=Qwen3TTSModel.from_pretrained(model_path,device_map='cuda:0',dtype=torch.bfloat16,attn_implementation='sdpa')
 profile=Path('/artifacts/rhysbot-voice-profile-v1.pt')
 profile_reused=profile.is_file()
 if _VOICE_PROMPT is None:
  if profile_reused:
   embedding=torch.load(profile,map_location='cuda:0',weights_only=True)['speaker_embedding']
  else:
   # Build the authorized speaker identity once, independently of reply text.
   item=_VOICE_MODEL.create_voice_clone_prompt(ref_audio='/voice/rhys.wav',x_vector_only_mode=True)[0]
   embedding=item.ref_spk_embedding
   torch.save({'speaker_embedding':embedding.detach().cpu()},profile)
   volume.commit()
  _VOICE_PROMPT={'ref_code':[None],'ref_spk_embedding':[embedding],'x_vector_only_mode':[True],'icl_mode':[False]}
 store.put('voice-state:'+nonce,{'state':'speaking'})
 wavs,sr=_VOICE_MODEL.generate_voice_clone(text=text,language='English',voice_clone_prompt=_VOICE_PROMPT,max_new_tokens=1800,do_sample=False)
 audio=wavs[0]
 if len(audio)/sr>=149:raise ValueError('Speech exceeded duration limit; refusing truncated playback')
 output=io.BytesIO();sf.write(output,audio,sr,format='WAV',subtype='PCM_16');volume.commit()
 return {'audio':output.getvalue(),'sample_rate':sr,'duration_seconds':len(audio)/sr,'elapsed_seconds':time.monotonic()-start,'attempt':attempt,'profile_reused':profile_reused,'model':TTS_MODEL,'revision':TTS_REV}

@APP.function(image=GPU,gpu='L4',cpu=(2,4),memory=(12288,24576),timeout=180,retries=0,min_containers=0,max_containers=1,scaledown_window=2,volumes={'/artifacts':volume})
def compare_job(nonce,messages,qwen_messages=None):
 os.environ.update(HF_HOME='/artifacts/rhysbot-hf-cache',HF_HUB_CACHE='/artifacts/rhysbot-hf-cache/hub',TOKENIZERS_PARALLELISM='false',OMP_NUM_THREADS='2')
 messages,qwen_messages=comparison_histories(messages,qwen_messages);store=jobs();state=store.get('compare:'+nonce)
 if state!={'messages_sha256':digest({'messages':messages,'qwen_messages':qwen_messages})}:raise ValueError('No comparison grant')
 if not store.put('execution:compare:'+nonce,True,skip_if_exists=True):raise ValueError('Comparison already executed')
 import torch
 from serve import checkpoint_artifacts,cached_qwen_checkpoint
 from qwen_run import chat_prompt
 torch.set_num_threads(2)
 targets=json.loads(os.environ['RHYSBOT_SERVING_TARGETS']);target=targets['qwen'];paths=checkpoint_artifacts('/artifacts','qwen',target,PINS);start=time.monotonic();model,tokenizer=cached_qwen_checkpoint(paths,PINS,target)
 left=list(messages);right=list(qwen_messages)
 def encode(rows):return tokenizer(chat_prompt(tokenizer,[{'role':'system','content':COMPARISON_SYSTEM}]+rows),return_tensors='pt',add_special_tokens=False).to('cuda')
 encoded_left,encoded_right=encode(left),encode(right)
 while max(encoded_left['input_ids'].shape[1],encoded_right['input_ids'].shape[1])>384 and len(left)>1:
  del left[:2];del right[:2];encoded_left,encoded_right=encode(left),encode(right)
 if max(encoded_left['input_ids'].shape[1],encoded_right['input_ids'].shape[1])>384:raise ValueError('Question too long for 512-token research checkpoint')
 def answer(encoded):
  n=encoded['input_ids'].shape[1]
  with torch.inference_mode():out=model.generate(**encoded,max_new_tokens=96,do_sample=False,use_cache=True,eos_token_id=tokenizer.convert_tokens_to_ids('<|im_end|>'),pad_token_id=tokenizer.pad_token_id)
  return {'text':tokenizer.decode(out[0,n:],skip_special_tokens=True),'output_tokens':len(out[0,n:])}
 adapted=answer(encoded_left)
 with model.disable_adapter():baseline=answer(encoded_right)
 volume.commit()
 return {'rhys':adapted,'qwen':baseline,'checkpoint_run_id':target['run_id'],'weights_sha256':target['weights_sha256'],'baseline':'same pinned Qwen3.5-4B-Base with adapter disabled','elapsed_seconds':time.monotonic()-start,'input_tokens':{'rhys':encoded_left['input_ids'].shape[1],'qwen':encoded_right['input_ids'].shape[1]},'comparison':'same system, template, retained user turns and decoding; independent assistant histories, saved CPT/SFT adapter enabled versus disabled'}

@APP.function(image=CPU,cpu=.25,memory=512,timeout=240,min_containers=0,max_containers=1,scaledown_window=2,secrets=[SECRET],volumes={'/artifacts':volume})
@modal.asgi_app(requires_proxy_auth=True)
def api():
 from fastapi import FastAPI,Request
 from fastapi.responses import JSONResponse,Response,StreamingResponse
 web=FastAPI();store=jobs()
 async def auth(request):
  if not hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+os.environ['RHYSBOT_TRAIN_BRIDGE_SECRET']):return False
  return True
 async def body(request,limit=20000):
  data=bytearray()
  async for part in request.stream():
   data.extend(part)
   if len(data)>limit:raise ValueError('Body too large')
  return json.loads(data)
 @web.post('/dispatch')
 async def dispatch(request:Request):
  if not await auth(request):return JSONResponse({'error':'unauthorized'},403)
  try:payload=await body(request);key=validated_dispatch(payload)
  except Exception:return JSONResponse({'error':'invalid_dispatch'},400)
  prior=job_state(store,key)
  if prior:
   if prior['body_sha256']!=digest(payload):return JSONResponse({'error':'idempotency_conflict'},409)
   return JSONResponse({'job_id':key,'state':prior['state'] if prior.get('call_id') or prior['state'] in {'completed','failed','budget_blocked'} else 'recovery_required'},202)
  d=modal.Dict.from_name('rhysbot-training-singleton')
  if not d.put('active',key,skip_if_exists=True):return JSONResponse({'job_id':key,'state':'budget_blocked'},202)
  reserved=False
  try:
   volume.reload();ledger=read_json('/artifacts/spend-ledger.json');reserve(ledger,key,'qwen','eval',900,CONFIG['budget']);reserved=True
   ledger['jobs'][key].update(family='customer_training',phase='private_corpus_pilot',status='running_reserved');write_json('/artifacts/spend-ledger.json',ledger);volume.commit()
   entry={'job_id':key,'state':'queued','body_sha256':digest(payload)}
   if not store.put(key,entry,skip_if_exists=True):raise ValueError('Dispatch already exists')
   call=await train_job.spawn.aio(key,payload);store.put('call:'+key,call.object_id)
   current=job_state(store,key)
   return JSONResponse({'job_id':key,'state':current['state']},202)
  except Exception:
   if not reserved and d.get('active')==key:d.pop('active')
   return JSONResponse({'job_id':key,'state':'recovery_required' if reserved else 'budget_blocked'},503)
 @web.get('/status')
 async def status(request:Request,job:str):
  if not await auth(request):return JSONResponse({'error':'unauthorized'},403)
  try:key=job_id(job)
  except ValueError:return JSONResponse({'error':'invalid_job'},400)
  state=job_state(store,key)
  if not state:return JSONResponse({'error':'not_found'},404)
  if state['state'] in {'queued','running'}:
   if not state.get('call_id'):return {'job_id':key,'state':'recovery_required','artifact_ready':False}
   try:
    report=await modal.FunctionCall.from_id(state['call_id']).get.aio(timeout=0)
    terminal_training(key,report,store,modal.Dict.from_name('rhysbot-training-singleton'),volume)
   except Exception as error:
    # FunctionTimeoutError proves provider termination. A polling timeout,
    # OutputExpired or unavailable result does NOT permit unlock/re-dispatch.
    timeout_report=provider_timeout_report(key,error,modal.exception.FunctionTimeoutError)
    if timeout_report:
     terminal_training(key,timeout_report,store,modal.Dict.from_name('rhysbot-training-singleton'),volume)
    elif not pending_poll_timeout(error,modal.exception.TimeoutError):return {'job_id':key,'state':'recovery_required','artifact_ready':False}
   state=job_state(store,key)
  return {'job_id':key,'state':state['state'],'artifact_ready':bool(state.get('artifact_ready'))}
 @web.get('/artifact')
 async def artifact(request:Request,job:str):
  if not await auth(request):return JSONResponse({'error':'unauthorized'},403)
  try:key=job_id(job)
  except ValueError:return JSONResponse({'error':'invalid_job'},400)
  state=job_state(store,key)
  if not state or state.get('state')!='completed':return JSONResponse({'error':'not_ready'},409)
  volume.reload();path=customer_path(key)/'adapter.zip'
  if path.is_symlink() or not path.is_file() or path.stat().st_size>100*1024*1024:return JSONResponse({'error':'artifact_unavailable'},503)
  from runtime import file_sha
  if path.stat().st_size!=state.get('report',{}).get('artifact_bytes') or file_sha(path)!=state.get('report',{}).get('artifact_sha256'):return JSONResponse({'error':'artifact_integrity'},503)
  def chunks():
   with path.open('rb') as f:
    for part in iter(lambda:f.read(262144),b''):yield part
  return StreamingResponse(chunks(),media_type='application/zip',headers={'Content-Length':str(path.stat().st_size),'X-Rhysbot-Artifact-Bytes':str(path.stat().st_size),'X-Rhysbot-Artifact-SHA256':state['report']['artifact_sha256'],'Cache-Control':'no-store'})
 @web.post('/voice')
 async def voice(request:Request):
  if not await auth(request):return JSONResponse({'error':'unauthorized'},403)
  try:
   p=await body(request,16000);text=p['text'];nonce=p['request_id']
   nonce_id(nonce)
   if not isinstance(text,str) or not text.strip() or len(text)>2000 or not 1<=len(text.encode())<=8000:raise ValueError()
  except Exception:return JSONResponse({'error':'invalid_voice'},400)
  grant={'text_sha256':hashlib.sha256(text.encode()).hexdigest()}
  if not await store.put.aio('voice:'+nonce,grant,skip_if_exists=True):
   if await store.get.aio('voice:'+nonce)!=grant:return JSONResponse({'error':'idempotency_conflict'},409)
   return JSONResponse({'state':'queued' if await store.get.aio('voice-call:'+nonce) else 'recovery_required'},202)
  try:
   call=await voice_job.spawn.aio(nonce,text);await store.put.aio('voice-call:'+nonce,call.object_id)
  except Exception:return JSONResponse({'error':'voice_failed_no_retry'},503)
  return JSONResponse({'state':'queued'},202)
 @web.post('/voice-status')
 async def voice_status(request:Request):
  if not await auth(request):return JSONResponse({'error':'unauthorized'},403)
  try:p=await body(request,256);nonce=nonce_id(p['request_id'])
  except Exception:return JSONResponse({'error':'invalid_voice'},400)
  call_id=await store.get.aio('voice-call:'+nonce)
  if not call_id:return JSONResponse({'error':'voice_not_found'},404)
  try:r=await modal.FunctionCall.from_id(call_id).get.aio(timeout=0)
  except Exception as error:
   if pending_poll_timeout(error,modal.exception.TimeoutError):
    return JSONResponse(await store.get.aio('voice-state:'+nonce,{'state':'queued'}),202)
   return JSONResponse({'error':'voice_failed_no_retry'},503)
  audio=r['audio']
  return Response(audio,media_type='audio/wav',headers={'Cache-Control':'no-store','X-Rhysbot-Voice':'AI-generated authorized Rhys voice','X-Generation-Seconds':str(round(r['elapsed_seconds'],3)),'X-Rhysbot-Audio-Bytes':str(len(audio)),'X-Rhysbot-Audio-SHA256':hashlib.sha256(audio).hexdigest(),'X-Rhysbot-Profile-Reused':str(r['profile_reused']).lower()})
 @web.post('/compare')
 async def compare(request:Request):
  if not await auth(request):return JSONResponse({'error':'unauthorized'},403)
  try:
   p=await body(request,30000);messages=p['messages'];qwen_messages=p.get('qwen_messages',messages);nonce=p['request_id']
   nonce_id(nonce);messages,qwen_messages=comparison_histories(messages,qwen_messages)
  except Exception:return JSONResponse({'error':'invalid_history'},400)
  grant=digest({'messages':messages,'qwen_messages':qwen_messages})
  if not store.put('compare:'+nonce,{'messages_sha256':grant},skip_if_exists=True):return JSONResponse({'error':'duplicate_request_not_replayed'},409)
  try:
   call=await compare_job.spawn.aio(nonce,messages,qwen_messages);store.put('compare-call:'+nonce,call.object_id)
   return await call.get.aio(timeout=200)
  except Exception:return JSONResponse({'error':'comparison_failed_no_retry'},503)
 return web
