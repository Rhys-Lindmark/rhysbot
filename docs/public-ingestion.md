# Public writing, HEE and podcast acquisition

`scripts/ingest_public.py` produces private **candidates**, not approved release
data. Public availability does not establish authorship of quoted text, consent
to represent a guest's speech as Rhys, or approval to republish sensitive essays.
All candidates still pass the main corpus privacy/publication gate.

## Sources and coverage

- Substack: enumerate `/api/v1/archive` until an empty page, then request each
  public `/api/v1/posts/<slug>` body. The initial archive contains **223 posts**
  spanning June 2017–October 2026. Short announcement bodies remain short; links
  to full articles are explicitly retained. `truncated_body_text` is never used
  as a substitute for the body. Restricted, absent and paywall-marker bodies
  receive distinct coverage labels.
- HEE: **89 existing `.txt` transcripts**, mapped through `filename-map.json` to
  YouTube video IDs and through `index.json` to publication dates. The existing
  extractor obtains captions through yt-dlp / transcript API, so these are
  transcripts, not scripts or generated book summaries. There are 89 files in
  this located directory; a previously expected 92 is not verified. No media,
  summaries, Anki package, book draft or unrelated local file is copied.
- The Rhys Lindmark channel: yt-dlp enumerates the public `videos`, `shorts` and
  `streams` tabs without accounts/cookies or downloading media. Initial counts:
  **169 videos, 39 shorts, 7 streams; 215 unique video IDs**. Available English
  manual captions take priority over auto captions. The cleaner removes VTT cue
  metadata and rolling repeated words. Caption text is not verified against
  audio and lacks reliable speaker labels.
- Additional HEE inventory: YouTube oEmbed verified the existing HEE transcript source channel as `https://www.youtube.com/@HowEverythingEvolved`. Its 89 public videos were merged into the 215-video podcast inventory, yielding 304 unique public videos. Existing HEE transcripts cover these 89 without another caption download. Use `youtube-enumerate --channel-url https://www.youtube.com/@HowEverythingEvolved` to reproduce the merged inventory.
- Linked writing: two full AI 2026 articles directly linked from acquired public
  Substack posts are available as local public HTML. Only their static `<main>`
  text is extracted; dynamic charts are excluded. Other local pages/drafts are
  not crawled.

Exact current acquisition counts and every failed/unattempted public URL live in
private `coverage-summary.json` and `coverage-gaps.json`. Source inventories are
not proof that all associated bodies or caption tracks have been acquired.

Checkpoint at 2026-10-19 04:30 UTC (acquisition continues): 89 HEE transcripts;
54/223 full Substack bodies, 51 rate-limited attempts and 118 unattempted posts;
2 linked local article bodies; 33/215 YouTube English caption tracks, 182
unattempted videos. There are no confirmed absent-caption cases at this point.
Substack collection is stopped for cooldown; YouTube collection remains active.

## Reproduction

Use an existing Python 3 and yt-dlp. No package installation, browser, account or
cookie is required. `certifi`, if already installed, supplies Python's CA bundle;
TLS certificate verification remains enabled.

```sh
python3 scripts/ingest_public.py hee --hee-source "$HEE_PROJECT" --output "$CANDIDATE_DIR"
python3 scripts/ingest_public.py substack --output "$CANDIDATE_DIR"
python3 scripts/ingest_public.py linked-local-writing --website-source "$WEBSITE_PROJECT" --output "$CANDIDATE_DIR"
python3 scripts/ingest_public.py youtube-enumerate --output "$CANDIDATE_DIR"
python3 scripts/ingest_public.py youtube-captions --output "$CANDIDATE_DIR"
python3 scripts/ingest_public.py summarize --output "$CANDIDATE_DIR"
python3 -m unittest discover -s tests -p test_ingest_public.py
```

`CANDIDATE_DIR` must resolve outside this Git repository. The main project uses
an ignored private work directory with restrictive parent permissions. The
script rejects output directories under its repository. Do not copy the private
directory into a repository, even temporarily.

## Checkpointing, limits and attribution

The private `public-ingestion-state.json` records stage start/end status. JSONL
coverage logs retain failures; latest status for each source ID is authoritative.
Successful records are skipped on resume. Substack's exhausted archive inventory
is reused; failed bodies can be retried after cooldown. HTTP 429 stops new post
requests immediately; normal requests are spaced two seconds apart. YouTube
stops after two consecutive acquisition failures, retains exact reasons, and
does not label unattempted videos as lacking captions. Network responses are
bounded to 8 MiB, enumeration to 2,000 entries/tab, and subprocesses have finite
timeouts. Bulk media is never downloaded.

Each candidate has stable `id`, `source_type`, `source_url`, `title`,
`published_at`, `text`, `authorship`, `acquisition`, and `source_group_id` fields.
Local HEE inputs retain a content SHA-256. Podcast/YouTube speech is marked
`mixed_or_unverified_speakers` and HEE is marked
`rhys_hosted_speaker_attribution_unverified`. Neither is eligible as an
unqualified Rhys instruction answer. Writing may contain quotations. Dates do
not establish current beliefs, and imported guest claims are not Rhys's beliefs.

Resource footprint: stdlib streaming/sequential network jobs; no new runtimes,
model weights, media files, servers or dependencies. Raw per-video metadata is
reused in one scratch file and removed at the completed caption phase boundary.

The initial Substack run hit rate limiting before this stop-on-429 safeguard was
added; its 51 failed attempts remain in the coverage log as evidence. No missing
body was replaced with a preview. Later retries append new status entries;
summary generation resolves them by source ID to the most recent status.
