"""Pure speech accounting/alignment checks; no Modal or model download."""
import ast
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'training'))
from budget import initial_ledger, committed, reserve
from runtime import safe_run_id, file_sha

tree = ast.parse((ROOT / 'training/speech.py').read_text())
functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {
    'align_words', 'validate_items', 'manifest_sha', 'reserve_corpus', 'choose_rhys', 'corpus_lane',
    'voice_intervals','checked_staged_audio'}]
namespace = dict(hashlib=hashlib, json=json, safe_run_id=safe_run_id, reserve=reserve, Path=Path, file_sha=file_sha)
exec(compile(ast.Module(body=functions, type_ignores=[]), 'speech-pure', 'exec'), namespace)
ITEMS = [{'id': 'episode-one', 'enclosure_url': 'https://feeds.soundcloud.com/stream/123-show.mp3'}]


class SpeechTests(unittest.TestCase):
    def test_overlap_and_missing_diarization_stay_unknown(self):
        turns, unknown = namespace['align_words']([
            {'start': 0, 'end': 1, 'word': 'Hello'}, {'start': 1, 'end': 2, 'word': ' yes'},
            {'start': 3, 'end': 4, 'word': ' goodbye'}], [
            {'Start': 0, 'End': 2, 'Speaker': 0}, {'Start': 1, 'End': 2, 'Speaker': 1}])
        self.assertEqual([t['speaker_id'] for t in turns], ['speaker_0', 'unknown'])
        self.assertEqual(unknown, 2)

    def test_corpus_lane_is_fully_charged_but_does_not_block_inference_accounting(self):
        ledger = initial_ledger()
        ledger['pilot_passed']['qwen'] = 'passed'
        config = json.loads((ROOT / 'training/config.json').read_text())['budget']
        amount = namespace['reserve_corpus'](ledger, 'speech-pilot-one', ITEMS, 900, config)
        self.assertAlmostEqual(amount, 3.60)
        self.assertEqual(ledger['jobs']['speech-pilot-one']['status'], 'running_reserved')
        reserve(ledger, 'serve-one', 'qwen', 'eval', 180, config)
        self.assertAlmostEqual(committed(ledger), 4.80)
        ledger['initial_spent_usd'] = 196
        ledger['jobs']['serve-one']['status'] = 'completed_pending_billing'
        with self.assertRaisesRegex(ValueError, 'cap'):
            namespace['reserve_corpus'](ledger, 'speech-pilot-two', ITEMS, 900, config)

    def test_untrusted_audio_and_duplicates_rejected(self):
        for items in ([], ITEMS * 2, [{**ITEMS[0], 'enclosure_url': 'http://localhost:80/secret'}]):
            with self.assertRaises(ValueError):
                namespace['validate_items'](items)
        video = {'id': 'youtube-one', 'youtube_id': 'g2BAKx2Rr-E',
                 'youtube_url': 'https://www.youtube.com/watch?v=g2BAKx2Rr-E',
                 'enclosure_url': None, 'audio_source_kind': 'youtube_audio'}
        namespace['validate_items']([video])
        for changed in ({'youtube_url': 'https://www.youtube.com.evil.invalid/watch?v=g2BAKx2Rr-E'},
                        {'youtube_id': '../private'}, {'enclosure_url': 'https://private.invalid'}):
            with self.assertRaises(ValueError):
                namespace['validate_items']([{**video, **changed}])

    def test_voice_identity_requires_multiple_consistent_clips_and_margin(self):
        choose = namespace['choose_rhys']
        self.assertEqual(choose({'speaker_0': [.95]*4, 'speaker_1': [.65]*4}), 'speaker_0')
        for profiles in ({'speaker_0': [.95]*3}, {'speaker_0': [.85]*8},
                         {'speaker_0': [.95]*4, 'speaker_1': [.90]*4},
                         {'speaker_0': [.95,.95,.95,.50]}):
            self.assertIsNone(choose(profiles))

    def test_source_lanes_cannot_bypass_shared_cap_or_accept_mixed_batches(self):
        video = {'id': 'video-one', 'audio_source_kind': 'youtube_audio', 'youtube_id': 'g2BAKx2Rr-E',
                 'youtube_url': 'https://www.youtube.com/watch?v=g2BAKx2Rr-E', 'enclosure_url': None}
        self.assertNotEqual(namespace['corpus_lane'](ITEMS), namespace['corpus_lane']([video]))
        with self.assertRaises(ValueError):
            namespace['corpus_lane'](ITEMS+[video])
        ledger=initial_ledger();ledger['pilot_passed']['qwen']='passed'
        config=json.loads((ROOT/'training/config.json').read_text())['budget']
        namespace['reserve_corpus'](ledger,'speech-rss',ITEMS,900,config)
        namespace['reserve_corpus'](ledger,'speech-video',[video],3600,config)
        self.assertAlmostEqual(committed(ledger),16.20)
        ledger['initial_spent_usd']=184
        with self.assertRaisesRegex(ValueError,'cap'):
            namespace['reserve_corpus'](ledger,'speech-extra',[video],900,config)

    def test_long_monologue_supplies_multiple_clean_voice_clips_but_overlap_does_not(self):
        sample=namespace['voice_intervals']
        clips=sample([{'Start':0,'End':120,'Speaker':0}],0)
        self.assertEqual(len(clips),8)
        self.assertTrue(all(4<=b-a<=8 for a,b in clips))
        self.assertTrue(all(clips[i][1]<clips[i+1][0] for i in range(len(clips)-1)))
        self.assertEqual(sample([{'Start':0,'End':20,'Speaker':0},{'Start':3,'End':5,'Speaker':1}],0),[])

    def test_staged_audio_rejects_corrupt_bytes_paths_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'audio-inputs/speech-video/episode-one.m4a'
            path.parent.mkdir(parents=True);path.write_bytes(b'public audio fixture')
            staged={'relative_path':str(path.relative_to(root)),'sha256':file_sha(path),'bytes':path.stat().st_size}
            item={'id':'episode-one','staged_audio':staged}
            self.assertEqual(namespace['checked_staged_audio'](item,'speech-video',root),path)
            for changes in ({'relative_path':'../checkpoints/model.pt'},{'bytes':1},{'sha256':'0'*64}):
                with self.assertRaises(ValueError):
                    namespace['checked_staged_audio']({**item,'staged_audio':{**staged,**changes}},'speech-video',root)
            target=path.with_name('other.m4a');path.rename(target);path.symlink_to(target)
            with self.assertRaises(ValueError):
                namespace['checked_staged_audio'](item,'speech-video',root)
