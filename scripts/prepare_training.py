"""Derive training examples solely from the released corpus, preserving splits."""
import argparse
import json
from collections import Counter
from pathlib import Path
from corpus_lib import digest, read_jsonl, write_jsonl


SYSTEM = ('You are Rhysbot, an experimental assistant based on Rhys Lindmark\'s '
          'public writing and curated study notes. Distinguish quotations and '
          'guest views from Rhys\'s views. Do not claim to be Rhys or invent private facts.')


def examples(rows):
    pretraining, sft = [], []
    for row in rows:
        # Mixed podcast/HEE captions remain available for research but are not
        # silently treated as Rhys's voice in this first training recipe.
        if row['source_type'] not in ('anki','x','substack','linked_writing'):
            continue
        if row['authorship']=='rhys_public_reply_parent_context_not_included':
            continue
        if row['source_type'] == 'anki' and not (row.get('front', '').strip() or row.get('back', '').strip()):
            continue
        origin = row['source_url'] or 'curated study note; not necessarily original'
        text = ('Source: '+origin+'\nAuthorship: '+row['authorship']+'\n\n'+row['text'])
        pretraining.append({'id':row['id'],'text':text,'split':row['split'],'split_group_id':row['split_group_id']})
        task = None
        if row['source_type']=='anki' and row.get('front', '').strip() and row.get('back', '').strip():
            prompt = 'Answer this question from a curated study note: '+row['front']
            answer = row['back']
            task = 'study_note_qa_not_verified_fact'
        elif row['source_type'] in ('substack','linked_writing') and len(row['text'].split())>=100:
            words = row['text'].split()
            cut = min(70, len(words)//3)
            prompt = ('Continue this passage from the published piece "'+row['title']+'":\n\n'+' '.join(words[:cut]))
            answer = ' '.join(words[cut:])
            task = 'sourced_passage_continuation'
        if task:
            sft.append({'id':row['id'],'split':row['split'],'split_group_id':row['split_group_id'],
                        'source_url':row['source_url'],'task':task,'answer_is_verbatim_source':not bool(row.get('privacy_redaction')),
                        'messages':[{'role':'system','content':SYSTEM},{'role':'user','content':prompt},{'role':'assistant','content':answer}]})
    return pretraining,sft


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--corpus',type=Path,default=Path(__file__).resolve().parents[1]/'data/public/corpus.jsonl')
    p.add_argument('--out',type=Path,default=Path(__file__).resolve().parents[1]/'data/training')
    args=p.parse_args()
    rows=list(read_jsonl(args.corpus))
    pretraining,sft=examples(rows)
    for kind,data in [('pretrain',pretraining),('sft',sft)]:
        for split in ('train','validation','test'):
            write_jsonl(args.out/(kind+'-'+split+'.jsonl'),(r for r in data if r['split']==split))
    manifest={'source_corpus_sha256':digest(args.corpus.read_text()),'pretraining_records':len(pretraining),
              'sft_records':len(sft),'sft_tasks':dict(Counter(r['task'] for r in sft)),
              'splits':{kind:dict(Counter(r['split'] for r in data)) for kind,data in [('pretrain',pretraining),('sft',sft)]},
              'excluded_from_default_training':['mixed_or_unverified_speakers','rhys_hosted_speaker_attribution_unverified'],
              'limitations':['Small seed SFT set, not a validated Rhys personality dataset.','Study cards are not fact checked.','Continuation examples are verbatim public text, not invented question answers.','Test answers are public; do not tune on them.','Freeze the Git release commit for every run.']}
    (args.out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':
    main()
