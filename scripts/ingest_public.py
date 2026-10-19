#!/usr/bin/env python3
"""Acquire public writing/captions and local HEE transcripts, never account data.

All acquired text is a *candidate*, not privacy/republication approval. Output must
be outside the repository. Uses stdlib plus an existing yt-dlp executable; certifi
is optional when the Python installation has no working default certificate CA.
"""
import argparse
from collections import Counter
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import ssl
import subprocess
import time
import unicodedata
import urllib.parse
import urllib.request
import urllib.error

REPO = Path(__file__).resolve().parents[1]
USER_AGENT = "Mozilla/5.0 RhysbotCorpus/0.1 (public text acquisition)"
MAX_RESPONSE = 8 * 1024 * 1024


class TextHTML(HTMLParser):
    """Keep article text/links while excluding scripts, style and navigation."""
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "blockquote", "br", "tr"}
    OMIT = {"script", "style", "nav", "footer", "header", "noscript"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.skip = [], [], []

    def handle_starttag(self, tag, attrs):
        if tag in self.OMIT:
            self.skip.append(tag)
        if self.skip:
            return
        if tag in self.BLOCK:
            self.parts.append("\n\n")
        if tag == "a":
            self.links.extend(v for k, v in attrs if k == "href" and v)

    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip[-1]:
                self.skip.pop()
            return
        if tag in self.BLOCK:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self):
        value = unicodedata.normalize("NFC", "".join(self.parts)).replace("\xa0", " ")
        value = re.sub(r"[ \t]+", " ", value)
        return re.sub(r"\n\s*\n(?:\s*\n)+", "\n\n", value).strip()


def html_text(value):
    parser = TextHTML()
    parser.feed(value or "")
    return parser.text(), parser.links


def clean_vtt(value):
    """Remove cue metadata and rolling-window duplicate/overlapping words."""
    merged = []
    for raw in value.splitlines():
        line = raw.strip()
        if (not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE"))
                or "-->" in line or re.fullmatch(r"\d+", line)):
            continue
        line = html.unescape(re.sub(r"<[^>]*>", "", line)).strip()
        if not line:
            continue
        words = line.split()
        # Auto captions repeat the previous cue then append its next words.
        if merged:
            overlap = next((n for n in range(min(len(merged), len(words)), 0, -1)
                            if merged[-n:] == words[:n]), 0)
            words = words[overlap:]
        if words:
            merged.extend(words)
    return " ".join(merged)


def stable_id(source_type, key):
    return source_type + ":" + hashlib.sha256(key.encode()).hexdigest()[:24]


def candidate(source_type, url, title, date, text, authorship, acquisition):
    if date and re.fullmatch(r"\d{8}", date):
        date = date[:4] + "-" + date[4:6] + "-" + date[6:]
    return {"id": stable_id(source_type, url), "source_type": source_type,
            "source_url": url, "title": title, "published_at": date,
            "text": text, "authorship": authorship, "acquisition": acquisition,
            "source_group_id": url}


def write_json(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append_jsonl(path, value):
    with path.open("a") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def fetch(url):
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30, context=ctx) as response:
        body = response.read(MAX_RESPONSE + 1)
        if len(body) > MAX_RESPONSE:
            raise ValueError("response exceeded 8 MiB bounded download")
        return body.decode("utf-8")


def classify_post(post):
    """Never silently treat a paid preview or truncated text as a full post."""
    if not post.get("body_html"):
        return "missing_body"
    if post.get("audience") not in (None, "everyone"):
        return "restricted_or_preview"
    if post.get("is_geoblocked") or post.get("free_unlock_required"):
        return "restricted_or_preview"
    # A public essay discussing a paywall is not itself paywalled. Keep
    # structural markers quarantined, alongside audience/access checks above.
    if re.search(r'''<!--\s*paywall\b|\b(?:class|id|data-component-name)\s*=\s*["'][^"']*\bpaywall\b''',
                 post["body_html"], re.I):
        return "paywall_marker_review"
    return "full_public_body"


def ingest_substack(out, max_pages=40):
    root = "https://rhyslindmark.substack.com"
    archive_path = out / "substack-archive.json"
    enumeration_path = out / "substack-enumeration.json"
    previous = json.loads(enumeration_path.read_text()) if enumeration_path.exists() else {}
    archive = json.loads(archive_path.read_text()) if previous.get("archive_exhausted") else []
    complete = bool(previous.get("archive_exhausted"))
    offset = 0
    for page in range(0 if complete else max_pages):
        url = root + "/api/v1/archive?" + urllib.parse.urlencode(
            {"sort": "new", "offset": offset, "limit": 50})
        batch = json.loads(fetch(url))
        if not isinstance(batch, list):
            raise ValueError("Unexpected Substack archive response")
        if not batch:
            complete = True
            break
        # Some pages have fewer than limit before the archive is exhausted.
        prior = {r["id"] for r in archive}
        fresh = [r for r in batch if r.get("id") not in prior]
        if not fresh:
            raise ValueError("Archive pagination repeated a page")
        archive.extend({k: r.get(k) for k in
                       ["id", "slug", "title", "canonical_url", "post_date", "audience", "wordcount"]}
                       for r in fresh)
        write_json(archive_path, archive)
        offset += len(batch)
        time.sleep(0.15)
    write_json(enumeration_path, {
        "posts": len(archive), "archive_exhausted": complete, "next_offset": offset or previous.get("next_offset", 0)})
    statuses = {r["id"]: r for r in read_jsonl(out / "substack-coverage.jsonl")}
    for post in archive:
        if post["id"] in statuses and statuses[post["id"]]["status"] not in {"failed", "paywall_marker_review"}:
            continue
        url = post["canonical_url"] or root + "/p/" + post["slug"]
        status = {"id": post["id"], "source_url": url, "title": post["title"]}
        try:
            body = json.loads(fetch(root + "/api/v1/posts/" + urllib.parse.quote(post["slug"])))
            kind = classify_post(body)
            text, links = html_text(body.get("body_html"))
            own_links = sorted(set(link for link in links if urllib.parse.urlparse(link).hostname
                                   in {"rhyslindmark.com", "www.rhyslindmark.com"}))
            status.update(status=kind, characters=len(text), linked_own_articles=own_links)
            if text:
                item = candidate("substack", url, body.get("title") or post["title"],
                                 body.get("post_date") or post["post_date"], text,
                                 "rhys_publication_with_quotations_possible", {
                                     "method": "public_substack_post_api", "coverage": kind,
                                     "declared_wordcount": body.get("wordcount"),
                                     "linked_own_articles": own_links})
                append_jsonl(out / "substack.jsonl", item)
        except Exception as error:
            status.update(status="failed", error=str(error))
        append_jsonl(out / "substack-coverage.jsonl", status)
        print("substack", post["slug"], status["status"], flush=True)
        statuses[post["id"]] = status
        if status.get("error", "").startswith("HTTP Error 429"):
            write_json(out / "substack-blocker.json", {
                "reason": "Public endpoint returned HTTP 429; stopped requests. Retry after a cooldown.",
                "source_url": url, "remaining_unattempted": True})
            break
        time.sleep(2.0)


def ingest_hee(out, source):
    mapping = json.loads((source / "transcripts" / "filename-map.json").read_text())
    index = {row["id"]: row for row in json.loads((source / "index.json").read_text())}
    by_file = {row["transcript"]: (video_id, row) for video_id, row in mapping.items()}
    coverage = []
    records = []
    for path in sorted((source / "transcripts").glob("*.txt")):
        if path.name not in by_file:
            coverage.append({"status": "unmapped_local_transcript", "filename": path.name})
            continue
        video_id, meta = by_file[path.name]
        row = index.get(video_id, {})
        date = row.get("upload_date")
        if date and re.fullmatch(r"\d{8}", date):
            date = date[:4] + "-" + date[4:6] + "-" + date[6:]
        url = "https://www.youtube.com/watch?v=" + video_id
        records.append(candidate("hee_transcript", url, meta["title"], date,
                                 path.read_text().strip(),
                                 "rhys_hosted_speaker_attribution_unverified", {
                                     "method": "existing_local_caption_transcript",
                                     "coverage": "existing_transcript_not_audio_verified",
                                     "speaker_labels": False, "youtube_video_id": video_id,
                                     "transcript_origin": "extract_transcripts.py (yt-dlp/API fallback)",
                                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}))
        coverage.append({"source_url": url, "status": "existing_transcript",
                         "characters": len(records[-1]["text"])})
    with (out / "hee.jsonl").open("w") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    write_json(out / "hee-coverage.json", coverage)
    print("hee", len(records), "transcripts", flush=True)


def ingest_linked_local_writing(out, source):
    """Read only local public HTML directly linked by acquired public posts.

    No crawl of arbitrary local Markdown drafts, relationship pages or generated
    build directories. Article markup is extracted without executing JavaScript.
    """
    linked = {}
    for item in read_jsonl(out / "substack.jsonl"):
        for url in item["acquisition"].get("linked_own_articles", []):
            linked.setdefault(url, []).append(item)
    coverage, records = [], []
    for url, parents in sorted(linked.items()):
        parsed = urllib.parse.urlparse(url)
        parts = Path(parsed.path.lstrip("/"))
        if not parsed.path.startswith("/posts/") or ".." in parts.parts:
            continue
        path = (source / "public" / parts / "index.html").resolve()
        if (source / "public").resolve() not in path.parents or not path.exists():
            coverage.append({"source_url": url, "status": "linked_local_html_unavailable"})
            continue
        raw = path.read_text()
        main = re.search(r"<main\b[^>]*>(.*?)</main>", raw, re.S | re.I)
        if not main:
            coverage.append({"source_url": url, "status": "article_markup_unrecognized"})
            continue
        title = re.search(r"<title>(.*?)</title>", raw, re.S | re.I)
        text, _ = html_text(main.group(1))
        record = candidate("linked_writing", url,
                           html.unescape(title.group(1)) if title else parents[0]["title"],
                           min(p["published_at"] for p in parents if p["published_at"]),
                           text, "rhys_publication_with_quotations_possible", {
                               "method": "local_public_article_html_linked_from_public_substack",
                               "coverage": "static_article_text_dynamic_chart_content_excluded",
                               "linked_from": [p["source_url"] for p in parents],
                               "sha256": hashlib.sha256(raw.encode()).hexdigest()})
        records.append(record)
        coverage.append({"source_url": url, "status": "static_article_acquired", "characters": len(text)})
    with (out / "linked-writing.jsonl").open("w") as handle:
        for item in records:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    write_json(out / "linked-writing-coverage.json", coverage)
    print("linked local writing", len(records), flush=True)


def enumerate_youtube(out, channel_url="https://www.youtube.com/user/RhysLindmark"):
    if urllib.parse.urlparse(channel_url).hostname not in ('www.youtube.com','youtube.com'):
        raise ValueError('Channel must be on YouTube')
    inventory_path = out / 'youtube-inventory.json'
    found = {r['id']:r for r in json.loads(inventory_path.read_text())} if inventory_path.exists() else {}
    enumeration_path = out / 'youtube-enumeration.json'
    tabs = json.loads(enumeration_path.read_text()) if enumeration_path.exists() else []
    for tab in ("videos", "shorts", "streams"):
        url = channel_url.rstrip('/') + '/' + tab
        target = out / ("youtube-" + tab + "-enumeration.jsonl")
        error_path = out / ("youtube-" + tab + "-enumeration.stderr.txt")
        with target.open("w") as stdout, error_path.open("w") as stderr:
            try:
                result = subprocess.run([
                    "yt-dlp", "--flat-playlist", "--dump-json", "--skip-download",
                    "--no-warnings", "--socket-timeout", "20", "--retries", "1",
                    "--extractor-retries", "1", "--playlist-end", "2000", url],
                    stdout=stdout, stderr=stderr, timeout=120)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = "timeout"
        rows = read_jsonl(target)
        for row in rows:
            video_id = row.get("id")
            if not video_id or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                continue
            found[video_id] = {k: row.get(k) for k in
                               ["id", "title", "url", "duration", "channel_id", "channel", "upload_date"]}
        tabs.append({"tab": tab, "url": url, "exit_code": code, "listed": len(rows),
                     "error": error_path.read_text()[-1800:]})
    write_json(out / "youtube-inventory.json", list(found.values()))
    write_json(out / "youtube-enumeration.json", tabs)
    print("youtube unique videos", len(found), flush=True)


def ingest_youtube(out, limit=2000, retry_failed=False):
    inventory = json.loads((out / "youtube-inventory.json").read_text())
    existing = {r["acquisition"]["youtube_video_id"] for r in read_jsonl(out / "hee.jsonl")}
    statuses = {r["video_id"]: r for r in read_jsonl(out / "youtube-coverage.jsonl")}
    consecutive_failures = 0
    for row in inventory[:limit]:
        video_id = row["id"]
        if video_id in statuses and not (retry_failed and statuses[video_id]["status"] == "failed"):
            continue
        url = "https://www.youtube.com/watch?v=" + video_id
        status = {"video_id": video_id, "source_url": url, "title": row["title"]}
        if video_id in existing:
            status["status"] = "covered_by_existing_hee_transcript"
            append_jsonl(out / "youtube-coverage.jsonl", status)
            continue
        try:
            info_path = out / "youtube-active-info.json"
            stderr_path = out / "youtube-active.stderr.txt"
            with info_path.open("w") as handle, stderr_path.open("w") as stderr:
                run = subprocess.run([
                    "yt-dlp", "--dump-single-json", "--skip-download", "--no-warnings",
                    "--ignore-no-formats-error", "--socket-timeout", "20", "--retries", "1",
                    "--extractor-retries", "1", url], stdout=handle, stderr=stderr, timeout=90)
            if run.returncode:
                raise RuntimeError(stderr_path.read_text()[-1800:])
            info = json.loads(info_path.read_text())
            tracks = []
            for kind in ("subtitles", "automatic_captions"):
                for lang in ("en", "en-orig", "en-US", "en-GB"):
                    tracks.extend((kind, lang, track) for track in info.get(kind, {}).get(lang, [])
                                  if track.get("ext") == "vtt")
                if tracks:
                    break
            if not tracks:
                status["status"] = "no_available_english_caption"
            else:
                kind, lang, track = tracks[0]
                raw = fetch(track["url"])
                text = clean_vtt(raw)
                if not text:
                    raise ValueError("Empty caption response")
                append_jsonl(out / "youtube.jsonl", candidate(
                    "youtube_transcript", url, info.get("title") or row["title"],
                    info.get("upload_date"), text, "mixed_or_unverified_speakers", {
                        "method": "yt-dlp_public_caption_url", "caption_type": kind,
                        "language": lang, "coverage": "caption_track_not_audio_verified",
                        "speaker_labels": False, "youtube_video_id": video_id}))
                status.update(status="caption_acquired", characters=len(text), caption_type=kind)
            consecutive_failures = 0
        except Exception as error:
            status.update(status="failed", error=str(error))
            consecutive_failures += 1
        append_jsonl(out / "youtube-coverage.jsonl", status)
        print("youtube", video_id, status["status"], flush=True)
        if consecutive_failures >= 2:
            write_json(out / "youtube-blocker.json", {
                "reason": "Two consecutive caption acquisition failures; stop to avoid hammering service",
                "last_video_id": video_id, "remaining_unattempted": True})
            break
        time.sleep(0.3)
    for path in (out / "youtube-active-info.json", out / "youtube-active.stderr.txt"):
        if path.exists():
            path.unlink()


def summarize(out):
    archive = json.loads((out / "substack-archive.json").read_text()) if (out / "substack-archive.json").exists() else []
    videos = json.loads((out / "youtube-inventory.json").read_text()) if (out / "youtube-inventory.json").exists() else []
    sub_status = {r["id"]: r for r in read_jsonl(out / "substack-coverage.jsonl")}
    yt_status = {r["video_id"]: r for r in read_jsonl(out / "youtube-coverage.jsonl")}
    summary = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "substack": {"inventoried": len(archive), "attempted": len(sub_status),
                            "statuses": dict(Counter(r["status"] for r in sub_status.values()))},
               "youtube": {"inventoried": len(videos), "attempted": len(yt_status),
                           "statuses": dict(Counter(r["status"] for r in yt_status.values()))},
               "candidates": {}}
    for name in ("hee", "substack", "linked-writing", "youtube"):
        rows = list({r["id"]: r for r in read_jsonl(out / (name + ".jsonl"))}.values())
        summary["candidates"][name] = {"records": len(rows), "characters": sum(len(r["text"]) for r in rows)}
    gaps = {"substack": [], "youtube": []}
    for row in archive:
        status = sub_status.get(row["id"], {})
        if status.get("status") != "full_public_body":
            gaps["substack"].append({"source_url": row["canonical_url"], "title": row["title"],
                                       "status": status.get("status", "unattempted"), "error": status.get("error")})
    for row in videos:
        status = yt_status.get(row["id"], {})
        if status.get("status") not in {"caption_acquired", "covered_by_existing_hee_transcript"}:
            gaps["youtube"].append({"source_url": "https://www.youtube.com/watch?v=" + row["id"],
                                     "title": row["title"], "status": status.get("status", "unattempted"),
                                     "error": status.get("error")})
    write_json(out / "coverage-summary.json", summary)
    write_json(out / "coverage-gaps.json", gaps)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["hee", "substack", "linked-local-writing", "youtube-enumerate", "youtube-captions", "summarize"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hee-source", type=Path)
    parser.add_argument("--website-source", type=Path)
    parser.add_argument("--max-pages", type=int, default=40)
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--channel-url", default="https://www.youtube.com/user/RhysLindmark")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Retry failed YouTube acquisitions once; use only after a cooldown")
    args = parser.parse_args()
    out = args.output.resolve()
    if out == REPO or REPO in out.parents:
        parser.error("Acquired candidates must stay outside the Git repository")
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_path = out / "public-ingestion-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    state[args.stage] = {"status": "running", "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    write_json(state_path, state)
    try:
        if args.stage == "hee":
            if not args.hee_source:
                parser.error("--hee-source is required for hee")
            ingest_hee(out, args.hee_source)
        elif args.stage == "substack":
            ingest_substack(out, args.max_pages)
        elif args.stage == "linked-local-writing":
            if not args.website_source:
                parser.error("--website-source is required")
            ingest_linked_local_writing(out, args.website_source)
        elif args.stage == "youtube-enumerate":
            enumerate_youtube(out, args.channel_url)
        elif args.stage == "summarize":
            summarize(out)
        else:
            ingest_youtube(out, args.limit, args.retry_failed)
        state[args.stage]["status"] = "completed_with_coverage_manifest"
    except KeyboardInterrupt:
        state[args.stage]["status"] = "interrupted_resumable"
        raise
    except Exception as error:
        state[args.stage].update(status="failed", error=str(error))
        raise
    finally:
        state[args.stage]["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        current = json.loads(state_path.read_text()) if state_path.exists() else {}
        current[args.stage] = state[args.stage]
        write_json(state_path, current)


if __name__ == "__main__":
    main()
