import sys
import unittest
import contextlib
import io
import json
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from corpus_lib import digest
from build_corpus import approve_anki, deduplicate_and_split, build


class ReleaseGateTests(unittest.TestCase):
    def test_substack_latest_document_must_be_confirmed_full(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory)
            candidates = private/'public-source-candidates'
            candidates.mkdir()
            def row(identifier, coverage):
                return {'id':identifier,'source_type':'substack','source_url':'https://example.org/'+identifier,'title':'General ideas','text':'Public knowledge can make collective understanding stronger.','source_group_id':identifier,'acquisition':{'coverage':coverage}}
            records = [row('a','full_public_body'), row('a','restricted_or_preview'),
                       row('b','paywall_marker_review'), row('b','full_public_body')]
            (candidates/'substack.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
            with contextlib.redirect_stdout(io.StringIO()):
                released, stats = build(private, private/'output')
            self.assertEqual([r['document_id'] for r in released], ['b'])
            self.assertEqual(stats['source_processing']['substack']['input_documents'], 2)
            self.assertEqual(stats['source_processing']['substack']['withheld_not_full_public_body'], 1)

    def test_unreviewed_or_changed_anki_never_passes(self):
        row={'id':'a','text':'Question: What is ATP?\nAnswer: An energy carrier.','privacy_flags':[],
             'front':'What is ATP?', 'back':'An energy carrier.', 'citation':''}
        self.assertFalse(approve_anki(row,{}))
        approvals={'a':{'decision':'approve','content_sha256':digest(row['text'])}}
        self.assertTrue(approve_anki(row,approvals))
        row['text']+=' Additional private content.'
        self.assertFalse(approve_anki(row,approvals))

    def test_identical_documents_from_different_sources_group_before_split(self):
        rows=[{'id':str(i),'source_group_id':str(i),'source_type':'x','source_url':'https://example.com/'+str(i),'title':'Post','text':'Identical text in both sources.'} for i in range(2)]
        result,stats=deduplicate_and_split(rows)
        self.assertEqual(len(result),1)
        self.assertEqual(stats['exact_duplicates_removed'],1)

    def test_same_document_chunks_cannot_cross_splits(self):
        rows=[{'id':str(i),'source_group_id':'episode','source_type':'youtube_transcript','source_url':'https://example.com/episode','title':'The same podcast episode','text':'Distinct passage '+str(i)} for i in range(3)]
        result,_=deduplicate_and_split(rows)
        self.assertEqual(len({r['split'] for r in result}),1)
