import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("ingest_public", Path(__file__).resolve().parents[1] / "scripts" / "ingest_public.py")
ingest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingest)


class PublicParsingTests(unittest.TestCase):
    def test_caption_retry_preserves_successes_and_requires_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out / "youtube-inventory.json").write_text(json.dumps([
                {"id": video, "title": video} for video in ("success", "missing", "failed")]))
            ingest.append_jsonl(out / "youtube-coverage.jsonl", {"video_id": "success", "status": "caption_acquired"})
            ingest.append_jsonl(out / "youtube-coverage.jsonl", {"video_id": "missing", "status": "no_available_english_caption"})
            ingest.append_jsonl(out / "youtube-coverage.jsonl", {"video_id": "failed", "status": "failed"})
            def metadata(command, **kwargs):
                self.assertTrue(command[-1].endswith("v=failed"))
                kwargs["stdout"].write("{}")
                return SimpleNamespace(returncode=0)
            with patch.object(ingest.subprocess, "run", side_effect=metadata) as run, patch.object(ingest.time, "sleep"):
                ingest.ingest_youtube(out)
                run.assert_not_called()
                ingest.ingest_youtube(out, retry_failed=True)
                self.assertEqual(run.call_count, 1)
            statuses = {r["video_id"]: r["status"] for r in ingest.read_jsonl(out / "youtube-coverage.jsonl")}
            self.assertEqual(statuses, {"success": "caption_acquired", "missing": "no_available_english_caption", "failed": "no_available_english_caption"})

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
        self.assertEqual(ingest.classify_post({"body_html": "<p>An essay about Medium's paywall.</p>", "audience": "everyone"}), "full_public_body")
        for body in ('<p>Preview</p><!-- paywall -->', '<div class="paywall">Preview</div>'):
            self.assertEqual(ingest.classify_post({"body_html": body, "audience": "everyone"}), "paywall_marker_review")

    def test_stable_provenance_and_authorship(self):
        a = ingest.candidate("youtube_transcript", "https://example.org/a", "Title", None, "Text", "mixed_or_unverified_speakers", {"method": "test"})
        b = ingest.candidate("youtube_transcript", "https://example.org/a", "Changed title", None, "New text", "mixed_or_unverified_speakers", {})
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(a["source_group_id"], a["source_url"])
        self.assertEqual(a["authorship"], "mixed_or_unverified_speakers")


if __name__ == "__main__":
    unittest.main()
