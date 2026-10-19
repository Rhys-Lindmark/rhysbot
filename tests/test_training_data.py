import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_training import examples


class TrainingDataTests(unittest.TestCase):
    def test_standalone_note_is_pretraining_only_without_a_question_answer_pair(self):
        note = {'id': 'statement', 'source_type': 'anki', 'source_url': None,
                'authorship': 'rhys_curated_not_necessarily_authored',
                'text': 'Basalt is a common volcanic rock.', 'front': 'Basalt is a common volcanic rock.',
                'back': '', 'split': 'train', 'split_group_id': 'statement'}
        pre, sft = examples([note])
        self.assertEqual(len(pre), 1)
        self.assertEqual(sft, [])

    def test_guest_speech_never_becomes_rhys_sft(self):
        guest={'source_type':'youtube_transcript','id':'g','text':'Guest content','authorship':'mixed_or_unverified_speakers','source_url':'https://example.com','split':'test','split_group_id':'episode'}
        pre,sft=examples([guest])
        self.assertEqual((pre,sft),([],[]))

    def test_answer_and_split_preserved(self):
        note={'source_type':'anki','id':'a','text':'Question: What? Answer: Source answer.','front':'What?','back':'Source answer.','authorship':'curated','source_url':None,'split':'test','split_group_id':'held-out-note'}
        pre,sft=examples([note])
        self.assertEqual(sft[0]['messages'][-1]['content'],'Source answer.')
        self.assertEqual(sft[0]['split'],'test')
        self.assertEqual(pre[0]['split_group_id'],'held-out-note')
