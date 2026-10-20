"""Exercise the remote accounting coordinator without Modal, models or network."""
import ast
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'training'))
from budget import initial_ledger,reserve,finish,committed
from runtime import safe_run_id


class Guard(dict):
    def put(self,key,value,skip_if_exists=False):
        if skip_if_exists and key in self:return False
        self[key]=value;return True


class AccountingTests(unittest.TestCase):
    def run_coordinator(self,*,error=None,fail_commit=False,initial=158.8):
        ledger=initial_ledger();ledger['initial_spent_usd']=initial;ledger['pilot_passed']['qwen']='accepted';guard=Guard();state={'ledger':ledger,'commits':0,'spawns':0}
        def write(_,value):state['ledger']=copy.deepcopy(value)
        def commit():
            state['commits']+=1
            if fail_commit and state['commits']==3:raise OSError('Commit uncertain')
        def get():
            if error:raise error
            return {'status':'measured','scored_records':48}
        def spawn(*args):state['spawns']+=1;return SimpleNamespace(object_id='test-call',get=get)
        fn=SimpleNamespace(spawn=spawn);volume=SimpleNamespace(reload=lambda:None,commit=commit)
        scope={'safe_run_id':safe_run_id,'modal':SimpleNamespace(Dict=SimpleNamespace(from_name=lambda *a,**k:guard),exception=SimpleNamespace(FunctionTimeoutError=type('ExecutionTimeout',(Exception,),{}))),
               'volume':volume,'Path':Path,'read_json':lambda _:copy.deepcopy(state['ledger']),'write_json':write,
               'reserve':reserve,'finish':finish,'committed':committed,'CONFIG':{'budget':{'authorization_usd':200,'max_job_timeout_seconds':7200,'conservative_rate_usd_per_hour':12,'billing_overhead_seconds':180}},'pilot_gpu':fn,'full_gpu':fn}
        tree=ast.parse((ROOT/'training/anki_eval_job.py').read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='coordinate');node.decorator_list=[]
        exec(compile(ast.Module(body=[node],type_ignores=[]),'coordinator','exec'),scope)
        caught=None;result=None
        try:result=scope['coordinate']('anki-eval-test',True,'a'*64,'b'*40)
        except Exception as e:caught=e
        return state,guard,result,caught

    def test_success_retains_full_hold_and_unlocks_after_accounting(self):
        state,guard,result,error=self.run_coordinator();self.assertIsNone(error);self.assertEqual(result['reserved_usd'],2.6);self.assertAlmostEqual(result['committed_usd'],161.4);self.assertEqual(guard,{})
        self.assertEqual(state['ledger']['jobs']['anki-eval-test']['status'],'completed_pending_billing')

    def test_unknown_transport_or_failed_accounting_retains_guard(self):
        for options in [{'error':ConnectionError('Unknown provider state')},{'fail_commit':True}]:
            state,guard,_,error=self.run_coordinator(**options);self.assertIsNotNone(error);self.assertEqual(guard['active'],'anki-eval-test');self.assertEqual(state['spawns'],1);self.assertAlmostEqual(committed(state['ledger']),161.4)

    def test_over_cap_cannot_dispatch(self):
        state,guard,_,error=self.run_coordinator(initial=199);self.assertIsNotNone(error);self.assertEqual(state['spawns'],0);self.assertEqual(guard,{})


if __name__=='__main__':unittest.main()
