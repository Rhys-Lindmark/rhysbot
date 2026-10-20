"""Execute the actual async voice routes with provider/storage doubles; no ML imports."""
import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
import uuid

TREE=ast.parse((Path(__file__).parents[1]/'training/platform_service.py').read_text())
API=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='api')
ROUTES=[n for n in API.body if isinstance(n,ast.AsyncFunctionDef) and n.name in {'voice','voice_status'}]
for route in ROUTES:route.decorator_list=[]

class AsyncMethod:
 def __init__(self,fn):self.fn=fn
 async def aio(self,*args,**kwargs):return self.fn(*args,**kwargs)

class Store(dict):
 def __init__(self):super().__init__();self.get=AsyncMethod(super().get);self.put=AsyncMethod(self.save)
 def save(self,k,v,skip_if_exists=False):
  if skip_if_exists and k in self:return False
  self[k]=v;return True

class Response:
 def __init__(self,body,status_code=200,headers=None,media_type=None):self.body=body;self.status_code=status_code;self.headers=headers or {};self.media_type=media_type

class VoiceRouteTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.store=Store();self.spawned=0;self.result=TimeoutError();self.nonce='12345678-1234-4234-8234-123456789abc'
  def spawn(nonce,text):self.spawned+=1;return SimpleNamespace(object_id='fc-one')
  def get(timeout):
   if isinstance(self.result,Exception):raise self.result
   return self.result
  async def auth(request):return True
  async def body(request,limit):return request
  def canonical(value):
   if str(uuid.UUID(value))!=value:raise ValueError()
   return value
  class PollTimeout(Exception):pass
  self.scope={'Request':object,'JSONResponse':Response,'Response':Response,'store':self.store,'auth':auth,'body':body,'nonce_id':canonical,'hashlib':hashlib,'voice_job':SimpleNamespace(spawn=AsyncMethod(spawn)),'modal':SimpleNamespace(FunctionCall=SimpleNamespace(from_id=lambda _:SimpleNamespace(get=AsyncMethod(get))),exception=SimpleNamespace(TimeoutError=PollTimeout)),'pending_poll_timeout':lambda e,t:type(e) in {TimeoutError,t}}
  exec(compile(ast.Module(body=ROUTES,type_ignores=[]),'voice-routes','exec'),self.scope)

 async def test_duplicate_dispatch_replays_handle_and_never_spawns_another_gpu(self):
  request={'request_id':self.nonce,'text':'A full reply longer than five hundred characters. '*20}
  first=await self.scope['voice'](request);again=await self.scope['voice'](request)
  self.assertEqual((first.status_code,again.status_code),(202,202));self.assertEqual(self.spawned,1)
  changed=await self.scope['voice']({**request,'text':'Different'})
  self.assertEqual(changed.status_code,409);self.assertEqual(self.spawned,1)

 async def test_status_pending_then_verified_audio_without_gpu_dispatch(self):
  self.store['voice-call:'+self.nonce]='fc-one';self.store['voice-state:'+self.nonce]={'state':'speaking'}
  pending=await self.scope['voice_status']({'request_id':self.nonce})
  self.assertEqual(pending.status_code,202);self.assertEqual(pending.body,{'state':'speaking'})
  self.result={'audio':b'WAVE'*25,'elapsed_seconds':51.23,'profile_reused':True}
  for _ in range(2):
   ready=await self.scope['voice_status']({'request_id':self.nonce})
   self.assertEqual(ready.body,self.result['audio']);self.assertEqual(ready.headers['X-Rhysbot-Audio-Bytes'],'100');self.assertEqual(ready.headers['X-Rhysbot-Audio-SHA256'],hashlib.sha256(ready.body).hexdigest())
  self.assertEqual(self.spawned,0)

 async def test_missing_and_failed_jobs_are_not_restarted(self):
  self.assertEqual((await self.scope['voice_status']({'request_id':self.nonce})).status_code,404)
  self.store['voice-call:'+self.nonce]='fc-one';self.result=RuntimeError('Provider failed')
  self.assertEqual((await self.scope['voice_status']({'request_id':self.nonce})).status_code,503);self.assertEqual(self.spawned,0)
  self.store['voice:'+self.nonce]={'text_sha256':hashlib.sha256(b'Same').hexdigest()}
  uncertain=await self.scope['voice']({'request_id':self.nonce,'text':'Same'})
  self.assertEqual(uncertain.body['state'],'queued');self.assertEqual(self.spawned,0)

 async def test_invalid_nonce_and_oversized_text_never_dispatch(self):
  for request in [{'request_id':'-'*36,'text':'Valid'},{'request_id':self.nonce,'text':'x'*2001},{'request_id':self.nonce,'text':' '}]:
   self.assertEqual((await self.scope['voice'](request)).status_code,400)
  self.assertEqual(self.spawned,0)

class VoiceRecoveryTests(unittest.TestCase):
 def test_provider_preemption_has_one_bounded_recovery_without_a_new_grant(self):
  fn=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='claim_voice_attempt')
  ns={};exec(compile(ast.Module(body=[fn],type_ignores=[]),'voice-attempts','exec'),ns)
  class SyncStore(dict):
   def put(self,k,v,skip_if_exists=False):
    if k in self:return False
    self[k]=v;return True
  store=SyncStore();claim=ns['claim_voice_attempt']
  self.assertEqual(claim(store,'same-job'),1);self.assertEqual(claim(store,'same-job'),2)
  with self.assertRaises(ValueError):claim(store,'same-job')
  self.assertEqual(len(store),2)
