import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'training'));sys.path.insert(0,str(ROOT/'scripts'))
from anki_evaluation import answer_metrics,stable_sample
from prepare_anki_eval import build


class AnkiEvaluationTests(unittest.TestCase):
    def test_metrics_are_explicit_string_checks_not_model_judgments(self):
        self.assertEqual(answer_metrics('The mitochondrion.','the mitochondrion')['normalized_exact_match'],True)
        self.assertEqual(answer_metrics('red blue','blue green')['token_f1'],.5)
        self.assertEqual(answer_metrics('','some answer')['token_f1'],0)

    def test_sample_is_stable_and_keeps_cohorts_separate(self):
        rows=[{'id':str(i),'frozen_sft_split':'train' if i<5 else 'test'} for i in range(10)]
        a=stable_sample(rows,2);self.assertEqual(a,stable_sample(list(reversed(rows)),2));self.assertEqual(len(a),4)
        self.assertEqual(sum(x['frozen_sft_split']=='test' for x in a),2)

    def test_inventory_uses_only_released_anki_and_preserves_frozen_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);train=root/'training';train.mkdir();(train/'manifest.json').write_text('{}')
            for split in ['train','validation','test']:
                row={'id':split,'task':'study_note_qa_not_verified_fact','messages':[{'role':'system','content':'Fixed system'}]}
                (train/f'sft-{split}.jsonl').write_text(json.dumps(row)+'\n')
            corpus=root/'corpus.jsonl';rows=[{'id':'train','source_type':'anki','front':'Question','back':'Answer','split':'test','split_group_id':'a'},
                {'id':'missing','source_type':'anki','front':'Prompt','back':'','split':'train','split_group_id':'b'},
                {'id':'other','source_type':'x','front':'Skip','back':'Skip'}]
            corpus.write_text('\n'.join(map(json.dumps,rows)));out=root/'eval';r=build(corpus,train,out)
            self.assertEqual(r['counts'],{'all_released_records':2,'eligible_qa':1,'missing_reference':1})
            item=json.loads((out/'anki.jsonl').read_text());self.assertEqual(item['frozen_sft_split'],'train');self.assertEqual(item['source_split'],'test')


if __name__=='__main__':unittest.main()
