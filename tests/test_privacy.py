import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from corpus_lib import clean_text, privacy_flags, group_split
from ingest_local import load_archive_js, extract_x
import json
import tempfile


class PrivacyTests(unittest.TestCase):
    def test_sensitive_material_excluded(self):
        cases = {
            'What does my girlfriend prefer?': 'relationship_or_intimacy',
            'My mother lives at 123 Example Street': 'family',
            'Contact: someone@example.com': 'contact_email',
            'Call 415-555-0101': 'phone',
            'API key sk-abcdefghijklmnop123456': 'credential',
            '/Users/example/private.txt': 'private_path',
            'Do not share this': 'private_marker',
            'Change in net worth this year': 'personal_health_or_finance',
            'Source https://example.test/asset.png?token=synthetic_access': 'signed_access_url',
        }
        for text, reason in cases.items():
            with self.subTest(reason=reason):
                self.assertIn(reason, privacy_flags(text, local=True))

    def test_no_keyword_clearance_means_no_semantic_approval(self):
        self.assertEqual(privacy_flags('A proton contains two up quarks and one down quark.'), [])
        self.assertIn('personal_reference', privacy_flags('What is my favorite molecule?', local=True))

    def test_markup_does_not_leak_attributes(self):
        self.assertEqual(clean_text('<img src="private-file.jpg"><script>secret</script><b>ATP</b><br>energy', markup=True), 'ATP\nenergy')

    def test_split_stable_per_source(self):
        self.assertEqual(group_split('episode-123'), group_split('episode-123'))

    def test_x_reads_posts_only_excludes_reposts_labels_own_replies(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d) / 'data'; data.mkdir()
            base = {'created_at': 'Tue Oct 13 21:09:00 +0000 2026', 'entities': {}}
            tweets = [dict(base, id_str='1', full_text='RT @other: Someone else wrote this text.'), dict(base, id_str='2', full_text='@friend This is an original public reply.'), dict(base, id_str='3', full_text='Here is a sufficiently long original educational observation.')]
            (data / 'tweets.js').write_text('window.YTD.tweets.part0 = ' + json.dumps([{'tweet': t} for t in tweets]))
            (data / 'direct-messages.js').write_text('INVALID_PRIVATE_DATA_DO_NOT_READ')
            result, stats = extract_x(d)
            self.assertEqual([r['id'] for r in result], ['x-2','x-3'])
            self.assertEqual(result[0]['authorship'],'rhys_public_reply_parent_context_not_included')
            self.assertEqual(stats['reposts_excluded'], 1)


if __name__ == '__main__':
    unittest.main()
