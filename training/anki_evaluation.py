"""Finite paired checkpoint evaluation. All model work runs remotely."""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import time


def normalize(text):return ' '.join(re.findall(r'\w+',text.casefold()))


def answer_metrics(answer,reference):
    a,b=normalize(answer),normalize(reference);left,right=Counter(a.split()),Counter(b.split())
    overlap=sum((left&right).values());precision=overlap/max(1,sum(left.values()));recall=overlap/max(1,sum(right.values()))
    return {'normalized_exact_match':a==b,'token_f1':2*precision*recall/(precision+recall) if precision+recall else 0}


def stable_sample(rows,per_cohort):
    selected=[]
    for cohort in sorted({x['frozen_sft_split'] for x in rows}):
        candidates=sorted((x for x in rows if x['frozen_sft_split']==cohort),key=lambda x:hashlib.sha256(('anki-eval-v1:'+x['id']).encode()).hexdigest())
        selected.extend(candidates[:per_cohort])
    return selected


def evaluate(data,out,target,pins,*,pilot=False,deadline_seconds=3300):
    import torch
    import torch.nn.functional as F
    from qwen_run import load_model,chat_tokens,chat_prompt
    from serve import checkpoint_artifacts
    start=time.monotonic();data=Path(data);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((data/'manifest.json').read_text());raw=(data/'anki.jsonl').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=manifest['qa_sha256']:raise ValueError('Evaluation data changed')
    rows=[json.loads(l) for l in raw.splitlines() if l.strip()]
    if pilot:rows=stable_sample(rows,16)
    paths=checkpoint_artifacts('/artifacts','qwen',target,pins)
    model_id='Qwen/Qwen3.5-4B-Base'
    model,tokenizer,loading=load_model(model_id,pins['models'][model_id]['revision'],adapter=paths['directory'])
    model.eval();torch.set_num_threads(2)
    from safetensors.torch import load_file
    from peft.utils.save_and_load import get_peft_model_state_dict
    saved=load_file(str(paths['weights']));restored=get_peft_model_state_dict(model)
    if set(saved)!=set(restored) or not all(torch.equal(restored[k].detach().cpu(),v.to(restored[k].dtype)) for k,v in saved.items()):raise ValueError('Adapter restore mismatch')
    del saved,restored
    def messages(row):return [{'role':'system','content':manifest['system']},{'role':'user','content':manifest['question_prefix']+row['question']},{'role':'assistant','content':row['reference']}]
    encoded=[];skipped=[]
    for row in rows:
        full_ids,full_labels=chat_tokens(tokenizer,messages(row));ids,labels=full_ids[:512],full_labels[:512]
        count=sum(v!=-100 for v in labels[1:])
        if not count:skipped.append({'id':row['id'],'reason':'question_exceeds_512_token_context'});continue
        encoded.append({'row':row,'ids':ids,'labels':labels,'target_tokens':count,'full_target_tokens':sum(v!=-100 for v in full_labels[1:]),'truncated':len(full_ids)>512})
    encoded.sort(key=lambda x:(len(x['ids']),x['row']['id']))
    scores={x['row']['id']:{'id':x['row']['id'],'frozen_sft_split':x['row']['frozen_sft_split'],'target_tokens':x['target_tokens'],'full_target_tokens':x['full_target_tokens'],'truncated':x['truncated']} for x in encoded}
    timings={}
    for variant in ['rhys','qwen_base']:
        phase=time.monotonic();context=model.disable_adapter() if variant=='qwen_base' else __import__('contextlib').nullcontext()
        with context,torch.inference_mode():
            for begin in range(0,len(encoded),4):
                if time.monotonic()-start>deadline_seconds:raise TimeoutError('Finite evaluation deadline')
                batch=encoded[begin:begin+4];length=max(len(x['ids']) for x in batch)
                ids=torch.tensor([x['ids']+[tokenizer.pad_token_id]*(length-len(x['ids'])) for x in batch],device='cuda')
                labels=torch.tensor([x['labels']+[-100]*(length-len(x['labels'])) for x in batch],device='cuda')
                mask=torch.tensor([[1]*len(x['ids'])+[0]*(length-len(x['ids'])) for x in batch],device='cuda')
                logits=model(input_ids=ids,attention_mask=mask,use_cache=False).logits
                for i,item in enumerate(batch):
                    target_labels=labels[i,1:];valid=target_labels!=-100
                    nll=float(F.cross_entropy(logits[i,:-1][valid].float(),target_labels[valid],reduction='mean'))
                    if not math.isfinite(nll):raise ValueError('Nonfinite evaluation loss')
                    scores[item['row']['id']][variant+'_nll']=nll
                del logits,ids,labels,mask
                if begin%400==0:print(json.dumps({'stage':variant,'scored':begin+len(batch),'total':len(encoded),'seconds':time.monotonic()-start}),flush=True)
        timings[variant]=time.monotonic()-phase
    samples=[]
    sample_rows=stable_sample([x['row'] for x in encoded if not x['truncated'] and not x['row'].get('source_has_images')],2 if pilot else 32)
    for row in sample_rows:
        if time.monotonic()-start>deadline_seconds:raise TimeoutError('Finite generation deadline')
        conversation=messages(row)[:-1];prompt=chat_prompt(tokenizer,conversation)
        inputs=tokenizer(prompt,return_tensors='pt',add_special_tokens=False).to('cuda');n=inputs['input_ids'].shape[1]
        if n>384:continue
        result={**row,'input_tokens':n}
        for variant in ['rhys','qwen_base']:
            phase=time.monotonic();context=model.disable_adapter() if variant=='qwen_base' else __import__('contextlib').nullcontext()
            with context,torch.inference_mode():output=model.generate(**inputs,max_new_tokens=64,do_sample=False,use_cache=True,eos_token_id=tokenizer.convert_tokens_to_ids('<|im_end|>'),pad_token_id=tokenizer.pad_token_id)
            text=tokenizer.decode(output[0,n:],skip_special_tokens=True);result[variant]={'text':text,**answer_metrics(text,row['reference']),'seconds':time.monotonic()-phase}
        samples.append(result)
    report={'status':'measured','pilot':pilot,'qa_sha256':manifest['qa_sha256'],'corpus_sha256':manifest['corpus_sha256'],'checkpoint_run_id':target['run_id'],'weights_sha256':target['weights_sha256'],'base_model_id':model_id,'base_revision':pins['models'][model_id]['revision'],'loading':loading,'input_records':len(rows),'scored_records':len(scores),'skipped':skipped,'timings':timings,'elapsed_seconds':time.monotonic()-start,'metrics':'Assistant-reference teacher-forced NLL for all eligible records; deterministic generation exact/F1 on a fixed cohort sample. Lower NLL is not answer accuracy.','generation_samples':len(samples),'max_sequence_length':512,'max_generation_tokens':64,'same_system_and_decoding':True}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    for filename,values in [('scores.jsonl',scores.values()),('samples.jsonl',samples)]:
        with(out/filename).open('w') as f:
            for row in values:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    return report
