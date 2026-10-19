import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from corpus_lib import CONTEXTUAL_RATIONALE, digest, privacy_flags
from import_anki_reviews import merge, RATIONALE


class ImportReviewTests(unittest.TestCase):
    def setup_shard(self, root, text='Question: What powers cells?\nAnswer: ATP transfers chemical energy.'):
        raw = {'id': 'anki-example', 'text': text, 'content_sha256': digest(text), 'privacy_flags': []}
        question, _, answer = text.partition('\nAnswer: ')
        raw.update(front=question.removeprefix('Question: ') if hasattr(question, 'removeprefix') else question[len('Question: '):],
                   back=answer, citation='')
        source = json.dumps(raw) + '\n'
        (root/'assigned.jsonl').write_text(source)
        (root/'assignment.json').write_text(json.dumps({'assigned_sha256': digest(source), 'records': 1,
                                                     'queue_start_inclusive': 0, 'queue_end_exclusive': 1}))
        decision = {'id': raw['id'], 'content_sha256': digest(text), 'decision': 'approve',
                    'reason': 'Private review reason must not be copied.', 'all_front_back_citation_read': True}
        (root/'decisions.jsonl').write_text(json.dumps(decision)+'\n')
        approval = {'id': raw['id'], 'content_sha256': digest(text), 'decision': 'approve',
                    'reviewer': 'codex-semantic-review', 'reviewed_at': '2026-10-18',
                    'scope': 'privacy_only_not_factual_validation', 'rationale': RATIONALE}
        (root/'approvals.jsonl').write_text(json.dumps(approval)+'\n')
        manifest = root/'manifest.jsonl'
        manifest.write_text('')
        return manifest

    def test_merge_is_idempotent_and_never_copies_private_reasons(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.setup_shard(root)
            self.assertEqual(merge(root, manifest)['new_approvals'], 1)
            self.assertEqual(merge(root, manifest)['new_approvals'], 0)
            self.assertNotIn('Private review reason', manifest.read_text())

    def test_rejects_incomplete_hash_changed_unread_or_extra_metadata(self):
        for fault in ('incomplete', 'source_hash', 'decision_hash', 'unread', 'private_metadata'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = self.setup_shard(root)
                if fault == 'source_hash':
                    (root/'assigned.jsonl').write_text((root/'assigned.jsonl').read_text()+' ')
                elif fault == 'private_metadata':
                    path = root/'approvals.jsonl'
                    value = json.loads(path.read_text()); value['private_reason'] = 'Do not publish'
                    path.write_text(json.dumps(value)+'\n')
                else:
                    path = root/'decisions.jsonl'
                    value = json.loads(path.read_text())
                    if fault == 'decision_hash': value['content_sha256'] = 'changed'
                    if fault == 'unread': value['all_front_back_citation_read'] = False
                    path.write_text('' if fault == 'incomplete' else json.dumps(value)+'\n')
                with self.assertRaises(ValueError): merge(root, manifest)
                self.assertEqual(manifest.read_text(), '')

    def test_withheld_decision_cannot_keep_an_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.setup_shard(root)
            path = root/'decisions.jsonl'
            value = json.loads(path.read_text()); value['decision'] = 'withhold'
            path.write_text(json.dumps(value)+'\n')
            with self.assertRaises(ValueError): merge(root, manifest)
            self.assertEqual(manifest.read_text(), '')

    def test_stale_empty_flags_cannot_bypass_current_privacy_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.setup_shard(root, 'Question: What does my family study?\nAnswer: Biology.')
            with self.assertRaisesRegex(ValueError, 'Flagged record'):
                merge(root, manifest)
            self.assertEqual(manifest.read_text(), '')

    def test_contextual_import_requires_review_scope_and_exact_flag_adjudication(self):
        for text, accepted in [('Question: Father of biogeography?\nAnswer: Wallace.', True),
                               ('Question: My password is synthetic.\nAnswer: Keep private.', False)]:
            with self.subTest(accepted=accepted), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = self.setup_shard(root, text)
                assignment = json.loads((root/'assignment.json').read_text())
                assignment['review_scope'] = 'reconsider_cached_flags'
                (root/'assignment.json').write_text(json.dumps(assignment))
                value = json.loads((root/'approvals.jsonl').read_text())
                value.update(contextual_privacy_review=True, supersedes_cached_flags=True,
                             approved_privacy_flags=privacy_flags(text, local=True), rationale=CONTEXTUAL_RATIONALE)
                (root/'approvals.jsonl').write_text(json.dumps(value)+'\n')
                if accepted:
                    self.assertEqual(merge(root, manifest)['new_approvals'], 1)
                else:
                    with self.assertRaises(ValueError): merge(root, manifest)
                    self.assertEqual(manifest.read_text(), '')
