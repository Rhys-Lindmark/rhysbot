"""Safety contracts for CPU ASR; fixtures never load a decoder or model."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('transcribe_podcast', SCRIPTS / 'transcribe_podcast.py')
asr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(asr)
BASE_REVISION = '3d3d5dee26484f91867d81cb899cfcf72b96be6c'
SMALL_REVISION = 'd1d751a5f8271d482d14ca55d9e2deeebbae577f'
SOURCE = 'https://soundcloud.com/the-rhys-show/test-episode'
WORDS = ' '.join('spokenword' + str(i) for i in range(30))


def segment(text=WORDS, start=0, end=20):
    return SimpleNamespace(start=start, end=end, text=text, avg_logprob=-0.2, no_speech_prob=0.01)


class TranscribePodcastSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.audio = self.root / 'fixture.mp3'
        self.audio.write_bytes(b'synthetic audio fixture; not decodable')
        self.model = self.root / BASE_REVISION
        self.model.mkdir()
        (self.model / 'model.bin').write_bytes(b'synthetic model fixture; not executable')
        self.output = self.root / 'private-output'
        self.stdout = io.StringIO()
        self.constructor = MagicMock(name='WhisperModel')
        self.constructor.return_value.transcribe.side_effect = lambda *a, **k: (
            iter([segment()]), SimpleNamespace(duration=60))
        fake_package = SimpleNamespace(WhisperModel=self.constructor)
        patches = [patch.dict(sys.modules, {'faster_whisper': fake_package}),
                   patch.object(asr.subprocess, 'check_output', return_value=b'{"format":{"duration":"60"}}'),
                   patch.object(asr.importlib.metadata, 'version', return_value='fixture-version'),
                   patch.object(asr, 'peak_mib', return_value=100),
                   contextlib.redirect_stdout(self.stdout)]
        self.mocks = []
        for manager in patches:
            self.mocks.append(manager.__enter__())
            self.addCleanup(manager.__exit__, None, None, None)
        self.probe = self.mocks[1]
        self.memory = self.mocks[3]

    def run_asr(self, **overrides):
        args = dict(audio=self.audio, model_dir=self.model, output=self.output,
                    source_url=SOURCE, title='Synthetic episode', published_at='2026-10-18')
        args.update(overrides)
        return asr.transcribe(**args)

    def assert_no_artifacts(self):
        self.assertFalse((self.output / 'candidate.jsonl').exists())
        self.assertFalse((self.output / 'report.json').exists())
        self.assertNotIn('spokenword', self.stdout.getvalue())

    def test_untrusted_sources_rejected_before_probe_or_model(self):
        urls = ['http://soundcloud.com/the-rhys-show/episode',
                'https://soundcloud.com.evil.example/the-rhys-show/episode',
                'https://soundcloud.com@evil.example/the-rhys-show/episode',
                'https://evil.example/the-rhys-show/episode',
                'https://soundcloud.com/another-show/episode',
                'https://soundcloud.com/the-rhys-show-impostor/episode',
                'file:///the-rhys-show/episode']
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.run_asr(source_url=url)
        self.probe.assert_not_called()
        self.constructor.assert_not_called()
        self.assert_no_artifacts()

    def test_url_credentials_and_nondefault_port_rejected_before_probe(self):
        urls = ['https://user:password@soundcloud.com/the-rhys-show/episode',
                'https://user@soundcloud.com/the-rhys-show/episode',
                'https://soundcloud.com:8443/the-rhys-show/episode']
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.run_asr(source_url=url)
        self.probe.assert_not_called()
        self.constructor.assert_not_called()
        self.assert_no_artifacts()
        self.assertNotIn('password', self.stdout.getvalue())

    def test_oversize_audio_rejected_without_probe_or_model(self):
        with patch.object(asr.Path, 'stat', return_value=SimpleNamespace(st_size=100 * 1024 ** 2 + 1)):
            with self.assertRaisesRegex(ValueError, '100 MiB'):
                self.run_asr()
        self.probe.assert_not_called()
        self.constructor.assert_not_called()
        self.assert_no_artifacts()

    def test_predecode_duration_rejects_nonfinite_zero_negative_and_over_limit(self):
        for duration in ['0', '-1', '7200.01', 'nan', 'inf']:
            with self.subTest(duration=duration):
                self.probe.return_value = json.dumps({'format': {'duration': duration}}).encode()
                with self.assertRaises(ValueError):
                    self.run_asr()
        self.constructor.assert_not_called()
        self.assert_no_artifacts()

    def test_failed_or_unusable_probe_never_starts_asr(self):
        for result in [b'not json', b'{}', b'{"format":{"duration":"unavailable"}}']:
            with self.subTest(probe=result):
                self.probe.return_value = result
                with self.assertRaises((ValueError, KeyError)):
                    self.run_asr()
        self.probe.side_effect = asr.subprocess.TimeoutExpired('ffprobe', 30)
        with self.assertRaises(asr.subprocess.TimeoutExpired):
            self.run_asr()
        self.constructor.assert_not_called()
        self.assert_no_artifacts()
        self.assertEqual(self.probe.call_args.kwargs['timeout'], 30)
        self.assertEqual(self.probe.call_args.args[0][0], 'ffprobe')

    def test_model_revision_and_memory_allowance_rejected_before_model_load(self):
        with self.assertRaisesRegex(ValueError, 'pinned'):
            self.run_asr(model_dir=self.root / 'unpinned-latest')
        for allowance in [0, 511, 2561, float('nan'), float('inf')]:
            with self.subTest(allowance=allowance), self.assertRaises(ValueError):
                self.run_asr(memory_limit_mib=allowance)
        self.constructor.assert_not_called()
        self.assert_no_artifacts()

    def test_model_size_cannot_use_another_revision_or_unknown_model(self):
        with self.assertRaisesRegex(ValueError, 'pinned'):
            self.run_asr(model_size='small.en')
        with self.assertRaises((ValueError, KeyError)):
            self.run_asr(model_size='unbounded-large-model')
        self.constructor.assert_not_called()
        self.assert_no_artifacts()
        small = self.root / SMALL_REVISION
        small.mkdir()
        (small / 'model.bin').write_bytes(b'synthetic small model fixture')
        self.run_asr(model_dir=small, model_size='small.en')
        report = json.loads((self.output / 'report.json').read_text())
        self.assertEqual(report['model'], 'Systran/faster-whisper-small.en')
        self.assertEqual(report['revision'], SMALL_REVISION)

    def test_public_output_and_symlink_to_public_output_rejected_before_model(self):
        public = SCRIPTS.parent / 'data' / 'test-asr-raw-output-must-not-exist'
        self.assertFalse(public.exists())
        link = self.root / 'public-link'
        link.symlink_to(public, target_is_directory=True)
        for destination in [public, link]:
            with self.subTest(destination=destination), self.assertRaisesRegex(ValueError, 'publication'):
                self.run_asr(output=destination)
        self.constructor.assert_not_called()
        self.assertFalse(public.exists())
        self.assert_no_artifacts()

    def test_decoder_duration_is_independently_checked_before_consuming_segments(self):
        consumed = []
        def stream():
            consumed.append(True)
            yield segment()
        for duration in [0, -1, 7201, float('nan'), float('inf')]:
            self.constructor.return_value.transcribe.side_effect = lambda *a, d=duration, **k: (
                stream(), SimpleNamespace(duration=d))
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                self.run_asr()
        self.assertEqual(consumed, [])
        self.assert_no_artifacts()

    def test_exceeded_runtime_memory_stops_stream_without_partial_outputs(self):
        consumed = []
        def stream():
            for i in range(3):
                consumed.append(i)
                yield segment()
        self.constructor.return_value.transcribe.side_effect = lambda *a, **k: (
            stream(), SimpleNamespace(duration=60))
        self.memory.side_effect = [100, 1537]
        with self.assertRaises(MemoryError):
            self.run_asr()
        self.assertEqual(consumed, [0, 1])
        self.assert_no_artifacts()

    def test_empty_or_fewer_than_thirty_words_never_claim_completion(self):
        for text in ['', ' \n ', ' '.join(['word'] * 29)]:
            self.constructor.return_value.transcribe.side_effect = lambda *a, t=text, **k: (
                iter([segment(t)]), SimpleNamespace(duration=60))
            with self.subTest(words=len(text.split())), self.assertRaisesRegex(ValueError, 'adequate speech'):
                self.run_asr()
            self.assert_no_artifacts()
        self.constructor.return_value.transcribe.side_effect = lambda *a, **k: (
            iter([]), SimpleNamespace(duration=60))
        with self.assertRaises(ValueError):
            self.run_asr()
        self.assert_no_artifacts()

    def test_thirty_words_complete_with_private_unverified_metadata_and_hashes(self):
        self.run_asr()
        candidate = json.loads((self.output / 'candidate.jsonl').read_text())
        report = json.loads((self.output / 'report.json').read_text())
        self.assertEqual(candidate['text'], WORDS)
        self.assertEqual(candidate['authorship'], 'mixed_or_unverified_speakers')
        self.assertEqual(candidate['source_group_id'], SOURCE)
        self.assertEqual(candidate['source_url'], SOURCE)
        self.assertEqual(candidate['source_type'], 'podcast_transcript')
        self.assertEqual(candidate['published_at'], '2026-10-18')
        self.assertEqual(report['status'], 'automatic_transcription_complete_not_audio_verified')
        acquisition = candidate['acquisition']
        self.assertFalse(acquisition['speaker_labels'])
        self.assertTrue(acquisition['visual_content_not_transcribed'])
        self.assertEqual(acquisition['coverage'], 'automatic_asr_not_audio_verified')
        self.assertNotIn('segments', acquisition)
        for key, path in [('audio_sha256', self.audio), ('model_bin_sha256', self.model / 'model.bin')]:
            self.assertEqual(report[key], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(report['revision'], BASE_REVISION)
        self.assertEqual(report['model'], 'Systran/faster-whisper-base.en')
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        self.assertNotIn(WORDS, self.stdout.getvalue())
        self.assertNotIn(str(self.root), self.stdout.getvalue())
        self.constructor.assert_called_once_with(str(self.model.resolve()), device='cpu', compute_type='int8',
                                                 cpu_threads=2, num_workers=1)
        options = self.constructor.return_value.transcribe.call_args.kwargs
        self.assertTrue(options['vad_filter'])
        self.assertFalse(options['condition_on_previous_text'])

    def test_known_youtube_identity_keeps_same_source_group_across_editions(self):
        groups, ids = [], []
        for i in range(2):
            out = self.root / ('edition-' + str(i))
            self.run_asr(output=out, source_url=SOURCE + str(i), youtube_id='abcdefghijk')
            row = json.loads((out / 'candidate.jsonl').read_text())
            groups.append(row['source_group_id'])
            ids.append(row['id'])
            self.assertEqual(row['acquisition']['corresponding_youtube_video_id'], 'abcdefghijk')
        self.assertEqual(groups, ['https://www.youtube.com/watch?v=abcdefghijk'] * 2)
        self.assertNotEqual(ids[0], ids[1])

    def test_progress_logs_never_include_raw_speech(self):
        self.constructor.return_value.transcribe.side_effect = lambda *a, **k: (
            iter([segment('privatefixtureword ' + str(i), i, i + 1) for i in range(25)]),
            SimpleNamespace(duration=25))
        self.run_asr()
        messages = [json.loads(line) for line in self.stdout.getvalue().splitlines()]
        self.assertEqual(messages[0]['segments'], 25)
        self.assertNotIn('privatefixtureword', self.stdout.getvalue())
        self.assertNotIn('text', messages[0])

    def test_duration_and_memory_inclusive_limits_accept_bounded_cpu_model(self):
        self.probe.return_value = b'{"format":{"duration":"7200"}}'
        self.constructor.return_value.transcribe.side_effect = lambda *a, **k: (
            iter([segment()]), SimpleNamespace(duration=7200))
        for allowance in [512, 2560]:
            self.memory.return_value = allowance
            self.run_asr(output=self.root / str(allowance), memory_limit_mib=allowance)
            self.assertTrue((self.root / str(allowance) / 'candidate.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
