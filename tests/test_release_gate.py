import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from corpus_lib import digest
from build_corpus import approve_anki, deduplicate_and_split


class ReleaseGateTests(unittest.TestCase):
    def test_unreviewed_or_changed_anki_never_passes(self):
        row={'id':'a','text':'Question: What is ATP?\nAnswer: An energy carrier.','privacy_flags':[]}
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
