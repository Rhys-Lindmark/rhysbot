"""Small CPU streaming voice; private reference and conditioning never leave Modal.

Pocket TTS by Kyutai; pinned ONNX export by KevinAHM (CC BY 4.0 models,
Apache 2.0 inference wrapper). No model downloads or ML run on the developer Mac.
"""
import base64,hashlib,hmac,io,json,os,time,uuid
from pathlib import Path
import modal

REV='58a6d00cf13d239b6748cb0769f35c580a8f606c'
WRAPPER_SHA='4381a4396ba08b2626a25a87001e3c51dbacd136e1022d2d40a8cefb14b44be0'
REFERENCE_SHA='392b8c0ce6e2b70f1ed7bb4e00ce964d08190e892a3e9f5f274c4d5c644d1622'

def prepare_assets():
 from huggingface_hub import snapshot_download
 files=['pocket_tts_onnx.py','onnx/LICENSE']
 names=['bundle.json','bos_before_voice.npy','tokenizer.model','mimi_encoder.onnx','text_conditioner.onnx','flow_lm_main_int8.onnx','flow_lm_flow_int8.onnx','mimi_decoder_int8.onnx']
 snapshot_download('KevinAHM/pocket-tts-onnx',revision=REV,local_dir='/pocket',allow_patterns=files+['onnx/english_2026-04/'+n for n in names])
 if hashlib.sha256(Path('/pocket/pocket_tts_onnx.py').read_bytes()).hexdigest()!=WRAPPER_SHA:raise ValueError('Unexpected pinned inference code')

def engine(include_encoder=True):
 import onnxruntime as ort
 from pocket_tts_onnx import PocketTTSOnnx
 class SmallVoice(PocketTTSOnnx):
  def _make_session_options(self):
   opts=ort.SessionOptions();opts.intra_op_num_threads=2;opts.inter_op_num_threads=1
   opts.enable_cpu_mem_arena=False;opts.enable_mem_pattern=False
   opts.add_session_config_entry('session.intra_op.allow_spinning','0')
   opts.add_session_config_entry('session.inter_op.allow_spinning','0')
   opts.add_session_config_entry('session.set_denormal_as_zero','1')
   return opts
  def _load_models(self):
   if include_encoder:return super()._load_models()
   self.mimi_encoder=None;opts=self._make_session_options()
   for name in ['text_conditioner','flow_lm_main','flow_lm_flow','mimi_decoder']:
    file='text_conditioner.onnx' if name=='text_conditioner' else self._model_file(name)
    ready=self.bundle_dir/(name+'.ready.onnx')
    if ready.exists():file=ready.name;opts.graph_optimization_level=ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    setattr(self,name,ort.InferenceSession(str(self.bundle_dir/file),sess_options=opts,providers=self.providers))
 return SmallVoice(models_dir='/pocket/onnx',language='english_2026-04',device='cpu',temperature=.3)

def prepare_conditioning():
 import numpy as np,onnxruntime as ort
 if hashlib.sha256(Path('/voice/rhys.wav').read_bytes()).hexdigest()!=REFERENCE_SHA:raise ValueError('Unapproved voice reference')
 model=engine(True);state=model.prepare_voice_state('/voice/rhys.wav')
 np.savez_compressed('/voice/rhys-state.npz',**state)
 # Perform portable CPU graph rewrites once at image build, not every cold boot.
 # Extended excludes hardware-specific layout optimizations such as NCHWc.
 for name in ['text_conditioner','flow_lm_main','flow_lm_flow','mimi_decoder']:
  file='text_conditioner.onnx' if name=='text_conditioner' else model._model_file(name)
  opts=model._make_session_options();opts.graph_optimization_level=ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
  opts.optimized_model_filepath=str(model.bundle_dir/(name+'.ready.onnx'))
  ort.InferenceSession(str(model.bundle_dir/file),sess_options=opts,providers=['CPUExecutionProvider'])

app=modal.App('rhysbot-pocket-voice-v4')
image=(modal.Image.debian_slim(python_version='3.12').pip_install('onnxruntime==1.23.2','numpy==2.2.6','soundfile==0.13.1','sentencepiece==0.2.1','huggingface-hub==1.1.5','safetensors==0.6.2','scipy==1.15.3','fastapi==0.115.12').run_function(prepare_assets).env({'PYTHONPATH':'/pocket'}))
if os.environ.get('RHYSBOT_VOICE_REFERENCE'):image=image.add_local_file(os.environ['RHYSBOT_VOICE_REFERENCE'],'/voice/rhys.wav',copy=True).run_function(prepare_conditioning)

@app.cls(image=image,cpu=(.125,4),memory=(384,768),timeout=90,startup_timeout=60,min_containers=1,max_containers=1,scaledown_window=60,secrets=[modal.Secret.from_name('rhysbot-platform')])
@modal.concurrent(max_inputs=4,target_inputs=1)
class PocketVoice:
 @modal.enter()
 def load(self):
  from concurrent.futures import ThreadPoolExecutor
  self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='voice')
  self.executor.submit(self.load_model).result()

 def load_model(self):
  import numpy as np
  if hashlib.sha256(Path('/voice/rhys.wav').read_bytes()).hexdigest()!=REFERENCE_SHA:raise ValueError('Unapproved voice reference')
  self.model=engine(False)
  self.voice='/voice/rhys.wav'
  with np.load('/voice/rhys-state.npz',allow_pickle=False) as state:self.model._voice_state_cache[self.voice]={name:state[name] for name in state.files}
  warm=self.model.stream('Hello.',voice=self.voice,first_chunk_frames=2,max_frames=40)
  next(warm);warm.close()

 @modal.method()
 def benchmark(self,text):
  return self.executor.submit(self.measure,text).result()

 def measure(self,text):
  import numpy as np,soundfile as sf,resource
  start=time.monotonic();chunks=[];first=None;first_signal=None
  for part in self.model.stream(text,voice=self.voice,first_chunk_frames=2,max_chunk_frames=3,max_frames=1200):
   now=time.monotonic()-start
   if first is None:first=now
   if first_signal is None and np.max(np.abs(part),initial=0)>.001:first_signal=now
   chunks.append(part)
   if now>60 or sum(len(p) for p in chunks)>24000*149:raise ValueError('Benchmark bound')
  audio=np.concatenate(chunks);out=io.BytesIO();sf.write(out,audio,24000,format='WAV',subtype='PCM_16')
  return {'audio':out.getvalue(),'first_pcm_seconds':first,'first_signal_seconds':first_signal,'elapsed_seconds':time.monotonic()-start,'duration_seconds':len(audio)/24000,'parts':len(chunks),'max_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}

 @modal.asgi_app(requires_proxy_auth=True)
 def api(self):
  import threading,asyncio
  from fastapi import FastAPI,Request
  from fastapi.responses import JSONResponse,StreamingResponse
  import numpy as np
  web=FastAPI();lock=threading.Lock()
  @web.post('/stream')
  async def stream(request:Request):
   secret=os.environ.get('RHYSBOT_TRAIN_BRIDGE_SECRET','')
   if len(secret)<32 or not hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+secret):return JSONResponse({'error':'unauthorized'},401)
   raw=b''
   async for chunk in request.stream():
    raw+=chunk
    if len(raw)>16000:return JSONResponse({'error':'too_large'},413)
   try:
    body=json.loads(raw);text=body['text'];nonce=body['request_id']
    if not isinstance(text,str) or not text.strip() or len(text)>2000 or len(text.encode())>8000 or str(uuid.UUID(nonce))!=nonce:raise ValueError()
   except Exception:return JSONResponse({'error':'invalid_request'},400)
   if not lock.acquire(blocking=False):return JSONResponse({'error':'voice_busy'},429)
   store=modal.Dict.from_name('rhysbot-pocket-claims',create_if_missing=True)
   try:claimed=await store.put.aio(nonce,True,skip_if_exists=True)
   except Exception:lock.release();return JSONResponse({'error':'voice_claim_unavailable'},503)
   if not claimed:lock.release();return JSONResponse({'error':'voice_already_dispatched'},409)
   async def events():
    start=time.monotonic();samples=0;first=None
    iterator=self.model.stream(text,voice=self.voice,first_chunk_frames=2,max_chunk_frames=3,max_frames=1800)
    def step():
     try:return next(iterator)
     except StopIteration:return None
    try:
     while True:
      audio=await asyncio.wrap_future(self.executor.submit(step))
      if audio is None:break
      if first is None:first=time.monotonic()-start
      samples+=len(audio)
      if samples>24000*149 or time.monotonic()-start>75:raise ValueError('Speech limit')
      pcm=(np.clip(audio,-1,1)*32767).astype('<i2').tobytes()
      yield 'data: '+json.dumps({'type':'audio','data':base64.b64encode(pcm).decode()})+'\n\n'
     yield 'data: '+json.dumps({'type':'done','generation_seconds':time.monotonic()-start,'first_pcm_seconds':first})+'\n\n'
    except GeneratorExit:raise
    except Exception as e:
     print(json.dumps({'event':'voice_stream_failed','kind':type(e).__name__,'seconds':time.monotonic()-start,'samples':samples}),flush=True)
     yield 'data: {"type":"error","message":"Voice stream stopped before completion."}\n\n'
    finally:
     # Close on the same inference thread, after any in-flight ONNX call.
     # Its callback retains the lock until the iterator is actually closed.
     self.executor.submit(iterator.close).add_done_callback(lambda _:lock.release())
   return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})
  return web

@app.local_entrypoint()
def pilot(output_dir:str):
 import wave
 dest=Path(output_dir);dest.mkdir(mode=0o700,parents=True,exist_ok=False)
 start=time.monotonic();r=PocketVoice().benchmark.remote('Hi, I am AI Rhys. The Digicene is a new chapter in evolution, shaped by digital systems that learn and coordinate with us.')
 audio=r.pop('audio');r['wall_seconds']=time.monotonic()-start;r['bytes']=len(audio);r['sha256']=hashlib.sha256(audio).hexdigest();r['revision']=REV
 (dest/'voice.wav').write_bytes(audio);(dest/'result.json').write_text(json.dumps(r,indent=2));print(json.dumps(r))
