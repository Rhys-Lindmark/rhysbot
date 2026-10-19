"""Podcast release integration: private input -> screened/grouped research text."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_corpus import build
from prepare_training import examples


def podcast(identifier, **changes):
    row = {'id': identifier, 'source_type': 'podcast_transcript',
           'source_url': 'https://soundcloud.com/the-rhys-show/' + identifier,
           'title': 'A discussion of shared knowledge', 'published_at': '2022-01-01T00:00:00Z',
           'text': 'Public educational ideas can support collective understanding and scientific research.',
           'acquisition': {'method': 'public_rss_audio_cpu_asr',
                           'coverage': 'automatic_asr_not_audio_verified', 'speaker_labels': False}}
    row.update(changes)
    return row


class PodcastReleaseTests(unittest.TestCase):
    def release(self, directory, podcasts, youtube=()):
        private = Path(directory)
        candidates = private / 'public-source-candidates'
        candidates.mkdir(exist_ok=True)
        for name, rows in [('podcasts.jsonl', podcasts), ('youtube.jsonl', youtube)]:
            (candidates / name).write_text(''.join(json.dumps(row) + '\n' for row in rows))
        with contextlib.redirect_stdout(io.StringIO()):
            return build(private, private / 'output')

    def test_rss_only_defaults_mixed_authorship_and_preserves_asr_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            released, stats = self.release(directory, [podcast('rss-only')])
            self.assertEqual(len(released), 1)
            row = released[0]
            self.assertEqual(row['authorship'], 'mixed_or_unverified_speakers')
            self.assertEqual(row['source_group_id'], row['source_url'])
            self.assertEqual(row['privacy_status'], 'public_source_automated_screen')
            self.assertEqual(row['acquisition']['coverage'], 'automatic_asr_not_audio_verified')
            self.assertEqual(row['source_type'], 'podcast_transcript')
            self.assertEqual(stats['by_source']['podcast_transcript'], 1)
            self.assertEqual(examples(released), ([], []))

    def test_explicit_youtube_identity_groups_differently_titled_editions(self):
        youtube_url = 'https://www.youtube.com/watch?v=abcdefghijk'
        audio = podcast('rss-version', text=' '.join(['alpha'] * 350 + ['beta'] * 350),
                        source_group_id='https://soundcloud.com/the-rhys-show/rss-version',
                        acquisition={'method': 'public_rss_audio_cpu_asr',
                                     'coverage': 'automatic_asr_not_audio_verified',
                                     'corresponding_youtube_video_id': 'abcdefghijk'})
        video = {'id': 'yt-version', 'source_type': 'youtube_transcript',
                 'source_url': youtube_url, 'source_group_id': youtube_url,
                 'title': 'A substantially different video edition title',
                 'text': 'The video edition contains additional discussion of institutions and technology.',
                 'authorship': 'mixed_or_unverified_speakers'}
        with tempfile.TemporaryDirectory() as directory:
            released, _ = self.release(directory, [audio], [video])
            self.assertEqual(len(released), 3)
            self.assertEqual({row['source_group_id'] for row in released}, {youtube_url})
            self.assertEqual(len({row['split_group_id'] for row in released}), 1)
            self.assertEqual(len({row['split'] for row in released}), 1)

    def test_same_privacy_gates_and_latest_version_apply_to_podcasts(self):
        safe = podcast('screened', text=' '.join(['knowledge'] * 350)
                       + ' Contact reader@example.org for additional information.')
        flagged_title = podcast('title-flag', title='Family reflections')
        truncated = podcast('truncated', privacy_flags=['truncated_post'])
        old = podcast('changed')
        latest = podcast('changed', title='Family reflections')
        with tempfile.TemporaryDirectory() as directory:
            released, stats = self.release(directory, [safe, flagged_title, truncated, old, latest])
            self.assertEqual([row['document_id'] for row in released], ['screened'])
            self.assertNotIn('reader@example.org', released[0]['text'])
            source = stats['source_processing']['podcast_transcript']
            self.assertEqual(source['input_documents'], 4)
            self.assertEqual(source['withheld_title_flagged'], 2)
            self.assertEqual(source['withheld_truncated'], 1)
            self.assertEqual(source['withheld_flagged_excerpts'], 1)

    def test_audio_type_excluded_from_default_training_despite_authorship_claim(self):
        # Source allowlisting prevents mixed/uncertain speech entering training,
        # even when an upstream candidate has an overly confident author label.
        for authorship in ('mixed_or_unverified_speakers', 'rhys_authored'):
            row = podcast('guest', authorship=authorship, split='test', split_group_id='episode')
            self.assertEqual(examples([row]), ([], []))

    def test_invalid_explicit_video_identity_fails_closed(self):
        audio = podcast('bad', acquisition={'corresponding_youtube_video_id': 'invalid/video/id'})
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'YouTube video ID'):
                self.release(directory, [audio])


if __name__ == '__main__':
    unittest.main()
