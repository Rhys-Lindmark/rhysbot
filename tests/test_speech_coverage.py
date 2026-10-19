import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from speech_coverage import coverage


class CoverageTests(unittest.TestCase):
    def test_release_is_established_by_public_sidecar_and_private_fields_never_escape(self):
        rss = {'episodes': [{'id': 'episode-one', 'source_url': 'https://soundcloud.com/the-rhys-show/one',
                             'rss_duration_seconds': 123, 'private_note': 'PRIVATE_MARKER'}]}
        reports = [{'episodes': [{'id': 'episode-one', 'status': 'completed', 'voiceprint': 'PRIVATE_MARKER'}]}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'speaker-transcripts').mkdir()
            result = coverage(rss, [], root, reports)
            self.assertEqual(result['recordings'][0]['status'], 'processed_but_not_released')
            sidecar = {'source_url': rss['episodes'][0]['source_url'], 'audio_source_kind': 'rss_audio',
                       'rhys_speaker_id': None, 'audio_duration_seconds': 123.4,
                       'turn_counts': {'total': 2, 'published': 1, 'withheld': 1}, 'voiceprint': 'PRIVATE_MARKER'}
            (root / 'speaker-transcripts/episode-one.json').write_text(json.dumps(sidecar))
            result = coverage(rss, [], root, reports)
            self.assertEqual(result['summary']['released_rhys_matches'], 0)
            self.assertEqual(result['recordings'][0]['status'], 'released_speaker_transcript')
            self.assertNotIn('PRIVATE_MARKER', json.dumps(result))
            sidecar['source_url'] = 'https://soundcloud.com/the-rhys-show/other'
            (root / 'speaker-transcripts/episode-one.json').write_text(json.dumps(sidecar))
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                coverage(rss, [], root, reports)

    def test_gaps_and_youtube_only_inventory_are_explicit(self):
        rss = {'episodes': [{'id': 'episode-one', 'source_url': 'https://soundcloud.com/the-rhys-show/one'}]}
        reports = [{'episodes': [{'id': 'episode-one', 'status': 'failed_no_retry', 'error': 'PRIVATE_MARKER'}]}]
        with tempfile.TemporaryDirectory() as directory:
            result = coverage(rss, [{'id': 'g2BAKx2Rr-E', 'duration': 100}], directory, reports)
            self.assertEqual(result['summary']['statuses'], {'not_completed': 1, 'processing_failed': 1})
            self.assertNotIn('PRIVATE_MARKER', json.dumps(result))
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                coverage(rss, [{'id': 'g2BAKx2Rr-E'}] * 2, directory)
            for changed in ({'id': '../private'}, {'source_url': 'https://soundcloud.com/the-rhys-show/one?token=private'},
                            {'source_url': 'https://private.example/recording'}, {'rss_duration_seconds': {'private': 'value'}}):
                with self.assertRaises(ValueError):
                    coverage({'episodes': [{**rss['episodes'][0], **changed}]}, [], directory)
