"""Explicit finite Anki evaluation; original $200 ledger and singleton apply."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import modal

HERE=Path('/rhysbot/training') if Path('/rhysbot/training/runtime.py').is_file() else Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from modal_app import core_image,volume,CONFIG,PINS
from runtime import read_json,write_json,safe_run_id
from budget import reserve,finish,committed

ROOT=HERE.parent
TARGETS=json.loads(os.environ.get('RHYSBOT_SERVING_TARGETS','{}'))
app=modal.App('rhysbot-anki-evaluation')
image=(core_image.add_local_dir(str(HERE),'/rhysbot/training',copy=True,ignore=['**/__pycache__/**','**/*.pyc'])
       .add_local_dir(str(ROOT/'data/evaluation'),'/rhysbot/data/evaluation',copy=True)
       .env({'PYTHONPATH':'/rhysbot/training','HF_HOME':'/artifacts/rhysbot-hf-cache','HF_HUB_CACHE':'/artifacts/rhysbot-hf-cache/hub','RHYSBOT_SERVING_TARGETS':json.dumps(TARGETS)}))
control=(modal.Image.debian_slim(python_version='3.12').add_local_dir(str(HERE),'/rhysbot/training',copy=True,ignore=['**/__pycache__/**','**/*.pyc']).env({'PYTHONPATH':'/rhysbot/training'}))


def work(token,pilot,timeout,expected_sha):
    singleton=modal.Dict.from_name('rhysbot-training-singleton',create_if_missing=False)
    if singleton.get('active')!=token:raise ValueError('Reserved singleton required')
    data=Path('/rhysbot/data/evaluation')
    if hashlib.sha256((data/'anki.jsonl').read_bytes()).hexdigest()!=expected_sha:raise ValueError('Frozen evaluation inputs changed')
    target=json.loads(os.environ['RHYSBOT_SERVING_TARGETS'])['qwen']
    from anki_evaluation import evaluate
    try:result=evaluate(data,Path('/artifacts/anki-evaluations')/token,target,PINS,pilot=pilot,deadline_seconds=timeout-90)
    except Exception as e:
        import traceback
        traceback.print_exc()
        result={'status':'failed','error_class':type(e).__name__,'elapsed_seconds':None}
    volume.commit()
    return result


@app.function(image=image,gpu='A100-80GB',cpu=(2,4),memory=(16384,32768),timeout=600,startup_timeout=120,retries=0,min_containers=0,max_containers=1,scaledown_window=2,volumes={'/artifacts':volume})
def pilot_gpu(token,expected_sha):return work(token,True,600,expected_sha)


@app.function(image=image,gpu='A100-80GB',cpu=(2,4),memory=(16384,32768),timeout=2820,startup_timeout=120,retries=0,min_containers=0,max_containers=1,scaledown_window=2,volumes={'/artifacts':volume})
def full_gpu(token,expected_sha):return work(token,False,2820,expected_sha)


@app.function(image=control,cpu=.25,memory=512,timeout=4200,retries=0,min_containers=0,max_containers=1,scaledown_window=2,volumes={'/artifacts':volume})
def coordinate(token,pilot,expected_sha,release_commit):
    safe_run_id(token)
    singleton=modal.Dict.from_name('rhysbot-training-singleton',create_if_missing=False)
    if not singleton.put('active',token,skip_if_exists=True):raise ValueError('Existing research job owns the singleton')
    reserved=False;terminal=False;accounted=False;result=None;timeout=600 if pilot else 2820
    try:
        volume.reload();path=Path('/artifacts/spend-ledger.json');ledger=read_json(path)
        amount=reserve(ledger,token,'qwen','eval',timeout,CONFIG['budget']);reserved=True
        ledger['jobs'][token].update(evaluation='all_released_anki_v1',qa_sha256=expected_sha,release_commit=release_commit)
        write_json(path,ledger);volume.commit()
        call=(pilot_gpu if pilot else full_gpu).spawn(token,expected_sha)
        ledger['jobs'][token]['provider_call_id']=call.object_id;write_json(path,ledger);volume.commit()
        try:result=call.get();terminal=True
        except modal.exception.FunctionTimeoutError:
            terminal=True;result={'status':'failed','error_class':'FunctionTimeoutError'}
        # A transport/control exception retains the active reservation and lock.
        # Only a received result or the provider's execution timeout is terminal.
        volume.reload();ledger=read_json(path)
        finish(ledger,token,result.get('status')=='measured',{'evaluation':result,'full_hold_retained':True})
        write_json(path,ledger);volume.commit();accounted=True
        return {'run_id':token,'reserved_usd':amount,'committed_usd':committed(ledger),'result':result}
    finally:
        if (not reserved or accounted) and singleton.get('active')==token:singleton.pop('active')


@app.function(image=control,cpu=.25,memory=512,timeout=60,retries=0,min_containers=0,max_containers=1,scaledown_window=2,volumes={'/artifacts':volume})
def collect(token):
    safe_run_id(token);volume.reload();base=Path('/artifacts/anki-evaluations')/token
    return {name:(base/name).read_bytes() for name in ['report.json','scores.jsonl','samples.jsonl']}


@app.local_entrypoint()
def main(run_id:str,pilot:bool=False,collect_only:bool=False,output_dir:str=''):
    safe_run_id(run_id)
    if collect_only:
        if not output_dir:raise ValueError('Private output directory required')
        destination=Path(output_dir);destination.mkdir(mode=0o700,parents=True,exist_ok=False)
        for name,raw in collect.remote(run_id).items():(destination/name).write_bytes(raw)
        print(json.dumps({'collected':run_id,'files':3}));return
    status=subprocess.check_output(['git','status','--porcelain','--','training','data/evaluation'],cwd=ROOT,text=True)
    if status.strip():raise ValueError('Commit reviewed training/evaluation inputs before GPU launch')
    release=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    expected=read_json(ROOT/'data/evaluation/manifest.json')['qa_sha256']
    print(json.dumps(coordinate.remote(run_id,pilot,expected,release),indent=2))
