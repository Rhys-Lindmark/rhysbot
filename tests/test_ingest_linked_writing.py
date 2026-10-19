import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error

spec = importlib.util.spec_from_file_location("ingest_linked_writing", Path(__file__).resolve().parents[1] / "scripts" / "ingest_linked_writing.py")
ingest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingest)

URL = "https://www.rhyslindmark.com/example/"
BODY = " ".join("Public article sentence about history and science." for _ in range(12))


def article(url=URL, body=BODY, free=True):
    return (f'<html><head><link rel="canonical" href="{url}"><meta property="og:type" content="article">'
            '<script type="application/ld+json">' + json.dumps({"@type": "BlogPosting", "datePublished": "2020-01-01", "isAccessibleForFree": free}) +
            '</script></head><body><nav>MENU MUST BE OMITTED</nav><article><header><h1>Example Essay</h1></header>'
            '<div class="body markup"><p>' + body + '</p><script>BAD SCRIPT</script><div class="comments">COMMENT MUST BE OMITTED</div>'
            '</div><footer>FOOTER MUST BE OMITTED</footer></article></body></html>')


class LinkedWritingTests(unittest.TestCase):
    def test_enumeration_uses_latest_coverage_deduplicates_and_excludes_nav(self):
        rows = [{"id": 1, "source_url": "post1", "linked_own_articles": [URL]},
                {"id": 1, "source_url": "post1", "linked_own_articles": [URL + "#part", URL, "https://rhyslindmark.com/", "https://www.rhyslindmark.com/content/images/a.png", "https://www.rhyslindmark.com/private/one/"]},
                {"id": 2, "source_url": "post2", "linked_own_articles": ["http://rhyslindmark.com/example/?utm_source=email"]}]
        links, gaps = ingest.enumerate_links(rows)
        self.assertEqual(links, {URL: ["post1", "post2"]})
        self.assertEqual(len(gaps), 3)

    def test_extracts_only_body_with_title_date_and_canonical(self):
        result = ingest.extract_article(article(), URL)
        self.assertEqual(result["status"], "article_extracted")
        self.assertEqual(result["text"], BODY)
        self.assertEqual(result["title"], "Example Essay")
        self.assertEqual(result["published_at"], "2020-01-01")
        self.assertEqual(result["canonical_url"], URL)

    def test_does_not_accept_preview_missing_canonical_or_wrong_article(self):
        self.assertEqual(ingest.extract_article(article(free=False), URL)["status"], "restricted_or_preview")
        self.assertEqual(ingest.extract_article(article(body='<div class="paywall">Preview</div>'), URL)["status"], "restricted_or_preview")
        self.assertEqual(ingest.extract_article(article().replace('rel="canonical"', 'rel="alternate"'), URL)["status"], "missing_canonical_provenance")
        self.assertEqual(ingest.extract_article(article(url=URL + "wrong/"), URL)["status"], "canonical_url_mismatch")
        self.assertEqual(ingest.extract_article(article().replace("Example Essay", "404 Page not found"), URL)["status"], "not_an_article")

    def test_navigation_page_and_tiny_article_are_gaps(self):
        self.assertEqual(ingest.extract_article(article().replace('content="article"', 'content="website"').replace('"BlogPosting"', '"WebPage"'), URL)["status"], "unsupported_article_structure")
        self.assertEqual(ingest.extract_article(article(body="A teaser."), URL)["status"], "insufficient_article_text")

    def test_verified_astro_main_requires_matching_title_h1_and_canonical(self):
        source = f'<title>Example Essay — Rhys Lindmark</title><link rel="canonical" href="{URL}"><nav>Menu</nav><main><section class="series-hero">CHIPS MODELS AGENTS Read blog</section><h1>Example Essay</h1><p>{BODY}</p></main><footer>Footer</footer>'
        result = ingest.extract_article(source, URL)
        self.assertEqual(result["status"], "article_extracted")
        self.assertEqual(result["body_selector"], "main.")
        self.assertNotIn("Menu", result["text"])
        self.assertNotIn("CHIPS", result["text"])
        self.assertTrue(result["provenance"]["own_domain_titled_main"])
        self.assertEqual(ingest.extract_article(source.replace("<h1>Example Essay", "<h1>Different"), URL)["status"], "unsupported_article_structure")

    def test_resume_and_existing_substack_are_not_duplicate_candidates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            coverage = root / "source.jsonl"
            substack = "https://rhyslindmark.substack.com/p/example"
            coverage.write_text(json.dumps({"id": 1, "source_url": substack, "status": "full_public_body", "linked_own_articles": [URL]}) + "\n")
            calls = []
            def fetch(url):
                calls.append(url)
                return article(url=substack), substack
            first = ingest.run(coverage, root / "out", fetcher=fetch)
            self.assertEqual(first[URL]["status"], "existing_substack_exact_slug_match")
            ingest.run(coverage, root / "out", fetcher=fetch)
            self.assertEqual(calls, [])
            self.assertFalse((root / "out/candidates.jsonl").exists())

    def test_slug_match_requires_exact_full_body_and_preserves_failed_redirect(self):
        full = {"id": 1, "source_url": "https://rhyslindmark.substack.com/p/example", "status": "full_public_body", "linked_own_articles": [URL]}
        self.assertEqual(ingest.exact_substack_match(URL, [full])["id"], 1)
        self.assertIsNone(ingest.exact_substack_match(URL + "different/", [full]))
        self.assertIsNone(ingest.exact_substack_match(URL, [{**full, "status": "restricted_or_preview"}]))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            coverage = root / "source.jsonl"
            coverage.write_text(json.dumps(full) + "\n")
            out = root / "out"
            out.mkdir()
            ingest.append_jsonl(out / "coverage.jsonl", {"source_url": URL, "status": "failed", "http_status": 404, "final_url": "https://rhyslindmark.substack.com/example"})
            statuses = ingest.run(coverage, out, fetcher=lambda url: self.fail("Should use known body"))
            self.assertEqual(statuses[URL]["previous_network_observation"]["http_status"], 404)
            self.assertEqual(statuses[URL]["matched_source_id"], 1)

    def test_rate_limit_stops_without_attempting_next_or_implicit_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            coverage = root / "source.jsonl"
            coverage.write_text(json.dumps({"id": 1, "source_url": "post", "linked_own_articles": [URL, URL.replace("example", "z-next")]}) + "\n")
            calls = []
            def fetch(url):
                calls.append(url)
                raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
            statuses = ingest.run(coverage, root / "out", fetcher=fetch)
            self.assertEqual(len(calls), 1)
            self.assertEqual(statuses[URL]["status"], "rate_limited")
            ingest.run(coverage, root / "out", fetcher=fetch)
            self.assertEqual(len(calls), 1)

    def test_redirect_stays_tls_and_on_allowed_public_domain(self):
        handler = ingest.PublicRedirect(lambda: None)
        for url in ("http://www.rhyslindmark.com/essay/", "https://accounts.example/login", "https://person:secret@rhyslindmark.com/a", "https://rhyslindmark.substack.com/sign-in", "https://rhyslindmark.com:9999/a"):
            with self.assertRaises(ValueError):
                handler.redirect_request(None, None, 302, "redirect", {}, url)


if __name__ == "__main__":
    unittest.main()
