"""Build an evaluation inventory from released cards only; never raw Anki."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def records(path):
    with Path(path).open() as f:
        for line in f:
            if line.strip():yield json.loads(line)


def build(corpus,training,out):
    frozen={};system=None
    for split in ['train','validation','test']:
        for row in records(training/f'sft-{split}.jsonl'):
            if row['task']=='study_note_qa_not_verified_fact':frozen[row['id']]=split
            if system is None:system=row['messages'][0]['content']
    out.mkdir(parents=True,exist_ok=True)
    counts=Counter();cohorts=Counter();seen=set()
    with (out/'anki.jsonl').open('w') as target,(out/'anki-skipped.jsonl').open('w') as skipped:
        for row in records(corpus):
            if row['source_type']!='anki':continue
            counts['all_released_records']+=1
            question=row.get('front','').strip();reference=row.get('back','').strip()
            reason='missing_question' if not question else 'missing_reference' if not reference else None
            pair=hashlib.sha256((question+'\0'+reference).encode()).hexdigest()
            if not reason and pair in seen:reason='duplicate_qa'
            if reason:
                counts[reason]+=1;skipped.write(json.dumps({'id':row['id'],'reason':reason})+'\n');continue
            seen.add(pair);cohort=frozen.get(row['id'],'not_in_frozen_sft');cohorts[cohort]+=1;counts['eligible_qa']+=1
            target.write(json.dumps({'id':row['id'],'question':question,'reference':reference,
                'source_has_images':bool(row.get('source_has_images')),'source_split':row['split'],'frozen_sft_split':cohort,'source_group_id':row['split_group_id'],
                'qa_sha256':pair},ensure_ascii=False)+'\n')
    manifest={'counts':dict(counts),'cohorts':dict(cohorts),'system':system,
        'corpus_sha256':hashlib.sha256(corpus.read_bytes()).hexdigest(),
        'qa_sha256':hashlib.sha256((out/'anki.jsonl').read_bytes()).hexdigest(),
        'training_manifest_sha256':hashlib.sha256((training/'manifest.json').read_bytes()).hexdigest(),
        'question_prefix':'Answer this question from a curated study note: ',
        'limitations':['Only released records are used. No excluded/private cards are recovered.',
            'Training split membership is not proof that an optimizer consumed the exact example.',
            'Cards are reference answers, not independently verified facts. Missing answers are not invented.',
            'Loss, exact string match and token overlap are separate metrics; none establishes persona fidelity.',
            'This is a text-only benchmark. Cards marked source_has_images may lack required visual context; generation samples exclude them.']}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--corpus',type=Path,default=ROOT/'data/public/corpus.jsonl');p.add_argument('--training',type=Path,default=ROOT/'data/training');p.add_argument('--out',type=Path,default=ROOT/'data/evaluation');a=p.parse_args();print(json.dumps(build(a.corpus,a.training,a.out),indent=2))
