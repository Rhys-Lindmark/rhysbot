import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from summarize_anki_eval import summarize


class SummaryTests(unittest.TestCase):
    def fixture(self, root):
        cards = [{'id': str(i), 'frozen_sft_split': 'test', 'qa_sha256': str(i)*64, 'question': 'Q', 'reference': 'A'} for i in range(3)]
        raw = ''.join(json.dumps(row)+'\n' for row in cards).encode()
        (root/'anki.jsonl').write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        (root/'manifest.json').write_text(json.dumps({'qa_sha256': digest, 'corpus_sha256': 'a'*64, 'limitations': ['Reference agreement is not correctness.']}))
        report = {'status': 'measured', 'pilot': False, 'qa_sha256': digest, 'corpus_sha256': 'a'*64, 'input_records': 3, 'scored_records': 2, 'skipped': [{'id': '2', 'reason': 'context'}], 'generation_samples': 1}
        (root/'report.json').write_text(json.dumps(report))
        rows = [{'id': str(i), 'frozen_sft_split': 'test', 'target_tokens': n, 'full_target_tokens': n, 'truncated': False, 'rhys_nll': r, 'qwen_base_nll': q} for i,n,r,q in [(0,1,1,2),(1,9,3,2)]]
        (root/'scores.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
        sample = {**cards[0], 'rhys': {'normalized_exact_match': True, 'token_f1': 1}, 'qwen_base': {'normalized_exact_match': False, 'token_f1': .5}}
        (root/'samples.jsonl').write_text(json.dumps(sample)+'\n')
        return report, rows, sample

    def test_token_weighted_loss_and_counts_are_separate_from_sample_agreement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);self.fixture(root)
            result = summarize(root, root)
            self.assertAlmostEqual(result['cohorts']['test']['rhys_nll'], 2.8)
            self.assertEqual(result['cohorts']['test']['rhys_lower_loss'], 1)
            self.assertEqual(result['cohorts']['test']['base_lower_loss'], 1)
            self.assertEqual(result['generation']['qwen_base']['token_f1'], .5)
            self.assertEqual(result['generation']['by_cohort']['test']['count'], 1)
            self.assertIsNone(result['generation']['by_cohort']['train']['rhys']['exact_match'])
            self.assertIsNone(result['cohorts']['train']['rhys_nll'])

    def test_rejects_silent_omission_duplicate_score_and_wrong_sample_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for variant in ['omission', 'duplicate', 'sample']:
                report, rows, sample = self.fixture(root)
                if variant == 'omission':
                    report.update(input_records=2, skipped=[]);(root/'report.json').write_text(json.dumps(report))
                elif variant == 'duplicate':
                    rows[1] = copy.deepcopy(rows[0]);(root/'scores.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
                else:
                    sample['reference'] = 'Changed';(root/'samples.jsonl').write_text(json.dumps(sample)+'\n')
                with self.assertRaises(ValueError):summarize(root, root)


if __name__ == '__main__':unittest.main()
