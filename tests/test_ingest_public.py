import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("ingest_public", Path(__file__).resolve().parents[1] / "scripts" / "ingest_public.py")
ingest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingest)


class PublicParsingTests(unittest.TestCase):
    def test_article_structure_entities_and_excluded_code(self):
        text, links = ingest.html_text('<nav>Subscribe</nav><h2>A &amp; B</h2><p>First <strong>idea</strong>.</p><script>secret()</script><p><a href="https://example.org/source">Source</a></p>')
        self.assertEqual(text, "A & B\n\nFirst idea.\n\nSource")
        self.assertEqual(links, ["https://example.org/source"])

    def test_rolling_captions_no_duplicate_words(self):
        vtt = '''WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:01.000 align:start
<c>This is</c>

00:00:01.000 --> 00:00:02.000
This is an idea

00:00:02.000 --> 00:00:03.000
an idea &amp; example
'''
        self.assertEqual(ingest.clean_vtt(vtt), "This is an idea & example")

    def test_paid_preview_is_never_full(self):
        self.assertEqual(ingest.classify_post({"body_html": "<p>Preview</p>", "audience": "only_paid"}), "restricted_or_preview")
        self.assertEqual(ingest.classify_post({"body_html": None, "truncated_body_text": "Preview"}), "missing_body")
        self.assertEqual(ingest.classify_post({"body_html": "<p>Full</p>", "audience": "everyone"}), "full_public_body")

    def test_stable_provenance_and_authorship(self):
        a = ingest.candidate("youtube_transcript", "https://example.org/a", "Title", None, "Text", "mixed_or_unverified_speakers", {"method": "test"})
        b = ingest.candidate("youtube_transcript", "https://example.org/a", "Changed title", None, "New text", "mixed_or_unverified_speakers", {})
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(a["source_group_id"], a["source_url"])
        self.assertEqual(a["authorship"], "mixed_or_unverified_speakers")


if __name__ == "__main__":
    unittest.main()
