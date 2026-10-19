"""Pure speech accounting/alignment checks; no Modal or model download."""
import ast
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'training'))
from budget import initial_ledger, committed, reserve
from runtime import safe_run_id

tree = ast.parse((ROOT / 'training/speech.py').read_text())
functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {
    'align_words', 'validate_items', 'manifest_sha', 'reserve_corpus'}]
namespace = dict(hashlib=hashlib, json=json, safe_run_id=safe_run_id, reserve=reserve)
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
