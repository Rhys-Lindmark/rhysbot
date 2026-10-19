"""Additive collection labels, exact byte accounting and narrow writing review."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_corpus import build, collection, public_excerpt_review
from corpus_lib import digest, privacy_flags
from ingest_ai2026 import extract_snapshot, upsert
from refresh_research_corpus import refresh


class CorpusCollectionTests(unittest.TestCase):
    def test_collections_preserve_distinct_historical_source_types(self):
        expected = {'substack': 'writing', 'linked_writing': 'writing',
                    'youtube_transcript': 'podcasts_youtube', 'podcast_transcript': 'podcasts_youtube',
                    'hee_transcript': 'hee', 'anki': 'anki', 'x': 'x'}
        self.assertEqual({key: collection(key) for key in expected}, expected)
        with self.assertRaises(ValueError):
            collection('unknown')

    def test_build_reports_serialized_disk_bytes_separately_from_utf8_text(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory)
            candidates = private / 'public-source-candidates'
            candidates.mkdir()
            kinds = [('substack', 'substack.jsonl'), ('linked_writing', 'linked-writing.jsonl'),
                     ('youtube_transcript', 'youtube.jsonl'), ('podcast_transcript', 'podcasts.jsonl'),
                     ('hee_transcript', 'hee.jsonl')]
            originals = {}
            for index, (kind, name) in enumerate(kinds):
                row = {'id': str(index), 'source_type': kind, 'source_url': f'https://example.org/{index}',
                       'source_group_id': f'https://example.org/{index}', 'title': f'Public essay {index}',
                       'text': f'Café α scientific learning grows from open educational sources number {index}.',
                       'acquisition': {'coverage': 'full_public_body', 'method': 'fixture'}}
                originals[kind] = row
                (candidates / name).write_text(json.dumps(row) + '\n')
            with contextlib.redirect_stdout(io.StringIO()):
                rows, stats = build(private, private / 'out')
            self.assertEqual(stats['by_collection'], {'writing': 2, 'podcasts_youtube': 2, 'hee': 1})
            self.assertEqual(stats['text_utf8_bytes'], sum(len(row['text'].encode('utf-8')) for row in rows))
            self.assertGreater(stats['text_utf8_bytes'], stats['characters'])
            self.assertEqual(stats['corpus_jsonl_bytes'], (private / 'out/corpus.jsonl').stat().st_size)
            self.assertGreater(stats['corpus_jsonl_bytes'], stats['text_utf8_bytes'])
            for kind, original in originals.items():
                row = next(row for row in rows if row['source_type'] == kind)
                self.assertEqual(row['source_url'], original['source_url'])
                self.assertEqual(row['acquisition'], original['acquisition'])
                metrics = stats['source_metrics'][kind]
                self.assertEqual(metrics['documents'], 1)
                self.assertEqual(metrics['excerpts'], 1)
                self.assertEqual(metrics['words'], len(row['text'].split()))
                disk_line = next(line for line in (private / 'out/corpus.jsonl').read_bytes().splitlines(keepends=True)
                                 if json.loads(line)['id'] == row['id'])
                self.assertEqual(metrics['corpus_jsonl_bytes'], len(disk_line))
            self.assertEqual(stats['collection_metrics']['writing']['documents'], 2)

    def test_streaming_refresh_preserves_unrelated_text_and_updates_one_canonical(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory)
            candidates = private / 'public-source-candidates'
            candidates.mkdir()
            def row(identifier, kind, text):
                return {'id': identifier, 'source_type': kind, 'source_url': 'https://example.org/' + identifier,
                        'source_group_id': 'https://example.org/' + identifier,
                        'title': 'Educational source ' + identifier, 'text': text, 'authorship': 'original-label'}
            hee = row('hee-doc', 'hee_transcript', 'The original narration describes scientific concepts and educational history.')
            linked = row('essay', 'linked_writing', 'An older article describes collaborative knowledge and useful public research.')
            (candidates / 'hee.jsonl').write_text(json.dumps(hee) + '\n')
            (candidates / 'linked-writing.jsonl').write_text(json.dumps(linked) + '\n')
            output = private / 'output'
            with contextlib.redirect_stdout(io.StringIO()):
                before, _ = build(private, output)
            updates = [{**linked, 'text': 'A revised public essay retains attribution and describes changes to scientific institutions.'},
                       row('new-essay', 'linked_writing', 'This additional public article explains computer chips and the expanding technology market.')]
            (candidates / 'linked-writing.jsonl').write_text(''.join(json.dumps(value) + '\n' for value in updates))
            with contextlib.redirect_stdout(io.StringIO()):
                stats = refresh(output, private)
            after = [json.loads(line) for line in (output / 'corpus.jsonl').read_text().splitlines()]
            self.assertEqual(len(after), 3)
            self.assertEqual([r for r in before if r['source_type'] == 'hee_transcript'],
                             [r for r in after if r['source_type'] == 'hee_transcript'])
            self.assertEqual(len({r['document_id'] for r in after if r['source_url'] == linked['source_url']}), 1)
            self.assertIn(updates[0]['text'], [r['text'] for r in after])
            self.assertNotIn(linked['text'], [r['text'] for r in after])
            narrator = next(r for r in after if r['source_type'] == 'hee_transcript')
            self.assertEqual(narrator['authorship'], 'rhys_lindmark_narration_owner_confirmed')
            self.assertEqual(narrator['original_authorship'], 'original-label')
            self.assertEqual(narrator['speaker_attribution']['method'], 'owner_confirmation_not_acoustic_diarization')
            self.assertTrue(narrator['speaker_attribution']['quoted_material_may_be_other_authors'])
            self.assertEqual(stats['corpus_jsonl_bytes'], (output / 'corpus.jsonl').stat().st_size)
            self.assertEqual(sum(r['text_utf8_bytes'] for r in stats['collection_metrics'].values()),
                             sum(len(r['text'].encode()) for r in after))

    def test_public_exception_is_bound_to_document_url_id_hash_and_excerpt(self):
        text = 'Human children are a general comparison in this public robotics explanation.'
        flags = privacy_flags(text)
        row = {'id': 'essay', 'source_type': 'linked_writing', 'source_url': 'https://example.org/essay', 'text': text}
        entry = {'document_id': 'essay', 'source_type': 'linked_writing', 'source_url': row['source_url'],
                 'document_content_sha256': digest(text), 'approved_document_flags': flags,
                 'reviewer': 'codex-public-writing-contextual-review', 'reviewed_at': '2026-10-19',
                 'scope': 'privacy_only_not_factual_validation',
                 'excerpt_approvals': [{'content_sha256': digest(text), 'flags': flags}]}
        reviews = {(row['source_url'], row['id']): entry}
        self.assertIs(public_excerpt_review(row, reviews, digest(text)), entry)
        for changes in ({'id': 'different'}, {'source_url': 'https://elsewhere.example/essay'},
                        {'source_type': 'substack'}, {'text': text + ' More text.'}):
            self.assertIsNone(public_excerpt_review({**row, **changes}, reviews, digest(text)))
        self.assertIsNone(public_excerpt_review(row, reviews, digest('changed document')))
        sensitive = 'A password belongs outside any published educational article.'
        forged = {**entry, 'document_content_sha256': digest(sensitive),
                  'excerpt_approvals': [{'content_sha256': digest(sensitive), 'flags': privacy_flags(sensitive)}]}
        self.assertIsNone(public_excerpt_review({**row, 'text': sensitive},
                         {(row['source_url'], row['id']): forged}, digest(sensitive)))

    def test_offline_snapshot_retains_svg_text_and_upsert_replaces_canonical_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'article.html'
            url = 'https://www.rhyslindmark.com/posts/ai2026-pt3/'
            body = ' '.join(['Public science and history paragraph.'] * 12)
            path.write_text(f'<title>Example — Rhys Lindmark</title><link rel="canonical" href="{url}">'
                            f'<nav>MENU</nav><main><h1>Example</h1><p>{body}</p>'
                            '<figure><svg><title>Chart title</title><desc>Source supplied description</desc>'
                            '<text>Forty units</text></svg><figcaption>Actual caption</figcaption></figure>'
                            '<script>SECRET SCRIPT</script><video>Browser fallback</video></main><footer>FOOTER</footer>')
            row = extract_snapshot(path, url)
            for actual in ['Chart title', 'Source supplied description', 'Forty units', 'Actual caption']:
                self.assertIn(actual, row['text'])
            for chrome in ['MENU', 'SECRET SCRIPT', 'Browser fallback', 'FOOTER']:
                self.assertNotIn(chrome, row['text'])
            old = {**row, 'id': 'stable-existing-id', 'text': 'Older public text'}
            refreshed = upsert([old], [row])
            self.assertEqual(len(refreshed), 1)
            self.assertEqual(refreshed[0]['id'], old['id'])
            self.assertEqual(refreshed[0]['text'], row['text'])
            self.assertEqual(upsert(refreshed, [row]), refreshed)
            with self.assertRaises(ValueError):
                upsert([old, {**old, 'id': 'conflicting-id'}], [row])


if __name__ == '__main__':
    unittest.main()
