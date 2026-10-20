"""Stdlib source/helper regressions. No Modal, torch, models or network imports."""
import ast
import builtins
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import time
from types import SimpleNamespace
import unittest
import uuid

SOURCE=Path(__file__).resolve().parents[1]/'training/platform_service.py'
NAMES={'job_id','nonce_id','digest','job_state','validated_dispatch','corpus_text','fetch_corpus','token_blocks','customer_path','terminal_training','provider_timeout_report','pending_poll_timeout','comparison_histories'}
scope={'hashlib':hashlib,'json':json,'Path':Path,'re':re,'time':time,'uuid':uuid}
tree=ast.parse(SOURCE.read_text())
exec(compile(ast.Module(body=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in NAMES],type_ignores=[]),str(SOURCE),'exec'),scope)

class Store(dict):
 def put(self,key,value,skip_if_exists=False):
  if skip_if_exists and key in self:return False
  self[key]=value;return True

def payload(raw=b'Public educational text.'):
 key='job_'+'a'*32
 return {'job_id':key,'idempotency_key':key,'recipe':'qwen-lora-v1','authorization_mode':'sandbox','corpus':{'url':'https://ai.rhyslindmark.com/api/rhysbot/train-data?job='+key,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'format':'txt'},'limits':{'timeout_seconds':900,'max_cost_usd':3.6}}

class PlatformTests(unittest.TestCase):
 def test_exact_dispatch_cannot_fetch_other_urls_or_exceed_budget(self):
  self.assertEqual(scope['validated_dispatch'](payload()),payload()['job_id'])
  for mutate in [lambda p:p['corpus'].update(url='http://127.0.0.1/secret'),lambda p:p['corpus'].update(bytes=True),lambda p:p.update(extra='unapproved'),lambda p:p['limits'].update(timeout_seconds=900.0),lambda p:p['limits'].update(max_cost_usd=4)]:
   p=payload();mutate(p)
   with self.assertRaises(ValueError):scope['validated_dispatch'](p)

 def test_jsonl_matches_text_only_upload_schema_and_controls(self):
  self.assertEqual(scope['corpus_text'](b'{"text":"First"}\n{"text":"Second"}\n','jsonl'),'First\n\nSecond')
  for raw,kind in [(b'{"messages":[]}','jsonl'),(b'{"text":"a","url":"external"}','jsonl'),(b'{"text":"\\u0000"}','jsonl'),(b'\xff','txt'),(b'hi\x00there','txt'),(b' ','txt'),(b'{"text":""}','jsonl')]:
   with self.assertRaises((ValueError,UnicodeError)):scope['corpus_text'](raw,kind)

 def test_bounded_fetch_auth_no_redirect_hash_and_always_close(self):
  class Reply:
   status=200;headers={}
   def __init__(self,raw):self.raw=io.BytesIO(raw);self.closed=False
   def __enter__(self):return self
   def __exit__(self,*args):self.closed=True
   def read1(self,n):return self.raw.read(n)
  class Opener:
   def __init__(self,reply):self.reply=reply
   def open(self,request,timeout):self.request=request;self.timeout=timeout;return self.reply
  raw=b'Private upload example';reply=Reply(raw);opener=Opener(reply)
  self.assertEqual(scope['fetch_corpus'](payload(raw),'service-secret',opener=opener),raw)
  self.assertTrue(reply.closed);self.assertEqual(opener.timeout,10)
  self.assertEqual(opener.request.get_header('Authorization'),'Bearer service-secret')
  self.assertEqual(opener.request.get_header('User-agent'),'RhysbotTraining/1.0 (+https://ai.rhyslindmark.com/rhysbot/)')
  bad=Reply(raw+b'overflow')
  with self.assertRaises(ValueError):scope['fetch_corpus'](payload(raw),'secret',opener=Opener(bad))
  self.assertTrue(bad.closed)
  reply=Reply(raw);times=iter([0,0,46])
  with self.assertRaises(TimeoutError):scope['fetch_corpus'](payload(raw),'secret',opener=Opener(reply),clock=lambda:next(times))
  self.assertTrue(reply.closed)

 def test_exact_512_tokens_has_two_full_blocks(self):
  self.assertEqual(len(scope['token_blocks'](list(range(512)))),2)
  self.assertEqual(len(scope['token_blocks'](list(range(511)))),1)
  self.assertTrue(all(len(b)==256 for b in scope['token_blocks'](list(range(100010)))))

 def test_post_spawn_handle_does_not_overwrite_running_or_terminal(self):
  store=Store();key=payload()['job_id'];store.put(key,{'job_id':key,'state':'queued'});store.put('state:'+key,{'state':'completed','artifact_ready':True});store.put('call:'+key,'fc-real')
  self.assertEqual(scope['job_state'](store,key)['state'],'completed');self.assertEqual(scope['job_state'](store,key)['call_id'],'fc-real')

 def test_terminal_commit_before_publish_unlock_and_failure_retains_lock(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);key=payload()['job_id'];path=root/'spend-ledger.json'
   ledger={'jobs':{key:{'family':'customer_training','status':'running_reserved','reserved_usd':3.6}}};path.write_text(json.dumps(ledger));events=[]
   class Artifact:
    broken=True
    def reload(self):events.append('reload')
    def commit(self):
     events.append('commit')
     if self.broken:raise RuntimeError('commit uncertain')
   class RecordingStore(Store):
    def put(self,k,v,skip_if_exists=False):events.append('publish');return super().put(k,v,skip_if_exists)
   class Guard(Store):
    def pop(self,k):events.append('unlock');return super().pop(k)
   def finish(ledger,key,success,evidence):ledger['jobs'][key].update(status='completed_pending_billing' if success else 'failed_pending_billing',evidence=evidence)
   scope.update(read_json=lambda p:json.loads(Path(p).read_text()),write_json=lambda p,v:Path(p).write_text(json.dumps(v)),finish=finish)
   store=RecordingStore();guard=Guard(active=key);artifact=Artifact();report={'job_id':key,'status':'failed','error_type':'FunctionTimeoutError','provider_terminal':True}
   with self.assertRaises(RuntimeError):scope['terminal_training'](key,report,store,guard,artifact,root)
   self.assertEqual(guard['active'],key);self.assertNotIn('state:'+key,store)
   artifact.broken=False;scope['terminal_training'](key,report,store,guard,artifact,root)
   self.assertLess(events.index('commit'),events.index('publish'));self.assertLess(events.index('publish'),events.index('unlock'))
   self.assertEqual(json.loads(path.read_text())['jobs'][key]['reserved_usd'],3.6);self.assertNotIn('active',guard)

 def test_history_same_user_turns_independent_assistants_and_utf8_bounds(self):
  left=[{'role':'user','content':'Question'},{'role':'assistant','content':'Adapted answer'},{'role':'user','content':'Followup'}];right=[dict(r) for r in left];right[1]['content']='Base answer'
  self.assertEqual(scope['comparison_histories'](left,right),(left,right));right[2]['content']='Different question'
  with self.assertRaises(ValueError):scope['comparison_histories'](left,right)
  with self.assertRaises(ValueError):scope['comparison_histories']([{'role':'user','content':'🪴'*3001}],None)

 def test_only_exact_provider_timeout_proves_terminal_failure(self):
  class ProviderTimeout(TimeoutError):pass
  class ExpiredOutput(Exception):pass
  class OtherTimeout(ProviderTimeout):pass
  key=payload()['job_id']
  self.assertTrue(scope['provider_timeout_report'](key,ProviderTimeout(),ProviderTimeout)['provider_terminal'])
  for error in [TimeoutError(),ExpiredOutput(),OtherTimeout(),RuntimeError()]:self.assertIsNone(scope['provider_timeout_report'](key,error,ProviderTimeout))

 def test_builtin_poll_timeout_keeps_current_state_and_never_proves_terminal(self):
  class ModalPollTimeout(Exception):pass
  class FunctionTimeout(ModalPollTimeout):pass
  key=payload()['job_id'];store=Store({key:{'job_id':key,'state':'running'}});guard=Store(active=key)
  for error in [TimeoutError(),ModalPollTimeout()]:
   self.assertTrue(scope['pending_poll_timeout'](error,ModalPollTimeout))
   self.assertIsNone(scope['provider_timeout_report'](key,error,FunctionTimeout))
   self.assertEqual(scope['job_state'](store,key)['state'],'running');self.assertEqual(guard['active'],key)
  self.assertFalse(scope['pending_poll_timeout'](FunctionTimeout(),ModalPollTimeout))
  self.assertIsNotNone(scope['provider_timeout_report'](key,FunctionTimeout(),FunctionTimeout))

 def test_persistent_cache_is_visible_when_model_libraries_first_import(self):
  expected={'train_job':('/artifacts/rhysbot-hf-cache','/artifacts/rhysbot-hf-cache/hub'),'compare_job':('/artifacts/rhysbot-hf-cache','/artifacts/rhysbot-hf-cache/hub'),'voice_job':('/artifacts/rhysbot-voice-hf','/artifacts/rhysbot-voice-hf')}
  library_roots={'torch','peft','qwen_run','serve','huggingface_hub','qwen_tts'}
  for name,(home,hub) in expected.items():
   fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name);environment={};seen=[];nodes=[]
   for n in fn.body:
    if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and ast.unparse(n.value.func)=='os.environ.update':nodes.append(n)
    elif isinstance(n,(ast.Import,ast.ImportFrom)):nodes.append(n)
   def fake_import(module,globals=None,locals=None,fromlist=(),level=0):
    if module.split('.')[0] in library_roots:
     seen.append((module,dict(environment)))
     return SimpleNamespace(**{field:object() for field in (fromlist or ())})
    if module=='soundfile':return SimpleNamespace()
    if module=='voice_chunks':return SimpleNamespace(speech_chunks=object())
    if module=='numpy':return SimpleNamespace()
    return builtins.__import__(module,globals,locals,fromlist,level)
   ns={'os':SimpleNamespace(environ=environment),'__builtins__':{**vars(builtins),'__import__':fake_import}}
   exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),ns)
   self.assertTrue(seen,name)
   for module,frozen in seen:self.assertEqual((frozen.get('HF_HOME'),frozen.get('HF_HUB_CACHE')),(home,hub),(name,module))
  for image,(home,hub) in [('GPU',expected['train_job']),('VOICE',expected['voice_job'])]:
   assign=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==image for t in n.targets))
   env=assign.value.args[0];values={k.value:v for k,v in zip(env.keys,env.values)}
   self.assertEqual(ast.literal_eval(values['HF_HOME']),home);self.assertEqual(ast.literal_eval(values['HF_HUB_CACHE']),hub)

 def test_artifacts_reject_descendant_symlink_and_nonce_is_canonical(self):
  self.assertEqual(scope['nonce_id']('12345678-1234-4234-8234-123456789abc'),'12345678-1234-4234-8234-123456789abc')
  with self.assertRaises(ValueError):scope['nonce_id']('-'*36)
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'other').mkdir();(root/'customer-jobs').symlink_to(root/'other')
   with self.assertRaises(ValueError):scope['customer_path'](payload()['job_id'],root)

 def test_packaged_module_no_lazy_global_dict_and_positive_timeout_only(self):
  for node in tree.body:
   if isinstance(node,ast.Assign):
    self.assertFalse(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='from_name' and isinstance(n.func.value,ast.Attribute) and n.func.value.attr=='Dict' for n in ast.walk(node)))
  source=SOURCE.read_text();self.assertIn('app=APP',source);self.assertIn("provider_timeout_report(key,error,modal.exception.FunctionTimeoutError)",source)
  self.assertNotIn('TTS_TOKENIZER_REV',source);self.assertIn('x_vector_only_mode=True',source)

 def test_gpu_returns_report_without_reload_accounting_or_terminal_publish(self):
  train=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='train_job')
  calls=[n for n in ast.walk(train) if isinstance(n,ast.Call)]
  self.assertFalse(any(isinstance(n.func,ast.Name) and n.func.id in {'terminal_training','finish'} for n in calls))
  self.assertFalse(any(isinstance(n.func,ast.Attribute) and n.func.attr in {'reload','pop'} for n in calls))
  self.assertIsInstance(train.body[-1],ast.Return);self.assertEqual(train.body[-1].value.id,'report')
  # Structurally prohibit GPU finally-accounting; the executable CPU commit/
  # publication ordering regression above checks the actual finalizer behavior.
  block=next(n for n in train.body if isinstance(n,ast.Try))
  self.assertFalse(block.finalbody)
  cpu=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='api')
  status=next(n for n in ast.walk(cpu) if isinstance(n,ast.AsyncFunctionDef) and n.name=='status')
  terminal_calls=[n for n in ast.walk(status) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='terminal_training']
  self.assertEqual(len(terminal_calls),2)  # actual returned report or exact provider timeout

if __name__=='__main__':unittest.main()
