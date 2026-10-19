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
- Linked writing: two full AI 2026 articles directly linked from Substack were
  extracted from existing public HTML. A public-only follow-up resolved 101
  unique own-domain links: 12 navigation/assets excluded; 64 exact unique slugs
  match acquired Substack bodies; two redirects verified existing bodies; five
  full static articles extracted; 18 historical URLs returned HTTP 404. Of the
  five, the two AI 2026 articles were already included. Newly added sources are
  *Marriage Counseling with Capitalism*, *Music for 18 Pianos*, and *Us*. The
  last publicly describes an in-progress 2024 book version; no local book draft
  was read. Visuals, charts and audio are not transcribed. Exact slug matches
  do not assert a working redirect or identical historical article versions.

Exact current acquisition counts and every failed/unattempted public URL live in
private `coverage-summary.json` and `coverage-gaps.json`. Source inventories are
not proof that all associated bodies or caption tracks have been acquired.

Acquisition checkpoint, 2026-10-19 04:48 UTC: all 223 inventoried Substack posts
have full publicly accessible bodies; all 304 videos have coverage statuses.
Acquired 212 English caption tracks and reused 89 existing HEE transcripts.
Two videos lack available English captions; one caption request still returned
HTTP 429 after a single cooled-down retry. The two spoken gaps were subsequently covered by automatic ASR of their original public RSS audio. The title-labeled solo-piano video has no expected speech transcript; its audio was not independently checked. Two linked local article bodies plus three newly acquired public article bodies are included after normal excerpt screening.

A public post discussing Medium's “paywall” initially triggered a conservative
text match. Its complete public body was checked and reacquired after fixing the
classifier to detect structural paywall markers rather than the ordinary word.
Restricted audiences, access requirements and actual HTML paywall markers still
fail the full-body gate. The release builder and validator independently require
`full_public_body`, including after resumed acquisition.

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
python3 scripts/ingest_linked_writing.py --coverage "$CANDIDATE_DIR/substack-coverage.jsonl" --output "$LINKED_PRIVATE_DIR"
python3 -m unittest discover -s tests -p test_ingest_public.py
```

`CANDIDATE_DIR` must resolve outside this Git repository. The main project uses
an ignored private work directory with restrictive parent permissions. The
script rejects output directories under its repository. Do not copy the private
directory into a repository, even temporarily.

## Checkpointing, limits and attribution

The private `public-ingestion-state.json` records stage start/end status. JSONL
coverage logs retain failures; latest status for each source ID is authoritative.
Successful records are skipped on resume. Candidate files preserve append-only
history; summaries and corpus construction use the latest version of each
source document. Substack's exhausted archive inventory is reused; failed bodies can be retried after cooldown. HTTP 429 stops new post
requests immediately; normal requests are spaced two seconds apart. YouTube
stops after two consecutive acquisition failures, retains exact reasons, and
does not label unattempted videos as lacking captions. Use
`youtube-captions --retry-failed` only after a cooldown for a single new attempt
per failed video; successful acquisitions and confirmed absent-caption cases
are skipped. Network responses are
bounded to 8 MiB, enumeration to 2,000 entries/tab, and subprocesses have finite
timeouts. Caption collection does not download bulk media. The separate RSS follow-up downloads only the three identified recordings that fill spoken transcript gaps.

Each candidate has stable `id`, `source_type`, `source_url`, `title`,
`published_at`, `text`, `authorship`, `acquisition`, and `source_group_id` fields.
Local HEE inputs retain a content SHA-256. Podcast/YouTube speech is marked
`mixed_or_unverified_speakers` and HEE is marked
`rhys_hosted_speaker_attribution_unverified`. Neither is eligible as an
unqualified Rhys instruction answer. Writing may contain quotations. Dates do
not establish current beliefs, and imported guest claims are not Rhys's beliefs.

Caption/writing acquisition footprint: stdlib streaming/sequential network jobs; no new runtimes, model weights, media files, servers or dependencies for those collectors. RSS ASR has its separate bounded footprint below. Raw per-video metadata is
reused in one scratch file and removed at the completed caption phase boundary.

The initial Substack run hit rate limiting before this stop-on-429 safeguard was
added; its 51 failed attempts remain in the coverage log as evidence. No missing
body was replaced with a preview. Later retries append new status entries;
summary generation resolves them by source ID to the most recent status.

The linked collector preserves canonical URLs, titles, available dates and body
selectors; requires verified article metadata or the inspected own-domain
h1/title/main template; strips navigation and scripts; and blocks paid previews.
It uses HTTPS, allowed-domain redirects, two-second pacing, 8 MiB responses and
explicit 429 stop/cooldown behavior. Private candidates are not auto-released.
Before integration, inspect extraction examples and exclude URLs already in
`linked-writing.jsonl`; store only genuinely new documents in the private
`public-source-candidates/linked-articles.jsonl`, then rebuild and validate.


## Original podcast RSS and CPU transcription

Apple's public lookup for podcast ID `1254196635` identifies The Rhys Show feed:
`https://feeds.soundcloud.com/users/soundcloud:users:46689666/sounds.rss`.
The saved feed contains 140 episodes. Of these, 112 uniquely match channel titles
after exact normalization; 27 more have individually reviewed title/guest/topic
identity aliases. One episode, *Wisdom Age and the Evolution of Society: Rhys
Lindmark at The Index Show*, has no YouTube match. A link in an RSS description
can reference another episode and is never sufficient identity evidence.

The 137 matched videos with captions cover their YouTube editions. RSS and
YouTube durations sometimes differ; matching an episode does not establish
identical audio or transcription of every second of the RSS edition. The two
matched caption gaps (#13 Kirk Dameron and #12 Erik Voorhees), plus the Index
Show episode, have separate automatic transcripts of the original RSS audio.
See `docs/podcast-coverage.json` for episode-level provenance.

`scripts/transcribe_podcast.py` runs faster-whisper 1.2.1, av 16.1.0 and pinned
`Systran/faster-whisper-small.en` revision
`d1d751a5f8271d482d14ca55d9e2deeebbae577f` on CPU int8, two threads and one
worker. Audio is bounded to 100 MiB/two hours; process memory allowance is at
most 2,560 MiB. Models/audio stay in ignored private work, never Git. Transcript
reports preserve audio/model hashes, segments, timing and package versions.
The base.en pilot showed transcription errors; a 90-second small.en comparison
improved coherence, but neither run is a human audio review. Small.en full
recordings supply the released candidates. Do not import the comparison clip.

Candidates use `podcast_transcript`, `mixed_or_unverified_speakers` and
`automatic_asr_not_audio_verified`. They pass the same title/excerpt privacy
gates as public captions. Explicitly matched editions share evaluation groups.
All podcast transcripts remain excluded from default adaptation/SFT, including
when an upstream author label claims otherwise. Proper nouns, numbers and
other words may be wrong even where model confidence appears high.

Use the script help for a private output directory and a separately acquired,
pinned model snapshot. The RSS collector works on saved bounded metadata;
ASR requires the optional dependencies above and ffprobe. Downloaded audio
can be removed after hashes and accepted transcript evidence are retained.
