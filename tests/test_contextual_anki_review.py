"""Context review must be explicit, content-bound, and unable to waive secrets."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from corpus_lib import CONTEXTUAL_RATIONALE, anki_release_approved, canonical_anki_text, digest, privacy_flags
import build_corpus
from validate_corpus import validate


def approval(row):
    return {'id': row['id'], 'content_sha256': digest(row['text']), 'decision': 'approve',
            'reviewer': 'codex-semantic-review', 'reviewed_at': '2026-10-18',
            'scope': 'privacy_only_not_factual_validation', 'rationale': CONTEXTUAL_RATIONALE,
            'contextual_privacy_review': True, 'approved_privacy_flags': privacy_flags(row['text'], local=True),
            'supersedes_cached_flags': True}


class ContextualAnkiTests(unittest.TestCase):
    def test_topic_flag_needs_exact_explicit_reading_adjudication(self):
        row = {'id': 'educational', 'text': 'Question: Father of biogeography?\nAnswer: Alfred Russel Wallace.',
               'privacy_flags': ['family'], 'front':'Father of biogeography?', 'back':'Alfred Russel Wallace.', 'citation':''}
        reviewed = approval(row)
        self.assertTrue(anki_release_approved(row, reviewed))
        for key, value in [('contextual_privacy_review', False), ('approved_privacy_flags', []),
                           ('rationale', 'A keyword scan was clean'), ('supersedes_cached_flags', False)]:
            with self.subTest(key=key):
                self.assertFalse(anki_release_approved(row, dict(reviewed, **{key: value})))
        self.assertFalse(anki_release_approved(dict(row, text=row['text'] + ' Changed source.'), reviewed))
        self.assertFalse(anki_release_approved(dict(row, back='A changed unreviewed answer.'), reviewed))

    def test_context_review_cannot_waive_contact_secret_or_private_marker_flags(self):
        for text in ['Call 202-555-0123.', 'Email sample@example.org.', 'My password is synthetic.',
                     'Confidential private notes.', 'Number: 123-45-6789.', '/Users/example/private.txt']:
            with self.subTest(text=text):
                row = {'id': 'hard-flag', 'front': text, 'back': '', 'citation': '',
                       'text': canonical_anki_text(text, '', ''), 'privacy_flags': []}
                self.assertFalse(anki_release_approved(row, approval(row)))

    def test_incomplete_card_retained_with_labels_but_not_silently_cleared(self):
        row = {'id': 'incomplete', 'source_type': 'anki', 'source_url': None,
               'title': 'Curated study note', 'published_at': None, 'source_group_id': 'incomplete',
               'front': 'Moon travel?', 'back': '', 'citation': '', 'text': 'Question: Moon travel?\nAnswer: ',
               'authorship': 'rhys_curated_not_necessarily_authored',
               'privacy_flags': ['image_dependent', 'incomplete_card']}
        reviewed = approval(row)
        self.assertFalse(anki_release_approved(row, {'decision': 'approve', 'content_sha256': digest(row['text'])}))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'local').mkdir()
            duplicate = dict(row, id='same-text-different-card', source_group_id='another-card')
            duplicate_approval = approval(duplicate)
            (root/'local/anki-candidates.jsonl').write_text(json.dumps(row)+'\n'+json.dumps(duplicate)+'\n')
            (root/'data').mkdir()
            (root/'data/anki-review-approvals.jsonl').write_text(json.dumps(reviewed)+'\n'+json.dumps(duplicate_approval)+'\n')
            original_read = build_corpus.read_jsonl
            def selected_read(path):
                return iter([reviewed, duplicate_approval]) if Path(path).name == 'anki-review-approvals.jsonl' else original_read(path)
            with patch.object(build_corpus, 'read_jsonl', side_effect=selected_read), contextlib.redirect_stdout(io.StringIO()):
                released, _ = build_corpus.build(root, root/'data/public')
                validate(root)
            self.assertEqual(len(released), 1)
            self.assertEqual(released[0]['text_completeness'], 'front_text_without_answer')
            self.assertTrue(released[0]['source_has_images'])
            self.assertTrue(released[0]['review']['contextual_privacy_review'])
            cards = [json.loads(line) for line in (root/'data/public/anki-cards.jsonl').read_text().splitlines()]
            self.assertEqual({card['id'] for card in cards}, {row['id'], duplicate['id']})
            self.assertEqual({card['corpus_record_id'] for card in cards}, {released[0]['id']})
            self.assertEqual(len({card['split_group_id'] for card in cards}), 1)
            (root/'data/public/anki-cards.jsonl').unlink()
            with self.assertRaisesRegex(AssertionError, 'registry missing'), contextlib.redirect_stdout(io.StringIO()):
                validate(root)

