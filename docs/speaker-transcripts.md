# Screened podcast and YouTube speaker transcripts

`scripts/integrate_speaker_transcripts.py` prepares speaker-turn sidecars and a
canonical audio-text edition of the research corpus. It performs no speech/model
inference, network requests, training-data regeneration or deployment. A completed
ASR batch and reviewed publication are separate prerequisites. The initial local
acceptance uses only the completed voice pilot. After its read-only plan passed,
the operator-authorized single-pilot integration was applied locally and
validated. This does not establish full-archive completion or publish anything
automatically.

## Private input contract

Supply an explicit directory of completed episode JSON evidence from
`training/speech.py`, or one `--episode-file`. Choose one current result per
episode; duplicate episode IDs or ambiguous RSS/YouTube aliases are rejected.
`report.json`, `manifest.json` and `reference.json` are not episode files.
A file larger than 8 MiB is recorded as a gap. No file is treated as successful
merely because the remote job was launched; it must contain valid episode
metadata, timestamped turns, audio SHA256, duration and pinned speech models.

Only these input fields can enter public sidecars:

- Episode ID/title/publication date and `audio_source_kind`: `rss_audio` (default)
  or `youtube_audio`. RSS requires the inventoried public SoundCloud page and RSS
  enclosure; an explicit matching YouTube ID/URL is optional. YouTube audio
  requires an explicit 11-character `youtube_id`, exact canonical `youtube_url`
  and matching `source_url` (`https://www.youtube.com/watch?v=ID`), with
  `enclosure_url=null`. Signed/download stream URLs are never accepted or exposed.
- Audio SHA256/duration and ASR/diarization/voice model IDs plus exact revisions.
- Ordered `turns` with finite `start`, `end`, `speaker_id` and text.
- Authoritative upstream `rhys_speaker_id`, and aggregate `word_count` /
  `unknown_word_count` when available.

The importer ignores incoming display names. Only `rhys_speaker_id` produces
“Rhys Lindmark”; it never infers identity from a self-introduction, episode title,
transcript, model score or guest name. Other `speaker_N` IDs display as
“Guest Speaker N+1” while retaining zero-based IDs. `unknown` / `overlap` display
as “Unknown / overlap.” The provided identity can still be wrong; it is machine
attribution, not a calibrated probability or a claim about Rhys's beliefs.

Full private input must never be published. Word arrays/confidences, speaker
segments, voiceprints, cosine arrays, identity-anchor evidence, raw audio,
private paths and arbitrary additional fields are omitted by construction.

## Public sidecar and corpus links

Accepted files are written under
`data/public/speaker-transcripts/<episode-id>.json`. Top-level fields include
`schema_version`, `audio_source_kind`, `id`, `title`, `published_at`, `source_url`, `rss_feed_url`,
`rss_enclosure_url`, `youtube_id`, `youtube_url`, `audio_sha256`,
`audio_duration_seconds`, `pins`, `rhys_speaker_id`, `identity_basis`,
`word_alignment`, `turn_counts`, `limitations`, and `turns`.

Each turn has exactly its index, timestamps, anonymous speaker ID/display label,
text, and privacy status. Public privacy rules screen every turn; flagged text
becomes `null` with `privacy_status=withheld_automated_screen`. Timestamps and
labels remain. Short neighboring windows also screen patterns split across
speaker boundaries. Individual flags and rejected text are not disclosed.
Automatic screening can still miss sensitive context and can over-withhold
ordinary public discussion; this is not an exhaustive semantic privacy audit.

The public validator regenerates deterministic chunks one sidecar at a time
and retains only their hashes and turn/time bounds. Every linked corpus row
must match its episode/chunk ID, text digest, recorded content hash, turn-index
range and timestamps. Exact deduplication may omit generated chunks; an omitted
chunk is allowed, independently altered linked text is not.

Corpus chunks contain up to 350 source words with explicit speaker/time labels,
sidecar turn-index bounds, and time bounds. A long turn split across chunks uses
its original complete turn bounds, not invented/interpolated word timestamps.
All chunks remain `authorship=mixed_or_unverified_speakers`: identified Rhys turns
can quote others, and guest statements are not silently represented as his views.
The existing source-type training exclusion continues to apply. No existing
checkpoint is retrained or relabeled as having consumed these transcripts.

The frontend-friendly `speaker_transcript_url` is:

```text
https://raw.githubusercontent.com/Rhys-Lindmark/rhysbot/main/data/public/speaker-transcripts/<episode-id>.json
```

Rows also retain `speaker_transcript` as the relative path,
`turn_index_start`, `turn_index_end`, `time_start_seconds`, and
`time_end_seconds`. Render turn text as text; show a withheld placeholder for
`null`, not an empty quotation attributed to a speaker. Audio may load from the
public RSS enclosure only on user action. For `youtube_audio`, `rss_feed_url`
and `rss_enclosure_url` are null; open the canonical YouTube URL at the selected
turn timestamp instead. Do not expose private input files or
invent names for anonymous clusters.

## Canonical recording replacement and splits

For accepted processed episodes, new RSS ASR chunks replace old
`youtube_transcript` and `podcast_transcript` corpus editions whose explicit
source/group URL matches the episode's RSS page or YouTube alias. The sidecar
retains both public references. This identifies corresponding editions; it does
not claim their recordings, duration, words or edits are identical.

For `youtube_audio`, only an old `youtube_transcript` whose actual `source_url`
is the explicit canonical video URL is replaced. A matching split/group alias
alone never replaces another video or a canonical RSS edition. New rows use
`source_type=youtube_transcript` and `acquisition.media=youtube_audio`; RSS rows
use `source_type=podcast_transcript` and `acquisition.media=rss_audio`. The method
records GPU ASR/diarization, not original caption acquisition.

Unprocessed episodes, unmatched video-only captions, HEE narration, writing, X,
and all approved Anki/card-registry content remain. An invalid/title-flagged
input or episode with no publishable text is a gap; it does not erase the older
edition. No title-similarity match can authorize replacement. Existing matching
alias split groups are retained; exact-text and candidate-filtered 0.8-Jaccard
near repeats are grouped before writing split artifacts. This heuristic is not
exhaustive semantic deduplication.

SQLite stores new chunks/signatures on disk with a 4 MiB page-cache target.
Only one bounded episode's private evidence is parsed at a time. Corpus,
splits, source manifest, card registry and stats stream into private staging;
input and output corpus JSONL must remain below 64 MiB. All artifacts are staged
before apply, and each destination rename is atomic. A multi-file publication is
not a filesystem transaction: run the existing cross-file validator before any
commit/deployment, and never publish during application. A failed size/input
check before apply leaves the live corpus unchanged.

`stats.speaker_transcript_integration` includes `episodes_processed`,
`episodes_with_rhys_match`, `episodes_anonymous_or_ambiguous`,
`screened_turn_count`, `rss_audio_episodes`, `youtube_audio_episodes`, published/withheld turn counts, `unknown_words_fraction`,
replacement counts, sidecar bytes and provenance notes. Processed means accepted
screened episodes from this invocation. The unknown-word fraction is weighted
from available upstream aggregate counts; coverage and unavailable values are
explicit. It is not a confidence, diarization accuracy or identity probability.
Acquisition history remains separate from current canonical source metrics.

For final cumulative coverage, provide a directory containing all current
completed episodes, including the accepted pilot. Prior sidecars not selected by
an incremental invocation remain, while that invocation's processing counters
cover only its supplied accepted episodes. Failed/unattempted episodes must not
be counted as completed merely from the requested batch size.

## Bounded invocation and acceptance

Keep staging and the full ASR evidence outside Git. Plan mode is the default and
publishes nothing:

```sh
python3 scripts/integrate_speaker_transcripts.py \
  --episode-file "$VOICE_PILOT_JSON" --work-dir "$PRIVATE_STAGING" --plan

python3 scripts/integrate_speaker_transcripts.py \
  --evidence-dir "$COMPLETED_EPISODE_DIRECTORY" --work-dir "$PRIVATE_STAGING" --plan
```

After actual batch completion and review, the operator can explicitly apply:

```sh
python3 scripts/integrate_speaker_transcripts.py \
  --evidence-dir "$COMPLETED_EPISODE_DIRECTORY" --work-dir "$PRIVATE_STAGING" --apply
python3 scripts/validate_corpus.py
python3 scripts/integrate_speaker_transcripts.py --validate-only
```

Acceptance is finite: verify private-field omission and exact speaker ID mapping;
strict public-schema validation (no extra/raw fields), privacy-withheld text absence; deterministic chunk text/hash/timestamp/index
matching; explicit RSS/YouTube replacement only; preserved
video-only/HEE/Anki records and frozen `data/training` hashes; group consistency,
deduplication, byte counts and the 64 MiB limit; then inspect a small actual
sidecar sample before publishing. Source tests use synthetic fixtures; actual
full-archive memory/coverage and public output remain unverified until the real
completed directory is supplied and applied. No local ML or new dependencies are
required.

## Completed local acceptance

The actual completed voice pilot passed the latest importer in read-only plan
mode: one RSS episode with a supplied Rhys match, 659 screened turns, 643
published turn texts and 16 withheld turn texts. The weighted upstream unknown
word fraction was 0.04214080280105039. It would replace 15 corresponding old
YouTube excerpts, with no acquisition gaps and a 188,271-byte screened sidecar.
The bounded run took 9.217 seconds and peaked at 39,710,720 bytes (37.9 MiB)
child-process RSS. Public artifact hashes were unchanged and no public sidecar
directory was created. These measurements cover one pilot, not the full archive.

Eleven stdlib source tests cover private-field omission, authoritative identity
mapping, strict schemas, turn/boundary screening, chunk limits, read-only plans,
RSS and YouTube replacement boundaries, split grouping/deduplication, and the
publication size guard. They do not establish ASR, diarization or identity
accuracy. Full RSS/YouTube batch coverage and the final corpus size must still be
measured against actual completed inputs before application/publication.

The explicitly authorized single-pilot apply then passed both validators:
32 canonical RSS chunks replaced 15 old YouTube caption excerpts, producing
38,829 research excerpts. Corpus text is 17,311,114 UTF-8 bytes; corpus JSONL is
55,764,105 bytes, below 64 MiB. The screened sidecar is 188,271 bytes. Apply took
9.338 seconds / 36,868,096 bytes peak RSS; streaming corpus validation took
35.597 seconds / 92,393,472 bytes peak RSS; strict sidecar/chunk validation took
0.590 seconds / 21,024,768 bytes peak RSS. All are below the admitted 120 MiB
limit. Protected non-podcast corpus rows, the complete Anki card registry and
all seven frozen training files retained their pre-apply hashes. This is local
pilot integration evidence, not full-archive completion or ASR quality grading.
