#!/usr/bin/env python3
"""Acquire only public article links enumerated in Substack coverage.

No accounts, cookies, local drafts, videos, or publication. Candidates and gaps
are stored outside Git. Resume skips every attempted URL; --retry-failed is an
explicit later retry after the recorded server restriction/cooldown is resolved.
"""
import argparse
from collections import Counter
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import ssl
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

REPO = Path(__file__).resolve().parents[1]
OWN_HOSTS = {"rhyslindmark.com", "www.rhyslindmark.com"}
ALLOWED_HOSTS = OWN_HOSTS | {"rhyslindmark.substack.com"}
MAX_BYTES = 8 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 RhysbotCorpus/0.1 (public text acquisition)"
NONARTICLE = {"about", "about-me", "all-posts", "writing", "tools", "twitter",
              "work-with-me", "wpw", "speaking", "p/speaking.html"}


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append_jsonl(path, value):
    with path.open("a") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def write_json(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def normalize_link(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme not in {"https", "http"} or p.hostname not in OWN_HOSTS or p.username or p.password:
        return None, "outside_public_own_domain"
    if p.port not in (None, 80, 443):
        return None, "unsupported_port"
    path = urllib.parse.unquote(p.path).strip("/")
    if not path or path.lower() in NONARTICLE:
        return None, "navigation_or_root"
    if any(x.lower() in {"admin", "ghost", "login", "signin", "account", "private", "members", "preview", "draft", "api", "tag", "tags", "author", "page"} for x in path.split("/")):
        return None, "nonarticle_or_private_path"
    if re.search(r"\.(png|jpe?g|gif|webp|svg|pdf|mp[34]|zip|xml|json)$", path, re.I):
        return None, "nonarticle_asset"
    # The input links contain no content-bearing query arguments. Never carry
    # tracking tokens or fragments into an acquisition URL.
    if p.query and any(k.lower() not in {"utm_source", "utm_medium", "utm_campaign", "ref"} for k, _ in urllib.parse.parse_qsl(p.query)):
        return None, "unsupported_query"
    path = urllib.parse.quote(path, safe="/-._~")
    return "https://www.rhyslindmark.com/" + path + ("" if path.endswith(".html") else "/"), None


def enumerate_links(coverage):
    latest = {r["id"]: r for r in coverage}
    links, excluded = {}, []
    for post in latest.values():
        for raw in post.get("linked_own_articles", []):
            url, reason = normalize_link(raw)
            if not url:
                excluded.append({"source_url": raw, "status": reason, "discovered_from": post["source_url"]})
                continue
            links.setdefault(url, set()).add(post["source_url"])
    return {url: sorted(parents) for url, parents in sorted(links.items())}, excluded


def exact_substack_match(url, coverage):
    """A unique identical slug is evidence of an existing body, not a redirect."""
    path = urllib.parse.unquote(urllib.parse.urlsplit(url).path).strip("/")
    if path.startswith("posts/"):
        path = path[len("posts/"):]
    if "/" in path:
        return None
    latest = {r["id"]: r for r in coverage}
    matches = {}
    for row in latest.values():
        p = urllib.parse.urlsplit(row["source_url"])
        if row.get("status") != "full_public_body" or p.hostname != "rhyslindmark.substack.com":
            continue
        canonical_path = urllib.parse.unquote(p.path).strip("/")
        if canonical_path == "p/" + path:
            matches[row["source_url"].rstrip("/")] = row
    return next(iter(matches.values())) if len(matches) == 1 else None


class Node:
    def __init__(self, tag, attrs=None):
        self.tag, self.attrs, self.children = tag, dict(attrs or []), []


class Page(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]
        self.nodes = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        self.nodes.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, value):
        self.stack[-1].children.append(value)


OMIT = {"script", "style", "nav", "footer", "header", "aside", "form", "button", "noscript", "svg"}
BLOCK = {"p", "div", "section", "article", "main", "li", "h1", "h2", "h3", "h4", "blockquote", "br", "tr", "pre"}


def text(node):
    if isinstance(node, str):
        return node
    classes = node.attrs.get("class", "").lower()
    if node.tag in OMIT or re.search(r"\b(comment|comments|subscribe|subscription|share-dialog|post-ufi|post-footer|related-posts|recommendations|series-hero)\b", classes):
        return ""
    value = "".join(text(child) for child in node.children)
    return "\n\n" + value + "\n\n" if node.tag in BLOCK else value


def clean(value):
    value = unicodedata.normalize("NFC", value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    return re.sub(r"\n\s*\n(?:\s*\n)+", "\n\n", value).strip()


def extract_article(source, final_url):
    page = Page(source)
    canonical = next((n.attrs.get("href") for n in page.nodes if n.tag == "link" and "canonical" in n.attrs.get("rel", "").split()), None)
    if not canonical:
        return {"status": "missing_canonical_provenance"}
    canonical = urllib.parse.urljoin(final_url, canonical)
    parsed = urllib.parse.urlsplit(canonical)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password:
        return {"status": "unsupported_canonical", "canonical_url": canonical}
    canonical = urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path, "", ""))
    final = urllib.parse.urlsplit(final_url)
    if (not parsed.path.strip("/") or parsed.hostname.removeprefix("www.") != (final.hostname or "").removeprefix("www.")
            or urllib.parse.unquote(parsed.path).rstrip("/") != urllib.parse.unquote(final.path).rstrip("/")):
        return {"status": "canonical_url_mismatch", "canonical_url": canonical}
    meta = {n.attrs.get("property") or n.attrs.get("name"): n.attrs.get("content") for n in page.nodes if n.tag == "meta"}
    h1 = next((clean(text(n)) for n in page.nodes if n.tag == "h1"), "")
    document_title = next((clean(text(n)) for n in page.nodes if n.tag == "title"), "")
    title = h1 or meta.get("og:title") or document_title
    if not title or re.search(r"\b(404|not found|page not found|access denied|just a moment)\b", title, re.I):
        return {"status": "not_an_article", "canonical_url": canonical, "title": title}
    ld = []
    for node in page.nodes:
        if node.tag == "script" and node.attrs.get("type") == "application/ld+json":
            try:
                value = json.loads("".join(x for x in node.children if isinstance(x, str)))
                ld.extend(value if isinstance(value, list) else [value])
            except (ValueError, TypeError):
                pass
    ld = [item for obj in ld if isinstance(obj, dict) for item in obj.get("@graph", [obj]) if isinstance(item, dict)]
    articles = [obj for obj in ld if any(t in {"Article", "BlogPosting", "NewsArticle"} for t in (obj.get("@type") if isinstance(obj.get("@type"), list) else [obj.get("@type")]))]
    # The live Astro essays use a canonical URL, an exact h1/title match and a
    # dedicated main, without OpenGraph/JSON-LD article metadata. This verified
    # site template is narrower than falling back to arbitrary page/body text.
    own_titled_main = (parsed.hostname in OWN_HOSTS and bool(h1)
                      and document_title in {h1 + " — Rhys Lindmark", h1 + " - Rhys Lindmark"})
    if any(obj.get("isAccessibleForFree") in (False, "false") for obj in articles) or any(re.search(r"\b(paywall|paywall-content|subscriber-only|restricted-content)\b", n.attrs.get("class", "") + " " + n.attrs.get("id", ""), re.I) for n in page.nodes):
        return {"status": "restricted_or_preview", "canonical_url": canonical, "title": title}
    body = next((n for n in page.nodes if {"body", "markup"} <= set(n.attrs.get("class", "").split())), None)
    if body is None:
        body = next((n for n in page.nodes if set(n.attrs.get("class", "").split()) & {"post-content", "gh-content", "entry-content", "article-content"}), None)
    if body is None and (articles or meta.get("og:type") == "article" or own_titled_main):
        body = next((n for n in page.nodes if n.tag == "article"), None)
        if body is None:
            body = next((n for n in page.nodes if n.tag == "main"), None)
    if body is None or not (articles or meta.get("og:type") == "article" or own_titled_main):
        return {"status": "unsupported_article_structure", "canonical_url": canonical, "title": title}
    value = clean(text(body))
    if len(value.split()) < 40:
        return {"status": "insufficient_article_text", "canonical_url": canonical, "title": title, "characters": len(value)}
    return {"status": "article_extracted", "canonical_url": canonical, "title": title, "text": value,
            "published_at": next((obj.get("datePublished") for obj in articles if obj.get("datePublished")), meta.get("article:published_time")),
            "body_selector": body.tag + "." + ".".join(body.attrs.get("class", "").split()),
            "provenance": {"canonical_url": canonical, "h1": h1, "document_title": document_title,
                           "og_type": meta.get("og:type"), "jsonld_article": bool(articles), "own_domain_titled_main": own_titled_main}}


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, pace):
        self.pace = pace

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        private_route = any(part.lower() in {"admin", "ghost", "login", "sign-in", "sign-up", "signin", "account", "private", "members", "preview", "draft", "api", "subscribe"}
                            for part in urllib.parse.unquote(parsed.path).split("/"))
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443) or private_route:
            raise ValueError("Redirect outside allowed public domains/TLS")
        self.pace()
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Fetcher:
    def __init__(self, delay=2.0):
        self.delay, self.last = max(2.0, delay), 0
        try:
            import certifi
            context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            context = ssl.create_default_context()
        self.opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), PublicRedirect(self.pace))

    def pace(self):
        time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
        self.last = time.monotonic()

    def __call__(self, url):
        self.pace()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
        with self.opener.open(request, timeout=30) as response:
            if "text/html" not in response.headers.get("Content-Type", "").lower():
                raise ValueError("Unsupported non-HTML response")
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("Response exceeded 8 MiB bound")
            return body.decode(response.headers.get_content_charset() or "utf-8"), response.url


def run(coverage_path, out, retry_failed=False, limit=None, fetcher=None):
    out = out.resolve()
    if out == REPO or REPO in out.parents:
        raise ValueError("Candidate output must be outside the repository")
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    rows = read_jsonl(coverage_path)
    links, excluded = enumerate_links(rows)
    existing = {r["source_url"].rstrip("/") for r in rows if r.get("status") == "full_public_body"}
    write_json(out / "enumeration.json", {"urls": links, "excluded": excluded})
    statuses = {r["source_url"]: r for r in read_jsonl(out / "coverage.jsonl")}
    acquired = {r["source_url"] for r in read_jsonl(out / "candidates.jsonl")}
    for url, parents in links.items():
        old = statuses.get(url)
        match = exact_substack_match(url, rows)
        if match and (not old or old["status"] not in {"already_acquired_substack", "existing_substack_exact_slug_match"}):
            status = {"source_url": url, "discovered_from": parents, "status": "existing_substack_exact_slug_match",
                      "matched_source_id": match["id"], "canonical_url": match["source_url"],
                      "match_method": "unique_identical_decoded_path_slug_against_full_public_body"}
            if old:
                status["previous_network_observation"] = {k: old[k] for k in ("status", "error", "http_status", "final_url") if k in old}
            append_jsonl(out / "coverage.jsonl", status)
            statuses[url] = status
    def checkpoint():
        counts = dict(Counter(r["status"] for r in statuses.values()))
        write_json(out / "state.json", {"enumerated": len(links), "excluded_links": len(excluded), "excluded_unique_links": len({r["source_url"] for r in excluded}),
                                       "resolved_or_attempted": len(statuses), "remaining": len(links) - len(statuses), "counts": counts})
    checkpoint()
    if not retry_failed and any(r["status"] == "rate_limited" for r in statuses.values()):
        print("Recorded HTTP 429 blocker; no requests until an explicit cooled-down retry.", flush=True)
        return statuses
    fetcher = fetcher or Fetcher()
    attempts = 0
    for url, parents in links.items():
        old = statuses.get(url)
        if old and (not retry_failed or old["status"] not in {"failed", "rate_limited"}):
            continue
        if limit is not None and attempts >= limit:
            break
        attempts += 1
        status = {"source_url": url, "discovered_from": parents}
        try:
            source, final_url = fetcher(url)
            result = extract_article(source, final_url)
            value = result.pop("text", None)
            status.update(result, final_url=final_url, response_bytes=len(source.encode()))
            if value:
                status.update(characters=len(value), words=len(value.split()), content_sha256=hashlib.sha256(value.encode()).hexdigest())
                canonical = result["canonical_url"]
                if canonical.rstrip("/") in existing:
                    status["status"] = "already_acquired_substack"
                elif canonical in acquired:
                    status["status"] = "duplicate_canonical"
                else:
                    candidate = {"id": "linked_writing:" + hashlib.sha256(canonical.encode()).hexdigest()[:24], "source_type": "linked_writing",
                                 "source_url": canonical, "title": result["title"], "published_at": result["published_at"], "text": value,
                                 "authorship": "rhys_publication_with_quotations_possible", "source_group_id": canonical,
                                 "acquisition": {"method": "public_article_html", "coverage": "article_extracted_candidate_unreviewed",
                                                 "requested_url": url, "final_url": final_url, "discovered_from": parents,
                                                 "body_selector": result["body_selector"], "provenance": result["provenance"], "visual_content_not_transcribed": True}}
                    append_jsonl(out / "candidates.jsonl", candidate)
                    acquired.add(canonical)
        except urllib.error.HTTPError as error:
            # Python 3.9 HTTPError instances without a response body may not
            # initialize .url; logging must not mask the original 429/404.
            status.update(status="rate_limited" if error.code == 429 else "failed", error=str(error), http_status=error.code, final_url=vars(error).get("url"))
        except Exception as error:
            status.update(status="failed", error=str(error))
        append_jsonl(out / "coverage.jsonl", status)
        statuses[url] = status
        checkpoint()
        print(url, status["status"], flush=True)
        if status["status"] == "rate_limited":
            break
    return statuses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    run(args.coverage, args.output, args.retry_failed, args.limit)


if __name__ == "__main__":
    main()
