import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('podcast_feed', Path(__file__).resolve().parents[1] / 'scripts/ingest_podcast_feed.py')
feed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feed)

FIRST, SECOND = 'abcdefghijk', '12345678901'


def rss(title='An episode', description='', duration='01:02:03'):
    return ('''<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>
      <title>Show</title><link>https://example.com/</link><itunes:author>Author</itunes:author>
      <item><guid>episode-1</guid><title>''' + title + '''</title>
      <link>https://example.com/episode</link><pubDate>Mon, 26 Jun 2023 10:31:01 +0000</pubDate>
      <enclosure url="https://example.com/audio.mp3" type="audio/mpeg" length="1000"/>
      <itunes:duration>''' + duration + '''</itunes:duration><description><![CDATA[''' + description + ''']]></description>
      </item></channel></rss>''').encode()


def video(identifier=FIRST, title='An episode', duration=3724):
    return {'id': identifier, 'title': title, 'duration': duration}


class PodcastFeedTests(unittest.TestCase):
    def test_metadata_provenance_without_description_or_supporters(self):
        metadata, episodes = feed.parse_feed(rss(description='Supporter names do not belong in coverage'))
        row = episodes[0]
        self.assertEqual(metadata['author'], 'Author')
        self.assertEqual(row['rss_guid'], 'episode-1')
        self.assertEqual(row['published_at'], '2023-06-26T10:31:01Z')
        self.assertEqual(row['rss_duration_seconds'], 3723)
        self.assertEqual(row['enclosure_length_bytes'], 1000)
        self.assertNotIn('Supporter', json.dumps(row))
        self.assertEqual(row['canonical_url'], 'https://example.com/episode')

    def test_bounded_xml_and_no_entity_or_external_resolution(self):
        for raw in [b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss/>',
                    b'<!ENTITY a "abc">', b'<rss>\x00</rss>', b' ' * (feed.MAX_BYTES + 1)]:
            with self.assertRaises(ValueError):
                feed.parse_feed(raw)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'large'
            path.write_bytes(b'x' * (feed.MAX_BYTES + 1))
            with self.assertRaises(ValueError):
                feed.bounded_read(path)

    def test_duration_validation(self):
        self.assertEqual(feed.duration_seconds('59:59'), 3599)
        self.assertEqual(feed.duration_seconds('198'), 198)
        self.assertIsNone(feed.duration_seconds(None))
        for bad in ['1:99', '-1', float('inf'), True, '1:2:3:4']:
            with self.assertRaises(ValueError):
                feed.duration_seconds(bad)

    def test_unique_exact_normalization_and_edition_limitation(self):
        metadata, episodes = feed.parse_feed(rss(title='César: A &amp; B'))
        report = feed.build_coverage(metadata, episodes, [video(title='Cesar A B')],
                                     [{'video_id': FIRST, 'status': 'caption_acquired', 'caption_type': 'automatic_captions'}])
        row = report['episodes'][0]
        self.assertEqual(row['youtube_match_method'], 'unique_exact_normalized_title')
        self.assertEqual(row['youtube_minus_rss_duration_seconds'], 1)
        self.assertEqual(row['status'], 'matched_youtube_caption_available')
        self.assertEqual(report['summary']['rss_audio_transcriptions_verified'], 0)
        self.assertIn('YouTube edition', row['edition_limitation'])

    def test_duplicate_titles_are_ambiguous_not_first_match(self):
        metadata, episodes = feed.parse_feed(rss())
        report = feed.build_coverage(metadata, episodes, [video(), video(SECOND)], [])
        self.assertEqual(report['episodes'][0]['status'], 'ambiguous_youtube_identity_gap')
        self.assertIsNone(report['episodes'][0]['youtube_id'])

    def test_explicit_id_disambiguates_only_agreeing_exact_titles(self):
        metadata, episodes = feed.parse_feed(rss(description='https://youtu.be/' + SECOND))
        report = feed.build_coverage(metadata, episodes, [video(), video(SECOND)], [])
        self.assertEqual(report['episodes'][0]['youtube_id'], SECOND)
        self.assertEqual(report['episodes'][0]['youtube_match_method'], 'explicit_youtube_id_disambiguates_exact_title')
        metadata, episodes = feed.parse_feed(rss(description='Mentioned resource https://youtu.be/' + SECOND))
        report = feed.build_coverage(metadata, episodes, [video(SECOND, 'Unrelated referenced talk')], [])
        self.assertIsNone(report['episodes'][0]['youtube_id'])
        self.assertEqual(report['episodes'][0]['unverified_youtube_reference_ids'], [SECOND])

    def test_youtube_url_parser_rejects_lookalike_domains(self):
        self.assertEqual(feed.explicit_youtube_ids('https://youtube.com.evil.example/watch?v=' + FIRST), [])
        self.assertEqual(feed.explicit_youtube_ids('https://www.youtube.com/watch?v=' + FIRST + '&amp;t=1'), [FIRST])

    def test_reviewed_alias_is_pinned_to_titles_and_durations(self):
        metadata, episodes = feed.parse_feed(rss())
        videos = [video(title='Guest discussion')]
        alias = {'rss_guid': 'episode-1', 'rss_title': 'An episode', 'youtube_id': FIRST,
                 'youtube_title': 'Guest discussion', 'rss_duration_seconds': 3723,
                 'youtube_duration_seconds': 3724, 'decision': 'same_episode_identity',
                 'reviewer': 'codex-source-identity-review', 'reviewed_at': '2026-10-18',
                 'evidence': 'Read both guest/topic titles and compared durations.'}
        report = feed.build_coverage(metadata, episodes, videos, [], [alias])
        self.assertEqual(report['episodes'][0]['youtube_match_method'], 'manually_reviewed_episode_identity_alias')
        with self.assertRaises(ValueError):
            feed.build_coverage(metadata, episodes, [video(title='Changed title')], [], [alias])
        with self.assertRaises(ValueError):
            feed.build_coverage(metadata, episodes, [video(title='Guest discussion', duration=3800)], [], [alias])
        with self.assertRaises(ValueError):
            feed.build_coverage(metadata, episodes, videos, [], [alias, alias])

    def test_failure_and_rss_only_remain_gaps_no_asr_claims(self):
        metadata, episodes = feed.parse_feed(rss())
        report = feed.build_coverage(metadata, episodes, [video()],
                                     [{'video_id': FIRST, 'status': 'failed', 'error': 'HTTP 429'}])
        self.assertEqual(report['episodes'][0]['youtube_caption_status'], 'failed')
        self.assertEqual(report['episodes'][0]['status'], 'matched_youtube_transcript_gap')
        self.assertIsNone(report['episodes'][0]['transcript_source_url'])
        report = feed.build_coverage(metadata, episodes, [], [])
        self.assertEqual(report['episodes'][0]['status'], 'no_youtube_match_transcript_gap')
        self.assertEqual(report['episodes'][0]['rss_audio_transcription_status'], 'not_established_by_this_inventory')

    def test_duplicate_guids_and_duplicate_video_ids_rejected(self):
        raw = rss()
        item = raw[raw.index(b'<item>'):raw.index(b'</item>') + 7]
        with self.assertRaises(ValueError):
            feed.parse_feed(raw.replace(b'</channel>', item + b'</channel>'))
        with self.assertRaises(ValueError):
            feed.index_youtube([video(), video()])

    def test_private_output_guard(self):
        with self.assertRaises(ValueError):
            feed.private_output(Path(__file__).resolve().parents[1] / 'data/public-coverage.json')


if __name__ == '__main__':
    unittest.main()
